/**
 * Payments / settlements panel (F6, AGENTS.md §2.4).
 *
 * The lifecycle is `pending → confirmed | rejected | cancelled`. Only the
 * recipient can confirm or reject; only the sender can cancel. Balances move
 * only on confirmation, and the WS bus pushes the fresh snapshot on every
 * transition — so this list and the balances panel stay in sync for both
 * parties without a reload.
 */

import { useMemo, useState } from 'react'

import { useI18n } from '../i18n'
import { formatMoney } from '../lib/money'
import { formatDateTime } from '../lib/dates'
import {
  useCancelSettlement,
  useConfirmSettlement,
  useGroupBalances,
  useGroup,
  useMe,
  useRejectSettlement,
  useSettlements,
} from '../query/queries'
import type { SettlementOut, SettlementState } from '../types'
import { Badge, Button, Card, CardHeader, EmptyState, Modal, Spinner } from './ui'
import { ProposeModal } from './ProposeModal'

const STATE_TONE: Record<SettlementState, 'gray' | 'green' | 'red' | 'amber' | 'indigo'> = {
  pending: 'amber',
  confirmed: 'green',
  rejected: 'red',
  cancelled: 'gray',
}

export function SettlementsPanel({ groupId }: { groupId: number }) {
  const settlements = useSettlements(groupId)
  const balances = useGroupBalances(groupId)
  const group = useGroup(groupId)
  const me = useMe()
  const { t, locale } = useI18n()
  const [showPropose, setShowPropose] = useState(false)

  const confirm = useConfirmSettlement(groupId)
  const reject = useRejectSettlement(groupId)
  const cancel = useCancelSettlement(groupId)
  const [acting, setActing] = useState<SettlementOut | null>(null)

  const meId = me.data?.id ?? -1
  const currency = group.data?.currency ?? 'EUR'

  const { pending, history } = useMemo(() => {
    const all = settlements.data ?? []
    return {
      pending: all.filter((s) => s.state === 'pending'),
      history: all.filter((s) => s.state !== 'pending'),
    }
  }, [settlements.data])

  if (settlements.isPending) return <Spinner label={t('payments.loading')} />

  const closed = group.data?.status === 'closed'

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-800">{t('payments.title')}</h2>
        <Button onClick={() => setShowPropose(true)} disabled={closed}>
          {t('payments.proposeNew')}
        </Button>
      </div>

      <Card>
        <CardHeader title={t('payments.pendingTitle')} />
        {pending.length === 0 ? (
          <p className="px-4 py-3 text-sm text-gray-500">{t('payments.noPending')}</p>
        ) : (
          <ul className="divide-y divide-gray-100">
            {pending.map((s) => (
              <li key={s.id} className="px-4 py-3 flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm text-gray-800 truncate">
                    {s.from_name} <span className="text-gray-400">→</span> {s.to_name}
                    <span className="ml-2 font-medium tabular-nums">
                      {formatMoney(s.amount, currency, locale)}
                    </span>
                  </p>
                  <p className="text-xs text-gray-400 mt-0.5">
                    {s.from_user_id === meId ? t('payments.youProposed') : t('payments.waitingYou')}
                    {' · '}
                    {formatDateTime(s.created_at, locale)}
                  </p>
                </div>
                <div className="flex gap-2 shrink-0">
                  {s.to_user_id === meId ? (
                    <>
                      <Button
                        variant="secondary"
                        onClick={() =>
                          void reject.mutateAsync(s.id)
                        }
                        busy={reject.isPending && reject.variables === s.id}
                      >
                        {t('payments.reject')}
                      </Button>
                      <Button
                        onClick={() => setActing(s)}
                        busy={confirm.isPending && confirm.variables === s.id}
                      >
                        {t('payments.confirm')}
                      </Button>
                    </>
                  ) : s.from_user_id === meId ? (
                    <Button
                      variant="secondary"
                      onClick={() => void cancel.mutateAsync(s.id)}
                      busy={cancel.isPending && cancel.variables === s.id}
                    >
                      {t('payments.cancel')}
                    </Button>
                  ) : (
                    <Badge tone="amber">{t('state.pending')}</Badge>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {history.length > 0 ? (
        <Card>
          <CardHeader title={t('payments.historyTitle')} />
          <ul className="divide-y divide-gray-100">
            {history.map((s) => (
              <li key={s.id} className="px-4 py-2.5 flex items-center justify-between gap-3">
                <span className="text-sm text-gray-700 min-w-0 truncate">
                  {s.from_name} <span className="text-gray-400">→</span> {s.to_name}
                  <span className="ml-2 tabular-nums">{formatMoney(s.amount, currency, locale)}</span>
                </span>
                <span className="flex items-center gap-2 shrink-0">
                  <span className="text-xs text-gray-400">
                    {s.confirmed_at ? formatDateTime(s.confirmed_at, locale) : formatDateTime(s.created_at, locale)}
                  </span>
                  <Badge tone={STATE_TONE[s.state]}>{t('state.' + s.state)}</Badge>
                </span>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}

      {settlements.data && settlements.data.length === 0 ? (
        <Card>
          <EmptyState title={t('payments.emptyTitle')} hint={t('payments.emptyHint')} />
        </Card>
      ) : null}

      {showPropose && balances.data && me.data ? (
        <ProposeModal
          groupId={groupId}
          meId={meId}
          balances={balances.data}
          onClose={() => setShowPropose(false)}
        />
      ) : null}

      {acting ? (
        <Modal title={t('payments.confirmModalTitle')} onClose={() => setActing(null)}>
          <p className="text-sm text-gray-700">
            {t('payments.confirmModalBody', {
              name: acting.from_name,
              amount: formatMoney(acting.amount, currency, locale),
            })}
          </p>
          <div className="flex justify-end gap-2 mt-4">
            <Button variant="secondary" onClick={() => setActing(null)}>
              {t('common.cancel')}
            </Button>
            <Button
              busy={confirm.isPending}
              onClick={() =>
                void confirm
                  .mutateAsync(acting.id)
                  .then(() => setActing(null))
                  .catch(() => setActing(null))
              }
            >
              {t('payments.confirm')}
            </Button>
          </div>
        </Modal>
      ) : null}
    </div>
  )
}

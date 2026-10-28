/**
 * Balances panel (F4, AGENTS.md §2.2, §2.3).
 *
 * Three views of the same netted balances:
 *  - per-member balance (positive = you are owed, negative = you owe);
 *  - the current pairwise view (every debtor → creditor amount, §2.4);
 *  - the suggested transfers (the §2.3 greedy, at most n−1) with a "settle"
 *    affordance that opens the proposal dialog pre-filled.
 *
 * The whole panel is patched live by the WS bus: every group event carries a
 * fresh `group_balances` snapshot which is written straight into this cache.
 */

import { useMemo, useState } from 'react'

import { useI18n } from '../i18n'
import { formatMoney, formatSigned } from '../lib/money'
import { useGroupBalances, useMe } from '../query/queries'
import { balanceClass, Button, Card, CardHeader, EmptyState, Spinner } from './ui'
import { ProposeModal } from './ProposeModal'

export function BalancesPanel({ groupId }: { groupId: number }) {
  const balances = useGroupBalances(groupId)
  const me = useMe()
  const { t, locale } = useI18n()
  const [proposeTo, setProposeTo] = useState<{ to: number; amount: string } | null>(null)

  const nameById = useMemo(
    () =>
      new Map(
        (balances.data?.balances ?? []).map((b) => [b.user_id, b.display_name]),
      ),
    [balances.data],
  )

  if (balances.isPending) return <Spinner label={t('balances.loading')} />

  const data = balances.data
  if (!data) return null

  const meId = me.data?.id ?? -1
  const myBalance = data.balances.find((b) => b.user_id === meId)?.balance ?? '0'
  const settled = data.balances.every((b) => Number(b.balance.replace(/,/g, '')) === 0)

  return (
    <div className="space-y-4">
      {/* Your position at a glance. */}
      <Card className="p-4 flex items-center justify-between">
        <div>
          <p className="text-xs text-gray-500 uppercase tracking-wide">{t('balances.your')}</p>
          <p className={`text-2xl font-semibold tabular-nums ${balanceClass(myBalance)}`}>
            {formatSigned(myBalance, data.currency, locale)}
          </p>
          <p className="text-xs text-gray-400 mt-0.5">
            {settled
              ? t('balances.settledAll')
              : myBalance.startsWith('-')
                ? t('balances.owe')
                : t('balances.owed')}
          </p>
        </div>
        <Button
          onClick={() => setProposeTo({ to: 0, amount: '' })}
          disabled={settled}
          variant="secondary"
        >
          {t('balances.propose')}
        </Button>
      </Card>

      {settled ? (
        <Card>
          <EmptyState title={t('balances.emptyTitle')} hint={t('balances.emptyHint')} />
        </Card>
      ) : (
        <>
          {/* Per-member balances. */}
          <Card>
            <CardHeader title={t('balances.memberBalances')} />
            <ul className="divide-y divide-gray-100">
              {data.balances.map((b) => (
                <li key={b.user_id} className="flex items-center justify-between px-4 py-2.5">
                  <span className="text-sm text-gray-800">
                    {b.display_name}
                    {b.user_id === meId ? <span className="text-xs text-gray-400"> ({t('common.you')})</span> : null}
                  </span>
                  <span className={`text-sm font-medium tabular-nums ${balanceClass(b.balance)}`}>
                    {formatSigned(b.balance, data.currency, locale)}
                  </span>
                </li>
              ))}
            </ul>
          </Card>

          {/* Suggested transfers (the optimizer). */}
          <Card>
            <CardHeader title={t('balances.suggested')} />
            <p className="px-4 pt-2 text-xs text-gray-500">{t('balances.suggestedHint')}</p>
            {data.transfers.length === 0 ? (
              <p className="px-4 py-3 text-sm text-gray-500">{t('balances.noTransfers')}</p>
            ) : (
              <ul className="divide-y divide-gray-100 mt-2">
                {data.transfers.map((tr, i) => (
                  <li key={i} className="flex items-center justify-between gap-3 px-4 py-2.5">
                    <span className="text-sm text-gray-800 min-w-0 truncate">
                      {nameById.get(tr.from_user_id) ?? t('common.userById', { id: tr.from_user_id })}
                      <span className="text-gray-400"> → </span>
                      {nameById.get(tr.to_user_id) ?? t('common.userById', { id: tr.to_user_id })}
                    </span>
                    <span className="flex items-center gap-3 shrink-0">
                      <span className="text-sm font-medium tabular-nums text-gray-900">
                        {formatMoney(tr.amount, data.currency, locale)}
                      </span>
                      {tr.from_user_id === meId ? (
                        <Button
                          variant="secondary"
                          onClick={() => setProposeTo({ to: tr.to_user_id, amount: tr.amount })}
                        >
                          {t('balances.settle')}
                        </Button>
                      ) : null}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          {/* Pairwise view. */}
          <Card>
            <CardHeader title={t('balances.whoOwesWhom')} />
            {data.pairs.length === 0 ? (
              <p className="px-4 py-3 text-sm text-gray-500">{t('balances.noDebts')}</p>
            ) : (
              <ul className="divide-y divide-gray-100">
                {data.pairs.map((p, i) => (
                  <li key={i} className="flex items-center justify-between px-4 py-2">
                    <span className="text-sm text-gray-700">
                      {nameById.get(p.from_user_id) ?? t('common.userById', { id: p.from_user_id })}
                      <span className="text-gray-400"> → </span>
                      {nameById.get(p.to_user_id) ?? t('common.userById', { id: p.to_user_id })}
                    </span>
                    <span className="text-sm tabular-nums text-gray-900">
                      {formatMoney(p.amount, data.currency, locale)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </>
      )}

      {proposeTo !== null ? (
        <ProposeModal
          groupId={groupId}
          meId={meId}
          balances={data}
          initialTo={proposeTo.to || undefined}
          initialAmount={proposeTo.amount || undefined}
          onClose={() => setProposeTo(null)}
        />
      ) : null}
    </div>
  )
}

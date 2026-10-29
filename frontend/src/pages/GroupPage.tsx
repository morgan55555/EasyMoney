/**
 * Group page (F3–F9): the header (name, currency, status, owner close/reopen,
 * closed banner) and the tabbed body — Expenses | Balances | Payments |
 * Activity | People. Every tab is kept live by the WS bus.
 */

import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { ApiError } from '../api/client'
import { Badge, Button, ErrorText, Modal, Spinner } from '../components/ui'
import { ExpenseList } from '../components/ExpenseList'
import { BalancesPanel } from '../components/BalancesPanel'
import { SettlementsPanel } from '../components/SettlementsPanel'
import { FeedPanel } from '../components/FeedPanel'
import { InvitePanel } from '../components/InvitePanel'
import { PeoplePanel } from '../components/PeoplePanel'
import { useI18n, pluralForm } from '../i18n'
import { MEMBER_PLURAL } from '../i18n/locales'
import { formatDate } from '../lib/dates'
import {
  useCloseGroup,
  useGroup,
  useGroupBalances,
  useMe,
  useReopenGroup,
} from '../query/queries'

type Tab = 'expenses' | 'balances' | 'payments' | 'activity' | 'people' | 'invites'

const TABS: Array<{ id: Tab; key: string }> = [
  { id: 'expenses', key: 'tab.expenses' },
  { id: 'balances', key: 'tab.balances' },
  { id: 'payments', key: 'tab.payments' },
  { id: 'activity', key: 'tab.activity' },
  { id: 'invites', key: 'tab.invites' },
  { id: 'people', key: 'tab.people' },
]

/** A `?tab=` URL param (notification deep links) → the tab, else expenses. */
function parseTab(param: string | null): Tab {
  if (param && TABS.some((tb) => tb.id === param)) return param as Tab
  return 'expenses'
}

export function GroupPage({ groupId }: { groupId: number }) {
  const group = useGroup(groupId)
  const me = useMe()
  const balances = useGroupBalances(groupId)
  const close = useCloseGroup(groupId)
  const reopen = useReopenGroup(groupId)
  const { t, locale, lang } = useI18n()
  const [searchParams, setSearchParams] = useSearchParams()

  const [tab, setTab] = useState<Tab>(() => parseTab(searchParams.get('tab')))
  const [confirmClose, setConfirmClose] = useState(false)
  const [confirmReopen, setConfirmReopen] = useState(false)
  const [headerError, setHeaderError] = useState('')

  // Deep links (?tab=&focus=, from a notification's View button) can arrive
  // while the page is already mounted — follow the tab param.
  const tabParam = searchParams.get('tab')
  useEffect(() => {
    const parsed = parseTab(tabParam)
    setTab((cur) => (cur === parsed ? cur : parsed))
  }, [tabParam])

  // ?focus= names the element to scroll to and highlight after the tab
  // switch (lib/focus.ts): 'expense:<id>' | 'settlement:<id>' | 'feed:<type>'.
  const focus = searchParams.get('focus')
  const focusExpense = focus?.startsWith('expense:') ? Number(focus.slice('expense:'.length)) : NaN
  const focusSettlement = focus?.startsWith('settlement:') ? Number(focus.slice('settlement:'.length)) : NaN
  const focusFeed = focus?.startsWith('feed:') ? focus.slice('feed:'.length) : null

  const selectTab = (next: Tab): void => {
    setTab(next)
    const sp = new URLSearchParams(searchParams)
    sp.set('tab', next)
    sp.delete('focus')
    setSearchParams(sp, { replace: true })
  }

  if (group.isPending) return <Spinner label={t('group.loading')} />
  if (group.isError) return <ErrorText>{t('group.loadError')}</ErrorText>

  const g = group.data!
  const meId = me.data?.id ?? -1
  const iAmOwner = g.created_by === meId
  const closed = g.status === 'closed'

  const doClose = async () => {
    setHeaderError('')
    try {
      await close.mutateAsync()
      setConfirmClose(false)
    } catch (e) {
      setHeaderError(e instanceof ApiError ? e.message : t('group.closeError'))
    }
  }

  const doReopen = async () => {
    setHeaderError('')
    try {
      await reopen.mutateAsync()
      setConfirmReopen(false)
    } catch (e) {
      setHeaderError(e instanceof ApiError ? e.message : t('group.reopenError'))
    }
  }

  return (
    <div className="space-y-4">
      {/* Header. */}
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h1 className="text-xl font-bold text-gray-900 dark:text-gray-50 truncate">{g.name}</h1>
            <Badge tone="gray">{g.currency}</Badge>
            {closed ? <Badge tone="red">{t('group.closed')}</Badge> : <Badge tone="green">{t('group.active')}</Badge>}
          </div>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
            {t('groups.members', {
              n: g.member_count,
              plural: pluralForm(locale, g.member_count, MEMBER_PLURAL[lang]),
            })}
            {closed && g.closed_at ? ` · ${t('group.closedOn', { date: formatDate(g.closed_at, locale) })}` : ''}
          </p>
        </div>
        <div className="flex gap-2 shrink-0">
          {iAmOwner ? (
            closed ? (
              <Button onClick={() => setConfirmReopen(true)} busy={reopen.isPending}>
                {t('group.reopen')}
              </Button>
            ) : (
              <Button variant="danger" onClick={() => setConfirmClose(true)}>
                {t('group.close')}
              </Button>
            )
          ) : null}
        </div>
      </div>

      <ErrorText>{headerError}</ErrorText>

      {closed ? (
        <p className="text-sm text-amber-800 dark:text-amber-300 bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-900 rounded-md px-3 py-2">
          {t('group.closedBanner')}{' '}
          {iAmOwner ? t('group.closedBannerOwner') : t('group.closedBannerOther')}
        </p>
      ) : null}

      {/* Tabs. */}
      <div className="border-b border-gray-200 dark:border-gray-800">
        <nav className="-mb-px flex gap-1 overflow-x-auto" aria-label="Group sections">
          {TABS.map((tb) => (
            <button
              key={tb.id}
              onClick={() => selectTab(tb.id)}
              className={`whitespace-nowrap px-3 py-2 text-sm font-medium border-b-2 transition-colors ${
                tab === tb.id
                  ? 'border-indigo-600 text-indigo-700 dark:text-indigo-400'
                  : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200 hover:border-gray-300 dark:hover:border-gray-600'
              }`}
            >
              {t(tb.key)}
              {tb.id === 'balances' && balances.data ? (
                <span className="ml-1.5 text-xs text-gray-400 dark:text-gray-500">{balances.data.balances.length}</span>
              ) : null}
            </button>
          ))}
        </nav>
      </div>

      {/* Body. */}
      {tab === 'expenses' ? (
        <ExpenseList groupId={groupId} focusId={Number.isFinite(focusExpense) ? focusExpense : null} />
      ) : null}
      {tab === 'balances' ? <BalancesPanel groupId={groupId} /> : null}
      {tab === 'payments' ? (
        <SettlementsPanel groupId={groupId} focusId={Number.isFinite(focusSettlement) ? focusSettlement : null} />
      ) : null}
      {tab === 'activity' ? <FeedPanel groupId={groupId} focusType={focusFeed} /> : null}
      {tab === 'invites' ? <InvitePanel groupId={groupId} /> : null}
      {tab === 'people' ? <PeoplePanel groupId={groupId} /> : null}

      {confirmClose ? (
        <Modal title={t('group.closeModalTitle')} onClose={() => setConfirmClose(false)}>
          <p className="text-sm text-gray-700 dark:text-gray-300">
            {t('group.closeModalQ', { name: g.name })} {t('group.closeModalBody')}
          </p>
          <div className="flex justify-end gap-2 mt-4">
            <Button variant="secondary" onClick={() => setConfirmClose(false)}>
              {t('common.cancel')}
            </Button>
            <Button variant="danger" busy={close.isPending} onClick={() => void doClose()}>
              {t('group.close')}
            </Button>
          </div>
        </Modal>
      ) : null}

      {confirmReopen ? (
        <Modal title={t('group.reopenModalTitle')} onClose={() => setConfirmReopen(false)}>
          <p className="text-sm text-gray-700 dark:text-gray-300">
            {t('group.reopenModalQ', { name: g.name })} {t('group.reopenModalBody')}
          </p>
          <div className="flex justify-end gap-2 mt-4">
            <Button variant="secondary" onClick={() => setConfirmReopen(false)}>
              {t('common.cancel')}
            </Button>
            <Button busy={reopen.isPending} onClick={() => void doReopen()}>
              {t('group.reopen')}
            </Button>
          </div>
        </Modal>
      ) : null}
    </div>
  )
}

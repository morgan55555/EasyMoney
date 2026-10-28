/**
 * Notification bell (AGENTS.md §2.6, F8): unread count, live updates via the
 * WS `notification.created` event (query invalidation), explicit mark-read.
 */

import { useEffect, useRef, useState } from 'react'

import { useI18n, type TFunc } from '../i18n'
import { relativeTime } from '../lib/dates'
import { formatMoney } from '../lib/money'
import { useMarkNotificationsRead, useNotifications } from '../query/queries'

function makeDescribe(t: TFunc, locale: string) {
  return (type: string, payload: Record<string, unknown> | null): string => {
    const p = payload ?? {}
    const name = (key: string) => {
      const v = p[key]
      if (typeof v === 'string') return v
      if (v && typeof v === 'object' && 'display_name' in v) return String((v as { display_name: unknown }).display_name)
      return ''
    }
    const amount = (cur: unknown) =>
      typeof p.amount === 'string' ? formatMoney(p.amount, String(cur ?? ''), locale) : ''
    switch (type) {
      case 'expense.created': {
        const payer = name('payer')
        const label = payer ? t('notif.payerAdded', { payer }) : t('notif.newExpense')
        return `${label}: ${p.description ?? ''} (${amount(p.currency)})`
      }
      case 'expense.updated':
        return t('notif.expenseUpdated', { desc: String(p.description ?? ''), amount: amount(p.currency) })
      case 'expense.deleted':
        return t('notif.expenseDeleted', { desc: String(p.description ?? t('notif.anExpense')) })
      case 'settlement.created':
        return t('notif.settlementCreated', {
          from: String(p.from_name ?? ''),
          amount: amount(p.currency),
          to: String(p.to_name ?? ''),
        })
      case 'settlement.confirmed':
        return t('notif.settlementConfirmed', {
          from: String(p.from_name ?? ''),
          amount: amount(p.currency),
          to: String(p.to_name ?? ''),
        })
      case 'settlement.rejected':
        return t('notif.settlementRejected', { amount: amount(p.currency) })
      case 'settlement.cancelled':
        return t('notif.settlementCancelled', { amount: amount(p.currency) })
      case 'member.joined':
        return t('notif.memberJoined', { name: String(p.display_name ?? t('notif.aMember')) })
      case 'member.left':
        return t('notif.memberLeft', { name: String(p.display_name ?? t('notif.aMember')) })
      case 'group.closed':
        return t('notif.groupClosed')
      case 'group.reopened':
        return t('notif.groupReopened')
      default:
        return type.replace(/_/g, ' ')
    }
  }
}

export function NotificationsBell() {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const notifications = useNotifications()
  const markRead = useMarkNotificationsRead()
  const { t, locale } = useI18n()
  const describe = makeDescribe(t, locale)

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [])

  const unread = notifications.data?.unread_count ?? 0
  const items = notifications.data?.items ?? []

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="relative text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200 p-1"
        aria-label={
          t('notif.title') + (unread ? ` (${t('notif.unread', { n: unread > 99 ? '99+' : unread })})` : '')
        }
      >
        <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24" aria-hidden="true">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M15 17h5l-1.4-1.4A2 2 0 0118 14.2V11a6 6 0 10-12 0v3.2a2 2 0 01-.6 1.4L4 17h5m6 0v1a3 3 3 0 11-6 0v-1m6 0H9"
          />
        </svg>
        {unread > 0 ? (
          <span className="absolute -top-1 -right-1 bg-red-600 text-white text-[10px] font-semibold rounded-full min-w-[16px] h-4 px-1 flex items-center justify-center">
            {unread > 99 ? '99+' : unread}
          </span>
        ) : null}
      </button>

      {open ? (
        <div className="absolute right-0 mt-2 w-80 sm:w-96 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-lg shadow-lg z-40">
          <div className="flex items-center justify-between px-3 py-2 border-b border-gray-200 dark:border-gray-800">
            <span className="text-sm font-semibold text-gray-800 dark:text-gray-100">{t('notif.title')}</span>
            {unread > 0 ? (
              <button
                onClick={() => void markRead.mutate()}
                disabled={markRead.isPending}
                className="text-xs text-indigo-600 dark:text-indigo-400 hover:text-indigo-800 dark:hover:text-indigo-300"
              >
                {t('notif.markRead')}
              </button>
            ) : null}
          </div>
          <div className="max-h-96 overflow-y-auto">
            {notifications.isPending ? (
              <p className="text-sm text-gray-500 dark:text-gray-400 px-3 py-4">{t('notif.loading')}</p>
            ) : items.length === 0 ? (
              <p className="text-sm text-gray-500 dark:text-gray-400 px-3 py-4">{t('notif.empty')}</p>
            ) : (
              items.map((n) => (
                <div
                  key={n.id}
                  className={`px-3 py-2 border-b border-gray-100 dark:border-gray-800 last:border-0 ${n.read_at ? 'text-gray-500 dark:text-gray-400' : 'bg-indigo-50/50 dark:bg-indigo-950/40 text-gray-800 dark:text-gray-100'}`}
                >
                  <p className="text-sm">{describe(n.type, n.payload)}</p>
                  <p className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">{relativeTime(n.created_at, locale)}</p>
                </div>
              ))
            )}
          </div>
        </div>
      ) : null}
    </div>
  )
}

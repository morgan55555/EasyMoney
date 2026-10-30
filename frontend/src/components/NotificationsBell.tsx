/**
 * Notification bell (AGENTS.md §2.6, F8): unread count, live updates via the
 * WS `notification.created` event (query invalidation), explicit mark-read.
 *
 * Actionable rows: `invited` gets a Join button (opens the invite page);
 * every other group-scoped notification gets a View button that deep-links
 * into the group (`?tab=&focus=`) — the target tab is activated, the
 * mentioned row is scrolled into view and flash-highlighted (lib/focus.ts).
 * Both buttons mark the notification read.
 */

import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { useI18n } from '../i18n'
import { relativeTime } from '../lib/dates'
import { invitedToken, makeDescribe, notificationTarget } from '../lib/notificationText'
import { useMarkNotificationsRead, useNotifications } from '../query/queries'

export function NotificationsBell() {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()
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
              items.map((n) => {
                const token = invitedToken(n)
                const target = notificationTarget(n)
                return (
                  <div
                    key={n.id}
                    className={`flex items-start justify-between gap-2 px-3 py-2 border-b border-gray-100 dark:border-gray-800 last:border-0 ${n.read_at ? 'text-gray-500 dark:text-gray-400' : 'bg-indigo-50/50 dark:bg-indigo-950/40 text-gray-800 dark:text-gray-100'}`}
                  >
                    <div className="min-w-0">
                      <p className="text-sm">{describe(n.type, n.payload)}</p>
                      <p className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">{relativeTime(n.created_at, locale)}</p>
                    </div>
                    {token || target ? (
                      <div className="flex shrink-0 gap-1.5">
                        {token ? (
                          <button
                            type="button"
                            onClick={() => {
                              setOpen(false)
                              void markRead.mutate([n.id])
                              navigate(`/invite/${token}`)
                            }}
                            className="shrink-0 text-xs font-medium text-white bg-indigo-600 hover:bg-indigo-700 dark:bg-indigo-500 dark:hover:bg-indigo-400 rounded px-2 py-1"
                          >
                            {t('notif.join')}
                          </button>
                        ) : null}
                        {target ? (
                          <button
                            type="button"
                            onClick={() => {
                              setOpen(false)
                              void markRead.mutate([n.id])
                              navigate(`/groups/${target.group_id}?tab=${target.tab}&focus=${target.focus}`)
                            }}
                            className="shrink-0 text-xs font-medium text-indigo-600 dark:text-indigo-400 border border-indigo-200 dark:border-indigo-800 hover:bg-indigo-50 dark:hover:bg-indigo-950/40 rounded px-2 py-1"
                          >
                            {t('notif.view')}
                          </button>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                )
              })
            )}
          </div>
        </div>
      ) : null}
    </div>
  )
}

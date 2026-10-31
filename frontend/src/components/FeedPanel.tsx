/**
 * Activity feed (F7, AGENTS.md §3.3).
 *
 * Append-only group events, newest first, with cursor ("load more")
 * pagination. Soft-deleted expenses still render ("deleted an expense"). The
 * feed is invalidated by the WS bus on every group event so new rows appear
 * without a reload.
 *
 * Sentences are full i18n strings with an `{{actor}}` param (the RU word order
 * needs it), so the bold-actor span is rendered inline in the text instead.
 */

import { useI18n, type TFunc } from '../i18n'
import { formatMoney } from '../lib/money'
import { relativeTime } from '../lib/dates'
import { useFocusElement } from '../lib/focus'
import { Button, Card, CardHeader, EmptyState, Spinner } from './ui'
import { Avatar } from './Avatar'
import { useFeed } from '../query/queries'

function makeDescribe(t: TFunc, locale: string) {
  return (type: string, payload: Record<string, unknown> | null, actorName: string): string => {
    const p = payload ?? {}
    const str = (v: unknown): string => (typeof v === 'string' ? v : '')
    const name = (key: string): string => {
      const v = p[key]
      if (typeof v === 'string') return v
      if (v && typeof v === 'object' && 'display_name' in v) return str((v as { display_name: unknown }).display_name)
      return ''
    }
    const amount = (): string =>
      typeof p.amount === 'string' ? formatMoney(p.amount, str(p.currency) || 'EUR', locale) : ''

    switch (type) {
      case 'expense.created':
        return t('feed.expenseCreated', { actor: actorName, desc: str(p.description), amount: amount() })
      case 'expense.updated':
        return t('feed.expenseUpdated', { actor: actorName, desc: str(p.description), amount: amount() })
      case 'expense.deleted': {
        const base = t('feed.expenseDeleted', { actor: actorName })
        return str(p.description) ? base + t('feed.deletedDesc', { desc: str(p.description) }) : base
      }
      // For settlements the feed's "actor" is the one whose payment is meant
      // (the payer), even though the stored actor is who clicked the button
      // (e.g. the recipient who confirmed). The payload carries from_name.
      case 'settlement.created':
        return t('feed.settlementCreated', { actor: str(p.from_name) || actorName, amount: amount(), to: str(p.to_name) })
      case 'settlement.confirmed':
        return t('feed.settlementConfirmed', { actor: str(p.from_name) || actorName, amount: amount(), to: str(p.to_name) })
      case 'settlement.rejected':
        return t('feed.settlementRejected', { actor: str(p.from_name) || actorName, amount: amount() })
      case 'settlement.cancelled':
        return t('feed.settlementCancelled', { actor: str(p.from_name) || actorName, amount: amount() })
      case 'member.joined':
        // The payload is flat: {user_id, display_name} (backend groups.py).
        return t('feed.memberJoined', { actor: name('display_name') || t('notif.aMember') })
      case 'member.left':
        return t('feed.memberLeft', { actor: name('display_name') || t('notif.aMember') })
      case 'group.closed':
        return t('feed.groupClosed', { actor: actorName })
      case 'group.reopened':
        return t('feed.groupReopened', { actor: actorName })
      case 'group.owner_changed':
        return t('feed.ownerChanged', { actor: actorName, name: str(p.new_owner_name) })
      case 'group.created':
        return t('feed.groupCreated', { actor: actorName, name: str(p.name) })
      default:
        return type.replace(/_/g, ' ')
    }
  }
}

// The notification payload names the event type but not the feed row id, so
// a deep link highlights the NEWEST row of that type — the feed is newest
// first, so that is exactly the row the notification was raised for. Deleted
// expenses stay in the feed as their `expense.deleted` row (§3.3), so the
// removal's View button lands here, not on the (gone) expense.
const FOCUSABLE_FEED_TYPES = new Set([
  'expense.deleted',
  'member.joined',
  'member.left',
  'group.closed',
  'group.reopened',
  'group.owner_changed',
])

export function FeedPanel({ groupId, focusType = null }: { groupId: number; focusType?: string | null }) {
  const feed = useFeed(groupId)
  const { t, locale } = useI18n()
  const describe = makeDescribe(t, locale)
  // Deep link (notification View): scroll to + highlight the newest matching row.
  useFocusElement(
    focusType && FOCUSABLE_FEED_TYPES.has(focusType) ? `#feed-list [data-feed-type="${focusType}"]` : null,
  )

  if (feed.loading && feed.items.length === 0) {
    return <Spinner label={t('feed.loading')} />
  }

  if (feed.items.length === 0) {
    return (
      <Card>
        <EmptyState title={t('feed.emptyTitle')} hint={t('feed.emptyHint')} />
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader title={t('feed.title')} />
      <ol id="feed-list" className="divide-y divide-gray-100 dark:divide-gray-800">
        {feed.items.map((e) => (
          <li key={e.id} id={`feed-${e.id}`} data-feed-type={e.type} className="px-4 py-2.5 flex items-start gap-3">
            {e.actor_avatar_url ? (
              <Avatar url={e.actor_avatar_url} name={e.actor_name ?? t('common.someone')} size={24} />
            ) : (
              <span className="mt-1 h-2 w-2 rounded-full bg-indigo-400 shrink-0" aria-hidden="true" />
            )}
            <div className="min-w-0">
              <p className="text-sm text-gray-800 dark:text-gray-100">
                {describe(e.type, e.payload, e.actor_name ?? t('common.someone'))}
              </p>
              <p className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">{relativeTime(e.created_at, locale)}</p>
            </div>
          </li>
        ))}
      </ol>
      {feed.hasMore ? (
        <div className="px-4 py-3 text-center border-t border-gray-100 dark:border-gray-800">
          <Button variant="ghost" onClick={feed.loadMore} busy={feed.loadingMore}>
            {t('feed.loadOlder')}
          </Button>
        </div>
      ) : null}
    </Card>
  )
}

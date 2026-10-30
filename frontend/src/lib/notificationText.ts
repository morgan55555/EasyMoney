/**
 * Notification text & deep-link helpers (AGENTS.md §2.6, §2.14).
 *
 * Single source of truth for the one-line localized summary of a
 * notification (`makeDescribe`) and of where it leads (`invitedToken` for the
 * Join action, `notificationTarget` for the View action). Used by both the
 * in-app bell and the OS browser notification, so the two always render
 * identical text and navigate to the same target.
 */

import type { TFunc } from '../i18n/core'
import { formatMoney } from './money'
import type { NotificationOut } from '../types'

export function makeDescribe(t: TFunc, locale: string) {
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
      case 'invited':
        return t('notif.invited', {
          inviter: String(p.inviter_name ?? t('notif.aMember')),
          group: String(p.group_name ?? ''),
        })
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

/**
 * An email-invite notification ("invited") carries an actionable Join button:
 * the payload holds the invite token, and the button opens `/invite/{token}`
 * to join. Returns the token, or null for non-actionable notifications.
 */
export function invitedToken(n: NotificationOut): string | null {
  if (n.type !== 'invited') return null
  const tok = n.payload?.token
  return typeof tok === 'string' && tok !== '' ? tok : null
}

/**
 * The in-group target of a notification's View button (§2.6): the group to
 * open, the tab to activate, and the element to scroll to + highlight
 * (lib/focus.ts). `invited` has no target — its Join button opens the invite
 * page instead — and a payload without `group_id` cannot deep-link.
 */
export function notificationTarget(n: NotificationOut): { group_id: number; tab: string; focus: string } | null {
  const p = n.payload ?? {}
  const gid = p.group_id
  if (typeof gid !== 'number') return null
  switch (n.type) {
    case 'expense.created':
    case 'expense.updated':
      return typeof p.id === 'number' ? { group_id: gid, tab: 'expenses', focus: `expense:${p.id}` } : null
    case 'settlement.created':
    case 'settlement.confirmed':
    case 'settlement.rejected':
    case 'settlement.cancelled':
      return typeof p.id === 'number' ? { group_id: gid, tab: 'payments', focus: `settlement:${p.id}` } : null
    case 'member.joined':
      return { group_id: gid, tab: 'activity', focus: 'feed:member.joined' }
    case 'member.left':
      return { group_id: gid, tab: 'activity', focus: 'feed:member.left' }
    case 'group.closed':
      return { group_id: gid, tab: 'activity', focus: 'feed:group.closed' }
    case 'group.reopened':
      return { group_id: gid, tab: 'activity', focus: 'feed:group.reopened' }
    default:
      return null
  }
}

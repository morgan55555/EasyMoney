/**
 * Notification text & deep-link helpers (AGENTS.md §2.6, §2.14).
 *
 * `makeDescribe` is the single source of the one-line localized summary shown
 * by both the in-app bell and the OS browser notification — so the exact EN
 * strings (amounts formatted via the money helper, params interpolated) are
 * asserted here. `invitedToken` is the only notification that carries a Join
 * action; `notificationTarget` (the View deep-link) is exercised in the bell
 * test, which now imports it from this module.
 */

import { describe, expect, it } from 'vitest'

import { en } from '../../i18n/locales'
import type { NotificationOut } from '../../types'
import { invitedToken, makeDescribe, notificationTarget } from '../notificationText'

/** A `t` that reads the real EN dictionary with `{{param}}` interpolation. */
function makeT(): (key: string, params?: Record<string, string | number>) => string {
  return (key, params) => {
    const template = (en as Record<string, string>)[key] ?? key
    if (!params) return template
    return template.replace(/\{\{(\w+)\}\}/g, (m, name: string) => (name in params ? String(params[name]) : m))
  }
}

function n(id: number, type: string, payload: Record<string, unknown> | null, read = false): NotificationOut {
  return {
    id,
    type,
    payload,
    read_at: read ? '2026-01-02T00:00:00Z' : null,
    created_at: new Date().toISOString(),
  }
}

describe('makeDescribe (EN)', () => {
  const t = makeT()
  const describe = makeDescribe(t, 'en')

  it('expense.created names the payer when present', () => {
    expect(describe('expense.created', { payer: 'Alice', description: 'Lunch', amount: '33.34', currency: 'EUR' })).toBe(
      'Alice added: Lunch (33.34 EUR)',
    )
  })

  it('expense.created falls back to the generic label without a payer', () => {
    expect(describe('expense.created', { description: 'Groceries', amount: '10.00', currency: 'EUR' })).toBe(
      'New expense: Groceries (10.00 EUR)',
    )
  })

  it('expense.updated reports the new amount', () => {
    expect(describe('expense.updated', { description: 'Lunch', amount: '33.34', currency: 'EUR' })).toBe(
      'Expense updated: Lunch (33.34 EUR)',
    )
  })

  it('expense.deleted names the expense (or the generic fallback)', () => {
    expect(describe('expense.deleted', { description: 'Lunch' })).toBe('Expense deleted: Lunch')
    expect(describe('expense.deleted', null)).toBe('Expense deleted: an expense')
  })

  it('settlement.created / confirmed name both parties', () => {
    const p = { from_name: 'Bob', to_name: 'Alice', amount: '10.00', currency: 'EUR' }
    expect(describe('settlement.created', p)).toBe('Bob proposed a payment of 10.00 EUR to Alice')
    expect(describe('settlement.confirmed', p)).toBe('Bob settled with Alice (10.00 EUR)')
  })

  it('settlement.rejected / cancelled report only the amount', () => {
    expect(describe('settlement.rejected', { amount: '10.00', currency: 'EUR' })).toBe(
      'A payment of 10.00 EUR was rejected',
    )
    expect(describe('settlement.cancelled', { amount: '10.00', currency: 'EUR' })).toBe(
      'A payment of 10.00 EUR was cancelled',
    )
  })

  it('invited names the inviter and the group', () => {
    expect(describe('invited', { inviter_name: 'Carol', group_name: 'Trip' })).toBe(
      'Carol invited you to join Trip',
    )
  })

  it('member.joined / member.left name the member (or the generic fallback)', () => {
    expect(describe('member.joined', { display_name: 'Dave' })).toBe('Dave joined the group')
    expect(describe('member.left', { display_name: 'Dave' })).toBe('Dave left the group')
    expect(describe('member.joined', null)).toBe('A member joined the group')
  })

  it('group.closed / group.reopened are fixed strings', () => {
    expect(describe('group.closed', null)).toBe('The group was closed')
    expect(describe('group.reopened', null)).toBe('The group was reopened')
  })

  it('an unknown type degrades to its humanized name (underscores become spaces)', () => {
    expect(describe('some_unknown_type', null)).toBe('some unknown type')
  })

  it('formats zero- and three-digit currencies without a locale', () => {
    expect(describe('expense.created', { description: 'Taxi', amount: '1000', currency: 'JPY' })).toBe(
      'New expense: Taxi (1,000 JPY)',
    )
    expect(describe('expense.created', { description: 'Fuel', amount: '1234.567', currency: 'KWD' })).toBe(
      'New expense: Fuel (1,234.567 KWD)',
    )
  })
})

describe('invitedToken', () => {
  it('returns the token only for an "invited" notification with a non-empty token', () => {
    expect(invitedToken(n(1, 'invited', { group_id: 5, token: 'abc123' }))).toBe('abc123')
    expect(invitedToken(n(1, 'invited', { group_id: 5, token: '' }))).toBeNull()
    expect(invitedToken(n(1, 'invited', { group_id: 5 }))).toBeNull()
    expect(invitedToken(n(1, 'invited', null))).toBeNull()
  })

  it('returns null for every other type, even with a token in the payload', () => {
    expect(invitedToken(n(1, 'expense.created', { id: 2, group_id: 5, token: 'abc123' }))).toBeNull()
    expect(invitedToken(n(1, 'member.joined', { group_id: 5, token: 'abc123' }))).toBeNull()
  })
})

describe('notificationTarget', () => {
  it('maps group-scoped notifications to their in-group tab and focus', () => {
    expect(notificationTarget(n(1, 'expense.created', { id: 12, group_id: 5 }))).toEqual({
      group_id: 5,
      tab: 'expenses',
      focus: 'expense:12',
    })
    expect(notificationTarget(n(1, 'settlement.confirmed', { id: 4, group_id: 5 }))).toEqual({
      group_id: 5,
      tab: 'payments',
      focus: 'settlement:4',
    })
    expect(notificationTarget(n(1, 'expense.deleted', { id: 12, group_id: 5 }))).toEqual({
      group_id: 5,
      tab: 'activity',
      focus: 'feed:expense.deleted',
    })
    expect(notificationTarget(n(1, 'member.joined', { group_id: 5 }))).toEqual({
      group_id: 5,
      tab: 'activity',
      focus: 'feed:member.joined',
    })
    expect(notificationTarget(n(1, 'group.closed', { group_id: 5 }))).toEqual({
      group_id: 5,
      tab: 'activity',
      focus: 'feed:group.closed',
    })
  })

  it('has no target for "invited" (Join opens the invite page) or a missing id/group_id', () => {
    expect(notificationTarget(n(1, 'invited', { group_id: 5, token: 't' }))).toBeNull()
    expect(notificationTarget(n(1, 'expense.created', { id: 12 }))).toBeNull()
    expect(notificationTarget(n(1, 'member.joined', null))).toBeNull()
  })
})

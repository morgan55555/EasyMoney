/**
 * SettlementsPanel (AGENTS.md §2.4, §6.2): the lifecycle is
 * `pending → confirmed | rejected | cancelled`. Only the RECIPIENT can confirm
 * or reject; only the SENDER can cancel; a third party sees a passive badge.
 * Confirmed/rejected/cancelled rows move to History. The confirm action opens
 * a modal that portals to <body>.
 */

import { createElement } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import type { QueryClient } from '@tanstack/react-query'

import type { SettlementOut, SettlementState, UserOut } from '../../types'
import { keys } from '../../query/queries'
import { SettlementsPanel } from '../SettlementsPanel'
import { render, hasText, buttonsWithText, clickByText, type RenderResult } from '../../test/harness'

const GROUP = 1

function me(id = 1): UserOut {
  return {
    id,
    email: 'a@test.local',
    display_name: 'Alice',
    created_at: '2026-01-01T00:00:00Z',
    is_admin: true,
    email_verified: true,
    preferred_language: 'en',
    preferred_currency: null,
    avatar_url: null,
    email_change_confirmation: false,
    password_change_confirmation: false,
  }
}

function settlement(p: Partial<SettlementOut> & Pick<SettlementOut, 'from_user_id' | 'to_user_id'>): SettlementOut {
  return {
    id: 100,
    group_id: GROUP,
    from_name: 'Bob',
    from_avatar_url: null,
    to_name: 'Alice',
    to_avatar_url: null,
    amount: '25.00',
    state: 'pending',
    confirmed_at: null,
    created_at: '2026-01-02T12:00:00Z',
    ...p,
  }
}

function baseSeed(settlements: SettlementOut[]) {
  return (qc: QueryClient) => {
    qc.setQueryData(keys.me, me(1))
    qc.setQueryData(keys.group(GROUP), {
      id: GROUP,
      name: 'Trip',
      currency: 'EUR',
      status: 'active',
      created_by: 1,
      closed_at: null,
      member_count: 3,
      balance: '0.00',
    })
    qc.setQueryData(keys.balances(GROUP), {
      currency: 'EUR',
      balances: [],
      pairs: [],
      transfers: [],
    })
    qc.setQueryData(keys.settlements(GROUP), settlements)
  }
}

let r: RenderResult | null = null
afterEach(() => {
  r?.unmount()
  r = null
})

describe('SettlementsPanel', () => {
  it('the recipient sees Reject + Confirm and the waiting-you line', () => {
    // me = 1 (Alice); settlement Bob(2) → Alice(1), pending.
    r = render(
      createElement(SettlementsPanel, { groupId: GROUP }),
      { seed: baseSeed([settlement({ id: 1, from_user_id: 2, to_user_id: 1 })]) }
    )
    expect(hasText(r.body, 'Waiting for you to confirm or reject.')).toBe(true)
    expect(buttonsWithText(r.body, 'Reject').length).toBe(1)
    expect(buttonsWithText(r.body, 'Confirm').length).toBe(1)
    expect(buttonsWithText(r.body, 'Cancel').length).toBe(0)
  })

  it('the sender sees only Cancel and the you-proposed line', () => {
    // me = 1 (Alice); settlement Alice(1) → Bob(2), pending.
    r = render(
      createElement(SettlementsPanel, { groupId: GROUP }),
      { seed: baseSeed([settlement({ id: 1, from_user_id: 1, to_user_id: 2 })]) }
    )
    expect(hasText(r.body, 'You proposed this — waiting for the other person to confirm.')).toBe(true)
    expect(buttonsWithText(r.body, 'Cancel').length).toBe(1)
    expect(buttonsWithText(r.body, 'Confirm').length).toBe(0)
    expect(buttonsWithText(r.body, 'Reject').length).toBe(0)
  })

  it('a third party sees a passive pending badge and no action buttons', () => {
    // me = 1 (Alice); settlement Bob(2) → Carol(3), pending — Alice is not involved.
    r = render(
      createElement(SettlementsPanel, { groupId: GROUP }),
      { seed: baseSeed([settlement({ id: 1, from_user_id: 2, to_user_id: 3, to_name: 'Carol' })]) }
    )
    expect(hasText(r.body, 'pending')).toBe(true)
    expect(buttonsWithText(r.body, 'Confirm').length).toBe(0)
    expect(buttonsWithText(r.body, 'Reject').length).toBe(0)
    expect(buttonsWithText(r.body, 'Cancel').length).toBe(0)
  })

  it('a confirmed settlement appears in History with a confirmed badge, not in Pending', () => {
    r = render(
      createElement(SettlementsPanel, { groupId: GROUP }),
      {
        seed: baseSeed([
          settlement({ id: 1, from_user_id: 2, to_user_id: 1, state: 'confirmed' as SettlementState, confirmed_at: '2026-01-03T09:00:00Z' }),
        ]),
      }
    )
    expect(hasText(r.body, 'No pending payments.')).toBe(true)
    expect(hasText(r.body, 'History')).toBe(true)
    expect(hasText(r.body, 'confirmed')).toBe(true)
  })

  it('clicking Confirm (as recipient) opens the confirm modal portaled to <body>', () => {
    r = render(
      createElement(SettlementsPanel, { groupId: GROUP }),
      { seed: baseSeed([settlement({ id: 1, from_user_id: 2, to_user_id: 1 })]) }
    )
    clickByText(r.body, 'Confirm')
    // The modal portals to document.body with the confirm title + body copy.
    expect(hasText(r.body, 'Confirm settlement')).toBe(true)
    expect(hasText(r.body, 'Confirm that Bob paid you 25.00 EUR?')).toBe(true)
  })
})

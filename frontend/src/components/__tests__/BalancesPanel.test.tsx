/**
 * BalancesPanel (AGENTS.md §2.2/§2.3, §6.2): renders the member balances, the
 * optimizer's suggested transfers (with a Settle affordance on rows the viewer
 * owes), and the empty "all settled up" state. Money is displayed from the API
 * strings; the rendered balances still sum to zero (invariant 1 on the wire).
 */

import { createElement } from 'react'
import { afterEach, describe, expect, it } from 'vitest'

import type { UserOut } from '../../types'
import { keys } from '../../query/queries'
import { BalancesPanel } from '../BalancesPanel'
import { render, hasText, buttonsWithText, type RenderResult } from '../../test/harness'
import { toMinorUnits } from '../../lib/money'

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
    avatar_url: null,
    email_change_confirmation: false,
    password_change_confirmation: false,
  }
}

let r: RenderResult | null = null
afterEach(() => {
  r?.unmount()
  r = null
})

describe('BalancesPanel', () => {
  it('renders member balances, suggested transfers and a Settle button on the row the viewer owes', () => {
    r = render(
      createElement(BalancesPanel, { groupId: GROUP }),
      {
        seed: (qc) => {
          qc.setQueryData(keys.me, me(1))
          qc.setQueryData(keys.balances(GROUP), {
            currency: 'EUR',
            balances: [
              { user_id: 1, display_name: 'Alice', balance: '-50.00', avatar_url: null },
              { user_id: 2, display_name: 'Bob', balance: '30.00', avatar_url: null },
              { user_id: 3, display_name: 'Carol', balance: '20.00', avatar_url: null },
            ],
            pairs: [
              { from_user_id: 1, to_user_id: 2, amount: '30.00' },
              { from_user_id: 1, to_user_id: 3, amount: '20.00' },
            ],
            transfers: [
              { from_user_id: 1, to_user_id: 2, amount: '30.00' },
              { from_user_id: 1, to_user_id: 3, amount: '20.00' },
            ],
          })
        },
      },
    )
    // Your position.
    expect(hasText(r.body, 'Your balance')).toBe(true)
    expect(hasText(r.body, '-50.00 EUR')).toBe(true)
    expect(hasText(r.body, 'You owe the group this much.')).toBe(true)
    // Suggested transfers (the optimizer) section.
    expect(hasText(r.body, 'Suggested transfers')).toBe(true)
    expect(hasText(r.body, '30.00 EUR')).toBe(true)
    expect(hasText(r.body, '20.00 EUR')).toBe(true)
    // Both suggested rows are owed BY the viewer (from_user_id = me) → Settle shown.
    expect(buttonsWithText(r.body, 'Settle').length).toBe(2)
    // Who-owes-whom pairwise section is present.
    expect(hasText(r.body, 'Who owes whom')).toBe(true)
  })

  it('the rendered member balances sum to zero (invariant 1 holds on the wire)', () => {
    const balances = ['-50.00', '30.00', '20.00']
    // Same data as above; verify the wire values the panel would render net out.
    expect(balances.reduce((s, b) => s + toMinorUnits(b, 'EUR'), 0)).toBe(0)
    r = render(
      createElement(BalancesPanel, { groupId: GROUP }),
      {
        seed: (qc) => {
          qc.setQueryData(keys.me, me(1))
          qc.setQueryData(keys.balances(GROUP), {
            currency: 'EUR',
            balances: balances.map((b, i) => ({
              user_id: i + 1,
              display_name: `U${i + 1}`,
              balance: b,
              avatar_url: null,
            })),
            pairs: [],
            transfers: [],
          })
        },
      },
    )
    // Every balance is rendered with its currency code.
    for (const b of balances) {
      const signed = b.startsWith('-') ? b : `+${b}`
      expect(hasText(r.body, `${signed} EUR`)).toBe(true)
    }
  })

  it('shows the "all settled up" empty state when every balance is zero', () => {
    r = render(
      createElement(BalancesPanel, { groupId: GROUP }),
      {
        seed: (qc) => {
          qc.setQueryData(keys.me, me(1))
          qc.setQueryData(keys.balances(GROUP), {
            currency: 'USD',
            balances: [
              { user_id: 1, display_name: 'Alice', balance: '0.00', avatar_url: null },
              { user_id: 2, display_name: 'Bob', balance: '0.00', avatar_url: null },
            ],
            pairs: [],
            transfers: [],
          })
        },
      },
    )
    expect(hasText(r.body, 'All settled up')).toBe(true)
    expect(hasText(r.body, "No one owes anyone in this group.")).toBe(true)
    expect(hasText(r.body, 'Everyone is settled up.')).toBe(true)
    // No per-member / suggested-transfer sections when settled.
    expect(hasText(r.body, 'Suggested transfers')).toBe(false)
    expect(buttonsWithText(r.body, 'Settle').length).toBe(0)
  })
})

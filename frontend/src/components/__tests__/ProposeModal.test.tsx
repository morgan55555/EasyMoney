/**
 * ProposeModal (AGENTS.md §2.4, §6.2): the amount is capped at the debt to the
 * chosen recipient MINUS the caller's still-pending proposals to the same
 * recipient — the max sendable sum decreases with every active proposal.
 * Recipients whose pending proposals already cover the debt are dropped from
 * the "Pay to" list; when that is everyone, the noHeadroom empty state shows.
 * All the cap math is integer minor units (no float money math, §3.6).
 */

import { createElement } from 'react'
import { afterEach, describe, expect, it } from 'vitest'

import type { GroupBalancesOut, SettlementOut } from '../../types'
import { ProposeModal } from '../ProposeModal'
import {
  render,
  hasText,
  labeledSelect,
  labeledInput,
  type RenderResult,
} from '../../test/harness'

const GROUP = 1
const ME = 1 // Alice — the caller

/** A settlement row; only the fields ProposeModal reads matter. */
function pendingProposal(p: Pick<SettlementOut, 'from_user_id' | 'to_user_id'> & Partial<SettlementOut>): SettlementOut {
  return {
    id: 100,
    group_id: GROUP,
    from_name: 'Alice',
    from_avatar_url: null,
    to_name: 'Recipient',
    to_avatar_url: null,
    amount: '0.00',
    state: 'pending',
    confirmed_at: null,
    created_at: '2026-01-02T12:00:00Z',
    ...p,
  }
}

/** Alice(1) owes Bob(2) 50.00 and Carol(3) 30.00 in the current pairwise view. */
function balances(): GroupBalancesOut {
  return {
    currency: 'EUR',
    balances: [
      { user_id: 1, display_name: 'Alice', balance: '-80.00', avatar_url: null },
      { user_id: 2, display_name: 'Bob', balance: '50.00', avatar_url: null },
      { user_id: 3, display_name: 'Carol', balance: '30.00', avatar_url: null },
    ],
    pairs: [
      { from_user_id: 1, to_user_id: 2, amount: '50.00' },
      { from_user_id: 1, to_user_id: 3, amount: '30.00' },
    ],
    transfers: [
      { from_user_id: 1, to_user_id: 2, amount: '50.00' },
      { from_user_id: 1, to_user_id: 3, amount: '30.00' },
    ],
  }
}

let r: RenderResult | null = null
afterEach(() => {
  r?.unmount()
  r = null
})

describe('ProposeModal', () => {
  it('caps the amount at the debt minus the caller\'s pending proposals to that recipient', () => {
    // Alice owes Bob 50.00 but has a 20.00 proposal pending to Bob, so the
    // headroom is 30.00 — that is the maximum a new proposal may reach.
    r = render(
      createElement(ProposeModal, {
        groupId: GROUP,
        meId: ME,
        balances: balances(),
        myPending: [pendingProposal({ id: 1, from_user_id: 1, to_user_id: 2, amount: '20.00', to_name: 'Bob' })],
        initialTo: 2,
        onClose: () => {},
      }),
    )
    expect(hasText(r.body, 'Propose a payment')).toBe(true)
    expect(hasText(r.body, 'You can propose up to 30.00 EUR.')).toBe(true)
    // The not-yet-prefilled amount starts empty (no initialAmount given).
    expect((labeledInput(r.body, 'Amount (EUR)') as HTMLInputElement).value).toBe('')
  })

  it('drops a recipient whose pending proposals already cover the debt', () => {
    // Carol(3) is fully covered by a 30.00 pending proposal, so only Bob(2)
    // (50.00 debt, no pending) remains in the "Pay to" list.
    r = render(
      createElement(ProposeModal, {
        groupId: GROUP,
        meId: ME,
        balances: balances(),
        myPending: [pendingProposal({ id: 1, from_user_id: 1, to_user_id: 3, amount: '30.00', to_name: 'Carol' })],
        initialTo: 2,
        onClose: () => {},
      }),
    )
    const options = Array.from(labeledSelect(r.body, 'Pay to').querySelectorAll('option')).map(
      (o) => o.textContent ?? '',
    )
    expect(options.some((t) => t.includes('Bob'))).toBe(true)
    expect(options.some((t) => t.includes('Carol'))).toBe(false)
    // The cap is per recipient: Bob is unaffected by Carol's covered debt.
    expect(hasText(r.body, 'You can propose up to 50.00 EUR.')).toBe(true)
  })

  it('shows the noHeadroom state when every creditor is fully covered by pending proposals', () => {
    // Alice owes Bob 30.00 (only one creditor) and a 30.00 proposal to Bob is
    // pending: nothing is left to propose.
    const b = balances()
    b.pairs = [{ from_user_id: 1, to_user_id: 2, amount: '30.00' }]
    b.transfers = [{ from_user_id: 1, to_user_id: 2, amount: '30.00' }]
    r = render(
      createElement(ProposeModal, {
        groupId: GROUP,
        meId: ME,
        balances: b,
        myPending: [pendingProposal({ id: 1, from_user_id: 1, to_user_id: 2, amount: '30.00', to_name: 'Bob' })],
        onClose: () => {},
      }),
    )
    expect(hasText(r.body, 'Your pending proposals already cover everything you owe in this group.')).toBe(true)
    // No "Pay to" field is rendered in the noHeadroom state.
    expect(hasText(r.body, 'Pay to')).toBe(false)
  })

  it('never prefills an initialAmount above the remaining headroom', () => {
    // Headroom to Bob is 50.00 - 20.00 = 30.00; an initialAmount of 40.00 is
    // clamped down to the headroom.
    r = render(
      createElement(ProposeModal, {
        groupId: GROUP,
        meId: ME,
        balances: balances(),
        myPending: [pendingProposal({ id: 1, from_user_id: 1, to_user_id: 2, amount: '20.00', to_name: 'Bob' })],
        initialTo: 2,
        initialAmount: '40.00',
        onClose: () => {},
      }),
    )
    expect((labeledInput(r.body, 'Amount (EUR)') as HTMLInputElement).value).toBe('30.00')
  })
})

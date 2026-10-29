/**
 * ExpenseForm (AGENTS.md §2.1, §6.2): the live split preview must match the
 * server's exact rules —
 *   - equal: 10.01 across 3 → 3.34 / 3.34 / 3.33 (leftover units go to the
 *     earliest joiners, in join order);
 *   - weighted [1,2,2]: largest-remainder → 2.00 / 4.01 / 4.00;
 *   - exact: the "Shares total" line shows the mismatch against the amount and
 *     flips to "matches the amount" once they line up;
 *   - zero-digit currency (JPY): no decimal point in the preview.
 * The submit button is enabled only once description, amount and a payer are
 * all set. No network is involved — the preview is pure client-side math.
 */

import { createElement } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import type { QueryClient } from '@tanstack/react-query'

import type { MemberOut, UserOut } from '../../types'
import { keys } from '../../query/queries'
import { ExpenseForm } from '../ExpenseForm'
import {
  render,
  typeInput,
  clickByText,
  labeledInput,
  buttonDisabled,
  hasText,
  countText,
  type RenderResult,
} from '../../test/harness'

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

function member(id: number, display_name: string): MemberOut {
  return {
    id,
    display_name,
    email: `${display_name.toLowerCase()}@test.local`,
    joined_at: '2026-01-01T00:00:00Z', // same instant → tie-break is user_id
    is_owner: id === 1,
    avatar_url: null,
  }
}

const members = [member(1, 'Alice'), member(2, 'Bob'), member(3, 'Carol')]

const seed = (qc: QueryClient): void => {
  qc.setQueryData(keys.me, me(1))
}

/** The member row (the div holding the "Include <name>" checkbox). */
function row(root: ParentNode, name: string): HTMLElement {
  const cb = root.querySelector(`input[aria-label="Include ${name}"]`) as HTMLInputElement | null
  expect(cb, `no row for member ${name}`).not.toBeNull()
  return cb!.parentElement as HTMLElement
}

let r: RenderResult | null = null
afterEach(() => {
  r?.unmount()
  r = null
})

describe('ExpenseForm split preview (UI parity with the server rule)', () => {
  it('equal split of 10.01 across 3 shows 3.34/3.34/3.33 and enables submit only with a description', () => {
    r = render(
      createElement(ExpenseForm, { groupId: GROUP, members, currency: 'EUR', onDone: () => undefined }),
      { seed },
    )
    typeInput(labeledInput(r.container, 'Amount (EUR)'), '10.01')
    // r = 1 leftover minor unit → the two earliest joiners get 3.34.
    expect(countText(r.container, '3.34 EUR')).toBe(2)
    expect(countText(r.container, '3.33 EUR')).toBe(1)
    // Submit is still disabled: no description yet.
    expect(buttonDisabled(r.container, 'Add expense')).toBe(true)
    typeInput(labeledInput(r.container, 'Description'), 'Dinner')
    expect(buttonDisabled(r.container, 'Add expense')).toBe(false)
  })

  it('weighted split [1,2,2] of 10.01 uses largest-remainder → 2.00/4.01/4.00', () => {
    r = render(
      createElement(ExpenseForm, { groupId: GROUP, members, currency: 'EUR', onDone: () => undefined }),
      { seed },
    )
    clickByText(r.container, 'By weight')
    typeInput(row(r.container, 'Bob').querySelector('input[placeholder="1"]') as HTMLInputElement, '2')
    typeInput(row(r.container, 'Carol').querySelector('input[placeholder="1"]') as HTMLInputElement, '2')
    typeInput(labeledInput(r.container, 'Amount (EUR)'), '10.01')
    // raw = [2.002, 4.004, 4.004]; the single leftover unit goes to Bob
    // (largest remainder, tie with Carol broken by join order).
    expect(countText(r.container, '2.00 EUR')).toBe(1)
    expect(countText(r.container, '4.01 EUR')).toBe(1)
    expect(countText(r.container, '4.00 EUR')).toBe(1)
  })

  it('exact amounts: the total line shows the mismatch, then matches when the amount lines up', () => {
    r = render(
      createElement(ExpenseForm, { groupId: GROUP, members, currency: 'EUR', onDone: () => undefined }),
      { seed },
    )
    clickByText(r.container, 'Exact amounts')
    typeInput(labeledInput(r.container, 'Amount (EUR)'), '10.00')
    typeInput(row(r.container, 'Alice').querySelector('input[placeholder="0.00"]') as HTMLInputElement, '4.00')
    typeInput(row(r.container, 'Bob').querySelector('input[placeholder="0.00"]') as HTMLInputElement, '3.50')
    typeInput(row(r.container, 'Carol').querySelector('input[placeholder="0.00"]') as HTMLInputElement, '3.00')
    expect(hasText(r.container, 'Shares total 10.50 EUR — expected 10.00 EUR.')).toBe(true)
    typeInput(labeledInput(r.container, 'Amount (EUR)'), '10.50')
    expect(hasText(r.container, 'Shares total 10.50 EUR — matches the amount.')).toBe(true)
  })

  it('zero-digit currency (JPY): 100 across 3 → 34/33/33 with no decimal point', () => {
    r = render(
      createElement(ExpenseForm, { groupId: GROUP, members, currency: 'JPY', onDone: () => undefined }),
      { seed },
    )
    typeInput(labeledInput(r.container, 'Amount (JPY)'), '100')
    expect(countText(r.container, '34 JPY')).toBe(1)
    expect(countText(r.container, '33 JPY')).toBe(2)
    expect(hasText(r.container, '.0 JPY')).toBe(false)
  })
})

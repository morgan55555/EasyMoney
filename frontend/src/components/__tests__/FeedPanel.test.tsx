/**
 * FeedPanel (AGENTS.md §3.3, §6.2): renders the described activity rows
 * (expense created, soft-deleted expense keeps its description, member joined),
 * shows "Load older" only while a next cursor exists, and the empty state
 * otherwise. Data comes from the seeded `keys.feed(id, null)` first page.
 */

import { createElement } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import type { QueryClient } from '@tanstack/react-query'

import type { ActivityEventOut } from '../../types'
import { keys } from '../../query/queries'
import { FeedPanel } from '../FeedPanel'
import { render, hasText, buttonsWithText, type RenderResult } from '../../test/harness'

const GROUP = 1

function ev(id: number, type: string, actor: string, payload: Record<string, unknown> | null): ActivityEventOut {
  return {
    id,
    type,
    actor_id: actor ? 1 : null,
    actor_name: actor ? actor : null,
    actor_avatar_url: null,
    ref_type: null,
    ref_id: null,
    payload,
    created_at: new Date().toISOString(), // "just now"
  }
}

function seedFeed(items: ActivityEventOut[], next_cursor: string | null) {
  return (qc: QueryClient): void => {
    qc.setQueryData(keys.feed(GROUP, null), { items, next_cursor })
  }
}

let r: RenderResult | null = null
afterEach(() => {
  r?.unmount()
  r = null
})

describe('FeedPanel', () => {
  it('renders the described rows and a "Load older" button while a next cursor exists', () => {
    r = render(
      createElement(FeedPanel, { groupId: GROUP }),
      {
        seed: seedFeed(
          [
            ev(1, 'expense.created', 'Bob', { description: 'Dinner', amount: '42.00', currency: 'EUR' }),
            ev(2, 'expense.deleted', 'Alice', { description: 'Dinner' }),
            ev(3, 'member.joined', 'Carol', { user_id: 3, display_name: 'Carol' }),
          ],
          'cursor-2',
        ),
      },
    )
    expect(hasText(r.container, 'Bob added \u201CDinner\u201D (42.00 EUR)')).toBe(true)
    // Soft-deleted expenses keep their description in the feed (§3.3).
    expect(hasText(r.container, 'Alice deleted an expense: \u201CDinner\u201D')).toBe(true)
    expect(hasText(r.container, 'Carol joined the group')).toBe(true)
    expect(buttonsWithText(r.container, 'Load older').length).toBe(1)
  })

  it('hides "Load older" when there is no next cursor', () => {
    r = render(
      createElement(FeedPanel, { groupId: GROUP }),
      {
        seed: seedFeed(
          [ev(1, 'member.joined', 'Carol', { user_id: 3, display_name: 'Carol' })],
          null,
        ),
      },
    )
    expect(buttonsWithText(r.container, 'Load older').length).toBe(0)
  })

  it('shows the empty state when there is no activity', () => {
    r = render(createElement(FeedPanel, { groupId: GROUP }), { seed: seedFeed([], null) })
    expect(hasText(r.container, 'No activity yet')).toBe(true)
    expect(hasText(r.container, 'Expenses, payments and member changes will appear here.')).toBe(true)
  })
})

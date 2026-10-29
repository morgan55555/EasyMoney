/**
 * XSS guard (§6.2): hostile server strings (display names, expense
 * descriptions, invite emails/URLs) must render as inert text — React escapes
 * them, so no <script>/<img> elements may ever appear, while the raw string is
 * still visible as text content.
 */

import { createElement } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import type { QueryClient } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'

import type { ActivityEventOut, InviteOut, NotificationOut, UserOut } from '../../types'
import { keys } from '../../query/queries'
import { FeedPanel } from '../FeedPanel'
import { NotificationsBell } from '../NotificationsBell'
import { BalancesPanel } from '../BalancesPanel'
import { InvitePanel } from '../InvitePanel'
import { render, click, hasText, type RenderResult } from '../../test/harness'

const SCRIPT = '<script>alert(1)</script>'
const IMG = '<img src=x onerror=alert(1)>'

function me(): UserOut {
  return {
    id: 1,
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

function noInjectedElements(): void {
  expect(document.body.querySelectorAll('script').length).toBe(0)
  expect(document.body.querySelectorAll('img[src="x"]').length).toBe(0)
}

let r: RenderResult | null = null
afterEach(() => {
  r?.unmount()
  r = null
})

describe('XSS guard', () => {
  it('feed renders a hostile actor name and description as inert text', () => {
    r = render(createElement(FeedPanel, { groupId: 1 }), {
      seed: (qc: QueryClient) => {
        const item: ActivityEventOut = {
          id: 1,
          type: 'expense.created',
          actor_id: 1,
          actor_name: SCRIPT,
          actor_avatar_url: null,
          ref_type: null,
          ref_id: null,
          payload: { description: IMG, amount: '1.00', currency: 'EUR' },
          created_at: new Date().toISOString(),
        }
        qc.setQueryData(keys.feed(1, null), { items: [item], next_cursor: null })
      },
    })
    noInjectedElements()
    expect(hasText(r.container, SCRIPT)).toBe(true)
    expect(hasText(r.container, IMG)).toBe(true)
  })

  it('notifications render a hostile description as inert text', () => {
    r = render(createElement(MemoryRouter, null, createElement(NotificationsBell)), {
      seed: (qc: QueryClient) => {
        const item: NotificationOut = {
          id: 1,
          type: 'expense.deleted',
          payload: { description: SCRIPT },
          read_at: null,
          created_at: new Date().toISOString(),
        }
        qc.setQueryData(keys.notifications, { items: [item], unread_count: 1 })
      },
    })
    // Open the panel so the item is described.
    click(r.container.querySelector('button[aria-label]') as HTMLButtonElement)
    noInjectedElements()
    expect(hasText(r.container, `Expense deleted: ${SCRIPT}`)).toBe(true)
  })

  it('balances and invites render hostile names/emails/URLs as inert text', () => {
    r = render(createElement(BalancesPanel, { groupId: 1 }), {
      seed: (qc: QueryClient) => {
        qc.setQueryData(keys.me, me())
        qc.setQueryData(keys.balances(1), {
          currency: 'USD',
          balances: [
            { user_id: 1, display_name: SCRIPT, balance: '-1.00', avatar_url: null },
            { user_id: 2, display_name: 'Bob', balance: '1.00', avatar_url: null },
          ],
          pairs: [{ from_user_id: 1, to_user_id: 2, amount: '1.00' }],
          transfers: [{ from_user_id: 1, to_user_id: 2, amount: '1.00' }],
        })
      },
    })
    noInjectedElements()
    expect(hasText(r.container, SCRIPT)).toBe(true)

    r.unmount()
    const inv: InviteOut = {
      id: 1,
      kind: 'email',
      token: 'tok1',
      url: `http://localhost:8081/invite/${SCRIPT}`,
      email_to: IMG,
      created_by: 1,
      expires_at: '2026-11-04T12:00:00Z',
      status: 'active',
      used_by: null,
      used_at: null,
    }
    r = render(createElement(InvitePanel, { groupId: 1 }), {
      seed: (qc: QueryClient) => {
        qc.setQueryData(keys.invites(1), [inv])
      },
    })
    noInjectedElements()
    expect(hasText(r.container, IMG)).toBe(true)
    expect(hasText(r.container, SCRIPT)).toBe(true)
  })
})

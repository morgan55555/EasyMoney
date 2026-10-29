/**
 * NotificationsBell (AGENTS.md §2.6, §6.2): the unread count drives the badge
 * and the button's aria-label; the panel (rendered in place, not portaled)
 * shows the described items and "Mark all read"; marking read posts to the
 * endpoint and the invalidation refetch clears the badge.
 */

import { createElement } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import type { QueryClient } from '@tanstack/react-query'
import { MemoryRouter, useLocation } from 'react-router-dom'

import type { NotificationOut } from '../../types'
import { keys } from '../../query/queries'
import { NotificationsBell } from '../NotificationsBell'
import {
  render,
  click,
  clickByText,
  hasText,
  flush,
  stubFetch,
  type RenderResult,
} from '../../test/harness'

function n(id: number, type: string, payload: Record<string, unknown> | null, read = false): NotificationOut {
  return {
    id,
    type,
    payload,
    read_at: read ? '2026-01-02T00:00:00Z' : null,
    created_at: new Date().toISOString(), // "just now"
  }
}

function bellButton(r: RenderResult): HTMLButtonElement {
  const btn = r.container.querySelector('button[aria-label]') as HTMLButtonElement | null
  if (!btn) throw new Error('no bell button')
  return btn
}

let r: RenderResult | null = null
afterEach(() => {
  r?.unmount()
  r = null
})

describe('NotificationsBell', () => {
  it('shows the unread badge in the aria-label and the described items after opening', () => {
    r = render(createElement(MemoryRouter, null, createElement(NotificationsBell)), {
      seed: (qc: QueryClient) => {
        qc.setQueryData(keys.notifications, {
          items: [
            n(1, 'expense.created', { payer: 'Bob', description: 'Dinner', amount: '42.00', currency: 'EUR' }),
            n(2, 'member.joined', { display_name: 'Carol' }),
          ],
          unread_count: 2,
        })
      },
    })
    expect(bellButton(r).getAttribute('aria-label')).toBe('Notifications (2 unread)')
    click(bellButton(r))
    expect(hasText(r.container, 'Bob added: Dinner (42.00 EUR)')).toBe(true)
    expect(hasText(r.container, 'Carol joined the group')).toBe(true)
    expect(hasText(r.container, 'Mark all read')).toBe(true)
  })

  it('mark all read posts to the endpoint and clears the badge on the refetched list', async () => {
    const res = render(createElement(MemoryRouter, null, createElement(NotificationsBell)), {
      seed: (qc: QueryClient) => {
        qc.setQueryData(keys.notifications, {
          items: [n(1, 'member.joined', { display_name: 'Bob' })],
          unread_count: 1,
        })
      },
    })
    r = res
    // Specific fragment first: /api/notifications/read before /api/notifications.
    const stub = stubFetch({
      '/api/notifications/read': { marked: 1 },
      '/api/notifications': {
        items: [n(1, 'member.joined', { display_name: 'Bob' }, true)],
        unread_count: 0,
      },
    })
    try {
      click(bellButton(res))
      clickByText(res.container, 'Mark all read')
      // The mutation, then the invalidation refetch: settle both (each flush
      // drains the microtask chain and the React work it schedules).
      for (let i = 0; i < 10 && bellButton(res).getAttribute('aria-label') !== 'Notifications'; i++) {
        await flush()
      }
      expect(stub.calls.some((c) => c.url.includes('/api/notifications/read'))).toBe(true)
      expect(bellButton(res).getAttribute('aria-label')).toBe('Notifications')
    } finally {
      stub.restore()
    }
  })

  it('an "invited" notification carries a Join button that opens the invite page (marking it read)', async () => {
    let path = '/'
    const Probe = () => {
      path = useLocation().pathname
      return null
    }
    const payload = { group_id: 1, group_name: 'Trip', token: 'tok123', inviter_name: 'Alice' }
    const res = render(
      createElement(
        MemoryRouter,
        { initialEntries: ['/'] },
        createElement(NotificationsBell),
        createElement(Probe),
      ),
      {
        seed: (qc: QueryClient) => {
          qc.setQueryData(keys.notifications, { items: [n(1, 'invited', payload)], unread_count: 1 })
        },
      },
    )
    r = res
    const stub = stubFetch({
      '/api/notifications/read': { marked: 1 },
      '/api/notifications': { items: [n(1, 'invited', payload, true)], unread_count: 0 },
    })
    try {
      click(bellButton(res))
      expect(hasText(res.container, 'Alice invited you to join Trip')).toBe(true)
      // The invited row carries a Join button: clicking it navigates to the
      // invite page and marks the notification read.
      clickByText(res.container, 'Join')
      await flush()
      expect(path).toBe('/invite/tok123')
      expect(stub.calls.some((c) => c.url.includes('/api/notifications/read'))).toBe(true)
    } finally {
      stub.restore()
    }
  })
})

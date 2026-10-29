/**
 * /invite/:token accept page (AGENTS.md §2.10) — auth gating.
 *
 * Regression for the reported bug: a signed-in user opening an invite link was
 * bounced to the login/register prompt. The page decides "signed in?" from
 * whether any session material exists (access and/or refresh token) and, when
 * it does, confirms it with the self-healing /me query (a lapsed access token
 * is renewed through the 401→refresh dance before we give up). A signed-in
 * visitor sees the Join button; a genuinely signed-out visitor sees
 * register/login immediately, with no network call at all.
 */

import { createElement } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { QueryClient } from '@tanstack/react-query'

import { MemoryRouter, Route, Routes } from 'react-router-dom'

import { clearTokens, setTokens } from '../../api/client'
import { keys } from '../../query/queries'
import type { TokenResponse, UserOut } from '../../types'
import { flush, hasText, render, type RenderResult } from '../../test/harness'
import { InvitePage } from '../InvitePage'

function user(p: Partial<UserOut> & Pick<UserOut, 'id'>): UserOut {
  return {
    email: `u${p.id}@example.com`,
    display_name: `User ${p.id}`,
    created_at: '2026-01-01T00:00:00Z',
    is_admin: false,
    email_verified: true,
    preferred_language: 'en',
    avatar_url: null,
    email_change_confirmation: false,
    password_change_confirmation: false,
    ...p,
  }
}

/** InvitePage inside a minimal router at /invite/:token. */
function renderInvitePage(seed?: (qc: QueryClient) => void): RenderResult {
  return render(
    createElement(
      MemoryRouter,
      { initialEntries: ['/invite/tok123'] },
      createElement(
        Routes,
        null,
        createElement(Route, { path: '/invite/:token', element: createElement(InvitePage) }),
      ),
    ),
    { seed },
  )
}

let r: RenderResult | null = null
afterEach(() => {
  r?.unmount()
  r = null
  clearTokens()
  vi.unstubAllGlobals()
})

describe('InvitePage auth gating', () => {
  it('shows the Join button — not login/register — for a signed-in user', () => {
    setTokens({ access: 'tok', refresh: 'ref' })
    r = renderInvitePage((qc) => qc.setQueryData(keys.me, user({ id: 1 })))
    expect(hasText(r.container, 'Join group')).toBe(true)
    expect(hasText(r.container, 'Register and join')).toBe(false)
    expect(hasText(r.container, 'Log in and join')).toBe(false)
  })

  it('recovers a lapsed access token from the stored refresh token and shows Join', async () => {
    // Only the 7-day refresh survives: the 15-min access lapsed. The page
    // must heal the session (401 → refresh → /me) and show the Join button,
    // never the register/login prompt.
    localStorage.setItem('easymoney.refresh', 'ref')
    const healed: TokenResponse = { access_token: 'healed', refresh_token: 'ref2', token_type: 'bearer' }
    let meCalls = 0
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input) => {
        const url = String(input)
        if (url.includes('/api/auth/refresh')) {
          return { ok: true, status: 200, json: async () => healed }
        }
        meCalls++
        if (meCalls === 1) {
          return { ok: false, status: 401, json: async () => ({ detail: 'Not authenticated' }) }
        }
        return { ok: true, status: 200, json: async () => user({ id: 1 }) }
      }),
    )
    r = renderInvitePage()
    // A few ticks: /me 401 → refresh → /me retry → data → re-render.
    await flush()
    await flush()
    await flush()
    expect(hasText(r.container, 'Join group')).toBe(true)
    expect(hasText(r.container, 'Register and join')).toBe(false)
    expect(hasText(r.container, 'Log in and join')).toBe(false)
    // The session healed: the rotated tokens are stored.
    expect(localStorage.getItem('easymoney.access')).toBe('healed')
    expect(localStorage.getItem('easymoney.refresh')).toBe('ref2')
  })

  it('shows the register/login prompt for a signed-out visitor — without any network call', async () => {
    clearTokens()
    const fetchMock = vi.fn(async () => ({
      ok: false,
      status: 401,
      json: async () => ({ detail: 'Not authenticated' }),
    }))
    vi.stubGlobal('fetch', fetchMock)
    r = renderInvitePage()
    await flush()
    expect(hasText(r.container, 'Register and join')).toBe(true)
    expect(hasText(r.container, 'Log in and join')).toBe(true)
    expect(hasText(r.container, 'Join group')).toBe(false)
    // No token to heal with: /me must not even be attempted.
    expect(fetchMock).not.toHaveBeenCalled()
  })
})

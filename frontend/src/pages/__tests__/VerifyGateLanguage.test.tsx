/**
 * Verify-gate language + cross-account cache leak (AGENTS.md §2.13).
 *
 * Reproduced live during TESTING_PLAN.md phase 5: user A (verified, en) logs
 * out, then user B (fresh, RU selector) registers in the SAME document without
 * a reload. The query client lives for the whole document, and the previous
 * account's `me` was still fresh (30 s staleTime), so `PreferredLanguageSync`
 * — previously mounted BELOW RequireVerified, i.e. only while verified —
 * adopted A's `preferred_language` ('en') during the stale window, and the
 * gate shown to B rendered in English although B's language (selector, stored
 * choice, and registered preferred_language) was Russian. A full reload
 * "fixed" it only because the provider re-read localStorage.
 *
 * Both halves of the fix are pinned here:
 *  1. Every session-end path fires AUTH_EXPIRED_EVENT and the query client
 *     clears ALL its caches in response — the next login on the same document
 *     starts from an empty client (no stale `me`, no admin-link flash, no
 *     wrong gate language). See the listener in query/queries.ts and the
 *     dispatches in components/Layout.tsx (logout + 401-on-/me).
 *  2. The gate itself renders in the signed-in user's `preferred_language`
 *     (§2.13: the authenticated shell adopts it; the gate renders in place of
 *     the shell) — PreferredLanguageSync is mounted ABOVE RequireVerified in
 *     App.tsx, so it runs while the gate is shown.
 */

import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it } from 'vitest'

import { AUTH_EXPIRED_EVENT, clearTokens, setTokens } from '../../api/client'
import { I18nProvider } from '../../i18n'
import { keys, queryClient } from '../../query/queries'
import type { UserOut } from '../../types'
import App from '../../App'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

function user(p: Partial<UserOut> & Pick<UserOut, 'id'>): UserOut {
  return {
    email: `u${p.id}@example.com`,
    display_name: `User ${p.id}`,
    created_at: '2026-01-01T00:00:00Z',
    is_admin: false,
    email_verified: true,
    preferred_language: 'en',
    preferred_currency: null,
    avatar_url: null,
    email_change_confirmation: false,
    password_change_confirmation: false,
    ...p,
  }
}

/** Render the real App with the same provider stack main.tsx uses. */
function renderApp(seed?: (qc: QueryClient) => void): { body: HTMLElement; unmount: () => void } {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 30_000, refetchOnWindowFocus: false },
    },
  })
  seed?.(qc)
  const el = document.createElement('div')
  document.body.appendChild(el)
  const root: Root = createRoot(el)
  act(() => {
    root.render(
      createElement(I18nProvider, null, createElement(QueryClientProvider, { client: qc }, createElement(App))),
    )
  })
  return {
    body: document.body,
    unmount: () => {
      act(() => root.unmount())
      el.remove()
    },
  }
}

async function flush(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
}

afterEach(() => {
  clearTokens()
  queryClient.removeQueries()
})

describe('session end clears the query cache (cross-account leak)', () => {
  it('AUTH_EXPIRED_EVENT empties every cached query, so the next login starts from an empty client', () => {
    // The previous account's data is still fresh (within staleTime): without
    // the listener the next user on this document would read it.
    queryClient.setQueryData(keys.me, user({ id: 1, preferred_language: 'en' }))
    queryClient.setQueryData(keys.notifications, [user({ id: 1 })])

    act(() => {
      window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))
    })

    expect(queryClient.getQueryData(keys.me)).toBeUndefined()
    expect(queryClient.getQueryData(keys.notifications)).toBeUndefined()
  })
})

describe('verify gate language (App level, §2.13)', () => {
  it("renders in the signed-in user's preferred_language even on an EN browser", async () => {
    setTokens({ access: 'tok', refresh: 'ref' })
    window.history.pushState({}, '', '/')
    const r = renderApp((qc) =>
      qc.setQueryData(keys.me, user({ id: 2, email: 'bob@example.com', email_verified: false, preferred_language: 'ru' })),
    )
    // A few ticks: /me is seeded (no fetch), PreferredLanguageSync's effect
    // applies 'ru', the gate re-renders.
    await flush()
    await flush()
    expect(r.body.textContent).toContain('Подтвердите email')
    expect(r.body.textContent).not.toContain('Confirm your email')
    r.unmount()
  })

  it('renders the gate in EN for an EN-preferred unverified user (no override)', async () => {
    setTokens({ access: 'tok', refresh: 'ref' })
    window.history.pushState({}, '', '/')
    const r = renderApp((qc) =>
      qc.setQueryData(keys.me, user({ id: 3, email: 'carol@example.com', email_verified: false, preferred_language: 'en' })),
    )
    await flush()
    await flush()
    expect(r.body.textContent).toContain('Confirm your email')
    expect(r.body.textContent).not.toContain('Подтвердите email')
    r.unmount()
  })
})

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { apiFetch, clearTokens, getTokens, refreshTokens, setTokens } from '../client'

// A minimal Response stand-in: apiFetch only reads ok, status and json().
function fakeResponse(status: number, body: unknown = {}): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response
}

const NEW_TOKENS = { access_token: 'new-access', refresh_token: 'new-refresh', token_type: 'bearer' }

describe('apiFetch token handling', () => {
  beforeEach(() => {
    clearTokens()
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('returns parsed JSON on success and attaches the bearer token', async () => {
    setTokens({ access: 'tok', refresh: 'ref' })
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockImplementation(async (_input, init) => {
      const headers = (init?.headers as Headers) ?? new Headers()
      expect(headers.get('Authorization')).toBe('Bearer tok')
      return fakeResponse(200, { ok: true })
    })
    await expect(apiFetch('/api/groups')).resolves.toEqual({ ok: true })
  })

  it('refreshes once on 401, stores the new tokens, and retries the request', async () => {
    setTokens({ access: 'old-access', refresh: 'old-refresh' })
    const fetchMock = vi.mocked(fetch)
    let originalCalls = 0
    fetchMock.mockImplementation(async (input) => {
      const url = String(input)
      if (url.includes('/api/auth/refresh')) {
        return fakeResponse(200, NEW_TOKENS)
      }
      originalCalls++
      if (originalCalls === 1) return fakeResponse(401)
      return fakeResponse(200, { ok: 'retried' })
    })

    const data = await apiFetch<{ ok: string }>('/api/groups')
    expect(data).toEqual({ ok: 'retried' })
    expect(getTokens()?.access).toBe('new-access')
    expect(getTokens()?.refresh).toBe('new-refresh')
    expect(originalCalls).toBe(2) // one 401 + one retry
  })

  it('is single-flight: a burst of 401s triggers exactly one refresh', async () => {
    setTokens({ access: 'a', refresh: 'r' })
    const fetchMock = vi.mocked(fetch)
    let refreshCalls = 0
    let originalCalls = 0
    fetchMock.mockImplementation(async (input) => {
      const url = String(input)
      if (url.includes('/api/auth/refresh')) {
        refreshCalls++
        await new Promise((resolve) => setTimeout(resolve, 10))
        return fakeResponse(200, NEW_TOKENS)
      }
      originalCalls++
      if (originalCalls <= 2) return fakeResponse(401)
      return fakeResponse(200, { ok: true })
    })

    const [a, b] = await Promise.all([apiFetch('/api/x'), apiFetch('/api/y')])
    expect(a).toEqual({ ok: true })
    expect(b).toEqual({ ok: true })
    expect(refreshCalls).toBe(1)
  })

  it('clears tokens and throws when the refresh fails', async () => {
    setTokens({ access: 'bad', refresh: 'bad' })
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockImplementation(async (input) => {
      const url = String(input)
      if (url.includes('/api/auth/refresh')) return fakeResponse(401)
      return fakeResponse(401)
    })

    await expect(apiFetch('/api/groups')).rejects.toMatchObject({ status: 401 })
    expect(getTokens()).toBeNull()
  })

  it('does not attempt a refresh for auth calls (isAuthCall)', async () => {
    setTokens({ access: 'a', refresh: 'r' })
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValue(fakeResponse(401, { detail: 'Invalid credentials' }))

    await expect(apiFetch('/api/auth/login', { method: 'POST' }, { isAuthCall: true })).rejects.toMatchObject({
      status: 401,
      message: 'Invalid credentials',
    })
    // The refresh endpoint must never have been hit.
    const refreshHits = fetchMock.mock.calls.filter(([input]) => String(input).includes('/api/auth/refresh'))
    expect(refreshHits).toHaveLength(0)
    // Tokens are preserved (a login 401 is a bad-password, not an expiry).
    expect(getTokens()?.access).toBe('a')
  })

  it('rotates the session from the stored refresh token even when the access token is absent', async () => {
    // Only the 7-day refresh survives: the 15-min access lapsed and was dropped,
    // but a signed-in user must still be recoverable from the refresh token.
    localStorage.setItem('easymoney.refresh', 'ref')
    const fetchMock = vi.mocked(fetch)
    let refreshHits = 0
    fetchMock.mockImplementation(async (input) => {
      refreshHits++
      expect(String(input)).toContain('/api/auth/refresh')
      return fakeResponse(200, NEW_TOKENS)
    })

    await expect(refreshTokens()).resolves.toBe(true)
    expect(refreshHits).toBe(1)
    expect(getTokens()?.access).toBe('new-access')
    expect(getTokens()?.refresh).toBe('new-refresh')
  })

  it('does not log a signed-in user out when a concurrent tab rotated the session mid-refresh', async () => {
    // We send the request with a stale access token; while our refresh is in
    // flight a second tab rotates the shared session (revoking our jti, so our
    // refresh 401s) and writes a fresh access token to storage. We must adopt
    // the fresh token and retry — not clearTokens() a still-valid session.
    setTokens({ access: 'stale', refresh: 'ref' })
    const fetchMock = vi.mocked(fetch)
    let originalCalls = 0
    fetchMock.mockImplementation(async (input) => {
      const url = String(input)
      if (url.includes('/api/auth/refresh')) {
        // Simulate the concurrent tab's successful rotation.
        localStorage.setItem('easymoney.access', 'rotated')
        return fakeResponse(401)
      }
      originalCalls++
      if (originalCalls === 1) return fakeResponse(401)
      return fakeResponse(200, { ok: 'retried' })
    })

    const data = await apiFetch<{ ok: string }>('/api/groups')
    expect(data).toEqual({ ok: 'retried' })
    // The session survived: the rotated token is kept and used for the retry.
    expect(getTokens()?.access).toBe('rotated')
    expect(originalCalls).toBe(2)
  })
})

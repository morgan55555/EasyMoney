/**
 * Fetch wrapper (AGENTS.md §3.6): attaches the access token, refreshes once
 * on 401, and throws a typed ApiError with the server's detail message.
 *
 * Tokens live in localStorage (access: 15 min, refresh: 7 d with rotation,
 * §2.11). Refreshing is single-flight so a burst of 401s triggers exactly
 * one rotation; all tabs of the user share the same token, which is valid
 * everywhere (stateless JWTs).
 */

import type { TokenResponse } from '../types'

const ACCESS_KEY = 'easymoney.access'
const REFRESH_KEY = 'easymoney.refresh'

/** Fired after the tokens are cleared (refresh failed / logout). */
export const AUTH_EXPIRED_EVENT = 'easymoney:auth-expired'

export interface StoredTokens {
  access: string
  refresh: string
}

export function getTokens(): StoredTokens | null {
  const access = localStorage.getItem(ACCESS_KEY)
  const refresh = localStorage.getItem(REFRESH_KEY)
  if (!access) return null
  return { access, refresh: refresh ?? '' }
}

/**
 * True when there is any session material at all — the access token, the
 * refresh token, or both. The access token may be lapsed/absent while a
 * valid refresh still is, so "signed in?" must not be decided by the access
 * token alone.
 */
export function hasSession(): boolean {
  return !!localStorage.getItem(ACCESS_KEY) || !!localStorage.getItem(REFRESH_KEY)
}

export function setTokens(tokens: StoredTokens): void {
  localStorage.setItem(ACCESS_KEY, tokens.access)
  if (tokens.refresh) localStorage.setItem(REFRESH_KEY, tokens.refresh)
}

export function clearTokens(): void {
  localStorage.removeItem(ACCESS_KEY)
  localStorage.removeItem(REFRESH_KEY)
}

export class ApiError extends Error {
  status: number
  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
  }
}

async function rawFetch(path: string, init: RequestInit = {}, withAuth: boolean): Promise<Response> {
  const headers = new Headers(init.headers)
  const tokens = getTokens()
  if (withAuth && tokens?.access) headers.set('Authorization', `Bearer ${tokens.access}`)
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  return fetch(path, { ...init, headers })
}

let refreshInFlight: Promise<boolean> | null = null

/**
 * Rotate the refresh token (single-flight). Resolves false when there is no
 * refresh token to rotate with.
 *
 * The refresh token is read straight from storage rather than via
 * {@link getTokens}: the access token may be absent or lapsed while a valid
 * refresh still is, and we must still be able to rotate it (that is the whole
 * point of a 7-day refresh outliving a 15-minute access).
 */
export function refreshTokens(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      const refresh = localStorage.getItem(REFRESH_KEY)
      if (!refresh) return false
      try {
        const res = await rawFetch(
          '/api/auth/refresh',
          { method: 'POST', body: JSON.stringify({ refresh_token: refresh }) },
          false,
        )
        if (!res.ok) return false
        const data = (await res.json()) as TokenResponse
        setTokens({ access: data.access_token, refresh: data.refresh_token })
        return true
      } catch {
        return false
      }
    })().finally(() => {
      refreshInFlight = null
    })
  }
  return refreshInFlight
}

async function extractDetail(res: Response): Promise<string> {
  const fallback = `Request failed (${res.status})`
  try {
    const body: unknown = await res.json()
    const detail = (body as { detail?: unknown })?.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      // FastAPI 422: [{loc, msg, type}, ...]
      const msgs = detail
        .map((e) => (e as { msg?: unknown })?.msg)
        .filter((m): m is string => typeof m === 'string')
      if (msgs.length) return msgs.join('; ')
    }
    return fallback
  } catch {
    return fallback
  }
}

export interface ApiFetchOptions {
  /** Auth endpoints must not trigger the 401 refresh dance. */
  isAuthCall?: boolean
}

/**
 * fetch with token + refresh-once. `init.body` is a JSON string (or FormData
 * for uploads). Returns the parsed JSON, or undefined for 204.
 */
export async function apiFetch<T>(path: string, init: RequestInit = {}, opts: ApiFetchOptions = {}): Promise<T> {
  const withAuth = !opts.isAuthCall
  const accessAtSend = getTokens()?.access ?? null
  let res = await rawFetch(path, init, withAuth)
  if (res.status === 401 && withAuth) {
    const refreshed = await refreshTokens()
    // A concurrent tab may have rotated the shared session while we were
    // refreshing: its refresh succeeded (revoking the jti we just tried) so
    // ours fails, but storage now holds a fresh, valid access token. In that
    // case the session is still alive — adopt the rotated token and retry
    // rather than logging a signed-in user out.
    const rotated = !refreshed && (getTokens()?.access ?? null) !== accessAtSend
    if (refreshed || rotated) {
      res = await rawFetch(path, init, withAuth)
    } else {
      clearTokens()
      window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))
    }
  }
  if (!res.ok) {
    throw new ApiError(res.status, await extractDetail(res))
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

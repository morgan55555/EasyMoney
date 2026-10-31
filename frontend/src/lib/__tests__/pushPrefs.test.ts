/**
 * The page ⇄ service-worker prefs mirror key (AGENTS.md §2.14, req 35).
 *
 * The load-bearing property: a Cache Storage key string is resolved against
 * the *issuer's* base URL. The page writes the record from a document on some
 * route (`/groups/15`), the worker reads it from the script URL (`/sw.js`).
 * A bare relative key therefore resolves to DIFFERENT records on the two
 * sides (`/groups/while-visible` vs `/while-visible`) and the mirror silently
 * never matched on any non-root route — the "notify while visible changes
 * nothing" bug. The key must be absolute and origin-rooted so both sides
 * resolve the same record.
 */

import { afterEach, describe, expect, it, vi, type Mock } from 'vitest'
import {
  PREFS_CACHE,
  WHILE_VISIBLE_KEY,
  readWhileVisiblePref,
  writeWhileVisiblePref,
} from '../pushPrefs'

describe('WHILE_VISIBLE_KEY', () => {
  it('is absolute and origin-rooted', () => {
    // A bare relative key is the regression: it resolves against the issuer's
    // path, which differs between a page on a route and the worker's /sw.js.
    expect(WHILE_VISIBLE_KEY).toMatch(/^\/[^/]+$/)
  })

  it('resolves to the SAME record from a page on a route and from the SW script URL', () => {
    // Simulate the two resolvers the browser actually uses.
    const fromSw = new URL(WHILE_VISIBLE_KEY, 'http://localhost/sw.js').href
    const fromRoot = new URL(WHILE_VISIBLE_KEY, 'http://localhost/').href
    const fromGroupPage = new URL(WHILE_VISIBLE_KEY, 'http://localhost/groups/15').href
    const fromDeepPage = new URL(WHILE_VISIBLE_KEY, 'http://localhost/invite/abc123').href
    expect(fromSw).toBe('http://localhost/while-visible')
    expect(fromRoot).toBe(fromSw)
    expect(fromGroupPage).toBe(fromSw)
    expect(fromDeepPage).toBe(fromSw)
  })
})

interface CachesFake {
  store: Map<string, string>
  open: Mock
}

function installCachesFake(initial: Map<string, string> = new Map()): CachesFake {
  const store = new Map(initial)
  const open = vi.fn(async (name: string) => {
    expect(name).toBe(PREFS_CACHE)
    return {
      match: vi.fn(async (key: string) =>
        store.has(key) ? { text: async () => store.get(key) } : undefined,
      ),
      put: vi.fn(async (key: string, res: Response) => {
        store.set(key, await res.text())
      }),
    }
  })
  Object.defineProperty(globalThis, 'caches', { value: { open }, configurable: true, writable: true })
  return { store, open }
}

describe('mirror round-trip', () => {
  afterEach(() => {
    Object.defineProperty(globalThis, 'caches', { value: undefined, configurable: true, writable: true })
  })

  it('writeWhileVisiblePref then readWhileVisiblePref round-trips on and off', async () => {
    const { store } = installCachesFake()
    await writeWhileVisiblePref(true)
    expect(store.get(WHILE_VISIBLE_KEY)).toBe('on')
    expect(await readWhileVisiblePref()).toBe(true)
    await writeWhileVisiblePref(false)
    expect(await readWhileVisiblePref()).toBe(false)
  })

  it('readWhileVisiblePref is false when the record is missing', async () => {
    installCachesFake()
    expect(await readWhileVisiblePref()).toBe(false)
  })

  it('falls back to off when the cache is unreadable (and a failing write does not throw)', async () => {
    Object.defineProperty(globalThis, 'caches', {
      value: {
        open: vi.fn(async () => {
          throw new Error('caches unavailable')
        }),
      },
      configurable: true,
      writable: true,
    })
    expect(await readWhileVisiblePref()).toBe(false)
    await expect(writeWhileVisiblePref(true)).resolves.toBeUndefined()
  })
})

/**
 * Browser (Web) notifications preference (AGENTS.md §2.14, req 35): a
 * client-only on/off switch, persisted in localStorage, default off. Mirrors
 * the theme manager (tested the same way): the provider is tested for
 * read/persist behavior via a probe component that reads `useBrowserNotify()`.
 * The OS-notification display condition itself is a browser capability the
 * unit environment cannot exercise (no Web Push); the settings-toggle guards
 * are covered structurally by the SettingsModal tests.
 */

import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { BrowserNotifyProvider, useBrowserNotify } from '../browserNotify'
import { PREFS_CACHE, WHILE_VISIBLE_KEY as CACHE_KEY } from '../pushPrefs'

// React 18: tell the runtime we are in a test environment so `act` is allowed.
;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const KEY = 'easymoney.browserNotify'
const WHILE_VISIBLE_KEY = 'easymoney.pushWhileVisible'

// The shared Cache Storage record the service worker reads at push time
// (lib/pushPrefs): a fake that records every `put`, whatever happy-dom
// declares for `caches`.
const putCalls: Array<{ cache: string; key: string; text: string }> = []
let putError: Error | null = null

function installCachesFake(): void {
  putCalls.length = 0
  putError = null
  const open = vi.fn(async (name: string) => ({
    put: vi.fn(async (key: string, res: Response) => {
      if (putError) throw putError
      putCalls.push({ cache: name, key, text: await res.text() })
    }),
  }))
  Object.defineProperty(globalThis, 'caches', { value: { open }, configurable: true, writable: true })
}

async function flushCacheWrites(): Promise<void> {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0))
  })
}

describe('BrowserNotifyProvider', () => {
  let root: Root | null = null

  beforeEach(() => {
    localStorage.removeItem(KEY)
    localStorage.removeItem(WHILE_VISIBLE_KEY)
    installCachesFake()
  })

  afterEach(() => {
    const r = root
    if (r) act(() => r.unmount())
    root = null
    localStorage.removeItem(KEY)
    localStorage.removeItem(WHILE_VISIBLE_KEY)
    Object.defineProperty(globalThis, 'caches', { value: undefined, configurable: true, writable: true })
  })

  function render(): {
    enabled: () => boolean
    setEnabled: (next: boolean) => void
    whileVisible: () => boolean
    setWhileVisible: (next: boolean) => void
  } {
    const el = document.createElement('div')
    document.body.appendChild(el)
    const r = createRoot(el)
    root = r
    let enabled = false
    let set: (next: boolean) => void = () => undefined
    let wv = false
    let setWv: (next: boolean) => void = () => undefined
    function Probe() {
      const { enabled: e, setEnabled: s, whileVisible: w, setWhileVisible: sw } = useBrowserNotify()
      enabled = e
      set = s
      wv = w
      setWv = sw
      return null
    }
    act(() => {
      r.render(createElement(BrowserNotifyProvider, null, createElement(Probe)))
    })
    return {
      enabled: () => enabled,
      setEnabled: set,
      whileVisible: () => wv,
      setWhileVisible: setWv,
    }
  }

  it('defaults to off when nothing is stored', () => {
    const p = render()
    expect(p.enabled()).toBe(false)
  })

  it('starts enabled when a stored preference is "on"', () => {
    localStorage.setItem(KEY, 'on')
    const p = render()
    expect(p.enabled()).toBe(true)
  })

  it('treats any other stored value as off', () => {
    localStorage.setItem(KEY, 'garbage')
    const p = render()
    expect(p.enabled()).toBe(false)
  })

  it('setEnabled(true) turns the preference on and persists it', () => {
    const p = render()
    act(() => p.setEnabled(true))
    expect(p.enabled()).toBe(true)
    expect(localStorage.getItem(KEY)).toBe('on')
  })

  it('setEnabled(false) turns the preference off and persists it', () => {
    localStorage.setItem(KEY, 'on')
    const p = render()
    act(() => p.setEnabled(false))
    expect(p.enabled()).toBe(false)
    expect(localStorage.getItem(KEY)).toBe('off')
  })

  it('syncs the in-memory preference from a storage event fired by another tab', () => {
    const p = render()
    expect(p.enabled()).toBe(false)
    // Another tab of the same browser turned the preference on: localStorage
    // now holds "on" and this tab receives the `storage` event (the browser
    // fires it only in the *other* tabs, never the one that wrote). Without
    // this sync a tab opened before the toggle stays off in memory and never
    // registers the service worker, so it can't raise an OS notification
    // while hidden (AGENTS.md §2.14, req 7).
    localStorage.setItem(KEY, 'on')
    act(() => {
      window.dispatchEvent(new StorageEvent('storage', { key: KEY, newValue: 'on' }))
    })
    expect(p.enabled()).toBe(true)
    // ...and back off the other way.
    localStorage.setItem(KEY, 'off')
    act(() => {
      window.dispatchEvent(new StorageEvent('storage', { key: KEY, newValue: 'off' }))
    })
    expect(p.enabled()).toBe(false)
  })

  it('ignores storage events for other keys', () => {
    const p = render()
    localStorage.setItem('easymoney.other', 'on')
    act(() => {
      window.dispatchEvent(new StorageEvent('storage', { key: 'easymoney.other', newValue: 'on' }))
    })
    expect(p.enabled()).toBe(false)
  })

  it('defaults off even when localStorage is unavailable', () => {
    const real = Storage.prototype.getItem
    Storage.prototype.getItem = () => {
      throw new Error('storage unavailable')
    }
    try {
      const p = render()
      expect(p.enabled()).toBe(false)
      // Persisting must not throw either — the in-memory choice still applies.
      act(() => p.setEnabled(true))
      expect(p.enabled()).toBe(true)
    } finally {
      Storage.prototype.getItem = real
    }
  })

  it('whileVisible defaults to off when nothing is stored', () => {
    const p = render()
    expect(p.whileVisible()).toBe(false)
  })

  it('whileVisible starts on when a stored preference is "on"', () => {
    localStorage.setItem(WHILE_VISIBLE_KEY, 'on')
    const p = render()
    expect(p.whileVisible()).toBe(true)
  })

  it('setWhileVisible(true) turns the preference on and persists it', () => {
    const p = render()
    act(() => p.setWhileVisible(true))
    expect(p.whileVisible()).toBe(true)
    expect(localStorage.getItem(WHILE_VISIBLE_KEY)).toBe('on')
  })

  it('setWhileVisible(false) turns the preference off and persists it', () => {
    localStorage.setItem(WHILE_VISIBLE_KEY, 'on')
    const p = render()
    act(() => p.setWhileVisible(false))
    expect(p.whileVisible()).toBe(false)
    expect(localStorage.getItem(WHILE_VISIBLE_KEY)).toBe('off')
  })

  it('syncs whileVisible from a storage event fired by another tab', () => {
    const p = render()
    expect(p.whileVisible()).toBe(false)
    // Another tab of the same browser turned the preference on (the browser
    // fires the `storage` event only in the *other* tabs, never the writer).
    localStorage.setItem(WHILE_VISIBLE_KEY, 'on')
    act(() => {
      window.dispatchEvent(new StorageEvent('storage', { key: WHILE_VISIBLE_KEY, newValue: 'on' }))
    })
    expect(p.whileVisible()).toBe(true)
    // ...and the main preference must NOT have moved with it.
    expect(p.enabled()).toBe(false)
  })

  it('a storage event for the main preference does not move whileVisible', () => {
    const p = render()
    act(() => p.setWhileVisible(true))
    localStorage.setItem(KEY, 'on')
    act(() => {
      window.dispatchEvent(new StorageEvent('storage', { key: KEY, newValue: 'on' }))
    })
    expect(p.enabled()).toBe(true)
    expect(p.whileVisible()).toBe(true)
  })

  // --- shared-cache mirror (the service worker's view of whileVisible) ---

  it('re-asserts the stored while-visible preference into the shared cache on load', async () => {
    localStorage.setItem(WHILE_VISIBLE_KEY, 'on')
    render()
    await flushCacheWrites()
    expect(putCalls).toEqual([{ cache: PREFS_CACHE, key: CACHE_KEY, text: 'on' }])
  })

  it('mirrors "off" into the shared cache on load when nothing is stored', async () => {
    render()
    await flushCacheWrites()
    expect(putCalls).toEqual([{ cache: PREFS_CACHE, key: CACHE_KEY, text: 'off' }])
  })

  it('setWhileVisible mirrors the new value into the shared cache on every change', async () => {
    const p = render()
    await flushCacheWrites()
    putCalls.length = 0
    act(() => p.setWhileVisible(true))
    await flushCacheWrites()
    expect(putCalls.map((c) => c.text)).toEqual(['on'])
    act(() => p.setWhileVisible(false))
    await flushCacheWrites()
    expect(putCalls.map((c) => c.text)).toEqual(['on', 'off'])
  })

  it('a failing shared-cache write does not break the preference', async () => {
    const p = render()
    await flushCacheWrites()
    putError = new Error('quota exceeded')
    act(() => p.setWhileVisible(true))
    // The localStorage value and the in-memory state still apply; only the
    // worker-side mirror is lost (it will fall back to "off").
    expect(p.whileVisible()).toBe(true)
    expect(localStorage.getItem(WHILE_VISIBLE_KEY)).toBe('on')
  })
})

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
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { BrowserNotifyProvider, useBrowserNotify } from '../browserNotify'

// React 18: tell the runtime we are in a test environment so `act` is allowed.
;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const KEY = 'easymoney.browserNotify'

describe('BrowserNotifyProvider', () => {
  let root: Root | null = null

  beforeEach(() => {
    localStorage.removeItem(KEY)
  })

  afterEach(() => {
    const r = root
    if (r) act(() => r.unmount())
    root = null
    localStorage.removeItem(KEY)
  })

  function render(): { enabled: () => boolean; setEnabled: (next: boolean) => void } {
    const el = document.createElement('div')
    document.body.appendChild(el)
    const r = createRoot(el)
    root = r
    let enabled = false
    let set: (next: boolean) => void = () => undefined
    function Probe() {
      const { enabled: e, setEnabled: s } = useBrowserNotify()
      enabled = e
      set = s
      return null
    }
    act(() => {
      r.render(createElement(BrowserNotifyProvider, null, createElement(Probe)))
    })
    return { enabled: () => enabled, setEnabled: set }
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
})

/**
 * AGENTS.md §2.13 / req 28: the theme preference is client-only
 * (`light` | `dark` | `system`, default `system`), persisted in localStorage,
 * and applied as a `.dark` class on <html> (Tailwind darkMode: "class").
 *
 * `resolveTheme` is pure and tested directly; the provider is tested for the
 * class-strategy behavior (dark class applied/removed, persistence).
 */

import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { resolveTheme, ThemeProvider, useTheme, type Theme } from '../theme'

// React 18: tell the runtime we are in a test environment so `act` is allowed.
;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const THEME_KEY = 'easymoney.theme'

describe('resolveTheme', () => {
  it('system follows the OS preference', () => {
    expect(resolveTheme('system', true)).toBe('dark')
    expect(resolveTheme('system', false)).toBe('light')
  })

  it('an explicit choice wins over the OS preference', () => {
    expect(resolveTheme('light', true)).toBe('light')
    expect(resolveTheme('dark', false)).toBe('dark')
  })
})

describe('ThemeProvider (class strategy)', () => {
  let root: Root | null = null

  beforeEach(() => {
    localStorage.removeItem(THEME_KEY)
  })

  afterEach(() => {
    const r = root
    if (r) act(() => r.unmount())
    root = null
    document.documentElement.classList.remove('dark')
  })

  function render(): (t: Theme) => void {
    const el = document.createElement('div')
    document.body.appendChild(el)
    const r = createRoot(el)
    root = r
    let set: (t: Theme) => void = () => undefined
    function Probe() {
      set = useTheme().setTheme
      return null
    }
    act(() => r.render(createElement(ThemeProvider, null, createElement(Probe))))
    return set
  }

  it('applies .dark for a stored dark preference and removes it on change', () => {
    localStorage.setItem(THEME_KEY, 'dark')
    const set = render()
    expect(document.documentElement.classList.contains('dark')).toBe(true)
    act(() => set('light'))
    expect(document.documentElement.classList.contains('dark')).toBe(false)
    expect(localStorage.getItem(THEME_KEY)).toBe('light')
  })

  it('defaults to light when nothing is stored', () => {
    render()
    expect(document.documentElement.classList.contains('dark')).toBe(false)
  })

  it('an explicit light preference stays light (OS dark is irrelevant here)', () => {
    localStorage.setItem(THEME_KEY, 'light')
    render()
    expect(document.documentElement.classList.contains('dark')).toBe(false)
  })
})

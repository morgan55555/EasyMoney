/**
 * lib/focus.ts: scroll-to + flash for notification deep links (View button).
 *
 * happy-dom has no layout, so `Element.prototype.scrollIntoView` is stubbed
 * and spied on; the flash class lifetime (~2.5s) is asserted with fake
 * timers. The polling hook is exercised against the real DOM with real
 * (~100ms) waits.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, createElement, type ReactElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'

import { focusElement, useFocusElement } from '../focus'
import { flush } from '../../test/harness'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

// A few ms over the hook's 100ms poll interval (real timers, no fakes here).
const POLL_SETTLE = 110

let scrollSpy: ReturnType<typeof vi.fn>
let original: typeof Element.prototype.scrollIntoView | undefined

beforeEach(() => {
  scrollSpy = vi.fn()
  original = Element.prototype.scrollIntoView
  Element.prototype.scrollIntoView = scrollSpy as unknown as typeof Element.prototype.scrollIntoView
})

afterEach(() => {
  if (original === undefined) {
    delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView
  } else {
    Element.prototype.scrollIntoView = original
  }
  vi.useRealTimers()
})

function mount(ui: ReactElement): { root: Root; host: HTMLDivElement } {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  act(() => root.render(ui))
  return { root, host }
}

describe('focusElement', () => {
  it('returns false when the element is not in the document yet', () => {
    expect(focusElement('#nope-42')).toBe(false)
  })

  it('gives up on a malformed selector without throwing', () => {
    expect(focusElement('#["unclosed')).toBe(false)
  })

  it('scrolls a present element into view and flashes it for ~2.5s', () => {
    vi.useFakeTimers()
    const el = document.createElement('div')
    el.id = 'expense-7'
    document.body.appendChild(el)
    try {
      expect(focusElement('#expense-7')).toBe(true)
      expect(scrollSpy).toHaveBeenCalledTimes(1)
      expect(scrollSpy).toHaveBeenCalledWith({ behavior: 'smooth', block: 'center' })
      expect(el.classList.contains('focus-flash')).toBe(true)
      vi.advanceTimersByTime(2400)
      expect(el.classList.contains('focus-flash')).toBe(true)
      vi.advanceTimersByTime(200)
      expect(el.classList.contains('focus-flash')).toBe(false)
    } finally {
      el.remove()
    }
  })
})

describe('useFocusElement', () => {
  it('does nothing while the selector is null', () => {
    const Host = () => {
      useFocusElement(null)
      return createElement('div', null, 'host')
    }
    const { root, host } = mount(createElement(Host))
    expect(scrollSpy).not.toHaveBeenCalled()
    act(() => root.unmount())
    host.remove()
  })

  it('polls until the element appears, then focuses it exactly once', async () => {
    const Host = () => {
      useFocusElement('#late-1')
      return createElement('div', null, 'host')
    }
    const { root, host } = mount(createElement(Host))
    expect(scrollSpy).not.toHaveBeenCalled()
    // One poll passes with the target still absent…
    await flush(POLL_SETTLE)
    // …the target appears; the next poll finds it.
    const target = document.createElement('div')
    target.id = 'late-1'
    host.appendChild(target)
    await flush(POLL_SETTLE * 3)
    expect(scrollSpy).toHaveBeenCalledTimes(1)
    expect(target.classList.contains('focus-flash')).toBe(true)
    act(() => root.unmount())
    host.remove()
  })

  it('unmounting clears the pending poll (no focus after unmount)', async () => {
    const Host = () => {
      useFocusElement('#never-1')
      return createElement('div', null, 'host')
    }
    const { root, host } = mount(createElement(Host))
    await flush(POLL_SETTLE)
    act(() => root.unmount())
    host.remove()
    // The target appears long after the unmount — nothing should focus it.
    const target = document.createElement('div')
    target.id = 'never-1'
    document.body.appendChild(target)
    await flush(POLL_SETTLE * 3)
    expect(scrollSpy).not.toHaveBeenCalled()
    target.remove()
  })
})

/**
 * Deep-link focus for notification "View" (§2.6): scroll the target element
 * into view and flash a highlight ring (`.focus-flash`, index.css) so the
 * user notices where the notification pointed.
 *
 * The target may not be in the DOM yet (the tab's data is still loading, or
 * the row sits below a "show more" boundary the parent expands on focus), so
 * `useFocusElement` polls until the element appears and gives up after ~8s.
 */

import { useEffect } from 'react'

const FLASH_MS = 2500
const POLL_MS = 100
const MAX_TRIES = 80 // 80 * 100ms ≈ 8s

/**
 * Focus the element matching `selector` once: smooth-scroll to the center of
 * the viewport and add the flash class (removed after ~2.5s). Returns false
 * when nothing matches (or the selector is malformed), so the caller can
 * retry.
 */
export function focusElement(selector: string): boolean {
  let el: Element | null
  try {
    el = document.querySelector(selector)
  } catch {
    return false // a hand-typed ?focus= with a broken selector must not crash
  }
  if (!el) return false
  el.scrollIntoView({ behavior: 'smooth', block: 'center' })
  el.classList.add('focus-flash')
  window.setTimeout(() => el.classList.remove('focus-flash'), FLASH_MS)
  return true
}

/** Focus `selector` (a CSS selector) once it appears in the document; `null` disables. */
export function useFocusElement(selector: string | null): void {
  useEffect(() => {
    if (!selector) return
    let tries = 0
    let timer = 0
    const tick = (): void => {
      if (focusElement(selector)) return
      if (++tries >= MAX_TRIES) return
      timer = window.setTimeout(tick, POLL_MS)
    }
    tick()
    return () => window.clearTimeout(timer)
  }, [selector])
}

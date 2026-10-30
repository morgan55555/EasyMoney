/**
 * Web Push deep-link receiver (AGENTS.md §2.14) — the in-page half of a
 * notification click.
 *
 * The service worker routes a click with `Client.postMessage` rather than
 * `Client.navigate`: `navigate` only reaches a client the worker *controls*,
 * and after a redeploy (or across several tabs) the target tab is often
 * uncontrolled, where it throws in Firefox and silently no-ops in Chrome.
 * `postMessage` reaches both in every browser, so the worker always posts
 * `{ type: 'em:nav', url }` to the tab it focuses, and THIS component performs
 * the actual react-router navigation.
 *
 * Delivery target: per the service-worker spec (MDN, `Client.postMessage`),
 * the posted message arrives as a `message` event on the page's
 * **`navigator.serviceWorker`** (the ServiceWorkerContainer) — never on
 * `window`. A `window` listener never sees it: that is exactly why the earlier
 * implementation of this component — which listened on `window` — focused the
 * tab but never routed, in both browsers. This component therefore listens on
 * `navigator.serviceWorker`, guarded for contexts where it is absent (no
 * secure context, some test environments).
 *
 * It is mounted unconditionally inside `<BrowserRouter>` (App.tsx), so every
 * route — the app, the auth pages, the invite page — can receive a click while
 * it is on screen. The guard keeps only same-origin messages of the exact
 * shape the worker posts; react-router navigation is client-side, so a
 * same-shape message can at most move us to an internal route (no open
 * redirect).
 */

import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'

export function PushDeepLink() {
  const navigate = useNavigate()

  useEffect(() => {
    // A service worker's `Client.postMessage` is delivered to the page's
    // `navigator.serviceWorker`, NOT to `window` (AGENTS.md §2.14) — without
    // a service worker there is nothing to receive.
    const target: EventTarget | undefined = navigator.serviceWorker
    if (!target) return

    const onMessage: EventListener = (event: Event) => {
      const m = event as MessageEvent
      const d = m.data as { type?: unknown; url?: unknown } | null
      if (!d || typeof d !== 'object' || d.type !== 'em:nav') return
      const url = d.url
      if (typeof url !== 'string' || !url.startsWith('/')) return
      // A *different* (external) origin is rejected; an empty origin is
      // allowed — some browsers report a worker-posted message with no origin.
      if (m.origin && m.origin !== window.location.origin) return
      navigate(url)
    }
    target.addEventListener('message', onMessage)
    return () => target.removeEventListener('message', onMessage)
  }, [navigate])

  return null
}

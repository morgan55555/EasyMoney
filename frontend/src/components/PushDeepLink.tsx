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
    const onMessage = (event: MessageEvent) => {
      const d = event.data as { type?: unknown; url?: unknown } | null
      if (!d || typeof d !== 'object' || d.type !== 'em:nav') return
      const url = d.url
      if (typeof url !== 'string' || !url.startsWith('/')) return
      // A *different* (external) origin is rejected; an empty origin is
      // allowed — some browsers report a worker-posted message with no origin.
      if (event.origin && event.origin !== window.location.origin) return
      navigate(url)
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [navigate])

  return null
}

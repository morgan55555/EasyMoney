/**
 * PushDeepLink (AGENTS.md §2.14): the in-page half of a notification click.
 * The service worker routes a click with `Client.postMessage`, which per the
 * spec is delivered as a `message` event on the page's `navigator.serviceWorker`
 * (the ServiceWorkerContainer) — never on `window`. The mock must be in place
 * BEFORE the component mounts (the listener target is resolved once in the
 * mount effect), and this test feeds the exact message the worker posts and
 * asserts the react-router navigation happens — plus (regression) that a
 * same-shape message posted on `window` is ignored, and that off-shape,
 * external, or cross-origin messages are ignored, and that the component
 * tolerates an environment without a service worker.
 */

import { act, createElement } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import { MemoryRouter, useLocation } from 'react-router-dom'

import { PushDeepLink } from '../PushDeepLink'
import { render, flush } from '../../test/harness'

function withProbe(initial: string) {
  let loc = ''
  const Probe = () => {
    const l = useLocation()
    loc = l.pathname + l.search
    return null
  }
  const res = render(
    createElement(
      MemoryRouter,
      { initialEntries: [initial] },
      createElement(PushDeepLink),
      createElement(Probe),
    ),
  )
  return { res, getLoc: () => loc }
}

// --- navigator.serviceWorker mock ----------------------------------------------------
// happy-dom has no service worker, so the container is an EventTarget with the
// same surface the component uses (addEventListener / removeEventListener for
// `message`). It must exist before the component mounts, so each test installs
// it first; afterEach restores the absent state (the no-SW test relies on it).

let swTarget: EventTarget | null = null

function installServiceWorker(): EventTarget {
  const target = new EventTarget()
  Object.defineProperty(navigator, 'serviceWorker', { value: target, configurable: true })
  swTarget = target
  return target
}

function clearServiceWorker(): void {
  Object.defineProperty(navigator, 'serviceWorker', { value: undefined, configurable: true })
  swTarget = null
}

/** Post a message the way the service worker does (on navigator.serviceWorker). */
function postToWorker(data: unknown, origin?: string): void {
  const target = swTarget ?? installServiceWorker()
  act(() => {
    target.dispatchEvent(new MessageEvent('message', { data, origin }))
  })
}

/** Post the exact message the service worker sends on a notification click. */
function postNav(url: string, origin?: string): void {
  postToWorker({ type: 'em:nav', url }, origin)
}

afterEach(() => {
  clearServiceWorker()
})

describe('PushDeepLink', () => {
  it('navigates to the posted target when the worker sends a deep link', async () => {
    installServiceWorker()
    const { res, getLoc } = withProbe('/')
    await flush()
    postNav('/groups/5?tab=expenses&focus=expense:12', window.location.origin)
    await flush()
    expect(getLoc()).toBe('/groups/5?tab=expenses&focus=expense:12')
    res.unmount()
  })

  it('navigates even when the posted message carries no explicit origin (some browsers)', async () => {
    installServiceWorker()
    const { res, getLoc } = withProbe('/overview')
    await flush()
    postNav('/groups/9?tab=activity&focus=feed:expense.deleted', '')
    await flush()
    expect(getLoc()).toBe('/groups/9?tab=activity&focus=feed:expense.deleted')
    res.unmount()
  })

  it('ignores a message that is not the deep-link shape', async () => {
    installServiceWorker()
    const { res, getLoc } = withProbe('/')
    await flush()
    postToWorker({ type: 'other', url: '/x' })
    await flush()
    expect(getLoc()).toBe('/')
    res.unmount()
  })

  it('ignores a deep link whose url is not a same-origin relative path', async () => {
    installServiceWorker()
    const { res, getLoc } = withProbe('/')
    await flush()
    postNav('https://evil.example/x', window.location.origin)
    await flush()
    expect(getLoc()).toBe('/')
    res.unmount()
  })

  it('ignores a deep link posted from a different origin', async () => {
    installServiceWorker()
    const { res, getLoc } = withProbe('/')
    await flush()
    postNav('/groups/5?tab=expenses&focus=expense:12', 'https://evil.example')
    await flush()
    expect(getLoc()).toBe('/')
    res.unmount()
  })

  it('ignores a deep link posted on window (the message lands on navigator.serviceWorker, not window)', async () => {
    installServiceWorker()
    const { res, getLoc } = withProbe('/')
    await flush()
    // Regression: the pre-fix component listened on window — the browser never
    // delivers the worker's postMessage there, so the tab focused but never
    // routed. A window-dispatched message must be ignored now.
    act(() => {
      window.dispatchEvent(
        new MessageEvent('message', {
          data: { type: 'em:nav', url: '/x' },
          origin: window.location.origin,
        }),
      )
    })
    await flush()
    expect(getLoc()).toBe('/')
    res.unmount()
  })

  it('mounts without a service worker context and ignores window messages', async () => {
    clearServiceWorker() // no navigator.serviceWorker (happy-dom default)
    const { res, getLoc } = withProbe('/')
    await flush()
    act(() => {
      window.dispatchEvent(
        new MessageEvent('message', {
          data: { type: 'em:nav', url: '/x' },
          origin: window.location.origin,
        }),
      )
    })
    await flush()
    expect(getLoc()).toBe('/')
    res.unmount()
  })
})

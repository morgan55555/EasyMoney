/**
 * Service worker event handlers (AGENTS.md §2.14).
 *
 * In happy-dom `self` is the `window`, so importing `../sw` attaches its
 * `push` / `notificationclick` listeners to whatever `window.addEventListener`
 * is at import time — we replace it with a capturing spy before each dynamic
 * import and invoke the captured handlers with synthetic events. The fakes
 * stood in for `clients` (matchAll / openWindow) and `registration`
 * (showNotification) live on `window` because the module resolves them
 * through `self`.
 *
 * The load-bearing assertion in every handler test is that `event.waitUntil`
 * was called: without it the browser may terminate the worker the moment the
 * synchronous handler returns, which drops the notification (push) or makes
 * the click silently do nothing (openWindow never runs) — exactly the bug
 * this test guards against.
 */

import { afterEach, describe, expect, it, vi, type Mock } from 'vitest'
import { WHILE_VISIBLE_KEY } from '../../lib/pushPrefs'

type Handler = (event: unknown) => void

const captured: { push?: Handler; click?: Handler; install?: Handler; activate?: Handler } = {}

let addEventListenerSpy: Mock
const origAddEventListener = window.addEventListener

function captureListeners(): void {
  addEventListenerSpy = vi.fn((type: string, listener: EventListenerOrEventListenerObject) => {
    const fn = listener as Handler
    if (type === 'push') captured.push = fn
    if (type === 'notificationclick') captured.click = fn
    if (type === 'install') captured.install = fn
    if (type === 'activate') captured.activate = fn
  })
  window.addEventListener = addEventListenerSpy as unknown as typeof window.addEventListener
}

async function importSw(): Promise<void> {
  vi.resetModules()
  captured.push = undefined
  captured.click = undefined
  captured.install = undefined
  captured.activate = undefined
  captureListeners()
  await import('../sw')
}

interface WinFakes {
  clients: { matchAll: Mock; openWindow: Mock; claim: Mock }
  registration: { showNotification: Mock }
  skipWaiting: Mock
}

function installFakes(clients: Array<Record<string, unknown>> = []): WinFakes {
  const fakes: WinFakes = {
    clients: {
      matchAll: vi.fn(async () => clients),
      openWindow: vi.fn(async () => ({})),
      claim: vi.fn(),
    },
    registration: { showNotification: vi.fn(async () => ({})) },
    skipWaiting: vi.fn(async () => undefined),
  }
  ;(window as unknown as { clients: unknown }).clients = fakes.clients
  ;(window as unknown as { registration: unknown }).registration = fakes.registration
  ;(window as unknown as { skipWaiting: unknown }).skipWaiting = fakes.skipWaiting
  return fakes
}

/**
 * Set the while-visible preference as the page would mirror it into the
 * shared Cache Storage record the worker reads at push time (a real service
 * worker cannot read the page's localStorage — that is why the mirror
 * exists). `value` is the cache entry's text (`null` = no record); with
 * `openError` the `caches.open` call throws, as on a storage-blocked
 * context.
 */
function setSharedPref(value: string | null, opts: { openError?: boolean } = {}): { open: Mock; match: Mock } {
  const match = vi.fn(async (key: string) => {
    if (key !== WHILE_VISIBLE_KEY || value === null) return undefined
    return { text: async () => value }
  })
  const open = vi.fn(async () => {
    if (opts.openError) throw new Error('caches unavailable')
    return { match }
  })
  defineCaches({ open })
  return { open, match }
}

/** Install (or clear) the global `caches` fake, whatever happy-dom declares. */
function defineCaches(fake: unknown): void {
  Object.defineProperty(globalThis, 'caches', { value: fake, configurable: true, writable: true })
}

const EXPENSE_PAYLOAD = {
  lang: 'en',
  notification: {
    id: 42,
    type: 'expense.created',
    payload: {
      id: 42,
      group_id: 1,
      description: 'E2E dinner',
      amount: '25.00',
      currency: 'USD',
      payer: { id: 2, display_name: 'Bob' },
    },
    created_at: '2026-10-30T07:00:00Z',
  },
}

function pushEvent(dataJson: unknown, jsonShouldThrow = false) {
  const waitUntil: Mock = vi.fn()
  const event = {
    data: {
      json: () => {
        if (jsonShouldThrow) throw new SyntaxError('malformed')
        return dataJson
      },
    },
    waitUntil,
  }
  return { event, waitUntil }
}

function clickEvent(data: unknown) {
  const close: Mock = vi.fn()
  const waitUntil: Mock = vi.fn()
  const event = { notification: { close, data }, waitUntil }
  return { event, close, waitUntil }
}

afterEach(() => {
  window.addEventListener = origAddEventListener
  delete (window as unknown as { clients?: unknown }).clients
  delete (window as unknown as { registration?: unknown }).registration
  delete (window as unknown as { skipWaiting?: unknown }).skipWaiting
  // The shared-caches fake (the worker's view of the while-visible
  // preference) must not leak between tests: no record = off = suppressed
  // while a tab is visible.
  defineCaches(undefined)
})

describe('push handler', () => {
  it('shows the OS notification when no app tab is visible, and waits for it', async () => {
    await importSw()
    const fakes = installFakes([])
    const { event, waitUntil } = pushEvent(EXPENSE_PAYLOAD)

    captured.push!(event)

    expect(waitUntil).toHaveBeenCalledTimes(1)
    await waitUntil.mock.calls[0][0]
    expect(fakes.registration.showNotification).toHaveBeenCalledTimes(1)
    const [title, options] = fakes.registration.showNotification.mock.calls[0]
    expect(title).toBe('EasyMoney')
    expect(options).toMatchObject({
      // formatMoney is "value + code" by design (§2.12), symbol-less.
      body: 'Bob added: E2E dinner (25.00 USD)',
      icon: '/icon.png',
      tag: '42',
      data: { url: '/groups/1?tab=expenses&focus=expense:42' },
    })
  })

  it('uses the group name as the title when the payload carries one', async () => {
    await importSw()
    const fakes = installFakes([])
    const payload = {
      ...EXPENSE_PAYLOAD,
      notification: {
        ...EXPENSE_PAYLOAD.notification,
        payload: { ...EXPENSE_PAYLOAD.notification.payload, group_name: 'Smoke USD' },
      },
    }
    const { event, waitUntil } = pushEvent(payload)

    captured.push!(event)

    await waitUntil.mock.calls[0][0]
    expect(fakes.registration.showNotification.mock.calls[0][0]).toBe('Smoke USD')
  })

  it('suppresses the notification while an app tab is visible (the bell covers it)', async () => {
    await importSw()
    const fakes = installFakes([{ visibilityState: 'visible' }])
    // No mirrored preference record: the default (off) keeps suppression.
    setSharedPref(null)
    const { event, waitUntil } = pushEvent(EXPENSE_PAYLOAD)

    captured.push!(event)

    await waitUntil.mock.calls[0][0]
    expect(fakes.registration.showNotification).not.toHaveBeenCalled()
  })

  it('shows the notification while an app tab is visible when the notify-while-visible preference is on', async () => {
    await importSw()
    const fakes = installFakes([{ visibilityState: 'visible' }])
    // The page mirrors the preference into the shared cache; the worker reads
    // it at push time (a real service worker cannot read the page's
    // localStorage).
    const { open, match } = setSharedPref('on')
    const { event, waitUntil } = pushEvent(EXPENSE_PAYLOAD)

    captured.push!(event)

    await waitUntil.mock.calls[0][0]
    expect(open).toHaveBeenCalledTimes(1)
    expect(match).toHaveBeenCalledWith(WHILE_VISIBLE_KEY)
    expect(fakes.registration.showNotification).toHaveBeenCalledTimes(1)
  })

  it('treats a garbage cached value as "not allowed" while a tab is visible', async () => {
    await importSw()
    const fakes = installFakes([{ visibilityState: 'visible' }])
    setSharedPref('garbage')
    const { event, waitUntil } = pushEvent(EXPENSE_PAYLOAD)

    captured.push!(event)

    await waitUntil.mock.calls[0][0]
    expect(fakes.registration.showNotification).not.toHaveBeenCalled()
  })

  it('falls back to suppressed when the shared cache is unreadable', async () => {
    await importSw()
    const fakes = installFakes([{ visibilityState: 'visible' }])
    setSharedPref('on', { openError: true })
    const { event, waitUntil } = pushEvent(EXPENSE_PAYLOAD)

    captured.push!(event)

    // A storage failure must not crash the worker nor raise the notification:
    // the waitUntil promise still resolves and the bell stays the fallback.
    await expect(waitUntil.mock.calls[0][0]).resolves.toBeUndefined()
    expect(fakes.registration.showNotification).not.toHaveBeenCalled()
  })

  it('ignores a malformed payload without waiting on anything', async () => {
    await importSw()
    const fakes = installFakes([])
    const { event, waitUntil } = pushEvent(null, true)

    captured.push!(event)

    expect(waitUntil).not.toHaveBeenCalled()
    expect(fakes.registration.showNotification).not.toHaveBeenCalled()
  })
})

describe('notificationclick handler', () => {
  it('opens a new window at the deep link when no app tab exists — and waits for it', async () => {
    await importSw()
    const fakes = installFakes([])
    const { event, close, waitUntil } = clickEvent({ url: '/groups/1?tab=expenses&focus=expense:42' })

    captured.click!(event)

    expect(close).toHaveBeenCalledTimes(1)
    expect(waitUntil).toHaveBeenCalledTimes(1)
    await waitUntil.mock.calls[0][0]
    expect(fakes.clients.openWindow).toHaveBeenCalledTimes(1)
    expect(fakes.clients.openWindow).toHaveBeenCalledWith('/groups/1?tab=expenses&focus=expense:42')
  })

  it('focuses an existing app tab and routes it in-page via postMessage (not a new window)', async () => {
    await importSw()
    const client = {
      visibilityState: 'visible',
      focus: vi.fn(async () => {}),
      postMessage: vi.fn(),
    }
    const fakes = installFakes([client])
    const { event, waitUntil } = clickEvent({ url: '/groups/7?tab=expenses&focus=expense:44' })

    captured.click!(event)

    await waitUntil.mock.calls[0][0]
    expect(client.focus).toHaveBeenCalledTimes(1)
    // Routing is in-page: the worker posts the site-relative target and the
    // app's message listener (PushDeepLink) does the react-router navigation.
    // postMessage reaches controlled AND uncontrolled clients, so the deep link
    // lands even when this tab is not controlled by the worker handling the
    // click (the old Client.navigate path threw in Firefox / no-op'd in Chrome).
    expect(client.postMessage).toHaveBeenCalledWith({
      type: 'em:nav',
      url: '/groups/7?tab=expenses&focus=expense:44',
    })
    expect(fakes.clients.openWindow).not.toHaveBeenCalled()
  })

  it('routes the visible tab, not the oldest, when several app tabs are open', async () => {
    await importSw()
    const oldest = { visibilityState: 'hidden', focus: vi.fn(async () => {}), postMessage: vi.fn() }
    const active = { visibilityState: 'visible', focus: vi.fn(async () => {}), postMessage: vi.fn() }
    const fakes = installFakes([oldest, active])
    const { event, waitUntil } = clickEvent({ url: '/groups/9?tab=activity&focus=feed:expense.deleted' })

    captured.click!(event)

    await waitUntil.mock.calls[0][0]
    // The user is looking at the second tab; the click must route THAT one, not
    // the first-opened (the reported "works on the first tab, not the second").
    expect(active.focus).toHaveBeenCalledTimes(1)
    expect(active.postMessage).toHaveBeenCalledWith({
      type: 'em:nav',
      url: '/groups/9?tab=activity&focus=feed:expense.deleted',
    })
    expect(oldest.focus).not.toHaveBeenCalled()
    expect(oldest.postMessage).not.toHaveBeenCalled()
    expect(fakes.clients.openWindow).not.toHaveBeenCalled()
  })

  it('still focuses the tab when posting the route throws (the client died mid-flight)', async () => {
    await importSw()
    const client = {
      visibilityState: 'visible',
      focus: vi.fn(async () => {}),
      postMessage: vi.fn(() => {
        throw new DOMException('client closed', 'InvalidStateError')
      }),
    }
    const fakes = installFakes([client])
    const { event, waitUntil } = clickEvent({ url: '/groups/3?tab=expenses&focus=expense:8' })

    captured.click!(event)

    // The throw is swallowed, so the waitUntil promise resolves (no unhandled
    // rejection) and the tab was still brought to the front.
    await expect(waitUntil.mock.calls[0][0]).resolves.toBeUndefined()
    expect(client.focus).toHaveBeenCalledTimes(1)
    expect(client.postMessage).toHaveBeenCalledTimes(1)
    expect(fakes.clients.openWindow).not.toHaveBeenCalled()
  })

  it('opens the app root when the notification carries no deep link', async () => {
    await importSw()
    const fakes = installFakes([])
    const { event, waitUntil } = clickEvent(undefined)

    captured.click!(event)

    await waitUntil.mock.calls[0][0]
    expect(fakes.clients.openWindow).toHaveBeenCalledWith('/')
  })
})

describe('install / activate (a rebuilt worker takes over at once, §2.14)', () => {
  it('skips the waiting state on install so a redeployed worker activates immediately', async () => {
    await importSw()
    const fakes = installFakes()
    const waitUntil: Mock = vi.fn()

    captured.install!({ waitUntil })

    expect(waitUntil).toHaveBeenCalledTimes(1)
    await waitUntil.mock.calls[0][0]
    expect(fakes.skipWaiting).toHaveBeenCalledTimes(1)
  })

  it('claims existing clients on activate so open pages switch to the new worker', async () => {
    await importSw()
    const fakes = installFakes()
    const waitUntil: Mock = vi.fn()

    captured.activate!({ waitUntil })

    // claim() is synchronous, so it runs as the handler executes.
    expect(fakes.clients.claim).toHaveBeenCalledTimes(1)
  })
})

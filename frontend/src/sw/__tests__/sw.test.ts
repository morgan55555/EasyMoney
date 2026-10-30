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
  registration: { showNotification: Mock; scope: string }
  skipWaiting: Mock
}

function installFakes(clients: Array<Record<string, unknown>> = []): WinFakes {
  const fakes: WinFakes = {
    clients: {
      matchAll: vi.fn(async () => clients),
      openWindow: vi.fn(async () => ({})),
      claim: vi.fn(),
    },
    // scope stands in for the registration scope the SW resolves deep links
    // against before client.navigate (which needs an absolute URL).
    registration: { showNotification: vi.fn(async () => ({})), scope: 'http://localhost/' },
    skipWaiting: vi.fn(async () => undefined),
  }
  ;(window as unknown as { clients: unknown }).clients = fakes.clients
  ;(window as unknown as { registration: unknown }).registration = fakes.registration
  ;(window as unknown as { skipWaiting: unknown }).skipWaiting = fakes.skipWaiting
  return fakes
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
    const { event, waitUntil } = pushEvent(EXPENSE_PAYLOAD)

    captured.push!(event)

    await waitUntil.mock.calls[0][0]
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

  it('focuses and navigates an existing app tab instead of opening a window', async () => {
    await importSw()
    const client = {
      visibilityState: 'visible',
      focus: vi.fn(async () => {}),
      navigate: vi.fn(async () => {}),
    }
    const fakes = installFakes([client])
    const { event, waitUntil } = clickEvent({ url: '/groups/7?tab=expenses&focus=expense:44' })

    captured.click!(event)

    await waitUntil.mock.calls[0][0]
    expect(client.focus).toHaveBeenCalledTimes(1)
    // client.navigate() requires a fully valid absolute URL — a relative
    // path throws and the click silently does nothing (the reported bug:
    // the deep link only worked while no app tab was open). So the relative
    // target is resolved against the registration scope first.
    expect(client.navigate).toHaveBeenCalledWith('http://localhost/groups/7?tab=expenses&focus=expense:44')
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

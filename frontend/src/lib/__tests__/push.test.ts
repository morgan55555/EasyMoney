/**
 * Web Push client (AGENTS.md §2.14): the client-only half of the OS
 * notification path. Covered here:
 *   - `pushSupported()` feature detection (SW + PushManager + Notification);
 *   - `enablePush()` — SW registration, the base64url VAPID key -> byte
 *     conversion (padding included), subscription storage, and the
 *     'ok' / 'denied' (NotAllowedError) / 'unsupported' (VAPID 404, no SW)
 *     outcomes;
 *   - `disablePush()` — local unsubscribe + server forget, idempotent
 *     no-ops, a swallowed server-forget failure, and the endpoint captured
 *     BEFORE the local unsubscribe invalidates it (the server deletes by
 *     (user, endpoint), so a post-unsubscribe read would send '');
 *   - `registerServiceWorker()` — registers /sw.js once, reuses the cached
 *     registration promise, and revalidates for a rebuilt worker.
 *
 * happy-dom ships no Web Push APIs, so each test installs the minimal
 * stand-ins it needs (restored afterwards). The push module caches its SW
 * registration at module level, so every test loads a FRESH module instance
 * (`vi.resetModules()` + dynamic import) — the cached promise must never leak
 * across tests. `../api/endpoints` is mocked, so no network is touched.
 */

import { afterEach, describe, expect, it, vi, type Mock } from 'vitest'

/**
 * base64url (unpadded) encoding of raw bytes — used only to build the
 * expected VAPID key. Deliberately a different code path (btoa) than the
 * atob-based decoder under test, so it serves as an independent oracle.
 */
function toBase64Url(bytes: Uint8Array): string {
  let bin = ''
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i])
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

vi.mock('../../api/endpoints', () => ({
  pushApi: {
    vapidPublicKey: vi.fn(),
    subscribe: vi.fn(),
    unsubscribe: vi.fn(),
  },
}))

interface PushApiMocks {
  vapidPublicKey: Mock
  subscribe: Mock
  unsubscribe: Mock
}

/** Fresh push module + fresh (mocked) pushApi, with call history reset.
 *
 * Imports must be sequential: after `vi.resetModules()` the mock factory runs
 * per import, so the push module's own `endpoints` import has to finish (and
 * cache its instance) before this file imports `endpoints` directly — a
 * concurrent `Promise.all` would race two factory runs and hand the two
 * modules different `pushApi` objects.
 */
async function loadPush(): Promise<{ push: typeof import('../push'); pushApi: PushApiMocks }> {
  vi.resetModules()
  const push = await import('../push')
  const endpoints = await import('../../api/endpoints')
  const pushApi = endpoints.pushApi as unknown as PushApiMocks
  pushApi.vapidPublicKey.mockReset()
  pushApi.subscribe.mockReset()
  pushApi.unsubscribe.mockReset()
  return { push, pushApi }
}

// --- Browser API stand-ins --------------------------------------------------------
// `pushSupported()` only feature-detects presence (`in` checks), so plain
// class stand-ins satisfy the gate; the behavior under test lives on the
// registration object the (stubbed) `navigator.serviceWorker` returns.

const origServiceWorker = Object.getOwnPropertyDescriptor(navigator, 'serviceWorker')
const origPushManager = Object.getOwnPropertyDescriptor(window, 'PushManager')
const origNotification = Object.getOwnPropertyDescriptor(window, 'Notification')

function installGlobals(opts: {
  serviceWorker?: boolean
  pushManager?: boolean
  notification?: boolean
  register?: (url: string) => Promise<ServiceWorkerRegistration>
  getRegistration?: () => Promise<ServiceWorkerRegistration | undefined>
}): { register: Mock; getRegistration: Mock } {
  const register = vi.fn(
    opts.register ?? (async () => { throw new Error('register not stubbed') }),
  )
  const getRegistration = vi.fn(
    opts.getRegistration ?? (async () => undefined as ServiceWorkerRegistration | undefined),
  )
  if (opts.serviceWorker) {
    Object.defineProperty(navigator, 'serviceWorker', {
      value: { register, getRegistration },
      configurable: true,
      writable: true,
    })
  }
  if (opts.pushManager) {
    Object.defineProperty(window, 'PushManager', {
      value: class PushManager {},
      configurable: true,
      writable: true,
    })
  }
  if (opts.notification) {
    Object.defineProperty(window, 'Notification', {
      value: class Notification {},
      configurable: true,
      writable: true,
    })
  }
  return { register, getRegistration }
}

function uninstallGlobals(): void {
  if (origServiceWorker) Object.defineProperty(navigator, 'serviceWorker', origServiceWorker)
  else delete (navigator as unknown as { serviceWorker?: unknown }).serviceWorker
  if (origPushManager) Object.defineProperty(window, 'PushManager', origPushManager)
  else delete (window as unknown as { PushManager?: unknown }).PushManager
  if (origNotification) Object.defineProperty(window, 'Notification', origNotification)
  else delete (window as unknown as { Notification?: unknown }).Notification
}

interface FakeSubscription {
  endpoint: string
  keys: { p256dh: string; auth: string }
  unsubscribe: Mock
  toJSON: () => { endpoint: string; keys: { p256dh: string; auth: string } }
}

/**
 * A spec-shaped subscription: once `unsubscribe()` has resolved, reading
 * `endpoint` returns '' (the Web Push spec invalidates it) — exactly the
 * behavior that made the old disablePush send an empty endpoint to the server.
 */
function makeSubscription(endpoint = 'https://push.example/sub-1'): FakeSubscription {
  const keys = { p256dh: 'p256dh', auth: 'auth' }
  let current = endpoint
  return {
    get endpoint() {
      return current
    },
    keys,
    unsubscribe: vi.fn(async () => {
      current = ''
      return true
    }),
    toJSON: () => ({ endpoint: current, keys: { ...keys } }),
  }
}

function makeRegistration(
  sub: FakeSubscription | null,
  subscribeImpl?: (opts: PushSubscriptionOptionsInit) => Promise<FakeSubscription>,
): { reg: ServiceWorkerRegistration; subscribe: Mock; getSubscription: Mock; update: Mock } {
  const subscribe = vi.fn(async (opts: PushSubscriptionOptionsInit) => {
    if (subscribeImpl) return subscribeImpl(opts)
    if (!sub) throw new DOMException('no subscription', 'InvalidStateError')
    return sub
  })
  const getSubscription = vi.fn(async () => sub)
  const update = vi.fn(async () => undefined)
  const reg = {
    pushManager: { subscribe, getSubscription },
    update,
  } as unknown as ServiceWorkerRegistration
  return { reg, subscribe, getSubscription, update }
}

const ALL: { serviceWorker: true; pushManager: true; notification: true } = {
  serviceWorker: true,
  pushManager: true,
  notification: true,
}

afterEach(() => {
  uninstallGlobals()
})

describe('pushSupported (§2.14)', () => {
  it('is false when the browser lacks the Web Push APIs', async () => {
    const { push } = await loadPush()
    expect(push.pushSupported()).toBe(false)
  })

  it('is false when only some of the APIs are present', async () => {
    installGlobals({ serviceWorker: true, pushManager: true })
    const { push } = await loadPush()
    expect(push.pushSupported()).toBe(false)
  })

  it('is true when Service Worker, PushManager and Notification all exist', async () => {
    installGlobals(ALL)
    const { push } = await loadPush()
    expect(push.pushSupported()).toBe(true)
  })
})

describe('registerServiceWorker', () => {
  it('registers /sw.js once, reuses the cached registration, and revalidates', async () => {
    const { reg, update } = makeRegistration(null)
    const stubs = installGlobals({ serviceWorker: true, register: async () => reg })
    const { push } = await loadPush()

    const first = await push.registerServiceWorker()
    const second = await push.registerServiceWorker()

    expect(first).toBe(reg)
    expect(second).toBe(reg)
    expect(stubs.register).toHaveBeenCalledTimes(1)
    expect(stubs.register).toHaveBeenCalledWith('/sw.js')
    // A rebuilt /sw.js must be picked up on the next reload; the registration
    // revalidates exactly once (the promise is cached).
    expect(update).toHaveBeenCalledTimes(1)
  })
})

describe('enablePush', () => {
  it('subscribes and stores the subscription, decoding the base64url VAPID key (with padding)', async () => {
    // 49 bytes -> 68 standard-base64 chars with two '=' paddings -> a 66-char
    // base64url string, so the padding re-addition is exercised.
    const raw = new Uint8Array(49)
    for (let i = 0; i < raw.length; i++) raw[i] = (i * 7 + 3) % 256
    const vapidKey = toBase64Url(raw)
    expect(vapidKey.length).toBe(66)
    expect(vapidKey).not.toContain('=')

    const sub = makeSubscription()
    const { reg, subscribe } = makeRegistration(sub)
    installGlobals({ ...ALL, register: async () => reg })
    const { push, pushApi } = await loadPush()
    pushApi.vapidPublicKey.mockResolvedValue({ key: vapidKey })
    pushApi.subscribe.mockResolvedValue(undefined)

    const result = await push.enablePush()

    expect(result).toBe('ok')
    expect(pushApi.vapidPublicKey).toHaveBeenCalledTimes(1)
    // The key handed to the browser is the exact VAPID byte sequence.
    const [opts] = subscribe.mock.calls[0]
    expect(opts.userVisibleOnly).toBe(true)
    const received = opts.applicationServerKey as Uint8Array
    expect(received.byteLength).toBe(raw.byteLength)
    expect(Array.from(received)).toEqual(Array.from(raw))
    // The subscription is stored server-side in JSON form.
    expect(pushApi.subscribe).toHaveBeenCalledTimes(1)
    expect(pushApi.subscribe).toHaveBeenCalledWith({
      endpoint: sub.endpoint,
      keys: { p256dh: sub.keys.p256dh, auth: sub.keys.auth },
    })
  })

  it('decodes a VAPID key whose base64url length is already a multiple of 4', async () => {
    // 48 bytes -> exactly 64 base64 chars, no padding at all.
    const raw = new Uint8Array(48)
    for (let i = 0; i < raw.length; i++) raw[i] = 255 - i
    const vapidKey = toBase64Url(raw)
    expect(vapidKey.length).toBe(64)

    const sub = makeSubscription()
    const { reg, subscribe } = makeRegistration(sub)
    installGlobals({ ...ALL, register: async () => reg })
    const { push, pushApi } = await loadPush()
    pushApi.vapidPublicKey.mockResolvedValue({ key: vapidKey })
    pushApi.subscribe.mockResolvedValue(undefined)

    expect(await push.enablePush()).toBe('ok')
    const [opts] = subscribe.mock.calls[0]
    const received = opts.applicationServerKey as Uint8Array
    expect(Array.from(received)).toEqual(Array.from(raw))
  })

  it('returns "denied" when the permission prompt is refused', async () => {
    const sub = makeSubscription()
    const { reg, subscribe } = makeRegistration(
      sub,
      async () => {
        throw new DOMException('the user dismissed the prompt', 'NotAllowedError')
      },
    )
    installGlobals({ ...ALL, register: async () => reg })
    const { push, pushApi } = await loadPush()
    pushApi.vapidPublicKey.mockResolvedValue({ key: 'a2V5' })

    const result = await push.enablePush()

    expect(result).toBe('denied')
    expect(subscribe).toHaveBeenCalledTimes(1)
    expect(pushApi.subscribe).not.toHaveBeenCalled()
  })

  it('returns "unsupported" when the browser lacks Web Push', async () => {
    const { push, pushApi } = await loadPush()
    expect(await push.enablePush()).toBe('unsupported')
    expect(pushApi.vapidPublicKey).not.toHaveBeenCalled()
  })

  it('returns "unsupported" when the VAPID endpoint 404s (push disabled)', async () => {
    const sub = makeSubscription()
    const { reg, subscribe } = makeRegistration(sub)
    installGlobals({ ...ALL, register: async () => reg })
    const { push, pushApi } = await loadPush()
    pushApi.vapidPublicKey.mockRejectedValue(new Error('404 Not Found'))

    const result = await push.enablePush()

    expect(result).toBe('unsupported')
    expect(subscribe).not.toHaveBeenCalled()
    expect(pushApi.subscribe).not.toHaveBeenCalled()
  })

  it('returns "unsupported" when the service worker fails to register', async () => {
    installGlobals({
      ...ALL,
      register: async () => {
        throw new Error('Service Worker registration failed')
      },
    })
    const { push, pushApi } = await loadPush()

    expect(await push.enablePush()).toBe('unsupported')
    expect(pushApi.vapidPublicKey).not.toHaveBeenCalled()
  })
})

describe('disablePush', () => {
  it('unsubscribes locally and forgets the subscription server-side', async () => {
    const sub = makeSubscription('https://push.example/bye')
    const { reg } = makeRegistration(sub)
    installGlobals({ serviceWorker: true, getRegistration: async () => reg })
    const { push, pushApi } = await loadPush()
    pushApi.unsubscribe.mockResolvedValue(undefined)

    await push.disablePush()

    expect(sub.unsubscribe).toHaveBeenCalledTimes(1)
    expect(pushApi.unsubscribe).toHaveBeenCalledTimes(1)
    expect(pushApi.unsubscribe).toHaveBeenCalledWith('https://push.example/bye')
  })

  it('still un-subscribes locally when the server forget fails', async () => {
    const sub = makeSubscription()
    const { reg } = makeRegistration(sub)
    installGlobals({ serviceWorker: true, getRegistration: async () => reg })
    const { push, pushApi } = await loadPush()
    pushApi.unsubscribe.mockRejectedValue(new Error('500 Internal Server Error'))

    await expect(push.disablePush()).resolves.toBeUndefined()
    expect(sub.unsubscribe).toHaveBeenCalledTimes(1)
  })

  it('is a no-op when there is no service worker registration', async () => {
    installGlobals({ serviceWorker: true })
    const { push, pushApi } = await loadPush()

    await push.disablePush()

    expect(pushApi.unsubscribe).not.toHaveBeenCalled()
  })

  it('is a no-op when there is no subscription', async () => {
    const { reg } = makeRegistration(null)
    installGlobals({ serviceWorker: true, getRegistration: async () => reg })
    const { push, pushApi } = await loadPush()

    await push.disablePush()

    expect(pushApi.unsubscribe).not.toHaveBeenCalled()
  })
})

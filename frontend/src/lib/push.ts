/**
 * Web Push client (AGENTS.md §2.14).
 *
 * The client-only half of the OS-notification path. It registers the service
 * worker quietly (never a permission prompt) and, on the Settings toggle
 * gesture, fetches the VAPID key and calls `PushManager.subscribe` — the one
 * place the browser's push-permission prompt is raised — then stores the
 * subscription server-side. Turning the toggle off unsubscribes locally and
 * server-side (the permission itself is never revoked programmatically).
 */

import { pushApi } from '../api/endpoints'
import type { PushSubscriptionJson } from '../types'

/** True when this browser can do Web Push (SW + PushManager + notifications). */
export function pushSupported(): boolean {
  return (
    typeof window !== 'undefined' &&
    'serviceWorker' in navigator &&
    'PushManager' in window &&
    'Notification' in window
  )
}

let swPromise: Promise<ServiceWorkerRegistration> | null = null

/**
 * Register (or reuse) the service worker at /sw.js. Never raises a permission
 * prompt — only `PushManager.subscribe` does, and that happens in
 * {@link enablePush} on the user's gesture. `updateViaCache: 'none'` makes the
 * browser check the *network* (never the HTTP cache) when looking for a newer
 * worker, and a fire-and-forget `update()` revalidates /sw.js right after —
 * together they guarantee a rebuilt worker (e.g. a new click handler) is picked
 * up on the next load instead of the previously installed copy lingering.
 */
export function registerServiceWorker(): Promise<ServiceWorkerRegistration> {
  if (!swPromise) {
    swPromise = navigator.serviceWorker
      .register('/sw.js', { updateViaCache: 'none' })
      .then((reg) => {
        void reg.update().catch(() => undefined)
        return reg
      })
  }
  return swPromise
}

/** base64url (no padding) -> the ArrayBuffer-backed Uint8Array `PushManager.subscribe` expects. */
function urlBase64ToUint8Array(base64String: string): Uint8Array<ArrayBuffer> {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4)
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/')
  const rawData = window.atob(base64)
  const output = new Uint8Array(rawData.length)
  for (let i = 0; i < rawData.length; ++i) output[i] = rawData.charCodeAt(i)
  return output
}

/** The `PushManager.subscribe` options this app uses (shared by enable + reconcile). */
function pushSubscribeOptions(vapidKey: string): PushSubscriptionOptionsInit {
  return {
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(vapidKey),
  }
}

/** Map a browser `PushSubscription` onto the JSON the server stores. */
function subToJson(sub: PushSubscription): PushSubscriptionJson {
  const json = sub.toJSON()
  const keys = json.keys! // a real subscription always carries both keys
  return {
    endpoint: sub.endpoint,
    keys: { p256dh: keys.p256dh, auth: keys.auth },
  }
}

export type PushResult = 'ok' | 'denied' | 'unsupported'

/**
 * Enable Web Push — call on the user's toggle gesture. Registers the SW,
 * fetches the VAPID key, and subscribes (raising the permission prompt).
 *
 * - `'ok'` — subscribed + stored; the caller turns the preference on.
 * - `'denied'` — the user refused the prompt; stay off.
 * - `'unsupported'` — no Web Push in the browser, or the server has push
 *   disabled (VAPID endpoint 404); stay off and show the hint.
 */
export async function enablePush(): Promise<PushResult> {
  if (!pushSupported()) return 'unsupported'
  try {
    const reg = await registerServiceWorker()
    const { key } = await pushApi.vapidPublicKey()
    const sub = await reg.pushManager.subscribe(pushSubscribeOptions(key))
    await pushApi.subscribe(subToJson(sub))
    return 'ok'
  } catch (e) {
    // A refused permission surfaces as a NotAllowedError; anything else (no SW,
    // VAPID 404, network) reads as "can't enable" from the user's point of view.
    if (e instanceof DOMException && e.name === 'NotAllowedError') return 'denied'
    return 'unsupported'
  }
}

/**
 * Disable Web Push: unsubscribe the browser's subscription and forget it
 * server-side (idempotent). The permission is intentionally not revoked. The
 * endpoint is captured BEFORE the local unsubscribe: the Web Push spec
 * invalidates it (the getter then returns '') and the server deletes by
 * (user, endpoint), so an empty endpoint would be a silent no-op.
 */
export async function disablePush(): Promise<void> {
  try {
    const reg = await navigator.serviceWorker.getRegistration()
    if (!reg) return
    const sub = await reg.pushManager.getSubscription()
    if (!sub) return
    const endpoint = sub.endpoint
    await sub.unsubscribe()
    try {
      await pushApi.unsubscribe(endpoint)
    } catch {
      // The server row may already be gone (e.g. pruned); the local
      // unsubscribe still holds.
    }
  } catch {
    // No SW or no push support — nothing to clean up locally.
  }
}

/**
 * Reconcile the Web Push subscription with the server — call on app load while
 * the browser-notifications preference is on (and on a cross-tab turn-on).
 *
 * A plain page reload re-registers the service worker but never re-confirms the
 * subscription, so once the endpoint the browser now holds differs from — or was
 * pruned against — the stored row, pushes go to a dead endpoint (HTTP 410) and
 * silently stop until the user re-enables the toggle. This does what a manual
 * resubscribe does, automatically, on load: re-POST the *current* endpoint
 * (the server upserts by (user, endpoint), so it is idempotent).
 *
 * No permission prompt is ever raised here (§2.14): the existing subscription
 * is re-POSTed as-is, and `subscribe()` is only invoked when
 * `Notification.permission === 'granted'` (which never prompts). Any failure
 * (no SW, VAPID 404, network) is swallowed — the in-app bell is unaffected.
 */
export async function reconcilePush(): Promise<void> {
  if (!pushSupported()) return
  let reg: ServiceWorkerRegistration
  try {
    reg = await registerServiceWorker()
  } catch {
    return
  }
  try {
    let sub = await reg.pushManager.getSubscription()
    if (!sub && typeof Notification !== 'undefined' && Notification.permission === 'granted') {
      // The browser dropped the subscription but permission persists: recreate
      // it. Permission is already granted, so subscribe() raises no prompt.
      const { key } = await pushApi.vapidPublicKey()
      sub = await reg.pushManager.subscribe(pushSubscribeOptions(key))
    }
    if (!sub) return
    await pushApi.subscribe(subToJson(sub))
  } catch {
    // No subscription, VAPID 404, or a network error — nothing to (re)store.
  }
}

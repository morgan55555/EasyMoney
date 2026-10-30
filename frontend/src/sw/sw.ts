/**
 * Web Push service worker (AGENTS.md §2.14).
 *
 * A separate, React-free Vite build emitted as an unhashed `dist/sw.js` so the
 * browser can register it at the fixed URL `/sw.js`. It is the *only* thing
 * that shows an OS-level notification: the in-app bell + sound run in parallel
 * and are untouched. The push payload arrives pre-serialized from the backend
 * (`lang` + the notification row), and the one-line summary, the title, and the
 * click target are all computed from the same helpers the bell uses
 * (lib/notificationText) so the two render identical text and navigate to the
 * same place.
 *
 * Display condition (§2.14): an OS notification is shown only when no app
 * window is visible. While any tab is visible the bell badge + sound already
 * inform the user (no double-alerting); with no clients at all (app closed)
 * the notification shows. The push-permission prompt is raised only inside the
 * Settings-toggle gesture (lib/push.enablePush) — never here, never on load.
 *
 * Updates take over immediately (install → skipWaiting, activate →
 * clients.claim): without that a redeployed worker would sit "waiting" until
 * every tab in the scope is closed, and the stale worker's older click logic
 * would keep handling notifications — the bug where a newly added deep link
 * silently did nothing on click.
 */

import { en, ru, makeT, type Dict, type Lang } from '../i18n/core'
import { invitedToken, makeDescribe, notificationTarget } from '../lib/notificationText'
import type { NotificationOut } from '../types'

/** The notification as pushed (the backend's `notification` object). */
interface PushNotification {
  id: number
  type: string
  payload: Record<string, unknown> | null
  created_at: string
}

/** The Web Push payload the backend posts (services/push.py). */
interface PushPayload {
  lang: Lang
  notification: PushNotification
}

const DICTS: Record<Lang, Dict> = { en, ru }
/** BCP-47 tag for money formatting (mirrors I18nValue.locale). */
const LOCALES: Record<Lang, string> = { en: 'en-US', ru: 'ru-RU' }

const scope = self as unknown as ServiceWorkerGlobalScope

/** The server only ever sends 'en' | 'ru'; anything else falls back to 'en'. */
function pickLang(raw: unknown): Lang {
  return raw === 'ru' ? 'ru' : 'en'
}

/**
 * Where the notification's click leads — the same target the bell's View/Join
 * buttons use: `/invite/{token}` for an email-invite row, the group page for
 * group-scoped rows, or null when there is no deep link.
 */
function targetFor(n: NotificationOut): string | null {
  const token = invitedToken(n)
  if (token) return `/invite/${token}`
  const target = notificationTarget(n)
  if (target) return `/groups/${target.group_id}?tab=${target.tab}&focus=${target.focus}`
  return null
}

/** Show the OS notification, but only when no app tab is visible (§2.14). */
async function showWhenHidden(data: PushPayload): Promise<void> {
  const clients = await scope.clients.matchAll({ type: 'window' })
  if (clients.some((c) => c.visibilityState === 'visible')) return

  const lang = pickLang(data.lang)
  const t = makeT(DICTS[lang])
  const n = data.notification
  const out: NotificationOut = {
    id: n.id,
    type: n.type,
    payload: n.payload,
    read_at: null,
    created_at: n.created_at,
  }
  const body = makeDescribe(t, LOCALES[lang])(n.type, n.payload)
  const group = n.payload?.group_name
  const title = typeof group === 'string' && group !== '' ? group : t('notif.appTitle')

  await scope.registration.showNotification(title, {
    body,
    icon: '/icon.png',
    badge: '/icon.png',
    tag: String(n.id),
    data: { url: targetFor(out) },
  })
}

/**
 * Focus an existing app window (or open one) and navigate it to the target.
 * `Client.navigate()` requires a fully valid *absolute* URL — unlike
 * `clients.openWindow()`, which resolves a relative path against the
 * registration scope — so the site-relative deep link is resolved with
 * `toAbsolute` before navigating an already-open tab. Without that, the
 * navigate call throws and the click silently does nothing (the deep link
 * only worked while no app tab was open).
 */
async function focusAndNavigate(url: string | undefined): Promise<void> {
  const clients = await scope.clients.matchAll({ type: 'window', includeUncontrolled: true })
  let client: WindowClient | undefined
  for (const c of clients) {
    if ('focus' in c) {
      client = c
      break
    }
  }
  if (!client) {
    await scope.clients.openWindow(url ?? '/')
    return
  }
  await client.focus()
  if (url) await client.navigate(toAbsolute(url))
}

/** Resolve a site-relative path to the absolute URL `client.navigate` requires. */
function toAbsolute(url: string): string {
  return new URL(url, scope.registration.scope).href
}

// Lifecycle: a rebuilt worker must take over at once. Without skipWaiting a
// new worker stays "waiting" until every tab in the scope is closed, so after
// a redeploy the stale worker (with older push/click logic) keeps handling
// events — e.g. a notification deep link the old worker didn't know about
// silently does nothing on click. skipWaiting + claim() hand existing pages to
// the new worker immediately. Safe here: this worker only handles push +
// notificationclick (no fetch interception).
scope.addEventListener('install', (event: ExtendableEvent) => {
  event.waitUntil(scope.skipWaiting())
})
scope.addEventListener('activate', () => {
  scope.clients.claim()
})

scope.addEventListener('push', (event: PushEvent) => {
  let data: PushPayload | null = null
  try {
    data = event.data ? (event.data.json() as PushPayload) : null
  } catch {
    return // malformed payload — nothing to show
  }
  if (!data || !data.notification || typeof data.notification.type !== 'string') return
  // waitUntil keeps the SW alive for the whole async body: without it the
  // browser may terminate the worker before matchAll/showNotification finish,
  // which drops the notification (worst on a cold SW, e.g. after a refresh).
  event.waitUntil(showWhenHidden(data))
})

scope.addEventListener('notificationclick', (event: NotificationEvent) => {
  event.notification.close()
  const url = (event.notification.data as { url?: string } | undefined)?.url
  // Same reason as in the push handler: openWindow must run before the SW
  // is terminated, or the click silently does nothing.
  event.waitUntil(focusAndNavigate(url))
})

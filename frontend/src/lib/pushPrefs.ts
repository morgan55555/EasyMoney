/**
 * The shared channel between the app's pages and the Web Push service worker
 * for the while-visible preference (AGENTS.md §2.14).
 *
 * A service worker cannot read the page's localStorage (WorkerGlobalScope
 * does not expose Storage — `self.localStorage` is undefined in real
 * browsers), so the client-only `easymoney.pushWhileVisible` preference,
 * whose source of truth remains localStorage (cross-tab synced by
 * lib/browserNotify), is mirrored into a Cache Storage record both sides
 * can reach: the page writes it on every app load and on every change, and
 * the worker reads it fresh at push time. A missing record or a read error
 * means "off" (suppressed) — the default.
 */

/** Cache name shared between the pages and the service worker. */
export const PREFS_CACHE = 'easymoney:prefs'
/** Cache key of the while-visible preference (text value `on` / `off`). */
export const WHILE_VISIBLE_KEY = 'while-visible'

/** Mirror the preference into the shared cache (best effort). */
export async function writeWhileVisiblePref(on: boolean): Promise<void> {
  try {
    const cache = await caches.open(PREFS_CACHE)
    await cache.put(WHILE_VISIBLE_KEY, new Response(on ? 'on' : 'off'))
  } catch {
    // Cache Storage unavailable (or blocked) — the worker falls back to off.
  }
}

/** Read the preference from the shared cache (the worker's view of it). */
export async function readWhileVisiblePref(): Promise<boolean> {
  try {
    const cache = await caches.open(PREFS_CACHE)
    const entry = await cache.match(WHILE_VISIBLE_KEY)
    if (!entry) return false
    return (await entry.text()) === 'on'
  } catch {
    return false
  }
}

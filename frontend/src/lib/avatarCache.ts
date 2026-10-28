/**
 * Session-scoped avatar cache (AGENTS.md §2.13).
 *
 * Avatars are served behind auth (GET /users/{id}/avatar), so an <img> cannot
 * use the URL directly; the image is fetched with the bearer token and shown
 * through an object URL. The same user's avatar is rendered everywhere a
 * username appears (header, members, balances, settlements, feed,
 * participants) — without caching, every <Avatar> instance on the page would
 * fetch and decode the same image once per instance. This module keeps
 * exactly one fetch and one object URL per avatar URL per page session, so
 * the browser decodes each avatar only once.
 *
 * The avatar URL string does not change when its content does (set/clear), so
 * after the current user's avatar mutation we drop the entry with
 * `invalidateAvatar()`; every mounted <Avatar> with that URL refetches
 * (concurrent refetches dedupe into one request) and swaps to the new image
 * while the previous one keeps showing until the replacement arrives.
 *
 * Object URLs are never revoked within a session: revoking one that an <img>
 * still references mid-swap would break it. Blobs are released with the page;
 * the count is bounded by the number of avatar loads in a session.
 */

import { fetchAuthedBlob } from '../api/endpoints'

type Entry =
  | { kind: 'loading'; promise: Promise<string> }
  | { kind: 'ready'; objectUrl: string }

const entries = new Map<string, Entry>()
const listeners = new Set<() => void>()
let version = 0

function emit(): void {
  version += 1
  for (const listener of [...listeners]) listener()
}

/**
 * Resolve the object URL to render for an avatar, fetching at most once per
 * URL: concurrent callers share the in-flight promise, and a cached result is
 * returned without any network work. Rejects if the fetch fails (the entry is
 * dropped so a later call retries).
 */
export function loadAvatar(url: string): Promise<string> {
  const existing = entries.get(url)
  if (existing) {
    return existing.kind === 'ready' ? Promise.resolve(existing.objectUrl) : existing.promise
  }
  const promise = fetchAuthedBlob(url)
    .then((blob) => {
      const objectUrl = URL.createObjectURL(blob)
      const current = entries.get(url)
      if (current && current.kind === 'loading' && current.promise === promise) {
        entries.set(url, { kind: 'ready', objectUrl })
        emit()
        return objectUrl
      }
      if (current) {
        // A newer load superseded this one (invalidate + reload happened while
        // in flight): drop our blob and hand the caller the current entry.
        URL.revokeObjectURL(objectUrl)
        return current.kind === 'ready' ? current.objectUrl : current.promise
      }
      // Invalidated and no reload started yet: hand the blob to the caller
      // without committing, so the invalidation-triggered reload still runs.
      return objectUrl
    })
    .catch((err: unknown) => {
      const current = entries.get(url)
      if (current && current.kind === 'loading' && current.promise === promise) entries.delete(url)
      throw err
    })
  entries.set(url, { kind: 'loading', promise })
  return promise
}

/** The object URL for a cached avatar, or null if it is not loaded yet. */
export function getReadyAvatarUrl(url: string | null | undefined): string | null {
  if (!url) return null
  const entry = entries.get(url)
  return entry && entry.kind === 'ready' ? entry.objectUrl : null
}

/**
 * Drop the cached entry for an avatar URL — used after the current user's
 * avatar is set or cleared, since the URL string stays the same while its
 * content changes. Mounted <Avatar> subscribers refetch on the next tick.
 */
export function invalidateAvatar(url: string | null | undefined): void {
  if (!url) return
  if (entries.delete(url)) emit()
}

/** Subscribe to cache changes (load completions, invalidations). */
export function subscribeAvatarCache(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/** Snapshot for useSyncExternalStore; changes with every cache change. */
export function getAvatarCacheVersion(): number {
  return version
}

/** Test helper: drop every entry and reset the version between tests. */
export function clearAvatarCache(): void {
  entries.clear()
  version = 0
}

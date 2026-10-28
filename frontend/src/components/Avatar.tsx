/**
 * Avatar (AGENTS.md §2.13): renders a user's avatar image or an initials
 * fallback. The image is served behind auth (GET /users/{id}/avatar), so it
 * is fetched with the bearer token and shown via an object URL — through the
 * session cache (lib/avatarCache.ts), so the same avatar URL is fetched and
 * decoded once per page no matter how many <Avatar> instances render it.
 *
 * `url` is the short avatar_url from the API (null = no avatar → initials).
 * When the cached image is invalidated (the current user's avatar was
 * set/cleared), the previous image keeps showing until the replacement
 * arrives — no flash to initials. A 404 (the avatar was cleared, e.g. in
 * another tab) drops straight to initials; any other fetch failure keeps the
 * previous image, so a stale/broken avatar never blocks the UI.
 */

import { useEffect, useState, useSyncExternalStore } from 'react'

import { AuthedBlobError } from '../api/endpoints'
import {
  getAvatarCacheVersion,
  getReadyAvatarUrl,
  loadAvatar,
  subscribeAvatarCache,
} from '../lib/avatarCache'

/** Up to two uppercase initials from a display name (e.g. "frank a." → "FA"). */
export function initialsOf(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (parts.length === 0) return '?'
  const first = parts[0][0] ?? ''
  const last = parts.length > 1 ? (parts[parts.length - 1][0] ?? '') : ''
  const s = (first + last).toUpperCase()
  return s.length > 0 ? s : '?'
}

export interface AvatarProps {
  /** Short avatar URL or null (null → initials fallback). */
  url: string | null
  /** Display name; used for alt text and the initials fallback. */
  name: string
  /** Rendered width/height in px (default 32). */
  size?: number
  className?: string
}

/**
 * The object URL to render for an avatar: the cached one when ready, the
 * previous one kept while an invalidated entry refetches. Null (→ initials)
 * only while no image has loaded yet or the url is null.
 */
function useAvatarSrc(url: string | null): string | null {
  const version = useSyncExternalStore(subscribeAvatarCache, getAvatarCacheVersion)
  const [src, setSrc] = useState<string | null>(() => getReadyAvatarUrl(url))

  useEffect(() => {
    if (!url) {
      setSrc(null)
      return
    }
    let cancelled = false
    void loadAvatar(url)
      .then((objectUrl) => {
        if (!cancelled) setSrc(objectUrl)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        // A 404 means the avatar is gone (cleared, possibly from another tab
        // or an older payload): fall back to initials now. Any other failure
        // keeps the previous image if there is one. The cache dropped the
        // entry, so the next mount retries.
        if (err instanceof AuthedBlobError && err.status === 404) setSrc(null)
      })
    return () => {
      cancelled = true
    }
  }, [url, version])

  return src
}

export function Avatar({ url, name, size = 32, className = '' }: AvatarProps) {
  const src = useAvatarSrc(url)

  const box: React.CSSProperties = { width: size, height: size }
  if (src) {
    return (
      <img
        src={src}
        alt={name}
        style={box}
        className={`shrink-0 rounded-full object-cover ${className}`}
      />
    )
  }
  return (
    <span
      role="img"
      aria-label={name}
      style={{ ...box, fontSize: Math.max(10, Math.round(size * 0.38)) }}
      className={`inline-flex shrink-0 select-none items-center justify-center rounded-full bg-indigo-100 font-semibold text-indigo-700 dark:bg-indigo-900 dark:text-indigo-200 ${className}`}
    >
      {initialsOf(name)}
    </span>
  )
}

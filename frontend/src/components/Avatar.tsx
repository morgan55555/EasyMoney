/**
 * Avatar (AGENTS.md §2.13): renders a user's avatar image or an initials
 * fallback. The image is served behind auth (GET /users/{id}/avatar), so it is
 * fetched with the bearer token and shown via an object URL; the object URL is
 * revoked when the component unmounts or the url changes.
 *
 * `url` is the short avatar_url from the API (null = no avatar → initials).
 * Falls back to initials on fetch failure too, so a stale/broken avatar never
 * blocks the UI.
 */

import { useEffect, useState } from 'react'

import { fetchAuthedBlob } from '../api/endpoints'

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

export function Avatar({ url, name, size = 32, className = '' }: AvatarProps) {
  const [src, setSrc] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    let objectUrl: string | null = null
    if (!url) {
      setSrc(null)
      return
    }
    fetchAuthedBlob(url)
      .then((blob) => {
        if (cancelled) return
        objectUrl = URL.createObjectURL(blob)
        setSrc(objectUrl)
      })
      .catch(() => {
        if (!cancelled) setSrc(null)
      })
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [url])

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

/**
 * App mark / logo (AGENTS.md §2.13): renders the light or dark variant of the
 * app icon depending on the *resolved* theme. The dark variant is a separate
 * client asset (`/icon-dark.png`); the resolved mode comes from ThemeManager,
 * which already handles `system` via `prefers-color-scheme`, so `system` picks
 * the right mark live. Only the `src` of a single <img> changes — callers keep
 * passing their own sizing/rounded/shadow classes, so layout is unaffected.
 */

import { useTheme } from '../lib/theme'

export function AppIcon({ className = '', alt = '' }: { className?: string; alt?: string }) {
  const { resolved } = useTheme()
  return <img src={resolved === 'dark' ? '/icon-dark.png' : '/icon.png'} alt={alt} className={className} />
}

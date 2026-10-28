/**
 * Theme manager (AGENTS.md §2.13): a client-only preference (`light` |
 * `dark` | `system`, default `system`) persisted in localStorage.
 *
 * Uses Tailwind's class strategy: the resolved dark mode toggles a `.dark`
 * class on <html>. `system` follows `prefers-color-scheme` via a matchMedia
 * listener, so switching the OS theme updates the app live. This is a pure
 * client preference — it is never sent to the server and never part of the
 * API (unlike the user's `preferred_language`).
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

export type Theme = 'light' | 'dark' | 'system'

const THEME_KEY = 'easymoney.theme'

/** Pure resolution: what the OS/system choice means right now. */
export function resolveTheme(theme: Theme, systemDark: boolean): 'light' | 'dark' {
  if (theme === 'system') return systemDark ? 'dark' : 'light'
  return theme
}

function readStoredTheme(): Theme {
  try {
    const v = localStorage.getItem(THEME_KEY)
    if (v === 'light' || v === 'dark' || v === 'system') return v
  } catch {
    // localStorage unavailable (e.g. unit tests without jsdom) — fall through.
  }
  return 'system'
}

function systemPrefersDark(): boolean {
  return typeof window !== 'undefined' && window.matchMedia?.('(prefers-color-scheme: dark)')?.matches === true
}

interface ThemeContextValue {
  /** The persisted preference (light | dark | system). */
  theme: Theme
  /** The concrete mode after resolving `system` (light | dark). */
  resolved: 'light' | 'dark'
  setTheme: (theme: Theme) => void
}

const ThemeContext = createContext<ThemeContextValue>({
  theme: 'system',
  resolved: 'light',
  setTheme: () => undefined,
})

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(() => readStoredTheme())
  const [systemDark, setSystemDark] = useState<boolean>(() => systemPrefersDark())

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next)
    try {
      localStorage.setItem(THEME_KEY, next)
    } catch {
      // Ignore storage failures; the in-memory choice still applies this session.
    }
  }, [])

  // Track the OS preference so `system` stays live.
  useEffect(() => {
    const mq = window.matchMedia?.('(prefers-color-scheme: dark)')
    if (!mq) return
    const onChange = (e: MediaQueryListEvent) => setSystemDark(e.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  const resolved = resolveTheme(theme, systemDark)

  // Apply the class to <html> (Tailwind darkMode: "class").
  useEffect(() => {
    const root = document.documentElement
    if (resolved === 'dark') root.classList.add('dark')
    else root.classList.remove('dark')
  }, [resolved])

  const value = useMemo<ThemeContextValue>(() => ({ theme, resolved, setTheme }), [theme, resolved, setTheme])

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme(): ThemeContextValue {
  return useContext(ThemeContext)
}

/**
 * Browser (Web) notifications preferences (AGENTS.md §2.14): client-only
 * on/off switches, persisted in localStorage, default off. Mirror the theme
 * manager: they are never sent to the server and never part of the API.
 *
 * Two preferences:
 *   - `enabled` (`easymoney.browserNotify`): whether Web Push is active in
 *     this browser. Raising the actual OS notification is done by the Web
 *     Push service worker (src/sw/sw.ts, driven by the Settings toggle
 *     through lib/push); this module only owns the preferences.
 *   - `whileVisible` (`easymoney.pushWhileVisible`): whether an OS
 *     notification may be shown while an app tab is visible (default off =
 *     suppressed while a tab is visible). The service worker cannot read the
 *     page's localStorage, so this provider mirrors the preference into a
 *     shared Cache Storage record (lib/pushPrefs) on every app load and on
 *     every change; the worker reads that record at push time, so the choice
 *     applies from the very next push — no reload or re-subscribe needed.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { writeWhileVisiblePref } from './pushPrefs'

const STORAGE_KEY = 'easymoney.browserNotify'
const WHILE_VISIBLE_KEY = 'easymoney.pushWhileVisible'

function readStored(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'on'
  } catch {
    // localStorage unavailable (e.g. unit tests without storage) — default off.
    return false
  }
}

function persist(on: boolean): void {
  try {
    localStorage.setItem(STORAGE_KEY, on ? 'on' : 'off')
  } catch {
    // Ignore storage failures; the in-memory choice still applies this session.
  }
}

function readWhileVisible(): boolean {
  try {
    return localStorage.getItem(WHILE_VISIBLE_KEY) === 'on'
  } catch {
    return false
  }
}

function persistWhileVisible(on: boolean): void {
  try {
    localStorage.setItem(WHILE_VISIBLE_KEY, on ? 'on' : 'off')
  } catch {
    // Ignore storage failures; the in-memory choice still applies this session.
  }
}

interface BrowserNotifyContextValue {
  /** The persisted preference (default off). */
  enabled: boolean
  setEnabled: (next: boolean) => void
  /** Whether OS notifications may show while an app tab is visible (default off). */
  whileVisible: boolean
  setWhileVisible: (next: boolean) => void
}

const BrowserNotifyContext = createContext<BrowserNotifyContextValue>({
  enabled: false,
  setEnabled: () => undefined,
  whileVisible: false,
  setWhileVisible: () => undefined,
})

export function BrowserNotifyProvider({ children }: { children: ReactNode }) {
  const [enabled, setEnabledState] = useState<boolean>(() => readStored())
  const [whileVisible, setWhileVisibleState] = useState<boolean>(() => readWhileVisible())

  const setEnabled = useCallback((next: boolean) => {
    setEnabledState(next)
    persist(next)
  }, [])

  const setWhileVisible = useCallback((next: boolean) => {
    setWhileVisibleState(next)
    persistWhileVisible(next)
    // Mirror to the shared cache the service worker reads at push time.
    void writeWhileVisiblePref(next)
  }, [])

  // Cross-tab sync (AGENTS.md §2.14, req 7): both preferences live in
  // localStorage and every tab of the browser must apply the same display
  // condition. The `storage` event fires only in the *other* tabs when one
  // writes, so this keeps this tab's in-memory state in lockstep — without
  // it, a tab opened before the toggle stays off in memory and never
  // registers the service worker, so it can never raise an OS notification
  // while hidden. On mount the while-visible mirror in the shared cache is
  // re-asserted from localStorage, so a cleared/absent cache (fresh profile,
  // upgrade from before the mirror existed) can never leave a stored "on"
  // dead.
  useEffect(() => {
    void writeWhileVisiblePref(readWhileVisible())
    const onStorage = (e: StorageEvent) => {
      if (e.key === STORAGE_KEY || e.key === WHILE_VISIBLE_KEY) {
        setEnabledState(readStored())
        setWhileVisibleState(readWhileVisible())
      }
    }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [])

  const value = useMemo<BrowserNotifyContextValue>(
    () => ({ enabled, setEnabled, whileVisible, setWhileVisible }),
    [enabled, setEnabled, whileVisible, setWhileVisible],
  )

  return <BrowserNotifyContext.Provider value={value}>{children}</BrowserNotifyContext.Provider>
}

export function useBrowserNotify(): BrowserNotifyContextValue {
  return useContext(BrowserNotifyContext)
}

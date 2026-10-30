/**
 * Browser (Web) notifications preference (AGENTS.md §2.14): a client-only
 * on/off switch, persisted in localStorage, default off. Mirrors the theme
 * manager: it is never sent to the server and never part of the API.
 *
 * Raising the actual OS notification is done by the Web Push service worker
 * (src/sw/sw.ts, driven by the Settings toggle through lib/push); this module
 * only owns the preference.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

const STORAGE_KEY = 'easymoney.browserNotify'

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

interface BrowserNotifyContextValue {
  /** The persisted preference (default off). */
  enabled: boolean
  setEnabled: (next: boolean) => void
}

const BrowserNotifyContext = createContext<BrowserNotifyContextValue>({
  enabled: false,
  setEnabled: () => undefined,
})

export function BrowserNotifyProvider({ children }: { children: ReactNode }) {
  const [enabled, setEnabledState] = useState<boolean>(() => readStored())

  const setEnabled = useCallback((next: boolean) => {
    setEnabledState(next)
    persist(next)
  }, [])

  // Cross-tab sync (AGENTS.md §2.14, req 7): the preference lives in
  // localStorage and every tab of the browser must apply the same display
  // condition. The `storage` event fires only in the *other* tabs when one
  // writes, so this keeps this tab's in-memory state in lockstep — without
  // it, a tab opened before the toggle stays off in memory and never
  // registers the service worker, so it can never raise an OS notification
  // while hidden.
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === STORAGE_KEY) setEnabledState(readStored())
    }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [])

  const value = useMemo<BrowserNotifyContextValue>(() => ({ enabled, setEnabled }), [enabled, setEnabled])

  return <BrowserNotifyContext.Provider value={value}>{children}</BrowserNotifyContext.Provider>
}

export function useBrowserNotify(): BrowserNotifyContextValue {
  return useContext(BrowserNotifyContext)
}

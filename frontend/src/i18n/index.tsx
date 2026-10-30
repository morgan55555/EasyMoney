/**
 * Lightweight i18n (EN/RU): a context provider + a `t(key, params)` function
 * with `{{param}}` interpolation, plus Russian plural forms via
 * `Intl.PluralRules`. Applied to <html lang>. Components call `useI18n()`;
 * outside the provider (e.g. in unit tests) the context defaults to English.
 *
 * UI language vs. preferred language (AGENTS.md §2.13):
 *   - Inside the app the UI language is exactly the user's
 *     **preferred_language** (a server-side profile field, edited in
 *     settings). `PreferredLanguageSync` (in App.tsx) calls `applyPreferred`
 *     with it; `applyPreferred` updates the state only and never writes to
 *     localStorage, so switching accounts or users always takes effect.
 *   - On the public pages (login / register / invite) the language selector
 *     uses `setLang`, which persists the choice to localStorage. The stored
 *     choice is used as the initial language on load and is passed to the
 *     server as `preferred_language` when registering.
 *   - Initial language on load: stored choice → else browser language
 *     (`browserLang(navigator.language)`) → else `en`.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

import { en, ru, makeT, browserLang, type Lang, type TFunc } from './core'

// The pure helpers (and the dictionaries they fall back to) live in the
// React-free core (core.ts), shared with the service-worker build. Re-exported
// here so existing importers of '../i18n' keep working.
export { pluralForm, browserLang, makeT } from './core'
export type { TFunc, PluralForms } from './core'

export interface I18nValue {
  lang: Lang
  /** BCP-47 tag for Intl formatting: 'en-US' | 'ru-RU'. */
  locale: string
  t: TFunc
  /** Persisted user action (public pages): set the UI language and store it. */
  setLang: (lang: Lang) => void
  /** Server-driven: adopt a language for this session without persisting. */
  applyPreferred: (lang: Lang) => void
}

const STORAGE_KEY = 'easymoney.lang'

/**
 * Read the stored UI-language choice (plain `'en'` / `'ru'`; the legacy
 * `{"lang": …, "manual": …}` JSON is still parsed). `null` when nothing is
 * stored — the initial language then falls back to the browser language.
 */
function readStored(): Lang | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    if (raw === 'en' || raw === 'ru') return raw
    const parsed = JSON.parse(raw) as { lang?: unknown }
    if (parsed && (parsed.lang === 'en' || parsed.lang === 'ru')) return parsed.lang
  } catch {
    // localStorage unavailable or corrupt — fall through.
  }
  return null
}

function persist(lang: Lang): void {
  try {
    localStorage.setItem(STORAGE_KEY, lang)
  } catch {
    // Non-fatal: the choice just won't persist.
  }
}

function initialLang(): Lang {
  return readStored() ?? browserLang(typeof navigator !== 'undefined' ? navigator.language : undefined)
}

const defaultValue: I18nValue = {
  lang: 'en',
  locale: 'en-US',
  t: makeT(en),
  setLang: () => {},
  applyPreferred: () => {},
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(initialLang)

  const setLang = useCallback((next: Lang) => {
    persist(next)
    setLangState(next)
  }, [])

  // Server-driven change: state only, never persisted (the profile is the
  // source of truth, and each account may prefer a different language).
  const applyPreferred = useCallback((next: Lang) => {
    setLangState(next)
  }, [])

  useEffect(() => {
    document.documentElement.lang = lang
  }, [lang])

  const value = useMemo<I18nValue>(
    () => ({
      lang,
      locale: lang === 'ru' ? 'ru-RU' : 'en-US',
      t: makeT(lang === 'ru' ? ru : en),
      setLang,
      applyPreferred,
    }),
    [lang, setLang, applyPreferred],
  )

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}

const I18nContext = createContext<I18nValue>(defaultValue)

/** Current language, locale tag, translator and setters. Safe outside the provider (defaults to EN). */
export function useI18n(): I18nValue {
  return useContext(I18nContext)
}

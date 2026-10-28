/**
 * Lightweight i18n (EN/RU): a context provider + a `t(key, params)` function
 * with `{{param}}` interpolation, plus Russian plural forms via
 * `Intl.PluralRules`. The choice is persisted in localStorage and applied to
 * <html lang>. Components call `useI18n()`; outside the provider (e.g. in
 * unit tests) the context defaults to English.
 *
 * UI language vs. preferred language (AGENTS.md §2.13):
 *   - The **UI language** is a client preference (localStorage). The header
 *     switcher changes it via `setLang`, which marks the choice `manual`.
 *   - The user's **preferred_language** (a server-side profile field) is the
 *     *default*: when the user has made no manual choice, the app adopts the
 *     preferred language via `applyPreferred` (which never marks manual, so a
 *     later manual choice still wins). On load: manual choice → else
 *     preferred → else `en`.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

import { en, ru, type Dict, type Lang } from './locales'

export type TFunc = (key: string, params?: Record<string, string | number>) => string

export interface PluralForms {
  one: string
  few?: string
  many?: string
  /** Optional: RU never selects 'other', so its dictionary omits it. */
  other?: string
}

/** Pick the right word form for `n` in `locale` (en/ru rules via ICU). */
export function pluralForm(locale: string, n: number, forms: PluralForms): string {
  let category: string
  try {
    category = new Intl.PluralRules(locale).select(n)
  } catch {
    category = n === 1 ? 'one' : 'other'
  }
  if (category === 'one') return forms.one
  if (category === 'few') return forms.few ?? forms.other ?? forms.one
  if (category === 'many') return forms.many ?? forms.other ?? forms.one
  return forms.other ?? forms.one
}

function interpolate(template: string, params?: Record<string, string | number>): string {
  if (!params) return template
  return template.replace(/\{\{(\w+)\}\}/g, (match, name: string) =>
    name in params ? String(params[name]) : match,
  )
}

function makeT(dict: Dict): TFunc {
  return (key, params) => interpolate(dict[key] ?? en[key] ?? key, params)
}

export interface I18nValue {
  lang: Lang
  /** BCP-47 tag for Intl formatting: 'en-US' | 'ru-RU'. */
  locale: string
  t: TFunc
  /** Whether the user has made an explicit UI-language choice (switcher). */
  manual: boolean
  /** User action: set the UI language and remember it as a manual choice. */
  setLang: (lang: Lang) => void
  /** Server default: adopt a language without marking it manual (idempotent). */
  applyPreferred: (lang: Lang) => void
}

const STORAGE_KEY = 'easymoney.lang'

interface StoredLang {
  lang: Lang
  manual: boolean
}

function readStored(): StoredLang {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      if (raw === 'en' || raw === 'ru') {
        // Legacy value (written before the manual flag existed) — a stored
        // value means the user chose it, so treat it as manual.
        return { lang: raw, manual: true }
      }
      const parsed = JSON.parse(raw) as Partial<StoredLang>
      if (parsed.lang === 'en' || parsed.lang === 'ru') {
        return { lang: parsed.lang, manual: parsed.manual === true }
      }
    }
  } catch {
    // localStorage unavailable (private mode / tests) — fall through.
  }
  return { lang: 'en', manual: false }
}

function persist(stored: StoredLang): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(stored))
  } catch {
    // Non-fatal: the choice just won't persist.
  }
}

const defaultValue: I18nValue = {
  lang: 'en',
  locale: 'en-US',
  t: makeT(en),
  manual: false,
  setLang: () => {},
  applyPreferred: () => {},
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<StoredLang>(readStored)

  const setLang = useCallback((lang: Lang) => {
    const next: StoredLang = { lang, manual: true }
    persist(next)
    setState(next)
  }, [])

  const applyPreferred = useCallback((lang: Lang) => {
    setState((prev) => {
      // Never overwrite a manual choice; skip the write when unchanged.
      if (prev.manual || prev.lang === lang) return prev
      const next: StoredLang = { lang, manual: false }
      persist(next)
      return next
    })
  }, [])

  useEffect(() => {
    document.documentElement.lang = state.lang
  }, [state.lang])

  const value = useMemo<I18nValue>(
    () => ({
      lang: state.lang,
      locale: state.lang === 'ru' ? 'ru-RU' : 'en-US',
      t: makeT(state.lang === 'ru' ? ru : en),
      manual: state.manual,
      setLang,
      applyPreferred,
    }),
    [state.lang, state.manual, setLang, applyPreferred],
  )

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}

const I18nContext = createContext<I18nValue>(defaultValue)

/** Current language, locale tag, translator and setters. Safe outside the provider (defaults to EN). */
export function useI18n(): I18nValue {
  return useContext(I18nContext)
}

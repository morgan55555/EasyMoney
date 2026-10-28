/**
 * Lightweight i18n (EN/RU): a context provider + a `t(key, params)` function
 * with `{{param}}` interpolation, plus Russian plural forms via
 * `Intl.PluralRules`. The choice is persisted in localStorage and applied to
 * <html lang>. Components call `useI18n()`; outside the provider (e.g. in
 * unit tests) the context defaults to English.
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

interface I18nValue {
  lang: Lang
  /** BCP-47 tag for Intl formatting: 'en-US' | 'ru-RU'. */
  locale: string
  t: TFunc
  setLang: (lang: Lang) => void
}

const STORAGE_KEY = 'easymoney.lang'

const defaultValue: I18nValue = {
  lang: 'en',
  locale: 'en-US',
  t: makeT(en),
  setLang: () => {},
}

function initialLang(): Lang {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === 'en' || stored === 'ru') return stored
  } catch {
    // localStorage unavailable (private mode / tests) — fall through.
  }
  return 'en'
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(initialLang)

  const setLang = useCallback((next: Lang) => {
    setLangState(next)
    try {
      localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // Non-fatal: the choice just won't persist.
    }
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
    }),
    [lang, setLang],
  )

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}

const I18nContext = createContext<I18nValue>(defaultValue)

/** Current language, locale tag, translator and setter. Safe outside the provider (defaults to EN). */
export function useI18n(): I18nValue {
  return useContext(I18nContext)
}

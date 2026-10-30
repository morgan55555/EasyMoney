/**
 * React-free i18n core (AGENTS.md §2.14): the pure translation helpers and the
 * locale dictionaries.
 *
 * Kept free of React so the service-worker build (`src/sw/sw.ts`, a separate
 * Vite build whose lib is `["ES2020", "WebWorker"]`) can import `makeDescribe`
 * from `lib/notificationText` and the dictionaries from here without pulling the
 * provider in. `i18n/index.tsx` re-exports this module and layers the React
 * context on top. `makeDescribe` + `invitedToken` + `notificationTarget` (in
 * `lib/notificationText`) are the single shared source of the one-line
 * localized summary that the in-app bell and the OS notification both render.
 */

import { en, ru, MEMBER_PLURAL, type Dict, type Lang } from './locales'

export type { Dict, Lang }
export { en, ru, MEMBER_PLURAL }

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

/**
 * Map a BCP-47 language tag to the app's UI language. Pure (unit-testable):
 * any Russian tag → 'ru', everything else (including empty/undefined) → 'en'.
 */
export function browserLang(tag: string | undefined | null): Lang {
  return tag && tag.toLowerCase().startsWith('ru') ? 'ru' : 'en'
}

function interpolate(template: string, params?: Record<string, string | number>): string {
  if (!params) return template
  return template.replace(/\{\{(\w+)\}\}/g, (match, name: string) =>
    name in params ? String(params[name]) : match,
  )
}

export function makeT(dict: Dict): TFunc {
  return (key, params) => interpolate(dict[key] ?? en[key] ?? key, params)
}

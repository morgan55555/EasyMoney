/**
 * i18n dictionaries (EN + RU), one file per language:
 *
 *   locales/en.ts — English dictionary
 *   locales/ru.ts  — Russian dictionary
 *
 * Flat, dotted keys: `t('auth.email')`. Values may contain `{{param}}`
 * placeholders (see `t` in i18n/index.tsx). The RU dictionary must cover
 * exactly the same keys as EN (checked by __tests__/locales.test.ts).
 */

export type Lang = 'en' | 'ru'

export type Dict = Record<string, string>

/** Plural word forms for "member" per language (used with `pluralForm`). */
export const MEMBER_PLURAL: Record<Lang, { one: string; few?: string; many?: string; other?: string }> = {
  en: { one: 'member', other: 'members' },
  ru: { one: 'участник', few: 'участника', many: 'участников' },
}

export { en } from './en'
export { ru } from './ru'

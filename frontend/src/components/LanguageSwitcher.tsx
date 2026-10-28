/**
 * Compact language switcher (EN/RU). Lives in the app header (Layout) and,
 * because it must be reachable before authentication, on the auth and invite
 * pages too.
 */

import { useI18n } from '../i18n'
import type { Lang } from '../i18n/locales'

export function LanguageSwitcher({ className = '' }: { className?: string }) {
  const { lang, setLang, t } = useI18n()

  return (
    <select
      value={lang}
      onChange={(e) => setLang(e.target.value as Lang)}
      aria-label="Language"
      className={`rounded-md border border-gray-300 bg-white px-2 py-1 text-xs font-medium text-gray-700 shadow-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 ${className}`}
    >
      <option value="en">{t('lang.en')}</option>
      <option value="ru">{t('lang.ru')}</option>
    </select>
  )
}

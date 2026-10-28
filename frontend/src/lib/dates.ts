/**
 * Small date helpers for display.
 *
 * All of them accept an optional locale tag (e.g. 'ru-RU'); without it the
 * browser default locale is used, so existing callers/tests are unaffected.
 */

const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/

/**
 * Parse an ISO string for *display*: date-only strings (`YYYY-MM-DD`) are
 * treated as a local date (the bare `new Date('YYYY-MM-DD')` is UTC midnight,
 * which renders a day early in timezones west of UTC); full timestamps are
 * real instants and are parsed normally.
 */
export function parseDateForDisplay(iso: string): Date {
  if (DATE_ONLY.test(iso)) {
    const [y, m, d] = iso.split('-').map(Number)
    return new Date(y, m - 1, d)
  }
  return new Date(iso)
}

/** Today's date as `YYYY-MM-DD` in the browser's local timezone. */
export function localTodayIso(): string {
  const d = new Date()
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${mm}-${dd}`
}

/** Russian plural form for counter nouns (minutes/hours/days). */
function ruPlural(n: number, one: string, few: string, many: string): string {
  const n10 = n % 10
  const n100 = n % 100
  if (n10 === 1 && n100 !== 11) return one
  if (n10 >= 2 && n10 <= 4 && (n100 < 12 || n100 > 14)) return few
  return many
}

export function relativeTime(iso: string, locale?: string): string {
  const then = new Date(iso)
  const now = new Date()
  const sec = Math.round((now.getTime() - then.getTime()) / 1000)
  const ru = locale === 'ru-RU'
  if (sec < 45) return ru ? 'только что' : 'just now'
  const min = Math.round(sec / 60)
  if (min < 60) {
    return ru ? `${min} ${ruPlural(min, 'минуту', 'минуты', 'минут')} назад` : `${min} min ago`
  }
  const hr = Math.round(min / 60)
  if (hr < 24) {
    return ru ? `${hr} ${ruPlural(hr, 'час', 'часа', 'часов')} назад` : `${hr} h ago`
  }
  const day = Math.round(hr / 24)
  if (day < 7) {
    return ru ? `${day} ${ruPlural(day, 'день', 'дня', 'дней')} назад` : `${day} d ago`
  }
  return then.toLocaleDateString(locale)
}

export function formatDateTime(iso: string, locale?: string): string {
  return new Date(iso).toLocaleString(locale)
}

export function formatDate(iso: string, locale?: string): string {
  return parseDateForDisplay(iso).toLocaleDateString(locale)
}

/**
 * Money display helpers (AGENTS.md §2.12).
 *
 * The API always sends money as strings with the currency's exact minor
 * digits, so no conversion is ever needed — these helpers only add locale
 * grouping and the currency code for display. They must never be used for
 * arithmetic (there is no arithmetic on money in the frontend).
 */

/** Currencies with zero minor digits. */
const ZERO_DECIMAL = new Set(['JPY', 'KRW', 'VND'])
/** Currencies with three minor digits. */
const THREE_DECIMAL = new Set(['KWD', 'BHD', 'OMR', 'TND'])

export function decimalDigits(currency: string): number {
  if (ZERO_DECIMAL.has(currency)) return 0
  if (THREE_DECIMAL.has(currency)) return 3
  return 2
}

const groupFormatter = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 })

/** Locale-specific grouping formatters, cached per locale tag. */
const localeFormatters = new Map<string, Intl.NumberFormat>()

function getFormatter(locale?: string): Intl.NumberFormat {
  if (!locale) return groupFormatter
  let formatter = localeFormatters.get(locale)
  if (!formatter) {
    formatter = new Intl.NumberFormat(locale, { maximumFractionDigits: 0 })
    localeFormatters.set(locale, formatter)
  }
  return formatter
}

/** Locale-specific decimal separators, cached per locale tag. */
const localeSeparators = new Map<string, string>()

function getDecimalSeparator(locale: string): string {
  let sep = localeSeparators.get(locale)
  if (!sep) {
    const parts = new Intl.NumberFormat(locale).formatToParts(0.1)
    sep = parts.find((p) => p.type === 'decimal')?.value ?? '.'
    localeSeparators.set(locale, sep)
  }
  return sep
}

/**
 * Format an API money string for display: locale digit grouping + currency
 * code, preserving the exact minor digits the server sent. Pass an i18n
 * locale tag (e.g. 'ru-RU') for locale-specific digit grouping; without it
 * the browser default is used.
 *
 * `formatMoney("33.34", "EUR")` -> "33.34 EUR"
 * `formatMoney("1000", "JPY")`   -> "1,000 JPY"
 * `formatMoney("1234.567", "KWD")` -> "1,234.567 KWD"
 */
export function formatMoney(value: string | number, currency: string, locale?: string): string {
  const digits = decimalDigits(currency)
  const s = typeof value === 'number' ? value.toFixed(digits) : value
  const negative = s.startsWith('-')
  const body = negative ? s.slice(1) : s
  const [intPart, decPart] = body.split('.')
  // NUMERIC(14,3) keeps integer parts far below Number.MAX_SAFE_INTEGER.
  const grouped = getFormatter(locale).format(Number.parseInt(intPart, 10) || 0)
  const dec = decPart ? `${locale ? getDecimalSeparator(locale) : '.'}${decPart}` : ''
  return `${negative ? '-' : ''}${grouped}${dec} ${currency}`
}

/**
 * Signed variant for balance chips: "+33.34 EUR" / "-12.00 EUR" / "0.00 EUR".
 * Positive values are credits (you are owed); negative are debts (you owe).
 */
export function formatSigned(value: string | number, currency: string, locale?: string): string {
  const s = typeof value === 'number' ? value.toFixed(decimalDigits(currency)) : value
  if (s.startsWith('+') || s.startsWith('-')) return formatMoney(s, currency, locale)
  return `+${formatMoney(s, currency, locale)}`
}

/** Parse an API money string to integer minor units (display/preview math only). */
export function toMinorUnits(value: string, currency: string): number {
  const digits = decimalDigits(currency)
  const s = value.trim()
  const negative = s.startsWith('-')
  const body = negative ? s.slice(1) : s
  const [intPart, decPart = ''] = body.split('.')
  const padded = (decPart + '0'.repeat(digits)).slice(0, digits)
  const whole = Number.parseInt(intPart, 10) || 0
  const frac = padded === '' ? 0 : Number.parseInt(padded, 10)
  const minor = whole * 10 ** digits + frac
  return negative ? -minor : minor
}

/** Format integer minor units back to a money string with the currency's digits. */
export function fromMinorUnits(minor: number, currency: string): string {
  const digits = decimalDigits(currency)
  const negative = minor < 0
  const abs = Math.abs(minor)
  const whole = Math.floor(abs / 10 ** digits)
  const frac = abs % 10 ** digits
  const fracStr = digits > 0 ? '.' + String(frac).padStart(digits, '0') : ''
  return `${negative ? '-' : ''}${whole}${fracStr}`
}

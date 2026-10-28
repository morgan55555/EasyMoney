import { describe, expect, it } from 'vitest'

import { formatDate, localTodayIso, parseDateForDisplay } from '../dates'

describe('parseDateForDisplay', () => {
  it('parses date-only strings as a local date, not UTC midnight', () => {
    const d = parseDateForDisplay('2026-10-26')
    expect(d.getFullYear()).toBe(2026)
    expect(d.getMonth()).toBe(9)
    expect(d.getDate()).toBe(26)
  })

  it('parses full timestamps as real instants', () => {
    expect(parseDateForDisplay('2026-10-26T12:00:00Z').toISOString()).toBe('2026-10-26T12:00:00.000Z')
  })
})

describe('formatDate', () => {
  it('renders date-only strings without an off-by-one day (any browser timezone)', () => {
    // Local parse -> local format is stable regardless of the runtime TZ.
    expect(formatDate('2026-10-26', 'en-US')).toBe('10/26/2026')
    expect(formatDate('2026-10-26', 'ru-RU')).toBe('26.10.2026')
  })

  it('renders full timestamps for the same local date consistently', () => {
    const instant = new Date(2026, 9, 26, 15, 30) // local
    expect(formatDate(instant.toISOString(), 'en-US')).toBe(formatDate('2026-10-26', 'en-US'))
  })
})

describe('localTodayIso', () => {
  it('returns today in YYYY-MM-DD in the local timezone', () => {
    const s = localTodayIso()
    expect(s).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    const d = new Date()
    expect(s).toBe(
      `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`,
    )
  })
})

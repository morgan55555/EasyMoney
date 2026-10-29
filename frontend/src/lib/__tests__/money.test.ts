import { describe, expect, it } from 'vitest'

import { decimalDigits, formatMoney, formatSigned, fromMinorUnits, toMinorUnits } from '../money'

describe('decimalDigits', () => {
  it('uses zero minor digits for JPY/KRW/VND', () => {
    expect(decimalDigits('JPY')).toBe(0)
    expect(decimalDigits('KRW')).toBe(0)
    expect(decimalDigits('VND')).toBe(0)
  })

  it('uses three minor digits for KWD/BHD/OMR/TND', () => {
    expect(decimalDigits('KWD')).toBe(3)
    expect(decimalDigits('BHD')).toBe(3)
    expect(decimalDigits('OMR')).toBe(3)
    expect(decimalDigits('TND')).toBe(3)
  })

  it('defaults to two minor digits', () => {
    expect(decimalDigits('EUR')).toBe(2)
    expect(decimalDigits('USD')).toBe(2)
  })
})

describe('formatMoney', () => {
  it('keeps the exact minor digits and appends the code', () => {
    expect(formatMoney('33.34', 'EUR')).toBe('33.34 EUR')
  })

  it('adds locale grouping for whole units (JPY)', () => {
    expect(formatMoney('1000', 'JPY')).toBe('1,000 JPY')
  })

  it('keeps three decimals for KWD', () => {
    expect(formatMoney('1234.567', 'KWD')).toBe('1,234.567 KWD')
  })

  it('preserves the negative sign', () => {
    expect(formatMoney('-12.00', 'USD')).toBe('-12.00 USD')
  })

  it('groups large integer parts', () => {
    expect(formatMoney('1234567.89', 'EUR')).toBe('1,234,567.89 EUR')
  })
})

describe('formatMoney with locale', () => {
  it('uses the Russian decimal separator', () => {
    expect(formatMoney('90.00', 'USD', 'ru-RU')).toBe('90,00 USD')
  })

  it('uses Russian grouping (narrow no-break space) and separator', () => {
    // ru-RU groups with U+00A0; normalize to a plain space for a stable assertion.
    expect(formatMoney('1234567.89', 'EUR', 'ru-RU').replace(/\u00a0/g, ' ')).toBe('1 234 567,89 EUR')
  })

  it('keeps the no-locale output unchanged', () => {
    expect(formatMoney('1234567.89', 'EUR')).toBe('1,234,567.89 EUR')
  })
})

describe('formatSigned', () => {
  it('prefixes positives with +', () => {
    expect(formatSigned('33.34', 'EUR')).toBe('+33.34 EUR')
  })

  it('keeps negatives as-is', () => {
    expect(formatSigned('-12.00', 'USD')).toBe('-12.00 USD')
  })

  it('passes through an explicit + or -', () => {
    expect(formatSigned('-5.00', 'EUR')).toBe('-5.00 EUR')
  })
})

describe('toMinorUnits / fromMinorUnits round-trip', () => {
  it.each([
    ['33.34', 'EUR', 3334],
    ['1000', 'JPY', 1000],
    ['1234.567', 'KWD', 1234567],
    ['-12.00', 'USD', -1200],
    ['0.01', 'EUR', 1],
  ])('toMinorUnits(%s, %s) = %i', (value, currency, expected) => {
    expect(toMinorUnits(value, currency)).toBe(expected)
  })

  it.each([
    ['1e2', 'USD'],
    ['1.5e-1', 'EUR'],
    ['1e-2', 'JPY'],
    ['1.2.3', 'USD'],
    ['12abc', 'USD'],
    ['', 'USD'],
    ['.', 'USD'],
    ['-', 'USD'],
    ['+1.00', 'USD'],
  ])('toMinorUnits(%s, %s) = null for non-plain-decimal input', (value, currency) => {
    expect(toMinorUnits(value, currency)).toBeNull()
  })

  it.each([
    [3334, 'EUR', '33.34'],
    [1000, 'JPY', '1000'],
    [1234567, 'KWD', '1234.567'],
    [-1200, 'USD', '-12.00'],
    [1, 'EUR', '0.01'],
  ])('fromMinorUnits(%i, %s) = %s', (minor, currency, expected) => {
    expect(fromMinorUnits(minor, currency)).toBe(expected)
  })

  it('round-trips without loss', () => {
    const cases: Array<[string, string]> = [
      ['33.34', 'EUR'],
      ['1000', 'JPY'],
      ['1234.567', 'KWD'],
      ['0.01', 'USD'],
    ]
    for (const [value, currency] of cases) {
      const minor = toMinorUnits(value, currency)
      if (minor === null) throw new Error(`unexpected null for ${value}`)
      expect(fromMinorUnits(minor, currency)).toBe(value)
    }
  })
})

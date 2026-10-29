import { describe, expect, it } from 'vitest'

import { toMinorUnits } from '../money'
import { previewSplit, sumExactShares } from '../split'

function sumMinor(shares: Array<{ share: string }>, currency: string): number {
  return shares.reduce((acc, s) => acc + toMinorUnits(s.share, currency), 0)
}

describe('previewSplit — equal', () => {
  it('splits 100.00 among 3 as 33.34/33.33/33.33 (remainder to earliest)', () => {
    const res = previewSplit({
      amount: '100.00',
      currency: 'EUR',
      method: 'equal',
      participants: [
        { user_id: 1 },
        { user_id: 2 },
        { user_id: 3 },
      ],
    })
    expect(Array.isArray(res)).toBe(true)
    if (!Array.isArray(res)) return
    expect(res.map((r) => r.share)).toEqual(['33.34', '33.33', '33.33'])
    expect(sumMinor(res, 'EUR')).toBe(10000)
  })

  it('assigns the leftover unit to the first participant in join order', () => {
    const res = previewSplit({
      amount: '10.01',
      currency: 'EUR',
      method: 'equal',
      participants: [
        { user_id: 7 },
        { user_id: 8 },
        { user_id: 9 },
      ],
    })
    if (!Array.isArray(res)) throw new Error('expected rows')
    // user 7 is first (earliest), so it gets the extra unit.
    expect(res[0].share).toBe('3.34')
    expect(res[1].share).toBe('3.34')
    expect(res[2].share).toBe('3.33')
    expect(sumMinor(res, 'EUR')).toBe(1001)
  })

  it('handles whole amounts exactly', () => {
    const res = previewSplit({
      amount: '90.00',
      currency: 'USD',
      method: 'equal',
      participants: [
        { user_id: 1 },
        { user_id: 2 },
        { user_id: 3 },
      ],
    })
    if (!Array.isArray(res)) throw new Error('expected rows')
    expect(res.map((r) => r.share)).toEqual(['30.00', '30.00', '30.00'])
  })
})

describe('previewSplit — UI parity with the server rule (AGENTS.md §2.1)', () => {
  it('zero-digit currency (JPY) still assigns the remainder to the earliest joiner', () => {
    // 100 JPY / 3 → base 33, r = 1 → the first participant gets 34.
    const res = previewSplit({
      amount: '100',
      currency: 'JPY',
      method: 'equal',
      participants: [
        { user_id: 1 },
        { user_id: 2 },
        { user_id: 3 },
      ],
    })
    if (!Array.isArray(res)) throw new Error('expected rows')
    expect(res.map((r) => r.share)).toEqual(['34', '33', '33'])
    // Zero-digit currency: no decimal point in any share, sum exact.
    for (const row of res) expect(row.share).not.toContain('.')
    expect(sumMinor(res, 'JPY')).toBe(100)
  })

  it('the remainder follows join order, not user_id order (equal split)', () => {
    // The list order IS the join order. Here the earliest joiner has the HIGHER
    // id, so an id-ordered rule would put the extra unit on user 1 — wrong.
    const res = previewSplit({
      amount: '10.01',
      currency: 'EUR',
      method: 'equal',
      participants: [
        { user_id: 3 }, // joined first
        { user_id: 1 }, // joined second
      ],
    })
    if (!Array.isArray(res)) throw new Error('expected rows')
    expect(res.map((r) => r.share)).toEqual(['5.01', '5.00'])
    expect(sumMinor(res, 'EUR')).toBe(1001)
  })

  it('weighted tie-break goes to the earliest joiner (list order), not the lowest id', () => {
    // Equal weights → equal fractional parts → tie. The two earliest joiners
    // (first two in the list) get the +1 unit even though their ids are higher.
    const res = previewSplit({
      amount: '10.01',
      currency: 'EUR',
      method: 'weighted',
      participants: [
        { user_id: 9, weight: '1' }, // joined first
        { user_id: 2, weight: '1' }, // joined second
        { user_id: 4, weight: '1' }, // joined third
      ],
    })
    if (!Array.isArray(res)) throw new Error('expected rows')
    expect(res.map((r) => r.share)).toEqual(['3.34', '3.34', '3.33'])
    expect(sumMinor(res, 'EUR')).toBe(1001)
  })
})

describe('previewSplit — weighted (largest remainder)', () => {
  it('matches the AGENTS.md example: 10.01 with weights [1,1,1]', () => {
    const res = previewSplit({
      amount: '10.01',
      currency: 'EUR',
      method: 'weighted',
      participants: [
        { user_id: 1, weight: '1' },
        { user_id: 2, weight: '1' },
        { user_id: 3, weight: '1' },
      ],
    })
    if (!Array.isArray(res)) throw new Error('expected rows')
    expect(res.map((r) => r.share)).toEqual(['3.34', '3.34', '3.33'])
    expect(sumMinor(res, 'EUR')).toBe(1001)
  })

  it('applies weights [1,2,3] on 100.00 as 16.67/33.33/50.00', () => {
    const res = previewSplit({
      amount: '100.00',
      currency: 'EUR',
      method: 'weighted',
      participants: [
        { user_id: 1, weight: '1' },
        { user_id: 2, weight: '2' },
        { user_id: 3, weight: '3' },
      ],
    })
    if (!Array.isArray(res)) throw new Error('expected rows')
    expect(res.map((r) => r.share)).toEqual(['16.67', '33.33', '50.00'])
    expect(sumMinor(res, 'EUR')).toBe(10000)
  })

  it('never loses or creates a unit across random-ish weight sets', () => {
    const sets: Array<[string, string[]]> = [
      ['100.00', ['1', '1', '1', '1']],
      ['10.00', ['1', '2']],
      ['33.33', ['3', '5', '2']],
      ['1.00', ['1', '1', '1', '1', '1']],
    ]
    for (const [amount, weights] of sets) {
      const res = previewSplit({
        amount,
        currency: 'EUR',
        method: 'weighted',
        participants: weights.map((w, i) => ({ user_id: i + 1, weight: w })),
      })
      if (!Array.isArray(res)) throw new Error('expected rows')
      expect(sumMinor(res, 'EUR')).toBe(toMinorUnits(amount, 'EUR'))
    }
  })
})

describe('previewSplit — exact', () => {
  it('passes the supplied shares through unchanged', () => {
    const res = previewSplit({
      amount: '30.00',
      currency: 'EUR',
      method: 'exact',
      participants: [
        { user_id: 1, amount: '10.00' },
        { user_id: 2, amount: '20.00' },
      ],
    })
    if (!Array.isArray(res)) throw new Error('expected rows')
    expect(res.map((r) => r.share)).toEqual(['10.00', '20.00'])
  })
})

describe('previewSplit — validation', () => {
  it('rejects a zero or negative amount', () => {
    expect(previewSplit({ amount: '0.00', currency: 'EUR', method: 'equal', participants: [{ user_id: 1 }] }))
      .toEqual({ error: 'Enter an amount greater than zero.' })
  })

  it('rejects no participants', () => {
    const res = previewSplit({ amount: '10.00', currency: 'EUR', method: 'equal', participants: [] })
    expect(res).toEqual({ error: 'Pick at least one participant.' })
  })

  it('rejects more decimal places than the currency supports', () => {
    const res = previewSplit({ amount: '10.999', currency: 'JPY', method: 'equal', participants: [{ user_id: 1 }] })
    expect(res).toMatchObject({ error: expect.stringContaining('decimal place') })
  })

  it('rejects non-positive weights', () => {
    const res = previewSplit({
      amount: '10.00',
      currency: 'EUR',
      method: 'weighted',
      participants: [
        { user_id: 1, weight: '0' },
        { user_id: 2, weight: '1' },
      ],
    })
    expect(res).toEqual({ error: 'Weights must be positive numbers.' })
  })
})

describe('sumExactShares', () => {
  it('sums the exact shares', () => {
    expect(sumExactShares([{ user_id: 1, amount: '10.00' }, { user_id: 2, amount: '20.00' }], 'EUR')).toBe('30.00')
  })

  it('returns null when an input is missing or blank', () => {
    expect(sumExactShares([{ user_id: 1, amount: '10.00' }, { user_id: 2, amount: '' }], 'EUR')).toBeNull()
  })
})

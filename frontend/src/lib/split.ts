/**
 * Client-side split preview (AGENTS.md §2.1).
 *
 * Pure integer math on minor units, mirroring the server's exact rules so the
 * form can show a live preview. The server always recomputes and is the only
 * writer of the stored shares — this preview is advisory only.
 */

import { decimalDigits, fromMinorUnits, toMinorUnits } from './money'

export interface SplitPreviewInput {
  amount: string
  currency: string
  method: 'equal' | 'weighted' | 'exact'
  /**
   * Participants in the deterministic remainder order: joined_at ascending,
   * tie-break user_id ascending (the server's ordering, §2.1).
   */
  participants: Array<{ user_id: number; weight?: string | null; amount?: string | null }>
}

export type SplitPreview = SplitPreviewRow[] | { error: string }

export interface SplitPreviewRow {
  user_id: number
  share: string
}

function decimalPlaces(s: string): number {
  const i = s.indexOf('.')
  return i < 0 ? 0 : s.length - i - 1
}

/** Weights are scale-free; parse at 6 fixed decimals for the ratio math. */
function weightToMinor(w: string): number {
  const t = w.trim()
  if (t === '') return 1_000_000
  const negative = t.startsWith('-')
  const body = negative ? t.slice(1) : t
  const [intPart, decPart = ''] = body.split('.')
  const padded = (decPart + '000000').slice(0, 6)
  const value = (Number.parseInt(intPart, 10) || 0) * 10 ** 6 + (padded ? Number.parseInt(padded, 10) : 0)
  return negative ? -value : value
}

export function previewSplit(input: SplitPreviewInput): SplitPreview {
  const { amount, currency, method, participants } = input
  const digits = decimalDigits(currency)
  const t = amount.trim()
  if (t === '' || Number.isNaN(Number(t))) return { error: 'Enter an amount.' }
  if (decimalPlaces(t) > digits) {
    return { error: `${currency} has ${digits} decimal place(s); the amount has too many.` }
  }
  const a = toMinorUnits(t, currency)
  if (a <= 0) return { error: 'Enter an amount greater than zero.' }
  if (participants.length === 0) return { error: 'Pick at least one participant.' }

  if (method === 'equal') {
    // base = floor(A / n); the r leftover minor units go one each to the r
    // earliest-joined participants (the list order is that order).
    const n = participants.length
    const base = Math.floor(a / n)
    const r = a - n * base
    return participants.map((p, i) => ({
      user_id: p.user_id,
      share: fromMinorUnits(base + (i < r ? 1 : 0), currency),
    }))
  }

  if (method === 'weighted') {
    // Largest-remainder: floor each raw share, then the leftover units go to
    // the largest fractional parts, ties broken by participant order.
    const weights = participants.map((p) => weightToMinor(p.weight ?? '1'))
    if (weights.some((w) => w <= 0 || Number.isNaN(w))) {
      return { error: 'Weights must be positive numbers.' }
    }
    const totalWeight = weights.reduce((sum, w) => sum + w, 0)
    const rows = participants.map((p, i) => {
      const raw = (a * weights[i]) / totalWeight
      const base = Math.floor(raw)
      return { user_id: p.user_id, base, frac: raw - base }
    })
    const leftover = a - rows.reduce((sum, row) => sum + row.base, 0)
    const get = rows
      .map((row, index) => ({ index, frac: row.frac }))
      .sort((x, y) => y.frac - x.frac || x.index - y.index)
      .slice(0, leftover)
      .map((o) => o.index)
    return rows.map((row, index) => ({
      user_id: row.user_id,
      share: fromMinorUnits(row.base + (get.includes(index) ? 1 : 0), currency),
    }))
  }

  // exact: the client supplies the shares; the server validates the sum (422).
  return participants.map((p) => ({
    user_id: p.user_id,
    share: p.amount ?? '0',
  }))
}

/** Sum of the exact-split inputs, as a money string (to compare against A). */
export function sumExactShares(
  shares: Array<{ user_id: number; amount?: string | null }>,
  currency: string,
): string | null {
  let total = 0
  for (const s of shares) {
    const t = (s.amount ?? '').trim()
    if (t === '' || Number.isNaN(Number(t))) return null
    total += toMinorUnits(t, currency)
  }
  return fromMinorUnits(total, currency)
}

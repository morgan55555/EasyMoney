/**
 * List filters (expenses / members / groups).
 *
 * Matching is a substring test that is case-insensitive and diacritic-blind
 * (ё ≡ е, ü ≡ u), so "Жилье" finds "жильё" and "FOOD" finds "food". The
 * comparison is done on a normalized form; no parsing of money or dates.
 */

/**
 * Fold a string to its match form: lowercase, drop combining marks, and fold
 * ё/Ё to е (Unicode NFD does not decompose ё — it is a full letter, not a
 * composed е + diaeresis). Everything else (ü, à, ö, …) decomposes under NFD.
 */
export function norm(s: string): string {
  return s
    .replace(/ё/gi, 'е')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLocaleLowerCase()
}

/**
 * True when every part of the query (split on whitespace) is found in at least
 * one of the given fields, in any order. An empty/blank query matches
 * everything. Null/undefined fields are simply skipped.
 */
export function matchesAny(query: string, ...fields: (string | null | undefined)[]): boolean {
  const parts = norm(query).split(/\s+/).filter(Boolean)
  if (parts.length === 0) return true
  const folded = fields.filter((f): f is string => f != null && f.length > 0).map(norm)
  return parts.every((part) => folded.some((field) => field.includes(part)))
}

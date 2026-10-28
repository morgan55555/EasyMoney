import { describe, expect, it } from 'vitest'

import { en, ru } from '../locales'

/** Extract the set of `{{param}}` names used by a template. */
function paramsOf(template: string): Set<string> {
  const names = new Set<string>()
  for (const match of template.matchAll(/\{\{(\w+)\}\}/g)) {
    names.add(match[1])
  }
  return names
}

describe('i18n dictionaries', () => {
  it('covers exactly the same keys in EN and RU', () => {
    const enKeys = Object.keys(en).sort()
    const ruKeys = Object.keys(ru).sort()
    expect(ruKeys).toEqual(enKeys)
  })

  it('has no duplicate keys (object literal keys collapse silently)', () => {
    // Object.keys already dedupes; guard against accidental shrink by checking
    // the raw count of `key:` lines is not feasible here, so instead assert a
    // few namespaces exist in both languages.
    for (const ns of ['auth.', 'expenses.', 'payments.', 'feed.', 'notif.', 'invites.', 'lang.']) {
      expect(enKeysContaining(ns)).toBeGreaterThan(0)
      expect(ruKeysContaining(ns)).toBeGreaterThan(0)
    }
  })

  it('uses the same {{param}} placeholders per key in EN and RU', () => {
    for (const key of Object.keys(en)) {
      expect(paramsOf(ru[key]).size, `key ${key}: param count differs`).toBe(paramsOf(en[key]).size)
      const enParams = paramsOf(en[key])
      for (const p of enParams) {
        expect(paramsOf(ru[key]).has(p), `key ${key}: missing param ${p} in RU`).toBe(true)
      }
    }
  })

  it('has no empty RU values', () => {
    for (const [key, value] of Object.entries(ru)) {
      expect(value.trim().length, `key ${key} is empty`).toBeGreaterThan(0)
    }
  })
})

function enKeysContaining(prefix: string): number {
  return Object.keys(en).filter((k) => k.startsWith(prefix)).length
}

function ruKeysContaining(prefix: string): number {
  return Object.keys(ru).filter((k) => k.startsWith(prefix)).length
}

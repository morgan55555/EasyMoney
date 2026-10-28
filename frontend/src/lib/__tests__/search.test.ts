import { describe, expect, it } from 'vitest'

import { matchesAny, norm } from '../search'

describe('norm', () => {
  it('lowercases', () => {
    expect(norm('FOOD')).toBe('food')
  })

  it('folds ё/Ё to е (NFD alone does not)', () => {
    expect(norm('жильё')).toBe('жилье')
    expect(norm('счёт')).toBe('счет')
    expect(norm('Ёлка')).toBe('елка')
  })
})

describe('matchesAny', () => {
  it('an empty or blank query matches everything', () => {
    expect(matchesAny('', 'anything')).toBe(true)
    expect(matchesAny('   ', null)).toBe(true)
  })

  it('is case-insensitive', () => {
    expect(matchesAny('Smoke', 'Smoke dinner')).toBe(true)
    expect(matchesAny('smoke dinner', 'Smoke Dinner')).toBe(true)
    expect(matchesAny('SMOKE', 'no match here')).toBe(false)
  })

  it('is a substring test: every part must appear, in any order', () => {
    expect(matchesAny('oke', 'Smoke dinner')).toBe(true)
    expect(matchesAny('dinner smoke', 'Smoke dinner')).toBe(true)
    expect(matchesAny('dinner breakfast', 'Smoke dinner')).toBe(false)
  })

  it('is diacritic-blind both ways', () => {
    expect(matchesAny('счет', 'Счёт')).toBe(true)
    expect(matchesAny('счёт', 'счет')).toBe(true)
    expect(matchesAny('jилье', 'жильё')).toBe(false)
  })

  it('checks every whitespace-separated part against any field', () => {
    expect(matchesAny('frank food', 'Food order', 'Frank paid')).toBe(true)
    expect(matchesAny('frank food', 'Food order', 'Grace paid')).toBe(false)
    // Order of the parts does not matter.
    expect(matchesAny('food frank', 'Frank paid', 'Food order')).toBe(true)
  })

  it('skips null/undefined fields and matches the rest', () => {
    expect(matchesAny('note', null, undefined, 'a note')).toBe(true)
    expect(matchesAny('note', null, undefined, '')).toBe(false)
  })
})

/**
 * AGENTS.md §2.13 / req 27: UI language vs. preferred language.
 *
 * Pins the resolution order the provider implements: a stored manual choice
 * wins, else the server's `preferred_language` (adopted by `applyPreferred`
 * *without* marking manual, so a later manual choice still wins), else `en`.
 * `setLang` (the header switcher) always marks the choice manual and never
 * touches the server's `preferred_language`. Also covers Russian plural forms
 * (`Intl.PluralRules`) and `{{param}}` interpolation.
 */

import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { I18nProvider, pluralForm, useI18n, type I18nValue } from '../index'
import { ru } from '../locales'

// React 18: tell the runtime we are in a test environment so `act` is allowed.
;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const STORAGE_KEY = 'easymoney.lang'

let captured: I18nValue | undefined
let root: Root | null = null

function Probe() {
  captured = useI18n()
  return null
}

function renderProvider(): void {
  const el = document.createElement('div')
  document.body.appendChild(el)
  const r = createRoot(el)
  root = r
  act(() => {
    r.render(createElement(I18nProvider, null, createElement(Probe)))
  })
}

beforeEach(() => {
  localStorage.removeItem(STORAGE_KEY)
})

afterEach(() => {
  const r = root
  if (r) act(() => r.unmount())
  root = null
  captured = undefined
})

describe('pluralForm', () => {
  const forms = { one: 'one', few: 'few', many: 'many', other: 'other' }

  it('EN: 1 → one, anything else → other', () => {
    expect(pluralForm('en', 1, forms)).toBe('one')
    expect(pluralForm('en', 2, forms)).toBe('other')
    expect(pluralForm('en', 0, forms)).toBe('other')
  })

  it('RU: one/few/many per ICU rules (21 → one, 11 → many, 0 → many)', () => {
    expect(pluralForm('ru', 1, forms)).toBe('one')
    expect(pluralForm('ru', 2, forms)).toBe('few')
    expect(pluralForm('ru', 4, forms)).toBe('few')
    expect(pluralForm('ru', 5, forms)).toBe('many')
    expect(pluralForm('ru', 11, forms)).toBe('many')
    expect(pluralForm('ru', 0, forms)).toBe('many')
    expect(pluralForm('ru', 21, forms)).toBe('one')
  })

  it('falls back sensibly when a form is missing (RU dicts omit `other`)', () => {
    const ruLike = { one: 'один', few: 'несколько', many: 'много' }
    expect(pluralForm('ru', 1, ruLike)).toBe('один')
    expect(pluralForm('ru', 3, ruLike)).toBe('несколько')
    expect(pluralForm('ru', 25, ruLike)).toBe('много')
  })
})

describe('I18nProvider: manual vs. preferred', () => {
  it('defaults to EN when nothing is stored', () => {
    renderProvider()
    expect(captured?.lang).toBe('en')
    expect(captured?.manual).toBe(false)
    expect(captured?.locale).toBe('en-US')
  })

  it('treats a legacy bare stored value as a manual choice', () => {
    localStorage.setItem(STORAGE_KEY, 'ru')
    renderProvider()
    expect(captured?.lang).toBe('ru')
    expect(captured?.manual).toBe(true)
    expect(captured?.t('lang.ru')).toBe(ru['lang.ru'])
  })

  it('setLang marks the choice manual and persists it', () => {
    renderProvider()
    act(() => captured?.setLang('ru'))
    expect(captured?.lang).toBe('ru')
    expect(captured?.manual).toBe(true)
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '')).toEqual({ lang: 'ru', manual: true })
  })

  it('applyPreferred adopts the language without marking it manual', () => {
    renderProvider()
    act(() => captured?.applyPreferred('ru'))
    expect(captured?.lang).toBe('ru')
    expect(captured?.manual).toBe(false)
    expect(captured?.locale).toBe('ru-RU')
    expect(captured?.t('lang.ru')).toBe(ru['lang.ru'])
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '')).toEqual({ lang: 'ru', manual: false })
  })

  it('applyPreferred never overwrites a manual choice', () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ lang: 'ru', manual: true }))
    renderProvider()
    act(() => captured?.applyPreferred('en'))
    expect(captured?.lang).toBe('ru')
    expect(captured?.manual).toBe(true)
  })

  it('applyPreferred with the current language is a no-op (no write)', () => {
    renderProvider()
    const before = captured
    act(() => before?.applyPreferred('en'))
    expect(captured).toBe(before)
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull()
  })

  it('a manual choice made after applyPreferred wins over later applies', () => {
    renderProvider()
    act(() => captured?.applyPreferred('ru'))
    act(() => captured?.setLang('en'))
    act(() => captured?.applyPreferred('ru'))
    expect(captured?.lang).toBe('en')
    expect(captured?.manual).toBe(true)
  })

  it('sets <html lang> and interpolates {{params}}', () => {
    renderProvider()
    act(() => captured?.setLang('ru'))
    expect(document.documentElement.lang).toBe('ru')
    expect(captured?.t('common.userById', { id: 7 })).toBe(ru['common.userById'].replace('{{id}}', '7'))
  })

  it('a missing key falls back to the key itself', () => {
    renderProvider()
    expect(captured?.t('does.not.exist')).toBe('does.not.exist')
  })
})

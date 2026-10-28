/**
 * AGENTS.md §2.13 / req 27: UI language vs. preferred language.
 *
 * Pins the provider semantics:
 *  - `browserLang` maps a BCP-47 tag to a UI language (pure, unit-tested);
 *  - initial language on load: stored choice → else browser language → else 'en';
 *  - `setLang` (the public-page selector) sets the state AND persists it
 *    (plain `'en'`/`'ru'` under `easymoney.lang`);
 *  - `applyPreferred` (server-driven from `me.preferred_language` inside the
 *    app) updates the state only — it never writes to localStorage and always
 *    wins over the stored choice, so every account's preference takes effect.
 * Also covers Russian plural forms (`Intl.PluralRules`) and `{{param}}`
 * interpolation.
 */

import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { I18nProvider, pluralForm, browserLang, useI18n, type I18nValue } from '../index'
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

function setBrowserLang(tag: string): void {
  Object.defineProperty(navigator, 'language', { value: tag, configurable: true })
}

beforeEach(() => {
  localStorage.removeItem(STORAGE_KEY)
  delete (navigator as unknown as { language?: string }).language
})

afterEach(() => {
  const r = root
  if (r) act(() => r.unmount())
  root = null
  captured = undefined
  delete (navigator as unknown as { language?: string }).language
})

describe('browserLang', () => {
  it('maps any Russian tag to ru', () => {
    expect(browserLang('ru')).toBe('ru')
    expect(browserLang('ru-RU')).toBe('ru')
    expect(browserLang('ru_KZ')).toBe('ru')
    expect(browserLang('RU-ru')).toBe('ru')
  })

  it('maps everything else (incl. empty/undefined) to en', () => {
    expect(browserLang('en')).toBe('en')
    expect(browserLang('en-US')).toBe('en')
    expect(browserLang('de-DE')).toBe('en')
    expect(browserLang('')).toBe('en')
    expect(browserLang(undefined)).toBe('en')
    expect(browserLang(null)).toBe('en')
  })
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

describe('I18nProvider: initial language', () => {
  it('uses the browser language when nothing is stored (ru browser)', () => {
    setBrowserLang('ru-RU')
    renderProvider()
    expect(captured?.lang).toBe('ru')
    expect(captured?.locale).toBe('ru-RU')
    expect(captured?.t('lang.ru')).toBe(ru['lang.ru'])
  })

  it('uses the browser language when nothing is stored (en browser)', () => {
    setBrowserLang('en-US')
    renderProvider()
    expect(captured?.lang).toBe('en')
    expect(captured?.locale).toBe('en-US')
  })

  it('falls back to en when the browser tag is not Russian and nothing is stored', () => {
    setBrowserLang('de-DE')
    renderProvider()
    expect(captured?.lang).toBe('en')
  })

  it('a stored choice wins over the browser language', () => {
    setBrowserLang('ru-RU')
    localStorage.setItem(STORAGE_KEY, 'en')
    renderProvider()
    expect(captured?.lang).toBe('en')
  })

  it('still parses the legacy JSON storage format', () => {
    setBrowserLang('en-US')
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ lang: 'ru', manual: true }))
    renderProvider()
    expect(captured?.lang).toBe('ru')
  })

  it('corrupt storage falls back to the browser language', () => {
    setBrowserLang('ru-RU')
    localStorage.setItem(STORAGE_KEY, '{not json')
    renderProvider()
    expect(captured?.lang).toBe('ru')
  })
})

describe('I18nProvider: setLang vs. applyPreferred', () => {
  it('setLang sets the state and persists the plain value', () => {
    setBrowserLang('en-US')
    renderProvider()
    act(() => captured?.setLang('ru'))
    expect(captured?.lang).toBe('ru')
    expect(localStorage.getItem(STORAGE_KEY)).toBe('ru')
  })

  it('applyPreferred updates the state without touching storage', () => {
    setBrowserLang('en-US')
    renderProvider()
    act(() => captured?.applyPreferred('ru'))
    expect(captured?.lang).toBe('ru')
    expect(captured?.locale).toBe('ru-RU')
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull()
  })

  it('applyPreferred wins over a stored choice (per-account preference)', () => {
    setBrowserLang('ru-RU')
    localStorage.setItem(STORAGE_KEY, 'ru')
    renderProvider()
    act(() => captured?.applyPreferred('en'))
    expect(captured?.lang).toBe('en')
    // The stored public-page choice is left intact for the next visit.
    expect(localStorage.getItem(STORAGE_KEY)).toBe('ru')
  })

  it('setLang persists again after applyPreferred (no stale storage clobber)', () => {
    setBrowserLang('en-US')
    renderProvider()
    act(() => captured?.applyPreferred('ru'))
    act(() => captured?.setLang('en'))
    expect(captured?.lang).toBe('en')
    expect(localStorage.getItem(STORAGE_KEY)).toBe('en')
  })

  it('sets <html lang> and interpolates {{params}}', () => {
    setBrowserLang('en-US')
    renderProvider()
    act(() => captured?.applyPreferred('ru'))
    expect(document.documentElement.lang).toBe('ru')
    expect(captured?.t('common.userById', { id: 7 })).toBe(ru['common.userById'].replace('{{id}}', '7'))
  })

  it('a missing key falls back to the key itself', () => {
    setBrowserLang('en-US')
    renderProvider()
    expect(captured?.t('does.not.exist')).toBe('does.not.exist')
  })
})

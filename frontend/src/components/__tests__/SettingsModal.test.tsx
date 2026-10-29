/**
 * SettingsModal (AGENTS.md §2.13, §3.6, §6.2): profile (name/language), the
 * email-change flow (confirmation-pending notice vs. immediate "Email
 * changed."), the password-change flow (8-char minimum, result message) and
 * the theme segmented control (aria-pressed + persistence to localStorage).
 * Network is stubbed per request (client.ts only reads .ok/.status/.json()).
 */

import { createElement } from 'react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import type { QueryClient } from '@tanstack/react-query'

import type { UserOut } from '../../types'
import { keys } from '../../query/queries'
import { SettingsModal } from '../SettingsModal'
import {
  render,
  typeInput,
  clickByText,
  selectValue,
  labeledInput,
  labeledSelect,
  buttonsWithText,
  buttonDisabled,
  hasText,
  flush,
  stubFetch,
  type RenderResult,
} from '../../test/harness'

function me(): UserOut {
  return {
    id: 1,
    email: 'alice@test.local',
    display_name: 'Alice',
    created_at: '2026-01-01T00:00:00Z',
    is_admin: true,
    email_verified: true,
    preferred_language: 'en',
    avatar_url: null,
    email_change_confirmation: true,
    password_change_confirmation: true,
  }
}

const seed = (qc: QueryClient): void => {
  qc.setQueryData(keys.me, me())
}

/** The theme segmented-control button (the only aria-pressed buttons here). */
function themeButton(root: ParentNode, text: string): HTMLButtonElement {
  const hit = buttonsWithText(root, text).find((b) => b.hasAttribute('aria-pressed'))
  if (!hit) throw new Error(`no theme button "${text}"`)
  return hit as HTMLButtonElement
}

let r: RenderResult | null = null
beforeEach(() => {
  localStorage.removeItem('easymoney.theme')
})
afterEach(() => {
  r?.unmount()
  r = null
  localStorage.removeItem('easymoney.theme')
  document.documentElement.classList.remove('dark')
})

describe('SettingsModal', () => {
  it('theme buttons carry aria-pressed and switch the persisted preference', () => {
    r = render(createElement(SettingsModal, { open: true, onClose: () => undefined }), { seed, theme: true })
    // No stored preference → defaults to "system".
    expect(themeButton(r.body, 'System').getAttribute('aria-pressed')).toBe('true')
    expect(themeButton(r.body, 'Dark').getAttribute('aria-pressed')).toBe('false')
    clickByText(r.body, 'Dark')
    expect(themeButton(r.body, 'Dark').getAttribute('aria-pressed')).toBe('true')
    expect(localStorage.getItem('easymoney.theme')).toBe('dark')
    expect(document.documentElement.classList.contains('dark')).toBe(true)
  })

  it('email change with confirmation on reports the two-emails notice', async () => {
    r = render(createElement(SettingsModal, { open: true, onClose: () => undefined }), { seed, theme: true })
    typeInput(labeledInput(r.body, 'Change email'), 'new@test.local')
    const stub = stubFetch({ '/api/me/email/change': { status: 'confirmation_sent' } })
    try {
      clickByText(r.body, 'Change email')
      await flush()
      expect(stub.calls.some((c) => c.url.includes('/api/me/email/change'))).toBe(true)
      expect(hasText(r.body, 'Two emails were sent:')).toBe(true)
    } finally {
      stub.restore()
    }
  })

  it('email change applied immediately reports "Email changed."', async () => {
    r = render(createElement(SettingsModal, { open: true, onClose: () => undefined }), { seed, theme: true })
    typeInput(labeledInput(r.body, 'Change email'), 'new@test.local')
    const stub = stubFetch({ '/api/me/email/change': { status: 'changed' } })
    try {
      clickByText(r.body, 'Change email')
      await flush()
      expect(hasText(r.body, 'Email changed.')).toBe(true)
    } finally {
      stub.restore()
    }
  })

  it('password change enforces the 8-char minimum and reports the result', async () => {
    r = render(createElement(SettingsModal, { open: true, onClose: () => undefined }), { seed, theme: true })
    const field = labeledInput(r.body, 'Change password')
    typeInput(field, 'short')
    expect(buttonDisabled(r.body, 'Change password')).toBe(true)
    typeInput(field, 'longenough1')
    expect(buttonDisabled(r.body, 'Change password')).toBe(false)
    const stub = stubFetch({ '/api/me/password/change': { status: 'changed' } })
    try {
      clickByText(r.body, 'Change password')
      await flush()
      expect(hasText(r.body, 'Password changed.')).toBe(true)
    } finally {
      stub.restore()
    }
  })

  it('switching the language PATCHes /me and shows "Saved"', async () => {
    r = render(createElement(SettingsModal, { open: true, onClose: () => undefined }), { seed, theme: true })
    const stub = stubFetch({ '/api/me': me() })
    try {
      selectValue(labeledSelect(r.body, 'Language'), 'ru')
      await flush()
      expect(
        stub.calls.some(
          (c) => c.url === '/api/me' && String(c.init?.method ?? '').toUpperCase() === 'PATCH',
        ),
      ).toBe(true)
      expect(hasText(r.body, 'Saved')).toBe(true)
    } finally {
      stub.restore()
    }
  })
})

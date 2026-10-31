/**
 * SettingsModal (AGENTS.md §2.13, §2.14, §3.6, §6.2): profile (name/language),
 * the email-change flow (confirmation-pending notice vs. immediate "Email
 * changed."), the password-change flow (8-char minimum, result message), the
 * theme segmented control (aria-pressed + persistence) and the
 * browser-notifications toggle (Web Push gate: supported / denied /
 * unsupported). Network is stubbed per request (client.ts only reads
 * .ok/.status/.json()). The Web Push client (lib/push) is mocked here — the
 * toggle logic is what is under test, and the push client's own behavior
 * (SW registration, VAPID key decoding, subscribe/unsubscribe) is covered in
 * lib/__tests__/push.test.ts.
 */

import { createElement } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { QueryClient } from '@tanstack/react-query'

import type { UserOut } from '../../types'
import { keys } from '../../query/queries'
import { disablePush, enablePush, pushSupported } from '../../lib/push'
import { SettingsModal } from '../SettingsModal'

vi.mock('../../lib/push', () => ({
  pushSupported: vi.fn(),
  enablePush: vi.fn(),
  disablePush: vi.fn(),
}))

const pushSupportedMock = vi.mocked(pushSupported)
const enablePushMock = vi.mocked(enablePush)
const disablePushMock = vi.mocked(disablePush)
import {
  render,
  typeInput,
  click,
  clickByText,
  selectValue,
  labeledInput,
  labeledButton,
  labeledSelect,
  buttonsWithText,
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
    preferred_currency: null,
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
  localStorage.removeItem('easymoney.browserNotify')
  localStorage.removeItem('easymoney.pushWhileVisible')
  localStorage.removeItem('easymoney.notifySound')
  // Web Push defaults: unsupported (toggle disabled with the unsupported
  // hint), enable/disable inert. The toggle tests override what they need.
  pushSupportedMock.mockReturnValue(false)
  enablePushMock.mockResolvedValue('ok')
  disablePushMock.mockResolvedValue(undefined)
})
afterEach(() => {
  r?.unmount()
  r = null
  localStorage.removeItem('easymoney.theme')
  localStorage.removeItem('easymoney.browserNotify')
  localStorage.removeItem('easymoney.pushWhileVisible')
  localStorage.removeItem('easymoney.notifySound')
  document.documentElement.classList.remove('dark')
  vi.clearAllMocks()
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
      click(labeledButton(r.body, 'Change email'))
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
      click(labeledButton(r.body, 'Change email'))
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
    expect(labeledButton(r.body, 'Change password').disabled).toBe(true)
    typeInput(field, 'longenough1')
    expect(labeledButton(r.body, 'Change password').disabled).toBe(false)
    const stub = stubFetch({ '/api/me/password/change': { status: 'changed' } })
    try {
      click(labeledButton(r.body, 'Change password'))
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

  describe('browser notifications toggle (§2.14)', () => {
    it('is disabled with an unsupported hint when the browser cannot do Web Push', () => {
      r = render(createElement(SettingsModal, { open: true, onClose: () => undefined }), {
        seed,
        theme: true,
        notify: true,
      })
      const box = labeledInput(r.body, 'Browser notifications')
      expect(box.disabled).toBe(true)
      expect(hasText(r.body, 'Browser notifications are not available in this browser.')).toBe(true)
    })

    it('toggles the persisted preference on/off when enabling succeeds', async () => {
      pushSupportedMock.mockReturnValue(true)
      enablePushMock.mockResolvedValue('ok')
      r = render(createElement(SettingsModal, { open: true, onClose: () => undefined }), {
        seed,
        theme: true,
        notify: true,
      })
      const box = labeledInput(r.body, 'Browser notifications')
      expect(box.disabled).toBe(false)
      expect(box.checked).toBe(false)
      click(box)
      await flush()
      expect(enablePushMock).toHaveBeenCalledTimes(1)
      expect(box.checked).toBe(true)
      expect(localStorage.getItem('easymoney.browserNotify')).toBe('on')
      click(box)
      await flush()
      expect(disablePushMock).toHaveBeenCalledTimes(1)
      expect(box.checked).toBe(false)
      expect(localStorage.getItem('easymoney.browserNotify')).toBe('off')
    })

    it('stays off and shows the denied hint when the permission prompt is refused', async () => {
      pushSupportedMock.mockReturnValue(true)
      enablePushMock.mockResolvedValue('denied')
      r = render(createElement(SettingsModal, { open: true, onClose: () => undefined }), {
        seed,
        theme: true,
        notify: true,
      })
      const box = labeledInput(r.body, 'Browser notifications')
      click(box)
      await flush()
      expect(box.checked).toBe(false)
      expect(localStorage.getItem('easymoney.browserNotify')).not.toBe('on')
      expect(hasText(r.body, 'Notifications are blocked for this site.')).toBe(true)
    })

    it('stays off and shows the unsupported hint when push cannot be enabled (e.g. VAPID 404)', async () => {
      pushSupportedMock.mockReturnValue(true)
      enablePushMock.mockResolvedValue('unsupported')
      r = render(createElement(SettingsModal, { open: true, onClose: () => undefined }), {
        seed,
        theme: true,
        notify: true,
      })
      const box = labeledInput(r.body, 'Browser notifications')
      click(box)
      await flush()
      expect(box.checked).toBe(false)
      expect(localStorage.getItem('easymoney.browserNotify')).not.toBe('on')
      expect(hasText(r.body, 'Browser notifications are not available in this browser.')).toBe(true)
    })

    it('exposes the notify-while-visible checkbox, enabled once the main toggle is on, and persists it', async () => {
      pushSupportedMock.mockReturnValue(true)
      enablePushMock.mockResolvedValue('ok')
      r = render(createElement(SettingsModal, { open: true, onClose: () => undefined }), {
        seed,
        theme: true,
        notify: true,
      })
      const box = labeledInput(r.body, 'Browser notifications')
      const wv = labeledInput(r.body, 'Also notify while the app is visible')
      // Main toggle is off → the while-visible preference is meaningless and
      // disabled (no subscription exists, so no push could ever fire).
      expect(wv.disabled).toBe(true)
      expect(wv.checked).toBe(false)
      click(box)
      await flush()
      expect(box.checked).toBe(true)
      expect(wv.disabled).toBe(false)
      click(wv)
      await flush()
      expect(wv.checked).toBe(true)
      expect(localStorage.getItem('easymoney.pushWhileVisible')).toBe('on')
      click(wv)
      await flush()
      expect(wv.checked).toBe(false)
      expect(localStorage.getItem('easymoney.pushWhileVisible')).toBe('off')
    })
  })

  describe('notification sound setting (§3.2, §2.14)', () => {
    it('is checked by default, always available (no Web Push gate), and persists the choice', async () => {
      // Web Push is unsupported by default here: the sound toggle must still
      // work — it is an in-app preference, not a Web Push one.
      r = render(createElement(SettingsModal, { open: true, onClose: () => undefined }), {
        seed,
        theme: true,
        notify: true,
      })
      const box = labeledInput(r.body, 'Notification sound')
      expect(box.disabled).toBe(false)
      expect(box.checked).toBe(true)
      // Unset by default: the sound plays (the long-standing behavior).
      expect(localStorage.getItem('easymoney.notifySound')).toBeNull()
      click(box)
      await flush()
      expect(box.checked).toBe(false)
      expect(localStorage.getItem('easymoney.notifySound')).toBe('off')
      click(box)
      await flush()
      expect(box.checked).toBe(true)
      expect(localStorage.getItem('easymoney.notifySound')).toBe('on')
    })
  })
})

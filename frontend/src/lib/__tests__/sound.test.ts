/**
 * Notification sound (AGENTS.md §3.2, §2.14): `/alert.mp3` plays on
 * `notification.created` unless the client-only *Notification sound* setting
 * (`easymoney.notifySound`, default on) says otherwise. The setting is read
 * fresh from localStorage on every play — the sound path keeps no state, so a
 * change made in one tab applies from the very next event in every tab of the
 * browser (req 7); the cross-tab `storage` event is handled on the
 * Settings-modal side (lib/browserNotify).
 */

import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest'

import { isSoundEnabled, playNotification, SOUND_STORAGE_KEY } from '../sound'

describe('isSoundEnabled', () => {
  beforeEach(() => localStorage.removeItem(SOUND_STORAGE_KEY))
  afterEach(() => localStorage.removeItem(SOUND_STORAGE_KEY))

  it('defaults to enabled when nothing is stored', () => {
    expect(isSoundEnabled()).toBe(true)
  })

  it('is enabled for an explicit "on"', () => {
    localStorage.setItem(SOUND_STORAGE_KEY, 'on')
    expect(isSoundEnabled()).toBe(true)
  })

  it('is disabled only for an explicit "off"', () => {
    localStorage.setItem(SOUND_STORAGE_KEY, 'off')
    expect(isSoundEnabled()).toBe(false)
  })

  it('treats any other stored value as enabled', () => {
    localStorage.setItem(SOUND_STORAGE_KEY, 'garbage')
    expect(isSoundEnabled()).toBe(true)
  })

  it('stays enabled when localStorage is unavailable', () => {
    const real = Storage.prototype.getItem
    Storage.prototype.getItem = () => {
      throw new Error('storage unavailable')
    }
    try {
      expect(isSoundEnabled()).toBe(true)
    } finally {
      Storage.prototype.getItem = real
    }
  })
})

describe('playNotification', () => {
  let playSpy: MockInstance<() => Promise<void>>

  beforeEach(() => {
    localStorage.removeItem(SOUND_STORAGE_KEY)
    playSpy = vi.spyOn(HTMLAudioElement.prototype, 'play').mockResolvedValue(undefined)
  })

  afterEach(() => {
    playSpy.mockRestore()
    localStorage.removeItem(SOUND_STORAGE_KEY)
  })

  it('plays the sound when nothing is stored (default on)', () => {
    playNotification()
    expect(playSpy).toHaveBeenCalledTimes(1)
  })

  it('plays the sound when the preference is "on"', () => {
    localStorage.setItem(SOUND_STORAGE_KEY, 'on')
    playNotification()
    expect(playSpy).toHaveBeenCalledTimes(1)
  })

  it('stays silent when the preference is "off"', () => {
    localStorage.setItem(SOUND_STORAGE_KEY, 'off')
    playNotification()
    expect(playSpy).not.toHaveBeenCalled()
  })

  it('reads the preference fresh on every play (no state to go stale)', () => {
    playNotification()
    expect(playSpy).toHaveBeenCalledTimes(1)
    // Another tab of the same browser disables the sound: localStorage now
    // holds "off" and this tab must be silent from the very next event
    // (req 7) without any reload or in-memory sync.
    localStorage.setItem(SOUND_STORAGE_KEY, 'off')
    playNotification()
    expect(playSpy).toHaveBeenCalledTimes(1)
  })
})

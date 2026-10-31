/**
 * Notification sound (AGENTS.md §3.2, `/alert.mp3`).
 *
 * The Audio element is created lazily. A one-time unlock on the first user
 * gesture (pointerdown/keydown) primes it: a WebSocket message is not a user
 * gesture, so under the browser autoplay policy the very first sound may be
 * silent until the user interacts once — a standard, accepted pattern.
 *
 * The client-only *Notification sound* setting (§2.14, `easymoney.notifySound`)
 * gates `playNotification()`. It is read fresh from localStorage on every
 * play and no state is kept, so disabling it in one tab silences the sound
 * from the very next `notification.created` in every tab of the browser
 * (req 7) — no reload, and no shared-cache mirror like while-visible (the
 * sound path is page-side, not service-worker-side). Default on (the sound
 * is the long-standing behavior); only an explicit stored 'off' silences it.
 */

/** localStorage key of the client-only notification-sound setting (`on`/`off`). */
export const SOUND_STORAGE_KEY = 'easymoney.notifySound'

/** Whether the notification sound is enabled (default on, §2.14). */
export function isSoundEnabled(): boolean {
  try {
    return localStorage.getItem(SOUND_STORAGE_KEY) !== 'off'
  } catch {
    // localStorage unavailable — keep the long-standing default (sound on).
    return true
  }
}

let audio: HTMLAudioElement | null = null
let primed = false
let listenersAdded = false

function getAudio(): HTMLAudioElement {
  if (!audio) {
    audio = new Audio('/alert.mp3')
    audio.preload = 'auto'
  }
  return audio
}

function onGesture(): void {
  if (primed) return
  primed = true
  window.removeEventListener('pointerdown', onGesture)
  window.removeEventListener('keydown', onGesture)
  // Prime the element with a play/pause so subsequent plays are allowed.
  const el = getAudio()
  void el
    .play()
    .then(() => {
      el.pause()
      el.currentTime = 0
    })
    .catch(() => {
      /* Autoplay still blocked; the next real play retries. */
    })
}

/** Register the one-time gesture unlock (idempotent; call once on app start). */
export function initSound(): void {
  if (listenersAdded) return
  listenersAdded = true
  window.addEventListener('pointerdown', onGesture)
  window.addEventListener('keydown', onGesture)
}

/** Play the notification sound. Safe to call before the first gesture. */
export function playNotification(): void {
  if (!isSoundEnabled()) return
  const el = getAudio()
  el.currentTime = 0
  void el.play().catch(() => {})
}

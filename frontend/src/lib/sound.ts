/**
 * Notification sound (AGENTS.md §3.2, `/alert.mp3`).
 *
 * The Audio element is created lazily. A one-time unlock on the first user
 * gesture (pointerdown/keydown) primes it: a WebSocket message is not a user
 * gesture, so under the browser autoplay policy the very first sound may be
 * silent until the user interacts once — a standard, accepted pattern.
 */

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
  const el = getAudio()
  el.currentTime = 0
  void el.play().catch(() => {})
}

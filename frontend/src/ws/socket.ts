/**
 * WS client (AGENTS.md §3.2): connects to /ws?token=<access token>,
 * re-authenticates on expiry (4401), reconnects with exponential backoff on
 * any drop, and keeps the socket alive with text pings (answered "pong").
 *
 * On every (re)connect the query layer refetches its state over REST before
 * trusting it — the bus exposes this via `onReconnect`.
 */

import { AUTH_EXPIRED_EVENT, clearTokens, getTokens, refreshTokens } from '../api/client'
import type { WSMessage } from '../types'

export type WSStatus = 'idle' | 'connecting' | 'open' | 'reconnecting'

type MessageListener = (msg: WSMessage) => void
type StatusListener = (status: WSStatus) => void

const PING_INTERVAL_MS = 25_000
const BASE_BACKOFF_MS = 500
const MAX_BACKOFF_MS = 30_000

class SocketClient {
  private ws: WebSocket | null = null
  private status: WSStatus = 'idle'
  private pingTimer: number | null = null
  private reconnectTimer: number | null = null
  private backoff = BASE_BACKOFF_MS
  private stopped = true
  private hasConnectedOnce = false
  private messageListeners = new Set<MessageListener>()
  private statusListeners = new Set<StatusListener>()

  get currentStatus(): WSStatus {
    return this.status
  }

  onMessage(listener: MessageListener): () => void {
    this.messageListeners.add(listener)
    return () => this.messageListeners.delete(listener)
  }

  onStatus(listener: StatusListener): () => void {
    this.statusListeners.add(listener)
    listener(this.status)
    return () => this.statusListeners.delete(listener)
  }

  /** Refetch-all hook for the query layer (fired after a drop + reconnect). */
  onReconnect: (() => void) | null = null

  connect(): void {
    if (this.stopped) return
    this.openSocket()
  }

  start(): void {
    this.stopped = false
    this.openSocket()
  }

  stop(): void {
    this.stopped = true
    this.clearTimers()
    if (this.ws) {
      // 1000 = normal closure; the server will not try to re-deliver.
      this.ws.close(1000)
      this.ws = null
    }
    this.setStatus('idle')
  }

  private clearTimers(): void {
    if (this.pingTimer !== null) {
      window.clearInterval(this.pingTimer)
      this.pingTimer = null
    }
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
  }

  private setStatus(status: WSStatus): void {
    this.status = status
    for (const listener of this.statusListeners) listener(status)
  }

  private openSocket(): void {
    this.clearTimers()
    const token = getTokens()?.access
    if (!token) {
      // No session: nothing to connect (the app redirects to /login).
      this.stopped = true
      return
    }
    this.setStatus(this.hasConnectedOnce ? 'reconnecting' : 'connecting')
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const url = `${proto}://${window.location.host}/ws?token=${encodeURIComponent(token)}`
    const ws = new WebSocket(url)
    this.ws = ws

    ws.onopen = () => {
      const reconnected = this.hasConnectedOnce
      this.hasConnectedOnce = true
      this.backoff = BASE_BACKOFF_MS
      this.setStatus('open')
      this.pingTimer = window.setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send('ping')
      }, PING_INTERVAL_MS)
      if (reconnected && this.onReconnect) this.onReconnect()
    }

    ws.onmessage = (event) => {
      if (typeof event.data !== 'string') return
      if (event.data === 'pong') return
      let msg: WSMessage
      try {
        msg = JSON.parse(event.data) as WSMessage
      } catch {
        return
      }
      for (const listener of this.messageListeners) listener(msg)
    }

    ws.onclose = (event) => {
      this.clearTimers()
      this.ws = null
      if (this.stopped) return
      if (event.code === 4401) {
        // Token expired (or rejected): re-authenticate once, then reconnect.
        void this.handleAuthFailure()
        return
      }
      // Any other drop (1011 = server not ready, network, restart): backoff.
      this.scheduleReconnect()
    }
  }

  private async handleAuthFailure(): Promise<void> {
    const refreshed = await refreshTokens()
    if (!refreshed) {
      clearTokens()
      this.stopped = true
      this.setStatus('idle')
      window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))
      return
    }
    // Fresh access token: reconnect immediately without waiting for backoff.
    this.openSocket()
  }

  private scheduleReconnect(): void {
    const delay = this.backoff
    this.backoff = Math.min(this.backoff * 2, MAX_BACKOFF_MS)
    this.setStatus('reconnecting')
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null
      if (!this.stopped) this.openSocket()
    }, delay)
  }
}

export const socket = new SocketClient()

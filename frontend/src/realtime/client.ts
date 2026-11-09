import { ApiError } from '../api/client'
import {
  REALTIME_PROTOCOL,
  REALTIME_TICKET_PROTOCOL_PREFIX,
  type RealtimeChannel,
  type RealtimeServerEvent,
  type RealtimeUpdateEvent,
  type WebSocketTicketResponse,
} from './types'

const DEFAULT_FALLBACK_INTERVAL_MS = 15_000
const DEFAULT_CONNECTION_TIMEOUT_MS = 10_000
const DEFAULT_RECONNECT_BASE_DELAY_MS = 1_000
const DEFAULT_RECONNECT_MAX_DELAY_MS = 30_000
const AUTHENTICATION_CLOSE_CODES = new Set([4401, 4403, 4404])

type Callback = (signal: AbortSignal) => void | Promise<void>

export interface RealtimeTicketClient {
  request(
    path: string,
    options: { readonly method: 'POST'; readonly signal: AbortSignal },
  ): Promise<WebSocketTicketResponse>
}

interface RealtimeEnvironment {
  readonly window: Pick<Window, 'addEventListener' | 'removeEventListener' | 'location'>
  readonly document: Pick<Document, 'addEventListener' | 'removeEventListener' | 'visibilityState'>
  readonly navigator: Pick<Navigator, 'onLine'>
}

export interface RealtimeClientOptions {
  readonly apiClient: RealtimeTicketClient
  readonly channel: RealtimeChannel
  readonly onEvent: (event: RealtimeUpdateEvent) => void
  readonly onSynchronize: Callback
  readonly onFallbackRefetch: Callback
  readonly fallbackIntervalMs?: number
  readonly connectionTimeoutMs?: number
  readonly reconnectBaseDelayMs?: number
  readonly reconnectMaxDelayMs?: number
  readonly random?: () => number
  readonly createWebSocket?: (url: string, protocols: string[]) => WebSocket
  readonly environment?: RealtimeEnvironment
}

export class RealtimeClient {
  readonly #apiClient: RealtimeTicketClient
  readonly #channel: RealtimeChannel
  readonly #onEvent: (event: RealtimeUpdateEvent) => void
  readonly #onSynchronize: Callback
  readonly #onFallbackRefetch: Callback
  readonly #fallbackIntervalMs: number
  readonly #connectionTimeoutMs: number
  readonly #reconnectBaseDelayMs: number
  readonly #reconnectMaxDelayMs: number
  readonly #random: () => number
  readonly #createWebSocket: (url: string, protocols: string[]) => WebSocket
  readonly #environment: RealtimeEnvironment

  #started = false
  #generation = 0
  #socket: WebSocket | null = null
  #ticketController: AbortController | null = null
  #reconnectTimer: ReturnType<typeof setTimeout> | null = null
  #openTimer: ReturnType<typeof setTimeout> | null = null
  #fallbackTimer: ReturnType<typeof setInterval> | null = null
  #fallbackInFlight = false
  readonly #callbackControllers = new Set<AbortController>()
  #fallbackController: AbortController | null = null
  #synchronizeController: AbortController | null = null
  #reconnectAttempt = 0
  #authenticationFailed = false

  constructor(options: RealtimeClientOptions) {
    this.#apiClient = options.apiClient
    this.#channel = options.channel
    this.#onEvent = options.onEvent
    this.#onSynchronize = options.onSynchronize
    this.#onFallbackRefetch = options.onFallbackRefetch
    this.#fallbackIntervalMs = options.fallbackIntervalMs ?? DEFAULT_FALLBACK_INTERVAL_MS
    this.#connectionTimeoutMs = options.connectionTimeoutMs ?? DEFAULT_CONNECTION_TIMEOUT_MS
    this.#reconnectBaseDelayMs =
      options.reconnectBaseDelayMs ?? DEFAULT_RECONNECT_BASE_DELAY_MS
    this.#reconnectMaxDelayMs = options.reconnectMaxDelayMs ?? DEFAULT_RECONNECT_MAX_DELAY_MS
    this.#random = options.random ?? Math.random
    this.#createWebSocket =
      options.createWebSocket ?? ((url, protocols) => new WebSocket(url, protocols))
    this.#environment = options.environment ?? {
      window,
      document,
      navigator,
    }
  }

  start(): void {
    if (this.#started) return
    this.#started = true
    this.#generation += 1
    this.#authenticationFailed = false
    this.#environment.document.addEventListener('visibilitychange', this.#handleVisibilityChange)
    this.#environment.window.addEventListener('online', this.#handleOnline)
    this.#environment.window.addEventListener('offline', this.#handleOffline)
    if (this.#canUseNetwork()) void this.#connect(this.#generation)
  }

  stop(): void {
    if (!this.#started) return
    this.#started = false
    this.#generation += 1
    this.#environment.document.removeEventListener(
      'visibilitychange',
      this.#handleVisibilityChange,
    )
    this.#environment.window.removeEventListener('online', this.#handleOnline)
    this.#environment.window.removeEventListener('offline', this.#handleOffline)
    this.#cancelPendingWork()
    this.#closeSocket()
    this.#stopFallback()
  }

  get connected(): boolean {
    return this.#socket?.readyState === WebSocket.OPEN
  }

  async #connect(generation: number): Promise<void> {
    if (!this.#isCurrent(generation) || !this.#canUseNetwork()) return
    if (this.#socket || this.#ticketController || this.#authenticationFailed) return

    const controller = new AbortController()
    this.#ticketController = controller
    try {
      const ticket = await this.#apiClient.request(ticketPath(this.#channel), {
        method: 'POST',
        signal: controller.signal,
      })
      if (!this.#isCurrent(generation) || controller.signal.aborted || !this.#canUseNetwork()) {
        return
      }
      const socket = this.#createWebSocket(
        webSocketUrl(this.#channel, this.#environment.window.location),
        [REALTIME_PROTOCOL, `${REALTIME_TICKET_PROTOCOL_PREFIX}${ticket.ticket}`],
      )
      this.#socket = socket
      this.#bindSocket(socket, generation)
      this.#openTimer = setTimeout(() => {
        this.#openTimer = null
        if (this.#isCurrentSocket(socket, generation) && socket.readyState === WebSocket.CONNECTING) {
          socket.close(1000, 'WebSocket connection timeout')
        }
      }, this.#connectionTimeoutMs)
    } catch (error) {
      if (!this.#isCurrent(generation) || controller.signal.aborted) return
      if (isTerminalTicketError(error, this.#channel)) {
        this.#authenticationFailed = true
        this.#stopFallback()
        return
      }
      this.#startFallback()
      this.#scheduleReconnect(generation)
    } finally {
      if (this.#ticketController === controller) this.#ticketController = null
    }
  }

  #bindSocket(socket: WebSocket, generation: number): void {
    socket.onopen = () => {
      if (!this.#isCurrentSocket(socket, generation)) return
      this.#clearOpenTimeout()
      if (socket.protocol !== REALTIME_PROTOCOL) {
        this.#detachSocket(socket)
        this.#socket = null
        socket.close(1002, 'Unexpected WebSocket protocol')
        this.#startFallback()
        this.#scheduleReconnect(generation)
        return
      }
      this.#reconnectAttempt = 0
      this.#clearReconnect()
      this.#stopFallback()
      this.#synchronizeController?.abort()
      this.#synchronizeController = this.#runCallback(
        this.#onSynchronize,
        (controller) => {
          if (this.#synchronizeController === controller) this.#synchronizeController = null
        },
      )
    }
    socket.onmessage = (message) => {
      if (!this.#isCurrentSocket(socket, generation)) return
      const event = parseRealtimeEvent(message.data)
      if (event && event.type !== 'heartbeat') {
        try {
          this.#onEvent(event)
        } catch {
          // Consumer failures must not break the transport lifecycle.
        }
      }
    }
    socket.onerror = () => {
      if (this.#isCurrentSocket(socket, generation)) socket.close()
    }
    socket.onclose = (event) => {
      if (!this.#isCurrentSocket(socket, generation)) return
      this.#clearOpenTimeout()
      this.#detachSocket(socket)
      this.#socket = null
      if (AUTHENTICATION_CLOSE_CODES.has(event.code)) {
        this.#authenticationFailed = true
        this.#stopFallback()
        return
      }
      if (!this.#canUseNetwork()) return
      this.#startFallback()
      this.#scheduleReconnect(generation)
    }
  }

  #scheduleReconnect(generation: number): void {
    if (
      this.#reconnectTimer ||
      !this.#isCurrent(generation) ||
      !this.#canUseNetwork() ||
      this.#authenticationFailed
    ) {
      return
    }
    const exponential = Math.min(
      this.#reconnectMaxDelayMs,
      this.#reconnectBaseDelayMs * 2 ** this.#reconnectAttempt,
    )
    const delay = exponential * (0.5 + this.#random() * 0.5)
    this.#reconnectAttempt += 1
    this.#reconnectTimer = setTimeout(() => {
      this.#reconnectTimer = null
      void this.#connect(generation)
    }, delay)
  }

  #startFallback(): void {
    if (this.#fallbackTimer || !this.#canUseNetwork() || this.#authenticationFailed) return
    this.#fallbackTimer = setInterval(() => {
      if (this.#fallbackInFlight || !this.#canUseNetwork() || this.connected) return
      this.#fallbackInFlight = true
      this.#fallbackController = this.#runCallback(
        this.#onFallbackRefetch,
        (controller) => {
          if (this.#fallbackController === controller) {
            this.#fallbackController = null
            this.#fallbackInFlight = false
          }
        },
      )
    }, this.#fallbackIntervalMs)
  }

  #stopFallback(): void {
    if (this.#fallbackTimer) clearInterval(this.#fallbackTimer)
    this.#fallbackTimer = null
    this.#fallbackController?.abort()
    this.#fallbackController = null
    this.#fallbackInFlight = false
  }

  #handleVisibilityChange = (): void => {
    if (!this.#started) return
    if (this.#environment.document.visibilityState !== 'visible') {
      this.#suspend()
      return
    }
    this.#resume()
  }

  #handleOffline = (): void => {
    if (this.#started) this.#suspend()
  }

  #handleOnline = (): void => {
    if (this.#started) this.#resume()
  }

  #suspend(): void {
    this.#generation += 1
    this.#cancelPendingWork()
    this.#closeSocket()
    this.#stopFallback()
  }

  #resume(): void {
    if (!this.#canUseNetwork()) return
    if (this.#socket || this.#ticketController) return
    this.#generation += 1
    this.#clearReconnect()
    this.#authenticationFailed = false
    this.#reconnectAttempt = 0
    void this.#connect(this.#generation)
  }

  #cancelPendingWork(): void {
    this.#ticketController?.abort()
    this.#ticketController = null
    for (const controller of this.#callbackControllers) controller.abort()
    this.#callbackControllers.clear()
    this.#fallbackController = null
    this.#synchronizeController = null
    this.#clearReconnect()
  }

  #runCallback(
    callback: Callback,
    onSettled?: (controller: AbortController) => void,
  ): AbortController {
    const controller = new AbortController()
    this.#callbackControllers.add(controller)
    void Promise.resolve()
      .then(() => callback(controller.signal))
      .catch(() => undefined)
      .finally(() => {
        this.#callbackControllers.delete(controller)
        onSettled?.(controller)
      })
    return controller
  }

  #clearReconnect(): void {
    if (this.#reconnectTimer) clearTimeout(this.#reconnectTimer)
    this.#reconnectTimer = null
  }

  #clearOpenTimeout(): void {
    if (this.#openTimer) clearTimeout(this.#openTimer)
    this.#openTimer = null
  }

  #closeSocket(): void {
    const socket = this.#socket
    this.#socket = null
    this.#clearOpenTimeout()
    if (!socket) return
    this.#detachSocket(socket)
    if (socket.readyState === WebSocket.CONNECTING || socket.readyState === WebSocket.OPEN) {
      socket.close(1000, 'Client suspended')
    }
  }

  #detachSocket(socket: WebSocket): void {
    socket.onopen = null
    socket.onmessage = null
    socket.onerror = null
    socket.onclose = null
  }

  #canUseNetwork(): boolean {
    return (
      this.#started &&
      this.#environment.document.visibilityState === 'visible' &&
      this.#environment.navigator.onLine
    )
  }

  #isCurrent(generation: number): boolean {
    return this.#started && generation === this.#generation
  }

  #isCurrentSocket(socket: WebSocket, generation: number): boolean {
    return this.#isCurrent(generation) && this.#socket === socket
  }
}

export function parseRealtimeEvent(value: unknown): RealtimeServerEvent | null {
  if (typeof value !== 'string') return null
  let candidate: unknown
  try {
    candidate = JSON.parse(value)
  } catch {
    return null
  }
  if (!isRecord(candidate) || typeof candidate.timestamp !== 'string') return null
  if (candidate.type === 'heartbeat') return { type: 'heartbeat', timestamp: candidate.timestamp } as const
  if (candidate.type === 'sessions.updated') {
    return { type: 'sessions.updated', timestamp: candidate.timestamp } as const
  }
  if (candidate.type === 'session.updated' && typeof candidate.session_id === 'string') {
    return {
      type: 'session.updated',
      session_id: candidate.session_id,
      timestamp: candidate.timestamp,
    } as const
  }
  return null
}

function ticketPath(channel: RealtimeChannel): string {
  return channel.type === 'sessions'
    ? '/game-sessions/ws-ticket'
    : `/game-sessions/${channel.sessionId}/ws-ticket`
}

function webSocketUrl(
  channel: RealtimeChannel,
  location: Pick<Location, 'protocol' | 'host'>,
): string {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const path =
    channel.type === 'sessions'
      ? '/api/v1/game-sessions/ws'
      : `/api/v1/game-sessions/${channel.sessionId}/ws`
  return `${protocol}//${location.host}${path}`
}

function isTerminalTicketError(error: unknown, channel: RealtimeChannel): boolean {
  return error instanceof ApiError && (
    error.status === 401 ||
    error.status === 403 ||
    (error.status === 404 && channel.type === 'session')
  )
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

import { useEffect, useRef } from 'react'

import { RealtimeClient, type RealtimeTicketClient } from './client'
import type { RealtimeChannel, RealtimeUpdateEvent } from './types'

interface UseRealtimeChannelOptions {
  readonly apiClient: RealtimeTicketClient
  readonly channel: RealtimeChannel
  readonly enabled: boolean
  readonly onEvent: (event: RealtimeUpdateEvent) => void
  readonly onSynchronize: (signal: AbortSignal) => void | Promise<void>
  readonly onFallbackRefetch: (signal: AbortSignal) => void | Promise<void>
  readonly fallbackIntervalMs?: number
  readonly connectionTimeoutMs?: number
  readonly reconnectBaseDelayMs?: number
  readonly reconnectMaxDelayMs?: number
  readonly random?: () => number
  readonly createWebSocket?: (url: string, protocols: string[]) => WebSocket
}

export function useRealtimeChannel(options: UseRealtimeChannelOptions): void {
  const {
    apiClient,
    channel,
    connectionTimeoutMs,
    createWebSocket,
    enabled,
    fallbackIntervalMs,
    onEvent,
    onFallbackRefetch,
    onSynchronize,
    random,
    reconnectBaseDelayMs,
    reconnectMaxDelayMs,
  } = options
  const callbacks = useRef({
    onEvent,
    onSynchronize,
    onFallbackRefetch,
  })
  useEffect(() => {
    callbacks.current = { onEvent, onSynchronize, onFallbackRefetch }
  }, [onEvent, onFallbackRefetch, onSynchronize])
  const channelKey =
    channel.type === 'sessions' ? 'sessions' : `session:${channel.sessionId}`
  const channelType = channel.type
  const sessionId = channel.type === 'session' ? channel.sessionId : null

  useEffect(() => {
    if (!enabled) return
    const stableChannel: RealtimeChannel =
      channelType === 'sessions' ? { type: 'sessions' } : { type: 'session', sessionId: sessionId! }
    const client = new RealtimeClient({
      apiClient,
      channel: stableChannel,
      onEvent: (event) => callbacks.current.onEvent(event),
      onSynchronize: (signal) => callbacks.current.onSynchronize(signal),
      onFallbackRefetch: (signal) => callbacks.current.onFallbackRefetch(signal),
      fallbackIntervalMs,
      connectionTimeoutMs,
      reconnectBaseDelayMs,
      reconnectMaxDelayMs,
      random,
      createWebSocket,
    })
    client.start()
    return () => client.stop()
  }, [
    channelKey,
    apiClient,
    channelType,
    connectionTimeoutMs,
    createWebSocket,
    enabled,
    fallbackIntervalMs,
    random,
    reconnectBaseDelayMs,
    reconnectMaxDelayMs,
    sessionId,
  ])
}

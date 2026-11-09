import { StrictMode } from 'react'
import { act, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/client'
import {
  RealtimeClient,
  parseRealtimeEvent,
  type RealtimeTicketClient,
} from './client'
import type { RealtimeUpdateEvent, WebSocketTicketResponse } from './types'
import { useRealtimeChannel } from './useRealtimeChannel'

describe('RealtimeClient', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    setVisibility('visible')
    setOnline(true)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('gets a fresh ticket and connects without credentials in the URL', async () => {
    const harness = createHarness()
    harness.client.start()
    await flushPromises()

    expect(harness.request.mock.calls[0]?.[0]).toBe('/game-sessions/ws-ticket')
    expect(harness.request.mock.calls[0]?.[1].method).toBe('POST')
    expect(harness.request.mock.calls[0]?.[1].signal).toBeInstanceOf(AbortSignal)
    expect(harness.sockets).toHaveLength(1)
    expect(harness.sockets[0]?.url).toBe('ws://localhost:3000/api/v1/game-sessions/ws')
    expect(harness.sockets[0]?.url).not.toContain('ticket-1')
    expect(harness.sockets[0]?.protocols).toEqual([
      'pingstats.v1',
      'pingstats.ticket.ticket-1',
    ])

    harness.sockets[0]?.open()
    await flushPromises()
    expect(harness.synchronize).toHaveBeenCalledOnce()
    harness.client.stop()
  })

  it('supports session endpoint and requires the negotiated protocol', async () => {
    const harness = createHarness({ channel: { type: 'session', sessionId: 'session-1' } })
    harness.client.start()
    await flushPromises()

    expect(harness.request.mock.calls[0]?.[0]).toBe('/game-sessions/session-1/ws-ticket')
    expect(harness.request.mock.calls[0]?.[1].method).toBe('POST')
    expect(harness.request.mock.calls[0]?.[1].signal).toBeInstanceOf(AbortSignal)
    expect(harness.sockets[0]?.url).toContain('/api/v1/game-sessions/session-1/ws')
    harness.sockets[0]?.open('wrong.protocol')
    expect(harness.sockets[0]?.closedWith).toBe(1002)
    expect(harness.synchronize).not.toHaveBeenCalled()
    harness.client.stop()
  })

  it('ignores heartbeat, malformed JSON and unknown events but exposes updates once', async () => {
    const harness = createHarness()
    harness.client.start()
    await flushPromises()
    const socket = harness.sockets[0]!
    socket.open()

    socket.message('{')
    socket.message(JSON.stringify({ type: 'unknown', timestamp: '2026-11-09T00:00:00Z' }))
    socket.message(JSON.stringify({ type: 'heartbeat', timestamp: '2026-11-09T00:00:00Z' }))
    socket.message(
      JSON.stringify({
        type: 'session.updated',
        session_id: 'session-1',
        timestamp: '2026-11-09T00:00:00Z',
      }),
    )
    socket.message(JSON.stringify({ type: 'sessions.updated', timestamp: '2026-11-09T00:00:01Z' }))

    expect(harness.onEvent).toHaveBeenCalledTimes(2)
    expect(harness.onEvent.mock.calls.map(([event]) => event.type)).toEqual([
      'session.updated',
      'sessions.updated',
    ])
    harness.client.stop()
  })

  it('reconnects with a new ticket, backoff and jitter, then fully synchronizes', async () => {
    const harness = createHarness({ random: () => 0 })
    harness.request
      .mockResolvedValueOnce({ ticket: 'first', expires_in: 30 })
      .mockResolvedValueOnce({ ticket: 'second', expires_in: 30 })
    harness.client.start()
    await flushPromises()
    harness.sockets[0]?.open()
    await flushPromises()
    harness.sockets[0]?.serverClose(1006)

    await vi.advanceTimersByTimeAsync(499)
    expect(harness.request).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    await flushPromises()
    expect(harness.request).toHaveBeenCalledTimes(2)
    expect(harness.sockets[1]?.protocols[1]).toBe('pingstats.ticket.second')
    harness.sockets[1]?.open()
    await flushPromises()
    expect(harness.synchronize).toHaveBeenCalledTimes(2)

    await vi.advanceTimersByTimeAsync(15_000)
    expect(harness.fallback).not.toHaveBeenCalled()
    harness.client.stop()
  })

  it('increases reconnect delay and caps it while applying jitter', async () => {
    const harness = createHarness({ random: () => 0, reconnectMaxDelayMs: 1_500 })
    harness.request.mockRejectedValue(new TypeError('offline'))
    harness.client.start()
    await flushPromises()

    await vi.advanceTimersByTimeAsync(500)
    await flushPromises()
    expect(harness.request).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(749)
    expect(harness.request).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(1)
    await flushPromises()
    expect(harness.request).toHaveBeenCalledTimes(3)
    harness.client.stop()
  })

  it('does not loop or poll after permanent authentication failure', async () => {
    const harness = createHarness()
    harness.request.mockRejectedValue(new ApiError(401, 'unauthorized', 'Unauthorized'))
    harness.client.start()
    await flushPromises()
    await vi.advanceTimersByTimeAsync(120_000)

    expect(harness.request).toHaveBeenCalledOnce()
    expect(harness.fallback).not.toHaveBeenCalled()
    harness.client.stop()
  })

  it('does not loop when a session disappears before ticket issuance', async () => {
    const harness = createHarness({
      channel: { type: 'session', sessionId: 'missing-session' },
    })
    harness.request.mockRejectedValue(new ApiError(404, 'not_found', 'Not found'))
    harness.client.start()
    await flushPromises()
    await vi.advanceTimersByTimeAsync(120_000)

    expect(harness.request).toHaveBeenCalledOnce()
    expect(harness.fallback).not.toHaveBeenCalled()
    harness.client.stop()
  })

  it('does not reconnect after an authentication WebSocket close code', async () => {
    const harness = createHarness()
    harness.client.start()
    await flushPromises()
    harness.sockets[0]?.open()
    await flushPromises()
    harness.sockets[0]?.serverClose(4401)
    await vi.advanceTimersByTimeAsync(120_000)

    expect(harness.request).toHaveBeenCalledOnce()
    expect(harness.fallback).not.toHaveBeenCalled()
    harness.client.stop()
  })

  it('uses one REST fallback interval only while the socket is unavailable', async () => {
    const harness = createHarness({ fallbackIntervalMs: 15_000 })
    harness.request.mockRejectedValue(new TypeError('network unavailable'))
    harness.client.start()
    await flushPromises()

    await vi.advanceTimersByTimeAsync(15_000)
    expect(harness.fallback).toHaveBeenCalledOnce()
    await vi.advanceTimersByTimeAsync(15_000)
    expect(harness.fallback).toHaveBeenCalledTimes(2)
    harness.client.stop()
    await vi.advanceTimersByTimeAsync(30_000)
    expect(harness.fallback).toHaveBeenCalledTimes(2)
  })

  it('falls back and reconnects when the WebSocket handshake stalls', async () => {
    const harness = createHarness({ connectionTimeoutMs: 100, random: () => 0 })
    harness.client.start()
    await flushPromises()

    await vi.advanceTimersByTimeAsync(100)
    expect(harness.sockets[0]?.closedWith).toBe(1000)
    await vi.advanceTimersByTimeAsync(500)
    await flushPromises()
    expect(harness.request).toHaveBeenCalledTimes(2)
    harness.client.stop()
  })

  it('aborts an in-flight fallback refetch on unmount', async () => {
    const harness = createHarness({ fallbackIntervalMs: 15_000 })
    harness.request.mockRejectedValue(new TypeError('network unavailable'))
    harness.fallback.mockImplementation(() => new Promise(() => undefined))
    harness.client.start()
    await flushPromises()
    await vi.advanceTimersByTimeAsync(15_000)
    const signal = harness.fallback.mock.calls[0]?.[0]
    expect(signal?.aborted).toBe(false)

    harness.client.stop()

    expect(signal?.aborted).toBe(true)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('suspends in background and offline, then reconnects and synchronizes on recovery', async () => {
    const harness = createHarness()
    harness.client.start()
    await flushPromises()
    harness.sockets[0]?.open()
    await flushPromises()

    setVisibility('hidden')
    document.dispatchEvent(new Event('visibilitychange'))
    expect(harness.sockets[0]?.closedWith).toBe(1000)
    await vi.advanceTimersByTimeAsync(30_000)
    expect(harness.request).toHaveBeenCalledOnce()

    setVisibility('visible')
    document.dispatchEvent(new Event('visibilitychange'))
    await flushPromises()
    expect(harness.request).toHaveBeenCalledTimes(2)
    harness.sockets[1]?.open()
    await flushPromises()

    setOnline(false)
    window.dispatchEvent(new Event('offline'))
    setOnline(true)
    window.dispatchEvent(new Event('online'))
    await flushPromises()
    expect(harness.request).toHaveBeenCalledTimes(3)
    harness.sockets[2]?.open()
    await flushPromises()
    expect(harness.synchronize).toHaveBeenCalledTimes(3)
    harness.client.stop()
  })

  it('stops ticket work, reconnect and listeners on stop', async () => {
    let resolveTicket: ((value: { ticket: string; expires_in: number }) => void) | undefined
    const addWindow = vi.spyOn(window, 'addEventListener')
    const removeWindow = vi.spyOn(window, 'removeEventListener')
    const addDocument = vi.spyOn(document, 'addEventListener')
    const removeDocument = vi.spyOn(document, 'removeEventListener')
    const harness = createHarness()
    harness.request.mockReturnValue(
      new Promise((resolve) => {
        resolveTicket = resolve
      }),
    )

    harness.client.start()
    harness.client.start()
    expect(addWindow.mock.calls.filter(([name]) => name === 'online')).toHaveLength(1)
    expect(addDocument.mock.calls.filter(([name]) => name === 'visibilitychange')).toHaveLength(1)
    harness.client.stop()
    resolveTicket?.({ ticket: 'late', expires_in: 30 })
    await flushPromises()

    expect(harness.sockets).toHaveLength(0)
    expect(removeWindow.mock.calls.filter(([name]) => name === 'online')).toHaveLength(1)
    expect(removeDocument.mock.calls.filter(([name]) => name === 'visibilitychange')).toHaveLength(1)
    expect(vi.getTimerCount()).toBe(0)
  })
})

describe('useRealtimeChannel', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    setVisibility('visible')
    setOnline(true)
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('survives Strict Mode and rerenders without duplicate sockets or stale callbacks', async () => {
    const request = ticketRequestMock().mockResolvedValue({ ticket: 'strict', expires_in: 30 })
    const sockets: FakeWebSocket[] = []
    const firstCallback = vi.fn()
    const latestCallback = vi.fn()
    const createWebSocket = (url: string, protocols: string[]) => {
      const socket = new FakeWebSocket(url, protocols)
      sockets.push(socket)
      return socket as unknown as WebSocket
    }
    const apiClient: RealtimeTicketClient = { request }

    function Consumer({ enabled, latest }: { enabled: boolean; latest: boolean }) {
      useRealtimeChannel({
        apiClient,
        channel: { type: 'sessions' },
        enabled,
        onEvent: latest ? latestCallback : firstCallback,
        onSynchronize: vi.fn(),
        onFallbackRefetch: vi.fn(),
        createWebSocket,
      })
      return null
    }

    const view = render(
      <StrictMode>
        <Consumer enabled latest={false} />
      </StrictMode>,
    )
    await act(flushPromises)
    const active = sockets.filter((socket) => socket.readyState !== WebSocket.CLOSED)
    expect(active).toHaveLength(1)
    active[0]?.open()

    view.rerender(
      <StrictMode>
        <Consumer enabled latest />
      </StrictMode>,
    )
    active[0]?.message(
      JSON.stringify({ type: 'sessions.updated', timestamp: '2026-11-09T00:00:00Z' }),
    )
    expect(firstCallback).not.toHaveBeenCalled()
    expect(latestCallback).toHaveBeenCalledOnce()
    expect(sockets.filter((socket) => socket.readyState !== WebSocket.CLOSED)).toHaveLength(1)

    view.rerender(
      <StrictMode>
        <Consumer enabled={false} latest />
      </StrictMode>,
    )
    expect(sockets.filter((socket) => socket.readyState !== WebSocket.CLOSED)).toHaveLength(0)
    view.unmount()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('closes the old socket before connecting to another session ID', async () => {
    const request = ticketRequestMock().mockResolvedValue({ ticket: 'session', expires_in: 30 })
    const sockets: FakeWebSocket[] = []
    const apiClient: RealtimeTicketClient = { request }

    function Consumer({ sessionId }: { sessionId: string }) {
      useRealtimeChannel({
        apiClient,
        channel: { type: 'session', sessionId },
        enabled: true,
        onEvent: vi.fn(),
        onSynchronize: vi.fn(),
        onFallbackRefetch: vi.fn(),
        createWebSocket: (url, protocols) => {
          const socket = new FakeWebSocket(url, protocols)
          sockets.push(socket)
          return socket as unknown as WebSocket
        },
      })
      return null
    }

    const view = render(<Consumer sessionId="session-a" />)
    await act(flushPromises)
    expect(sockets).toHaveLength(1)
    sockets[0]?.open()

    view.rerender(<Consumer sessionId="session-b" />)
    expect(sockets[0]?.readyState).toBe(WebSocket.CLOSED)
    await act(flushPromises)

    expect(sockets).toHaveLength(2)
    expect(request.mock.calls.map(([path]) => path)).toEqual([
      '/game-sessions/session-a/ws-ticket',
      '/game-sessions/session-b/ws-ticket',
    ])
    view.unmount()
    expect(sockets[1]?.readyState).toBe(WebSocket.CLOSED)
    expect(vi.getTimerCount()).toBe(0)
  })
})

it('parses only the documented realtime protocol', () => {
  expect(parseRealtimeEvent(new Blob())).toBeNull()
  expect(parseRealtimeEvent(JSON.stringify({ type: 'heartbeat' }))).toBeNull()
  expect(
    parseRealtimeEvent(
      JSON.stringify({ type: 'session.updated', session_id: 123, timestamp: 'now' }),
    ),
  ).toBeNull()
})

function createHarness(
  overrides: Partial<{
    channel: { type: 'sessions' } | { type: 'session'; sessionId: string }
    connectionTimeoutMs: number
    fallbackIntervalMs: number
    random: () => number
    reconnectMaxDelayMs: number
  }> = {},
) {
  const request = ticketRequestMock().mockResolvedValue({ ticket: 'ticket-1', expires_in: 30 })
  const sockets: FakeWebSocket[] = []
  const onEvent = vi.fn<(event: RealtimeUpdateEvent) => void>()
  const synchronize = vi.fn<(signal: AbortSignal) => void | Promise<void>>()
  const fallback = vi.fn<(signal: AbortSignal) => void | Promise<void>>()
  const client = new RealtimeClient({
    apiClient: { request },
    channel: overrides.channel ?? { type: 'sessions' },
    connectionTimeoutMs: overrides.connectionTimeoutMs,
    onEvent,
    onSynchronize: synchronize,
    onFallbackRefetch: fallback,
    fallbackIntervalMs: overrides.fallbackIntervalMs,
    reconnectBaseDelayMs: 1_000,
    reconnectMaxDelayMs: overrides.reconnectMaxDelayMs,
    random: overrides.random,
    createWebSocket: (url, protocols) => {
      const socket = new FakeWebSocket(url, protocols)
      sockets.push(socket)
      return socket as unknown as WebSocket
    },
    environment: {
      window: {
        addEventListener: window.addEventListener.bind(window),
        removeEventListener: window.removeEventListener.bind(window),
        location: { protocol: 'http:', host: 'localhost:3000' } as Location,
      },
      document,
      navigator,
    },
  })
  return { client, fallback, onEvent, request, sockets, synchronize }
}

class FakeWebSocket {
  readonly url: string
  readonly protocols: string[]
  protocol = ''
  readyState: number = WebSocket.CONNECTING
  closedWith: number | null = null
  onopen: WebSocket['onopen'] = null
  onmessage: WebSocket['onmessage'] = null
  onerror: WebSocket['onerror'] = null
  onclose: WebSocket['onclose'] = null

  constructor(url: string, protocols: string[]) {
    this.url = url
    this.protocols = protocols
  }

  open(protocol = 'pingstats.v1'): void {
    this.protocol = protocol
    this.readyState = WebSocket.OPEN
    this.onopen?.call(this as unknown as WebSocket, new Event('open'))
  }

  message(data: string): void {
    this.onmessage?.call(this as unknown as WebSocket, new MessageEvent('message', { data }))
  }

  serverClose(code: number): void {
    this.readyState = WebSocket.CLOSED
    this.onclose?.call(this as unknown as WebSocket, new CloseEvent('close', { code }))
  }

  close(code = 1000): void {
    this.closedWith = code
    this.readyState = WebSocket.CLOSED
    this.onclose?.call(this as unknown as WebSocket, new CloseEvent('close', { code }))
  }
}

async function flushPromises(): Promise<void> {
  await Promise.resolve()
  await Promise.resolve()
}

function setVisibility(value: DocumentVisibilityState): void {
  Object.defineProperty(document, 'visibilityState', { configurable: true, value })
}

function setOnline(value: boolean): void {
  Object.defineProperty(navigator, 'onLine', { configurable: true, value })
}

function ticketRequestMock() {
  return vi.fn<
    (
      path: string,
      options: { readonly method: 'POST'; readonly signal: AbortSignal },
    ) => Promise<WebSocketTicketResponse>
  >()
}

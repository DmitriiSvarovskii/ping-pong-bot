import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { act, render, waitFor } from '@testing-library/react'

import { ApiClient } from '../api/client'
import { sessionKeys } from '../hooks/useGameSession'
import type { RealtimeUpdateEvent } from './types'
import { useSessionRealtime } from './useSessionRealtime'

interface CapturedOptions {
  enabled: boolean
  channel: { type: string; sessionId?: string }
  onEvent(event: RealtimeUpdateEvent): void
  onSynchronize(signal: AbortSignal): void | Promise<void>
  onFallbackRefetch(signal: AbortSignal): void | Promise<void>
}

const auth = vi.hoisted(() => ({
  apiClient: null as ApiClient | null,
  status: 'authenticated',
}))
const realtime = vi.hoisted(() => ({ options: null as CapturedOptions | null }))

vi.mock('../auth/AuthProvider', () => ({
  useAuth: () => ({ apiClient: auth.apiClient as ApiClient, status: auth.status }),
}))
vi.mock('./useRealtimeChannel', () => ({
  useRealtimeChannel: (options: CapturedOptions) => {
    realtime.options = options
  },
}))

const SESSION_ID = '11111111-1111-4111-8111-111111111111'
const OTHER_SESSION_ID = '22222222-2222-4222-8222-222222222222'
type QueryName = 'detail' | 'participants' | 'queue' | 'current' | 'matches' | 'statistics'

beforeEach(() => {
  auth.apiClient = new ApiClient()
  auth.status = 'authenticated'
  realtime.options = null
})

describe('useSessionRealtime', () => {
  it('subscribes only after authentication and with a non-empty session ID', async () => {
    const counts = emptyCounts()
    const view = renderHarness(SESSION_ID, counts)
    await waitForInitialQueries(counts)

    expect(realtime.options?.enabled).toBe(true)
    expect(realtime.options?.channel).toEqual({ type: 'session', sessionId: SESSION_ID })

    auth.status = 'error'
    view.rerender(tree(view.client, SESSION_ID, counts))
    expect(realtime.options?.enabled).toBe(false)

    auth.status = 'authenticated'
    view.rerender(tree(view.client, '', counts))
    expect(realtime.options?.enabled).toBe(false)
  })

  it('refreshes all six session projections for session.updated', async () => {
    const counts = emptyCounts()
    renderHarness(SESSION_ID, counts)
    await waitForInitialQueries(counts)

    act(() => realtime.options?.onEvent(sessionUpdated(SESSION_ID)))
    await waitFor(() => expect(counts).toEqual(allCounts(2)))

    act(() => realtime.options?.onEvent(sessionUpdated(OTHER_SESSION_ID)))
    await Promise.resolve()
    expect(counts).toEqual(allCounts(2))
  })

  it('performs a full refresh after reconnect and through REST fallback', async () => {
    const counts = emptyCounts()
    renderHarness(SESSION_ID, counts)
    await waitForInitialQueries(counts)

    await act(async () => {
      await realtime.options?.onSynchronize(new AbortController().signal)
    })
    expect(counts).toEqual(allCounts(2))

    await act(async () => {
      await realtime.options?.onFallbackRefetch(new AbortController().signal)
    })
    expect(counts).toEqual(allCounts(3))
  })

  it('coalesces concurrent events into one REST refresh', async () => {
    const counts = emptyCounts()
    let release: (() => void) | null = null
    let blocked = false
    const blocker = new Promise<void>((resolve) => { release = resolve })
    renderHarness(SESSION_ID, counts, async (name, signal) => {
      counts[name] += 1
      if (blocked) {
        await Promise.race([
          blocker,
          new Promise<void>((_, reject) => {
            signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), {
              once: true,
            })
          }),
        ])
      }
      return name
    })
    await waitForInitialQueries(counts)
    blocked = true

    act(() => {
      realtime.options?.onEvent(sessionUpdated(SESSION_ID))
      realtime.options?.onEvent(sessionUpdated(SESSION_ID))
    })
    await waitFor(() => expect(counts).toEqual(allCounts(2)))
    expect(counts).toEqual(allCounts(2))

    act(() => { release?.() })
  })

  it('aborts active session refetches on unmount', async () => {
    const counts = emptyCounts()
    const aborted = new Set<QueryName>()
    let blocked = false
    const view = renderHarness(SESSION_ID, counts, (name, signal) => {
      counts[name] += 1
      if (!blocked) return Promise.resolve(name)
      return new Promise<string>((_resolve, reject) => {
        signal.addEventListener('abort', () => {
          aborted.add(name)
          reject(new DOMException('Aborted', 'AbortError'))
        }, { once: true })
      })
    })
    await waitForInitialQueries(counts)
    blocked = true

    act(() => realtime.options?.onEvent(sessionUpdated(SESSION_ID)))
    await waitFor(() => expect(counts).toEqual(allCounts(2)))
    view.unmount()

    await waitFor(() => expect(aborted.size).toBe(6))
  })

  it('rebinds the channel when sessionId changes', async () => {
    const counts = emptyCounts()
    const view = renderHarness(SESSION_ID, counts)
    await waitForInitialQueries(counts)

    view.rerender(tree(view.client, OTHER_SESSION_ID, counts))
    expect(realtime.options?.channel).toEqual({ type: 'session', sessionId: OTHER_SESSION_ID })
  })
})

function SessionQueries({
  sessionId,
  counts,
  query,
}: {
  sessionId: string
  counts: Record<QueryName, number>
  query?: (name: QueryName, signal: AbortSignal) => Promise<string>
}) {
  useSessionRealtime(sessionId)
  const run = (name: QueryName, signal: AbortSignal) => {
    if (query) return query(name, signal)
    // Mutable counters are test instrumentation, not component state.
    // eslint-disable-next-line react-hooks/immutability
    counts[name] += 1
    return Promise.resolve(name)
  }
  useQuery({ queryKey: sessionKeys.detail(sessionId), queryFn: ({ signal }) => run('detail', signal) })
  useQuery({ queryKey: sessionKeys.participants(sessionId), queryFn: ({ signal }) => run('participants', signal) })
  useQuery({ queryKey: sessionKeys.queue(sessionId), queryFn: ({ signal }) => run('queue', signal) })
  useQuery({ queryKey: sessionKeys.currentMatch(sessionId), queryFn: ({ signal }) => run('current', signal) })
  useQuery({ queryKey: sessionKeys.matches(sessionId), queryFn: ({ signal }) => run('matches', signal) })
  useQuery({ queryKey: sessionKeys.statistics(sessionId), queryFn: ({ signal }) => run('statistics', signal) })
  return null
}

function renderHarness(
  sessionId: string,
  counts: Record<QueryName, number>,
  query?: (name: QueryName, signal: AbortSignal) => Promise<string>,
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return { ...render(tree(client, sessionId, counts, query)), client }
}

function tree(
  client: QueryClient,
  sessionId: string,
  counts: Record<QueryName, number>,
  query?: (name: QueryName, signal: AbortSignal) => Promise<string>,
) {
  return (
    <QueryClientProvider client={client}>
      <SessionQueries sessionId={sessionId} counts={counts} query={query} />
    </QueryClientProvider>
  )
}

function emptyCounts(): Record<QueryName, number> {
  return { detail: 0, participants: 0, queue: 0, current: 0, matches: 0, statistics: 0 }
}

function allCounts(value: number): Record<QueryName, number> {
  return { detail: value, participants: value, queue: value, current: value, matches: value, statistics: value }
}

async function waitForInitialQueries(counts: Record<QueryName, number>) {
  await waitFor(() => expect(counts).toEqual(allCounts(1)))
}

function sessionUpdated(sessionId: string): RealtimeUpdateEvent {
  return { type: 'session.updated', session_id: sessionId, timestamp: '2026-11-09T00:00:00Z' }
}

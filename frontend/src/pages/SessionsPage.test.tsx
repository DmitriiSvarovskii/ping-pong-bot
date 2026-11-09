import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { StrictMode } from 'react'
import { MemoryRouter } from 'react-router-dom'

import { ApiClient } from '../api/client'
import { sessionKeys } from '../hooks/useGameSession'
import type { RealtimeUpdateEvent } from '../realtime/types'
import type { GameSession, SessionListResponse } from '../types/gameSessions'
import { SessionsPage } from './SessionsPage'

interface CapturedRealtimeOptions {
  enabled: boolean
  channel: { type: string }
  onEvent(event: RealtimeUpdateEvent): void
  onSynchronize(signal: AbortSignal): void | Promise<void>
  onFallbackRefetch(signal: AbortSignal): void | Promise<void>
}

const auth = vi.hoisted(() => ({
  apiClient: null as ApiClient | null,
  status: 'authenticated',
  userId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
}))
const realtime = vi.hoisted(() => ({ options: null as CapturedRealtimeOptions | null }))
let observers: MockIntersectionObserver[] = []

vi.mock('../auth/AuthProvider', () => ({
  useAuth: () => ({
    apiClient: auth.apiClient as ApiClient,
    status: auth.status,
    user: { id: auth.userId },
  }),
}))
vi.mock('../realtime/useRealtimeChannel', () => ({
  useRealtimeChannel: (options: CapturedRealtimeOptions) => {
    realtime.options = options
  },
}))

beforeEach(() => {
  auth.apiClient = new ApiClient()
  auth.status = 'authenticated'
  realtime.options = null
  observers = []
  vi.stubGlobal('IntersectionObserver', MockIntersectionObserver)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('SessionsPage infinite history', () => {
  it('loads only the initial page and subscribes after authentication', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(() =>
      Promise.resolve(jsonResponse(listResponse([session(0)], 1, 0))),
    )
    const view = renderPage()

    expect(await screen.findByText('Игра 0')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledOnce()
    expect(fetchMock.mock.calls[0]?.[0]).toContain('/game-sessions?limit=20&offset=0')
    expect(screen.getByText('Вся история загружена')).toBeInTheDocument()
    expect(realtime.options?.channel).toEqual({ type: 'sessions' })
    expect(realtime.options?.enabled).toBe(true)

    auth.status = 'error'
    view.rerender(pageTree(view.client))
    expect(realtime.options?.enabled).toBe(false)
  })

  it('loads the next page near the list end and appends it', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse(listResponse(sessionRange(0, 20), 21, 0)))
      .mockResolvedValueOnce(jsonResponse(listResponse([session(20)], 21, 20)))
    renderPage()
    await screen.findByText('Игра 0')

    await intersectLatestObserver()

    expect(await screen.findByText('Игра 20')).toBeInTheDocument()
    expect(screen.getByText('Игра 0')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(fetchMock.mock.calls[1]?.[0]).toContain('/game-sessions?limit=20&offset=20')
    expect(screen.getByText('Вся история загружена')).toBeInTheDocument()
  })

  it('deduplicates a session returned on adjacent pages', async () => {
    const duplicate = session(19)
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse(listResponse(sessionRange(0, 20), 22, 0)))
      .mockResolvedValueOnce(jsonResponse(listResponse([duplicate, session(20)], 22, 20)))
    renderPage()
    await screen.findByText('Игра 0')

    await intersectLatestObserver()

    expect(await screen.findByText('Игра 20')).toBeInTheDocument()
    expect(screen.getAllByText('Игра 19')).toHaveLength(1)
  })

  it('does not observe or request another page after reaching the end', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(() =>
      Promise.resolve(jsonResponse(listResponse([session(0)], 1, 0))),
    )
    renderPage()

    expect(await screen.findByText('Вся история загружена')).toBeInTheDocument()
    expect(observers).toHaveLength(0)
    expect(fetchMock).toHaveBeenCalledOnce()
  })

  it('keeps loaded sessions after a next-page error and retries it', async () => {
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse(listResponse(sessionRange(0, 20), 21, 0)))
      .mockResolvedValueOnce(jsonResponse({ error: { message: 'Unavailable' } }, 503))
      .mockResolvedValueOnce(jsonResponse(listResponse([session(20)], 21, 20)))
    renderPage()
    await screen.findByText('Игра 0')

    await intersectLatestObserver()

    expect(await screen.findByText('Не удалось загрузить следующую часть истории.'))
      .toBeInTheDocument()
    expect(screen.getByText('Игра 0')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Повторить' }))

    expect(await screen.findByText('Игра 20')).toBeInTheDocument()
    expect(screen.queryByText('Не удалось загрузить следующую часть истории.'))
      .not.toBeInTheDocument()
  })

  it('coalesces repeated intersections while the next page is loading', async () => {
    let resolveNext: ((response: Response) => void) | null = null
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse(listResponse(sessionRange(0, 20), 21, 0)))
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveNext = resolve }))
    renderPage()
    await screen.findByText('Игра 0')

    const observer = latestObserver()
    act(() => {
      observer.intersect()
      observer.intersect()
    })
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    expect(fetchMock).toHaveBeenCalledTimes(2)

    act(() => resolveNext?.(jsonResponse(listResponse([session(20)], 21, 20))))
    expect(await screen.findByText('Игра 20')).toBeInTheDocument()
  })

  it('disconnects the observer when the page unmounts', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse(listResponse(sessionRange(0, 20), 21, 0)),
    )
    const view = renderPage()
    await screen.findByText('Игра 0')
    const observer = latestObserver()

    view.unmount()

    expect(observer.disconnect).toHaveBeenCalledOnce()
  })

  it('starts from a legacy offset link without rewriting its URL', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse(listResponse([session(20)], 21, 20)),
    )
    const client = createQueryClient()
    render(pageTree(client, '/games?offset=20'))

    expect(await screen.findByText('Игра 20')).toBeInTheDocument()
    expect(fetchMock.mock.calls[0]?.[0]).toContain('/game-sessions?limit=20&offset=20')
    expect(screen.getByText('Вся история загружена')).toBeInTheDocument()
  })

  it('keeps all loaded pages in the TanStack cache across a page remount', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse(listResponse(sessionRange(0, 20), 21, 0)))
      .mockResolvedValueOnce(jsonResponse(listResponse([session(20)], 21, 20)))
      .mockImplementation(() => new Promise<Response>(() => undefined))
    const client = createQueryClient()
    const first = render(pageTree(client))
    await screen.findByText('Игра 0')
    await intersectLatestObserver()
    expect(await screen.findByText('Игра 20')).toBeInTheDocument()
    first.unmount()
    expect(client.getQueryCache().find({
      queryKey: sessionKeys.list(0, auth.userId),
    })?.options.gcTime)
      .toBe(Infinity)

    render(pageTree(client))

    expect(screen.getByText('Игра 0')).toBeInTheDocument()
    expect(screen.getByText('Игра 20')).toBeInTheDocument()
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(2)
  })

  it('refreshes all loaded pages after sessions.updated without duplicating items', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse(listResponse(sessionRange(0, 20), 21, 0)))
      .mockResolvedValueOnce(jsonResponse(listResponse([session(20)], 21, 20)))
      .mockResolvedValueOnce(jsonResponse(listResponse([
        session(0, { name: 'Обновлённая игра' }),
        ...sessionRange(1, 19),
      ], 21, 0)))
      .mockResolvedValueOnce(jsonResponse(listResponse([session(20)], 21, 20)))
    renderPage()
    await screen.findByText('Игра 0')
    await intersectLatestObserver()
    await screen.findByText('Игра 20')

    act(() => realtime.options?.onEvent(updatedEvent()))

    expect(await screen.findByText('Обновлённая игра')).toBeInTheDocument()
    expect(screen.getAllByText('Игра 20')).toHaveLength(1)
    expect(fetchMock).toHaveBeenCalledTimes(4)
  })

  it('coalesces concurrent realtime, reconnect, and fallback synchronization', async () => {
    let resolveRefresh: ((response: Response) => void) | null = null
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse(listResponse([session(0)], 1, 0)))
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveRefresh = resolve }))
    renderPage()
    await screen.findByText('Игра 0')

    act(() => {
      realtime.options?.onEvent(updatedEvent())
      void realtime.options?.onSynchronize(new AbortController().signal)
      void realtime.options?.onFallbackRefetch(new AbortController().signal)
    })
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))

    act(() => resolveRefresh?.(jsonResponse(listResponse([
      session(0, { name: 'После синхронизации' }),
    ], 1, 0))))
    expect(await screen.findByText('После синхронизации')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('aborts active synchronization and remains safe in React Strict Mode', async () => {
    let refreshAborted = false
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(() =>
      Promise.resolve(jsonResponse(listResponse([session(0)], 1, 0))),
    )
    const client = createQueryClient()
    const view = render(<StrictMode>{pageTree(client)}</StrictMode>)
    await screen.findByText('Игра 0')
    fetchMock.mockImplementation((_input, init) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => {
        refreshAborted = true
        reject(new DOMException('Aborted', 'AbortError'))
      })
    }))
    fetchMock.mockClear()

    act(() => realtime.options?.onEvent(updatedEvent()))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce())
    view.unmount()

    await waitFor(() => expect(refreshAborted).toBe(true))
  })
})

class MockIntersectionObserver {
  readonly root = null
  readonly rootMargin: string
  readonly thresholds = [0]
  readonly observe = vi.fn((target: Element) => { this.target = target })
  readonly unobserve = vi.fn()
  readonly disconnect = vi.fn()
  readonly takeRecords = vi.fn(() => [])
  private target: Element | null = null

  constructor(
    private readonly callback: IntersectionObserverCallback,
    options?: IntersectionObserverInit,
  ) {
    this.rootMargin = options?.rootMargin ?? '0px'
    observers.push(this)
  }

  intersect() {
    if (!this.target) throw new Error('Observer target is unavailable')
    this.callback(
      [{ isIntersecting: true, target: this.target } as IntersectionObserverEntry],
      this as unknown as IntersectionObserver,
    )
  }
}

function intersectLatestObserver(): Promise<void> {
  act(() => {
    latestObserver().intersect()
  })
  return Promise.resolve()
}

function latestObserver(): MockIntersectionObserver {
  const observer = observers.at(-1)
  if (!observer) throw new Error('IntersectionObserver was not created')
  return observer
}

function renderPage() {
  const client = createQueryClient()
  return { ...render(pageTree(client)), client }
}

function pageTree(client: QueryClient, initialEntry = '/games') {
  return (
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialEntry]}><SessionsPage /></MemoryRouter>
    </QueryClientProvider>
  )
}

function createQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } })
}

function updatedEvent(): RealtimeUpdateEvent {
  return { type: 'sessions.updated', timestamp: '2026-11-09T00:00:00Z' }
}

function listResponse(
  items: GameSession[],
  total = items.length,
  offset = 0,
): SessionListResponse {
  return { items, total, limit: 20, offset }
}

function sessionRange(start: number, count: number): GameSession[] {
  return Array.from({ length: count }, (_, index) => session(start + index))
}

function session(index: number, overrides: Partial<GameSession> = {}): GameSession {
  const suffix = index.toString(16).padStart(12, '0')
  const user = {
    id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    first_name: 'Анна',
    last_name: null,
    username: 'anna',
    photo_url: null,
    created_at: '2026-11-07T10:00:00Z',
    last_active_at: null,
  }
  return {
    id: `11111111-1111-4111-8111-${suffix}`,
    organizer_id: user.id,
    status: 'completed',
    name: `Игра ${index}`,
    points_to_win: 11,
    sets_to_win: 1,
    is_ranked: true,
    created_at: '2026-11-07T10:00:00Z',
    started_at: '2026-11-07T10:00:00Z',
    completed_at: '2026-11-07T11:00:00Z',
    version: 1,
    active_participants: [
      {
        participation_id: `participant-${index}`,
        user,
        joined_at: '2026-11-07T10:00:00Z',
        left_at: null,
        is_active: true,
      },
    ],
    ...overrides,
  }
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

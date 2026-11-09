import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { StrictMode } from 'react'
import { MemoryRouter } from 'react-router-dom'

import { ApiClient } from '../api/client'
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
}))
const realtime = vi.hoisted(() => ({ options: null as CapturedRealtimeOptions | null }))

vi.mock('../auth/AuthProvider', () => ({
  useAuth: () => ({ apiClient: auth.apiClient as ApiClient, status: auth.status }),
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
})

describe('SessionsPage realtime', () => {
  it('subscribes to the sessions channel only when authentication is ready', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(listResponse([session()])))
    const view = renderPage()

    expect(await screen.findByText('Тестовая игра')).toBeInTheDocument()
    expect(realtime.options?.channel).toEqual({ type: 'sessions' })
    expect(realtime.options?.enabled).toBe(true)

    auth.status = 'error'
    view.rerender(pageTree(view.client))
    expect(realtime.options?.enabled).toBe(false)
  })

  it('invalidates and refreshes the visible list after sessions.updated', async () => {
    let requestCount = 0
    vi.spyOn(globalThis, 'fetch').mockImplementation(() => {
      requestCount += 1
      const name = requestCount === 1 ? 'До обновления' : 'После обновления'
      return Promise.resolve(jsonResponse(listResponse([session({ name })])))
    })
    renderPage()

    expect(await screen.findByText('До обновления')).toBeInTheDocument()
    act(() => realtime.options?.onEvent(updatedEvent()))

    expect(await screen.findByText('После обновления')).toBeInTheDocument()
    expect(requestCount).toBe(2)
  })

  it('synchronizes through reconnect and REST fallback callbacks', async () => {
    let requestCount = 0
    vi.spyOn(globalThis, 'fetch').mockImplementation(() => {
      requestCount += 1
      return Promise.resolve(
        jsonResponse(listResponse([session({ name: `Версия ${requestCount}` })])),
      )
    })
    renderPage()

    expect(await screen.findByText('Версия 1')).toBeInTheDocument()
    await act(async () => {
      await realtime.options?.onSynchronize(new AbortController().signal)
    })
    expect(await screen.findByText('Версия 2')).toBeInTheDocument()

    await act(async () => {
      await realtime.options?.onFallbackRefetch(new AbortController().signal)
    })
    expect(await screen.findByText('Версия 3')).toBeInTheDocument()
  })

  it('keeps the current page and cached content during a background refresh', async () => {
    let resolveRefresh: ((response: Response) => void) | null = null
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse(listResponse([session()], 21, 0)))
      .mockResolvedValueOnce(jsonResponse(listResponse([session({ name: 'Страница 2' })], 21, 20)))
      .mockImplementationOnce(
        () => new Promise<Response>((resolve) => { resolveRefresh = resolve }),
      )
    renderPage()

    await screen.findByText('Тестовая игра')
    await userEvent.click(screen.getByRole('button', { name: 'Далее' }))
    expect(await screen.findByText('Страница 2')).toBeInTheDocument()
    expect(screen.getByText('21–21 из 21')).toBeInTheDocument()

    act(() => realtime.options?.onEvent(updatedEvent()))
    await waitFor(() => expect(resolveRefresh).not.toBeNull())
    expect(screen.getByText('Страница 2')).toBeInTheDocument()
    expect(screen.getByText('21–21 из 21')).toBeInTheDocument()
    expect(screen.queryByText('Загружаем игру…')).not.toBeInTheDocument()

    act(() => {
      resolveRefresh?.(jsonResponse(listResponse([session({ name: 'Обновлённая страница 2' })], 21, 20)))
    })
    expect(await screen.findByText('Обновлённая страница 2')).toBeInTheDocument()
  })

  it('coalesces concurrent synchronization requests', async () => {
    let resolveRefresh: ((response: Response) => void) | null = null
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse(listResponse([session()])))
      .mockImplementationOnce(
        () => new Promise<Response>((resolve) => { resolveRefresh = resolve }),
      )
    renderPage()
    await screen.findByText('Тестовая игра')

    act(() => {
      realtime.options?.onEvent(updatedEvent())
      realtime.options?.onEvent(updatedEvent())
    })
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    expect(fetchMock).toHaveBeenCalledTimes(2)

    act(() => {
      resolveRefresh?.(jsonResponse(listResponse([session({ name: 'Готово' })])))
    })
    expect(await screen.findByText('Готово')).toBeInTheDocument()
  })

  it('aborts an active page refresh when the page unmounts', async () => {
    let refreshAborted = false
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse(listResponse([session()])))
      .mockImplementationOnce((_input, init) => new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => {
          refreshAborted = true
          reject(new DOMException('Aborted', 'AbortError'))
        })
      }))
    const view = renderPage()
    await screen.findByText('Тестовая игра')
    act(() => realtime.options?.onEvent(updatedEvent()))
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledTimes(2))
    view.unmount()

    await waitFor(() => expect(refreshAborted).toBe(true))
  })

  it('remains usable in React Strict Mode', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(() =>
      Promise.resolve(jsonResponse(listResponse([session()]))),
    )
    const client = createQueryClient()
    render(<StrictMode>{pageTree(client)}</StrictMode>)

    expect(await screen.findByText('Тестовая игра')).toBeInTheDocument()
    expect(realtime.options?.channel).toEqual({ type: 'sessions' })
    expect(realtime.options?.enabled).toBe(true)
  })
})

function renderPage() {
  const client = createQueryClient()
  return { ...render(pageTree(client)), client }
}

function pageTree(client: QueryClient) {
  return (
    <QueryClientProvider client={client}>
      <MemoryRouter><SessionsPage /></MemoryRouter>
    </QueryClientProvider>
  )
}

function createQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } })
}

function updatedEvent(): RealtimeUpdateEvent {
  return { type: 'sessions.updated', timestamp: '2026-11-09T00:00:00Z' }
}

function listResponse(items: GameSession[], total = items.length, offset = 0): SessionListResponse {
  return { items, total, limit: 20, offset }
}

function session(overrides: Partial<GameSession> = {}): GameSession {
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
    id: '11111111-1111-4111-8111-111111111111',
    organizer_id: user.id,
    status: 'pending',
    name: 'Тестовая игра',
    points_to_win: 11,
    sets_to_win: 1,
    is_ranked: true,
    created_at: '2026-11-07T10:00:00Z',
    started_at: null,
    completed_at: null,
    version: 1,
    active_participants: [
      { participation_id: 'participant-a', user, joined_at: '2026-11-07T10:00:00Z', left_at: null, is_active: true },
    ],
    ...overrides,
  }
}

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

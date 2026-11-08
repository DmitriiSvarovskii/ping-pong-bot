import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'

import { ApiClient } from '../api/client'
import { SessionQueuePanel } from '../components/SessionQueuePanel'
import { sessionKeys } from '../hooks/useGameSession'
import type { CurrentUser } from '../types/api'
import type {
  GameSession,
  SessionMatch,
  SessionMatchCommandResponse,
  SessionParticipant,
  SessionQueue,
} from '../types/gameSessions'
import { CreateSessionPage } from './CreateSessionPage'
import { ResultPage } from './ResultPage'
import { SessionInvitePage } from './SessionInvitePage'
import { SessionPage } from './SessionPage'

const auth = vi.hoisted(() => ({
  apiClient: null as ApiClient | null,
  user: null as CurrentUser | null,
}))

vi.mock('../auth/AuthProvider', () => ({
  useAuth: () => ({ apiClient: auth.apiClient as ApiClient, user: auth.user }),
}))

const SESSION_ID = '11111111-1111-4111-8111-111111111111'
const MATCH_ID = '22222222-2222-4222-8222-222222222222'
const NEXT_MATCH_ID = '33333333-3333-4333-8333-333333333333'
const USER_A = user('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Анна')
const USER_B = user('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'Борис')
const USER_C = user('cccccccc-cccc-4ccc-8ccc-cccccccccccc', 'Саша')
const PARTICIPANTS = [participant('pa', USER_A), participant('pb', USER_B), participant('pc', USER_C)]

beforeEach(() => {
  delete window.Telegram
  auth.apiClient = new ApiClient()
  auth.user = { ...USER_A, telegram_id: 1, updated_at: '2026-11-07T10:00:00Z' }
})

describe('game sessions frontend', () => {
  it('creates a session with backend defaults and navigates to it', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(session()))
    renderRoute(<CreateSessionPage />, '/games/create', '/games/create')
    await userEvent.type(screen.getByLabelText('Название игры'), 'Вечерняя игра')
    await userEvent.click(screen.getByRole('button', { name: 'Создать игру' }))

    await screen.findByText(`/games/${SESSION_ID}`)
    const [, request] = fetchMock.mock.calls[0]!
    expect(JSON.parse(typeof request?.body === 'string' ? request.body : '')).toEqual({
      name: 'Вечерняя игра',
      points_to_win: 11,
      sets_to_win: 1,
      is_ranked: true,
    })
  })

  it('renders the current pair and ordered waiting queue', () => {
    const queue: SessionQueue = {
      current_pair: [queueItem('pa', USER_A.id), queueItem('pb', USER_B.id)],
      waiting: [queueItem('pc', USER_C.id)],
      is_paused: false,
    }
    render(<SessionQueuePanel queue={queue} participants={PARTICIPANTS} />)
    expect(screen.getByText('Анна')).toBeInTheDocument()
    expect(screen.getByText('Борис')).toBeInTheDocument()
    expect(screen.getByText('Саша')).toBeInTheDocument()
    expect(screen.getByText('Следующий')).toBeInTheDocument()
  })

  it('records a full result and displays the server-selected winner', async () => {
    mockResultFlow(commandResponse(match({ status: 'completed', winnerId: USER_A.id }), nextMatch()))
    renderRoute(<ResultPage />, `/games/${SESSION_ID}/result`, '/games/:sessionId/result')
    const score = await screen.findByLabelText('Счёт Анна')
    await userEvent.clear(score)
    await userEvent.type(score, '11')
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить результат' }))
    expect(await screen.findByRole('heading', { name: 'Анна победил' })).toBeInTheDocument()
  })

  it('invalidates session statistics after recording a result', async () => {
    mockResultFlow(commandResponse(match({ status: 'completed', winnerId: USER_A.id }), nextMatch()))
    const { client } = renderRoute(<ResultPage />, `/games/${SESSION_ID}/result`, '/games/:sessionId/result')
    client.setQueryData(sessionKeys.statistics(SESSION_ID), { items: [] })
    const score = await screen.findByLabelText('Счёт Анна')
    await userEvent.clear(score)
    await userEvent.type(score, '11')
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить результат' }))

    await waitFor(() => expect(client.getQueryState(sessionKeys.statistics(SESSION_ID))?.isInvalidated).toBe(true))
  })

  it('clears an initial zero on focus and accepts only numeric integer input', async () => {
    mockResultFlow(commandResponse(match({ status: 'completed', winnerId: USER_A.id }), nextMatch()))
    renderRoute(<ResultPage />, `/games/${SESSION_ID}/result`, '/games/:sessionId/result')
    const score = await screen.findByLabelText('Счёт Анна')

    expect(score).toHaveAttribute('inputmode', 'numeric')
    await userEvent.click(score)
    expect(score).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Сохранить результат' })).toBeDisabled()

    await userEvent.type(score, '-1.5')
    expect(score).toHaveValue('15')
    await userEvent.clear(score)
    await userEvent.type(score, '11')
    expect(score).toHaveValue('11')
    expect(screen.getByRole('button', { name: 'Сохранить результат' })).toBeEnabled()
  })

  it('returns through Telegram Back Button without saving or cancelling the match', async () => {
    let telegramBack: (() => void) | undefined
    const offClick = vi.fn()
    const hide = vi.fn()
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      if (requestUrl(input).includes('/matches/current')) return Promise.resolve(jsonResponse(match()))
      return Promise.resolve(jsonResponse({ items: [], total: 0, limit: 100, offset: 0 }))
    })
    window.Telegram = {
      WebApp: {
        initData: 'signed',
        colorScheme: 'light',
        platform: 'ios',
        version: '9.0',
        ready: vi.fn(),
        expand: vi.fn(),
        BackButton: {
          show: vi.fn(),
          hide,
          onClick: vi.fn((callback: () => void) => { telegramBack = callback }),
          offClick,
        },
      },
    }
    renderRoute(<ResultPage />, `/games/${SESSION_ID}/result`, '/games/:sessionId/result')
    await screen.findByText('Записать результат')

    act(() => telegramBack?.())

    expect(await screen.findByText(`/games/${SESSION_ID}`)).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([input, request]) => request?.method === 'PUT' || requestUrl(input).includes('/cancel'))).toBe(false)
    expect(offClick).toHaveBeenCalledWith(expect.any(Function))
    expect(hide).toHaveBeenCalledOnce()
  })

  it('renders result entry safely without the Telegram API', async () => {
    delete window.Telegram
    mockResultFlow(commandResponse(match({ status: 'completed', winnerId: USER_A.id }), nextMatch()))
    renderRoute(<ResultPage />, `/games/${SESSION_ID}/result`, '/games/:sessionId/result')
    expect(await screen.findByText('Записать результат')).toBeInTheDocument()
  })

  it('shows the next pair returned by backend after rotation', async () => {
    mockResultFlow(commandResponse(match({ status: 'completed', winnerId: USER_A.id }), nextMatch()))
    renderRoute(<ResultPage />, `/games/${SESSION_ID}/result`, '/games/:sessionId/result')
    const score = await screen.findByLabelText('Счёт Анна')
    await userEvent.clear(score)
    await userEvent.type(score, '11')
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить результат' }))
    expect(await screen.findByText('Анна — Саша')).toBeInTheDocument()
    expect(screen.getByText('Следующий матч #2')).toBeInTheDocument()
  })

  it('does not resubmit an old score after a stale-match conflict', async () => {
    let putCount = 0
    vi.spyOn(globalThis, 'fetch').mockImplementation((input, request) => {
      const url = requestUrl(input)
      if (request?.method === 'PUT') {
        putCount += 1
        return Promise.resolve(jsonResponse({ error: { code: 'match_version_conflict', message: 'Conflict', details: {} } }, 409))
      }
      if (url.includes('/matches/current')) return Promise.resolve(jsonResponse(match()))
      return Promise.resolve(jsonResponse({ items: [], total: 0, limit: 100, offset: 0 }))
    })
    renderRoute(<ResultPage />, `/games/${SESSION_ID}/result`, '/games/:sessionId/result')
    await screen.findByText('Записать результат')
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить результат' }))
    expect(await screen.findByText('Матч уже изменился')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Сохранить результат' })).toBeDisabled()
    expect(putCount).toBe(1)
  })

  it('joins through an invitation route before opening the protected session', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(session()))
    renderRoute(<SessionInvitePage />, `/games/${SESSION_ID}/invite`, '/games/:sessionId/invite')
    await userEvent.click(screen.getByRole('button', { name: 'Присоединиться' }))
    await screen.findByText(`/games/${SESSION_ID}`)
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/game-sessions/${SESSION_ID}/join`,
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('requires confirmation before completing the whole session', async () => {
    let completeCalls = 0
    vi.spyOn(globalThis, 'fetch').mockImplementation((input, request) => {
      const url = requestUrl(input)
      if (url.endsWith(`/${SESSION_ID}/complete`) && request?.method === 'POST') {
        completeCalls += 1
        return Promise.resolve(jsonResponse(session({ status: 'completed' })))
      }
      if (url.endsWith(`/game-sessions/${SESSION_ID}`)) return Promise.resolve(jsonResponse(session({ status: 'active' })))
      if (url.includes('/participants')) return Promise.resolve(jsonResponse({ items: PARTICIPANTS, total: 3, limit: 100, offset: 0 }))
      if (url.includes('/queue')) return Promise.resolve(jsonResponse({ current_pair: [queueItem('pa', USER_A.id), queueItem('pb', USER_B.id)], waiting: [queueItem('pc', USER_C.id)], is_paused: false }))
      if (url.includes('/matches/current')) return Promise.resolve(jsonResponse(match()))
      if (url.includes('/matches')) return Promise.resolve(jsonResponse({ items: [], total: 0, limit: 100, offset: 0 }))
      return Promise.resolve(jsonResponse({}))
    })
    renderRoute(<SessionPage />, `/games/${SESSION_ID}`, '/games/:sessionId')
    const initial = await screen.findByRole('button', { name: 'Завершить всю сессию' })
    await userEvent.click(initial)
    expect(completeCalls).toBe(0)
    await userEvent.click(screen.getByRole('button', { name: 'Подтвердить' }))
    await waitFor(() => expect(completeCalls).toBe(1))
  })

  it('opens the skip modal and refreshes the current pair after success', async () => {
    let current = match()
    const queueBefore = { current_pair: [queueItem('pa', USER_A.id), queueItem('pb', USER_B.id)], waiting: [queueItem('pc', USER_C.id)], is_paused: false }
    const queueAfter = { current_pair: [queueItem('pb', USER_B.id), queueItem('pc', USER_C.id)], waiting: [queueItem('pa', USER_A.id)], is_paused: false }
    let statisticsCalls = 0
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation((input, request) => {
      const url = requestUrl(input)
      if (url.endsWith('/matches/current/skip') && request?.method === 'POST') {
        current = { ...nextMatch(), participants: [{ position: 1, user: USER_B }, { position: 2, user: USER_C }] }
        return Promise.resolve(jsonResponse({ match: match({ status: 'cancelled' }), current_match: current, queue: queueAfter }))
      }
      if (url.endsWith(`/game-sessions/${SESSION_ID}`)) return Promise.resolve(jsonResponse(session({ status: 'active' })))
      if (url.includes('/participants')) return Promise.resolve(jsonResponse({ items: PARTICIPANTS, total: 3, limit: 100, offset: 0 }))
      if (url.includes('/queue')) return Promise.resolve(jsonResponse(current.sequence_number === 1 ? queueBefore : queueAfter))
      if (url.includes('/matches/current')) return Promise.resolve(jsonResponse(current))
      if (url.includes('/matches')) return Promise.resolve(jsonResponse({ items: [], total: 0, limit: 100, offset: 0 }))
      if (url.includes('/statistics')) {
        statisticsCalls += 1
        return Promise.resolve(jsonResponse({ items: [{ user: USER_A, wins: 3, losses: 1, points_for: 50, points_against: 33, point_difference: 17 }] }))
      }
      return Promise.resolve(jsonResponse({}))
    })
    renderRoute(<SessionPage />, `/games/${SESSION_ID}`, '/games/:sessionId')
    await userEvent.click(await screen.findByRole('button', { name: 'Пропустить' }))
    const dialog = screen.getByRole('dialog', { name: 'Кого пропустить?' })
    expect(within(dialog).getByRole('button', { name: 'Борис' })).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Обоих игроков' })).toBeInTheDocument()

    await userEvent.click(within(dialog).getByRole('button', { name: 'Анна' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(await screen.findByText('#2')).toBeInTheDocument()
    const skipCall = fetchMock.mock.calls.find(([input]) => requestUrl(input).endsWith('/matches/current/skip'))
    const skipBody = skipCall?.[1]?.body
    expect(JSON.parse(typeof skipBody === 'string' ? skipBody : '')).toEqual({
      match_id: MATCH_ID,
      skipped_player_id: USER_A.id,
      skip_both: false,
    })
    await waitFor(() => expect(statisticsCalls).toBeGreaterThan(1))
    expect(screen.getByText('+17')).toBeInTheDocument()
  })

  it('shows a retry state when session statistics fail to load', async () => {
    let statisticsCalls = 0
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      const url = requestUrl(input)
      if (url.endsWith(`/game-sessions/${SESSION_ID}`)) {
        return Promise.resolve(jsonResponse(session({ status: 'active' })))
      }
      if (url.includes('/participants')) {
        return Promise.resolve(
          jsonResponse({ items: PARTICIPANTS, total: 3, limit: 100, offset: 0 }),
        )
      }
      if (url.includes('/queue')) {
        return Promise.resolve(
          jsonResponse({
            current_pair: [queueItem('pa', USER_A.id), queueItem('pb', USER_B.id)],
            waiting: [queueItem('pc', USER_C.id)],
            is_paused: false,
          }),
        )
      }
      if (url.includes('/matches/current')) return Promise.resolve(jsonResponse(match()))
      if (url.includes('/matches')) {
        return Promise.resolve(jsonResponse({ items: [], total: 0, limit: 100, offset: 0 }))
      }
      if (url.includes('/statistics')) {
        statisticsCalls += 1
        if (statisticsCalls === 1) {
          return Promise.resolve(
            jsonResponse(
              { error: { code: 'service_unavailable', message: 'Unavailable', details: {} } },
              503,
            ),
          )
        }
        return Promise.resolve(
          jsonResponse({
            items: [
              {
                user: USER_A,
                wins: 1,
                losses: 0,
                points_for: 11,
                points_against: 5,
                point_difference: 6,
              },
            ],
          }),
        )
      }
      return Promise.resolve(jsonResponse({}))
    })
    renderRoute(<SessionPage />, `/games/${SESSION_ID}`, '/games/:sessionId')

    expect(await screen.findByText('Не удалось загрузить данные')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Повторить' }))

    expect(await screen.findByText('+6')).toBeInTheDocument()
    expect(statisticsCalls).toBe(2)
  })
})

function renderRoute(element: React.ReactNode, initial: string, path = '*') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return { ...render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[initial]}><Routes><Route path={path} element={element}/><Route path="*" element={<LocationProbe/>}/></Routes></MemoryRouter></QueryClientProvider>), client }
}

function LocationProbe() {
  return <span>{useLocation().pathname}</span>
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function user(id: string, firstName: string) {
  return { id, first_name: firstName, last_name: null, username: firstName.toLowerCase(), photo_url: null, created_at: '2026-11-07T10:00:00Z', last_active_at: null }
}

function participant(id: string, profile: ReturnType<typeof user>): SessionParticipant {
  return { participation_id: id, user: profile, joined_at: '2026-11-07T10:00:00Z', left_at: null, is_active: true }
}

function queueItem(id: string, userId: string) {
  return { participation_id: id, user_id: userId, joined_at: '2026-11-07T10:00:00Z' }
}

function session(overrides: Partial<GameSession> = {}): GameSession {
  return { id: SESSION_ID, organizer_id: USER_A.id, status: 'pending', name: 'Тестовая игра', points_to_win: 11, sets_to_win: 1, is_ranked: true, created_at: '2026-11-07T10:00:00Z', started_at: null, completed_at: null, version: 1, active_participants: PARTICIPANTS, ...overrides }
}

function match(options: { status?: SessionMatch['status']; winnerId?: string } = {}): SessionMatch {
  return { id: MATCH_ID, context: 'game_session', status: options.status ?? 'active', created_by: USER_A.id, points_to_win: 11, win_by: 2, sets_to_win: 1, is_ranked: true, winner_id: options.winnerId ?? null, created_at: '2026-11-07T10:00:00Z', updated_at: '2026-11-07T10:00:00Z', started_at: '2026-11-07T10:00:00Z', completed_at: options.status === 'completed' ? '2026-11-07T10:01:00Z' : null, cancelled_at: null, version: 1, participants: [{ position: 1, user: USER_A }, { position: 2, user: USER_B }], sets: options.status === 'completed' ? [{ id: 'set-1', set_number: 1, player1_score: 11, player2_score: 5, created_at: '2026-11-07T10:01:00Z' }] : [], game_session_id: SESSION_ID, sequence_number: 1 }
}

function nextMatch(): SessionMatch {
  return { ...match(), id: NEXT_MATCH_ID, participants: [{ position: 1, user: USER_A }, { position: 2, user: USER_C }], sequence_number: 2 }
}

function commandResponse(recorded: SessionMatch, next: SessionMatch): SessionMatchCommandResponse {
  return { match: recorded, current_match: next, queue: { current_pair: [queueItem('pa', USER_A.id), queueItem('pc', USER_C.id)], waiting: [queueItem('pb', USER_B.id)], is_paused: false } }
}

function mockResultFlow(response: SessionMatchCommandResponse) {
  vi.spyOn(globalThis, 'fetch').mockImplementation((input, request) => {
    if (request?.method === 'PUT') return Promise.resolve(jsonResponse(response))
    if (requestUrl(input).includes('/matches/current')) return Promise.resolve(jsonResponse(match()))
    return Promise.resolve(jsonResponse({ items: [], total: 0, limit: 100, offset: 0 }))
  })
}

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input
  if (input instanceof URL) return input.toString()
  return input.url
}

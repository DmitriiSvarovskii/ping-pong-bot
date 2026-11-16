import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'

import { ApiClient } from '../api/client'
import type { CurrentRating, LeaderboardItem, LeaderboardResponse } from '../types/ratings'
import { RatingPage } from './RatingPage'

const CURRENT_USER_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const auth = vi.hoisted(() => ({ apiClient: null as ApiClient | null }))
let observers: MockIntersectionObserver[] = []

vi.mock('../auth/AuthProvider', () => ({
  useAuth: () => ({
    apiClient: auth.apiClient as ApiClient,
    status: 'authenticated',
    user: { id: CURRENT_USER_ID },
  }),
}))

beforeEach(() => {
  auth.apiClient = new ApiClient()
  observers = []
  vi.stubGlobal('IntersectionObserver', MockIntersectionObserver)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('RatingPage', () => {
  it('shows the current rating and highlights the current player in server order', async () => {
    const other = leaderboardPlayer(1, 'Иван', 'Петров')
    const current = leaderboardPlayer(2, 'Анна', null, CURRENT_USER_ID)
    mockRatingApi(currentRating(), leaderboardResponse([other, current], 2))

    renderPage()

    const summary = await screen.findByLabelText('Ваш рейтинг')
    expect(within(summary).getByText('1613')).toBeInTheDocument()
    expect(within(summary).getByText('#2')).toBeInTheDocument()
    expect(within(summary).getByText('14')).toBeInTheDocument()
    const currentRow = screen.getByTestId(`leaderboard-user-${CURRENT_USER_ID}`)
    expect(currentRow).toHaveClass('current-user')
    expect(within(currentRow).getByText('Вы')).toBeInTheDocument()
    const rows = screen.getAllByTestId(/^leaderboard-user-/)
    expect(within(rows[0] as HTMLElement).getByText('Иван Петров')).toBeInTheDocument()
    expect(within(rows[1] as HTMLElement).getByText('Анна')).toBeInTheDocument()
    expect(screen.getByText('Рейтинг обновляется ежедневно')).toBeInTheDocument()
  })

  it('distinguishes a missing rating from a request error without showing 1500', async () => {
    mockRatingApi(
      jsonResponse({ error: { code: 'rating_not_found', message: 'Rating not found' } }, 404),
      leaderboardResponse([], 0),
    )

    renderPage()

    expect(await screen.findByText('Сыграйте первый рейтинговый матч')).toBeInTheDocument()
    expect(screen.queryByText('Не удалось загрузить ваш рейтинг')).not.toBeInTheDocument()
    expect(screen.queryByText('1500')).not.toBeInTheDocument()
    expect(screen.getByText('Рейтинг пока пуст')).toBeInTheDocument()
  })

  it('loads subsequent pages and removes duplicate players without reordering', async () => {
    const firstPage = Array.from({ length: 20 }, (_, index) => (
      leaderboardPlayer(index + 1, `Игрок ${index + 1}`)
    ))
    const duplicate = firstPage[19] as LeaderboardItem
    const lastPlayer = leaderboardPlayer(21, 'Последний игрок')
    const fetchMock = mockRatingApi(
      currentRating(),
      leaderboardResponse(firstPage, 21, 0),
      leaderboardResponse([duplicate, lastPlayer], 21, 20),
    )
    renderPage()
    await screen.findByText('Игрок 1')

    act(() => latestObserver().intersect())

    expect(await screen.findByText('Последний игрок')).toBeInTheDocument()
    expect(screen.getAllByText('Игрок 20')).toHaveLength(1)
    expect(fetchMock.mock.calls.some(([input]) => getRequestUrl(input).includes(
      '/leaderboard?limit=20&offset=20',
    ))).toBe(true)
    expect(screen.getByText('Вся таблица загружена')).toBeInTheDocument()
  })

  it('shows a leaderboard error and retries without hiding the current rating', async () => {
    let leaderboardCalls = 0
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      const url = getRequestUrl(input)
      if (url.includes('/ratings/me')) return Promise.resolve(jsonResponse(currentRating()))
      leaderboardCalls += 1
      if (leaderboardCalls === 1) {
        return Promise.resolve(jsonResponse({ error: { message: 'Unavailable' } }, 503))
      }
      return Promise.resolve(jsonResponse(leaderboardResponse([
        leaderboardPlayer(1, 'После повтора'),
      ], 1)))
    })
    renderPage()

    expect(await screen.findByText('Не удалось загрузить данные')).toBeInTheDocument()
    expect(within(screen.getByLabelText('Ваш рейтинг')).getByText('1613')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Повторить' }))

    expect(await screen.findByText('После повтора')).toBeInTheDocument()
    expect(leaderboardCalls).toBe(2)
  })

  it('opens another player profile from the leaderboard', async () => {
    const other = leaderboardPlayer(1, 'Иван', 'Петров')
    mockRatingApi(currentRating(), leaderboardResponse([other], 1))
    renderPage()
    await screen.findByText('Иван Петров')

    await userEvent.click(screen.getByRole('link', { name: 'Открыть профиль Иван Петров' }))

    expect(screen.getByTestId('destination')).toHaveTextContent(`/players/${other.user_id}`)
  })

  it('opens the existing personal profile for the current player', async () => {
    const current = leaderboardPlayer(2, 'Анна', null, CURRENT_USER_ID)
    mockRatingApi(currentRating(), leaderboardResponse([current], 1))
    renderPage()
    await screen.findByText('Анна')

    await userEvent.click(screen.getByRole('link', { name: 'Открыть профиль Анна' }))

    expect(screen.getByTestId('destination')).toHaveTextContent('/profile')
  })

  it('disconnects pagination observation on unmount', async () => {
    mockRatingApi(
      currentRating(),
      leaderboardResponse(Array.from({ length: 20 }, (_, index) => (
        leaderboardPlayer(index + 1, `Игрок ${index + 1}`)
      )), 21),
    )
    const view = renderPage()
    await screen.findByText('Игрок 1')
    const observer = latestObserver()

    view.unmount()

    expect(observer.disconnect).toHaveBeenCalledOnce()
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

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/rating']}>
        <Routes>
          <Route path="/rating" element={<RatingPage />} />
          <Route path="/profile" element={<Destination />} />
          <Route path="/players/:userId" element={<Destination />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function Destination() {
  const location = useLocation()
  return <span data-testid="destination">{location.pathname}</span>
}

function latestObserver(): MockIntersectionObserver {
  const observer = observers.at(-1)
  if (!observer) throw new Error('IntersectionObserver was not created')
  return observer
}

function getRequestUrl(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input
  if (input instanceof URL) return input.href
  return input.url
}

function mockRatingApi(
  rating: CurrentRating | Response,
  ...leaderboardPages: LeaderboardResponse[]
) {
  let pageIndex = 0
  return vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
    const url = getRequestUrl(input)
    if (url.includes('/ratings/me')) {
      return Promise.resolve(rating instanceof Response ? rating : jsonResponse(rating))
    }
    const page = leaderboardPages[Math.min(pageIndex, leaderboardPages.length - 1)]
    pageIndex += 1
    return Promise.resolve(jsonResponse(page))
  })
}

function currentRating(): CurrentRating {
  return {
    user_id: CURRENT_USER_ID,
    position: 2,
    rating: '1612.500000',
    rd: '70.000000',
    effective_rd: '72.000000',
    volatility: '0.060000',
    rated_matches_count: 14,
    as_of_period_id: '11111111-1111-4111-8111-111111111111',
    as_of_period_ends_at: '2026-11-16T00:00:00Z',
    inactivity_periods: 0,
    updated_at: '2026-11-16T00:05:00Z',
  }
}

function leaderboardPlayer(
  position: number,
  firstName: string,
  lastName: string | null = null,
  userId = `00000000-0000-4000-8000-${position.toString().padStart(12, '0')}`,
): LeaderboardItem {
  return {
    position,
    user_id: userId,
    first_name: firstName,
    last_name: lastName,
    username: null,
    photo_url: null,
    rating: String(1700 - position),
    rd: '70',
    effective_rd: '72',
    rated_matches_count: position + 3,
  }
}

function leaderboardResponse(
  items: LeaderboardItem[],
  total = items.length,
  offset = 0,
): LeaderboardResponse {
  return { items, total, limit: 20, offset, current_user_position: 2 }
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

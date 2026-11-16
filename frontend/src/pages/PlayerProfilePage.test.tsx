import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'

import { ApiClient } from '../api/client'
import type { CurrentUser, UserPublicProfile } from '../types/api'
import type { CurrentRating } from '../types/ratings'
import { PlayerProfilePage } from './PlayerProfilePage'

const CURRENT_USER_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const OTHER_USER_ID = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
const auth = vi.hoisted(() => ({ apiClient: null as ApiClient | null }))

vi.mock('../auth/AuthProvider', () => ({
  useAuth: () => ({
    apiClient: auth.apiClient as ApiClient,
    status: 'authenticated',
    user: currentUser(),
  }),
}))
vi.mock('../ratings/RatingHistory', () => ({
  RatingHistory: ({ userId }: { userId: string }) => (
    <section data-testid="rating-history">История {userId}</section>
  ),
}))

beforeEach(() => {
  auth.apiClient = new ApiClient()
})

describe('PlayerProfilePage', () => {
  it('shows a public profile and published rating without personal status', async () => {
    mockPlayerApi([ok(otherUser())], [ok(playerRating())])

    renderPage()

    expect(await screen.findByText('Пётр Петров')).toBeInTheDocument()
    expect(screen.getByText('@petr')).toBeInTheDocument()
    const summary = screen.getByLabelText('Рейтинг')
    expect(within(summary).getByText('1651')).toBeInTheDocument()
    expect(within(summary).getByText('#5')).toBeInTheDocument()
    expect(within(summary).getByText('9')).toBeInTheDocument()
    expect(within(summary).getByText('Надёжность: Средняя')).toBeInTheDocument()
    expect(screen.queryByText('Расчёт рейтинга')).not.toBeInTheDocument()
    expect(screen.getByTestId('rating-history')).toHaveTextContent(OTHER_USER_ID)
  })

  it('keeps the public profile visible when rating is not published', async () => {
    mockPlayerApi([ok(otherUser())], [failure(404, 'rating_not_found')])

    renderPage()

    expect(await screen.findByText('Пётр Петров')).toBeInTheDocument()
    expect(screen.getByText('Рейтинг пока не опубликован')).toBeInTheDocument()
    expect(screen.queryByText('1500')).not.toBeInTheDocument()
  })

  it('shows a dedicated state when the player does not exist', async () => {
    mockPlayerApi(
      [failure(404, 'not_found')],
      [failure(404, 'rating_not_found')],
    )

    renderPage()

    expect(await screen.findByText('Игрок не найден')).toBeInTheDocument()
    expect(screen.queryByText('Не удалось загрузить профиль')).not.toBeInTheDocument()
  })

  it('retries a failed profile request without duplicating the rating request', async () => {
    const fetchMock = mockPlayerApi(
      [failure(503), failure(503), ok(otherUser())],
      [ok(playerRating())],
    )
    renderPage()

    expect(await screen.findByText('Не удалось загрузить профиль')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Повторить' }))

    expect(await screen.findByText('Пётр Петров')).toBeInTheDocument()
    expect(countRequests(fetchMock, `/users/${OTHER_USER_ID}`)).toBe(3)
    expect(countRequests(fetchMock, `/ratings/${OTHER_USER_ID}`)).toBe(1)
  })

  it('retries a rating error while preserving player information', async () => {
    const fetchMock = mockPlayerApi(
      [ok(otherUser())],
      [failure(503), failure(503), ok(playerRating())],
    )
    renderPage()

    expect(await screen.findByText('Не удалось загрузить рейтинг')).toBeInTheDocument()
    expect(screen.getByText('Пётр Петров')).toBeInTheDocument()
    const summary = screen.getByLabelText('Рейтинг')
    await userEvent.click(within(summary).getByRole('button', { name: 'Повторить' }))

    expect(await within(summary).findByText('1651')).toBeInTheDocument()
    expect(countRequests(fetchMock, `/users/${OTHER_USER_ID}`)).toBe(1)
    expect(countRequests(fetchMock, `/ratings/${OTHER_USER_ID}`)).toBe(3)
  })

  it('redirects the current user to the existing personal profile without API requests', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')

    renderPage(`/players/${CURRENT_USER_ID}`)

    expect(await screen.findByTestId('destination')).toHaveTextContent('/profile')
    expect(fetchMock).not.toHaveBeenCalled()
  })
})

interface ResponseSpec {
  body: unknown
  status: number
}

function renderPage(initialEntry = `/players/${OTHER_USER_ID}`) {
  const client = new QueryClient({ defaultOptions: { queries: { retryDelay: 0 } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/players/:userId" element={<PlayerProfilePage />} />
          <Route path="/profile" element={<Destination />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function Destination() {
  const location = useLocation()
  return <span data-testid="destination">{location.pathname}</span>
}

function mockPlayerApi(profileResponses: ResponseSpec[], ratingResponses: ResponseSpec[]) {
  let profileIndex = 0
  let ratingIndex = 0
  return vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
    const url = requestUrl(input)
    if (url.endsWith(`/users/${OTHER_USER_ID}`)) {
      const response = responseAt(profileResponses, profileIndex)
      profileIndex += 1
      return Promise.resolve(jsonResponse(response))
    }
    if (url.endsWith(`/ratings/${OTHER_USER_ID}`)) {
      const response = responseAt(ratingResponses, ratingIndex)
      ratingIndex += 1
      return Promise.resolve(jsonResponse(response))
    }
    throw new Error(`Unexpected request: ${url}`)
  })
}

function countRequests(fetchMock: FetchSpy, suffix: string): number {
  return fetchMock.mock.calls.filter(([input]) => requestUrl(input).endsWith(suffix)).length
}

type FetchSpy = { mock: { calls: Array<Parameters<typeof fetch>> } }

function responseAt(responses: ResponseSpec[], index: number): ResponseSpec {
  const response = responses[Math.min(index, responses.length - 1)]
  if (!response) throw new Error('A mocked response is required')
  return response
}

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input
  if (input instanceof URL) return input.href
  return input.url
}

function ok(body: unknown): ResponseSpec {
  return { body, status: 200 }
}

function failure(status: number, code = 'request_failed'): ResponseSpec {
  return { body: { error: { code, message: 'Request failed' } }, status }
}

function jsonResponse(response: ResponseSpec): Response {
  return new Response(JSON.stringify(response.body), {
    status: response.status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function currentUser(): CurrentUser {
  return {
    id: CURRENT_USER_ID,
    telegram_id: 123456,
    first_name: 'Анна',
    last_name: null,
    username: 'anna',
    photo_url: null,
    created_at: '2026-11-01T10:00:00Z',
    updated_at: '2026-11-16T10:00:00Z',
    last_active_at: null,
  }
}

function otherUser(): UserPublicProfile {
  return {
    id: OTHER_USER_ID,
    first_name: 'Пётр',
    last_name: 'Петров',
    username: 'petr',
    photo_url: null,
    created_at: '2026-11-02T10:00:00Z',
    last_active_at: '2026-11-16T09:00:00Z',
  }
}

function playerRating(): CurrentRating {
  return {
    user_id: OTHER_USER_ID,
    position: 5,
    rating: '1650.600000',
    rd: '75.000000',
    effective_rd: '80.000000',
    volatility: '0.060000',
    rated_matches_count: 9,
    as_of_period_id: '11111111-1111-4111-8111-111111111111',
    as_of_period_ends_at: '2026-11-16T00:00:00Z',
    inactivity_periods: 0,
    updated_at: '2026-11-16T00:05:00Z',
  }
}

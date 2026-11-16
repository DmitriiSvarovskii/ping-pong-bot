import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { ApiClient } from '../api/client'
import { formatNextCalculationAt } from '../ratings/formatters'
import type { CurrentUser } from '../types/api'
import type { CurrentRating, RatingStatusResponse } from '../types/ratings'
import { ProfilePage } from './ProfilePage'

const USER_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
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

describe('ProfilePage rating', () => {
  it('shows the published rating, position, matches, reliability, and calculation status', async () => {
    mockProfileApi([ok(currentRating())], [ok(ratingStatus())])

    renderPage()

    const summary = await screen.findByLabelText('Рейтинг')
    expect(within(summary).getByText('1613')).toBeInTheDocument()
    expect(within(summary).getByText('#2')).toBeInTheDocument()
    expect(within(summary).getByText('14')).toBeInTheDocument()
    expect(within(summary).getByText('Надёжность: Высокая')).toHaveClass('reliability-high')
    expect(screen.getByText('Рейтинг актуален')).toBeInTheDocument()
    expect(screen.getByText(formatNextCalculationAt(ratingStatus().next_calculation_at)))
      .toBeInTheDocument()
    expect(screen.getAllByText('@anna')).toHaveLength(2)
    expect(screen.getByTestId('rating-history')).toHaveTextContent(USER_ID)
  })

  it('shows pending publication instead of suggesting a first match', async () => {
    mockProfileApi(
      [failure(404, 'rating_not_found')],
      [ok(ratingStatus({ state: 'pending', pending_matches_count: 2 }))],
    )

    renderPage()

    expect(await screen.findByText('Рейтинг ожидает расчёта')).toBeInTheDocument()
    expect(screen.getByText('Ожидает расчёта')).toBeInTheDocument()
    expect(screen.getByText('2')).toBeInTheDocument()
    expect(screen.queryByText('Сыграйте первый рейтинговый матч')).not.toBeInTheDocument()
    expect(screen.queryByText('1500')).not.toBeInTheDocument()
  })

  it('keeps the latest published rating visible during a historical rebuild', async () => {
    mockProfileApi(
      [ok(currentRating())],
      [ok(ratingStatus({ state: 'rebuilding', pending_matches_count: 1 }))],
    )

    renderPage()

    expect(within(await screen.findByLabelText('Рейтинг')).getByText('1613'))
      .toBeInTheDocument()
    expect(screen.getByText('Пересчитывается история')).toBeInTheDocument()
    expect(screen.getByText('1')).toBeInTheDocument()
  })

  it.each([
    ['80', 'Надёжность: Средняя', 'reliability-medium'],
    ['120', 'Надёжность: Низкая', 'reliability-low'],
  ])('styles effective RD %s as %s', async (effectiveRd, label, className) => {
    mockProfileApi(
      [ok(currentRating({ effective_rd: effectiveRd }))],
      [ok(ratingStatus())],
    )

    renderPage()

    expect(await screen.findByText(label)).toHaveClass(className)
  })

  it('keeps status visible when rating fails and retries only the rating query', async () => {
    const fetchMock = mockProfileApi(
      [failure(503), failure(503), ok(currentRating())],
      [ok(ratingStatus({ state: 'pending', pending_matches_count: 3 }))],
    )
    renderPage()

    expect(await screen.findByText('Не удалось загрузить рейтинг')).toBeInTheDocument()
    expect(screen.getByText('Ожидает расчёта')).toBeInTheDocument()
    const summary = screen.getByLabelText('Рейтинг')
    await userEvent.click(within(summary).getByRole('button', { name: 'Повторить' }))

    expect(await within(summary).findByText('1613')).toBeInTheDocument()
    expect(countExactRequests(fetchMock, '/ratings/me')).toBe(3)
    expect(countExactRequests(fetchMock, '/ratings/me/status')).toBe(1)
  })

  it('keeps rating visible when status fails and retries only the status query', async () => {
    const fetchMock = mockProfileApi(
      [ok(currentRating())],
      [failure(503), failure(503), ok(ratingStatus())],
    )
    renderPage()

    expect(await screen.findByText('Не удалось загрузить статус расчёта.')).toBeInTheDocument()
    expect(within(screen.getByLabelText('Рейтинг')).getByText('1613')).toBeInTheDocument()
    const statusSection = screen.getByRole('heading', { name: 'Расчёт рейтинга' }).closest('section')
    if (!statusSection) throw new Error('Rating status section is unavailable')
    await userEvent.click(within(statusSection).getByRole('button', { name: 'Повторить' }))

    expect(await screen.findByText('Рейтинг актуален')).toBeInTheDocument()
    expect(countExactRequests(fetchMock, '/ratings/me/status')).toBe(3)
    expect(countExactRequests(fetchMock, '/ratings/me')).toBe(1)
  })
})

interface ResponseSpec {
  body: unknown
  status: number
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retryDelay: 0 } } })
  return render(
    <QueryClientProvider client={client}>
      <ProfilePage />
    </QueryClientProvider>,
  )
}

function mockProfileApi(ratingResponses: ResponseSpec[], statusResponses: ResponseSpec[]) {
  let ratingIndex = 0
  let statusIndex = 0
  return vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
    const url = requestUrl(input)
    if (url.endsWith('/ratings/me/status')) {
      const response = responseAt(statusResponses, statusIndex)
      statusIndex += 1
      return Promise.resolve(jsonResponse(response))
    }
    if (url.endsWith('/ratings/me')) {
      const response = responseAt(ratingResponses, ratingIndex)
      ratingIndex += 1
      return Promise.resolve(jsonResponse(response))
    }
    throw new Error(`Unexpected request: ${url}`)
  })
}

function countExactRequests(fetchMock: FetchSpy, suffix: string): number {
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
    id: USER_ID,
    telegram_id: 123456,
    first_name: 'Анна',
    last_name: 'Иванова',
    username: 'anna',
    photo_url: null,
    created_at: '2026-11-01T10:00:00Z',
    updated_at: '2026-11-16T10:00:00Z',
    last_active_at: '2026-11-16T10:00:00Z',
  }
}

function currentRating(overrides: Partial<CurrentRating> = {}): CurrentRating {
  return {
    user_id: USER_ID,
    position: 2,
    rating: '1612.500000',
    rd: '45.000000',
    effective_rd: '48.000000',
    volatility: '0.060000',
    rated_matches_count: 14,
    as_of_period_id: '11111111-1111-4111-8111-111111111111',
    as_of_period_ends_at: '2026-11-16T00:00:00Z',
    inactivity_periods: 0,
    updated_at: '2026-11-16T00:05:00Z',
    ...overrides,
  }
}

function ratingStatus(overrides: Partial<RatingStatusResponse> = {}): RatingStatusResponse {
  return {
    state: 'up_to_date',
    pending_matches_count: 0,
    next_calculation_at: '2026-11-17T00:05:00Z',
    ...overrides,
  }
}

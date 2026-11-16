import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, within } from '@testing-library/react'

import { ApiClient } from '../api/client'
import type { RatingHistoryItem, RatingHistoryResponse } from '../types/ratings'
import { RatingHistory } from './RatingHistory'

const USER_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const auth = vi.hoisted(() => ({ apiClient: null as ApiClient | null }))

vi.mock('../auth/AuthProvider', () => ({
  useAuth: () => ({ apiClient: auth.apiClient as ApiClient }),
}))

beforeEach(() => {
  auth.apiClient = new ApiClient()
})

describe('RatingHistory', () => {
  it('uses the default 30-day filter, real time intervals, and point details', async () => {
    const items = [
      historyItem('period-10', '2026-11-10', '1510', '1530', 5),
      historyItem('period-02', '2026-11-02', '1515', '1510', 1),
      historyItem('period-01', '2026-11-01', '1500', '1505', 2),
      historyItem('period-empty', '2026-10-31', '1500', '1500', 0),
    ]
    const fetchMock = mockHistoryApi([ok(historyResponse(items))])
    renderHistory()

    expect(await screen.findByTestId('rating-history-item-period-10')).toBeInTheDocument()
    const requestUrl = getRequestUrl(fetchMock.mock.calls[0]?.[0])
    expect(requestUrl).toContain('limit=30&offset=0')
    expect(requestUrl).toContain('from_date=')
    expect(requestUrl).toContain('to_date=')
    expect(screen.getByRole('button', { name: '30 дней' })).toHaveAttribute('aria-pressed', 'true')
    for (const label of ['7 дней', '30 дней', '90 дней', 'Всё время']) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument()
    }

    const firstX = pointCoordinate('period-01', 'cx')
    const secondX = pointCoordinate('period-02', 'cx')
    const lastX = pointCoordinate('period-10', 'cx')
    expect(secondX - firstX).toBeLessThan(lastX - secondX)

    fireEvent.click(screen.getByTestId('rating-chart-point-period-10'))
    const details = screen.getByTestId('rating-point-details')
    expect(within(details).getByText('10.11.2026')).toBeInTheDocument()
    expect(within(details).getByText('1530')).toBeInTheDocument()
    expect(within(details).getByText('+20')).toHaveClass('rating-change-positive')
    expect(within(details).getByText('5 матчей')).toBeInTheDocument()
    expect(within(screen.getByTestId('rating-history-item-period-02')).getByText('−5'))
      .toHaveClass('rating-change-negative')
    expect(within(screen.getByTestId('rating-history-item-period-02')).getByText('1 матч'))
      .toBeInTheDocument()
    expect(within(screen.getByTestId('rating-history-item-period-01')).getByText('2 матча'))
      .toBeInTheDocument()
    expect(screen.queryByTestId('rating-history-item-period-empty')).not.toBeInTheDocument()
    expect(screen.queryByTestId('rating-chart-point-period-empty')).not.toBeInTheDocument()
  })

  it('adds the real initial value only after the complete all-time history is loaded', async () => {
    const item = historyItem('first-period', '2026-10-01', '1500', '1512', 2)
    mockHistoryApi([
      ok(historyResponse([item])),
      ok(historyResponse([
        item,
        historyItem('empty-before-first', '2026-09-30', '1500', '1500', 0),
      ], 2)),
    ])
    renderHistory()

    await screen.findByTestId('rating-chart-point-first-period')
    expect(screen.queryByTestId('rating-chart-point-initial-first-period')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Всё время' }))

    expect(await screen.findByTestId('rating-chart-point-initial-first-period')).toBeInTheDocument()
    expect(screen.getAllByTestId(/^rating-chart-point-/)).toHaveLength(2)
    expect(screen.queryByTestId('rating-chart-point-empty-before-first')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Начальный рейтинг/ })).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('rating-chart-point-initial-first-period'))
    expect(within(screen.getByTestId('rating-point-details')).getByText('1500'))
      .toBeInTheDocument()
  })

  it('renders a single rating point without inventing a line or initial point', async () => {
    mockHistoryApi([ok(historyResponse([
      historyItem('single', '2026-11-01', '1500', '1510', 1),
    ]))])
    renderHistory()

    const point = await screen.findByTestId('rating-chart-point-single')
    expect(Number(point.getAttribute('cx'))).toBeGreaterThan(0)
    expect(Number(point.getAttribute('cy'))).toBeGreaterThan(0)
    expect(document.querySelector('.rating-chart-line')).not.toBeInTheDocument()
    expect(screen.queryByTestId('rating-chart-point-initial-single')).not.toBeInTheDocument()
  })

  it('renders equal ratings without invalid SVG coordinates', async () => {
    const equalItems = [
      historyItem('equal-2', '2026-11-02', '1500', '1500', 1),
      historyItem('equal-1', '2026-11-01', '1500', '1500', 1),
    ]
    mockHistoryApi([ok(historyResponse(equalItems))])
    renderHistory()

    await screen.findByTestId('rating-chart-point-equal-2')
    const line = document.querySelector('.rating-chart-line')
    expect(line?.getAttribute('d')).not.toContain('NaN')
    expect(screen.getAllByText('0')).toHaveLength(2)
  })

  it('keeps newest entries first and loads the next page', async () => {
    const newestPage = Array.from({ length: 30 }, (_, index) => (
      historyItem(
        `new-${index}`,
        `2026-11-${String(30 - index).padStart(2, '0')}`,
        '1500',
        '1501',
        index === 5 ? 0 : 1,
      )
    ))
    const oldest = historyItem('oldest', '2026-10-01', '1490', '1500', 1)
    const fetchMock = mockHistoryApi([
      ok(historyResponse(newestPage, 31, 0)),
      ok(historyResponse([oldest], 31, 30)),
    ])
    renderHistory()
    await screen.findByTestId('rating-history-item-new-0')

    fireEvent.click(screen.getByRole('button', { name: 'Показать ещё' }))

    expect(await screen.findByTestId('rating-history-item-oldest')).toBeInTheDocument()
    const entries = screen.getAllByTestId(/^rating-history-item-/)
    expect(entries[0]).toHaveAttribute('data-testid', 'rating-history-item-new-0')
    expect(entries.at(-1)).toHaveAttribute('data-testid', 'rating-history-item-oldest')
    expect(screen.queryByTestId('rating-history-item-new-5')).not.toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([input]) => getRequestUrl(input).includes('offset=30')))
      .toBe(true)
  })

  it('handles empty history and retries an initial request error', async () => {
    const fetchMock = mockHistoryApi([
      failure(503),
      failure(503),
      ok(historyResponse([])),
    ])
    renderHistory()

    expect(await screen.findByText('Не удалось загрузить историю рейтинга')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Повторить' }))

    expect(await screen.findByText('История пока пуста')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })
})

interface ResponseSpec {
  body: unknown
  status: number
}

function renderHistory() {
  const client = new QueryClient({ defaultOptions: { queries: { retryDelay: 0 } } })
  return render(
    <QueryClientProvider client={client}>
      <RatingHistory userId={USER_ID} />
    </QueryClientProvider>,
  )
}

function mockHistoryApi(responses: ResponseSpec[]) {
  let index = 0
  return vi.spyOn(globalThis, 'fetch').mockImplementation(() => {
    const response = responseAt(responses, index)
    index += 1
    return Promise.resolve(new Response(JSON.stringify(response.body), {
      status: response.status,
      headers: { 'Content-Type': 'application/json' },
    }))
  })
}

function pointCoordinate(periodId: string, attribute: 'cx' | 'cy'): number {
  const value = screen.getByTestId(`rating-chart-point-${periodId}`).getAttribute(attribute)
  if (value === null) throw new Error(`Missing ${attribute} for ${periodId}`)
  return Number(value)
}

function getRequestUrl(input: RequestInfo | URL | undefined): string {
  if (!input) throw new Error('Request input is unavailable')
  if (typeof input === 'string') return input
  if (input instanceof URL) return input.href
  return input.url
}

function responseAt(responses: ResponseSpec[], index: number): ResponseSpec {
  const response = responses[Math.min(index, responses.length - 1)]
  if (!response) throw new Error('A mocked response is required')
  return response
}

function ok(body: unknown): ResponseSpec {
  return { body, status: 200 }
}

function failure(status: number): ResponseSpec {
  return { body: { error: { code: 'request_failed', message: 'Request failed' } }, status }
}

function historyResponse(
  items: RatingHistoryItem[],
  total = items.length,
  offset = 0,
): RatingHistoryResponse {
  return { items, total, limit: PAGE_SIZE_FOR_TEST, offset }
}

const PAGE_SIZE_FOR_TEST = 30

function historyItem(
  periodId: string,
  date: string,
  ratingBefore: string,
  ratingAfter: string,
  gamesCount: number,
): RatingHistoryItem {
  const start = `${date}T00:00:00Z`
  const end = new Date(`${date}T00:00:00Z`)
  end.setUTCDate(end.getUTCDate() + 1)
  return {
    period_id: periodId,
    period_starts_at: start,
    period_ends_at: end.toISOString(),
    published_at: end.toISOString(),
    rating_before: ratingBefore,
    rating_after: ratingAfter,
    rd_before: '100',
    rd_after: '90',
    volatility_before: '0.06',
    volatility_after: '0.06',
    games_count: gamesCount,
    is_active: true,
  }
}

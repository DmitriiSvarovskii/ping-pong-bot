import { useInfiniteQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'

import { getRatingHistory } from '../api/ratings'
import { useAuth } from '../auth/AuthProvider'
import type { RatingHistoryItem, RatingHistoryResponse } from '../types/ratings'
import {
  calculateRatingChange,
  formatRating,
  formatRatingChange,
  formatRatingPeriodDate,
} from './formatters'
import { ratingKeys } from './queryKeys'

const PAGE_SIZE = 30
const SVG_WIDTH = 320
const SVG_HEIGHT = 176
const PADDING_X = 18
const PADDING_Y = 18

type HistoryRange = '7d' | '30d' | '90d' | 'all'

const ranges: Array<{ value: HistoryRange; label: string; days?: number }> = [
  { value: '7d', label: '7 дней', days: 7 },
  { value: '30d', label: '30 дней', days: 30 },
  { value: '90d', label: '90 дней', days: 90 },
  { value: 'all', label: 'Всё время' },
]

export function RatingHistory({ userId }: { userId: string }) {
  const { apiClient } = useAuth()
  const [range, setRange] = useState<HistoryRange>('30d')
  const [selectedPointId, setSelectedPointId] = useState<string | null>(null)
  const dateRange = useMemo(() => historyDateRange(range), [range])
  const history = useInfiniteQuery({
    queryKey: ratingKeys.history(userId, dateRange.fromDate, dateRange.toDate),
    queryFn: ({ pageParam, signal }) => getRatingHistory(
      apiClient,
      userId,
      {
        limit: PAGE_SIZE,
        offset: pageParam,
        from_date: dateRange.fromDate,
        to_date: dateRange.toDate,
      },
      signal,
    ),
    initialPageParam: 0,
    getNextPageParam: nextHistoryOffset,
    retry: 1,
  })

  const allItems = history.data ? mergeHistory(history.data.pages) : []
  const items = allItems.filter((item) => item.games_count > 0)
  const total = history.data?.pages[0]?.total ?? 0
  const includesFirstPeriod = range === 'all'
    && !history.hasNextPage
    && allItems.length === total
    && items.length > 0

  return (
    <section className="rating-history-section" aria-labelledby={`rating-history-${userId}`}>
      <div className="section-title">
        <h2 id={`rating-history-${userId}`}>Динамика рейтинга</h2>
      </div>
      <div className="rating-range-filters" aria-label="Период истории рейтинга">
        {ranges.map((item) => (
          <button
            key={item.value}
            type="button"
            className={range === item.value ? 'active' : ''}
            aria-pressed={range === item.value}
            onClick={() => {
              setSelectedPointId(null)
              setRange(item.value)
            }}
          >
            {item.label}
          </button>
        ))}
      </div>
      {history.isPending ? (
        <HistoryState loading />
      ) : !history.data ? (
        <HistoryState error onRetry={() => void history.refetch()} />
      ) : (
        <>
          {items.length === 0 ? (
            <HistoryState />
          ) : (
            <>
              <RatingChart
                items={items}
                includeInitial={includesFirstPeriod}
                selectedPointId={selectedPointId}
                onSelectPoint={setSelectedPointId}
              />
              <RatingHistoryList items={items} />
            </>
          )}
          <HistoryPagination
            error={history.isFetchNextPageError}
            hasNextPage={!!history.hasNextPage}
            loading={history.isFetchingNextPage}
            onLoadNext={() => void history.fetchNextPage()}
          />
        </>
      )}
    </section>
  )
}

export function RatingChart({
  items,
  includeInitial,
  selectedPointId,
  onSelectPoint,
}: {
  items: RatingHistoryItem[]
  includeInitial: boolean
  selectedPointId: string | null
  onSelectPoint: (pointId: string) => void
}) {
  const points = chartPoints(items, includeInitial)
  const scaled = scalePoints(points)
  const selected = points.find((point) => point.id === selectedPointId)
  const path = scaled.map((point, index) => (
    `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`
  )).join(' ')

  return (
    <div className="rating-chart-card">
      <svg
        className="rating-chart"
        viewBox={`0 0 ${SVG_WIDTH} ${SVG_HEIGHT}`}
        role="group"
        aria-label="График изменения рейтинга"
      >
        <line className="rating-chart-grid" x1={PADDING_X} y1={SVG_HEIGHT / 2} x2={SVG_WIDTH - PADDING_X} y2={SVG_HEIGHT / 2} />
        {scaled.length > 1 && <path className="rating-chart-line" d={path} />}
        {scaled.map((point) => (
          <g
            key={point.data.id}
            className="rating-chart-point-control"
            role="button"
            tabIndex={0}
            aria-label={`${point.data.label}: рейтинг ${formatRating(point.data.rating)}`}
            onClick={() => onSelectPoint(point.data.id)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault()
                onSelectPoint(point.data.id)
              }
            }}
          >
            <circle className="rating-chart-hit-area" cx={point.x} cy={point.y} r="12" />
            <circle
              className={`rating-chart-point ${selectedPointId === point.data.id ? 'selected' : ''}`}
              data-testid={`rating-chart-point-${point.data.id}`}
              cx={point.x}
              cy={point.y}
              r="4.5"
            />
          </g>
        ))}
      </svg>
      {selected && <SelectedPointDetails point={selected} />}
    </div>
  )
}

function SelectedPointDetails({ point }: { point: ChartPoint }) {
  const change = point.ratingBefore === null
    ? '—'
    : formatRatingChange(point.ratingBefore, point.rating)
  return (
    <dl className="rating-point-details" aria-live="polite" data-testid="rating-point-details">
      <div><dt>Дата</dt><dd>{formatRatingPeriodDate(point.displayDate)}</dd></div>
      <div><dt>Рейтинг</dt><dd>{formatRating(point.rating)}</dd></div>
      <div><dt>Изменение</dt><dd className={trendClass(point.ratingBefore, point.rating)}>{change}</dd></div>
      <div><dt>Матчи</dt><dd>{formatMatchesCount(point.gamesCount)}</dd></div>
    </dl>
  )
}

function RatingHistoryList({ items }: { items: RatingHistoryItem[] }) {
  return (
    <div className="rating-history-list" aria-label="История изменения рейтинга">
      {items.map((item) => (
        <article
          key={item.period_id}
          className="rating-history-item"
          data-testid={`rating-history-item-${item.period_id}`}
        >
          <div>
            <time dateTime={item.period_starts_at}>
              {formatRatingPeriodDate(item.period_starts_at)}
            </time>
            <small>{formatMatchesCount(item.games_count)}</small>
          </div>
          <div>
            <strong>{formatRating(item.rating_after)}</strong>
            <span className={trendClass(item.rating_before, item.rating_after)}>
              {formatRatingChange(item.rating_before, item.rating_after)}
            </span>
          </div>
        </article>
      ))}
    </div>
  )
}

function HistoryPagination({
  error,
  hasNextPage,
  loading,
  onLoadNext,
}: {
  error: boolean
  hasNextPage: boolean
  loading: boolean
  onLoadNext: () => void
}) {
  if (error) {
    return (
      <div className="rating-history-pagination error-panel">
        <span>Не удалось загрузить следующую страницу.</span>
        <button className="secondary-button" onClick={onLoadNext}>Повторить</button>
      </div>
    )
  }
  if (!hasNextPage) return null
  return (
    <button
      className="secondary-button rating-history-more"
      disabled={loading}
      onClick={onLoadNext}
    >
      {loading ? 'Загружаем…' : 'Показать ещё'}
    </button>
  )
}

function HistoryState({
  loading = false,
  error = false,
  onRetry,
}: {
  loading?: boolean
  error?: boolean
  onRetry?: () => void
}) {
  return (
    <div className={`rating-history-state ${error ? 'error-panel' : ''}`}>
      {loading ? (
        <><div className="loader" /><p>Загружаем историю…</p></>
      ) : error ? (
        <>
          <strong>Не удалось загрузить историю рейтинга</strong>
          <button className="secondary-button" onClick={onRetry}>Повторить</button>
        </>
      ) : (
        <><strong>История пока пуста</strong><p>Изменения появятся после публикации рейтинга.</p></>
      )}
    </div>
  )
}

interface ChartPoint {
  id: string
  timestamp: number
  displayDate: string
  label: string
  rating: string
  ratingBefore: string | null
  gamesCount: number
}

interface ScaledPoint {
  data: ChartPoint
  x: number
  y: number
}

function chartPoints(items: RatingHistoryItem[], includeInitial: boolean): ChartPoint[] {
  const chronological = [...items].sort(
    (left, right) => Date.parse(left.period_starts_at) - Date.parse(right.period_starts_at),
  )
  const result: ChartPoint[] = chronological.map((item): ChartPoint => ({
    id: item.period_id,
    timestamp: Date.parse(item.period_ends_at),
    displayDate: item.period_starts_at,
    label: formatRatingPeriodDate(item.period_starts_at),
    rating: item.rating_after,
    ratingBefore: item.rating_before,
    gamesCount: item.games_count,
  }))
  const first = chronological[0]
  if (includeInitial && first) {
    result.unshift({
      id: `initial-${first.period_id}`,
      timestamp: Date.parse(first.period_starts_at),
      displayDate: first.period_starts_at,
      label: `Начальный рейтинг, ${formatRatingPeriodDate(first.period_starts_at)}`,
      rating: first.rating_before,
      ratingBefore: null,
      gamesCount: 0,
    })
  }
  return result
}

function scalePoints(points: ChartPoint[]): ScaledPoint[] {
  if (points.length === 0) return []
  const timestamps = points.map((point) => point.timestamp)
  const ratings = points.map((point) => Number(point.rating))
  const minTime = Math.min(...timestamps)
  const maxTime = Math.max(...timestamps)
  const minRating = Math.min(...ratings)
  const maxRating = Math.max(...ratings)
  const ratingRange = maxRating - minRating
  const yMin = ratingRange === 0 ? minRating - 1 : minRating - ratingRange * 0.12
  const yMax = ratingRange === 0 ? maxRating + 1 : maxRating + ratingRange * 0.12
  return points.map((point) => {
    const xRatio = maxTime === minTime ? 0.5 : (point.timestamp - minTime) / (maxTime - minTime)
    const yRatio = (Number(point.rating) - yMin) / (yMax - yMin)
    return {
      data: point,
      x: PADDING_X + xRatio * (SVG_WIDTH - PADDING_X * 2),
      y: SVG_HEIGHT - PADDING_Y - yRatio * (SVG_HEIGHT - PADDING_Y * 2),
    }
  })
}

function trendClass(before: string | null, after: string): string {
  const change = calculateRatingChange(before, after)
  if (change === null || change === 0) return 'rating-change-neutral'
  return change > 0 ? 'rating-change-positive' : 'rating-change-negative'
}

function formatMatchesCount(count: number): string {
  const absolute = Math.abs(count)
  const lastTwo = absolute % 100
  const last = absolute % 10
  if (lastTwo >= 11 && lastTwo <= 14) return `${count} матчей`
  if (last === 1) return `${count} матч`
  if (last >= 2 && last <= 4) return `${count} матча`
  return `${count} матчей`
}

function mergeHistory(pages: RatingHistoryResponse[]): RatingHistoryItem[] {
  const unique = new Map<string, RatingHistoryItem>()
  for (const page of pages) {
    for (const item of page.items) {
      if (!unique.has(item.period_id)) unique.set(item.period_id, item)
    }
  }
  return [...unique.values()]
}

function nextHistoryOffset(page: RatingHistoryResponse): number | undefined {
  if (page.items.length === 0 || page.offset + page.items.length >= page.total) return undefined
  return page.offset + page.limit
}

function historyDateRange(range: HistoryRange): { fromDate?: string; toDate?: string } {
  const selected = ranges.find((item) => item.value === range)
  if (!selected?.days) return {}
  const now = new Date()
  const end = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()))
  const start = new Date(end)
  start.setUTCDate(start.getUTCDate() - selected.days + 1)
  return { fromDate: utcDate(start), toDate: utcDate(end) }
}

function utcDate(date: Date): string {
  return date.toISOString().slice(0, 10)
}

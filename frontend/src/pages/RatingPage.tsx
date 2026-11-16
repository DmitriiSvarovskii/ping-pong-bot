import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { useCallback, useEffect, useRef } from 'react'

import { ApiError } from '../api/client'
import { getLeaderboard, getMyRating } from '../api/ratings'
import { useAuth } from '../auth/AuthProvider'
import { ErrorPanel, LoadingPanel } from '../components/SessionUi'
import { UserIcon } from '../components/icons'
import { getDisplayName } from '../hooks/useDisplayName'
import { formatRating, formatRatingPosition } from '../ratings/formatters'
import { ratingKeys } from '../ratings/queryKeys'
import type {
  CurrentRating,
  LeaderboardItem,
  LeaderboardResponse,
} from '../types/ratings'

const PAGE_SIZE = 20

export function RatingPage() {
  const { apiClient, user } = useAuth()
  const userId = user?.id ?? 'anonymous'
  const loadMoreElement = useRef<HTMLDivElement | null>(null)
  const nextPageRequest = useRef<Promise<void> | null>(null)
  const current = useQuery({
    queryKey: ratingKeys.current(userId),
    queryFn: ({ signal }) => getMyRating(apiClient, signal),
    retry: (failureCount, error) => !isMissingRating(error) && failureCount < 1,
  })
  const leaderboard = useInfiniteQuery({
    queryKey: ratingKeys.leaderboard(userId),
    queryFn: ({ pageParam, signal }) => getLeaderboard(
      apiClient,
      { offset: pageParam, limit: PAGE_SIZE },
      signal,
    ),
    initialPageParam: 0,
    getNextPageParam: nextLeaderboardOffset,
    gcTime: Infinity,
  })
  const { fetchNextPage, hasNextPage } = leaderboard

  const loadNextPage = useCallback((): Promise<void> => {
    if (!hasNextPage) return Promise.resolve()
    if (nextPageRequest.current) return nextPageRequest.current
    const promise = fetchNextPage()
      .then(() => undefined)
      .finally(() => {
        if (nextPageRequest.current === promise) nextPageRequest.current = null
      })
    nextPageRequest.current = promise
    return promise
  }, [fetchNextPage, hasNextPage])

  useEffect(() => {
    const target = loadMoreElement.current
    if (!target || !hasNextPage || typeof IntersectionObserver === 'undefined') return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) void loadNextPage()
      },
      { rootMargin: '240px 0px' },
    )
    observer.observe(target)
    return () => observer.disconnect()
  }, [hasNextPage, loadNextPage])

  const players = leaderboard.data ? mergeLeaderboard(leaderboard.data.pages) : []

  return (
    <div className="page-stack rating-page">
      <header className="page-header block">
        <p className="eyebrow">GLICKO-2</p>
        <h1>Рейтинг</h1>
        <p>Рейтинг обновляется ежедневно</p>
      </header>
      <CurrentRatingCard
        data={current.data}
        error={current.error}
        loading={current.isPending}
        onRetry={() => void current.refetch()}
      />
      <section className="rating-leaderboard" aria-labelledby="leaderboard-title">
        <div className="section-title">
          <h2 id="leaderboard-title">Таблица лидеров</h2>
          {leaderboard.data && <span>{leaderboard.data.pages[0]?.total ?? 0} игроков</span>}
        </div>
        {leaderboard.isPending ? (
          <LoadingPanel label="Загружаем таблицу лидеров…" />
        ) : !leaderboard.data ? (
          <ErrorPanel error={leaderboard.error} onRetry={() => void leaderboard.refetch()} />
        ) : players.length === 0 ? (
          <div className="state-panel rating-empty-state">
            <strong>Рейтинг пока пуст</strong>
            <p>Таблица появится после публикации первых рейтинговых матчей.</p>
          </div>
        ) : (
          <>
            <div className="global-leaderboard-list">
              {players.map((player) => (
                <LeaderboardRow
                  key={player.user_id}
                  player={player}
                  current={player.user_id === user?.id}
                />
              ))}
            </div>
            {leaderboard.isFetchNextPageError ? (
              <div className="infinite-list-status error-panel" role="status">
                <span>Не удалось загрузить следующую страницу.</span>
                <button className="secondary-button" onClick={() => void loadNextPage()}>
                  Повторить
                </button>
              </div>
            ) : leaderboard.isFetchingNextPage ? (
              <div className="infinite-list-status" role="status">
                <div className="loader" />
                <span>Загружаем ещё…</span>
              </div>
            ) : !leaderboard.hasNextPage ? (
              <p className="infinite-list-status">Вся таблица загружена</p>
            ) : null}
            <div ref={loadMoreElement} className="load-more-sentinel" aria-hidden="true" />
          </>
        )}
      </section>
    </div>
  )
}

function CurrentRatingCard({
  data,
  error,
  loading,
  onRetry,
}: {
  data: CurrentRating | undefined
  error: unknown
  loading: boolean
  onRetry: () => void
}) {
  if (loading) {
    return (
      <div
        className="rating-summary-card rating-summary-loading"
        aria-label="Ваш рейтинг загружается"
      >
        <div className="loader" />
      </div>
    )
  }
  if (isMissingRating(error)) {
    return (
      <div className="rating-summary-card rating-summary-empty" aria-label="Ваш рейтинг">
        <strong>Сыграйте первый рейтинговый матч</strong>
        <p>После ежедневного расчёта здесь появятся рейтинг и позиция.</p>
      </div>
    )
  }
  if (error || !data) {
    return (
      <div className="rating-summary-card rating-summary-error" aria-label="Ваш рейтинг">
        <strong>Не удалось загрузить ваш рейтинг</strong>
        <button className="secondary-button" onClick={onRetry}>Повторить</button>
      </div>
    )
  }
  return (
    <div className="rating-summary-card" aria-label="Ваш рейтинг">
      <div>
        <span>Ваш рейтинг</span>
        <strong>{formatRating(data.rating)}</strong>
      </div>
      <dl>
        <div><dt>Позиция</dt><dd>{formatRatingPosition(data.position)}</dd></div>
        <div><dt>Матчей</dt><dd>{data.rated_matches_count}</dd></div>
      </dl>
    </div>
  )
}

function LeaderboardRow({ player, current }: { player: LeaderboardItem; current: boolean }) {
  return (
    <article
      className={`global-leaderboard-row ${current ? 'current-user' : ''}`}
      data-testid={`leaderboard-user-${player.user_id}`}
    >
      <strong className="leaderboard-position">{formatRatingPosition(player.position)}</strong>
      {player.photo_url ? (
        <img className="leaderboard-avatar" src={player.photo_url} alt="" />
      ) : (
        <span className="leaderboard-avatar avatar-fallback"><UserIcon /></span>
      )}
      <div className="leaderboard-player-copy">
        <strong>{getDisplayName(player)}</strong>
        <small>{player.rated_matches_count} рейтинговых матчей</small>
      </div>
      <strong className="leaderboard-rating">{formatRating(player.rating)}</strong>
      {current && <span className="current-user-label">Вы</span>}
    </article>
  )
}

function nextLeaderboardOffset(page: LeaderboardResponse): number | undefined {
  if (page.items.length === 0 || page.offset + page.items.length >= page.total) return undefined
  return page.offset + page.limit
}

function mergeLeaderboard(pages: LeaderboardResponse[]): LeaderboardItem[] {
  const unique = new Map<string, LeaderboardItem>()
  for (const page of pages) {
    for (const player of page.items) {
      if (!unique.has(player.user_id)) unique.set(player.user_id, player)
    }
  }
  return [...unique.values()]
}

function isMissingRating(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404 && error.code === 'rating_not_found'
}

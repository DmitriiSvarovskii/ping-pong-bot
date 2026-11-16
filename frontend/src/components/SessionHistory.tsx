import { useEffect, useRef } from 'react'

import type { SessionMatch, SessionMatchSummary } from '../types/gameSessions'
import { getDisplayName } from '../hooks/useDisplayName'
import { formatRatingState } from '../ratings/formatters'

interface SessionHistoryProps {
  matches: SessionMatch[]
  total?: number
  summary?: SessionMatchSummary
  loading?: boolean
  error?: unknown
  summaryLoading?: boolean
  summaryError?: unknown
  hasNextPage?: boolean
  fetchingNextPage?: boolean
  nextPageError?: boolean
  onRetry?: () => void
  onRetrySummary?: () => void
  onLoadNext?: () => void
  onEditMatch?: (match: SessionMatch) => void
  onVoidMatch?: (match: SessionMatch) => void
}

export function SessionHistory({
  matches,
  total = matches.length,
  summary,
  loading = false,
  error,
  summaryLoading = false,
  summaryError,
  hasNextPage = false,
  fetchingNextPage = false,
  nextPageError = false,
  onRetry,
  onRetrySummary,
  onLoadNext,
  onEditMatch,
  onVoidMatch,
}: SessionHistoryProps) {
  const sentinel = useRef<HTMLDivElement | null>(null)
  const completed = matches.filter(
    (match) =>
      match.status === 'completed' ||
      match.status === 'cancelled' ||
      match.status === 'voided',
  )

  useEffect(() => {
    const target = sentinel.current
    if (
      !target ||
      !hasNextPage ||
      fetchingNextPage ||
      !onLoadNext ||
      typeof IntersectionObserver === 'undefined'
    ) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) onLoadNext()
      },
      { rootMargin: '240px 0px' },
    )
    observer.observe(target)
    return () => observer.disconnect()
  }, [fetchingNextPage, hasNextPage, onLoadNext])

  return (
    <section className="section-card">
      {summary ? (
        <dl className="session-match-summary" aria-label="Статистика матчей сессии">
          <div><dt>Сыграно</dt><dd>{summary.played}</dd></div>
          <div><dt>Рейтинговых</dt><dd>{summary.ranked}</dd></div>
          <div><dt>Товарищеских</dt><dd>{summary.friendly}</dd></div>
          <div><dt>Ожидают расчёта</dt><dd>{summary.pending_rating}</dd></div>
        </dl>
      ) : summaryLoading ? (
        <p className="muted-copy" role="status">Загружаем статистику матчей…</p>
      ) : summaryError ? (
        <div className="inline-error session-history-error" role="alert">
          <span>Не удалось загрузить статистику матчей.</span>
          {onRetrySummary && (
            <button className="secondary-button" onClick={onRetrySummary}>Повторить</button>
          )}
        </div>
      ) : null}
      <div className="section-title">
        <h2>История матчей</h2>
        <span>{total}</span>
      </div>

      {loading ? (
        <p className="muted-copy" role="status">Загружаем историю матчей…</p>
      ) : error && matches.length === 0 ? (
        <div className="inline-error session-history-error" role="alert">
          <span>Не удалось загрузить историю матчей.</span>
          {onRetry && (
            <button className="secondary-button" onClick={onRetry}>Повторить</button>
          )}
        </div>
      ) : completed.length ? (
        <div className="match-history">
          {completed.map((match) => {
            const first = match.participants.find(
              (item) => item.position === 1,
            )?.user

            const second = match.participants.find(
              (item) => item.position === 2,
            )?.user

            const setScore = match.sets.reduce<[number, number]>(
              ([left, right], set) =>
                set.player1_score > set.player2_score
                  ? [left + 1, right]
                  : [left, right + 1],
              [0, 0],
            )

            const score = `${setScore[0]}:${setScore[1]}`

            const winner = match.participants.find(
              (item) => item.user.id === match.winner_id,
            )?.user

            const isCancelled = match.status === 'cancelled'
            const isVoided = match.status === 'voided'
            const isCompleted = match.status === 'completed'

            return (
              <article key={match.id}>
                <div>
                  <span>Матч #{match.sequence_number}</span>
                  <div className="match-history-controls">
                    <small>
                      {isCancelled
                        ? 'Отменён / пропущен'
                        : isVoided
                          ? 'Аннулирован'
                          : score}
                    </small>
                    {isCompleted && (onEditMatch || onVoidMatch) && (<details className="match-actions-menu">
                      <summary
                        aria-label={`Действия матча ${match.sequence_number}`}
                      >
                        ⋯
                      </summary>

                      <div role="menu">
                        {onEditMatch && (
                          <button
                            type="button"
                            role="menuitem"
                            onClick={(event) => {
                              event.currentTarget.closest('details')
                                ?.removeAttribute('open')
                              onEditMatch(match)
                            }}
                          >
                            Редактировать результат
                          </button>
                        )}

                        {onVoidMatch && (
                          <button
                            type="button"
                            role="menuitem"
                            className="match-action-danger"
                            onClick={(event) => {
                              event.currentTarget.closest('details')
                                ?.removeAttribute('open')
                              onVoidMatch(match)
                            }}
                          >
                            Аннулировать матч
                          </button>
                        )}
                      </div>
                    </details>
                    )}
                  </div>
                </div>

                <p>
                  {first ? getDisplayName(first) : 'Игрок A'}
                  {' '}<b>—</b>{' '}
                  {second ? getDisplayName(second) : 'Игрок B'}
                </p>

                {(isCompleted || isVoided) && (
                  <time dateTime={match.completed_at ?? undefined}>
                    {formatCompletedAt(match.completed_at)}
                  </time>
                )}

                {isCompleted && winner && (
                  <strong className="winner-label">
                    Победитель: {getDisplayName(winner)}
                  </strong>
                )}

                {isCompleted && (
                  <span
                    className={`match-rating-label match-rating-${match.is_ranked ? match.rating_state : 'friendly'
                      }`}
                  >
                    {match.is_ranked
                      ? formatRatingState(match.rating_state)
                      : 'Товарищеский'}
                  </span>
                )}

                {isVoided && (
                  <span className="match-rating-label match-rating-voided">
                    Исключён из статистики и рейтинга
                  </span>
                )}
              </article>
            )
          })}
        </div>
      ) : (
        <p className="muted-copy">Завершённых матчей пока нет.</p>
      )}
      {nextPageError ? (
        <div className="infinite-list-status error-panel" role="status">
          <span>Не удалось загрузить следующие матчи.</span>
          {onLoadNext && (
            <button className="secondary-button" onClick={onLoadNext}>Повторить</button>
          )}
        </div>
      ) : fetchingNextPage ? (
        <div className="infinite-list-status" role="status">
          <div className="loader" />
          <span>Загружаем ещё…</span>
        </div>
      ) : null}
      <div ref={sentinel} className="load-more-sentinel" aria-hidden="true" />
    </section>
  )
}

function formatCompletedAt(value: string | null): string {
  if (!value) return 'Время завершения неизвестно'

  const date = new Date(value)

  if (Number.isNaN(date.getTime())) {
    return 'Время завершения неизвестно'
  }

  return new Intl.DateTimeFormat('ru-RU', {
    day: 'numeric',
    month: 'long',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

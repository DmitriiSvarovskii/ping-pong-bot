import {
  formatRating,
  formatRatingPosition,
  formatRatingReliability,
  getRatingReliability,
} from './formatters'
import type { CurrentRating } from '../types/ratings'

interface RatingSummaryProps {
  rating?: CurrentRating
  loading?: boolean
  missing?: boolean
  error?: boolean
  emptyTitle?: string
  emptyDescription?: string
  onRetry?: () => void
}

export function RatingSummary({
  rating,
  loading = false,
  missing = false,
  error = false,
  emptyTitle = 'Рейтинг пока не опубликован',
  emptyDescription,
  onRetry,
}: RatingSummaryProps) {
  if (loading) {
    return (
      <section className="profile-rating-summary profile-rating-state" aria-label="Рейтинг загружается">
        <div className="loader" />
        <p>Загружаем рейтинг…</p>
      </section>
    )
  }
  if (missing) {
    return (
      <section className="profile-rating-summary profile-rating-state" aria-label="Рейтинг">
        <strong>{emptyTitle}</strong>
        {emptyDescription && <p>{emptyDescription}</p>}
      </section>
    )
  }
  if (error || !rating) {
    return (
      <section className="profile-rating-summary profile-rating-state" aria-label="Рейтинг">
        <strong>Не удалось загрузить рейтинг</strong>
        <p>Проверьте соединение и попробуйте снова.</p>
        {onRetry && <button className="secondary-button" onClick={onRetry}>Повторить</button>}
      </section>
    )
  }

  const reliability = getRatingReliability(rating.effective_rd)
  return (
    <section className="profile-rating-summary" aria-labelledby="profile-rating-title">
      <div className="profile-rating-heading">
        <div>
          <span id="profile-rating-title">Рейтинг</span>
          <strong>{formatRating(rating.rating)}</strong>
        </div>
        <span className={`rating-reliability reliability-${reliability ?? 'unknown'}`}>
          Надёжность: {formatRatingReliability(rating.effective_rd)}
        </span>
      </div>
      <dl className="profile-rating-metrics">
        <div><dt>Позиция</dt><dd>{formatRatingPosition(rating.position)}</dd></div>
        <div><dt>Учтённых матчей</dt><dd>{rating.rated_matches_count}</dd></div>
      </dl>
    </section>
  )
}

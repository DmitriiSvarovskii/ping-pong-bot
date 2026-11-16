import { useQuery } from '@tanstack/react-query'

import { getMyRating, getMyRatingStatus } from '../api/ratings'
import { useAuth } from '../auth/AuthProvider'
import { ProfileCard } from '../components/ProfileCard'
import { isRatingNotFound } from '../ratings/errors'
import {
  formatNextCalculationAt,
  formatRatingCalculationState,
} from '../ratings/formatters'
import { ratingKeys } from '../ratings/queryKeys'
import { RatingSummary } from '../ratings/RatingSummary'
import type { RatingStatusResponse } from '../types/ratings'

export function ProfilePage() {
  const { apiClient, user } = useAuth()
  const userId = user?.id ?? 'anonymous'
  const rating = useQuery({
    queryKey: ratingKeys.current(userId),
    queryFn: ({ signal }) => getMyRating(apiClient, signal),
    enabled: user !== null,
    retry: (failureCount, error) => !isRatingNotFound(error) && failureCount < 1,
  })
  const ratingStatus = useQuery({
    queryKey: ratingKeys.status(userId),
    queryFn: ({ signal }) => getMyRatingStatus(apiClient, signal),
    enabled: user !== null,
    retry: 1,
  })
  if (!user) return null
  const ratingMissing = isRatingNotFound(rating.error)
  const emptyCopy = ratingEmptyCopy(ratingStatus.data, ratingStatus.isPending, !!ratingStatus.error)

  return (
    <div className="page-stack">
      <header className="page-header"><p className="eyebrow">ИГРОК</p><h1>Профиль</h1></header>
      <ProfileCard user={user} />
      <RatingSummary
        rating={rating.data}
        loading={rating.isPending}
        missing={ratingMissing}
        error={!!rating.error && !ratingMissing}
        emptyTitle={emptyCopy.title}
        emptyDescription={emptyCopy.description}
        onRetry={() => void rating.refetch()}
      />
      <RatingStatus
        data={ratingStatus.data}
        loading={ratingStatus.isPending}
        error={!!ratingStatus.error}
        onRetry={() => void ratingStatus.refetch()}
      />
      <section className="info-card">
        <div><span>Имя</span><strong>{user.first_name}</strong></div>
        {user.last_name && <div><span>Фамилия</span><strong>{user.last_name}</strong></div>}
        <div>
          <span>Username</span>
          <strong>{user.username ? `@${user.username}` : 'Не указан'}</strong>
        </div>
      </section>
    </div>
  )
}

function RatingStatus({
  data,
  loading,
  error,
  onRetry,
}: {
  data?: RatingStatusResponse
  loading: boolean
  error: boolean
  onRetry: () => void
}) {
  return (
    <section className="profile-rating-status" aria-labelledby="rating-status-title">
      <h2 id="rating-status-title">Расчёт рейтинга</h2>
      {loading ? (
        <div className="profile-rating-status-state"><div className="loader" /></div>
      ) : error || !data ? (
        <div className="profile-rating-status-error">
          <p>Не удалось загрузить статус расчёта.</p>
          <button className="secondary-button" onClick={onRetry}>Повторить</button>
        </div>
      ) : (
        <>
          <span className={`rating-calculation-state calculation-${data.state}`}>
            {formatRatingCalculationState(data.state)}
          </span>
          <dl className="profile-rating-status-details">
            <div><dt>Ожидающих матчей</dt><dd>{data.pending_matches_count}</dd></div>
            <div>
              <dt>Следующий расчёт</dt>
              <dd>{formatNextCalculationAt(data.next_calculation_at)}</dd>
            </div>
          </dl>
        </>
      )}
    </section>
  )
}

function ratingEmptyCopy(
  status: RatingStatusResponse | undefined,
  statusLoading: boolean,
  statusError: boolean,
): { title: string; description: string } {
  if (status?.pending_matches_count) {
    return {
      title: 'Рейтинг ожидает расчёта',
      description: 'Завершённые рейтинговые матчи будут учтены при следующей публикации.',
    }
  }
  if (statusLoading || statusError || !status) {
    return {
      title: 'Рейтинг пока не опубликован',
      description: 'Информация об ожидающих матчах загружается отдельно.',
    }
  }
  return {
    title: 'Сыграйте первый рейтинговый матч',
    description: 'После ежедневного расчёта здесь появятся рейтинг и позиция.',
  }
}

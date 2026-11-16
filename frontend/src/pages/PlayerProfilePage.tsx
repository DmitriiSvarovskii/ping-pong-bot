import { useQuery } from '@tanstack/react-query'
import { Navigate, useParams } from 'react-router-dom'

import { ApiError } from '../api/client'
import { getPlayerRating } from '../api/ratings'
import { getPlayerProfile } from '../api/users'
import { useAuth } from '../auth/AuthProvider'
import { ProfileCard } from '../components/ProfileCard'
import { isRatingNotFound } from '../ratings/errors'
import { ratingKeys } from '../ratings/queryKeys'
import { RatingHistory } from '../ratings/RatingHistory'
import { RatingSummary } from '../ratings/RatingSummary'

const userProfileKeys = {
  player: (userId: string) => ['users', 'profile', userId] as const,
}

export function PlayerProfilePage() {
  const { userId } = useParams<{ userId: string }>()
  const { user } = useAuth()

  if (!userId) return <PlayerNotFound />
  if (userId === user?.id) return <Navigate to="/profile" replace />
  return <OtherPlayerProfile userId={userId} />
}

function OtherPlayerProfile({ userId }: { userId: string }) {
  const { apiClient } = useAuth()
  const profile = useQuery({
    queryKey: userProfileKeys.player(userId),
    queryFn: ({ signal }) => getPlayerProfile(apiClient, userId, signal),
    retry: (failureCount, error) => !isNotFound(error) && failureCount < 1,
  })
  const rating = useQuery({
    queryKey: ratingKeys.player(userId),
    queryFn: ({ signal }) => getPlayerRating(apiClient, userId, signal),
    retry: (failureCount, error) => !isRatingNotFound(error) && failureCount < 1,
  })

  if (profile.isPending) {
    return <PlayerProfileState loading />
  }
  if (isNotFound(profile.error)) {
    return <PlayerNotFound />
  }
  if (profile.error || !profile.data) {
    return <PlayerProfileState onRetry={() => void profile.refetch()} />
  }

  const ratingMissing = isRatingNotFound(rating.error)
  return (
    <div className="page-stack player-profile-page">
      <header className="page-header block">
        <p className="eyebrow">ИГРОК</p>
        <h1>Профиль</h1>
      </header>
      <ProfileCard user={profile.data} />
      <RatingSummary
        rating={rating.data}
        loading={rating.isPending}
        missing={ratingMissing}
        error={!!rating.error && !ratingMissing}
        emptyTitle="Рейтинг пока не опубликован"
        emptyDescription="У игрока ещё нет опубликованного рейтинга."
        onRetry={() => void rating.refetch()}
      />
      <RatingHistory userId={userId} />
    </div>
  )
}

function PlayerProfileState({
  loading = false,
  onRetry,
}: {
  loading?: boolean
  onRetry?: () => void
}) {
  return (
    <div className="page-stack player-profile-page">
      <header className="page-header block">
        <p className="eyebrow">ИГРОК</p>
        <h1>Профиль</h1>
      </header>
      <section className={`state-panel ${loading ? '' : 'error-panel'}`}>
        {loading ? (
          <><div className="loader" /><p>Загружаем профиль…</p></>
        ) : (
          <>
            <strong>Не удалось загрузить профиль</strong>
            <p>Проверьте соединение и попробуйте снова.</p>
            {onRetry && <button className="secondary-button" onClick={onRetry}>Повторить</button>}
          </>
        )}
      </section>
    </div>
  )
}

function PlayerNotFound() {
  return (
    <div className="page-stack player-profile-page">
      <header className="page-header block">
        <p className="eyebrow">ИГРОК</p>
        <h1>Профиль</h1>
      </header>
      <section className="state-panel">
        <strong>Игрок не найден</strong>
        <p>Профиль удалён или ссылка устарела.</p>
      </section>
    </div>
  )
}

function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404
}

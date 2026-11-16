import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { ApiError } from '../api/client'
import { getSession, correctSessionMatchResult } from '../api/gameSessions'
import { getMatch } from '../api/matches'
import { useAuth } from '../auth/AuthProvider'
import {
  getScorePayload,
  MatchScoreForm,
  type EditableSetScore,
} from '../components/MatchScoreForm'
import { ErrorPanel, LoadingPanel } from '../components/SessionUi'
import { sessionKeys, useInvalidateSession } from '../hooks/useGameSession'
import { ratingKeys } from '../ratings/queryKeys'
import type { MatchResponse } from '../types/matches'

const matchKeys = {
  detail: (matchId: string) => ['matches', 'detail', matchId] as const,
}

export function SessionMatchEditPage() {
  const { sessionId = '', matchId = '' } = useParams()
  const { apiClient, user } = useAuth()
  const session = useQuery({
    queryKey: sessionKeys.detail(sessionId),
    queryFn: ({ signal }) => getSession(apiClient, sessionId, signal),
    enabled: sessionId.length > 0,
  })
  const match = useQuery({
    queryKey: matchKeys.detail(matchId),
    queryFn: ({ signal }) => getMatch(apiClient, matchId, signal),
    enabled: matchId.length > 0,
    retry: (failureCount, error) => !isAccessError(error) && failureCount < 1,
    refetchOnMount: 'always',
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  })

  if (!sessionId || !matchId) return <EditUnavailable message="Матч не найден." />
  if (

    session.isPending ||

    match.isPending ||

    !match.isFetchedAfterMount

  ) {

    return <LoadingPanel label="Загружаем результат…" />

  }
  if (
    (isAccessError(session.error) && !session.data) ||
    (isAccessError(match.error) && !match.data)
  ) {
    return <EditUnavailable message={accessErrorMessage(session.error ?? match.error)} />
  }
  if (
    (session.error && !session.data) ||
    (match.error && !match.data) ||
    !session.data ||
    !match.data
  ) {
    return (
      <ErrorPanel
        error={session.error ?? match.error}
        onRetry={() => { void Promise.all([session.refetch(), match.refetch()]) }}
      />
    )
  }
  if (session.data.organizer_id !== user?.id) {
    return <EditUnavailable message="Редактировать результат может только организатор сессии." />
  }
  if (session.data.status !== 'active' && session.data.status !== 'completed') {
    return <EditUnavailable message="Результаты этой сессии нельзя редактировать." />
  }
  if (
    match.data.context !== 'game_session' ||
    match.data.game_session_id !== sessionId
  ) {
    return <EditUnavailable message="Матч не принадлежит этой игровой сессии." />
  }
  if (match.data.status !== 'completed') {
    return <EditUnavailable message="Редактировать можно только завершённый матч." />
  }

  return (
    <SessionMatchEditor
      key={`${match.data.id}:${match.data.version}`}
      match={match.data}
      sessionId={sessionId}
      refreshMatch={async () => {
        const result = await match.refetch()
        if (result.error) throw result.error
      }}
    />
  )
}

function SessionMatchEditor({
  match,
  sessionId,
  refreshMatch,
}: {
  match: MatchResponse
  sessionId: string
  refreshMatch: () => Promise<void>
}) {
  const { apiClient } = useAuth()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const invalidateSession = useInvalidateSession()
  const [conflict, setConflict] = useState(false)
  const [refreshFailed, setRefreshFailed] = useState(false)
  const [sets, setSets] = useState<EditableSetScore[]>(() => {
    const saved = [...match.sets]
      .sort((left, right) => left.set_number - right.set_number)
      .map((set) => ({
        player1_score: set.player1_score,
        player2_score: set.player2_score,
      }))
    return saved.length > 0 ? saved : [{ player1_score: 0, player2_score: 0 }]
  })
  const mutation = useMutation({
    mutationFn: () => {
      const payload = getScorePayload(sets)
      if (!payload) throw new Error('Score is incomplete')
      return correctSessionMatchResult(apiClient, sessionId, match.id, {
        sets: payload,
        expected_version: match.version,
      })
    },
    onSuccess: async () => {
      await Promise.all([
        invalidateSession(sessionId),
        queryClient.invalidateQueries({ queryKey: ratingKeys.all }),
        queryClient.invalidateQueries({ queryKey: matchKeys.detail(match.id) }),
      ])
      void navigate(`/games/${sessionId}`, { replace: true })
    },
    onError: (error) => {
      if (
        error instanceof ApiError &&
        error.status === 409 &&
        error.code === 'match_version_conflict'
      ) setConflict(true)
    },
  })

  async function loadCurrentResult() {
    setRefreshFailed(false)
    try {
      await refreshMatch()
    } catch {
      setRefreshFailed(true)
    }
  }

  return (
    <div className="page-stack match-edit-page">
      <header className="page-header block">
        <p className="eyebrow">МАТЧ #{match.sequence_number ?? '—'}</p>
        <h1>Редактировать результат</h1>
        <p>Участники и параметры матча останутся без изменений.</p>
      </header>
      {match.is_ranked && (
        <aside className="rating-correction-warning" role="note">
          Изменение результата повлияет на рейтинг игроков. Если матч уже учтён в рейтинге,
          будет выполнен пересчёт.
        </aside>
      )}
      <MatchScoreForm
        players={match.participants}
        sets={sets}
        onSetsChange={setSets}
        onSubmit={() => mutation.mutate()}
        pending={mutation.isPending}
        disabled={conflict}
        submitLabel="Сохранить изменения"
        notice={conflict ? (
          <div className="stale-warning" role="alert">
            <strong>Результат уже изменён</strong>
            <p>Ваши изменения не сохранены. Загрузите актуальный результат перед повтором.</p>
            {refreshFailed && (
              <p className="form-error">Не удалось загрузить актуальный результат.</p>
            )}
            <button
              type="button"
              className="secondary-button"
              onClick={() => void loadCurrentResult()}
            >
              Загрузить актуальный результат
            </button>
          </div>
        ) : null}
        error={mutation.error && !conflict ? (
          <p className="form-error">{correctionErrorMessage(mutation.error)}</p>
        ) : null}
      />
    </div>
  )
}

function EditUnavailable({ message }: { message: string }) {
  return (
    <div className="state-panel error-panel">
      <strong>Редактирование недоступно</strong>
      <p>{message}</p>
    </div>
  )
}

function isAccessError(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 403 || error.status === 404)
}

function accessErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 403) {
    return 'У вас нет доступа к этому матчу или игровой сессии.'
  }
  return 'Матч или игровая сессия не найдены.'
}

function correctionErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 403) return 'Редактировать результат может только организатор сессии.'
    if (error.status === 404) return 'Матч или игровая сессия больше не существуют.'
    if (error.status === 409) return 'Результат сейчас нельзя изменить. Обновите данные и повторите позже.'
    if (error.status === 422) return 'Проверьте счёт: результат не соответствует правилам матча.'
  }
  return 'Не удалось сохранить результат. Проверьте соединение и попробуйте снова.'
}

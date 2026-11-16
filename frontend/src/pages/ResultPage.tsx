import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { getCurrentSessionMatch, recordSessionResult } from '../api/gameSessions'
import { ApiError, type ApiClient } from '../api/client'
import { useAuth } from '../auth/AuthProvider'
import {
  getScorePayload,
  MatchScoreForm,
  type EditableSetScore,
} from '../components/MatchScoreForm'
import { ErrorPanel, LoadingPanel, sessionErrorMessage } from '../components/SessionUi'
import { getDisplayName } from '../hooks/useDisplayName'
import { sessionKeys, useInvalidateSession } from '../hooks/useGameSession'
import { useSessionRealtime } from '../realtime/useSessionRealtime'
import type {
  SessionMatch,
  SessionMatchCommandResponse,
} from '../types/gameSessions'
import {
  loadResultDraft,
  removeResultDraft,
  saveResultDraft,
} from './resultDraft'

export function ResultPage() {
  const { sessionId = '' } = useParams()
  const { apiClient, user } = useAuth()
  const invalidate = useInvalidateSession()
  useSessionRealtime(sessionId)
  const current = useQuery({
    queryKey: sessionKeys.currentMatch(sessionId),
    queryFn: ({ signal }) => getCurrentSessionMatch(apiClient, sessionId, signal),
  })
  const previousMatchId = useRef<string | null>(null)
  const currentMatchId = current.data?.id ?? null

  useEffect(() => {
    const previous = previousMatchId.current
    if (previous && previous !== currentMatchId && user?.id) {
      removeResultDraft(user.id, previous)
      previousMatchId.current = null
    }
    if (currentMatchId) previousMatchId.current = currentMatchId
  }, [currentMatchId, user?.id])

  if (current.isPending) return <LoadingPanel label="Загружаем матч…" />
  if (current.error && !current.data) {
    return <ErrorPanel error={current.error} onRetry={() => void current.refetch()} />
  }
  if (!current.data) {
    return (
      <div className="state-panel">
        <strong>Активного матча нет</strong>
        <Link className="secondary-button" to={`/games/${sessionId}`}>Вернуться к сессии</Link>
      </div>
    )
  }

  const match = current.data
  return (
    <ResultEditor
      key={`${match.id}:${match.version}`}
      apiClient={apiClient}
      match={match}
      sessionId={sessionId}
      userId={user?.id ?? ''}
      invalidate={invalidate}
      refreshCurrent={async () => { await current.refetch() }}
    />
  )
}

function ResultEditor({
  apiClient,
  invalidate,
  match,
  refreshCurrent,
  sessionId,
  userId,
}: {
  apiClient: ApiClient
  invalidate: (sessionId: string) => Promise<void>
  match: SessionMatch
  refreshCurrent: () => Promise<void>
  sessionId: string
  userId: string
}) {
  const [sets, setSets] = useState<EditableSetScore[]>(() =>
    loadResultDraft(userId, match.id) ?? [{ player1_score: 0, player2_score: 0 }])
  const [stale, setStale] = useState(false)
  const [result, setResult] = useState<SessionMatchCommandResponse | null>(null)
  const mutation = useMutation({
    mutationFn: () => {
      const payload = getScorePayload(sets)
      if (!payload) throw new Error('Score is incomplete')
      return recordSessionResult(apiClient, sessionId, match.id, payload, match.version)
    },
    onSuccess: async (response) => {
      removeResultDraft(userId, match.id)
      setResult(response)
      await invalidate(sessionId)
    },
    onError: async (error) => {
      if (error instanceof ApiError && error.status === 409) {
        setStale(true)
        await invalidate(sessionId)
      }
    },
  })

  useEffect(() => {
    if (userId) saveResultDraft(userId, match.id, sets)
  }, [match.id, sets, userId])

  async function refreshStale() {
    removeResultDraft(userId, match.id)
    setSets([{ player1_score: 0, player2_score: 0 }])
    setResult(null)
    await refreshCurrent()
    setStale(false)
  }

  if (result) {
    const winner = result.match.participants.find(
      (item) => item.user.id === result.match.winner_id,
    )?.user
    const next = result.current_match
      ? [...result.current_match.participants].sort((a, b) => a.position - b.position)
      : undefined
    return (
      <div className="result-success">
        <p className="eyebrow">РЕЗУЛЬТАТ СОХРАНЁН</p>
        <h1>{winner ? `${getDisplayName(winner)} победил` : 'Победитель определён'}</h1>
        <p>Backend подтвердил результат и обновил очередь.</p>
        {next && (
          <div className="next-pair">
            <span>Следующий матч #{result.current_match?.sequence_number}</span>
            <strong>{next.map((item) => getDisplayName(item.user)).join(' — ')}</strong>
          </div>
        )}
        <Link className="primary-button" to={`/games/${sessionId}`}>К игровой сессии</Link>
      </div>
    )
  }

  return (
    <div className="page-stack">
      <header className="page-header block">
        <p className="eyebrow">МАТЧ #{match.sequence_number}</p>
        <h1>Записать результат</h1>
        <p>Победителя определит сервер.</p>
      </header>
      <MatchScoreForm
        players={match.participants}
        sets={sets}
        onSetsChange={setSets}
        onSubmit={() => mutation.mutate()}
        pending={mutation.isPending}
        disabled={stale}
        notice={stale ? (
          <div className="stale-warning">
            <strong>Матч уже изменился</strong>
            <p>Старый результат не будет отправлен в новый матч.</p>
            <button
              type="button"
              className="secondary-button"
              onClick={() => void refreshStale()}
            >
              Загрузить актуальный матч
            </button>
          </div>
        ) : null}
        error={mutation.error && !stale ? (
          <p className="form-error">{sessionErrorMessage(mutation.error)}</p>
        ) : null}
      />
    </div>
  )
}

import { useMutation, useQuery } from '@tanstack/react-query'
import { useCallback, useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { getCurrentSessionMatch, recordSessionResult } from '../api/gameSessions'
import { ApiError, type ApiClient } from '../api/client'
import { useAuth } from '../auth/AuthProvider'
import { ErrorPanel, LoadingPanel, sessionErrorMessage } from '../components/SessionUi'
import { getDisplayName } from '../hooks/useDisplayName'
import { sessionKeys, useInvalidateSession } from '../hooks/useGameSession'
import { useTelegramBackButton } from '../hooks/useTelegramBackButton'
import { useSessionRealtime } from '../realtime/useSessionRealtime'
import type {
  SessionMatch,
  SessionMatchCommandResponse,
  SetScoreInput,
} from '../types/gameSessions'

type EditableScore = number | ''
type EditableSetScore = {
  player1_score: EditableScore
  player2_score: EditableScore
}

export function ResultPage() {
  const { sessionId = '' } = useParams()
  const navigate = useNavigate()
  const { apiClient } = useAuth()
  const invalidate = useInvalidateSession()
  useSessionRealtime(sessionId)
  const returnToSession = useCallback(() => {
    void navigate(`/games/${sessionId}`)
  }, [navigate, sessionId])
  useTelegramBackButton(returnToSession)
  const current = useQuery({
    queryKey: sessionKeys.currentMatch(sessionId),
    queryFn: ({ signal }) => getCurrentSessionMatch(apiClient, sessionId, signal),
  })

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
}: {
  apiClient: ApiClient
  invalidate: (sessionId: string) => Promise<void>
  match: SessionMatch
  refreshCurrent: () => Promise<void>
  sessionId: string
}) {
  const [sets, setSets] = useState<EditableSetScore[]>([
    { player1_score: 0, player2_score: 0 },
  ])
  const [stale, setStale] = useState(false)
  const [result, setResult] = useState<SessionMatchCommandResponse | null>(null)
  const mutation = useMutation({
    mutationFn: () => {
      const payload = getScorePayload(sets)
      if (!payload) throw new Error('Score is incomplete')
      return recordSessionResult(apiClient, sessionId, match.id, payload, match.version)
    },
    onSuccess: async (response) => {
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
  const players = [...match.participants].sort((a, b) => a.position - b.position)
  const canSubmit = getScorePayload(sets) !== null

  function updateSet(index: number, position: 1 | 2, value: EditableScore) {
    setSets((items) => items.map((item, itemIndex) => itemIndex === index
      ? { ...item, [position === 1 ? 'player1_score' : 'player2_score']: value }
      : item))
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    if (!mutation.isPending && !stale && canSubmit) mutation.mutate()
  }

  async function refreshStale() {
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
      <form className="score-form" onSubmit={submit}>
        {sets.map((set, index) => (
          <section className="set-editor" key={index}>
            <div className="section-title">
              <h2>Сет {index + 1}</h2>
              {sets.length > 1 && (
                <button
                  type="button"
                  className="quiet-button"
                  onClick={() => setSets((items) => items.filter(
                    (_, itemIndex) => itemIndex !== index,
                  ))}
                >
                  Удалить
                </button>
              )}
            </div>
            <div className="score-grid">
              <ScoreControl
                label={players[0] ? getDisplayName(players[0].user) : 'Игрок A'}
                value={set.player1_score}
                onChange={(value) => updateSet(index, 1, value)}
              />
              <span className="score-divider">:</span>
              <ScoreControl
                label={players[1] ? getDisplayName(players[1].user) : 'Игрок B'}
                value={set.player2_score}
                onChange={(value) => updateSet(index, 2, value)}
              />
            </div>
          </section>
        ))}
        {stale && (
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
        )}
        {mutation.error && !stale && (
          <p className="form-error">{sessionErrorMessage(mutation.error)}</p>
        )}
        <div className="score-form-actions">
          <button
            type="button"
            className="secondary-button"
            onClick={() => setSets((items) => [
              ...items,
              { player1_score: 0, player2_score: 0 },
            ])}
          >
            + Добавить сет
          </button>
          <button className="primary-button" disabled={mutation.isPending || stale || !canSubmit}>
            {mutation.isPending ? 'Сохраняем…' : 'Сохранить результат'}
          </button>
        </div>
      </form>
    </div>
  )
}

function ScoreControl({
  label,
  onChange,
  value,
}: {
  label: string
  value: EditableScore
  onChange: (value: EditableScore) => void
}) {
  const numericValue = value === '' ? 0 : value
  return (
    <label className="score-control">
      <span>{label}</span>
      <div>
        <button
          type="button"
          aria-label={`Уменьшить счёт ${label}`}
          onClick={() => onChange(Math.max(0, numericValue - 1))}
        >
          −
        </button>
        <input
          aria-label={`Счёт ${label}`}
          type="text"
          inputMode="numeric"
          pattern="[0-9]*"
          maxLength={5}
          value={value}
          onFocus={() => { if (value === 0) onChange('') }}
          onChange={(event) => {
            const next = event.target.value
            if (next === '') onChange('')
            else if (/^\d+$/.test(next) && Number(next) <= 32767) onChange(Number(next))
          }}
        />
        <button
          type="button"
          aria-label={`Увеличить счёт ${label}`}
          onClick={() => onChange(Math.min(32767, numericValue + 1))}
        >
          +
        </button>
      </div>
    </label>
  )
}

function getScorePayload(sets: EditableSetScore[]): SetScoreInput[] | null {
  if (!sets.every((set) => isValidScore(set.player1_score) && isValidScore(set.player2_score))) {
    return null
  }
  return sets.map((set) => ({
    player1_score: Number(set.player1_score),
    player2_score: Number(set.player2_score),
  }))
}

function isValidScore(value: EditableScore): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= 32767
}

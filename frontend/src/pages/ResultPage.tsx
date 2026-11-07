import { useMutation, useQuery } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link, useParams } from 'react-router-dom'

import { getCurrentSessionMatch, recordSessionResult } from '../api/gameSessions'
import { ApiError } from '../api/client'
import { useAuth } from '../auth/AuthProvider'
import { ErrorPanel, LoadingPanel, sessionErrorMessage } from '../components/SessionUi'
import { getDisplayName } from '../hooks/useDisplayName'
import { sessionKeys, useInvalidateSession } from '../hooks/useGameSession'
import type { SessionMatchCommandResponse, SetScoreInput } from '../types/gameSessions'

export function ResultPage() {
  const { sessionId = '' } = useParams()
  const { apiClient } = useAuth()
  const invalidate = useInvalidateSession()
  const current = useQuery({ queryKey: sessionKeys.currentMatch(sessionId), queryFn: ({ signal }) => getCurrentSessionMatch(apiClient, sessionId, signal) })
  const [sets, setSets] = useState<SetScoreInput[]>([{ player1_score: 0, player2_score: 0 }])
  const [stale, setStale] = useState(false)
  const [result, setResult] = useState<SessionMatchCommandResponse | null>(null)
  const mutation = useMutation({
    mutationFn: () => {
      if (!current.data) throw new Error('Current match is unavailable')
      return recordSessionResult(apiClient, sessionId, current.data.id, sets, current.data.version)
    },
    onSuccess: async (response) => { setResult(response); await invalidate(sessionId) },
    onError: async (error) => { if (error instanceof ApiError && error.status === 409) { setStale(true); await invalidate(sessionId) } },
  })
  if (current.isPending) return <LoadingPanel label="Загружаем матч…" />
  if (current.error) return <ErrorPanel error={current.error} onRetry={() => void current.refetch()} />
  if (!current.data) return <div className="state-panel"><strong>Активного матча нет</strong><Link className="secondary-button" to={`/games/${sessionId}`}>Вернуться к сессии</Link></div>
  const match = current.data
  const players = [...match.participants].sort((a, b) => a.position - b.position)
  function updateSet(index: number, position: 1 | 2, value: number) { setSets((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, [position === 1 ? 'player1_score' : 'player2_score']: Math.max(0, Math.min(32767, value)) } : item)) }
  function submit(event: FormEvent) { event.preventDefault(); if (!mutation.isPending && !stale && sets.every((item) => Number.isInteger(item.player1_score) && Number.isInteger(item.player2_score))) mutation.mutate() }
  async function refreshStale() { setSets([{ player1_score: 0, player2_score: 0 }]); setResult(null); await current.refetch(); setStale(false) }
  if (result) { const winner = result.match.participants.find((item) => item.user.id === result.match.winner_id)?.user; const next = result.current_match ? [...result.current_match.participants].sort((a, b) => a.position - b.position) : undefined; return <div className="result-success"><p className="eyebrow">РЕЗУЛЬТАТ СОХРАНЁН</p><h1>{winner ? `${getDisplayName(winner)} победил` : 'Победитель определён'}</h1><p>Backend подтвердил результат и обновил очередь.</p>{next && <div className="next-pair"><span>Следующий матч #{result.current_match?.sequence_number}</span><strong>{next.map((item) => getDisplayName(item.user)).join(' — ')}</strong></div>}<Link className="primary-button" to={`/games/${sessionId}`}>К игровой сессии</Link></div> }
  return <div className="page-stack"><header className="page-header block"><p className="eyebrow">МАТЧ #{match.sequence_number}</p><h1>Записать результат</h1><p>Победителя определит сервер.</p></header><form className="score-form" onSubmit={submit}>{sets.map((set, index) => <section className="set-editor" key={index}><div className="section-title"><h2>Сет {index + 1}</h2>{sets.length > 1 && <button type="button" className="quiet-button" onClick={() => setSets((items) => items.filter((_, itemIndex) => itemIndex !== index))}>Удалить</button>}</div><div className="score-grid"><ScoreControl label={players[0] ? getDisplayName(players[0].user) : 'Игрок A'} value={set.player1_score} onChange={(value) => updateSet(index, 1, value)} /><span className="score-divider">:</span><ScoreControl label={players[1] ? getDisplayName(players[1].user) : 'Игрок B'} value={set.player2_score} onChange={(value) => updateSet(index, 2, value)} /></div></section>)}<button type="button" className="secondary-button full-button" onClick={() => setSets((items) => [...items, { player1_score: 0, player2_score: 0 }])}>Добавить сет</button>{stale && <div className="stale-warning"><strong>Матч уже изменился</strong><p>Старый результат не будет отправлен в новый матч.</p><button type="button" className="secondary-button" onClick={() => void refreshStale()}>Загрузить актуальный матч</button></div>}{mutation.error && !stale && <p className="form-error">{sessionErrorMessage(mutation.error)}</p>}<button className="primary-button full-button" disabled={mutation.isPending || stale}>{mutation.isPending ? 'Сохраняем…' : 'Сохранить результат'}</button></form></div>
}

function ScoreControl({ label, value, onChange }: { label: string; value: number; onChange: (value: number) => void }) {
  return <label className="score-control"><span>{label}</span><div><button type="button" aria-label={`Уменьшить счёт ${label}`} onClick={() => onChange(value - 1)}>−</button><input aria-label={`Счёт ${label}`} type="number" min="0" max="32767" value={value} onChange={(event) => onChange(event.target.valueAsNumber || 0)} /><button type="button" aria-label={`Увеличить счёт ${label}`} onClick={() => onChange(value + 1)}>+</button></div></label>
}

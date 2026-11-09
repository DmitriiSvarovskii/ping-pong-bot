import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'

import { createSession } from '../api/gameSessions'
import { useAuth } from '../auth/AuthProvider'
import { sessionKeys } from '../hooks/useGameSession'
import { sessionErrorMessage } from '../components/SessionUi'

export function CreateSessionPage() {
  const { apiClient } = useAuth()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [points, setPoints] = useState(11)
  const [sets, setSets] = useState(1)
  const [ranked, setRanked] = useState(true)
  const mutation = useMutation({
    mutationFn: () => createSession(apiClient, { name: name.trim() || null, points_to_win: points, sets_to_win: sets, is_ranked: ranked }),
    onSuccess: (session) => { void queryClient.invalidateQueries({ queryKey: sessionKeys.all }); void navigate(`/games/${session.id}`, { replace: true }) },
  })
  function submit(event: FormEvent) {
    event.preventDefault()
    if (points < 1 || sets < 1 || mutation.isPending) return
    mutation.mutate()
  }
  return <div className="page-stack"><header className="page-header block"><p className="eyebrow">НОВАЯ ИГРА</p><h1>Создать сессию</h1><p>Организатор добавится автоматически.</p></header><form className="session-form" onSubmit={submit}><label>Название игры<input value={name} maxLength={128} onChange={(event) => setName(event.target.value)} placeholder="Например, вечер пятницы" /></label><div className="form-grid"><label>Очков в сете<input type="number" min="1" max="32767" value={points} onChange={(event) => setPoints(event.target.valueAsNumber)} /></label><label>Сетов для победы<input type="number" min="1" max="99" value={sets} onChange={(event) => setSets(event.target.valueAsNumber)} /></label></div><label className="toggle-row"><span><strong>Рейтинговая игра</strong><small>Результаты будут учтены рейтингом</small></span><input type="checkbox" checked={ranked} onChange={(event) => setRanked(event.target.checked)} /></label>{mutation.error && <p className="form-error">{sessionErrorMessage(mutation.error)}</p>}<button className="primary-button full-button" disabled={mutation.isPending || points < 1 || sets < 1}>{mutation.isPending ? 'Создаём…' : 'Создать игру'}</button></form></div>
}

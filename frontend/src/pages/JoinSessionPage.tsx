import { useMutation } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'

import { transitionSession } from '../api/gameSessions'
import { useAuth } from '../auth/AuthProvider'
import { sessionErrorMessage } from '../components/SessionUi'

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

export function JoinSessionPage() {
  const { apiClient } = useAuth()
  const navigate = useNavigate()
  const [sessionId, setSessionId] = useState('')
  const mutation = useMutation({ mutationFn: () => transitionSession(apiClient, sessionId.trim(), 'join'), onSuccess: (session) => void navigate(`/games/${session.id}`) })
  function submit(event: FormEvent) { event.preventDefault(); if (UUID_PATTERN.test(sessionId.trim())) mutation.mutate() }
  const valid = UUID_PATTERN.test(sessionId.trim())
  return <div className="page-stack"><header className="page-header block"><p className="eyebrow">ПРИСОЕДИНИТЬСЯ</p><h1>Войти в игру</h1><p>Введите ID сессии или откройте Telegram-приглашение.</p></header><form className="session-form" onSubmit={submit}><label>ID игровой сессии<input value={sessionId} onChange={(event) => setSessionId(event.target.value)} placeholder="00000000-0000-0000-0000-000000000000" /></label>{sessionId && !valid && <p className="form-error">Проверьте формат UUID.</p>}{mutation.error && <p className="form-error">{sessionErrorMessage(mutation.error)}</p>}<button className="primary-button full-button" disabled={!valid || mutation.isPending}>{mutation.isPending ? 'Присоединяем…' : 'Присоединиться'}</button></form></div>
}

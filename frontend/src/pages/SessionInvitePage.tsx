import { useMutation } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'

import { transitionSession } from '../api/gameSessions'
import { useAuth } from '../auth/AuthProvider'
import { PaddleIcon } from '../components/icons'
import { sessionErrorMessage } from '../components/SessionUi'

export function SessionInvitePage() {
  const { sessionId = '' } = useParams()
  const { apiClient } = useAuth()
  const navigate = useNavigate()
  const mutation = useMutation({ mutationFn: () => transitionSession(apiClient, sessionId, 'join'), onSuccess: (session) => void navigate(`/games/${session.id}`, { replace: true }) })
  return <div className="placeholder"><div className="placeholder-icon"><PaddleIcon/></div><p className="eyebrow">ПРИГЛАШЕНИЕ</p><h1>Вас зовут играть</h1><p>Backend проверит сессию и право присоединиться. Ссылка сама по себе не даёт доступа.</p>{mutation.error && <p className="form-error">{sessionErrorMessage(mutation.error)}</p>}<button className="primary-button" disabled={mutation.isPending} onClick={() => mutation.mutate()}>{mutation.isPending ? 'Присоединяем…' : 'Присоединиться'}</button></div>
}

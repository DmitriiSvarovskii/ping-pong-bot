import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import { listSessions } from '../api/gameSessions'
import { useAuth } from '../auth/AuthProvider'
import { ErrorPanel, LoadingPanel, StatusBadge } from '../components/SessionUi'
import { getDisplayName } from '../hooks/useDisplayName'
import { sessionKeys } from '../hooks/useGameSession'

const PAGE_SIZE = 20

export function SessionsPage() {
  const { apiClient } = useAuth()
  const [offset, setOffset] = useState(0)
  const query = useQuery({ queryKey: [...sessionKeys.all, offset], queryFn: ({ signal }) => listSessions(apiClient, offset, PAGE_SIZE, signal) })
  return <div className="page-stack"><header className="page-header block"><p className="eyebrow">ИГРОВЫЕ СЕССИИ</p><h1>Мои игры</h1></header>{query.isPending ? <LoadingPanel /> : query.error ? <ErrorPanel error={query.error} onRetry={() => void query.refetch()} /> : query.data.items.length === 0 ? <div className="state-panel"><strong>Здесь пока пусто</strong><p>Создайте первую игру или присоединитесь по приглашению.</p><Link className="primary-button" to="/games/create">Создать игру</Link></div> : <><div className="session-list">{query.data.items.map((session) => { const organizer = session.active_participants.find((item) => item.user.id === session.organizer_id)?.user; return <Link to={`/games/${session.id}`} className="session-list-item" key={session.id}><div><StatusBadge status={session.status}/><h2>{session.name || 'Игра без названия'}</h2><p>{organizer ? `Организатор: ${getDisplayName(organizer)}` : 'Игровая сессия'}</p></div><div className="session-list-meta"><span>{session.active_participants.length} игроков</span><time>{new Intl.DateTimeFormat('ru', { day: 'numeric', month: 'short' }).format(new Date(session.created_at))}</time></div></Link> })}</div><div className="pagination"><button className="quiet-button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Назад</button><span>{offset + 1}–{Math.min(offset + PAGE_SIZE, query.data.total)} из {query.data.total}</span><button className="quiet-button" disabled={offset + PAGE_SIZE >= query.data.total} onClick={() => setOffset(offset + PAGE_SIZE)}>Далее</button></div></>}</div>
}

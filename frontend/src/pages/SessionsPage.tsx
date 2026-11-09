import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'

import { listSessions } from '../api/gameSessions'
import { useAuth } from '../auth/AuthProvider'
import { ErrorPanel, LoadingPanel, StatusBadge } from '../components/SessionUi'
import { getDisplayName } from '../hooks/useDisplayName'
import { sessionKeys } from '../hooks/useGameSession'
import type { RealtimeUpdateEvent } from '../realtime/types'
import { useRealtimeChannel } from '../realtime/useRealtimeChannel'

const PAGE_SIZE = 20

export function SessionsPage() {
  const { apiClient, status } = useAuth()
  const queryClient = useQueryClient()
  const [offset, setOffset] = useState(0)
  const synchronization = useRef<{ offset: number; promise: Promise<void> } | null>(null)
  const pageAbortController = useRef<AbortController | null>(null)

  const query = useQuery({
    queryKey: sessionKeys.list(offset),
    queryFn: ({ signal }) => listSessions(apiClient, offset, PAGE_SIZE, signal),
  })

  useEffect(() => {
    const controller = new AbortController()
    pageAbortController.current = controller
    return () => {
      controller.abort()
      if (pageAbortController.current === controller) pageAbortController.current = null
    }
  }, [offset, status])

  const synchronize = useCallback(
    (signal: AbortSignal): Promise<void> => {
      if (signal.aborted) return Promise.resolve()
      if (synchronization.current?.offset === offset) return synchronization.current.promise

      const onAbort = () => {
        void queryClient.cancelQueries({ queryKey: sessionKeys.list(offset), exact: true })
      }
      signal.addEventListener('abort', onAbort, { once: true })

      const promise = queryClient
        .invalidateQueries({
          queryKey: sessionKeys.lists,
          refetchType: 'none',
        })
        .then(() => {
          if (signal.aborted) return undefined
          return queryClient.refetchQueries(
            { queryKey: sessionKeys.list(offset), exact: true, type: 'active' },
            { cancelRefetch: false },
          )
        })
        .then(() => undefined)
        .catch(() => undefined)
        .finally(() => {
          signal.removeEventListener('abort', onAbort)
          if (synchronization.current?.promise === promise) synchronization.current = null
        })
      synchronization.current = { offset, promise }
      return promise
    },
    [offset, queryClient],
  )

  const handleRealtimeEvent = useCallback(
    (event: RealtimeUpdateEvent) => {
      if (event.type !== 'sessions.updated') return
      const controller = pageAbortController.current
      if (controller) void synchronize(controller.signal)
    },
    [synchronize],
  )

  useRealtimeChannel({
    apiClient,
    channel: { type: 'sessions' },
    enabled: status === 'authenticated',
    onEvent: handleRealtimeEvent,
    onSynchronize: synchronize,
    onFallbackRefetch: synchronize,
  })

  if (query.isPending) return <PageFrame><LoadingPanel /></PageFrame>
  if (!query.data) {
    return <PageFrame><ErrorPanel error={query.error} onRetry={() => void query.refetch()} /></PageFrame>
  }

  return (
    <PageFrame>
      {query.data.items.length === 0 ? (
        <div className="state-panel">
          <strong>Здесь пока пусто</strong>
          <p>Создайте первую игру или присоединитесь по приглашению.</p>
          <Link className="primary-button" to="/games/create">Создать игру</Link>
        </div>
      ) : (
        <>
          <div className="session-list">
            {query.data.items.map((session) => {
              const organizer = session.active_participants.find(
                (item) => item.user.id === session.organizer_id,
              )?.user
              return (
                <Link to={`/games/${session.id}`} className="session-list-item" key={session.id}>
                  <div>
                    <StatusBadge status={session.status}/>
                    <h2>{session.name || 'Игра без названия'}</h2>
                    <p>{organizer ? `Организатор: ${getDisplayName(organizer)}` : 'Игровая сессия'}</p>
                  </div>
                  <div className="session-list-meta">
                    <span>{session.active_participants.length} игроков</span>
                    <time>{new Intl.DateTimeFormat('ru', { day: 'numeric', month: 'short' }).format(new Date(session.created_at))}</time>
                  </div>
                </Link>
              )
            })}
          </div>
          <div className="pagination">
            <button
              className="quiet-button"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              Назад
            </button>
            <span>{offset + 1}–{Math.min(offset + PAGE_SIZE, query.data.total)} из {query.data.total}</span>
            <button
              className="quiet-button"
              disabled={offset + PAGE_SIZE >= query.data.total}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              Далее
            </button>
          </div>
        </>
      )}
    </PageFrame>
  )
}

function PageFrame({ children }: { children: ReactNode }) {
  return (
    <div className="page-stack">
      <header className="page-header block">
        <p className="eyebrow">ИГРОВЫЕ СЕССИИ</p>
        <h1>Мои игры</h1>
      </header>
      {children}
    </div>
  )
}

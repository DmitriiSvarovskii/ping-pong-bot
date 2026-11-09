import { useInfiniteQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { listSessions } from '../api/gameSessions'
import { useAuth } from '../auth/AuthProvider'
import { ErrorPanel, LoadingPanel, StatusBadge } from '../components/SessionUi'
import { getDisplayName } from '../hooks/useDisplayName'
import { sessionKeys } from '../hooks/useGameSession'
import type { RealtimeUpdateEvent } from '../realtime/types'
import { useRealtimeChannel } from '../realtime/useRealtimeChannel'
import type { GameSession, SessionListResponse } from '../types/gameSessions'

const PAGE_SIZE = 20

export function SessionsPage() {
  const { apiClient, status, user } = useAuth()
  const queryClient = useQueryClient()
  const [searchParams] = useSearchParams()
  const initialOffset = parseOffset(searchParams.get('offset'))
  const synchronization = useRef<{ offset: number; promise: Promise<void> } | null>(null)
  const pageAbortController = useRef<AbortController | null>(null)
  const loadMoreElement = useRef<HTMLDivElement | null>(null)
  const nextPageRequest = useRef<Promise<void> | null>(null)
  const userScope = user?.id ?? 'anonymous'
  const listQueryKey = sessionKeys.list(initialOffset, userScope)

  const query = useInfiniteQuery({
    queryKey: listQueryKey,
    queryFn: ({ pageParam, signal }) => listSessions(apiClient, pageParam, PAGE_SIZE, signal),
    initialPageParam: initialOffset,
    getNextPageParam: (lastPage) => nextPageOffset(lastPage),
    gcTime: Infinity,
  })
  const { fetchNextPage, hasNextPage } = query

  useEffect(() => {
    const controller = new AbortController()
    pageAbortController.current = controller
    return () => {
      controller.abort()
      if (pageAbortController.current === controller) pageAbortController.current = null
    }
  }, [initialOffset, status])

  const synchronize = useCallback(
    (signal: AbortSignal): Promise<void> => {
      if (signal.aborted) return Promise.resolve()
      if (synchronization.current?.offset === initialOffset) {
        return synchronization.current.promise
      }

      const queryKey = sessionKeys.list(initialOffset, userScope)
      const onAbort = () => {
        void queryClient.cancelQueries({ queryKey, exact: true })
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
            { queryKey, exact: true, type: 'active' },
            { cancelRefetch: false },
          )
        })
        .then(() => undefined)
        .catch(() => undefined)
        .finally(() => {
          signal.removeEventListener('abort', onAbort)
          if (synchronization.current?.promise === promise) synchronization.current = null
        })
      synchronization.current = { offset: initialOffset, promise }
      return promise
    },
    [initialOffset, queryClient, userScope],
  )

  const loadNextPage = useCallback((): Promise<void> => {
    if (!hasNextPage) return Promise.resolve()
    if (nextPageRequest.current) return nextPageRequest.current

    const promise = fetchNextPage()
      .then(() => undefined)
      .finally(() => {
        if (nextPageRequest.current === promise) nextPageRequest.current = null
      })
    nextPageRequest.current = promise
    return promise
  }, [fetchNextPage, hasNextPage])

  useEffect(() => {
    const target = loadMoreElement.current
    if (!target || !hasNextPage || typeof IntersectionObserver === 'undefined') return

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) void loadNextPage()
      },
      { rootMargin: '240px 0px' },
    )
    observer.observe(target)
    return () => observer.disconnect()
  }, [hasNextPage, loadNextPage])

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

  const sessions = mergeSessions(query.data.pages)

  return (
    <PageFrame>
      {sessions.length === 0 ? (
        <div className="state-panel">
          <strong>Здесь пока пусто</strong>
          <p>Создайте первую игру или присоединитесь по приглашению.</p>
          <Link className="primary-button" to="/games/create">Создать игру</Link>
        </div>
      ) : (
        <>
          <div className="session-list">
            {sessions.map((session) => {
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
          {query.isFetchNextPageError ? (
            <div className="infinite-list-status error-panel" role="status">
              <span>Не удалось загрузить следующую часть истории.</span>
              <button className="secondary-button" onClick={() => void loadNextPage()}>
                Повторить
              </button>
            </div>
          ) : query.isFetchingNextPage ? (
            <div className="infinite-list-status" role="status">
              <div className="loader" />
              <span>Загружаем ещё…</span>
            </div>
          ) : !query.hasNextPage ? (
            <p className="infinite-list-status">Вся история загружена</p>
          ) : null}
          <div ref={loadMoreElement} className="load-more-sentinel" aria-hidden="true" />
        </>
      )}
    </PageFrame>
  )
}

function nextPageOffset(page: SessionListResponse): number | undefined {
  if (page.items.length === 0 || page.offset + page.items.length >= page.total) return undefined
  return page.offset + page.limit
}

function mergeSessions(pages: SessionListResponse[]): GameSession[] {
  const unique = new Map<string, GameSession>()
  for (const page of pages) {
    for (const session of page.items) {
      if (!unique.has(session.id)) unique.set(session.id, session)
    }
  }
  return [...unique.values()]
}

function parseOffset(value: string | null): number {
  if (value === null || !/^\d+$/.test(value)) return 0
  const offset = Number(value)
  return Number.isSafeInteger(offset) && offset % PAGE_SIZE === 0 ? offset : 0
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

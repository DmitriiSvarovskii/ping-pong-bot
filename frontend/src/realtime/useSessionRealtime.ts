import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef } from 'react'

import { useAuth } from '../auth/AuthProvider'
import { sessionKeys } from '../hooks/useGameSession'
import type { RealtimeUpdateEvent } from './types'
import { useRealtimeChannel } from './useRealtimeChannel'

export function useSessionRealtime(sessionId: string): void {
  const { apiClient, status } = useAuth()
  const queryClient = useQueryClient()
  const synchronization = useRef<{
    sessionId: string
    promise: Promise<void>
  } | null>(null)
  const pageAbortController = useRef<AbortController | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    pageAbortController.current = controller
    return () => {
      controller.abort()
      if (pageAbortController.current === controller) pageAbortController.current = null
    }
  }, [sessionId, status])

  const synchronize = useCallback(
    (signal: AbortSignal): Promise<void> => {
      if (!sessionId || signal.aborted) return Promise.resolve()
      if (synchronization.current?.sessionId === sessionId) {
        return synchronization.current.promise
      }

      const onAbort = () => {
        void queryClient.cancelQueries({ queryKey: sessionKeys.session(sessionId) })
      }
      signal.addEventListener('abort', onAbort, { once: true })

      const promise = queryClient
        .invalidateQueries({
          queryKey: sessionKeys.session(sessionId),
          refetchType: 'none',
        })
        .then(() => {
          if (signal.aborted) return undefined
          return queryClient.refetchQueries(
            { queryKey: sessionKeys.session(sessionId), type: 'active' },
            { cancelRefetch: false },
          )
        })
        .then(() => undefined)
        .catch(() => undefined)
        .finally(() => {
          signal.removeEventListener('abort', onAbort)
          if (synchronization.current?.promise === promise) synchronization.current = null
        })
      synchronization.current = { sessionId, promise }
      return promise
    },
    [queryClient, sessionId],
  )

  const handleEvent = useCallback(
    (event: RealtimeUpdateEvent) => {
      if (
        event.type !== 'session.updated' ||
        event.session_id !== sessionId
      ) {
        return
      }
      const controller = pageAbortController.current
      if (controller) void synchronize(controller.signal)
    },
    [sessionId, synchronize],
  )

  useRealtimeChannel({
    apiClient,
    channel: { type: 'session', sessionId },
    enabled: status === 'authenticated' && sessionId.length > 0,
    onEvent: handleEvent,
    onSynchronize: synchronize,
    onFallbackRefetch: synchronize,
  })
}

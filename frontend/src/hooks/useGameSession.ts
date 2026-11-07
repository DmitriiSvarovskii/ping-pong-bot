import { useQuery, useQueryClient } from '@tanstack/react-query'

import {
  getCurrentSessionMatch,
  getSession,
  getSessionQueue,
  listParticipants,
  listSessionMatches,
} from '../api/gameSessions'
import { useAuth } from '../auth/AuthProvider'

export const sessionKeys = {
  all: ['game-sessions'] as const,
  detail: (id: string) => ['game-sessions', id, 'detail'] as const,
  participants: (id: string) => ['game-sessions', id, 'participants'] as const,
  queue: (id: string) => ['game-sessions', id, 'queue'] as const,
  currentMatch: (id: string) => ['game-sessions', id, 'current-match'] as const,
  matches: (id: string) => ['game-sessions', id, 'matches'] as const,
}

export function useGameSession(sessionId: string) {
  const { apiClient } = useAuth()
  const detail = useQuery({
    queryKey: sessionKeys.detail(sessionId),
    queryFn: ({ signal }) => getSession(apiClient, sessionId, signal),
    refetchInterval: (query) => query.state.data?.status === 'active' ? 4_000 : false,
  })
  const isActive = detail.data?.status === 'active'
  const participants = useQuery({
    queryKey: sessionKeys.participants(sessionId),
    queryFn: ({ signal }) => listParticipants(apiClient, sessionId, signal),
    refetchInterval: isActive ? 4_000 : false,
  })
  const queue = useQuery({
    queryKey: sessionKeys.queue(sessionId),
    queryFn: ({ signal }) => getSessionQueue(apiClient, sessionId, signal),
    enabled: detail.data?.status === 'active',
    refetchInterval: isActive ? 4_000 : false,
  })
  const currentMatch = useQuery({
    queryKey: sessionKeys.currentMatch(sessionId),
    queryFn: ({ signal }) => getCurrentSessionMatch(apiClient, sessionId, signal),
    enabled: detail.data?.status === 'active',
    refetchInterval: isActive ? 4_000 : false,
  })
  const matches = useQuery({
    queryKey: sessionKeys.matches(sessionId),
    queryFn: ({ signal }) => listSessionMatches(apiClient, sessionId, signal),
    refetchInterval: isActive ? 4_000 : false,
  })
  return { detail, participants, queue, currentMatch, matches }
}

export function useInvalidateSession() {
  const queryClient = useQueryClient()
  return async (sessionId: string) => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: sessionKeys.all }),
      queryClient.invalidateQueries({ queryKey: sessionKeys.detail(sessionId) }),
      queryClient.invalidateQueries({ queryKey: sessionKeys.participants(sessionId) }),
      queryClient.invalidateQueries({ queryKey: sessionKeys.queue(sessionId) }),
      queryClient.invalidateQueries({ queryKey: sessionKeys.currentMatch(sessionId) }),
      queryClient.invalidateQueries({ queryKey: sessionKeys.matches(sessionId) }),
    ])
  }
}

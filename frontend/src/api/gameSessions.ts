import type {
  CreateSessionInput,
  GameSession,
  SessionListResponse,
  SessionMatch,
  SessionMatchCommandResponse,
  SessionMatchListResponse,
  SessionParticipantListResponse,
  SessionQueue,
  SetScoreInput,
} from '../types/gameSessions'
import { ApiError, type ApiClient } from './client'

export function createSession(client: ApiClient, input: CreateSessionInput, signal?: AbortSignal) {
  return client.request<GameSession>('/game-sessions', { method: 'POST', body: input, signal })
}

export function listSessions(client: ApiClient, offset = 0, limit = 20, signal?: AbortSignal) {
  return client.request<SessionListResponse>(`/game-sessions?limit=${limit}&offset=${offset}`, { signal })
}

export function getSession(client: ApiClient, sessionId: string, signal?: AbortSignal) {
  return client.request<GameSession>(`/game-sessions/${sessionId}`, { signal })
}

export function transitionSession(
  client: ApiClient,
  sessionId: string,
  action: 'join' | 'leave' | 'start' | 'complete' | 'cancel',
  signal?: AbortSignal,
) {
  return client.request<GameSession>(`/game-sessions/${sessionId}/${action}`, {
    method: 'POST',
    signal,
  })
}

export function listParticipants(client: ApiClient, sessionId: string, signal?: AbortSignal) {
  return client.request<SessionParticipantListResponse>(
    `/game-sessions/${sessionId}/participants?active_only=true&limit=100&offset=0`,
    { signal },
  )
}

export function getSessionQueue(client: ApiClient, sessionId: string, signal?: AbortSignal) {
  return client.request<SessionQueue>(`/game-sessions/${sessionId}/queue`, { signal })
}

export function listSessionMatches(client: ApiClient, sessionId: string, signal?: AbortSignal) {
  return client.request<SessionMatchListResponse>(
    `/game-sessions/${sessionId}/matches?limit=100&offset=0`,
    { signal },
  )
}

export async function getCurrentSessionMatch(
  client: ApiClient,
  sessionId: string,
  signal?: AbortSignal,
): Promise<SessionMatch | null> {
  try {
    return await client.request<SessionMatch>(`/game-sessions/${sessionId}/matches/current`, {
      signal,
    })
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null
    throw error
  }
}

export function recordSessionResult(
  client: ApiClient,
  sessionId: string,
  matchId: string,
  sets: SetScoreInput[],
  expectedVersion: number,
) {
  return client.request<SessionMatchCommandResponse>(
    `/game-sessions/${sessionId}/matches/current/result`,
    {
      method: 'PUT',
      body: { match_id: matchId, sets, expected_version: expectedVersion },
    },
  )
}

export function cancelCurrentMatch(
  client: ApiClient,
  sessionId: string,
  matchId: string,
  leave = false,
) {
  const action = leave ? 'cancel-and-leave' : 'cancel'
  return client.request<SessionMatchCommandResponse>(
    `/game-sessions/${sessionId}/matches/current/${action}`,
    { method: 'POST', body: { match_id: matchId } },
  )
}

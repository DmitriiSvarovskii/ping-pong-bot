import type {
  CorrectMatchResultInput,
  MatchResponse,
  VoidMatchInput,
} from '../types/matches'
import type { ApiClient } from './client'

export function getMatch(client: ApiClient, matchId: string, signal?: AbortSignal) {
  return client.request<MatchResponse>(`/matches/${encodeURIComponent(matchId)}`, { signal })
}

export function correctMatchResult(
  client: ApiClient,
  matchId: string,
  input: CorrectMatchResultInput,
  signal?: AbortSignal,
) {
  return client.request<MatchResponse>(`/matches/${encodeURIComponent(matchId)}/results`, {
    method: 'PATCH',
    body: input,
    signal,
  })
}

export function voidMatch(
  client: ApiClient,
  matchId: string,
  input: VoidMatchInput,
  signal?: AbortSignal,
) {
  return client.request<MatchResponse>(`/matches/${encodeURIComponent(matchId)}/void`, {
    method: 'POST',
    body: input,
    signal,
  })
}

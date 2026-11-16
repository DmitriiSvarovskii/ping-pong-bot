import type {
  CurrentRating,
  LeaderboardQuery,
  LeaderboardResponse,
  RatingHistoryQuery,
  RatingHistoryResponse,
  RatingStatusResponse,
} from '../types/ratings'
import type { ApiClient } from './client'

export function getMyRating(client: ApiClient, signal?: AbortSignal) {
  return client.request<CurrentRating>('/ratings/me', { signal })
}

export function getMyRatingStatus(client: ApiClient, signal?: AbortSignal) {
  return client.request<RatingStatusResponse>('/ratings/me/status', { signal })
}

export function getPlayerRating(client: ApiClient, userId: string, signal?: AbortSignal) {
  return client.request<CurrentRating>(`/ratings/${encodeURIComponent(userId)}`, { signal })
}

export function getRatingHistory(
  client: ApiClient,
  userId: string,
  query: RatingHistoryQuery = {},
  signal?: AbortSignal,
) {
  const params = paginationParams(query)
  if (query.from_date) params.set('from_date', query.from_date)
  if (query.to_date) params.set('to_date', query.to_date)
  return client.request<RatingHistoryResponse>(
    `/ratings/${encodeURIComponent(userId)}/history?${params.toString()}`,
    { signal },
  )
}

export function getLeaderboard(
  client: ApiClient,
  query: LeaderboardQuery = {},
  signal?: AbortSignal,
) {
  return client.request<LeaderboardResponse>(`/leaderboard?${paginationParams(query).toString()}`, {
    signal,
  })
}

function paginationParams(query: { limit?: number; offset?: number }): URLSearchParams {
  const params = new URLSearchParams()
  if (query.limit !== undefined) params.set('limit', String(query.limit))
  if (query.offset !== undefined) params.set('offset', String(query.offset))
  return params
}

import type { UserPublicProfile } from '../types/api'
import type { ApiClient } from './client'

export function getPlayerProfile(
  client: ApiClient,
  userId: string,
  signal?: AbortSignal,
): Promise<UserPublicProfile> {
  return client.request(`/users/${encodeURIComponent(userId)}`, { signal })
}

import type { UserPublicProfile } from '../types/api'

export function getDisplayName(user: Pick<UserPublicProfile, 'first_name' | 'last_name'>): string {
  return [user.first_name, user.last_name].filter(Boolean).join(' ')
}

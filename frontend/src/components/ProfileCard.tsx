import type { UserPublicProfile } from '../types/api'
import { getDisplayName } from '../hooks/useDisplayName'
import { UserIcon } from './icons'

export function ProfileCard({ user, compact = false }: { user: UserPublicProfile; compact?: boolean }) {
  return (
    <section className={`profile-card ${compact ? 'compact' : ''}`}>
      <Avatar user={user} />
      <div className="profile-copy">
        <p className="profile-name">{getDisplayName(user)}</p>
        <p className="profile-username">{user.username ? `@${user.username}` : 'Telegram username не указан'}</p>
      </div>
    </section>
  )
}

function Avatar({ user }: { user: UserPublicProfile }) {
  if (user.photo_url) return <img className="avatar" src={user.photo_url} alt="" />
  return <span className="avatar avatar-fallback"><UserIcon /></span>
}

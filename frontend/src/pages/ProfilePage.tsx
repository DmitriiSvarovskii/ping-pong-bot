import { useAuth } from '../auth/AuthProvider'
import { ProfileCard } from '../components/ProfileCard'

export function ProfilePage() {
  const { user } = useAuth()
  if (!user) return null
  return <div className="page-stack"><header className="page-header"><p className="eyebrow">ИГРОК</p><h1>Профиль</h1></header><ProfileCard user={user} /><section className="info-card"><div><span>Имя</span><strong>{user.first_name}</strong></div>{user.last_name && <div><span>Фамилия</span><strong>{user.last_name}</strong></div>}<div><span>Username</span><strong>{user.username ? `@${user.username}` : 'Не указан'}</strong></div></section></div>
}

import { Link } from 'react-router-dom'

import { useAuth } from '../auth/AuthProvider'
import { ChevronIcon, JoinIcon, PaddleIcon, PlusIcon, UserIcon } from '../components/icons'
import { ProfileCard } from '../components/ProfileCard'

const actions = [
  { to: '/games/create', title: 'Создать игру', copy: 'Начать новую игровую сессию', icon: <PlusIcon />, accent: true },
  { to: '/games/join', title: 'Присоединиться', copy: 'Войти в игру друзей', icon: <JoinIcon /> },
  { to: '/games', title: 'Мои игры', copy: 'История и активные игры', icon: <PaddleIcon /> },
  { to: '/profile', title: 'Профиль', copy: 'Ваши данные игрока', icon: <UserIcon /> },
]

export function HomePage() {
  const { user } = useAuth()
  if (!user) return null
  return (
    <div className="page-stack">
      <header className="home-header"><div><p className="eyebrow">PINGSTATS</p><h1>Пора играть</h1></div><span className="ball" /></header>
      <ProfileCard user={user} compact />
      <section className="action-grid" aria-label="Действия">
        {actions.map((action) => <Link key={action.to} to={action.to} className={`action-card ${action.accent ? 'accent' : ''}`}><span className="action-icon">{action.icon}</span><span><strong>{action.title}</strong><small>{action.copy}</small></span><ChevronIcon className="chevron" /></Link>)}
      </section>
    </div>
  )
}

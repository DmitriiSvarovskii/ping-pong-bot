import { NavLink, Outlet } from 'react-router-dom'

import { HomeIcon, PaddleIcon, UserIcon } from './icons'

export function AppShell() {
  return (
    <div className="app-shell">
      <main className="page-container"><Outlet /></main>
      <nav className="bottom-nav" aria-label="Основная навигация">
        <NavItem to="/" label="Главная" icon={<HomeIcon />} />
        <NavItem to="/games" label="Игры" icon={<PaddleIcon />} />
        <NavItem to="/profile" label="Профиль" icon={<UserIcon />} />
      </nav>
    </div>
  )
}

function NavItem({ to, label, icon }: { to: string; label: string; icon: React.ReactNode }) {
  return <NavLink to={to} end={to === '/'} className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>{icon}<span>{label}</span></NavLink>
}

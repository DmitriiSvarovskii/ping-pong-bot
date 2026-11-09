import { useCallback } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'

import { useRouteScrollRestoration } from '../hooks/useRouteScrollRestoration'
import { useTelegramBackButton } from '../hooks/useTelegramBackButton'
import { AppUpdateBanner } from './AppUpdateBanner'
import { HomeIcon, PaddleIcon, UserIcon } from './icons'

const ROOT_ROUTES = new Set(['/', '/games', '/profile'])

export function AppShell() {
  const location = useLocation()
  const navigate = useNavigate()
  const isRootRoute = ROOT_ROUTES.has(location.pathname)
  const returnToPrevious = useCallback(() => {
    if (hasInternalHistory(location.key, location.state)) {
      void navigate(-1)
      return
    }
    void navigate(fallbackRoute(location.pathname), {
      replace: true,
      state: { navigationFallback: true },
    })
  }, [location.key, location.pathname, location.state, navigate])

  useTelegramBackButton(returnToPrevious, !isRootRoute)
  useRouteScrollRestoration(`${location.key}:${location.pathname}${location.search}`)

  return (
    <div className="app-shell">
      <main className="page-container"><Outlet /></main>
      <AppUpdateBanner />
      <nav className="bottom-nav" aria-label="Основная навигация">
        <NavItem to="/" label="Главная" icon={<HomeIcon />} />
        <NavItem to="/games" label="Игры" icon={<PaddleIcon />} />
        <NavItem to="/profile" label="Профиль" icon={<UserIcon />} />
      </nav>
    </div>
  )
}

function NavItem({ to, label, icon }: { to: string; label: string; icon: React.ReactNode }) {
  const location = useLocation()
  const alreadySelected = location.pathname === to
  return <NavLink to={to} end={to === '/'} onClick={(event) => { if (alreadySelected) event.preventDefault() }} className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>{icon}<span>{label}</span></NavLink>
}

function hasInternalHistory(locationKey: string, state: unknown): boolean {
  if (isFallbackState(state)) return false
  const historyState = window.history.state as unknown
  if (typeof historyState === 'object' && historyState !== null && 'idx' in historyState) {
    const index = historyState.idx
    if (typeof index === 'number') return index > 0
  }
  return locationKey !== 'default'
}

function isFallbackState(state: unknown): boolean {
  return typeof state === 'object' && state !== null && 'navigationFallback' in state
}

function fallbackRoute(pathname: string): string {
  const resultMatch = /^\/games\/[^/]+\/result$/.test(pathname)
  if (resultMatch) return pathname.slice(0, -'/result'.length)
  if (pathname.startsWith('/games/')) return '/games'
  return '/'
}

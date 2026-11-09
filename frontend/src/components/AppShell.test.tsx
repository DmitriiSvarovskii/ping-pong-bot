import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BrowserRouter, Link, Route, Routes, useLocation } from 'react-router-dom'

import { AppShell } from './AppShell'

vi.mock('../auth/AuthProvider', () => ({
  useAuth: () => ({ user: { id: 'test-user' } }),
}))
vi.mock('../update/useAppUpdate', () => ({
  useAppUpdate: () => null,
}))

let telegramBack: (() => void) | undefined
let scrollY = 0
let scrollToMock = vi.fn((options?: number | ScrollToOptions, y?: number) => {
  void options
  void y
})

beforeEach(() => {
  window.history.replaceState(null, '', '/games')
  window.sessionStorage.clear()
  scrollY = 0
  Object.defineProperty(window, 'scrollY', { configurable: true, get: () => scrollY })
  scrollToMock = vi.fn((options?: number | ScrollToOptions, y?: number) => {
    scrollY = typeof options === 'number' && typeof y === 'number' ? y : 0
  })
  window.scrollTo = scrollToMock
  telegramBack = undefined
})

afterEach(() => {
  delete window.Telegram
  window.history.replaceState(null, '', '/')
})

it.each(['/', '/games', '/profile'])('hides Telegram Back Button on root route %s', (path) => {
  const bridge = telegramBridge()
  window.Telegram = { WebApp: bridge.webApp }
  window.history.replaceState(null, '', path)

  renderNavigation()

  expect(bridge.hide).toHaveBeenCalledOnce()
  expect(bridge.show).not.toHaveBeenCalled()
  expect(bridge.onClick).not.toHaveBeenCalled()
})

it('uses a logical fallback for a directly opened nested route', async () => {
  const bridge = telegramBridge()
  window.Telegram = { WebApp: bridge.webApp }
  window.history.replaceState(null, '', '/games/direct-session')
  renderNavigation()

  expect(screen.getByText('Session')).toBeInTheDocument()
  expect(bridge.show).toHaveBeenCalledOnce()
  act(() => telegramBack?.())

  expect(await screen.findByText('Games offset: none')).toBeInTheDocument()
  expect(window.location.pathname).toBe('/games')
  expect(historyIndex()).toBe(0)
})

it('returns through router history with query state and scroll position intact', async () => {
  const bridge = telegramBridge()
  window.Telegram = { WebApp: bridge.webApp }
  window.history.replaceState(null, '', '/games?offset=20')
  renderNavigation()
  await screen.findByText('Games offset: 20')
  await waitFor(() => expect(scrollToMock).toHaveBeenCalledWith(0, 0))
  scrollToMock.mockClear()

  scrollY = 480
  fireEvent.scroll(window)
  await waitFor(() => expect(
    window.sessionStorage.getItem('pingstats:scroll:default:/games?offset=20'),
  ).toBe('480'))
  await userEvent.click(screen.getByRole('link', { name: 'Open session' }))
  expect(await screen.findByText('Session')).toBeInTheDocument()
  await waitFor(() => expect(telegramBack).toBeTypeOf('function'))

  act(() => telegramBack?.())

  expect(await screen.findByText('Games offset: 20')).toBeInTheDocument()
  await waitFor(() => expect(scrollToMock).toHaveBeenCalledWith(0, 480))
  expect(bridge.onClick).toHaveBeenCalledOnce()
  expect(bridge.offClick).toHaveBeenCalledOnce()
})

it('does not push a duplicate history entry for the selected root tab', async () => {
  window.Telegram = { WebApp: telegramBridge().webApp }
  renderNavigation()
  const initialKey = screen.getByTestId('location-key').textContent

  await userEvent.click(screen.getByRole('link', { name: 'Игры' }))

  expect(screen.getByTestId('location-key')).toHaveTextContent(initialKey ?? '')
  expect(historyIndex()).toBe(0)
})

function renderNavigation() {
  return render(
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/" element={<Root label="Home" />} />
          <Route path="/profile" element={<Root label="Profile" />} />
          <Route path="/games" element={<Games />} />
          <Route path="/games/:sessionId" element={<Root label="Session" />} />
        </Route>
      </Routes>
    </BrowserRouter>,
  )
}

function Games() {
  const location = useLocation()
  const offset = new URLSearchParams(location.search).get('offset') ?? 'none'
  return <><Root label={`Games offset: ${offset}`} /><Link to="/games/session-id">Open session</Link></>
}

function Root({ label }: { label: string }) {
  const location = useLocation()
  return <><span>{label}</span><span data-testid="location-key">{location.key}</span></>
}

function telegramBridge() {
  const show = vi.fn()
  const hide = vi.fn()
  const onClick = vi.fn((callback: () => void) => { telegramBack = callback })
  const offClick = vi.fn((callback: () => void) => {
    if (telegramBack === callback) telegramBack = undefined
  })
  const webApp: TelegramWebApp = {
    initData: 'signed',
    colorScheme: 'light',
    platform: 'ios',
    version: '9.0',
    ready: vi.fn(),
    expand: vi.fn(),
    BackButton: {
      show,
      hide,
      onClick,
      offClick,
    },
  }
  return { webApp, show, hide, onClick, offClick }
}

function historyIndex(): number | undefined {
  const state = window.history.state as unknown
  if (typeof state !== 'object' || state === null || !('idx' in state)) return undefined
  return typeof state.idx === 'number' ? state.idx : undefined
}

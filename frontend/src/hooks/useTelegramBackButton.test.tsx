import { act, render } from '@testing-library/react'

import { useTelegramBackButton } from './useTelegramBackButton'

afterEach(() => {
  delete window.Telegram
})

it('keeps one Telegram handler across rerenders and calls the latest callback', () => {
  let telegramBack: (() => void) | undefined
  const onClick = vi.fn((callback: () => void) => { telegramBack = callback })
  const offClick = vi.fn()
  const show = vi.fn()
  const hide = vi.fn()
  window.Telegram = {
    WebApp: {
      initData: 'signed',
      colorScheme: 'light',
      platform: 'ios',
      version: '9.0',
      ready: vi.fn(),
      expand: vi.fn(),
      BackButton: { show, hide, onClick, offClick },
    },
  }
  const first = vi.fn()
  const second = vi.fn()

  const view = render(<Harness onBack={first} />)
  view.rerender(<Harness onBack={second} />)

  expect(onClick).toHaveBeenCalledOnce()
  expect(show).toHaveBeenCalledOnce()
  act(() => telegramBack?.())
  expect(first).not.toHaveBeenCalled()
  expect(second).toHaveBeenCalledOnce()

  view.unmount()
  expect(offClick).toHaveBeenCalledOnce()
  expect(offClick).toHaveBeenCalledWith(telegramBack)
  expect(hide).toHaveBeenCalledOnce()
})

it('works safely outside Telegram', () => {
  delete window.Telegram
  const onBack = vi.fn()
  const view = render(<Harness onBack={onBack} />)

  expect(onBack).not.toHaveBeenCalled()
  expect(() => view.unmount()).not.toThrow()
})

it('actively hides the Telegram Back Button when navigation is at a root route', () => {
  const hide = vi.fn()
  const onClick = vi.fn()
  window.Telegram = {
    WebApp: {
      initData: 'signed',
      colorScheme: 'light',
      platform: 'ios',
      version: '9.0',
      ready: vi.fn(),
      expand: vi.fn(),
      BackButton: { show: vi.fn(), hide, onClick, offClick: vi.fn() },
    },
  }

  const view = render(<Harness onBack={vi.fn()} enabled={false} />)

  expect(hide).toHaveBeenCalledOnce()
  expect(onClick).not.toHaveBeenCalled()
  view.unmount()
})

function Harness({ onBack, enabled = true }: { onBack: () => void; enabled?: boolean }) {
  useTelegramBackButton(onBack, enabled)
  return null
}

import { getSessionStartParam, initializeTelegram, sessionInvitationLink } from './telegram'

const dynamicProperties = [
  '--app-telegram-safe-area-inset-top',
  '--app-telegram-safe-area-inset-right',
  '--app-telegram-safe-area-inset-bottom',
  '--app-telegram-safe-area-inset-left',
  '--app-telegram-content-safe-area-inset-top',
  '--app-telegram-content-safe-area-inset-right',
  '--app-telegram-content-safe-area-inset-bottom',
  '--app-telegram-content-safe-area-inset-left',
  '--app-telegram-safe-content-inset-top',
  '--app-telegram-safe-content-inset-right',
  '--app-telegram-safe-content-inset-bottom',
  '--app-telegram-safe-content-inset-left',
  '--app-telegram-viewport-height',
]

afterEach(() => {
  delete window.Telegram
  for (const property of dynamicProperties) document.documentElement.style.removeProperty(property)
})

describe('initializeTelegram', () => {
  it('returns raw initData and initializes the official WebApp SDK', () => {
    const ready = vi.fn()
    const expand = vi.fn()
    window.Telegram = { WebApp: { initData: 'signed-raw-data', colorScheme: 'dark', platform: 'ios', version: '9.0', ready, expand } }

    const telegram = initializeTelegram()
    expect(telegram).toMatchObject({ initData: 'signed-raw-data', isAvailable: true })
    expect(ready).toHaveBeenCalledOnce()
    expect(expand).toHaveBeenCalledOnce()
    telegram.cleanup()
  })

  it('does not fabricate auth data outside Telegram', () => {
    delete window.Telegram
    expect(initializeTelegram()).toMatchObject({ initData: '', isAvailable: false })
  })

  it('requests fullscreen once on a supported mobile Telegram client', () => {
    const requestFullscreen = vi.fn()
    const disableVerticalSwipes = vi.fn()
    const expand = vi.fn()
    const webApp = {
      initData: 'signed-raw-data',
      colorScheme: 'dark' as const,
      platform: 'android',
      version: '9.0',
      ready: vi.fn(),
      expand,
      requestFullscreen,
      disableVerticalSwipes,
      isVersionAtLeast: vi.fn(() => true),
    }
    window.Telegram = { WebApp: webApp }

    const first = initializeTelegram()
    const second = initializeTelegram()

    expect(requestFullscreen).toHaveBeenCalledOnce()
    expect(disableVerticalSwipes).toHaveBeenCalledOnce()
    expect(expand).not.toHaveBeenCalled()
    first.cleanup()
    second.cleanup()
  })

  it('uses expand without requesting fullscreen on Telegram Desktop', () => {
    const requestFullscreen = vi.fn()
    const expand = vi.fn()
    window.Telegram = {
      WebApp: {
        initData: 'signed-raw-data',
        colorScheme: 'light',
        platform: 'tdesktop',
        version: '9.0',
        ready: vi.fn(),
        expand,
        requestFullscreen,
      },
    }

    const telegram = initializeTelegram()

    expect(requestFullscreen).not.toHaveBeenCalled()
    expect(expand).toHaveBeenCalledOnce()
    telegram.cleanup()
  })

  it('works when disableVerticalSwipes is unavailable', () => {
    window.Telegram = {
      WebApp: {
        initData: 'signed-raw-data',
        colorScheme: 'light',
        platform: 'ios',
        version: '7.6',
        ready: vi.fn(),
        expand: vi.fn(),
      },
    }

    expect(() => initializeTelegram().cleanup()).not.toThrow()
  })

  it('updates safe areas and viewport metrics after Telegram events', () => {
    const bridge = telegramBridge()
    window.Telegram = { WebApp: bridge.webApp }

    const telegram = initializeTelegram()
    expect(document.documentElement.style.getPropertyValue('--app-telegram-safe-area-inset-top'))
      .toBe('24px')
    expect(document.documentElement.style.getPropertyValue('--app-telegram-content-safe-area-inset-top'))
      .toBe('52px')
    expect(document.documentElement.style.getPropertyValue('--app-telegram-safe-content-inset-top'))
      .toBe('76px')
    expect(document.documentElement.style.getPropertyValue('--app-telegram-safe-content-inset-bottom'))
      .toBe('28px')
    expect(document.documentElement.style.getPropertyValue('--app-telegram-viewport-height'))
      .toBe('700px')

    bridge.values.safeAreaInset = { top: 30, right: 1, bottom: 20, left: 1 }
    bridge.emit('safeAreaChanged')
    bridge.values.contentSafeAreaInset = { top: 64, right: 2, bottom: 12, left: 2 }
    bridge.emit('contentSafeAreaChanged')
    bridge.values.viewportHeight = 430
    bridge.emit('viewportChanged')

    expect(document.documentElement.style.getPropertyValue('--app-telegram-safe-area-inset-top'))
      .toBe('30px')
    expect(document.documentElement.style.getPropertyValue('--app-telegram-content-safe-area-inset-top'))
      .toBe('64px')
    expect(document.documentElement.style.getPropertyValue('--app-telegram-safe-content-inset-top'))
      .toBe('94px')
    expect(document.documentElement.style.getPropertyValue('--app-telegram-safe-content-inset-bottom'))
      .toBe('32px')
    expect(document.documentElement.style.getPropertyValue('--app-telegram-viewport-height'))
      .toBe('430px')

    bridge.values.safeAreaInset = { top: 31, right: 1, bottom: 21, left: 1 }
    bridge.values.contentSafeAreaInset = { top: 65, right: 2, bottom: 13, left: 2 }
    bridge.values.viewportHeight = 690
    bridge.emit('activated')
    expect(document.documentElement.style.getPropertyValue('--app-telegram-safe-content-inset-top'))
      .toBe('96px')
    expect(document.documentElement.style.getPropertyValue('--app-telegram-safe-content-inset-bottom'))
      .toBe('34px')
    expect(document.documentElement.style.getPropertyValue('--app-telegram-viewport-height'))
      .toBe('690px')
    telegram.cleanup()
  })

  it('subscribes once and removes all viewport subscriptions after the final cleanup', () => {
    const bridge = telegramBridge()
    window.Telegram = { WebApp: bridge.webApp }

    const first = initializeTelegram()
    const second = initializeTelegram()
    expect(bridge.onEvent).toHaveBeenCalledTimes(4)

    first.cleanup()
    expect(bridge.offEvent).not.toHaveBeenCalled()
    second.cleanup()
    expect(bridge.offEvent).toHaveBeenCalledTimes(4)

    bridge.values.viewportHeight = 300
    bridge.emit('viewportChanged')
    expect(document.documentElement.style.getPropertyValue('--app-telegram-viewport-height'))
      .toBe('700px')
  })
})

describe('session invitations', () => {
  const sessionId = '123e4567-e89b-42d3-a456-426614174000'

  it('reads a strict session start parameter from signed raw initData', () => {
    window.Telegram = {
      WebApp: {
        initData: `query_id=test&start_param=session_${sessionId}&hash=signed`,
        colorScheme: 'light',
        platform: 'ios',
        version: '9.0',
        ready: vi.fn(),
        expand: vi.fn(),
      },
    }

    expect(getSessionStartParam()).toBe(sessionId)
    expect(sessionInvitationLink(sessionId)).toBe(
      `https://t.me/PongStatsBot/play?startapp=session_${sessionId}`,
    )
  })

  it('rejects malformed invitation parameters', () => {
    window.Telegram = {
      WebApp: {
        initData: 'start_param=session_not-a-uuid&hash=signed',
        colorScheme: 'light',
        platform: 'ios',
        version: '9.0',
        ready: vi.fn(),
        expand: vi.fn(),
      },
    }

    expect(getSessionStartParam()).toBeNull()
  })
})

function telegramBridge() {
  const handlers = new Map<TelegramWebAppEvent, Set<() => void>>()
  const values = {
    viewportHeight: 700,
    safeAreaInset: { top: 24, right: 0, bottom: 18, left: 0 },
    contentSafeAreaInset: { top: 52, right: 0, bottom: 10, left: 0 },
  }
  const onEvent = vi.fn((event: TelegramWebAppEvent, callback: () => void) => {
    const callbacks = handlers.get(event) ?? new Set()
    callbacks.add(callback)
    handlers.set(event, callbacks)
  })
  const offEvent = vi.fn((event: TelegramWebAppEvent, callback: () => void) => {
    handlers.get(event)?.delete(callback)
  })
  const raw = {
    initData: 'signed-raw-data',
    colorScheme: 'light' as const,
    platform: 'ios',
    version: '9.0',
    ready: vi.fn(),
    expand: vi.fn(),
    requestFullscreen: vi.fn(),
    disableVerticalSwipes: vi.fn(),
    isVersionAtLeast: vi.fn(() => true),
    onEvent,
    offEvent,
    get viewportHeight() {
      return values.viewportHeight
    },
    get safeAreaInset() {
      return values.safeAreaInset
    },
    get contentSafeAreaInset() {
      return values.contentSafeAreaInset
    },
  }
  const webApp = raw as unknown as TelegramWebApp
  return {
    webApp,
    values,
    onEvent,
    offEvent,
    emit(event: TelegramWebAppEvent) {
      for (const callback of handlers.get(event) ?? []) callback()
    },
  }
}

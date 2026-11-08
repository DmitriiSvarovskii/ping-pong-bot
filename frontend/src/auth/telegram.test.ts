import { getSessionStartParam, initializeTelegram, sessionInvitationLink } from './telegram'

describe('initializeTelegram', () => {
  it('returns raw initData and initializes the official WebApp SDK', () => {
    const ready = vi.fn()
    const expand = vi.fn()
    window.Telegram = { WebApp: { initData: 'signed-raw-data', colorScheme: 'dark', platform: 'ios', version: '9.0', ready, expand } }

    expect(initializeTelegram()).toEqual({ initData: 'signed-raw-data', isAvailable: true })
    expect(ready).toHaveBeenCalledOnce()
    expect(expand).toHaveBeenCalledOnce()
  })

  it('does not fabricate auth data outside Telegram', () => {
    delete window.Telegram
    expect(initializeTelegram()).toEqual({ initData: '', isAvailable: false })
  })

  it('requests fullscreen once on a supported mobile Telegram client', () => {
    const requestFullscreen = vi.fn()
    const expand = vi.fn()
    const webApp = {
      initData: 'signed-raw-data',
      colorScheme: 'dark' as const,
      platform: 'android',
      version: '9.0',
      ready: vi.fn(),
      expand,
      requestFullscreen,
      isVersionAtLeast: vi.fn(() => true),
    }
    window.Telegram = { WebApp: webApp }

    initializeTelegram()
    initializeTelegram()

    expect(requestFullscreen).toHaveBeenCalledOnce()
    expect(expand).not.toHaveBeenCalled()
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

    initializeTelegram()

    expect(requestFullscreen).not.toHaveBeenCalled()
    expect(expand).toHaveBeenCalledOnce()
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

export interface TelegramContext {
  initData: string
  isAvailable: boolean
  cleanup(): void
}

const mobilePlatforms = new Set(['android', 'android_x', 'ios'])
const viewportControllers = new WeakMap<TelegramWebApp, ViewportController>()
const insetSides = ['top', 'right', 'bottom', 'left'] as const
const noop = () => undefined

interface ViewportController {
  references: number
  initialized: boolean
  subscribed: boolean
  sync: () => void
  subscriptions: Array<{
    event: TelegramWebAppEvent
    callback: TelegramEventHandler<TelegramWebAppEvent>
  }>
}

export function initializeTelegram(): TelegramContext {
  const webApp = window.Telegram?.WebApp
  if (!webApp) {
    return { initData: '', isAvailable: false, cleanup: noop }
  }
  webApp.ready()
  const cleanup = acquireViewport(webApp)
  return { initData: webApp.initData, isAvailable: true, cleanup }
}

function acquireViewport(webApp: TelegramWebApp): () => void {
  let controller = viewportControllers.get(webApp)
  if (!controller) {
    controller = createViewportController(webApp)
    viewportControllers.set(webApp, controller)
  }
  controller.references += 1
  if (!controller.initialized) {
    controller.initialized = true
    initializeViewport(webApp)
  }
  controller.sync()
  subscribeViewport(webApp, controller)

  let released = false
  return () => {
    if (released) return
    released = true
    controller.references = Math.max(0, controller.references - 1)
    if (controller.references === 0) unsubscribeViewport(webApp, controller)
  }
}

function createViewportController(webApp: TelegramWebApp): ViewportController {
  return {
    references: 0,
    initialized: false,
    subscribed: false,
    sync: () => syncViewportMetrics(webApp),
    subscriptions: [],
  }
}

function initializeViewport(webApp: TelegramWebApp) {
  if (
    typeof webApp.disableVerticalSwipes === 'function' &&
    (webApp.isVersionAtLeast?.('7.7') ?? true)
  ) {
    try {
      webApp.disableVerticalSwipes()
    } catch {
      // A partially supported bridge must not block authentication or viewport setup.
    }
  }

  const supportsFullscreen =
    mobilePlatforms.has(webApp.platform) &&
    typeof webApp.requestFullscreen === 'function' &&
    (webApp.isVersionAtLeast?.('8.0') ?? true)

  if (supportsFullscreen) {
    try {
      webApp.requestFullscreen?.()
      return
    } catch {
      // Older or partially supported clients may expose the method but reject the request.
    }
  }
  webApp.expand()
}

function subscribeViewport(webApp: TelegramWebApp, controller: ViewportController) {
  if (controller.subscribed || !webApp.onEvent || !webApp.offEvent) return
  const sync = controller.sync as TelegramEventHandler<TelegramWebAppEvent>
  const supportsSafeAreaEvents =
    webApp.isVersionAtLeast?.('8.0') ??
    (webApp.safeAreaInset !== undefined || webApp.contentSafeAreaInset !== undefined)
  const events: TelegramWebAppEvent[] = supportsSafeAreaEvents
    ? ['viewportChanged', 'safeAreaChanged', 'contentSafeAreaChanged']
    : ['viewportChanged']
  for (const event of events) {
    try {
      webApp.onEvent(event, sync)
      controller.subscriptions.push({ event, callback: sync })
    } catch {
      // Older clients can expose onEvent while rejecting newer event names.
    }
  }
  controller.subscribed = controller.subscriptions.length > 0
}

function unsubscribeViewport(webApp: TelegramWebApp, controller: ViewportController) {
  if (!controller.subscribed || !webApp.offEvent) return
  for (const { event, callback } of controller.subscriptions) {
    try {
      webApp.offEvent(event, callback)
    } catch {
      // Cleanup is best-effort for incomplete legacy bridges.
    }
  }
  controller.subscriptions = []
  controller.subscribed = false
}

function syncViewportMetrics(webApp: TelegramWebApp) {
  const root = document.documentElement
  syncInset(root, 'safe-area', webApp.safeAreaInset)
  syncInset(root, 'content-safe-area', webApp.contentSafeAreaInset)
  const height = validPixels(webApp.viewportHeight)
  if (height === null) root.style.removeProperty('--app-telegram-viewport-height')
  else root.style.setProperty('--app-telegram-viewport-height', `${height}px`)
}

function syncInset(
  root: HTMLElement,
  name: 'safe-area' | 'content-safe-area',
  inset: TelegramSafeAreaInset | undefined,
) {
  for (const side of insetSides) {
    const property = `--app-telegram-${name}-inset-${side}`
    const value = validPixels(inset?.[side])
    if (value === null) root.style.removeProperty(property)
    else root.style.setProperty(property, `${value}px`)
  }
}

function validPixels(value: number | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null
}

export function getSessionStartParam(): string | null {
  const raw = window.Telegram?.WebApp.initData
  if (!raw) return null
  const startParam = new URLSearchParams(raw).get('start_param')
  const match = /^session_([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$/i.exec(
    startParam ?? '',
  )
  return match?.[1] ?? null
}

export function sessionInvitationLink(sessionId: string): string {
  return `https://t.me/PongStatsBot/play?startapp=session_${sessionId}`
}

export async function shareSessionInvitation(sessionId: string): Promise<'telegram' | 'clipboard'> {
  const invitation = sessionInvitationLink(sessionId)
  const webApp = window.Telegram?.WebApp
  if (webApp?.openTelegramLink) {
    const shareUrl = `https://t.me/share/url?url=${encodeURIComponent(invitation)}&text=${encodeURIComponent('Присоединяйся к игре в PingStats!')}`
    webApp.openTelegramLink(shareUrl)
    return 'telegram'
  }
  await navigator.clipboard.writeText(invitation)
  return 'clipboard'
}

export interface TelegramContext {
  initData: string
  isAvailable: boolean
}

export function initializeTelegram(): TelegramContext {
  const webApp = window.Telegram?.WebApp
  if (!webApp) {
    return { initData: '', isAvailable: false }
  }
  webApp.ready()
  webApp.expand()
  return { initData: webApp.initData, isAvailable: true }
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

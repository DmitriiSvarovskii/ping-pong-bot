interface TelegramWebApp {
  readonly initData: string
  readonly colorScheme: 'light' | 'dark'
  readonly platform: string
  readonly version: string
  ready(): void
  expand(): void
  requestFullscreen?(): void
  isVersionAtLeast?(version: string): boolean
  openTelegramLink?(url: string): void
  BackButton?: TelegramBackButton
}

interface TelegramBackButton {
  show(): void
  hide(): void
  onClick(callback: () => void): void
  offClick(callback: () => void): void
}

interface Window {
  Telegram?: {
    WebApp: TelegramWebApp
  }
}

interface TelegramWebApp {
  readonly initData: string
  readonly colorScheme: 'light' | 'dark'
  readonly platform: string
  readonly version: string
  ready(): void
  expand(): void
  openTelegramLink?(url: string): void
}

interface Window {
  Telegram?: {
    WebApp: TelegramWebApp
  }
}

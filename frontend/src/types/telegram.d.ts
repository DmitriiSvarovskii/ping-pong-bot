interface TelegramWebApp {
  readonly initData: string
  readonly colorScheme: 'light' | 'dark'
  readonly platform: string
  readonly version: string
  readonly viewportHeight?: number
  readonly safeAreaInset?: TelegramSafeAreaInset
  readonly contentSafeAreaInset?: TelegramSafeAreaInset
  ready(): void
  expand(): void
  requestFullscreen?(): void
  disableVerticalSwipes?(): void
  isVersionAtLeast?(version: string): boolean
  onEvent?<T extends TelegramWebAppEvent>(event: T, callback: TelegramEventHandler<T>): void
  offEvent?<T extends TelegramWebAppEvent>(event: T, callback: TelegramEventHandler<T>): void
  openTelegramLink?(url: string): void
  BackButton?: TelegramBackButton
}

interface TelegramSafeAreaInset {
  top: number
  right: number
  bottom: number
  left: number
}

type TelegramWebAppEvent =
  | 'viewportChanged'
  | 'safeAreaChanged'
  | 'contentSafeAreaChanged'

type TelegramEventHandler<T extends TelegramWebAppEvent> = T extends 'viewportChanged'
  ? (event: { isStateStable: boolean }) => void
  : () => void

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

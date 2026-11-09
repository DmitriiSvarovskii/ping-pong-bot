export const APP_BUILD_ID = __APP_BUILD_ID__
export const UPDATE_CHECK_INTERVAL_MS = 15 * 60 * 1000

type VersionFetcher = (signal: AbortSignal) => Promise<string>

interface AppUpdateControllerOptions {
  currentBuildId: string
  onUpdate: (buildId: string) => void
  fetchVersion?: VersionFetcher
  intervalMs?: number
}

export class AppUpdateController {
  private readonly currentBuildId: string
  private readonly onUpdate: (buildId: string) => void
  private readonly fetchVersion: VersionFetcher
  private readonly intervalMs: number
  private timer: number | null = null
  private request: Promise<void> | null = null
  private requestController: AbortController | null = null
  private running = false
  private notifiedBuildId: string | null = null

  constructor(options: AppUpdateControllerOptions) {
    this.currentBuildId = options.currentBuildId
    this.onUpdate = options.onUpdate
    this.fetchVersion = options.fetchVersion ?? fetchServerBuildId
    this.intervalMs = options.intervalMs ?? UPDATE_CHECK_INTERVAL_MS
  }

  start(): void {
    if (this.running) return
    this.running = true
    document.addEventListener('visibilitychange', this.handleVisibilityChange)
    this.timer = window.setInterval(() => { void this.checkNow() }, this.intervalMs)
    void this.checkNow()
  }

  stop(): void {
    if (!this.running) return
    this.running = false
    document.removeEventListener('visibilitychange', this.handleVisibilityChange)
    if (this.timer !== null) window.clearInterval(this.timer)
    this.timer = null
    this.requestController?.abort()
    this.requestController = null
    this.request = null
  }

  checkNow(): Promise<void> {
    if (!this.running || document.visibilityState === 'hidden') return Promise.resolve()
    if (this.request) return this.request

    const controller = new AbortController()
    this.requestController = controller
    const request = this.fetchVersion(controller.signal)
      .then((serverBuildId) => {
        if (
          this.running &&
          serverBuildId !== this.currentBuildId &&
          serverBuildId !== this.notifiedBuildId
        ) {
          this.notifiedBuildId = serverBuildId
          this.onUpdate(serverBuildId)
        }
      })
      .catch(() => undefined)
      .finally(() => {
        if (this.request === request) this.request = null
        if (this.requestController === controller) this.requestController = null
      })
    this.request = request
    return request
  }

  private readonly handleVisibilityChange = () => {
    if (document.visibilityState === 'visible') void this.checkNow()
  }
}

async function fetchServerBuildId(signal: AbortSignal): Promise<string> {
  const url = `/version.json?current=${encodeURIComponent(APP_BUILD_ID)}&t=${Date.now()}`
  const response = await fetch(url, {
    cache: 'no-store',
    credentials: 'same-origin',
    headers: { 'Cache-Control': 'no-cache' },
    signal,
  })
  if (!response.ok) throw new Error('Version manifest is unavailable')
  const payload = await response.json() as unknown
  if (!isVersionManifest(payload)) throw new Error('Version manifest is invalid')
  return payload.build_id
}

function isVersionManifest(value: unknown): value is { build_id: string } {
  return (
    typeof value === 'object' &&
    value !== null &&
    'build_id' in value &&
    typeof value.build_id === 'string' &&
    value.build_id.length > 0
  )
}

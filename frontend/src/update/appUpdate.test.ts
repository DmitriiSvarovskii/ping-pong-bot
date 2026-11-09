import {
  APP_BUILD_ID,
  AppUpdateController,
  UPDATE_CHECK_INTERVAL_MS,
} from './appUpdate'

beforeEach(() => {
  vi.useFakeTimers()
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    value: 'visible',
  })
})

afterEach(() => {
  vi.useRealTimers()
})

it('does not announce an update when the server build matches', async () => {
  const onUpdate = vi.fn()
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(versionResponse(APP_BUILD_ID))
  const controller = new AppUpdateController({ currentBuildId: APP_BUILD_ID, onUpdate })

  controller.start()
  await controller.checkNow()

  expect(onUpdate).not.toHaveBeenCalled()
  expect(fetchMock).toHaveBeenCalledWith(
    expect.stringContaining('/version.json?current='),
    expect.objectContaining({ cache: 'no-store', credentials: 'same-origin' }),
  )
  controller.stop()
})

it('announces a different server build only once', async () => {
  const onUpdate = vi.fn()
  const fetchVersion = vi.fn().mockResolvedValue('new-build')
  const controller = new AppUpdateController({
    currentBuildId: 'current-build',
    fetchVersion,
    onUpdate,
  })

  controller.start()
  await controller.checkNow()
  await controller.checkNow()

  expect(onUpdate).toHaveBeenCalledOnce()
  expect(onUpdate).toHaveBeenCalledWith('new-build')
  controller.stop()
})

it('ignores a temporary version-check error', async () => {
  const onUpdate = vi.fn()
  const controller = new AppUpdateController({
    currentBuildId: 'current-build',
    fetchVersion: vi.fn().mockRejectedValue(new TypeError('Offline')),
    onUpdate,
  })

  controller.start()
  await expect(controller.checkNow()).resolves.toBeUndefined()

  expect(onUpdate).not.toHaveBeenCalled()
  controller.stop()
})

it('checks after returning from background and skips checks while hidden', async () => {
  const fetchVersion = vi.fn().mockResolvedValue('current-build')
  const controller = new AppUpdateController({
    currentBuildId: 'current-build',
    fetchVersion,
    onUpdate: vi.fn(),
  })
  controller.start()
  await controller.checkNow()
  expect(fetchVersion).toHaveBeenCalledOnce()

  setVisibility('hidden')
  document.dispatchEvent(new Event('visibilitychange'))
  await Promise.resolve()
  expect(fetchVersion).toHaveBeenCalledOnce()

  setVisibility('visible')
  document.dispatchEvent(new Event('visibilitychange'))
  await waitForCallCount(fetchVersion, 2)
  controller.stop()
})

it('uses one periodic timer and removes lifecycle resources on stop', async () => {
  const fetchVersion = vi.fn().mockResolvedValue('current-build')
  const removeListener = vi.spyOn(document, 'removeEventListener')
  const controller = new AppUpdateController({
    currentBuildId: 'current-build',
    fetchVersion,
    onUpdate: vi.fn(),
  })

  controller.start()
  controller.start()
  await controller.checkNow()
  expect(vi.getTimerCount()).toBe(1)

  await vi.advanceTimersByTimeAsync(UPDATE_CHECK_INTERVAL_MS)
  expect(fetchVersion).toHaveBeenCalledTimes(2)

  controller.stop()
  expect(vi.getTimerCount()).toBe(0)
  expect(removeListener).toHaveBeenCalledWith('visibilitychange', expect.any(Function))
})

function setVisibility(value: DocumentVisibilityState) {
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    value,
  })
}

async function waitForCallCount(mock: ReturnType<typeof vi.fn>, count: number) {
  for (let attempt = 0; attempt < 10 && mock.mock.calls.length < count; attempt += 1) {
    await Promise.resolve()
  }
  expect(mock).toHaveBeenCalledTimes(count)
}

function versionResponse(buildId: string): Response {
  return new Response(JSON.stringify({ build_id: buildId }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

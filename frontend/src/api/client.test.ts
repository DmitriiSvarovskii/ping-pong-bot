import { ApiClient, ApiError } from './client'

describe('ApiClient', () => {
  it('adds the in-memory bearer token and forwards AbortSignal', async () => {
    const signal = new AbortController().signal
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ id: 'user-id' }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    )
    const client = new ApiClient()
    client.setAccessToken('memory-only-token')

    await client.request('/users/me', { signal })

    const [, options] = fetchMock.mock.calls[0]!
    expect(new Headers(options?.headers).get('Authorization')).toBe('Bearer memory-only-token')
    expect(options?.signal).toBe(signal)
  })

  it('normalizes API errors and clears a rejected session', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ error: { code: 'token_invalid', message: 'Invalid token', details: {} } }), { status: 401, headers: { 'Content-Type': 'application/json' } }),
    )
    const onUnauthorized = vi.fn()
    const client = new ApiClient()
    client.setAccessToken('expired-token')
    client.setUnauthorizedHandler(onUnauthorized)

    await expect(client.request('/users/me')).rejects.toMatchObject({
      status: 401,
      code: 'token_invalid',
    } satisfies Partial<ApiError>)
    expect(onUnauthorized).toHaveBeenCalledOnce()
  })

  it('recovers authentication and retries the rejected request once', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(apiResponse(401, { error: { code: 'token_invalid' } }))
      .mockResolvedValueOnce(apiResponse(200, { saved: true }))
    const recover = vi.fn().mockResolvedValue('renewed-token')
    const client = new ApiClient()
    client.setAccessToken('expired-token')
    client.setAuthenticationRecoveryHandler(recover)

    await expect(client.request('/game-sessions/session/result', {
      method: 'PUT',
      body: { sets: [{ player1_score: 11, player2_score: 5 }] },
    })).resolves.toEqual({ saved: true })

    expect(recover).toHaveBeenCalledOnce()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(requestAuthorization(fetchMock.mock.calls, 0)).toBe('Bearer expired-token')
    expect(requestAuthorization(fetchMock.mock.calls, 1)).toBe('Bearer renewed-token')
  })

  it('coalesces concurrent 401 responses into one authentication recovery', async () => {
    let resolveRecovery: ((token: string | null) => void) | undefined
    const recovery = new Promise<string | null>((resolve) => { resolveRecovery = resolve })
    const recover = vi.fn(() => recovery)
    vi.spyOn(globalThis, 'fetch').mockImplementation((_input, options) => {
      const authorization = new Headers(options?.headers).get('Authorization')
      return Promise.resolve(authorization === 'Bearer renewed-token'
        ? apiResponse(200, { ok: true })
        : apiResponse(401, { error: { code: 'token_invalid' } }))
    })
    const client = new ApiClient()
    client.setAccessToken('expired-token')
    client.setAuthenticationRecoveryHandler(recover)

    const requests = [client.request('/first'), client.request('/second')]
    await vi.waitFor(() => expect(recover).toHaveBeenCalledOnce())
    resolveRecovery?.('renewed-token')

    await expect(Promise.all(requests)).resolves.toEqual([{ ok: true }, { ok: true }])
    expect(recover).toHaveBeenCalledOnce()
  })

  it('ends the session once when concurrent authentication recovery is impossible', async () => {
    const onUnauthorized = vi.fn()
    const recover = vi.fn().mockResolvedValue(null)
    vi.spyOn(globalThis, 'fetch').mockImplementation(() =>
      Promise.resolve(apiResponse(401, { error: { code: 'token_invalid' } })))
    const client = new ApiClient()
    client.setAccessToken('expired-token')
    client.setAuthenticationRecoveryHandler(recover)
    client.setUnauthorizedHandler(onUnauthorized)

    const results = await Promise.allSettled([
      client.request('/first'),
      client.request('/second'),
    ])

    expect(results.every((result) => result.status === 'rejected')).toBe(true)
    expect(recover).toHaveBeenCalledOnce()
    expect(onUnauthorized).toHaveBeenCalledOnce()
  })

  it('does not enter a recovery loop when the retried request is also unauthorized', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(() =>
      Promise.resolve(apiResponse(401, { error: { code: 'token_invalid' } })))
    const recover = vi.fn().mockResolvedValue('renewed-token')
    const onUnauthorized = vi.fn()
    const client = new ApiClient()
    client.setAccessToken('expired-token')
    client.setAuthenticationRecoveryHandler(recover)
    client.setUnauthorizedHandler(onUnauthorized)

    await expect(client.request('/users/me')).rejects.toBeInstanceOf(ApiError)

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(recover).toHaveBeenCalledOnce()
    expect(onUnauthorized).toHaveBeenCalledOnce()
  })

  it('keeps the session available for a later retry when recovery loses the network', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      apiResponse(401, { error: { code: 'token_invalid' } }),
    )
    const onUnauthorized = vi.fn()
    const client = new ApiClient()
    client.setAccessToken('expired-token')
    client.setAuthenticationRecoveryHandler(() => Promise.reject(new TypeError('Offline')))
    client.setUnauthorizedHandler(onUnauthorized)

    await expect(client.request('/users/me')).rejects.toThrow('Offline')
    expect(onUnauthorized).not.toHaveBeenCalled()
  })
})

function apiResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function requestAuthorization(calls: Parameters<typeof fetch>[], index: number): string | null {
  const options = calls[index]?.[1]
  return new Headers(options?.headers).get('Authorization')
}

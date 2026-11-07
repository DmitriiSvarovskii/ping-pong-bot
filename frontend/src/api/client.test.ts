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
})

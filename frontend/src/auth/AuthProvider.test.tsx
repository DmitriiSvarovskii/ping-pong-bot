import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'

import { AuthProvider, useAuth } from './AuthProvider'

beforeEach(() => {
  window.Telegram = {
    WebApp: {
      initData: 'old-init-data-that-must-not-be-reused',
      colorScheme: 'light',
      platform: 'ios',
      version: '9.0',
      ready: vi.fn(),
      expand: vi.fn(),
    },
  }
})

afterEach(() => {
  delete window.Telegram
})

it('restores an existing browser session from the HttpOnly cookie before using initData', async () => {
  const requestedPaths: string[] = []
  let refreshCsrfHeader: string | null = null
  vi.spyOn(globalThis, 'fetch').mockImplementation((input, options) => {
    const path = typeof input === 'string'
      ? input
      : input instanceof URL
        ? input.toString()
        : input.url
    requestedPaths.push(path)
    if (path.endsWith('/auth/refresh')) {
      refreshCsrfHeader = new Headers(options?.headers).get('X-PingStats-CSRF')
      return Promise.resolve(jsonResponse({
        access_token: 'renewed-access-token',
        token_type: 'bearer',
        expires_in: 3600,
      }))
    }
    if (path.endsWith('/users/me')) {
      return Promise.resolve(jsonResponse({
        id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
        telegram_id: 123,
        first_name: 'Анна',
        last_name: null,
        username: 'anna',
        photo_url: null,
        created_at: '2026-11-09T10:00:00Z',
        updated_at: '2026-11-09T10:00:00Z',
        last_active_at: '2026-11-09T10:00:00Z',
      }))
    }
    return Promise.reject(new Error(`Unexpected request: ${path}`))
  })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  render(
    <QueryClientProvider client={client}>
      <AuthProvider><AuthState /></AuthProvider>
    </QueryClientProvider>,
  )

  expect(await screen.findByText('authenticated:Анна')).toBeInTheDocument()
  expect(requestedPaths).toContain('/api/v1/auth/refresh')
  expect(requestedPaths).not.toContain('/api/v1/auth/telegram')
  expect(refreshCsrfHeader).toBe('1')
})

function AuthState() {
  const auth = useAuth()
  return <span>{auth.status}:{auth.user?.first_name ?? 'none'}</span>
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

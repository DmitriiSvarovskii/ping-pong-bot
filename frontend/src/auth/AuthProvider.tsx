import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'

import { getCurrentUser, telegramLogin } from '../api/auth'
import { ApiClient, ApiError } from '../api/client'
import type { CurrentUser } from '../types/api'
import { initializeTelegram } from './telegram'

type AuthStatus = 'loading' | 'authenticated' | 'development-login' | 'error'

interface AuthContextValue {
  status: AuthStatus
  user: CurrentUser | null
  apiClient: ApiClient
  errorMessage: string | null
  retry(): void
  loginForDevelopment(): void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const client = useMemo(() => new ApiClient(), [])
  const queryClient = useQueryClient()
  const [accessToken, setAccessToken] = useState<string | null>(null)
  const [loginError, setLoginError] = useState<string | null>(null)
  const [awaitingDevelopmentLogin, setAwaitingDevelopmentLogin] = useState(false)
  const [attempt, setAttempt] = useState(0)

  const clearSession = useCallback(() => {
    client.setAccessToken(null)
    setAccessToken(null)
    setLoginError('Сессия завершена. Авторизуйтесь повторно.')
    void queryClient.removeQueries({ queryKey: ['current-user'] })
  }, [client, queryClient])

  useEffect(() => {
    client.setUnauthorizedHandler(clearSession)
    return () => client.setUnauthorizedHandler(null)
  }, [clearSession, client])

  const authenticateTelegram = useCallback(
    async (initData: string, signal?: AbortSignal) => {
      setLoginError(null)
      setAwaitingDevelopmentLogin(false)
      try {
        const response = await telegramLogin(client, initData, signal)
        client.setAccessToken(response.access_token)
        setAccessToken(response.access_token)
      } catch (error) {
        if (signal?.aborted) return
        setLoginError(authErrorMessage(error))
      }
    },
    [client],
  )

  useEffect(() => {
    const controller = new AbortController()
    let cleanupTelegram: () => void = () => undefined
    async function bootstrap() {
      await Promise.resolve()
      if (controller.signal.aborted) return
      const telegram = initializeTelegram()
      cleanupTelegram = () => telegram.cleanup()
      if (telegram.initData) {
        await authenticateTelegram(telegram.initData, controller.signal)
      } else if (import.meta.env.DEV) {
        setAwaitingDevelopmentLogin(true)
      } else {
        setLoginError('Откройте PingStats через Telegram, чтобы войти.')
      }
    }
    void bootstrap()
    return () => {
      controller.abort()
      cleanupTelegram()
    }
  }, [attempt, authenticateTelegram])

  const profile = useQuery({
    queryKey: ['current-user'],
    queryFn: ({ signal }) => getCurrentUser(client, signal),
    enabled: accessToken !== null,
    retry: 1,
  })

  const retry = useCallback(() => {
    client.setAccessToken(null)
    setAccessToken(null)
    setLoginError(null)
    setAwaitingDevelopmentLogin(false)
    setAttempt((value) => value + 1)
  }, [client])

  const loginForDevelopment = useCallback(() => {
    if (!import.meta.env.DEV) return
    setLoginError(null)
    setAwaitingDevelopmentLogin(false)
    void import('../api/developmentAuth')
      .then(({ developmentLogin }) => developmentLogin(client))
      .then((response) => {
        client.setAccessToken(response.access_token)
        setAccessToken(response.access_token)
      })
      .catch((error: unknown) => setLoginError(authErrorMessage(error)))
  }, [client])

  const profileError = profile.error ? authErrorMessage(profile.error) : null
  const value: AuthContextValue = {
    status: accessToken
      ? profile.data
        ? 'authenticated'
        : profileError
          ? 'error'
          : 'loading'
      : awaitingDevelopmentLogin
        ? 'development-login'
        : loginError
          ? 'error'
          : 'loading',
    user: profile.data ?? null,
    apiClient: client,
    errorMessage: loginError ?? profileError,
    retry,
    loginForDevelopment,
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

function authErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return 'Telegram не подтвердил данные авторизации. Откройте приложение заново.'
    if (error.status === 503) return 'Авторизация Telegram временно недоступна.'
  }
  return 'Не удалось войти. Проверьте соединение и попробуйте снова.'
}

// The colocated hook is the public access point for this provider.
// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used inside AuthProvider')
  return context
}

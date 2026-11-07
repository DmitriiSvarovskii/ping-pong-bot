import type {
  CurrentUser,
  TelegramTokenResponse,
} from '../types/api'
import type { ApiClient } from './client'

export function telegramLogin(
  client: ApiClient,
  initData: string,
  signal?: AbortSignal,
): Promise<TelegramTokenResponse> {
  return client.request('/auth/telegram', {
    method: 'POST',
    body: { init_data: initData },
    authenticated: false,
    signal,
  })
}

export function getCurrentUser(client: ApiClient, signal?: AbortSignal): Promise<CurrentUser> {
  return client.request('/users/me', { signal })
}

import type { DevelopmentTokenResponse } from '../types/api'
import type { ApiClient } from './client'

export function developmentLogin(
  client: ApiClient,
  signal?: AbortSignal,
): Promise<DevelopmentTokenResponse> {
  return client.request('/auth/dev-login', {
    method: 'POST',
    authenticated: false,
    signal,
  })
}

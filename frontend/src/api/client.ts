import type { ApiErrorEnvelope } from '../types/api'

const API_BASE_URL = '/api/v1'

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly details?: unknown,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

interface RequestOptions extends Omit<RequestInit, 'body'> {
  body?: unknown
  authenticated?: boolean
}

type AuthenticationRecoveryHandler = () => Promise<string | null>

export class ApiClient {
  #accessToken: string | null = null
  #onUnauthorized: (() => void) | null = null
  #authenticationRecoveryHandler: AuthenticationRecoveryHandler | null = null
  #authenticationRecovery: Promise<string | null> | null = null

  setAccessToken(token: string | null): void {
    this.#accessToken = token
  }

  setUnauthorizedHandler(handler: (() => void) | null): void {
    this.#onUnauthorized = handler
  }

  setAuthenticationRecoveryHandler(handler: AuthenticationRecoveryHandler | null): void {
    this.#authenticationRecoveryHandler = handler
  }

  async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    return this.#request(path, options, true)
  }

  async #request<T>(path: string, options: RequestOptions, allowRecovery: boolean): Promise<T> {
    const { body, authenticated = true, headers: suppliedHeaders, ...requestInit } = options
    const requestToken = authenticated ? this.#accessToken : null
    const headers = new Headers(suppliedHeaders)
    headers.set('Accept', 'application/json')
    if (body !== undefined) {
      headers.set('Content-Type', 'application/json')
    }
    if (requestToken) {
      headers.set('Authorization', `Bearer ${requestToken}`)
    }

    const response = await fetch(`${API_BASE_URL}${path}`, {
      ...requestInit,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    })

    if (!response.ok) {
      if (response.status === 401 && authenticated && requestToken) {
        if (allowRecovery && await this.#recoverAuthentication(requestToken)) {
          return this.#request(path, options, false)
        }
        this.#expireRejectedSession(requestToken)
      }
      throw await toApiError(response)
    }

    if (response.status === 204) {
      return undefined as T
    }
    return (await response.json()) as T
  }

  async #recoverAuthentication(rejectedToken: string): Promise<boolean> {
    if (this.#accessToken !== rejectedToken) return this.#accessToken !== null
    if (!this.#authenticationRecoveryHandler) return false
    if (!this.#authenticationRecovery) {
      this.#authenticationRecovery = this.#authenticationRecoveryHandler().finally(() => {
        this.#authenticationRecovery = null
      })
    }
    const recoveredToken = await this.#authenticationRecovery
    if (!recoveredToken) return false
    this.#accessToken = recoveredToken
    return true
  }

  #expireRejectedSession(rejectedToken: string): void {
    if (this.#accessToken !== rejectedToken) return
    this.#accessToken = null
    this.#onUnauthorized?.()
  }
}

async function toApiError(response: Response): Promise<ApiError> {
  let envelope: ApiErrorEnvelope = {}
  try {
    envelope = (await response.json()) as ApiErrorEnvelope
  } catch {
    // A safe generic error is preferable to exposing an upstream response body.
  }
  return new ApiError(
    response.status,
    envelope.error?.code ?? 'request_failed',
    envelope.error?.message ?? 'Не удалось выполнить запрос',
    envelope.error?.details,
  )
}

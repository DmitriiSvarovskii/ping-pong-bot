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

export class ApiClient {
  #accessToken: string | null = null
  #onUnauthorized: (() => void) | null = null

  setAccessToken(token: string | null): void {
    this.#accessToken = token
  }

  setUnauthorizedHandler(handler: (() => void) | null): void {
    this.#onUnauthorized = handler
  }

  async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const { body, authenticated = true, headers: suppliedHeaders, ...requestInit } = options
    const headers = new Headers(suppliedHeaders)
    headers.set('Accept', 'application/json')
    if (body !== undefined) {
      headers.set('Content-Type', 'application/json')
    }
    if (authenticated && this.#accessToken) {
      headers.set('Authorization', `Bearer ${this.#accessToken}`)
    }

    const response = await fetch(`${API_BASE_URL}${path}`, {
      ...requestInit,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    })

    if (!response.ok) {
      if (response.status === 401 && authenticated && this.#accessToken) {
        this.#accessToken = null
        this.#onUnauthorized?.()
      }
      throw await toApiError(response)
    }

    if (response.status === 204) {
      return undefined as T
    }
    return (await response.json()) as T
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

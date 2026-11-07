export interface TelegramTokenResponse {
  access_token: string
  token_type: 'bearer'
}

export interface DevelopmentTokenResponse extends TelegramTokenResponse {
  expires_in: number
  user: UserPublicProfile
}

export interface UserPublicProfile {
  id: string
  first_name: string
  last_name: string | null
  username: string | null
  photo_url: string | null
  created_at: string
  last_active_at: string | null
}

export interface CurrentUser extends UserPublicProfile {
  telegram_id: number | null
  updated_at: string
}

export interface ApiErrorEnvelope {
  error?: {
    code?: string
    message?: string
    details?: unknown
  }
}

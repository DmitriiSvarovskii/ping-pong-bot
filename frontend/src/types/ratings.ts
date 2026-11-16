export type DecimalString = string
export type RatingCalculationState =
  | 'up_to_date'
  | 'pending'
  | 'calculating'
  | 'rebuilding'
  | 'delayed'

export interface CurrentRating {
  user_id: string
  position: number
  rating: DecimalString
  rd: DecimalString
  effective_rd: DecimalString
  volatility: DecimalString
  rated_matches_count: number
  as_of_period_id: string
  as_of_period_ends_at: string
  inactivity_periods: number
  updated_at: string
}

export interface RatingHistoryItem {
  period_id: string
  period_starts_at: string
  period_ends_at: string
  published_at: string
  rating_before: DecimalString
  rating_after: DecimalString
  rd_before: DecimalString
  rd_after: DecimalString
  volatility_before: DecimalString
  volatility_after: DecimalString
  games_count: number
  is_active: boolean
}

export interface RatingHistoryResponse {
  items: RatingHistoryItem[]
  total: number
  limit: number
  offset: number
}

export interface RatingHistoryQuery {
  limit?: number
  offset?: number
  from_date?: string
  to_date?: string
}

export interface LeaderboardItem {
  position: number
  user_id: string
  first_name: string
  last_name: string | null
  username: string | null
  photo_url: string | null
  rating: DecimalString
  rd: DecimalString
  effective_rd: DecimalString
  rated_matches_count: number
}

export interface LeaderboardResponse {
  items: LeaderboardItem[]
  total: number
  limit: number
  offset: number
  current_user_position: number | null
}

export interface LeaderboardQuery {
  limit?: number
  offset?: number
}

export interface RatingStatusResponse {
  state: RatingCalculationState
  pending_matches_count: number
  next_calculation_at: string
}

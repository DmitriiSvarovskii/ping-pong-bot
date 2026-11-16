import type { UserPublicProfile } from './api'

export type MatchContext = 'standalone' | 'game_session' | 'tournament'
export type MatchStatus =
  | 'pending'
  | 'active'
  | 'completed'
  | 'voided'
  | 'cancelled'
  | 'rejected'
export type RatingState = 'not_applicable' | 'pending' | 'rated' | 'recalculating'

export interface MatchParticipant {
  position: number
  user: UserPublicProfile
}

export interface MatchSet {
  id: string
  set_number: number
  player1_score: number
  player2_score: number
  created_at: string
}

export interface MatchResponse {
  id: string
  context: MatchContext
  status: MatchStatus
  created_by: string
  points_to_win: number
  win_by: number
  sets_to_win: number
  is_ranked: boolean
  winner_id: string | null
  created_at: string
  updated_at: string
  started_at: string | null
  completed_at: string | null
  cancelled_at: string | null
  version: number
  rating_state: RatingState
  participants: MatchParticipant[]
  sets: MatchSet[]
}

export interface MatchListResponse {
  items: MatchResponse[]
  total: number
  limit: number
  offset: number
}

export interface SetScoreInput {
  player1_score: number
  player2_score: number
}

export interface CorrectMatchResultInput {
  sets: SetScoreInput[]
  expected_version: number
  reason?: string | null
}

export interface VoidMatchInput {
  expected_version: number
  reason?: string | null
}

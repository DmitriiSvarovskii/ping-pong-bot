import type { UserPublicProfile } from './api'

export type SessionStatus = 'pending' | 'active' | 'completed' | 'cancelled'
export type MatchStatus = 'pending' | 'active' | 'completed' | 'cancelled' | 'rejected'

export interface SessionParticipant {
  participation_id: string
  user: UserPublicProfile
  joined_at: string
  left_at: string | null
  is_active: boolean
}

export interface GameSession {
  id: string
  organizer_id: string
  status: SessionStatus
  name: string | null
  points_to_win: number
  sets_to_win: number
  is_ranked: boolean
  created_at: string
  started_at: string | null
  completed_at: string | null
  version: number
  active_participants: SessionParticipant[]
}

export interface SessionListResponse {
  items: GameSession[]
  total: number
  limit: number
  offset: number
}

export interface SessionParticipantListResponse {
  items: SessionParticipant[]
  total: number
  limit: number
  offset: number
}

export interface QueueParticipant {
  participation_id: string
  user_id: string
  joined_at: string
}

export interface SessionQueue {
  current_pair: QueueParticipant[]
  waiting: QueueParticipant[]
  is_paused: boolean
}

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

export interface SessionMatch {
  id: string
  context: 'game_session'
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
  participants: MatchParticipant[]
  sets: MatchSet[]
  game_session_id: string
  sequence_number: number
}

export interface SessionMatchListResponse {
  items: SessionMatch[]
  total: number
  limit: number
  offset: number
}

export interface SessionMatchCommandResponse {
  match: SessionMatch
  current_match: SessionMatch | null
  queue: SessionQueue | null
}

export interface CreateSessionInput {
  name: string | null
  points_to_win: number
  sets_to_win: number
  is_ranked: boolean
}

export interface SetScoreInput {
  player1_score: number
  player2_score: number
}

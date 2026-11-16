import type { UserPublicProfile } from './api'
import type { MatchResponse } from './matches'

export type {
  MatchParticipant,
  MatchSet,
  MatchStatus,
  RatingState,
  SetScoreInput,
} from './matches'

export type SessionStatus = 'pending' | 'active' | 'completed' | 'cancelled'

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

export interface SessionMatch extends MatchResponse {
  context: 'game_session'
  game_session_id: string
  sequence_number: number
}

export interface SessionMatchListResponse {
  items: SessionMatch[]
  total: number
  limit: number
  offset: number
}

export interface SessionPlayerStatistics {
  user: UserPublicProfile
  wins: number
  losses: number
  points_for: number
  points_against: number
  point_difference: number
}

export interface SessionStatisticsResponse {
  items: SessionPlayerStatistics[]
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

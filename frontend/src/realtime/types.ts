export const REALTIME_PROTOCOL = 'pingstats.v1'
export const REALTIME_TICKET_PROTOCOL_PREFIX = 'pingstats.ticket.'

export type RealtimeChannel =
  | { readonly type: 'sessions' }
  | { readonly type: 'session'; readonly sessionId: string }

export interface HeartbeatEvent {
  readonly type: 'heartbeat'
  readonly timestamp: string
}

export interface SessionUpdatedEvent {
  readonly type: 'session.updated'
  readonly session_id: string
  readonly timestamp: string
}

export interface SessionsUpdatedEvent {
  readonly type: 'sessions.updated'
  readonly timestamp: string
}

export type RealtimeUpdateEvent = SessionUpdatedEvent | SessionsUpdatedEvent

export type RealtimeServerEvent = HeartbeatEvent | RealtimeUpdateEvent

export interface WebSocketTicketResponse {
  readonly ticket: string
  readonly expires_in: number
}

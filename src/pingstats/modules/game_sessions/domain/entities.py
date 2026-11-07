"""Framework-independent game session entities and lifecycle values."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class GameSessionStatus(StrEnum):
    """Persisted lifecycle states for a free game session."""

    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class GameSessionEventType(StrEnum):
    CREATED = "created"
    JOINED = "joined"
    LEFT = "left"
    STARTED = "started"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    QUEUE_INITIALIZED = "queue_initialized"
    PLAYER_ENQUEUED = "player_enqueued"
    PLAYER_DEQUEUED = "player_dequeued"
    ROTATION = "rotation"
    PLAYER_LEFT_QUEUE = "player_left_queue"
    CURRENT_GAME_CANCELLED = "current_game_cancelled"
    MATCH_STARTED = "match_started"
    MATCH_CANCELLED = "match_cancelled"


@dataclass(frozen=True, slots=True)
class GameSession:
    id: UUID
    organizer_id: UUID
    status: GameSessionStatus
    name: str | None
    points_to_win: int
    sets_to_win: int
    is_ranked: bool
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    version: int


@dataclass(frozen=True, slots=True)
class GameSessionParticipant:
    id: UUID
    session_id: UUID
    user_id: UUID
    joined_at: datetime
    left_at: datetime | None

    @property
    def is_active(self) -> bool:
        return self.left_at is None


@dataclass(frozen=True, slots=True)
class GameSessionQueueEvent:
    id: UUID
    session_id: UUID
    participant_id: UUID | None
    event_type: GameSessionEventType
    payload: dict[str, object]
    created_at: datetime

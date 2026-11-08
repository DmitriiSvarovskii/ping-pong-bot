"""Domain types for free game sessions."""

from pingstats.modules.game_sessions.domain.entities import (
    GameSession,
    GameSessionEventType,
    GameSessionParticipant,
    GameSessionQueueEvent,
    GameSessionStatus,
)
from pingstats.modules.game_sessions.domain.queue import (
    GameSessionQueueState,
    InvalidQueueStateError,
    LoserRotatesStrategy,
    append_waiting,
    initialize_queue,
    remove_waiting,
    restore_queue_after_cancel,
    skip_current_players,
)

__all__ = [
    "GameSession",
    "GameSessionEventType",
    "GameSessionParticipant",
    "GameSessionQueueEvent",
    "GameSessionStatus",
    "GameSessionQueueState",
    "InvalidQueueStateError",
    "LoserRotatesStrategy",
    "append_waiting",
    "initialize_queue",
    "remove_waiting",
    "restore_queue_after_cancel",
    "skip_current_players",
]

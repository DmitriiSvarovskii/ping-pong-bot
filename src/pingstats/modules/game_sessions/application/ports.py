"""Persistence ports for game session application services."""

from collections.abc import Sequence
from datetime import datetime
from types import TracebackType
from typing import Protocol
from uuid import UUID

from pingstats.modules.game_sessions.application.dto import (
    GameSessionParticipantItem,
    GameSessionView,
)
from pingstats.modules.game_sessions.domain import (
    GameSession,
    GameSessionEventType,
    GameSessionParticipant,
    GameSessionQueueEvent,
    GameSessionQueueState,
    GameSessionStatus,
)
from pingstats.modules.matches.application.ports import MatchRepository


class GameSessionRepository(Protocol):
    async def lock_user(self, user_id: UUID) -> bool: ...

    async def user_exists(self, user_id: UUID) -> bool: ...

    async def create_session(
        self,
        *,
        organizer_id: UUID,
        name: str | None,
        points_to_win: int,
        sets_to_win: int,
        is_ranked: bool,
    ) -> GameSession: ...

    async def get_session(
        self, session_id: UUID, *, for_update: bool = False
    ) -> GameSession | None: ...

    async def get_view(self, session_id: UUID) -> GameSessionView | None: ...

    async def list_for_user(
        self, user_id: UUID, *, limit: int, offset: int
    ) -> tuple[Sequence[GameSessionView], int]: ...

    async def has_participated(self, session_id: UUID, user_id: UUID) -> bool: ...

    async def get_active_participant(
        self, session_id: UUID, user_id: UUID
    ) -> GameSessionParticipant | None: ...

    async def add_participant(
        self, session_id: UUID, user_id: UUID, *, joined_at: datetime
    ) -> GameSessionParticipant: ...

    async def close_participant(
        self, participant: GameSessionParticipant, *, left_at: datetime
    ) -> GameSessionParticipant: ...

    async def count_active_participants(self, session_id: UUID) -> int: ...

    async def list_active_participants(
        self, session_id: UUID
    ) -> Sequence[GameSessionParticipant]: ...

    async def get_participants_by_ids(
        self, session_id: UUID, participant_ids: Sequence[UUID]
    ) -> Sequence[GameSessionParticipant]: ...

    async def get_queue_state(self, session_id: UUID) -> GameSessionQueueState | None: ...

    async def save_queue_state(self, session_id: UUID, state: GameSessionQueueState) -> None: ...

    async def clear_queue_state(self, session_id: UUID) -> None: ...

    async def has_processed_result(self, session_id: UUID, result_id: UUID) -> bool: ...

    async def mark_result_processed(self, session_id: UUID, result_id: UUID) -> None: ...

    async def update_session(
        self,
        session: GameSession,
        *,
        status: GameSessionStatus | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> GameSession: ...

    async def add_event(
        self,
        *,
        session_id: UUID,
        participant_id: UUID | None,
        event_type: GameSessionEventType,
        payload: dict[str, object],
    ) -> GameSessionQueueEvent: ...

    async def list_participants(
        self,
        session_id: UUID,
        *,
        active_only: bool,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[GameSessionParticipantItem], int]: ...


class CurrentSessionMatchChecker(Protocol):
    async def has_active_match(self, session_id: UUID, user_id: UUID | None = None) -> bool: ...


class GameSessionsUnitOfWork(Protocol):
    game_sessions: GameSessionRepository
    current_matches: CurrentSessionMatchChecker
    matches: MatchRepository

    async def __aenter__(self) -> "GameSessionsUnitOfWork": ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...

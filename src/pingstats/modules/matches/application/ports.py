"""Persistence ports used by match application services."""

from collections.abc import Awaitable, Callable, Hashable, Sequence
from datetime import datetime
from types import TracebackType
from typing import Protocol
from uuid import UUID

from pingstats.modules.matches.application.dto import MatchView, SessionMatchSummary
from pingstats.modules.matches.domain import (
    Match,
    MatchContext,
    MatchResultRevision,
    MatchRevisionOperation,
    MatchStatus,
    ResultSnapshot,
    SetScore,
)
from pingstats.modules.ratings.application.ports import (
    RatingInputRepository,
    RatingPeriodRepository,
    RatingRebuildJobRepository,
    RatingRebuildPeriodRepository,
    RatingRecalculationRepository,
)


class MatchRepository(Protocol):
    async def lock_user_pair(self, first_id: UUID, second_id: UUID) -> set[UUID]: ...

    async def create_standalone(
        self,
        *,
        created_by: UUID,
        invited_user_id: UUID,
        points_to_win: int,
        win_by: int,
        sets_to_win: int,
        is_ranked: bool,
    ) -> MatchView: ...

    async def create_game_session_match(
        self,
        *,
        created_by: UUID,
        session_id: UUID,
        sequence_number: int,
        player1_user_id: UUID,
        player2_user_id: UUID,
        points_to_win: int,
        win_by: int,
        sets_to_win: int,
        is_ranked: bool,
        started_at: datetime,
    ) -> MatchView: ...

    async def get_current_for_session(
        self, session_id: UUID, *, for_update: bool = False
    ) -> MatchView | None: ...

    async def next_session_sequence(self, session_id: UUID) -> int: ...

    async def get_view(self, match_id: UUID, *, for_update: bool = False) -> MatchView | None: ...

    async def list_for_user(
        self,
        user_id: UUID,
        *,
        status: MatchStatus | None,
        context: MatchContext | None,
        is_ranked: bool | None,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[MatchView], int]: ...

    async def list_for_session(
        self, session_id: UUID, *, limit: int, offset: int
    ) -> tuple[Sequence[MatchView], int]: ...

    async def summarize_session_matches(self, session_id: UUID) -> SessionMatchSummary: ...

    async def list_completed_for_session(self, session_id: UUID) -> Sequence[MatchView]: ...

    async def update_match(
        self,
        match: Match,
        *,
        status: MatchStatus | None = None,
        winner_id: UUID | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        cancelled_at: datetime | None = None,
    ) -> Match: ...

    async def add_set(self, match_id: UUID, set_number: int, score: SetScore): ...

    async def replace_sets(self, match_id: UUID, scores: Sequence[SetScore]) -> None: ...

    async def void_completed_match(self, match: Match) -> Match: ...

    async def create_revision(
        self,
        *,
        match_id: UUID,
        changed_by: UUID,
        previous_result: ResultSnapshot,
        new_result: ResultSnapshot,
        reason: str | None,
        operation: MatchRevisionOperation,
    ) -> MatchResultRevision: ...

    async def list_revisions(
        self, match_id: UUID, *, limit: int, offset: int
    ) -> tuple[Sequence[MatchResultRevision], int]: ...


class MatchesUnitOfWork(Protocol):
    matches: MatchRepository
    rating_periods: RatingPeriodRepository
    rating_inputs: RatingInputRepository
    rating_recalculations: RatingRecalculationRepository
    rating_rebuild_jobs: RatingRebuildJobRepository
    rating_rebuild_periods: RatingRebuildPeriodRepository

    async def __aenter__(self) -> "MatchesUnitOfWork": ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...

    def add_after_commit(self, key: Hashable, callback: Callable[[], Awaitable[None]]) -> None: ...

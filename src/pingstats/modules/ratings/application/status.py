"""Read-only rating update status projection."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from uuid import UUID

from pingstats.modules.ratings.application.ports import RatingQueryRepository
from pingstats.modules.ratings.domain import RatingRebuildStatus


class RatingUpdateState(StrEnum):
    UP_TO_DATE = "up_to_date"
    PENDING = "pending"
    CALCULATING = "calculating"
    REBUILDING = "rebuilding"
    DELAYED = "delayed"


@dataclass(frozen=True, slots=True)
class RatingStatusFacts:
    pending_matches_count: int
    has_calculating: bool
    rebuild_status: RatingRebuildStatus | None
    has_delayed_period: bool


@dataclass(frozen=True, slots=True)
class RatingStatusView:
    state: RatingUpdateState
    pending_matches_count: int
    next_calculation_at: datetime


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RatingStatusService:
    """Resolve the public state without exposing period or rebuild identifiers."""

    def __init__(self, *, clock: Callable[[], datetime] = _utc_now) -> None:
        self._clock = clock

    async def get_status(
        self,
        repository: RatingQueryRepository,
        *,
        user_id: UUID,
    ) -> RatingStatusView:
        at = self._clock()
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("Rating status time must be timezone-aware")
        now = at.astimezone(UTC)
        facts = await repository.get_status(user_id, at=now)
        state = self._state(facts)
        return RatingStatusView(
            state=state,
            pending_matches_count=facts.pending_matches_count,
            next_calculation_at=self._next_calculation_at(now),
        )

    @staticmethod
    def _state(facts: RatingStatusFacts) -> RatingUpdateState:
        if facts.has_delayed_period or facts.rebuild_status is RatingRebuildStatus.FAILED:
            return RatingUpdateState.DELAYED
        if facts.rebuild_status is not None:
            return RatingUpdateState.REBUILDING
        if facts.has_calculating:
            return RatingUpdateState.CALCULATING
        if facts.pending_matches_count:
            return RatingUpdateState.PENDING
        return RatingUpdateState.UP_TO_DATE

    @staticmethod
    def _next_calculation_at(now: datetime) -> datetime:
        scheduled = datetime.combine(now.date(), time(hour=0, minute=5), tzinfo=UTC)
        return scheduled if now < scheduled else scheduled + timedelta(days=1)

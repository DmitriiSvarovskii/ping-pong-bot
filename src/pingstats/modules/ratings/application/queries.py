"""Read-only queries over the active published rating branch."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import cast
from uuid import UUID

from pingstats.modules.ratings.application.calculator import RatingPeriodCalculator
from pingstats.modules.ratings.application.errors import RatingNotFoundError
from pingstats.modules.ratings.application.ports import RatingQueryRepository
from pingstats.modules.ratings.domain import (
    Glicko2Strategy,
    PublishedRating,
    RatingPeriod,
    RatingState,
)

_RATING_QUANTUM = Decimal("0.00001")


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class CurrentRatingView:
    user_id: UUID
    rating: Decimal
    rd: Decimal
    effective_rd: Decimal
    volatility: Decimal
    rated_matches_count: int
    as_of_period_id: UUID
    as_of_period_ends_at: datetime
    inactivity_periods: int
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RatingHistoryView:
    period_id: UUID
    period_starts_at: datetime
    period_ends_at: datetime
    published_at: datetime
    rating_before: Decimal
    rating_after: Decimal
    rd_before: Decimal
    rd_after: Decimal
    volatility_before: Decimal
    volatility_after: Decimal
    games_count: int
    is_active: bool


class RatingQueryService:
    """Expose current and historical values without mutating stored snapshots."""

    def __init__(self, *, clock: Callable[[], datetime] = _utc_now) -> None:
        self._clock = clock

    async def get_current(
        self,
        repository: RatingQueryRepository,
        user_id: UUID,
    ) -> CurrentRatingView:
        stored = await repository.get_current(user_id)
        if stored is None:
            raise RatingNotFoundError
        rating, period = stored
        return self.project_current(rating, period, at=self.read_time())

    @staticmethod
    def project_current(
        rating: PublishedRating,
        period: RatingPeriod,
        *,
        at: datetime,
    ) -> CurrentRatingView:
        if rating.as_of_period_id is None or rating.as_of_period_id != period.id:
            raise RatingNotFoundError
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("Rating projection time must be timezone-aware")
        now = at.astimezone(UTC)
        period_end = period.ends_at.astimezone(UTC)
        inactivity_periods = max((now.date() - period_end.date()).days, 0)
        parameters = RatingPeriodCalculator.parameters_for_period(period)
        effective = Glicko2Strategy(parameters).advance_inactivity(
            RatingState(
                rating=float(rating.rating),
                rd=float(rating.rd),
                volatility=float(rating.volatility),
            ),
            periods=inactivity_periods,
        )
        return CurrentRatingView(
            user_id=rating.user_id,
            rating=rating.rating,
            rd=rating.rd,
            effective_rd=Decimal(str(effective.rd)).quantize(
                _RATING_QUANTUM, rounding=ROUND_HALF_EVEN
            ),
            volatility=rating.volatility,
            rated_matches_count=rating.rated_matches_count,
            as_of_period_id=period.id,
            as_of_period_ends_at=period.ends_at,
            inactivity_periods=inactivity_periods,
            updated_at=rating.updated_at,
        )

    @staticmethod
    async def list_history(
        repository: RatingQueryRepository,
        user_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[tuple[RatingHistoryView, ...], int]:
        rows, total = await repository.list_history(
            user_id,
            limit=limit,
            offset=offset,
        )
        return (
            tuple(
                RatingHistoryView(
                    period_id=period.id,
                    period_starts_at=period.starts_at,
                    period_ends_at=period.ends_at,
                    published_at=cast(datetime, period.published_at),
                    rating_before=result.rating_before,
                    rating_after=result.rating_after,
                    rd_before=result.rd_before,
                    rd_after=result.rd_after,
                    volatility_before=result.volatility_before,
                    volatility_after=result.volatility_after,
                    games_count=result.games_count,
                    is_active=result.is_active,
                )
                for result, period in rows
            ),
            total,
        )

    def read_time(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Rating query clock must be timezone-aware")
        return now.astimezone(UTC)

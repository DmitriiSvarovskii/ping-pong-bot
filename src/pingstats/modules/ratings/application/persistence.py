"""Atomic persistence of a deterministic rating-period calculation snapshot."""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pingstats.modules.ratings.application.barrier import require_period_not_blocked
from pingstats.modules.ratings.application.calculator import RatingPeriodCalculator
from pingstats.modules.ratings.application.dto import (
    CalculatedPlayerRating,
    RatingPeriodCalculation,
)
from pingstats.modules.ratings.application.errors import (
    RatingCalculationSnapshotConflictError,
    RatingCalculationVersionConflictError,
    RatingPeriodNotCalculatingError,
    RatingPeriodNotFoundError,
)
from pingstats.modules.ratings.application.ports import RatingsUnitOfWork
from pingstats.modules.ratings.domain import RatingPeriodResult, RatingPeriodStatus


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RatingPeriodResultPersister:
    """Calculate and atomically store a complete result set for one period version."""

    def __init__(
        self,
        *,
        calculator: RatingPeriodCalculator | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._calculator = calculator or RatingPeriodCalculator()
        self._clock = clock

    async def persist(
        self,
        uow: RatingsUnitOfWork,
        *,
        period_id: UUID,
        expected_calculation_version: int,
    ) -> RatingPeriodCalculation:
        async with uow:
            candidate = await uow.periods.get(period_id)
            if candidate is None:
                raise RatingPeriodNotFoundError
            await uow.periods.acquire_publication_lock()
            await uow.periods.acquire_period_lock(candidate.starts_at)
            period = await uow.periods.get(period_id, for_update=True)
            if period is None:
                raise RatingPeriodNotFoundError
            await require_period_not_blocked(uow, period)
            if period.status is not RatingPeriodStatus.CALCULATING:
                raise RatingPeriodNotCalculatingError
            if period.calculation_version != expected_calculation_version:
                raise RatingCalculationVersionConflictError

            calculation = await self._calculator.calculate_in_uow(
                uow,
                period_id=period.id,
                locked_period=period,
            )
            existing = tuple(
                await uow.results.list_for_period(period.id, period.calculation_version)
            )
            if period.calculated_version == period.calculation_version:
                self._require_same_snapshot(existing, calculation.players)
                return calculation
            if existing:
                raise RatingCalculationSnapshotConflictError

            created_at = self._clock()
            rows = tuple(
                self._to_record(period.id, period.calculation_version, item, created_at)
                for item in calculation.players
            )
            if rows:
                await uow.results.add_all(rows)
            marked = await uow.periods.mark_calculated(
                period_id=period.id,
                calculation_version=period.calculation_version,
                expected_version=period.version,
            )
            if marked is None:
                raise RatingCalculationVersionConflictError
            await uow.commit()
            return calculation

    @classmethod
    def _require_same_snapshot(
        cls,
        stored: tuple[RatingPeriodResult, ...],
        calculated: tuple[CalculatedPlayerRating, ...],
    ) -> None:
        stored_snapshot = tuple(
            sorted((cls._stored_values(item) for item in stored), key=lambda item: item[0].int)
        )
        calculated_snapshot = tuple(
            sorted(
                (cls._calculated_values(item) for item in calculated), key=lambda item: item[0].int
            )
        )
        if stored_snapshot != calculated_snapshot:
            raise RatingCalculationSnapshotConflictError

    @staticmethod
    def _stored_values(item: RatingPeriodResult) -> tuple[object, ...]:
        return (
            item.user_id,
            item.rating_before,
            item.rating_after,
            item.rd_before,
            item.rd_after,
            item.volatility_before,
            item.volatility_after,
            item.games_count,
            item.is_active,
        )

    @staticmethod
    def _calculated_values(item: CalculatedPlayerRating) -> tuple[object, ...]:
        return (
            item.user_id,
            item.rating_before,
            item.rating_after,
            item.rd_before,
            item.rd_after,
            item.volatility_before,
            item.volatility_after,
            item.games_count,
            item.is_active,
        )

    @staticmethod
    def _to_record(
        period_id: UUID,
        calculation_version: int,
        item: CalculatedPlayerRating,
        created_at: datetime,
    ) -> RatingPeriodResult:
        return RatingPeriodResult(
            id=uuid4(),
            period_id=period_id,
            calculation_version=calculation_version,
            user_id=item.user_id,
            rating_before=item.rating_before,
            rating_after=item.rating_after,
            rd_before=item.rd_before,
            rd_after=item.rd_after,
            volatility_before=item.volatility_before,
            volatility_after=item.volatility_after,
            games_count=item.games_count,
            is_active=item.is_active,
            created_at=created_at,
        )

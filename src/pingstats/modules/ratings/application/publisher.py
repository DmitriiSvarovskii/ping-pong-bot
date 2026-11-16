"""Atomic publication of a persisted rating-period result snapshot."""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from uuid import UUID

from pingstats.modules.ratings.application.barrier import require_period_not_blocked
from pingstats.modules.ratings.application.errors import (
    RatingCalculationVersionConflictError,
    RatingPeriodNotFoundError,
    RatingPeriodNotPublishableError,
    RatingPublicationChronologyError,
    RatingPublicationSnapshotConflictError,
)
from pingstats.modules.ratings.application.ports import RatingsUnitOfWork
from pingstats.modules.ratings.application.snapshots import validate_rating_snapshot
from pingstats.modules.ratings.domain import (
    PublishedRating,
    RatingPeriod,
    RatingPeriodResult,
    RatingPeriodStatus,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RatingPeriodPublisher:
    """Publish one immutable result snapshot into current player ratings."""

    def __init__(self, *, clock: Callable[[], datetime] = _utc_now) -> None:
        self._clock = clock

    async def publish(
        self,
        uow: RatingsUnitOfWork,
        *,
        period_id: UUID,
        expected_calculation_version: int,
    ) -> RatingPeriod:
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
            if period.status is RatingPeriodStatus.PUBLISHED:
                if period.published_version is None:
                    raise RatingPublicationSnapshotConflictError
                if period.published_version == expected_calculation_version:
                    return period
                raise RatingPeriodNotPublishableError
            if period.calculation_version != expected_calculation_version:
                raise RatingCalculationVersionConflictError
            if period.status is not RatingPeriodStatus.CALCULATING:
                raise RatingPeriodNotPublishableError
            if period.calculated_version != period.calculation_version:
                raise RatingPeriodNotPublishableError

            previous = await uow.periods.get_previous(period.starts_at)
            if previous is not None and previous.status is not RatingPeriodStatus.PUBLISHED:
                raise RatingPublicationChronologyError
            if await uow.periods.get_next_published(period.starts_at) is not None:
                raise RatingPublicationChronologyError

            results = tuple(
                await uow.results.list_for_period(period.id, period.calculation_version)
            )
            inputs = tuple(await uow.inputs.list_for_period(period.id, period.calculation_version))
            previous_results = (
                tuple(
                    await uow.results.list_for_period(
                        previous.id,
                        self._published_version(previous),
                    )
                )
                if previous is not None
                else ()
            )
            validate_rating_snapshot(results, inputs, previous_results)
            published_at = self._clock()
            if published_at.tzinfo is None or published_at.utcoffset() is None:
                raise ValueError("Publication clock must return a timezone-aware datetime")
            published_at = published_at.astimezone(UTC)
            await self._publish_ratings(
                uow,
                period,
                previous,
                results,
                {item.user_id for item in previous_results},
                published_at,
            )
            updated = await uow.periods.mark_published(
                period_id=period.id,
                calculation_version=period.calculation_version,
                published_at=published_at,
                expected_version=period.version,
            )
            if updated is None:
                raise RatingCalculationVersionConflictError
            await uow.commit()
            return updated

    @staticmethod
    def _published_version(period: RatingPeriod) -> int:
        if period.status is not RatingPeriodStatus.PUBLISHED or period.published_version is None:
            raise RatingPublicationSnapshotConflictError
        return period.published_version

    async def _publish_ratings(
        self,
        uow: RatingsUnitOfWork,
        period: RatingPeriod,
        previous: RatingPeriod | None,
        results: Sequence[RatingPeriodResult],
        previous_users: set[UUID],
        published_at: datetime,
    ) -> None:
        for result in sorted(results, key=lambda item: item.user_id.int):
            current = await uow.ratings.get(result.user_id, for_update=True)
            if current is None:
                if result.user_id in previous_users:
                    raise RatingPublicationSnapshotConflictError
                await uow.ratings.add(
                    PublishedRating(
                        user_id=result.user_id,
                        rating=result.rating_after,
                        rd=result.rd_after,
                        volatility=result.volatility_after,
                        as_of_period_id=period.id,
                        rated_matches_count=result.games_count,
                        updated_at=published_at,
                        version=1,
                    )
                )
                continue
            if (
                previous is None
                or current.as_of_period_id != previous.id
                or current.rating != result.rating_before
                or current.rd != result.rd_before
                or current.volatility != result.volatility_before
            ):
                raise RatingPublicationSnapshotConflictError
            updated = await uow.ratings.update_published_state(
                user_id=result.user_id,
                rating=result.rating_after,
                rd=result.rd_after,
                volatility=result.volatility_after,
                as_of_period_id=period.id,
                rated_matches_count=current.rated_matches_count + result.games_count,
                expected_version=current.version,
            )
            if updated is None:
                raise RatingPublicationSnapshotConflictError

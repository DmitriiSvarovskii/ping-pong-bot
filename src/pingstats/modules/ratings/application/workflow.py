"""Restart-safe orchestration of the rating-period lifecycle."""

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from uuid import UUID

from pingstats.modules.ratings.application.closer import (
    RatingPeriodCloser,
    RatingsUnitOfWorkFactory,
)
from pingstats.modules.ratings.application.errors import (
    RatingCalculationVersionConflictError,
    RatingPeriodNotCalculatingError,
    RatingRebuildBarrierError,
)
from pingstats.modules.ratings.application.persistence import RatingPeriodResultPersister
from pingstats.modules.ratings.application.publisher import RatingPeriodPublisher
from pingstats.modules.ratings.domain import RatingPeriod, RatingPeriodStatus

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RatingWorkflowOrchestrator:
    """Advance due periods one atomic, restart-safe stage at a time."""

    def __init__(
        self,
        *,
        closer: RatingPeriodCloser | None = None,
        persister: RatingPeriodResultPersister | None = None,
        publisher: RatingPeriodPublisher | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._closer = closer or RatingPeriodCloser(clock=clock)
        self._persister = persister or RatingPeriodResultPersister(clock=clock)
        self._publisher = publisher or RatingPeriodPublisher(clock=clock)
        self._clock = clock

    async def run(self, uow_factory: RatingsUnitOfWorkFactory) -> tuple[RatingPeriod, ...]:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Rating workflow clock must return a timezone-aware datetime")
        now = now.astimezone(UTC)
        logger.info("Rating workflow started", extra={"stage": "workflow", "result": "started"})
        closed = await self._closer.close_due(uow_factory, now=now)
        for period in closed:
            self._log(period, "close", "completed")

        async with uow_factory() as uow:
            candidates = tuple(await uow.periods.list_calculating())

        published: list[RatingPeriod] = []
        try:
            for candidate in candidates:
                result = await self._advance_period(uow_factory, candidate.id)
                if result.status is RatingPeriodStatus.PUBLISHED:
                    published.append(result)
        except Exception:
            logger.exception(
                "Rating workflow failed",
                extra={"stage": "workflow", "result": "failed"},
            )
            raise
        logger.info(
            "Rating workflow completed",
            extra={
                "stage": "workflow",
                "result": "completed",
                "published_count": len(published),
            },
        )
        return tuple(published)

    async def run_periods(
        self,
        uow_factory: RatingsUnitOfWorkFactory,
        *,
        starts_at: Sequence[datetime],
    ) -> tuple[RatingPeriod, ...]:
        """Advance only explicitly selected periods, as required by historical backfill."""
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Rating workflow clock must return a timezone-aware datetime")
        now = now.astimezone(UTC)
        published: list[RatingPeriod] = []
        for period_start in sorted(set(starts_at)):
            await self._closer.close_period(
                uow_factory(),
                starts_at=period_start,
                now=now,
            )
            async with uow_factory() as uow:
                candidate = await uow.periods.get_by_start(period_start)
            if candidate is None:
                continue
            was_published = candidate.status is RatingPeriodStatus.PUBLISHED
            result = await self._advance_period(uow_factory, candidate.id)
            if not was_published and result.status is RatingPeriodStatus.PUBLISHED:
                published.append(result)
        return tuple(published)

    async def _advance_period(
        self,
        uow_factory: RatingsUnitOfWorkFactory,
        period_id: UUID,
    ) -> RatingPeriod:
        period = await self._get_period(uow_factory, period_id)
        if period.status is RatingPeriodStatus.PUBLISHED:
            return period
        if period.status is not RatingPeriodStatus.CALCULATING:
            return period
        if period.calculated_version != period.calculation_version:
            self._log(period, "persist", "started")
            try:
                await self._persister.persist(
                    uow_factory(),
                    period_id=period.id,
                    expected_calculation_version=period.calculation_version,
                )
            except RatingRebuildBarrierError:
                self._log(period, "persist", "blocked_by_rebuild")
                return period
            except (RatingPeriodNotCalculatingError, RatingCalculationVersionConflictError):
                concurrent = await self._get_period(uow_factory, period.id)
                if not (
                    concurrent.status is RatingPeriodStatus.PUBLISHED
                    or (
                        concurrent.status is RatingPeriodStatus.CALCULATING
                        and concurrent.calculated_version == concurrent.calculation_version
                    )
                ):
                    raise
            period = await self._get_period(uow_factory, period.id)
            self._log(period, "persist", "completed")
        if period.status is RatingPeriodStatus.PUBLISHED:
            return period
        if (
            period.status is RatingPeriodStatus.CALCULATING
            and period.calculated_version == period.calculation_version
        ):
            self._log(period, "publish", "started")
            try:
                period = await self._publisher.publish(
                    uow_factory(),
                    period_id=period.id,
                    expected_calculation_version=period.calculation_version,
                )
            except RatingRebuildBarrierError:
                self._log(period, "publish", "blocked_by_rebuild")
                return period
            self._log(period, "publish", "completed")
        return period

    @staticmethod
    async def _get_period(
        uow_factory: RatingsUnitOfWorkFactory,
        period_id: UUID,
    ) -> RatingPeriod:
        async with uow_factory() as uow:
            period = await uow.periods.get(period_id)
        if period is None:
            raise RuntimeError("Rating period disappeared during workflow")
        return period

    @staticmethod
    def _log(period: RatingPeriod, stage: str, result: str) -> None:
        logger.info(
            "Rating period workflow stage",
            extra={
                "period_id": str(period.id),
                "calculation_version": period.calculation_version,
                "stage": stage,
                "result": result,
            },
        )

"""Seal due UTC rating periods before their future calculation and publication."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pingstats.modules.ratings.application.ports import RatingsUnitOfWork
from pingstats.modules.ratings.domain import RatingPeriod, RatingPeriodStatus

RATING_PERIOD_CLOSE_DELAY = timedelta(minutes=5)

RatingsUnitOfWorkFactory = Callable[[], RatingsUnitOfWork]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RatingPeriodCloser:
    """Move due periods from open to calculating under the shared period lock."""

    def __init__(self, *, clock: Callable[[], datetime] = _utc_now) -> None:
        self._clock = clock

    async def close_period(
        self,
        uow: RatingsUnitOfWork,
        *,
        starts_at: datetime,
        now: datetime | None = None,
    ) -> RatingPeriod | None:
        current_time = self._utc_time(now or self._clock())
        period_start = self._utc_time(starts_at)
        period, _ = await self._close_period(uow, period_start, current_time)
        return period

    async def _close_period(
        self,
        uow: RatingsUnitOfWork,
        starts_at: datetime,
        current_time: datetime,
    ) -> tuple[RatingPeriod | None, bool]:
        async with uow:
            await uow.periods.acquire_period_lock(starts_at)
            period = await uow.periods.get_by_start(starts_at, for_update=True)
            if period is None:
                return None, False
            if period.status is not RatingPeriodStatus.OPEN:
                return period, False
            if current_time < period.ends_at.astimezone(UTC) + RATING_PERIOD_CLOSE_DELAY:
                return period, False
            updated = await uow.periods.update_state(
                period_id=period.id,
                status=RatingPeriodStatus.CALCULATING,
                calculation_version=period.calculation_version,
                published_at=None,
                expected_version=period.version,
            )
            if updated is None:
                raise RuntimeError("Locked rating period changed unexpectedly")
            await uow.commit()
            return updated, True

    async def close_due(
        self,
        uow_factory: RatingsUnitOfWorkFactory,
        *,
        now: datetime | None = None,
    ) -> tuple[RatingPeriod, ...]:
        current_time = self._utc_time(now or self._clock())
        cutoff = current_time - RATING_PERIOD_CLOSE_DELAY
        async with uow_factory() as uow:
            due = await uow.periods.list_open_ending_at_or_before(cutoff)

        closed: list[RatingPeriod] = []
        for candidate in due:
            period, changed = await self._close_period(
                uow_factory(),
                candidate.starts_at,
                current_time,
            )
            if changed and period is not None:
                closed.append(period)
        return tuple(closed)

    @staticmethod
    def _utc_time(value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Rating period time must be timezone-aware")
        return value.astimezone(UTC)

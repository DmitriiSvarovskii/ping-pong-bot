"""Application tests for sealing due rating periods."""

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from pingstats.modules.ratings.application import (
    RatingPeriodCloser,
    RatingPeriodNotPublishableError,
)
from pingstats.modules.ratings.domain import RatingPeriod, RatingPeriodStatus
from pingstats.modules.ratings.infrastructure.scheduler import (
    RatingPeriodScheduler,
    RatingSchedulerWakeup,
)
from tests.unit.rating_fakes import FakeRatingPeriodRepository


def make_period(starts_at: datetime) -> RatingPeriod:
    return RatingPeriod(
        id=uuid4(),
        starts_at=starts_at,
        ends_at=starts_at + timedelta(days=1),
        status=RatingPeriodStatus.OPEN,
        algorithm="glicko2",
        algorithm_version="1",
        parameters={"tau": 0.5},
        calculation_version=1,
        calculated_version=None,
        published_version=None,
        published_at=None,
        created_at=starts_at,
        updated_at=starts_at,
        version=1,
    )


class FakeRatingsUnitOfWork:
    def __init__(self, periods: FakeRatingPeriodRepository) -> None:
        self.periods = periods
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        pass


class FakeUowFactory:
    def __init__(self, periods: FakeRatingPeriodRepository) -> None:
        self.periods = periods
        self.instances: list[FakeRatingsUnitOfWork] = []

    def __call__(self) -> FakeRatingsUnitOfWork:
        uow = FakeRatingsUnitOfWork(self.periods)
        self.instances.append(uow)
        return uow


async def test_closes_at_exact_utc_deadline_and_repeat_is_idempotent() -> None:
    periods = FakeRatingPeriodRepository()
    starts_at = datetime(2026, 11, 9, tzinfo=UTC)
    original = await periods.add(make_period(starts_at))
    closer = RatingPeriodCloser()

    first_uow = FakeRatingsUnitOfWork(periods)
    closed = await closer.close_period(
        first_uow,
        starts_at=starts_at,
        now=datetime(2026, 11, 10, 0, 5, tzinfo=UTC),
    )
    repeated_uow = FakeRatingsUnitOfWork(periods)
    repeated = await closer.close_period(
        repeated_uow,
        starts_at=starts_at,
        now=datetime(2026, 11, 10, 1, tzinfo=UTC),
    )

    assert closed is not None and closed.status is RatingPeriodStatus.CALCULATING
    assert closed.version == original.version + 1
    assert repeated == closed
    assert first_uow.commits == 1
    assert repeated_uow.commits == 0
    assert periods.locked_starts == [starts_at, starts_at]


async def test_does_not_close_before_deadline_and_normalizes_timezone() -> None:
    periods = FakeRatingPeriodRepository()
    starts_at = datetime(2026, 11, 9, tzinfo=UTC)
    original = await periods.add(make_period(starts_at))
    uow = FakeRatingsUnitOfWork(periods)

    result = await RatingPeriodCloser().close_period(
        uow,
        starts_at=starts_at.astimezone(timezone(timedelta(hours=3))),
        now=datetime(2026, 11, 10, 3, 4, 59, tzinfo=timezone(timedelta(hours=3))),
    )

    assert result == original
    assert periods.by_start[starts_at] == original
    assert uow.commits == 0


async def test_close_due_catches_up_oldest_periods_without_closing_current_day() -> None:
    periods = FakeRatingPeriodRepository()
    starts = [datetime(2026, 11, day, tzinfo=UTC) for day in (7, 8, 9)]
    for starts_at in reversed(starts):
        await periods.add(make_period(starts_at))
    factory = FakeUowFactory(periods)

    closed = await RatingPeriodCloser().close_due(
        factory,
        now=datetime(2026, 11, 10, 0, 5, tzinfo=UTC),
    )

    assert [period.starts_at for period in closed] == starts
    assert all(period.status is RatingPeriodStatus.CALCULATING for period in closed)
    assert sum(uow.commits for uow in factory.instances) == 3


async def test_rejects_naive_times() -> None:
    closer = RatingPeriodCloser()
    periods = FakeRatingPeriodRepository()

    with pytest.raises(ValueError, match="timezone-aware"):
        await closer.close_period(
            FakeRatingsUnitOfWork(periods),
            starts_at=datetime(2026, 11, 9),
            now=datetime(2026, 11, 10, tzinfo=UTC),
        )


def test_scheduler_calculates_next_utc_boundary() -> None:
    assert (
        RatingPeriodScheduler.seconds_until_next_run(datetime(2026, 11, 10, 0, 4, tzinfo=UTC)) == 60
    )
    assert (
        RatingPeriodScheduler.seconds_until_next_run(
            datetime(2026, 11, 10, 3, 5, tzinfo=timezone(timedelta(hours=3)))
        )
        == 24 * 60 * 60
    )


async def test_scheduler_runs_catch_up_immediately_and_cleans_up_on_cancel() -> None:
    calls: list[datetime] = []
    sleeps: list[float] = []
    now = datetime(2026, 11, 10, 0, 5, tzinfo=UTC)

    class Workflow:
        async def run(self, uow_factory):
            del uow_factory
            calls.append(now)
            return ()

    class Rebuild:
        async def run_once(self, uow_factory):
            del uow_factory
            return None

    async def stop_after_sleep(delay: float) -> None:
        sleeps.append(delay)
        raise asyncio.CancelledError

    scheduler = RatingPeriodScheduler(
        lambda: FakeRatingsUnitOfWork(FakeRatingPeriodRepository()),
        workflow=Workflow(),
        rebuild=Rebuild(),
        clock=lambda: now,
        sleep=stop_after_sleep,
    )

    with pytest.raises(asyncio.CancelledError):
        await scheduler.run_forever()

    assert calls == [now]
    assert sleeps == [30]


async def test_scheduler_retries_failed_cycle_without_terminating() -> None:
    attempts = 0
    sleeps: list[float] = []
    now = datetime(2026, 11, 10, 12, tzinfo=UTC)

    class Workflow:
        async def run(self, uow_factory):
            nonlocal attempts
            del uow_factory
            attempts += 1
            if attempts == 1:
                raise RuntimeError("database unavailable")
            return ()

    class Rebuild:
        async def run_once(self, uow_factory):
            del uow_factory
            return None

    async def retry_once(delay: float) -> None:
        sleeps.append(delay)
        if len(sleeps) == 2:
            raise asyncio.CancelledError

    scheduler = RatingPeriodScheduler(
        lambda: FakeRatingsUnitOfWork(FakeRatingPeriodRepository()),
        workflow=Workflow(),
        rebuild=Rebuild(),
        clock=lambda: now,
        sleep=retry_once,
    )

    with pytest.raises(asyncio.CancelledError):
        await scheduler.run_forever()

    assert attempts == 2
    assert sleeps == [5, 30]


async def test_scheduler_does_not_fast_retry_business_conflict() -> None:
    sleeps: list[float] = []
    now = datetime(2026, 11, 10, 12, tzinfo=UTC)

    class Workflow:
        async def run(self, uow_factory):
            del uow_factory
            raise RatingPeriodNotPublishableError

    class Rebuild:
        async def run_once(self, uow_factory):
            del uow_factory
            return None

    async def stop(delay: float) -> None:
        sleeps.append(delay)
        raise asyncio.CancelledError

    scheduler = RatingPeriodScheduler(
        lambda: FakeRatingsUnitOfWork(FakeRatingPeriodRepository()),
        workflow=Workflow(),
        rebuild=Rebuild(),
        clock=lambda: now,
        sleep=stop,
    )
    with pytest.raises(asyncio.CancelledError):
        await scheduler.run_forever()

    assert sleeps == [30]


async def test_scheduler_limits_fast_infrastructure_retries() -> None:
    sleeps: list[float] = []
    now = datetime(2026, 11, 10, 12, tzinfo=UTC)

    class Rebuild:
        async def run_once(self, uow_factory):
            del uow_factory
            raise ConnectionError("database unavailable")

    class Workflow:
        async def run(self, uow_factory):
            del uow_factory
            return ()

    async def stop_after_degraded_retry(delay: float) -> None:
        sleeps.append(delay)
        if len(sleeps) == 4:
            raise asyncio.CancelledError

    scheduler = RatingPeriodScheduler(
        lambda: FakeRatingsUnitOfWork(FakeRatingPeriodRepository()),
        rebuild=Rebuild(),
        workflow=Workflow(),
        clock=lambda: now,
        sleep=stop_after_degraded_retry,
    )

    with pytest.raises(asyncio.CancelledError):
        await scheduler.run_forever()

    assert sleeps == [5, 10, 20, 300]


async def test_scheduler_wakeup_interrupts_the_discovery_wait() -> None:
    wakeup = RatingSchedulerWakeup()
    await wakeup.notify()
    await asyncio.wait_for(wakeup.wait(60), timeout=0.1)

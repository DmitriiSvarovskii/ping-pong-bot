"""Application tests for restart-safe rating workflow orchestration."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from pingstats.modules.ratings.application import (
    RatingRebuildBarrierError,
    RatingWorkflowOrchestrator,
)
from pingstats.modules.ratings.domain import RatingPeriodStatus
from tests.unit.rating_fakes import (
    FakeRatingInputRepository,
    FakeRatingPeriodRepository,
    FakeRatingRepository,
    FakeRatingResultRepository,
)
from tests.unit.test_rating_period_calculator import make_input, make_period, make_result

NOW = datetime(2026, 11, 4, 0, 5, tzinfo=UTC)


class FakeRatingsUnitOfWork:
    def __init__(self) -> None:
        self.ratings = FakeRatingRepository()
        self.periods = FakeRatingPeriodRepository()
        self.inputs = FakeRatingInputRepository()
        self.results = FakeRatingResultRepository()
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        pass


async def test_full_lifecycle_and_repeated_run_are_idempotent() -> None:
    uow = FakeRatingsUnitOfWork()
    period = replace(make_period(3, status=RatingPeriodStatus.OPEN), version=1)
    first, second = uuid4(), uuid4()
    await uow.periods.add(period)
    await uow.inputs.add_if_absent(make_input(period, first, second))
    workflow = RatingWorkflowOrchestrator(clock=lambda: NOW)

    published = await workflow.run(lambda: uow)
    repeated = await workflow.run(lambda: uow)

    stored = uow.periods.by_id[period.id]
    assert len(published) == 1
    assert repeated == ()
    assert stored.status is RatingPeriodStatus.PUBLISHED
    assert stored.calculated_version == stored.calculation_version
    assert len(uow.results.items) == len(uow.ratings.by_user) == 2
    assert {item.rated_matches_count for item in uow.ratings.by_user.values()} == {1}


async def test_processes_multiple_missed_periods_in_order() -> None:
    uow = FakeRatingsUnitOfWork()
    first_user, second_user = uuid4(), uuid4()
    periods = tuple(
        replace(make_period(day, status=RatingPeriodStatus.OPEN), version=1) for day in (1, 2, 3)
    )
    for period in periods:
        await uow.periods.add(period)
        await uow.inputs.add_if_absent(make_input(period, first_user, second_user))

    published = await RatingWorkflowOrchestrator(clock=lambda: NOW).run(lambda: uow)

    assert tuple(item.id for item in published) == tuple(item.id for item in periods)
    assert all(
        uow.periods.by_id[item.id].status is RatingPeriodStatus.PUBLISHED for item in periods
    )
    assert {item.rated_matches_count for item in uow.ratings.by_user.values()} == {3}


async def test_no_periods_and_empty_period() -> None:
    empty_uow = FakeRatingsUnitOfWork()
    assert await RatingWorkflowOrchestrator(clock=lambda: NOW).run(lambda: empty_uow) == ()

    uow = FakeRatingsUnitOfWork()
    period = replace(make_period(3, status=RatingPeriodStatus.OPEN), version=1)
    await uow.periods.add(period)
    published = await RatingWorkflowOrchestrator(clock=lambda: NOW).run(lambda: uow)
    assert len(published) == 1
    assert uow.results.items == []
    assert uow.ratings.by_user == {}


async def test_resumes_after_close_and_after_persistence() -> None:
    first, second = uuid4(), uuid4()
    closed_uow = FakeRatingsUnitOfWork()
    closed = make_period(3)
    await closed_uow.periods.add(closed)
    await closed_uow.inputs.add_if_absent(make_input(closed, first, second))
    await RatingWorkflowOrchestrator(clock=lambda: NOW).run(lambda: closed_uow)
    assert closed_uow.periods.by_id[closed.id].status is RatingPeriodStatus.PUBLISHED

    persisted_uow = FakeRatingsUnitOfWork()
    persisted = make_period(3, calculated=True)
    await persisted_uow.periods.add(persisted)
    item = make_input(persisted, first, second)
    await persisted_uow.inputs.add_if_absent(item)
    persisted_uow.results.items.extend(
        (make_result(persisted, first), make_result(persisted, second))
    )
    await RatingWorkflowOrchestrator(clock=lambda: NOW).run(lambda: persisted_uow)
    assert persisted_uow.periods.by_id[persisted.id].status is RatingPeriodStatus.PUBLISHED


async def test_failure_stops_later_periods() -> None:
    class FailingPersister:
        async def persist(self, uow, *, period_id, expected_calculation_version):
            del uow, period_id, expected_calculation_version
            raise RuntimeError("database unavailable")

    uow = FakeRatingsUnitOfWork()
    first = make_period(1)
    second = make_period(2)
    await uow.periods.add(first)
    await uow.periods.add(second)
    workflow = RatingWorkflowOrchestrator(
        persister=FailingPersister(),
        clock=lambda: NOW,
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        await workflow.run(lambda: uow)

    assert uow.periods.by_id[second.id].calculated_version is None
    assert uow.periods.by_id[second.id].status is RatingPeriodStatus.CALCULATING


async def test_rebuild_barrier_stops_run_without_retrying_or_publishing() -> None:
    class BlockedPersister:
        def __init__(self) -> None:
            self.calls = 0

        async def persist(self, uow, *, period_id, expected_calculation_version):
            del uow, period_id, expected_calculation_version
            self.calls += 1
            raise RatingRebuildBarrierError

    class UnexpectedPublisher:
        async def publish(self, uow, *, period_id, expected_calculation_version):
            del uow, period_id, expected_calculation_version
            raise AssertionError("publisher must not run behind the rebuild barrier")

    uow = FakeRatingsUnitOfWork()
    period = make_period(3)
    await uow.periods.add(period)
    persister = BlockedPersister()
    workflow = RatingWorkflowOrchestrator(
        persister=persister,
        publisher=UnexpectedPublisher(),
        clock=lambda: NOW,
    )

    assert await workflow.run(lambda: uow) == ()
    assert persister.calls == 1

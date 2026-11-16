"""Application tests for atomic rating result snapshot persistence."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from pingstats.modules.ratings.application import (
    RatingCalculationSnapshotConflictError,
    RatingCalculationVersionConflictError,
    RatingHistoryUnavailableError,
    RatingPeriodNotCalculatingError,
    RatingPeriodResultPersister,
)
from pingstats.modules.ratings.domain import RatingPeriodStatus
from tests.unit.rating_fakes import (
    FakeRatingInputRepository,
    FakeRatingPeriodRepository,
    FakeRatingResultRepository,
)
from tests.unit.test_rating_period_calculator import make_input, make_period


class FakeRatingsUnitOfWork:
    def __init__(
        self,
        periods: FakeRatingPeriodRepository,
        inputs: FakeRatingInputRepository,
        results: FakeRatingResultRepository,
    ) -> None:
        self.periods = periods
        self.inputs = inputs
        self.results = results
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        pass


async def make_uow(*, empty: bool = False, status=RatingPeriodStatus.CALCULATING):
    periods = FakeRatingPeriodRepository()
    inputs = FakeRatingInputRepository()
    results = FakeRatingResultRepository()
    period = make_period(1, status=status)
    await periods.add(period)
    if not empty:
        await inputs.add_if_absent(make_input(period, uuid4(), uuid4()))
    return period, FakeRatingsUnitOfWork(periods, inputs, results)


def persister() -> RatingPeriodResultPersister:
    return RatingPeriodResultPersister(clock=lambda: datetime(2026, 11, 2, tzinfo=UTC))


async def test_persists_full_snapshot_and_marks_version_atomically() -> None:
    period, uow = await make_uow()

    calculated = await persister().persist(
        uow,
        period_id=period.id,
        expected_calculation_version=1,
    )

    stored_period = uow.periods.by_id[period.id]
    assert stored_period.calculated_version == 1
    assert stored_period.status is RatingPeriodStatus.CALCULATING
    assert stored_period.published_at is None
    assert stored_period.version == period.version + 1
    assert len(uow.results.items) == len(calculated.players) == 2
    assert uow.commits == 1


async def test_empty_period_uses_marker_without_result_rows() -> None:
    period, uow = await make_uow(empty=True)

    calculated = await persister().persist(
        uow,
        period_id=period.id,
        expected_calculation_version=1,
    )

    assert calculated.players == ()
    assert uow.results.items == []
    assert uow.periods.by_id[period.id].calculated_version == 1
    assert uow.commits == 1


async def test_identical_repeat_is_noop() -> None:
    period, uow = await make_uow()
    service = persister()
    first = await service.persist(uow, period_id=period.id, expected_calculation_version=1)
    stored_ids = tuple(item.id for item in uow.results.items)
    repeated = await service.persist(uow, period_id=period.id, expected_calculation_version=1)

    assert repeated == first
    assert tuple(item.id for item in uow.results.items) == stored_ids
    assert uow.commits == 1


async def test_existing_different_snapshot_is_conflict() -> None:
    period, uow = await make_uow()
    service = persister()
    await service.persist(uow, period_id=period.id, expected_calculation_version=1)
    uow.results.items[0] = replace(
        uow.results.items[0],
        rating_after=uow.results.items[0].rating_after + 1,
    )

    with pytest.raises(RatingCalculationSnapshotConflictError):
        await service.persist(uow, period_id=period.id, expected_calculation_version=1)

    assert uow.commits == 1


async def test_partial_rows_without_completion_marker_are_conflict() -> None:
    period, uow = await make_uow()
    calculated = await persister().persist(uow, period_id=period.id, expected_calculation_version=1)
    uow.periods.by_id[period.id] = replace(uow.periods.by_id[period.id], calculated_version=None)
    uow.periods.by_start[period.starts_at] = uow.periods.by_id[period.id]

    with pytest.raises(RatingCalculationSnapshotConflictError):
        await persister().persist(uow, period_id=period.id, expected_calculation_version=1)

    assert len(uow.results.items) == len(calculated.players)


async def test_rejects_wrong_status_and_calculation_version() -> None:
    open_period, open_uow = await make_uow(status=RatingPeriodStatus.OPEN)
    with pytest.raises(RatingPeriodNotCalculatingError):
        await persister().persist(
            open_uow,
            period_id=open_period.id,
            expected_calculation_version=1,
        )

    period, uow = await make_uow()
    with pytest.raises(RatingCalculationVersionConflictError):
        await persister().persist(uow, period_id=period.id, expected_calculation_version=2)

    assert uow.commits == 0


async def test_missing_previous_history_rolls_back_without_marker() -> None:
    periods = FakeRatingPeriodRepository()
    inputs = FakeRatingInputRepository()
    results = FakeRatingResultRepository()
    previous = make_period(1)
    current = make_period(2)
    await periods.add(previous)
    await periods.add(current)
    uow = FakeRatingsUnitOfWork(periods, inputs, results)

    with pytest.raises(RatingHistoryUnavailableError):
        await persister().persist(uow, period_id=current.id, expected_calculation_version=1)

    assert periods.by_id[current.id].calculated_version is None
    assert results.items == []
    assert uow.commits == 0

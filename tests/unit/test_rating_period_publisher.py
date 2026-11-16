"""Application tests for atomic rating snapshot publication."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from pingstats.modules.ratings.application import (
    RatingCalculationVersionConflictError,
    RatingPeriodNotFoundError,
    RatingPeriodNotPublishableError,
    RatingPeriodPublisher,
    RatingPublicationChronologyError,
    RatingPublicationSnapshotConflictError,
)
from pingstats.modules.ratings.domain import PublishedRating, RatingPeriodStatus
from tests.unit.rating_fakes import (
    FakeRatingInputRepository,
    FakeRatingPeriodRepository,
    FakeRatingRepository,
    FakeRatingResultRepository,
)
from tests.unit.test_rating_period_calculator import make_input, make_period, make_result

NOW = datetime(2026, 11, 10, 1, tzinfo=UTC)


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


def publisher() -> RatingPeriodPublisher:
    return RatingPeriodPublisher(clock=lambda: NOW)


async def calculated_period(uow: FakeRatingsUnitOfWork, day: int = 1):
    period = make_period(day, calculated=True)
    await uow.periods.add(period)
    return period


async def test_publishes_snapshot_and_creates_new_ratings() -> None:
    uow = FakeRatingsUnitOfWork()
    period = await calculated_period(uow)
    first, second = uuid4(), uuid4()
    item = make_input(period, first, second)
    await uow.inputs.add_if_absent(item)
    uow.results.items.extend((make_result(period, first), make_result(period, second)))

    published = await publisher().publish(uow, period_id=period.id, expected_calculation_version=1)

    assert published.status is RatingPeriodStatus.PUBLISHED
    assert published.published_version == period.calculation_version
    assert published.published_at == NOW
    assert published.version == period.version + 1
    assert set(uow.ratings.by_user) == {first, second}
    assert all(item.as_of_period_id == period.id for item in uow.ratings.by_user.values())
    assert all(item.rated_matches_count == 1 for item in uow.ratings.by_user.values())
    assert uow.periods.publication_locks == uow.commits == 1


async def test_updates_existing_rating_and_inactive_player() -> None:
    uow = FakeRatingsUnitOfWork()
    user_id = uuid4()
    previous = replace(
        make_period(1, calculated=True),
        status=RatingPeriodStatus.PUBLISHED,
        published_version=1,
        published_at=NOW,
    )
    current = make_period(2, calculated=True)
    await uow.periods.add(previous)
    await uow.periods.add(current)
    uow.results.items.extend(
        (
            make_result(previous, user_id, rating="1510.00000", games_count=2),
            replace(
                make_result(current, user_id, rating="1510.00000", games_count=0),
                rd_after=Decimal("210.00000"),
                is_active=False,
            ),
        )
    )
    uow.ratings.by_user[user_id] = PublishedRating(
        user_id=user_id,
        rating=Decimal("1510.00000"),
        rd=Decimal("200.00000"),
        volatility=Decimal("0.0600000000"),
        as_of_period_id=previous.id,
        rated_matches_count=2,
        updated_at=NOW,
        version=1,
    )

    await publisher().publish(uow, period_id=current.id, expected_calculation_version=1)

    rating = uow.ratings.by_user[user_id]
    assert rating.rd == Decimal("210.00000")
    assert rating.rated_matches_count == 2
    assert rating.as_of_period_id == current.id
    assert rating.version == 2


async def test_empty_period_is_published_without_ratings() -> None:
    uow = FakeRatingsUnitOfWork()
    period = await calculated_period(uow)

    published = await publisher().publish(uow, period_id=period.id, expected_calculation_version=1)

    assert published.status is RatingPeriodStatus.PUBLISHED
    assert published.published_version == period.calculation_version
    assert uow.ratings.by_user == {}


async def test_repeat_is_noop_and_preserves_published_at() -> None:
    uow = FakeRatingsUnitOfWork()
    period = await calculated_period(uow)
    first = await publisher().publish(uow, period_id=period.id, expected_calculation_version=1)
    repeated = await RatingPeriodPublisher(clock=lambda: datetime(2026, 12, 1, tzinfo=UTC)).publish(
        uow, period_id=period.id, expected_calculation_version=1
    )

    assert repeated == first
    assert repeated.published_at == NOW
    assert repeated.published_version == 1
    assert uow.commits == 1


async def test_repeat_uses_active_published_version_not_preparing_version() -> None:
    uow = FakeRatingsUnitOfWork()
    period = replace(
        make_period(1, calculated=True),
        status=RatingPeriodStatus.PUBLISHED,
        calculation_version=2,
        calculated_version=2,
        published_version=1,
        published_at=NOW,
    )
    await uow.periods.add(period)

    repeated = await publisher().publish(
        uow,
        period_id=period.id,
        expected_calculation_version=1,
    )

    assert repeated == period
    assert uow.commits == 0
    with pytest.raises(RatingPeriodNotPublishableError):
        await publisher().publish(
            uow,
            period_id=period.id,
            expected_calculation_version=2,
        )


async def test_previous_snapshot_is_read_from_active_published_version() -> None:
    uow = FakeRatingsUnitOfWork()
    user_id = uuid4()
    previous = replace(
        make_period(1, calculated=True),
        status=RatingPeriodStatus.PUBLISHED,
        calculation_version=2,
        calculated_version=2,
        published_version=1,
        published_at=NOW,
    )
    current = make_period(2, calculated=True)
    await uow.periods.add(previous)
    await uow.periods.add(current)
    uow.results.items.extend(
        (
            replace(
                make_result(previous, user_id, rating="1500.00000", games_count=0),
                calculation_version=1,
            ),
            make_result(previous, user_id, rating="1600.00000", games_count=0),
            make_result(current, user_id, rating="1500.00000", games_count=0),
        )
    )
    uow.ratings.by_user[user_id] = PublishedRating(
        user_id=user_id,
        rating=Decimal("1500.00000"),
        rd=Decimal("200.00000"),
        volatility=Decimal("0.0600000000"),
        as_of_period_id=previous.id,
        rated_matches_count=0,
        updated_at=NOW,
        version=1,
    )

    published = await publisher().publish(
        uow,
        period_id=current.id,
        expected_calculation_version=1,
    )

    assert published.published_version == 1


async def test_rejects_missing_marker_wrong_status_version_and_period() -> None:
    uow = FakeRatingsUnitOfWork()
    period = make_period(1)
    await uow.periods.add(period)
    with pytest.raises(RatingPeriodNotPublishableError):
        await publisher().publish(uow, period_id=period.id, expected_calculation_version=1)

    uow.periods.by_id[period.id] = replace(period, status=RatingPeriodStatus.OPEN)
    uow.periods.by_start[period.starts_at] = uow.periods.by_id[period.id]
    with pytest.raises(RatingPeriodNotPublishableError):
        await publisher().publish(uow, period_id=period.id, expected_calculation_version=1)
    with pytest.raises(RatingCalculationVersionConflictError):
        await publisher().publish(uow, period_id=period.id, expected_calculation_version=2)
    with pytest.raises(RatingPeriodNotFoundError):
        await publisher().publish(uow, period_id=uuid4(), expected_calculation_version=1)


async def test_rejects_out_of_order_and_later_already_published_period() -> None:
    uow = FakeRatingsUnitOfWork()
    previous = make_period(1, calculated=True)
    current = make_period(2, calculated=True)
    await uow.periods.add(previous)
    await uow.periods.add(current)
    with pytest.raises(RatingPublicationChronologyError):
        await publisher().publish(uow, period_id=current.id, expected_calculation_version=1)

    uow = FakeRatingsUnitOfWork()
    current = await calculated_period(uow, 1)
    later = replace(
        make_period(2, calculated=True),
        status=RatingPeriodStatus.PUBLISHED,
        published_version=1,
        published_at=NOW,
    )
    await uow.periods.add(later)
    with pytest.raises(RatingPublicationChronologyError):
        await publisher().publish(uow, period_id=current.id, expected_calculation_version=1)


async def test_rejects_incomplete_snapshot_and_fresher_rating() -> None:
    uow = FakeRatingsUnitOfWork()
    period = await calculated_period(uow)
    first, second = uuid4(), uuid4()
    await uow.inputs.add_if_absent(make_input(period, first, second))
    uow.results.items.append(make_result(period, first))
    with pytest.raises(RatingPublicationSnapshotConflictError):
        await publisher().publish(uow, period_id=period.id, expected_calculation_version=1)

    uow.results.items.append(make_result(period, second))
    uow.ratings.by_user[first] = PublishedRating(
        user_id=first,
        rating=Decimal("1600.00000"),
        rd=Decimal("100.00000"),
        volatility=Decimal("0.0600000000"),
        as_of_period_id=uuid4(),
        rated_matches_count=5,
        updated_at=NOW,
        version=2,
    )
    with pytest.raises(RatingPublicationSnapshotConflictError):
        await publisher().publish(uow, period_id=period.id, expected_calculation_version=1)

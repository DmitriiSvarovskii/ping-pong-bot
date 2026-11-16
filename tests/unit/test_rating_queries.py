"""Application tests for read-only published rating queries."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from pingstats.modules.ratings.application.errors import RatingNotFoundError
from pingstats.modules.ratings.application.queries import RatingQueryService
from pingstats.modules.ratings.domain import (
    Glicko2Parameters,
    Glicko2Strategy,
    PublishedRating,
    RatingPeriod,
    RatingPeriodResult,
    RatingPeriodStatus,
    RatingState,
)


def make_period(day: int = 10) -> RatingPeriod:
    starts_at = datetime(2026, 11, day, tzinfo=UTC)
    return RatingPeriod(
        id=uuid4(),
        starts_at=starts_at,
        ends_at=starts_at + timedelta(days=1),
        status=RatingPeriodStatus.PUBLISHED,
        algorithm="glicko2",
        algorithm_version="2",
        parameters={"tau": 0.5, "max_rd": 350.0},
        calculation_version=2,
        calculated_version=2,
        published_version=2,
        published_at=starts_at + timedelta(days=1, minutes=5),
        created_at=starts_at,
        updated_at=starts_at,
        version=3,
    )


def make_rating(user_id: UUID, period: RatingPeriod) -> PublishedRating:
    return PublishedRating(
        user_id=user_id,
        rating=Decimal("1612.50000"),
        rd=Decimal("75.00000"),
        volatility=Decimal("0.0600000000"),
        as_of_period_id=period.id,
        rated_matches_count=8,
        updated_at=period.ends_at,
        version=2,
    )


def make_result(user_id: UUID, period: RatingPeriod) -> RatingPeriodResult:
    return RatingPeriodResult(
        id=uuid4(),
        period_id=period.id,
        calculation_version=2,
        user_id=user_id,
        rating_before=Decimal("1600.00000"),
        rating_after=Decimal("1612.50000"),
        rd_before=Decimal("80.00000"),
        rd_after=Decimal("75.00000"),
        volatility_before=Decimal("0.0600000000"),
        volatility_after=Decimal("0.0600000000"),
        games_count=2,
        is_active=True,
        created_at=period.ends_at,
    )


class QueryRepository:
    def __init__(
        self,
        current: tuple[PublishedRating, RatingPeriod] | None = None,
        history: tuple[tuple[RatingPeriodResult, RatingPeriod], ...] = (),
    ) -> None:
        self.current = current
        self.history = history
        self.last_page: tuple[int, int] | None = None

    async def get_current(self, user_id: UUID) -> tuple[PublishedRating, RatingPeriod] | None:
        if self.current is None or self.current[0].user_id != user_id:
            return None
        return self.current

    async def list_history(
        self, user_id: UUID, *, limit: int, offset: int
    ) -> tuple[tuple[tuple[RatingPeriodResult, RatingPeriod], ...], int]:
        self.last_page = (limit, offset)
        matching = tuple(row for row in self.history if row[0].user_id == user_id)
        return matching[offset : offset + limit], len(matching)


async def test_current_rating_applies_completed_calendar_inactivity_without_writes() -> None:
    user_id = uuid4()
    period = make_period()
    stored = make_rating(user_id, period)
    repository = QueryRepository((stored, period))
    service = RatingQueryService(clock=lambda: datetime(2026, 11, 14, 12, tzinfo=UTC))

    view = await service.get_current(repository, user_id)

    expected = Glicko2Strategy(Glicko2Parameters(tau=0.5, max_rd=350.0)).advance_inactivity(
        RatingState(rating=1612.5, rd=75.0, volatility=0.06), periods=3
    )
    assert view.rating == stored.rating
    assert view.rd == stored.rd
    assert view.effective_rd == Decimal(str(expected.rd)).quantize(Decimal("0.00001"))
    assert view.inactivity_periods == 3
    assert repository.current == (stored, period)


async def test_current_rating_has_no_inactivity_before_next_utc_boundary() -> None:
    user_id = uuid4()
    period = make_period()
    service = RatingQueryService(clock=lambda: datetime(2026, 11, 11, 23, 59, tzinfo=UTC))

    view = await service.get_current(
        QueryRepository((make_rating(user_id, period), period)), user_id
    )

    assert view.inactivity_periods == 0
    assert view.effective_rd == view.rd


async def test_missing_current_rating_is_not_materialized() -> None:
    with pytest.raises(RatingNotFoundError):
        await RatingQueryService().get_current(QueryRepository(), uuid4())


async def test_history_is_paginated_and_empty_history_is_valid() -> None:
    user_id = uuid4()
    periods = (make_period(8), make_period(9), make_period(10))
    rows = tuple((make_result(user_id, period), period) for period in reversed(periods))
    repository = QueryRepository(history=rows)

    history, total = await RatingQueryService.list_history(repository, user_id, limit=1, offset=1)
    empty, empty_total = await RatingQueryService.list_history(
        QueryRepository(), user_id, limit=20, offset=0
    )

    assert total == 3
    assert [item.period_id for item in history] == [periods[1].id]
    assert repository.last_page == (1, 1)
    assert empty == ()
    assert empty_total == 0

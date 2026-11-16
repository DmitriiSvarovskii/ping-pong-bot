"""Application tests for read-only published rating queries."""

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from pingstats.modules.ratings.application.errors import RatingNotFoundError
from pingstats.modules.ratings.application.queries import RatingQueryService
from pingstats.modules.ratings.application.status import RatingStatusFacts
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
        current: tuple[PublishedRating, RatingPeriod, int] | None = None,
        history: tuple[tuple[RatingPeriodResult, RatingPeriod], ...] = (),
        status: RatingStatusFacts | None = None,
        published_match_versions: Mapping[UUID, frozenset[int]] | None = None,
    ) -> None:
        self.current = current
        self.history = history
        self.last_page: tuple[int, int] | None = None
        self.last_dates: tuple[date | None, date | None] | None = None
        self.status = status or RatingStatusFacts(0, False, None, False)
        self.published_match_versions = dict(published_match_versions or {})
        self.published_match_version_calls = 0

    async def get_current(self, user_id: UUID) -> tuple[PublishedRating, RatingPeriod, int] | None:
        if self.current is None or self.current[0].user_id != user_id:
            return None
        return self.current

    async def list_history(
        self,
        user_id: UUID,
        *,
        limit: int,
        offset: int,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> tuple[tuple[tuple[RatingPeriodResult, RatingPeriod], ...], int]:
        self.last_page = (limit, offset)
        self.last_dates = (from_date, to_date)
        matching = tuple(row for row in self.history if row[0].user_id == user_id)
        if from_date is not None:
            matching = tuple(row for row in matching if row[1].starts_at.date() >= from_date)
        if to_date is not None:
            matching = tuple(row for row in matching if row[1].starts_at.date() <= to_date)
        return matching[offset : offset + limit], len(matching)

    async def get_status(self, user_id: UUID, *, at: datetime) -> RatingStatusFacts:
        del user_id, at
        return self.status

    async def get_published_match_versions(
        self, match_ids: Sequence[UUID]
    ) -> Mapping[UUID, frozenset[int]]:
        self.published_match_version_calls += 1
        return {
            match_id: self.published_match_versions[match_id]
            for match_id in match_ids
            if match_id in self.published_match_versions
        }


async def test_current_rating_applies_completed_calendar_inactivity_without_writes() -> None:
    user_id = uuid4()
    period = make_period()
    stored = make_rating(user_id, period)
    repository = QueryRepository((stored, period, 4))
    service = RatingQueryService(clock=lambda: datetime(2026, 11, 14, 12, tzinfo=UTC))

    view = await service.get_current(repository, user_id)

    expected = Glicko2Strategy(Glicko2Parameters(tau=0.5, max_rd=350.0)).advance_inactivity(
        RatingState(rating=1612.5, rd=75.0, volatility=0.06), periods=3
    )
    assert view.rating == stored.rating
    assert view.position == 4
    assert view.rd == stored.rd
    assert view.effective_rd == Decimal(str(expected.rd)).quantize(Decimal("0.00001"))
    assert view.inactivity_periods == 3
    assert repository.current == (stored, period, 4)


async def test_current_rating_has_no_inactivity_before_next_utc_boundary() -> None:
    user_id = uuid4()
    period = make_period()
    service = RatingQueryService(clock=lambda: datetime(2026, 11, 11, 23, 59, tzinfo=UTC))

    view = await service.get_current(
        QueryRepository((make_rating(user_id, period), period, 1)), user_id
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


async def test_history_date_boundaries_are_inclusive_and_filter_before_pagination() -> None:
    user_id = uuid4()
    periods = tuple(make_period(day) for day in (7, 8, 9, 10, 11))
    repository = QueryRepository(
        history=tuple((make_result(user_id, period), period) for period in reversed(periods))
    )

    history, total = await RatingQueryService.list_history(
        repository,
        user_id,
        limit=1,
        offset=1,
        from_date=date(2026, 11, 8),
        to_date=date(2026, 11, 10),
    )
    from_only, from_total = await RatingQueryService.list_history(
        repository,
        user_id,
        limit=20,
        offset=0,
        from_date=date(2026, 11, 10),
    )
    to_only, to_total = await RatingQueryService.list_history(
        repository,
        user_id,
        limit=20,
        offset=0,
        to_date=date(2026, 11, 8),
    )

    assert total == 3
    assert [item.period_id for item in history] == [periods[2].id]
    assert repository.last_dates == (None, date(2026, 11, 8))
    assert from_total == 2
    assert [item.period_id for item in from_only] == [periods[4].id, periods[3].id]
    assert to_total == 2
    assert [item.period_id for item in to_only] == [periods[1].id, periods[0].id]

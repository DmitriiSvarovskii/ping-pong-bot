"""Application tests for deterministic period-level Glicko-2 calculation."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from pingstats.modules.ratings.application import (
    RatingHistoryUnavailableError,
    RatingPeriodCalculator,
    RatingPeriodNotCalculatingError,
    RatingPeriodNotFoundError,
)
from pingstats.modules.ratings.domain import (
    Glicko2Parameters,
    Glicko2Strategy,
    RatingOpponent,
    RatingPeriod,
    RatingPeriodMatchInput,
    RatingPeriodResult,
    RatingPeriodStatus,
    RatingState,
)
from tests.unit.rating_fakes import (
    FakeRatingInputRepository,
    FakeRatingPeriodRepository,
    FakeRatingResultRepository,
)


def make_period(
    day: int,
    *,
    status: RatingPeriodStatus = RatingPeriodStatus.CALCULATING,
    calculated: bool = False,
    starts_at: datetime | None = None,
    algorithm_version: str = "2",
):
    starts_at = starts_at or datetime(2026, 11, day, tzinfo=UTC)
    return RatingPeriod(
        id=uuid4(),
        starts_at=starts_at,
        ends_at=starts_at + timedelta(days=1),
        status=status,
        algorithm="glicko2",
        algorithm_version=algorithm_version,
        parameters={
            "initial_rating": 1500.0,
            "initial_rd": 350.0,
            "initial_volatility": 0.06,
            "tau": 0.5,
            "max_rd": 350.0,
            "convergence_epsilon": 0.000001,
            "max_iterations": 100,
        },
        calculation_version=1,
        calculated_version=1 if calculated else None,
        published_version=None,
        published_at=None,
        created_at=starts_at,
        updated_at=starts_at,
        version=2,
    )


def make_input(
    period: RatingPeriod,
    first: UUID,
    second: UUID,
    *,
    first_score: str = "1.00",
    match_id: UUID | None = None,
) -> RatingPeriodMatchInput:
    return RatingPeriodMatchInput(
        period_id=period.id,
        calculation_version=period.calculation_version,
        match_id=match_id or uuid4(),
        source_match_version=2,
        correction_revision_id=None,
        result_fingerprint=uuid4().hex * 2,
        player1_id=first,
        player2_id=second,
        score1=Decimal(first_score),
        score2=Decimal("1.00") - Decimal(first_score),
        completed_at=period.starts_at + timedelta(hours=12),
        created_at=period.starts_at + timedelta(hours=12),
    )


def make_result(
    period: RatingPeriod,
    user_id: UUID,
    *,
    rating: str = "1500.00000",
    rd: str = "200.00000",
    volatility: str = "0.0600000000",
    games_count: int = 1,
) -> RatingPeriodResult:
    return RatingPeriodResult(
        id=uuid4(),
        period_id=period.id,
        calculation_version=period.calculation_version,
        user_id=user_id,
        rating_before=Decimal(rating),
        rating_after=Decimal(rating),
        rd_before=Decimal(rd),
        rd_after=Decimal(rd),
        volatility_before=Decimal(volatility),
        volatility_after=Decimal(volatility),
        games_count=games_count,
        is_active=games_count > 0,
        created_at=period.ends_at,
    )


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

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback

    async def commit(self) -> None:
        raise AssertionError("Calculation must not commit")

    async def rollback(self) -> None:
        pass


async def setup_calculation(
    period: RatingPeriod,
    inputs: tuple[RatingPeriodMatchInput, ...] = (),
    *,
    previous: RatingPeriod | None = None,
    previous_results: tuple[RatingPeriodResult, ...] = (),
):
    periods = FakeRatingPeriodRepository()
    input_repository = FakeRatingInputRepository()
    result_repository = FakeRatingResultRepository()
    if previous is not None:
        await periods.add(previous)
    await periods.add(period)
    for item in inputs:
        await input_repository.add_if_absent(item)
    result_repository.items.extend(previous_results)
    uow = FakeRatingsUnitOfWork(periods, input_repository, result_repository)
    return await RatingPeriodCalculator().calculate(uow, period_id=period.id)


async def test_one_match_uses_initial_state_in_first_period() -> None:
    first, second = uuid4(), uuid4()
    period = make_period(1)

    calculated = await setup_calculation(period, (make_input(period, first, second),))
    players = {item.user_id: item for item in calculated.players}

    assert calculated.period_id == period.id
    assert players[first].rating_before == Decimal("1500.00000")
    assert players[first].rating_after > players[first].rating_before
    assert players[second].rating_after < players[second].rating_before
    assert players[first].games_count == players[second].games_count == 1


async def test_multiple_matches_and_players_are_one_simultaneous_period() -> None:
    first, second, third = uuid4(), uuid4(), uuid4()
    period = make_period(1)
    inputs = (
        make_input(period, first, second),
        make_input(period, third, first),
        make_input(period, first, second),
    )

    calculated = await setup_calculation(period, inputs)
    players = {item.user_id: item for item in calculated.players}

    assert players[first].games_count == 3
    assert players[second].games_count == 2
    assert players[third].games_count == 1
    assert len(players) == 3


async def test_input_order_does_not_change_result() -> None:
    first, second, third = uuid4(), uuid4(), uuid4()
    period = make_period(1)
    inputs = (
        make_input(period, first, second),
        make_input(period, first, third, first_score="0.00"),
        make_input(period, second, third),
    )

    forward = await setup_calculation(period, inputs)
    reversed_result = await setup_calculation(period, tuple(reversed(inputs)))

    assert forward == reversed_result


async def test_uses_all_opponents_starting_ratings_not_sequential_updates() -> None:
    first, second = uuid4(), uuid4()
    previous = make_period(1, calculated=True)
    period = make_period(2)
    prior = (
        make_result(previous, first, rating="1600.00000", rd="100.00000"),
        make_result(previous, second, rating="1400.00000", rd="80.00000"),
    )

    calculated = await setup_calculation(
        period,
        (make_input(period, first, second),),
        previous=previous,
        previous_results=prior,
    )
    first_result = next(item for item in calculated.players if item.user_id == first)
    expected = Glicko2Strategy(Glicko2Parameters()).calculate(
        RatingState(1600.0, 100.0, 0.06),
        (RatingOpponent(1400.0, 80.0, 1.0),),
    )

    assert first_result.rating_before == Decimal("1600.00000")
    assert first_result.rating_after == Decimal(str(expected.rating)).quantize(Decimal("0.00001"))


async def test_inactive_previously_rated_player_gets_only_rd_increase() -> None:
    first, second, inactive = uuid4(), uuid4(), uuid4()
    previous = make_period(1, calculated=True)
    period = make_period(2)
    prior = tuple(
        make_result(previous, user_id, rd="80.00000") for user_id in (first, second, inactive)
    )

    calculated = await setup_calculation(
        period,
        (make_input(period, first, second),),
        previous=previous,
        previous_results=prior,
    )
    result = next(item for item in calculated.players if item.user_id == inactive)

    assert result.games_count == 0
    assert result.is_active is False
    assert result.rating_after == result.rating_before
    assert result.volatility_after == result.volatility_before
    assert result.rd_after > result.rd_before


async def test_new_player_in_later_period_uses_initial_state() -> None:
    existing, newcomer = uuid4(), uuid4()
    previous = make_period(1, calculated=True)
    period = make_period(2)

    calculated = await setup_calculation(
        period,
        (make_input(period, newcomer, existing),),
        previous=previous,
        previous_results=(make_result(previous, existing, rating="1700.00000"),),
    )
    result = next(item for item in calculated.players if item.user_id == newcomer)

    assert result.rating_before == Decimal("1500.00000")
    assert result.rd_before == Decimal("350.00000")


async def test_multiple_sequential_periods_use_immediately_previous_results() -> None:
    first, second = uuid4(), uuid4()
    older = make_period(1, calculated=True)
    previous = make_period(2, calculated=True)
    current = make_period(3)
    periods = FakeRatingPeriodRepository()
    inputs = FakeRatingInputRepository()
    results = FakeRatingResultRepository()
    for period in (older, previous, current):
        await periods.add(period)
    await inputs.add_if_absent(make_input(current, first, second))
    results.items.extend(
        (
            make_result(older, first, rating="1200.00000"),
            make_result(older, second, rating="1800.00000"),
            make_result(previous, first, rating="1550.00000"),
            make_result(previous, second, rating="1450.00000"),
        )
    )

    calculated = await RatingPeriodCalculator().calculate(
        FakeRatingsUnitOfWork(periods, inputs, results), period_id=current.id
    )
    before = {item.user_id: item.rating_before for item in calculated.players}

    assert before == {first: Decimal("1550.00000"), second: Decimal("1450.00000")}


async def test_open_period_is_rejected() -> None:
    period = make_period(1, status=RatingPeriodStatus.OPEN)

    with pytest.raises(RatingPeriodNotCalculatingError):
        await setup_calculation(period)


async def test_missing_period_is_rejected() -> None:
    uow = FakeRatingsUnitOfWork(
        FakeRatingPeriodRepository(),
        FakeRatingInputRepository(),
        FakeRatingResultRepository(),
    )

    with pytest.raises(RatingPeriodNotFoundError):
        await RatingPeriodCalculator().calculate(uow, period_id=uuid4())


async def test_missing_or_incomplete_previous_results_are_rejected() -> None:
    first, second = uuid4(), uuid4()
    previous = make_period(1)
    period = make_period(2)

    with pytest.raises(RatingHistoryUnavailableError):
        await setup_calculation(period, previous=previous)

    calculated_previous = make_period(1, calculated=True)
    previous_input = make_input(calculated_previous, first, second)
    periods = FakeRatingPeriodRepository()
    inputs = FakeRatingInputRepository()
    results = FakeRatingResultRepository()
    await periods.add(calculated_previous)
    await periods.add(period)
    await inputs.add_if_absent(previous_input)
    results.items.append(make_result(calculated_previous, first))
    with pytest.raises(RatingHistoryUnavailableError, match="incomplete"):
        await RatingPeriodCalculator().calculate(
            FakeRatingsUnitOfWork(periods, inputs, results), period_id=period.id
        )


async def test_repeated_calculation_is_identical_and_does_not_persist() -> None:
    first, second = uuid4(), uuid4()
    period = make_period(1)
    input_item = make_input(period, first, second)

    first_result = await setup_calculation(period, (input_item,))
    repeated = await setup_calculation(period, (input_item,))

    assert repeated == first_result


async def test_previous_open_period_blocks_out_of_order_calculation() -> None:
    previous = make_period(1, status=RatingPeriodStatus.OPEN)
    period = make_period(2)

    with pytest.raises(RatingHistoryUnavailableError, match="has not completed"):
        await setup_calculation(
            period,
            previous=previous,
            previous_results=(make_result(previous, uuid4()),),
        )


@pytest.mark.parametrize(
    ("previous_start", "current_start", "missing_periods"),
    [
        (datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 11, 2, tzinfo=UTC), 0),
        (datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 11, 3, tzinfo=UTC), 1),
        (datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 11, 10, tzinfo=UTC), 8),
        (datetime(2026, 11, 30, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC), 0),
        (datetime(2026, 12, 31, tzinfo=UTC), datetime(2027, 1, 2, tzinfo=UTC), 1),
        (datetime(2028, 2, 28, tzinfo=UTC), datetime(2028, 3, 1, tzinfo=UTC), 1),
    ],
)
async def test_calendar_gap_uses_utc_period_boundaries(
    previous_start: datetime,
    current_start: datetime,
    missing_periods: int,
) -> None:
    user_id = uuid4()
    previous = make_period(1, calculated=True, starts_at=previous_start)
    current = make_period(1, starts_at=current_start)
    prior = make_result(previous, user_id, rd="80.00000", games_count=0)

    calculated = await setup_calculation(
        current,
        previous=previous,
        previous_results=(prior,),
    )
    result = calculated.players[0]
    expected = Glicko2Strategy().advance_inactivity(
        RatingState(1500.0, 80.0, 0.06), periods=missing_periods + 1
    )

    assert result.rd_before == Decimal("80.00000")
    assert result.rd_after == Decimal(str(expected.rd)).quantize(Decimal("0.00001"))


async def test_active_players_and_opponents_use_calendar_adjusted_states() -> None:
    first, second = uuid4(), uuid4()
    previous = make_period(1, calculated=True)
    current = make_period(10)
    first_state = RatingState(1600.0, 80.0, 0.06)
    second_state = RatingState(1400.0, 120.0, 0.05)
    strategy = Glicko2Strategy()
    adjusted_first = strategy.advance_inactivity(first_state, periods=8)
    adjusted_second = strategy.advance_inactivity(second_state, periods=8)

    calculated = await setup_calculation(
        current,
        (make_input(current, first, second),),
        previous=previous,
        previous_results=(
            make_result(previous, first, rating="1600.00000", rd="80.00000"),
            make_result(
                previous,
                second,
                rating="1400.00000",
                rd="120.00000",
                volatility="0.0500000000",
            ),
        ),
    )
    players = {item.user_id: item for item in calculated.players}
    expected_first = strategy.calculate(
        adjusted_first,
        (RatingOpponent(adjusted_second.rating, adjusted_second.rd, 1.0),),
    )

    assert players[first].rating_before == Decimal("1600.00000")
    assert players[first].rd_before == Decimal("80.00000")
    assert players[first].rating_after == Decimal(str(expected_first.rating)).quantize(
        Decimal("0.00001")
    )
    assert players[first].rd_after == Decimal(str(expected_first.rd)).quantize(Decimal("0.00001"))


async def test_new_player_does_not_age_before_first_rated_period() -> None:
    existing, newcomer = uuid4(), uuid4()
    previous = make_period(1, calculated=True)
    current = make_period(10)
    strategy = Glicko2Strategy()
    adjusted_existing = strategy.advance_inactivity(RatingState(1700.0, 90.0, 0.06), periods=8)
    expected = strategy.calculate(
        RatingState(1500.0, 350.0, 0.06),
        (RatingOpponent(adjusted_existing.rating, adjusted_existing.rd, 1.0),),
    )

    calculated = await setup_calculation(
        current,
        (make_input(current, newcomer, existing),),
        previous=previous,
        previous_results=(make_result(previous, existing, rating="1700.00000", rd="90.00000"),),
    )
    result = next(item for item in calculated.players if item.user_id == newcomer)

    assert result.rating_before == Decimal("1500.00000")
    assert result.rd_before == Decimal("350.00000")
    assert result.rating_after == Decimal(str(expected.rating)).quantize(Decimal("0.00001"))


async def test_algorithm_version_one_preserves_legacy_sparse_semantics() -> None:
    user_id = uuid4()
    previous = make_period(1, calculated=True, algorithm_version="1")
    current = make_period(10, algorithm_version="1")

    calculated = await setup_calculation(
        current,
        previous=previous,
        previous_results=(make_result(previous, user_id, rd="80.00000", games_count=0),),
    )
    expected = Glicko2Strategy().calculate(RatingState(1500.0, 80.0, 0.06), ())

    assert calculated.players[0].rd_after == Decimal(str(expected.rd)).quantize(Decimal("0.00001"))


async def test_published_previous_period_uses_active_version_not_preparing_version() -> None:
    user_id = uuid4()
    previous = replace(
        make_period(1, calculated=True),
        status=RatingPeriodStatus.PUBLISHED,
        calculation_version=2,
        calculated_version=2,
        published_version=1,
    )
    current = make_period(2)
    active = replace(
        make_result(previous, user_id, rating="1500.00000", rd="80.00000", games_count=0),
        calculation_version=1,
    )
    preparing = make_result(
        previous,
        user_id,
        rating="1800.00000",
        rd="200.00000",
        games_count=0,
    )
    periods = FakeRatingPeriodRepository()
    inputs = FakeRatingInputRepository()
    results = FakeRatingResultRepository()
    await periods.add(previous)
    await periods.add(current)
    results.items.extend((active, preparing))

    calculated = await RatingPeriodCalculator().calculate(
        FakeRatingsUnitOfWork(periods, inputs, results),
        period_id=current.id,
    )

    assert calculated.players[0].rating_before == Decimal("1500.00000")


async def test_long_calendar_gap_is_capped_and_repeated_calculation_is_deterministic() -> None:
    user_id = uuid4()
    previous = make_period(
        1,
        calculated=True,
        starts_at=datetime(2000, 1, 1, tzinfo=UTC),
    )
    current = make_period(1, starts_at=datetime(2026, 1, 1, tzinfo=UTC))
    prior = (make_result(previous, user_id, rd="80.00000", games_count=0),)

    first = await setup_calculation(current, previous=previous, previous_results=prior)
    repeated = await setup_calculation(current, previous=previous, previous_results=prior)

    assert first == repeated
    assert first.players[0].rd_after == Decimal("350.00000")

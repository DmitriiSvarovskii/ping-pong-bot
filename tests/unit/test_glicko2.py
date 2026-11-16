"""Reference and invariant tests for the pure Glicko-2 rating engine."""

from dataclasses import FrozenInstanceError
from itertools import permutations
from math import isfinite

import pytest

from pingstats.modules.ratings.domain import (
    Glicko2ConvergenceError,
    Glicko2Parameters,
    Glicko2Strategy,
    InvalidRatingValueError,
    RatingOpponent,
    RatingState,
)


def official_example() -> tuple[RatingState, tuple[RatingOpponent, ...]]:
    return (
        RatingState(rating=1500.0, rd=200.0, volatility=0.06),
        (
            RatingOpponent(rating=1400.0, rd=30.0, score=1.0),
            RatingOpponent(rating=1550.0, rd=100.0, score=0.0),
            RatingOpponent(rating=1700.0, rd=300.0, score=0.0),
        ),
    )


def test_official_reference_vector() -> None:
    player, opponents = official_example()

    result = Glicko2Strategy().calculate(player, opponents)

    assert result.rating == pytest.approx(1464.06, abs=0.01)
    assert result.rd == pytest.approx(151.52, abs=0.01)
    assert result.volatility == pytest.approx(0.05999, abs=0.00001)


def test_one_win_and_one_loss_move_rating_in_expected_directions() -> None:
    strategy = Glicko2Strategy()
    player = RatingState(1500.0, 200.0, 0.06)
    win = strategy.calculate(player, [RatingOpponent(1500.0, 200.0, 1.0)])
    loss = strategy.calculate(player, [RatingOpponent(1500.0, 200.0, 0.0)])

    assert win.rating > player.rating
    assert loss.rating < player.rating
    assert win.rating - player.rating == pytest.approx(player.rating - loss.rating)


def test_draw_against_equal_opponent_keeps_rating_and_reduces_uncertainty() -> None:
    player = RatingState(1500.0, 200.0, 0.06)

    result = Glicko2Strategy().calculate(player, [RatingOpponent(1500.0, 200.0, 0.5)])

    assert result.rating == pytest.approx(player.rating)
    assert result.rd < player.rd
    assert result.volatility > 0.0


def test_multiple_opponents_are_calculated_as_one_period() -> None:
    player, opponents = official_example()

    result = Glicko2Strategy().calculate(player, opponents)

    assert result.rating < player.rating
    assert result.rd < player.rd


def test_inactive_period_preserves_rating_and_volatility_and_increases_rd() -> None:
    player = RatingState(1620.0, 80.0, 0.05)

    result = Glicko2Strategy().calculate(player, [])

    assert result.rating == player.rating
    assert result.volatility == player.volatility
    assert result.rd > player.rd


def test_multiple_inactive_periods_are_deterministic_and_accumulate_rd() -> None:
    strategy = Glicko2Strategy()
    state = RatingState(1500.0, 100.0, 0.06)

    states = []
    for _ in range(3):
        state = strategy.calculate(state, ())
        states.append(state)

    assert states[0].rd < states[1].rd < states[2].rd
    assert all(item.rating == 1500.0 and item.volatility == 0.06 for item in states)


@pytest.mark.parametrize("periods", [0, 1, 8, 10_000])
def test_optimized_inactivity_is_equivalent_to_sequential_periods(periods: int) -> None:
    strategy = Glicko2Strategy(Glicko2Parameters(max_rd=350.0))
    initial = RatingState(1620.0, 80.0, 0.05)
    sequential = initial
    for _ in range(periods):
        sequential = strategy.calculate(sequential, ())

    optimized = strategy.advance_inactivity(initial, periods=periods)

    assert optimized.rating == sequential.rating
    assert optimized.rd == pytest.approx(sequential.rd, abs=1e-12)
    assert optimized.volatility == sequential.volatility


def test_optimized_inactivity_respects_rd_cap() -> None:
    strategy = Glicko2Strategy(Glicko2Parameters(initial_rd=250.0, max_rd=250.0))

    result = strategy.advance_inactivity(RatingState(1500.0, 249.9, 0.5), periods=10_000)

    assert result == RatingState(1500.0, 250.0, 0.5)


@pytest.mark.parametrize("periods", [-1, 1.5, True])
def test_optimized_inactivity_rejects_invalid_period_count(periods: object) -> None:
    with pytest.raises(ValueError, match="non-negative integer"):
        Glicko2Strategy().advance_inactivity(  # type: ignore[arg-type]
            RatingState(1500.0, 100.0, 0.06), periods=periods
        )


def test_rd_is_capped_for_inactive_and_active_players() -> None:
    parameters = Glicko2Parameters(initial_rd=250.0, max_rd=250.0)
    strategy = Glicko2Strategy(parameters)
    inactive = strategy.calculate(RatingState(1500.0, 249.9, 0.5), ())
    active = strategy.calculate(
        RatingState(1500.0, 400.0, 0.06),
        [RatingOpponent(1500.0, 400.0, 0.5)],
    )

    assert inactive.rd == 250.0
    assert active.rd <= 250.0


def test_same_inputs_produce_identical_results() -> None:
    player, opponents = official_example()
    strategy = Glicko2Strategy()

    assert strategy.calculate(player, opponents) == strategy.calculate(player, opponents)


def test_opponent_order_does_not_materially_change_result() -> None:
    player, opponents = official_example()
    strategy = Glicko2Strategy()
    expected = strategy.calculate(player, opponents)

    for reordered in permutations(opponents):
        result = strategy.calculate(player, reordered)
        assert result.rating == pytest.approx(expected.rating, abs=1e-10)
        assert result.rd == pytest.approx(expected.rd, abs=1e-10)
        assert result.volatility == pytest.approx(expected.volatility, abs=1e-12)


def test_calculation_does_not_mutate_inputs_and_values_are_immutable() -> None:
    player, opponents = official_example()
    player_before = player
    opponents_before = tuple(opponents)

    Glicko2Strategy().calculate(player, opponents)

    assert player == player_before
    assert opponents == opponents_before
    with pytest.raises(FrozenInstanceError):
        player.rating = 1600.0  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("initial_rating", float("nan")),
        ("initial_rd", 0.0),
        ("initial_volatility", -0.1),
        ("tau", float("inf")),
        ("max_rd", 0.0),
        ("convergence_epsilon", 0.0),
        ("max_iterations", 0),
        ("max_iterations", 1.5),
    ],
)
def test_rejects_invalid_parameters(field: str, value: object) -> None:
    with pytest.raises(InvalidRatingValueError):
        Glicko2Parameters(**{field: value})  # type: ignore[arg-type]


def test_initial_rd_cannot_exceed_maximum() -> None:
    with pytest.raises(InvalidRatingValueError, match="must not exceed"):
        Glicko2Parameters(initial_rd=351.0, max_rd=350.0)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: RatingState(float("nan"), 100.0, 0.06),
        lambda: RatingState(1500.0, float("inf"), 0.06),
        lambda: RatingState(1500.0, 100.0, 0.0),
        lambda: RatingOpponent(float("-inf"), 100.0, 1.0),
        lambda: RatingOpponent(1500.0, float("nan"), 1.0),
        lambda: RatingOpponent(1500.0, 100.0, float("inf")),
        lambda: RatingOpponent(1500.0, 100.0, 0.25),
    ],
)
def test_rejects_invalid_states_opponents_nan_and_infinity(factory) -> None:
    with pytest.raises(InvalidRatingValueError):
        factory()


def test_initial_state_uses_validated_parameters() -> None:
    parameters = Glicko2Parameters(
        initial_rating=1400.0,
        initial_rd=250.0,
        initial_volatility=0.05,
        max_rd=300.0,
    )

    assert parameters.initial_state() == RatingState(1400.0, 250.0, 0.05)


def test_reports_non_convergence_instead_of_returning_partial_state() -> None:
    player, opponents = official_example()
    strategy = Glicko2Strategy(Glicko2Parameters(convergence_epsilon=1e-15, max_iterations=1))

    with pytest.raises(Glicko2ConvergenceError, match="did not converge"):
        strategy.calculate(player, opponents)


def test_results_are_finite_for_valid_inputs() -> None:
    player, opponents = official_example()

    result = Glicko2Strategy().calculate(player, opponents)

    assert all(isfinite(value) for value in (result.rating, result.rd, result.volatility))

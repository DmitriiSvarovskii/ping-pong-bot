"""Pure table-tennis score validation tests."""

import pytest

from pingstats.modules.matches.domain import InvalidMatchResultError, ScoreValidator, SetScore


@pytest.mark.parametrize(
    ("first", "second", "winner"),
    [(11, 0, 1), (11, 8, 1), (11, 9, 1), (12, 10, 1), (15, 13, 1), (21, 19, 1), (8, 11, 2)],
)
def test_valid_set_scores(first: int, second: int, winner: int) -> None:
    assert ScoreValidator.set_winner(SetScore(first, second), points_to_win=11, win_by=2) == winner


@pytest.mark.parametrize(
    ("first", "second"),
    [(11, 10), (12, 9), (10, 10), (-1, 11), (11, -1), (9, 8), (13, 10)],
)
def test_invalid_set_scores(first: int, second: int) -> None:
    with pytest.raises(InvalidMatchResultError):
        ScoreValidator.set_winner(SetScore(first, second), points_to_win=11, win_by=2)


@pytest.mark.parametrize("target", [7, 11, 21])
def test_configurable_target_score(target: int) -> None:
    assert ScoreValidator.set_winner(SetScore(target, target - 2), points_to_win=target) == 1
    assert ScoreValidator.set_winner(SetScore(target + 1, target - 1), points_to_win=target) == 1


def test_match_winner_and_incomplete_result() -> None:
    scores = [SetScore(11, 5), SetScore(8, 11), SetScore(12, 10), SetScore(11, 4)]

    assert (
        ScoreValidator.match_winner(
            scores,
            points_to_win=11,
            win_by=2,
            sets_to_win=3,
            require_complete=True,
        )
        == 1
    )
    assert (
        ScoreValidator.match_winner(
            scores[:2],
            points_to_win=11,
            win_by=2,
            sets_to_win=3,
            require_complete=False,
        )
        is None
    )
    with pytest.raises(InvalidMatchResultError, match="does not determine"):
        ScoreValidator.match_winner(
            scores[:2],
            points_to_win=11,
            win_by=2,
            sets_to_win=3,
            require_complete=True,
        )


def test_rejects_sets_after_match_winner() -> None:
    with pytest.raises(InvalidMatchResultError, match="after the match winner"):
        ScoreValidator.match_winner(
            [SetScore(11, 1), SetScore(11, 2), SetScore(11, 3), SetScore(4, 11)],
            points_to_win=11,
            win_by=2,
            sets_to_win=3,
            require_complete=True,
        )


@pytest.mark.parametrize("value", [1.5, True, "11"])
def test_rejects_non_integer_scores(value: object) -> None:
    with pytest.raises(InvalidMatchResultError, match="integers"):
        ScoreValidator.set_winner(SetScore(value, 0), points_to_win=11)  # type: ignore[arg-type]

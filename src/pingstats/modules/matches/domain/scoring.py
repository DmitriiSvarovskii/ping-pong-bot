"""Pure validation and winner calculation for table-tennis scores."""

from collections.abc import Sequence

from pingstats.modules.matches.domain.entities import SetScore


class InvalidMatchResultError(ValueError):
    """Raised when scores cannot represent a finished set or match."""


class ScoreValidator:
    """Validate configurable set rules and derive the match winner position."""

    @staticmethod
    def validate_settings(*, points_to_win: int, win_by: int, sets_to_win: int) -> None:
        values = (points_to_win, win_by, sets_to_win)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
            raise InvalidMatchResultError("Match settings must be integers")
        if points_to_win < 1 or win_by < 1 or sets_to_win < 1:
            raise InvalidMatchResultError("Match settings must be positive")

    @staticmethod
    def set_winner(score: SetScore, *, points_to_win: int, win_by: int = 2) -> int:
        ScoreValidator.validate_settings(points_to_win=points_to_win, win_by=win_by, sets_to_win=1)
        first, second = score.player1_score, score.player2_score
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (first, second)):
            raise InvalidMatchResultError("Set scores must be integers")
        if first < 0 or second < 0:
            raise InvalidMatchResultError("Set scores cannot be negative")
        if first == second:
            raise InvalidMatchResultError("A completed set cannot be tied")

        winner_score, loser_score = max(first, second), min(first, second)
        if winner_score < points_to_win or winner_score - loser_score < win_by:
            raise InvalidMatchResultError("Set has not been won by the required margin")
        deuce_threshold = points_to_win - win_by
        if loser_score <= deuce_threshold and winner_score != points_to_win:
            raise InvalidMatchResultError("Set continued after it should have ended")
        if loser_score > deuce_threshold and winner_score != loser_score + win_by:
            raise InvalidMatchResultError("Deuce set must end at the configured margin")
        return 1 if first > second else 2

    @staticmethod
    def match_winner(
        scores: Sequence[SetScore],
        *,
        points_to_win: int,
        win_by: int,
        sets_to_win: int,
        require_complete: bool,
    ) -> int | None:
        ScoreValidator.validate_settings(
            points_to_win=points_to_win,
            win_by=win_by,
            sets_to_win=sets_to_win,
        )
        wins = {1: 0, 2: 0}
        winner: int | None = None
        for score in scores:
            if winner is not None:
                raise InvalidMatchResultError("Sets cannot be added after the match winner")
            set_winner = ScoreValidator.set_winner(
                score, points_to_win=points_to_win, win_by=win_by
            )
            wins[set_winner] += 1
            if wins[set_winner] == sets_to_win:
                winner = set_winner
        if require_complete and winner is None:
            raise InvalidMatchResultError("Full result does not determine a match winner")
        return winner

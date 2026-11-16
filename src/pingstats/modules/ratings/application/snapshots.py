"""Shared validation for complete immutable rating-period snapshots."""

from collections import Counter
from collections.abc import Sequence

from pingstats.modules.ratings.application.errors import (
    RatingPublicationSnapshotConflictError,
)
from pingstats.modules.ratings.domain import RatingPeriodMatchInput, RatingPeriodResult


def validate_rating_snapshot(
    results: Sequence[RatingPeriodResult],
    inputs: Sequence[RatingPeriodMatchInput],
    previous_results: Sequence[RatingPeriodResult],
) -> None:
    result_users = {item.user_id for item in results}
    previous_users = {item.user_id for item in previous_results}
    games = Counter(user_id for item in inputs for user_id in (item.player1_id, item.player2_id))
    if not previous_users.issubset(result_users):
        raise RatingPublicationSnapshotConflictError
    if set(games).difference(result_users):
        raise RatingPublicationSnapshotConflictError
    if result_users.difference(previous_users | set(games)):
        raise RatingPublicationSnapshotConflictError
    for item in results:
        expected_games = games[item.user_id]
        if item.games_count != expected_games or item.is_active != (expected_games > 0):
            raise RatingPublicationSnapshotConflictError

"""Pure projection of session match history into participant statistics."""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from pingstats.modules.game_sessions.application.dto import GameSessionPlayerStatistics
from pingstats.modules.matches.application.dto import MatchView
from pingstats.modules.matches.domain import (
    InvalidMatchResultError,
    MatchContext,
    MatchStatus,
    ScoreValidator,
)
from pingstats.modules.users.domain import User


@dataclass(slots=True)
class _Totals:
    wins: int = 0
    losses: int = 0
    points_for: int = 0
    points_against: int = 0


def calculate_session_statistics(
    session_id: UUID,
    users: Sequence[User],
    matches: Sequence[MatchView],
) -> tuple[GameSessionPlayerStatistics, ...]:
    """Calculate an idempotent session-only leaderboard from persisted facts."""
    users_by_id = {user.id: user for user in users}
    totals = {user_id: _Totals() for user_id in users_by_id}

    for view in matches:
        match = view.match
        if (
            match.context is not MatchContext.GAME_SESSION
            or match.game_session_id != session_id
            or match.status is not MatchStatus.COMPLETED
            or match.winner_id is None
            or not view.sets
        ):
            continue

        participant_ids = {
            participant.position: participant.user_id for participant, _ in view.participants
        }
        if set(participant_ids) != {1, 2} or match.winner_id not in participant_ids.values():
            continue
        try:
            winner_position = ScoreValidator.match_winner(
                [item.score() for item in view.sets],
                points_to_win=match.points_to_win,
                win_by=match.win_by,
                sets_to_win=match.sets_to_win,
                require_complete=True,
            )
        except InvalidMatchResultError:
            continue
        if winner_position is None or participant_ids[winner_position] != match.winner_id:
            continue
        loser_id = next(
            user_id for user_id in participant_ids.values() if user_id != match.winner_id
        )
        for _, user in view.participants:
            users_by_id.setdefault(user.id, user)
            totals.setdefault(user.id, _Totals())

        player1_points = sum(item.player1_score for item in view.sets)
        player2_points = sum(item.player2_score for item in view.sets)
        first_id = participant_ids[1]
        second_id = participant_ids[2]
        totals[first_id].points_for += player1_points
        totals[first_id].points_against += player2_points
        totals[second_id].points_for += player2_points
        totals[second_id].points_against += player1_points
        totals[match.winner_id].wins += 1
        totals[loser_id].losses += 1

    result = tuple(
        GameSessionPlayerStatistics(
            user=users_by_id[user_id],
            wins=value.wins,
            losses=value.losses,
            points_for=value.points_for,
            points_against=value.points_against,
        )
        for user_id, value in totals.items()
    )
    return tuple(
        sorted(
            result,
            key=lambda item: (
                -item.wins,
                -item.point_difference,
                item.losses,
                str(item.user.id),
            ),
        )
    )

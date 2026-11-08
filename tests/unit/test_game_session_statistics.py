"""Session leaderboard projection tests."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pingstats.modules.game_sessions.application.statistics import (
    calculate_session_statistics,
)
from pingstats.modules.matches.application.dto import MatchView
from pingstats.modules.matches.domain import (
    Match,
    MatchContext,
    MatchParticipant,
    MatchSet,
    MatchStatus,
)
from pingstats.modules.users.domain import User

NOW = datetime(2026, 11, 8, 11, 32, tzinfo=UTC)


def user(value: int, name: str) -> User:
    return User(UUID(int=value), None, name, None, None, None, NOW, NOW, None)


def match_view(
    session_id: UUID,
    first: User,
    second: User,
    scores: list[tuple[int, int]],
    *,
    winner: User | None = None,
    status: MatchStatus = MatchStatus.COMPLETED,
    context: MatchContext = MatchContext.GAME_SESSION,
    completed_at: datetime | None = NOW,
) -> MatchView:
    match_id = uuid4()
    winner = winner or first
    winner_position = 1 if winner.id == first.id else 2
    sets_to_win = sum(
        1
        for left, right in scores
        if (left > right and winner_position == 1) or (right > left and winner_position == 2)
    )
    return MatchView(
        match=Match(
            id=match_id,
            context=context,
            status=status,
            created_by=first.id,
            points_to_win=11,
            win_by=2,
            sets_to_win=max(sets_to_win, 1),
            is_ranked=False,
            winner_id=winner.id if status is MatchStatus.COMPLETED else None,
            created_at=NOW,
            updated_at=NOW,
            started_at=NOW,
            completed_at=completed_at,
            cancelled_at=NOW if status is MatchStatus.CANCELLED else None,
            version=2,
            game_session_id=session_id if context is MatchContext.GAME_SESSION else None,
            sequence_number=1 if context is MatchContext.GAME_SESSION else None,
        ),
        participants=(
            (MatchParticipant(uuid4(), match_id, first.id, 1), first),
            (MatchParticipant(uuid4(), match_id, second.id, 2), second),
        ),
        sets=tuple(
            MatchSet(uuid4(), match_id, number, left, right, NOW)
            for number, (left, right) in enumerate(scores, start=1)
        ),
    )


def test_counts_wins_losses_and_all_points_across_matches() -> None:
    session_id = uuid4()
    alice, bob, idle = user(1, "Alice"), user(2, "Bob"), user(3, "Idle")
    matches = (
        match_view(session_id, alice, bob, [(11, 8), (9, 11), (11, 6)], winner=alice),
        match_view(session_id, bob, alice, [(11, 5)], winner=bob),
    )

    result = calculate_session_statistics(session_id, (alice, bob, idle), matches)
    by_user = {item.user.id: item for item in result}

    assert (by_user[alice.id].wins, by_user[alice.id].losses) == (1, 1)
    assert (by_user[alice.id].points_for, by_user[alice.id].points_against) == (36, 36)
    assert by_user[alice.id].point_difference == 0
    assert (by_user[bob.id].wins, by_user[bob.id].losses) == (1, 1)
    assert by_user[bob.id].point_difference == 0
    assert (by_user[idle.id].wins, by_user[idle.id].losses) == (0, 0)
    assert by_user[idle.id].point_difference == 0


def test_excludes_cancelled_active_incomplete_standalone_and_other_session_matches() -> None:
    session_id = uuid4()
    other_session_id = uuid4()
    alice, bob = user(1, "Alice"), user(2, "Bob")
    excluded = (
        match_view(session_id, alice, bob, [(11, 2)], status=MatchStatus.CANCELLED),
        match_view(session_id, alice, bob, [(11, 2)], status=MatchStatus.ACTIVE),
        match_view(session_id, alice, bob, [], completed_at=NOW),
        match_view(
            session_id,
            alice,
            bob,
            [(11, 2)],
            context=MatchContext.STANDALONE,
        ),
        match_view(other_session_id, alice, bob, [(11, 2)]),
    )

    result = calculate_session_statistics(session_id, (bob, alice), excluded)

    assert [item.user.id for item in result] == [alice.id, bob.id]
    assert [(item.wins, item.losses, item.point_difference) for item in result] == [
        (0, 0, 0),
        (0, 0, 0),
    ]


def test_sorting_uses_wins_difference_losses_then_stable_user_id() -> None:
    session_id = uuid4()
    first, second, third, fourth = (
        user(1, "First"),
        user(2, "Second"),
        user(3, "Third"),
        user(4, "Fourth"),
    )
    matches = (
        match_view(session_id, first, fourth, [(11, 1)]),
        match_view(session_id, second, third, [(11, 8)]),
        match_view(session_id, third, fourth, [(11, 8)]),
        match_view(session_id, second, fourth, [(11, 8)]),
        match_view(session_id, first, third, [(11, 1)]),
    )

    result = calculate_session_statistics(session_id, (fourth, third, second, first), matches)

    assert [item.user.id for item in result] == [first.id, second.id, third.id, fourth.id]
    assert result[0].wins == result[1].wins == 2
    assert result[0].point_difference > result[1].point_difference
    assert result[2].wins == 1


def test_projection_is_idempotent_and_keeps_historical_match_participant() -> None:
    session_id = uuid4()
    active, departed = user(1, "Active"), user(2, "Departed")
    view = match_view(session_id, departed, active, [(11, 4)])

    first = calculate_session_statistics(session_id, (active,), (view,))
    second = calculate_session_statistics(session_id, (active,), (view,))

    assert first == second
    assert [item.user.id for item in first] == [departed.id, active.id]
    assert first[0].point_difference == 7


def test_completed_match_with_unknown_historical_timestamp_still_counts() -> None:
    session_id = uuid4()
    alice, bob = user(1, "Alice"), user(2, "Bob")
    view = match_view(session_id, alice, bob, [(11, 7)], completed_at=None)

    result = calculate_session_statistics(session_id, (alice, bob), (view,))

    assert [(item.wins, item.losses) for item in result] == [(1, 0), (0, 1)]


def test_losses_break_a_tie_after_wins_and_point_difference() -> None:
    session_id = uuid4()
    fewer_losses, more_losses = user(1, "Fewer"), user(2, "More")
    opponents = [user(index, f"Opponent {index}") for index in range(3, 7)]
    matches = (
        match_view(session_id, fewer_losses, opponents[0], [(11, 9)]),
        match_view(session_id, more_losses, opponents[1], [(11, 1)]),
        match_view(session_id, opponents[2], more_losses, [(11, 3)]),
    )

    result = calculate_session_statistics(
        session_id, (fewer_losses, more_losses, *opponents), matches
    )
    tied_ids = {fewer_losses.id, more_losses.id}
    relevant = [item.user.id for item in result if item.user.id in tied_ids]

    assert relevant == [fewer_losses.id, more_losses.id]


def test_empty_session_has_empty_statistics() -> None:
    assert calculate_session_statistics(uuid4(), (), ()) == ()

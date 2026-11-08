"""Pure queue and loser-rotation strategy tests."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from pingstats.modules.game_sessions.domain import (
    GameSessionParticipant,
    GameSessionQueueState,
    InvalidQueueStateError,
    LoserRotatesStrategy,
    append_waiting,
    initialize_queue,
    remove_waiting,
    restore_queue_after_cancel,
    skip_current_players,
)


def participant(number: int) -> GameSessionParticipant:
    return GameSessionParticipant(
        id=UUID(int=number),
        session_id=UUID(int=100),
        user_id=UUID(int=1000 + number),
        joined_at=datetime(2026, 1, 1, tzinfo=UTC),
        left_at=None,
    )


@pytest.mark.parametrize("count", [2, 3, 5, 10])
def test_initialize_queue_is_stable_for_supported_group_sizes(count: int) -> None:
    participants = [participant(number) for number in reversed(range(1, count + 1))]

    state = initialize_queue(participants)

    assert state.current_pair == (UUID(int=1), UUID(int=2))
    assert state.waiting == tuple(UUID(int=number) for number in range(3, count + 1))


def test_winner_streak_rotates_waiting_players_in_order() -> None:
    strategy = LoserRotatesStrategy()
    winner = UUID(int=1)
    state = GameSessionQueueState(
        (winner, UUID(int=2)),
        (UUID(int=3), UUID(int=4), UUID(int=5)),
    )

    state = strategy.rotate(state, winner)
    assert state == GameSessionQueueState(
        (winner, UUID(int=3)),
        (UUID(int=4), UUID(int=5), UUID(int=2)),
    )
    state = strategy.rotate(state, winner)
    assert state == GameSessionQueueState(
        (winner, UUID(int=4)),
        (UUID(int=5), UUID(int=2), UUID(int=3)),
    )


def test_alternating_winners_and_two_player_empty_queue() -> None:
    strategy = LoserRotatesStrategy()
    state = GameSessionQueueState(
        (UUID(int=1), UUID(int=2)),
        (UUID(int=3),),
    )
    state = strategy.rotate(state, UUID(int=2))
    assert state == GameSessionQueueState(
        (UUID(int=2), UUID(int=3)),
        (UUID(int=1),),
    )
    state = strategy.rotate(state, UUID(int=3))
    assert state == GameSessionQueueState(
        (UUID(int=3), UUID(int=1)),
        (UUID(int=2),),
    )

    two_players = strategy.rotate(
        GameSessionQueueState((UUID(int=1), UUID(int=2)), ()),
        UUID(int=2),
    )
    assert two_players == GameSessionQueueState((UUID(int=2), UUID(int=1)), ())


def test_join_leave_and_cancel_restore_preserve_all_players() -> None:
    initial = GameSessionQueueState(
        (UUID(int=1), UUID(int=2)),
        (UUID(int=3), UUID(int=4)),
    )
    joined = append_waiting(initial, UUID(int=5))
    left = remove_waiting(joined, UUID(int=3))
    restored = restore_queue_after_cancel(
        left,
        (UUID(int=1), UUID(int=2), UUID(int=4), UUID(int=5)),
    )
    assert restored == GameSessionQueueState(
        (UUID(int=1), UUID(int=2)),
        (UUID(int=4), UUID(int=5)),
    )


def test_duplicate_or_inconsistent_queue_state_is_rejected() -> None:
    with pytest.raises(InvalidQueueStateError):
        GameSessionQueueState(
            (UUID(int=1), UUID(int=2)),
            (UUID(int=1),),
        ).validate()
    with pytest.raises(InvalidQueueStateError):
        append_waiting(GameSessionQueueState((UUID(int=1), UUID(int=2)), ()), UUID(int=2))
    with pytest.raises(InvalidQueueStateError):
        remove_waiting(GameSessionQueueState((UUID(int=1), UUID(int=2)), ()), UUID(int=1))
    with pytest.raises(InvalidQueueStateError):
        restore_queue_after_cancel(
            GameSessionQueueState((UUID(int=1), UUID(int=2)), ()),
            (UUID(int=1), UUID(int=2), UUID(int=3)),
        )


def test_skip_first_second_or_both_rotates_deterministically() -> None:
    state = GameSessionQueueState(
        (UUID(int=1), UUID(int=2)),
        (UUID(int=3), UUID(int=4)),
    )

    assert skip_current_players(state, (UUID(int=1),)) == GameSessionQueueState(
        (UUID(int=2), UUID(int=3)),
        (UUID(int=4), UUID(int=1)),
    )
    assert skip_current_players(state, (UUID(int=2),)) == GameSessionQueueState(
        (UUID(int=1), UUID(int=3)),
        (UUID(int=4), UUID(int=2)),
    )
    assert skip_current_players(state, state.current_pair) == GameSessionQueueState(
        (UUID(int=3), UUID(int=4)),
        (UUID(int=1), UUID(int=2)),
    )


def test_skip_with_two_or_three_players_never_duplicates_a_participant() -> None:
    two = GameSessionQueueState((UUID(int=1), UUID(int=2)), ())
    three = GameSessionQueueState((UUID(int=1), UUID(int=2)), (UUID(int=3),))

    assert skip_current_players(two, (UUID(int=1),)) == GameSessionQueueState(
        (UUID(int=2), UUID(int=1)), ()
    )
    assert skip_current_players(two, two.current_pair) == two
    assert skip_current_players(three, three.current_pair) == GameSessionQueueState(
        (UUID(int=3), UUID(int=1)), (UUID(int=2),)
    )


def test_skip_rejects_non_current_or_duplicate_targets() -> None:
    state = GameSessionQueueState(
        (UUID(int=1), UUID(int=2)),
        (UUID(int=3),),
    )
    with pytest.raises(InvalidQueueStateError):
        skip_current_players(state, ())
    with pytest.raises(InvalidQueueStateError):
        skip_current_players(state, (UUID(int=3),))
    with pytest.raises(InvalidQueueStateError):
        skip_current_players(state, (UUID(int=1), UUID(int=1)))

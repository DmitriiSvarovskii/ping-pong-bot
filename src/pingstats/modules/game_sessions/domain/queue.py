"""Pure queue rules for loser-rotates game sessions."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from uuid import UUID

from pingstats.modules.game_sessions.domain.entities import GameSessionParticipant


class InvalidQueueStateError(ValueError):
    """Raised when a queue state violates membership invariants."""


@dataclass(frozen=True, slots=True)
class GameSessionQueueState:
    """Current table pair and ordered waiting participant IDs."""

    current_pair: tuple[UUID, UUID]
    waiting: tuple[UUID, ...]

    def validate(self) -> None:
        all_participants = (*self.current_pair, *self.waiting)
        if len(set(all_participants)) != len(all_participants):
            raise InvalidQueueStateError("A participant cannot occur twice in queue state")


def initialize_queue(
    participants: Sequence[GameSessionParticipant],
) -> GameSessionQueueState:
    """Select the first pair by stable join order and enqueue the remainder."""
    ordered = sorted(participants, key=lambda item: (item.joined_at, item.id))
    if len(ordered) < 2:
        raise InvalidQueueStateError("At least two active participants are required")
    if any(not item.is_active for item in ordered):
        raise InvalidQueueStateError("Only active participants can initialize a queue")
    state = GameSessionQueueState(
        current_pair=(ordered[0].id, ordered[1].id),
        waiting=tuple(item.id for item in ordered[2:]),
    )
    state.validate()
    return state


class LoserRotatesStrategy:
    """Keep the winner at the table and rotate the loser to the waiting tail."""

    def rotate(self, state: GameSessionQueueState, winner_id: UUID) -> GameSessionQueueState:
        state.validate()
        if winner_id not in state.current_pair:
            raise InvalidQueueStateError("Winner must belong to the current pair")
        loser_id = (
            state.current_pair[1] if winner_id == state.current_pair[0] else state.current_pair[0]
        )
        if not state.waiting:
            rotated = GameSessionQueueState((winner_id, loser_id), ())
        else:
            rotated = GameSessionQueueState(
                (winner_id, state.waiting[0]),
                (*state.waiting[1:], loser_id),
            )
        rotated.validate()
        return rotated


def append_waiting(state: GameSessionQueueState, participant_id: UUID) -> GameSessionQueueState:
    state.validate()
    if participant_id in (*state.current_pair, *state.waiting):
        raise InvalidQueueStateError("Participant is already present in queue state")
    updated = GameSessionQueueState(state.current_pair, (*state.waiting, participant_id))
    updated.validate()
    return updated


def remove_waiting(state: GameSessionQueueState, participant_id: UUID) -> GameSessionQueueState:
    state.validate()
    if participant_id in state.current_pair:
        raise InvalidQueueStateError("A current player cannot leave the waiting queue")
    if participant_id not in state.waiting:
        raise InvalidQueueStateError("Participant is not waiting")
    updated = GameSessionQueueState(
        state.current_pair,
        tuple(item for item in state.waiting if item != participant_id),
    )
    updated.validate()
    return updated


def restore_queue_after_cancel(
    state: GameSessionQueueState,
    active_participant_ids: Iterable[UUID],
) -> GameSessionQueueState:
    """Validate and preserve an unplayed current game without inventing a result."""
    state.validate()
    active_ids = tuple(active_participant_ids)
    if len(set(active_ids)) != len(active_ids):
        raise InvalidQueueStateError("Active participant IDs must be unique")
    if set((*state.current_pair, *state.waiting)) != set(active_ids):
        raise InvalidQueueStateError("Queue state must contain every active participant once")
    return state

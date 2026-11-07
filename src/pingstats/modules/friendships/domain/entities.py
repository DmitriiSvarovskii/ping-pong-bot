"""Framework-independent friendship entities."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class FriendRequestStatus(StrEnum):
    """Lifecycle states of a friend request."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class FriendRequest:
    id: UUID
    sender_id: UUID
    receiver_id: UUID
    status: FriendRequestStatus
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None


@dataclass(frozen=True, slots=True)
class Friendship:
    id: UUID
    user_id: UUID
    friend_id: UUID
    created_at: datetime

    @staticmethod
    def canonical_pair(first_id: UUID, second_id: UUID) -> tuple[UUID, UUID]:
        """Return a deterministic storage order for a user pair."""
        return min(first_id, second_id), max(first_id, second_id)

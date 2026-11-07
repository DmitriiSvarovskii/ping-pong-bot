"""Persistence ports required by friendship use cases."""

from collections.abc import Sequence
from types import TracebackType
from typing import Protocol
from uuid import UUID

from pingstats.modules.friendships.domain import FriendRequest, FriendRequestStatus, Friendship
from pingstats.modules.users.domain import User

FriendRequestItem = tuple[FriendRequest, User]
FriendshipItem = tuple[Friendship, User]


class FriendshipRepository(Protocol):
    async def lock_user_pair(self, first_id: UUID, second_id: UUID) -> set[UUID]: ...

    async def get_request(
        self, request_id: UUID, *, for_update: bool = False
    ) -> FriendRequest | None: ...

    async def get_request_item(
        self, request_id: UUID, actor_id: UUID
    ) -> FriendRequestItem | None: ...

    async def create_pending_request(
        self, sender_id: UUID, receiver_id: UUID
    ) -> FriendRequest | None: ...

    async def resolve_request(
        self, request_id: UUID, status: FriendRequestStatus
    ) -> FriendRequest: ...

    async def friendship_exists(self, first_id: UUID, second_id: UUID) -> bool: ...

    async def create_friendship(self, first_id: UUID, second_id: UUID) -> Friendship | None: ...

    async def delete_friendship(self, first_id: UUID, second_id: UUID) -> bool: ...

    async def list_incoming(
        self, user_id: UUID, *, limit: int, offset: int
    ) -> tuple[Sequence[FriendRequestItem], int]: ...

    async def list_outgoing(
        self, user_id: UUID, *, limit: int, offset: int
    ) -> tuple[Sequence[FriendRequestItem], int]: ...

    async def list_friends(
        self, user_id: UUID, *, limit: int, offset: int
    ) -> tuple[Sequence[FriendshipItem], int]: ...


class FriendshipsUnitOfWork(Protocol):
    friendships: FriendshipRepository

    async def __aenter__(self) -> "FriendshipsUnitOfWork": ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...

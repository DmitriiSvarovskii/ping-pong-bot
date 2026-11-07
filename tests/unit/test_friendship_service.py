"""Friendship application-service tests."""

from dataclasses import replace
from datetime import UTC, datetime
from types import TracebackType
from uuid import UUID, uuid4

import pytest

from pingstats.modules.friendships.application.errors import (
    AlreadyFriendsError,
    FriendRequestPermissionError,
    FriendRequestStateError,
    FriendshipNotFoundError,
    PendingFriendRequestExistsError,
    SelfFriendRequestError,
)
from pingstats.modules.friendships.application.service import FriendshipService
from pingstats.modules.friendships.domain import FriendRequest, FriendRequestStatus, Friendship


class FakeFriendshipRepository:
    def __init__(self, users: set[UUID]) -> None:
        self.users = users
        self.requests: dict[UUID, FriendRequest] = {}
        self.friendships: dict[tuple[UUID, UUID], Friendship] = {}

    async def lock_user_pair(self, first_id: UUID, second_id: UUID) -> set[UUID]:
        return {user_id for user_id in (first_id, second_id) if user_id in self.users}

    async def get_request(
        self, request_id: UUID, *, for_update: bool = False
    ) -> FriendRequest | None:
        del for_update
        return self.requests.get(request_id)

    async def create_pending_request(
        self, sender_id: UUID, receiver_id: UUID
    ) -> FriendRequest | None:
        pair = Friendship.canonical_pair(sender_id, receiver_id)
        if any(
            Friendship.canonical_pair(item.sender_id, item.receiver_id) == pair
            and item.status is FriendRequestStatus.PENDING
            for item in self.requests.values()
        ):
            return None
        now = datetime.now(UTC)
        request = FriendRequest(
            id=uuid4(),
            sender_id=sender_id,
            receiver_id=receiver_id,
            status=FriendRequestStatus.PENDING,
            created_at=now,
            updated_at=now,
            resolved_at=None,
        )
        self.requests[request.id] = request
        return request

    async def resolve_request(self, request_id: UUID, status: FriendRequestStatus) -> FriendRequest:
        request = self.requests[request_id]
        now = datetime.now(UTC)
        request = replace(request, status=status, updated_at=now, resolved_at=now)
        self.requests[request_id] = request
        return request

    async def friendship_exists(self, first_id: UUID, second_id: UUID) -> bool:
        return Friendship.canonical_pair(first_id, second_id) in self.friendships

    async def create_friendship(self, first_id: UUID, second_id: UUID) -> Friendship | None:
        pair = Friendship.canonical_pair(first_id, second_id)
        if pair in self.friendships:
            return None
        friendship = Friendship(
            id=uuid4(), user_id=pair[0], friend_id=pair[1], created_at=datetime.now(UTC)
        )
        self.friendships[pair] = friendship
        return friendship

    async def delete_friendship(self, first_id: UUID, second_id: UUID) -> bool:
        return (
            self.friendships.pop(Friendship.canonical_pair(first_id, second_id), None) is not None
        )


class FakeUow:
    def __init__(self, repository: FakeFriendshipRepository) -> None:
        self.friendships = repository
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        pass


@pytest.fixture
def actors() -> tuple[UUID, UUID, UUID]:
    return uuid4(), uuid4(), uuid4()


@pytest.fixture
def service() -> FriendshipService:
    return FriendshipService()


async def test_send_reject_and_repeat_request(
    service: FriendshipService, actors: tuple[UUID, UUID, UUID]
) -> None:
    sender, receiver, _ = actors
    repository = FakeFriendshipRepository({sender, receiver})
    uow = FakeUow(repository)

    first = await service.send_request(uow, sender_id=sender, receiver_id=receiver)
    rejected = await service.reject_request(uow, actor_id=receiver, request_id=first.id)
    repeated = await service.send_request(uow, sender_id=receiver, receiver_id=sender)

    assert rejected.status is FriendRequestStatus.REJECTED
    assert repeated.status is FriendRequestStatus.PENDING
    assert len(repository.requests) == 2


async def test_rejects_self_duplicate_and_existing_friendship(
    service: FriendshipService, actors: tuple[UUID, UUID, UUID]
) -> None:
    sender, receiver, _ = actors
    repository = FakeFriendshipRepository({sender, receiver})
    uow = FakeUow(repository)

    with pytest.raises(SelfFriendRequestError):
        await service.send_request(uow, sender_id=sender, receiver_id=sender)

    await service.send_request(uow, sender_id=sender, receiver_id=receiver)
    with pytest.raises(PendingFriendRequestExistsError):
        await service.send_request(uow, sender_id=receiver, receiver_id=sender)

    request = next(iter(repository.requests.values()))
    await service.accept_request(uow, actor_id=receiver, request_id=request.id)
    with pytest.raises(AlreadyFriendsError):
        await service.send_request(uow, sender_id=sender, receiver_id=receiver)


async def test_accept_permissions_and_state_conflict(
    service: FriendshipService, actors: tuple[UUID, UUID, UUID]
) -> None:
    sender, receiver, outsider = actors
    repository = FakeFriendshipRepository({sender, receiver, outsider})
    uow = FakeUow(repository)
    request = await service.send_request(uow, sender_id=sender, receiver_id=receiver)

    with pytest.raises(FriendRequestPermissionError):
        await service.accept_request(uow, actor_id=outsider, request_id=request.id)

    accepted = await service.accept_request(uow, actor_id=receiver, request_id=request.id)
    with pytest.raises(FriendRequestStateError):
        await service.accept_request(uow, actor_id=receiver, request_id=request.id)

    assert accepted.status is FriendRequestStatus.ACCEPTED
    assert await repository.friendship_exists(sender, receiver)


async def test_reject_and_cancel_permissions(
    service: FriendshipService, actors: tuple[UUID, UUID, UUID]
) -> None:
    sender, receiver, outsider = actors
    repository = FakeFriendshipRepository({sender, receiver, outsider})
    uow = FakeUow(repository)
    request = await service.send_request(uow, sender_id=sender, receiver_id=receiver)

    with pytest.raises(FriendRequestPermissionError):
        await service.reject_request(uow, actor_id=sender, request_id=request.id)
    with pytest.raises(FriendRequestPermissionError):
        await service.cancel_request(uow, actor_id=outsider, request_id=request.id)

    cancelled = await service.cancel_request(uow, actor_id=sender, request_id=request.id)
    assert cancelled.status is FriendRequestStatus.CANCELLED


async def test_remove_friend_and_send_again(
    service: FriendshipService, actors: tuple[UUID, UUID, UUID]
) -> None:
    sender, receiver, outsider = actors
    repository = FakeFriendshipRepository({sender, receiver, outsider})
    uow = FakeUow(repository)
    request = await service.send_request(uow, sender_id=sender, receiver_id=receiver)
    await service.accept_request(uow, actor_id=receiver, request_id=request.id)

    with pytest.raises(FriendshipNotFoundError):
        await service.remove_friend(uow, actor_id=outsider, friend_id=sender)

    await service.remove_friend(uow, actor_id=sender, friend_id=receiver)
    repeated = await service.send_request(uow, sender_id=receiver, receiver_id=sender)

    assert repeated.status is FriendRequestStatus.PENDING
    assert not await repository.friendship_exists(sender, receiver)

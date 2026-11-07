"""Friend request and friendship use cases."""

from collections.abc import Sequence
from uuid import UUID

from pingstats.modules.friendships.application.errors import (
    AlreadyFriendsError,
    FriendRequestNotFoundError,
    FriendRequestPermissionError,
    FriendRequestStateError,
    FriendshipNotFoundError,
    FriendshipUserNotFoundError,
    PendingFriendRequestExistsError,
    SelfFriendRequestError,
)
from pingstats.modules.friendships.application.ports import (
    FriendRequestItem,
    FriendshipItem,
    FriendshipRepository,
    FriendshipsUnitOfWork,
)
from pingstats.modules.friendships.domain import FriendRequest, FriendRequestStatus


class FriendshipService:
    """Coordinate friendship rules inside explicit transaction boundaries."""

    async def send_request(
        self,
        uow: FriendshipsUnitOfWork,
        *,
        sender_id: UUID,
        receiver_id: UUID,
    ) -> FriendRequest:
        if sender_id == receiver_id:
            raise SelfFriendRequestError
        async with uow:
            users = await uow.friendships.lock_user_pair(sender_id, receiver_id)
            if users != {sender_id, receiver_id}:
                raise FriendshipUserNotFoundError
            if await uow.friendships.friendship_exists(sender_id, receiver_id):
                raise AlreadyFriendsError
            request = await uow.friendships.create_pending_request(sender_id, receiver_id)
            if request is None:
                raise PendingFriendRequestExistsError
            await uow.commit()
        return request

    async def accept_request(
        self,
        uow: FriendshipsUnitOfWork,
        *,
        actor_id: UUID,
        request_id: UUID,
    ) -> FriendRequest:
        async with uow:
            request = await uow.friendships.get_request(request_id)
            if request is None:
                raise FriendRequestNotFoundError
            await uow.friendships.lock_user_pair(request.sender_id, request.receiver_id)
            request = await self._locked_pending_request(uow.friendships, request_id)
            if request.receiver_id != actor_id:
                raise FriendRequestPermissionError("Only the receiver can accept this request")
            if await uow.friendships.friendship_exists(request.sender_id, request.receiver_id):
                raise AlreadyFriendsError
            friendship = await uow.friendships.create_friendship(
                request.sender_id, request.receiver_id
            )
            if friendship is None:
                raise AlreadyFriendsError
            request = await uow.friendships.resolve_request(
                request.id, FriendRequestStatus.ACCEPTED
            )
            await uow.commit()
        return request

    async def reject_request(
        self,
        uow: FriendshipsUnitOfWork,
        *,
        actor_id: UUID,
        request_id: UUID,
    ) -> FriendRequest:
        async with uow:
            request = await self._locked_pending_request(uow.friendships, request_id)
            if request.receiver_id != actor_id:
                raise FriendRequestPermissionError("Only the receiver can reject this request")
            request = await uow.friendships.resolve_request(
                request.id, FriendRequestStatus.REJECTED
            )
            await uow.commit()
        return request

    async def cancel_request(
        self,
        uow: FriendshipsUnitOfWork,
        *,
        actor_id: UUID,
        request_id: UUID,
    ) -> FriendRequest:
        async with uow:
            request = await self._locked_pending_request(uow.friendships, request_id)
            if request.sender_id != actor_id:
                raise FriendRequestPermissionError("Only the sender can cancel this request")
            request = await uow.friendships.resolve_request(
                request.id, FriendRequestStatus.CANCELLED
            )
            await uow.commit()
        return request

    async def remove_friend(
        self,
        uow: FriendshipsUnitOfWork,
        *,
        actor_id: UUID,
        friend_id: UUID,
    ) -> None:
        if actor_id == friend_id:
            raise FriendshipNotFoundError
        async with uow:
            users = await uow.friendships.lock_user_pair(actor_id, friend_id)
            if users != {actor_id, friend_id}:
                raise FriendshipUserNotFoundError
            if not await uow.friendships.delete_friendship(actor_id, friend_id):
                raise FriendshipNotFoundError
            await uow.commit()

    async def list_incoming(
        self,
        repository: FriendshipRepository,
        user_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[FriendRequestItem], int]:
        return await repository.list_incoming(user_id, limit=limit, offset=offset)

    async def list_outgoing(
        self,
        repository: FriendshipRepository,
        user_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[FriendRequestItem], int]:
        return await repository.list_outgoing(user_id, limit=limit, offset=offset)

    async def list_friends(
        self,
        repository: FriendshipRepository,
        user_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[FriendshipItem], int]:
        return await repository.list_friends(user_id, limit=limit, offset=offset)

    async def _locked_pending_request(
        self, repository: FriendshipRepository, request_id: UUID
    ) -> FriendRequest:
        request = await repository.get_request(request_id, for_update=True)
        if request is None:
            raise FriendRequestNotFoundError
        if request.status is not FriendRequestStatus.PENDING:
            raise FriendRequestStateError
        return request

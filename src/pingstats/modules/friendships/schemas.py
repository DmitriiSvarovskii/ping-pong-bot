"""HTTP schemas for friendships."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from pingstats.modules.friendships.application.ports import FriendRequestItem, FriendshipItem
from pingstats.modules.friendships.domain import FriendRequestStatus
from pingstats.modules.users.schemas import UserPublicResponse


class CreateFriendRequest(BaseModel):
    receiver_id: UUID


class FriendRequestResponse(BaseModel):
    id: UUID
    sender_id: UUID
    receiver_id: UUID
    status: FriendRequestStatus
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    user: UserPublicResponse

    @classmethod
    def from_item(cls, item: FriendRequestItem) -> "FriendRequestResponse":
        request, user = item
        return cls(
            id=request.id,
            sender_id=request.sender_id,
            receiver_id=request.receiver_id,
            status=request.status,
            created_at=request.created_at,
            updated_at=request.updated_at,
            resolved_at=request.resolved_at,
            user=UserPublicResponse.from_domain(user),
        )


class FriendRequestListResponse(BaseModel):
    items: list[FriendRequestResponse]
    total: int
    limit: int
    offset: int


class FriendshipResponse(BaseModel):
    id: UUID
    created_at: datetime
    user: UserPublicResponse

    @classmethod
    def from_item(cls, item: FriendshipItem) -> "FriendshipResponse":
        friendship, user = item
        return cls(
            id=friendship.id,
            created_at=friendship.created_at,
            user=UserPublicResponse.from_domain(user),
        )


class FriendshipListResponse(BaseModel):
    items: list[FriendshipResponse]
    total: int
    limit: int
    offset: int

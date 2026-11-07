"""HTTP schemas for users."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from pingstats.modules.users.domain import User


class UserPublicResponse(BaseModel):
    """Public fields visible to authenticated users."""

    id: UUID
    first_name: str
    last_name: str | None
    username: str | None
    photo_url: str | None
    created_at: datetime
    last_active_at: datetime | None

    @classmethod
    def from_domain(cls, user: User) -> "UserPublicResponse":
        return cls(
            id=user.id,
            first_name=user.first_name,
            last_name=user.last_name,
            username=user.username,
            photo_url=user.photo_url,
            created_at=user.created_at,
            last_active_at=user.last_active_at,
        )


class UserResponse(UserPublicResponse):
    """Current user's complete profile response."""

    telegram_id: int | None
    updated_at: datetime

    @classmethod
    def from_domain(cls, user: User) -> "UserResponse":
        return cls(
            id=user.id,
            telegram_id=user.telegram_id,
            first_name=user.first_name,
            last_name=user.last_name,
            username=user.username,
            photo_url=user.photo_url,
            created_at=user.created_at,
            updated_at=user.updated_at,
            last_active_at=user.last_active_at,
        )


class UserListResponse(BaseModel):
    """Paginated public user collection."""

    items: list[UserPublicResponse]
    total: int
    limit: int
    offset: int

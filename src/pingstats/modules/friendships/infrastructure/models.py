"""SQLAlchemy models for friend requests and friendships."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from pingstats.infrastructure.database.base import Base
from pingstats.modules.friendships.domain import FriendRequest, FriendRequestStatus, Friendship


def _enum_values(enum_type: type[FriendRequestStatus]) -> list[str]:
    return [item.value for item in enum_type]


friend_request_status_enum = Enum(
    FriendRequestStatus,
    name="friend_request_status",
    values_callable=_enum_values,
)


class FriendRequestModel(Base):
    """Persistent friend-request history."""

    __tablename__ = "friend_requests"
    __table_args__ = (
        CheckConstraint("sender_id <> receiver_id", name="different_users"),
        CheckConstraint(
            "(status = 'pending' AND resolved_at IS NULL) OR "
            "(status <> 'pending' AND resolved_at IS NOT NULL)",
            name="resolution_matches_status",
        ),
        Index(
            "uq_friend_requests_pending_pair",
            func.least(text("sender_id"), text("receiver_id")),
            func.greatest(text("sender_id"), text("receiver_id")),
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "ix_friend_requests_receiver_status_created",
            "receiver_id",
            "status",
            text("created_at DESC"),
        ),
        Index(
            "ix_friend_requests_sender_status_created",
            "sender_id",
            "status",
            text("created_at DESC"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    sender_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    receiver_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[FriendRequestStatus] = mapped_column(
        friend_request_status_enum,
        nullable=False,
        default=FriendRequestStatus.PENDING,
        server_default=FriendRequestStatus.PENDING.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def to_domain(self) -> FriendRequest:
        return FriendRequest(
            id=self.id,
            sender_id=self.sender_id,
            receiver_id=self.receiver_id,
            status=self.status,
            created_at=self.created_at,
            updated_at=self.updated_at,
            resolved_at=self.resolved_at,
        )


class FriendshipModel(Base):
    """One canonical row representing a mutual friendship."""

    __tablename__ = "friendships"
    __table_args__ = (
        CheckConstraint("user_id < friend_id", name="canonical_user_order"),
        UniqueConstraint("user_id", "friend_id", name="uq_friendships_user_friend"),
        Index("ix_friendships_user_id", "user_id"),
        Index("ix_friendships_friend_id", "friend_id"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    friend_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def to_domain(self) -> Friendship:
        return Friendship(
            id=self.id,
            user_id=self.user_id,
            friend_id=self.friend_id,
            created_at=self.created_at,
        )

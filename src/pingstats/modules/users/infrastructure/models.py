"""SQLAlchemy model for users."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, String, Text, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from pingstats.infrastructure.database.base import Base
from pingstats.modules.users.domain import User


class UserModel(Base):
    """Persistence representation of a registered user."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "last_active_at IS NULL OR last_active_at >= created_at",
            name="last_active_not_before_created",
        ),
        Index(
            "ix_users_username_lower",
            func.lower(text("username")),
            postgresql_where=text("username IS NOT NULL"),
        ),
        Index("ix_users_last_active_at_desc", text("last_active_at DESC")),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, unique=True)
    first_name: Mapped[str] = mapped_column(String(128), nullable=False)
    last_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    photo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def to_domain(self) -> User:
        """Map the ORM state to a framework-independent entity."""
        return User(
            id=self.id,
            telegram_id=self.telegram_id,
            first_name=self.first_name,
            last_name=self.last_name,
            username=self.username,
            photo_url=self.photo_url,
            created_at=self.created_at,
            updated_at=self.updated_at,
            last_active_at=self.last_active_at,
        )

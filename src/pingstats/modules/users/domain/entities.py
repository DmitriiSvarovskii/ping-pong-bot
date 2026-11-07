"""Framework-independent users domain entities."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class User:
    """Registered PingStats user."""

    id: UUID
    telegram_id: int | None
    first_name: str
    last_name: str | None
    username: str | None
    photo_url: str | None
    created_at: datetime
    updated_at: datetime
    last_active_at: datetime | None


@dataclass(frozen=True, slots=True)
class TelegramUserData:
    """Trusted Telegram profile data accepted by the future auth adapter."""

    telegram_id: int
    first_name: str
    last_name: str | None = None
    username: str | None = None
    photo_url: str | None = None
    last_active_at: datetime | None = None

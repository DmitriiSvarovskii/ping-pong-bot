"""Application contracts for game-session realtime notifications."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class SessionUpdatedEvent:
    """Minimal invalidation event emitted after a session mutation commits."""

    session_id: UUID
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class SessionsUpdatedEvent:
    """Invalidate the private session lists of the permitted users."""

    recipient_user_ids: frozenset[UUID]
    timestamp: datetime


class SessionUpdatePublisher(Protocol):
    """Publish committed session invalidations without exposing a transport."""

    async def publish(self, event: SessionUpdatedEvent) -> None: ...


class SessionsUpdatePublisher(Protocol):
    """Publish private list invalidations without exposing a transport."""

    async def publish(self, event: SessionsUpdatedEvent) -> None: ...


class NullSessionUpdatePublisher:
    """Default adapter for non-HTTP use cases and isolated service tests."""

    async def publish(self, event: SessionUpdatedEvent) -> None:
        del event


class NullSessionsUpdatePublisher:
    """Default list publisher for non-HTTP use cases and isolated tests."""

    async def publish(self, event: SessionsUpdatedEvent) -> None:
        del event

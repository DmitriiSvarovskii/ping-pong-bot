"""Single-process WebSocket authentication and connection infrastructure."""

import asyncio
import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from time import monotonic
from typing import Any, Protocol
from uuid import UUID

from fastapi import WebSocket
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pingstats.modules.game_sessions.application import GameSessionService
from pingstats.modules.game_sessions.application.realtime import (
    SessionsUpdatedEvent,
    SessionUpdatedEvent,
)
from pingstats.modules.game_sessions.infrastructure.repository import (
    SqlAlchemyGameSessionRepository,
)

WEBSOCKET_PROTOCOL = "pingstats.v1"
WEBSOCKET_TICKET_PROTOCOL_PREFIX = "pingstats.ticket."


class InvalidWebSocketTicketError(ValueError):
    """Raised for missing, expired, replayed, or incorrectly scoped tickets."""


class WebSocketTicketScope(StrEnum):
    SESSION = "session"
    SESSIONS_LIST = "sessions_list"


@dataclass(frozen=True, slots=True)
class WebSocketTicketClaims:
    user_id: UUID
    scope: WebSocketTicketScope
    session_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class _StoredTicket:
    claims: WebSocketTicketClaims
    expires_at: float


class WebSocketTicketStore:
    """Keep short-lived one-use opaque ticket digests in one application process."""

    def __init__(self, *, ttl_seconds: int, clock: Callable[[], float] = monotonic) -> None:
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._tickets: dict[str, _StoredTicket] = {}
        self._lock = asyncio.Lock()

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds

    async def issue(self, *, user_id: UUID, session_id: UUID) -> str:
        return await self._issue(
            WebSocketTicketClaims(
                user_id=user_id,
                scope=WebSocketTicketScope.SESSION,
                session_id=session_id,
            )
        )

    async def issue_sessions_list(self, *, user_id: UUID) -> str:
        return await self._issue(
            WebSocketTicketClaims(
                user_id=user_id,
                scope=WebSocketTicketScope.SESSIONS_LIST,
            )
        )

    async def _issue(self, claims: WebSocketTicketClaims) -> str:
        ticket = secrets.token_urlsafe(32)
        digest = self._digest(ticket)
        now = self._clock()
        async with self._lock:
            self._purge_expired(now)
            self._tickets[digest] = _StoredTicket(
                claims=claims,
                expires_at=now + self._ttl_seconds,
            )
        return ticket

    async def consume(self, ticket: str, *, session_id: UUID) -> WebSocketTicketClaims:
        stored = await self._consume(ticket)
        if (
            stored.claims.scope is not WebSocketTicketScope.SESSION
            or stored.claims.session_id != session_id
        ):
            raise InvalidWebSocketTicketError("Invalid WebSocket ticket")
        return stored.claims

    async def consume_sessions_list(self, ticket: str) -> WebSocketTicketClaims:
        stored = await self._consume(ticket)
        if stored.claims.scope is not WebSocketTicketScope.SESSIONS_LIST:
            raise InvalidWebSocketTicketError("Invalid WebSocket ticket")
        return stored.claims

    async def _consume(self, ticket: str) -> _StoredTicket:
        digest = self._digest(ticket)
        now = self._clock()
        async with self._lock:
            stored = self._tickets.pop(digest, None)
            self._purge_expired(now)
        if stored is None or stored.expires_at <= now:
            raise InvalidWebSocketTicketError("Invalid WebSocket ticket")
        return stored

    async def clear(self) -> None:
        async with self._lock:
            self._tickets.clear()

    async def count(self) -> int:
        async with self._lock:
            self._purge_expired(self._clock())
            return len(self._tickets)

    def _purge_expired(self, now: float) -> None:
        expired = [digest for digest, ticket in self._tickets.items() if ticket.expires_at <= now]
        for digest in expired:
            del self._tickets[digest]

    @staticmethod
    def _digest(ticket: str) -> str:
        return hashlib.sha256(ticket.encode("utf-8")).hexdigest()


class SessionAccessAuthorizer(Protocol):
    async def authorize(self, *, user_id: UUID, session_id: UUID) -> None: ...


class SqlAlchemyWebSocketSessionAuthorizer:
    """Authorize once with a short DB session that closes before WebSocket acceptance."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        repository_factory: Callable[[AsyncSession], Any] = SqlAlchemyGameSessionRepository,
        service: GameSessionService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._repository_factory = repository_factory
        self._service = service or GameSessionService()

    async def authorize(self, *, user_id: UUID, session_id: UUID) -> None:
        async with self._session_factory() as database_session:
            repository = self._repository_factory(database_session)
            await self._service.get_session(
                repository,
                actor_id=user_id,
                session_id=session_id,
            )


class WebSocketConnectionManager:
    """Isolate and fan out connections inside a single Uvicorn worker."""

    def __init__(self) -> None:
        self._connections: dict[UUID, dict[WebSocket, UUID]] = {}
        self._user_connections: dict[UUID, set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, *, session_id: UUID, user_id: UUID, websocket: WebSocket) -> None:
        await websocket.accept(subprotocol=WEBSOCKET_PROTOCOL)
        async with self._lock:
            self._connections.setdefault(session_id, {})[websocket] = user_id

    async def disconnect(self, *, session_id: UUID, websocket: WebSocket) -> None:
        async with self._lock:
            session_connections = self._connections.get(session_id)
            if session_connections is None:
                return
            session_connections.pop(websocket, None)
            if not session_connections:
                self._connections.pop(session_id, None)

    async def connect_user(self, *, user_id: UUID, websocket: WebSocket) -> None:
        await websocket.accept(subprotocol=WEBSOCKET_PROTOCOL)
        async with self._lock:
            self._user_connections.setdefault(user_id, set()).add(websocket)

    async def disconnect_user(self, *, user_id: UUID, websocket: WebSocket) -> None:
        async with self._lock:
            connections = self._user_connections.get(user_id)
            if connections is None:
                return
            connections.discard(websocket)
            if not connections:
                self._user_connections.pop(user_id, None)

    async def send(
        self, *, session_id: UUID, websocket: WebSocket, message: dict[str, Any]
    ) -> bool:
        try:
            await websocket.send_json(message)
        except Exception:
            await self.disconnect(session_id=session_id, websocket=websocket)
            return False
        return True

    async def broadcast(self, *, session_id: UUID, message: dict[str, Any]) -> int:
        async with self._lock:
            recipients = tuple(self._connections.get(session_id, {}))
        delivered = 0
        for websocket in recipients:
            if await self.send(session_id=session_id, websocket=websocket, message=message):
                delivered += 1
        return delivered

    async def send_user(
        self, *, user_id: UUID, websocket: WebSocket, message: dict[str, Any]
    ) -> bool:
        try:
            await websocket.send_json(message)
        except Exception:
            await self.disconnect_user(user_id=user_id, websocket=websocket)
            return False
        return True

    async def broadcast_users(self, *, user_ids: frozenset[UUID], message: dict[str, Any]) -> int:
        async with self._lock:
            recipients = tuple(
                (user_id, websocket)
                for user_id in user_ids
                for websocket in self._user_connections.get(user_id, ())
            )
        delivered = 0
        for user_id, websocket in recipients:
            if await self.send_user(user_id=user_id, websocket=websocket, message=message):
                delivered += 1
        return delivered

    async def connection_count(self, session_id: UUID | None = None) -> int:
        async with self._lock:
            if session_id is not None:
                return len(self._connections.get(session_id, {}))
            return sum(len(items) for items in self._connections.values())

    async def user_connection_count(self, user_id: UUID | None = None) -> int:
        async with self._lock:
            if user_id is not None:
                return len(self._user_connections.get(user_id, ()))
            return sum(len(items) for items in self._user_connections.values())

    async def close_all(self, *, code: int = 1012) -> None:
        async with self._lock:
            sockets = {
                websocket
                for session_connections in self._connections.values()
                for websocket in session_connections
            }
            sockets.update(
                websocket
                for user_connections in self._user_connections.values()
                for websocket in user_connections
            )
            self._connections.clear()
            self._user_connections.clear()
        for websocket in sockets:
            try:
                await websocket.close(code=code)
            except Exception:
                pass


class WebSocketSessionUpdatePublisher:
    """Publish application invalidations through the process-local manager."""

    def __init__(self, manager: WebSocketConnectionManager) -> None:
        self._manager = manager

    async def publish(self, event: SessionUpdatedEvent) -> None:
        await self._manager.broadcast(
            session_id=event.session_id,
            message=session_updated_message(event.session_id, timestamp=event.timestamp),
        )


class WebSocketSessionsUpdatePublisher:
    """Publish private list invalidations only to their authorized users."""

    def __init__(self, manager: WebSocketConnectionManager) -> None:
        self._manager = manager

    async def publish(self, event: SessionsUpdatedEvent) -> None:
        await self._manager.broadcast_users(
            user_ids=event.recipient_user_ids,
            message=sessions_updated_message(timestamp=event.timestamp),
        )


def extract_websocket_ticket(websocket: WebSocket) -> str | None:
    offered = {
        item.strip()
        for item in websocket.headers.get("sec-websocket-protocol", "").split(",")
        if item.strip()
    }
    if WEBSOCKET_PROTOCOL not in offered:
        return None
    tickets = [
        item.removeprefix(WEBSOCKET_TICKET_PROTOCOL_PREFIX)
        for item in offered
        if item.startswith(WEBSOCKET_TICKET_PROTOCOL_PREFIX)
    ]
    if len(tickets) != 1 or not tickets[0]:
        return None
    return tickets[0]


def session_updated_message(
    session_id: UUID, *, timestamp: datetime | None = None
) -> dict[str, str]:
    return {
        "type": "session.updated",
        "session_id": str(session_id),
        "timestamp": _iso_timestamp(timestamp or datetime.now(UTC)),
    }


def sessions_updated_message(*, timestamp: datetime | None = None) -> dict[str, str]:
    return {
        "type": "sessions.updated",
        "timestamp": _iso_timestamp(timestamp or datetime.now(UTC)),
    }


def heartbeat_message(session_id: UUID) -> dict[str, str]:
    return {
        "type": "heartbeat",
        "session_id": str(session_id),
        "timestamp": _iso_timestamp(datetime.now(UTC)),
    }


def sessions_heartbeat_message() -> dict[str, str]:
    return {
        "type": "heartbeat",
        "timestamp": _iso_timestamp(datetime.now(UTC)),
    }


def _iso_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

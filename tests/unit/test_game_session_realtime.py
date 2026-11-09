"""Unit tests for single-process game-session realtime infrastructure."""

from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import pytest

from pingstats.modules.game_sessions.application.realtime import (
    SessionsUpdatedEvent,
    SessionUpdatedEvent,
)
from pingstats.modules.game_sessions.realtime import (
    WEBSOCKET_PROTOCOL,
    InvalidWebSocketTicketError,
    SqlAlchemyWebSocketSessionAuthorizer,
    WebSocketConnectionManager,
    WebSocketSessionsUpdatePublisher,
    WebSocketSessionUpdatePublisher,
    WebSocketTicketStore,
    session_updated_message,
    sessions_updated_message,
)


async def test_ticket_is_scoped_expires_and_can_only_be_consumed_once() -> None:
    now = [100.0]
    store = WebSocketTicketStore(ttl_seconds=5, clock=lambda: now[0])
    user_id = uuid4()
    session_id = uuid4()
    ticket = await store.issue(user_id=user_id, session_id=session_id)

    claims = await store.consume(ticket, session_id=session_id)
    assert claims.user_id == user_id
    assert claims.session_id == session_id
    assert await store.count() == 0

    with pytest.raises(InvalidWebSocketTicketError):
        await store.consume(ticket, session_id=session_id)

    expired = await store.issue(user_id=user_id, session_id=session_id)
    now[0] += 6
    with pytest.raises(InvalidWebSocketTicketError):
        await store.consume(expired, session_id=session_id)

    wrong_scope = await store.issue(user_id=user_id, session_id=session_id)
    with pytest.raises(InvalidWebSocketTicketError):
        await store.consume(wrong_scope, session_id=uuid4())
    with pytest.raises(InvalidWebSocketTicketError):
        await store.consume(wrong_scope, session_id=session_id)

    list_ticket = await store.issue_sessions_list(user_id=user_id)
    list_claims = await store.consume_sessions_list(list_ticket)
    assert list_claims.user_id == user_id
    assert list_claims.session_id is None
    with pytest.raises(InvalidWebSocketTicketError):
        await store.consume_sessions_list(list_ticket)

    wrong_channel = await store.issue_sessions_list(user_id=user_id)
    with pytest.raises(InvalidWebSocketTicketError):
        await store.consume(wrong_channel, session_id=session_id)


async def test_manager_isolates_sessions_and_removes_only_failed_client() -> None:
    manager = WebSocketConnectionManager()
    session_a = uuid4()
    session_b = uuid4()
    healthy = FakeWebSocket()
    failed = FakeWebSocket(fail_send=True)
    isolated = FakeWebSocket()

    await manager.connect(session_id=session_a, user_id=uuid4(), websocket=cast(Any, healthy))
    await manager.connect(session_id=session_a, user_id=uuid4(), websocket=cast(Any, failed))
    await manager.connect(session_id=session_b, user_id=uuid4(), websocket=cast(Any, isolated))
    delivered = await manager.broadcast(
        session_id=session_a,
        message={"type": "session.updated"},
    )

    assert delivered == 1
    assert healthy.accepted_subprotocol == WEBSOCKET_PROTOCOL
    assert healthy.messages == [{"type": "session.updated"}]
    assert failed.messages == []
    assert isolated.messages == []
    assert await manager.connection_count(session_a) == 1
    assert await manager.connection_count(session_b) == 1

    await manager.disconnect(session_id=session_a, websocket=cast(Any, healthy))
    assert await manager.connection_count(session_a) == 0
    assert await manager.connection_count() == 1
    await manager.close_all()
    assert isolated.closed_with == 1012
    assert await manager.connection_count() == 0


async def test_publisher_fans_out_only_to_the_changed_session() -> None:
    manager = WebSocketConnectionManager()
    publisher = WebSocketSessionUpdatePublisher(manager)
    session_id = uuid4()
    other_session_id = uuid4()
    first = FakeWebSocket()
    second = FakeWebSocket()
    isolated = FakeWebSocket()
    timestamp = datetime(2026, 11, 9, tzinfo=UTC)
    await manager.connect(session_id=session_id, user_id=uuid4(), websocket=cast(Any, first))
    await manager.connect(session_id=session_id, user_id=uuid4(), websocket=cast(Any, second))
    await manager.connect(
        session_id=other_session_id,
        user_id=uuid4(),
        websocket=cast(Any, isolated),
    )

    await publisher.publish(SessionUpdatedEvent(session_id, timestamp))

    expected = session_updated_message(session_id, timestamp=timestamp)
    assert first.messages == second.messages == [expected]
    assert isolated.messages == []


async def test_publisher_is_a_noop_without_subscribers() -> None:
    publisher = WebSocketSessionUpdatePublisher(WebSocketConnectionManager())

    await publisher.publish(SessionUpdatedEvent(uuid4(), datetime.now(UTC)))


async def test_list_publisher_targets_users_and_never_mixes_session_connections() -> None:
    manager = WebSocketConnectionManager()
    publisher = WebSocketSessionsUpdatePublisher(manager)
    permitted = uuid4()
    outsider = uuid4()
    session_id = uuid4()
    first = FakeWebSocket()
    second = FakeWebSocket()
    hidden = FakeWebSocket()
    session_socket = FakeWebSocket()
    timestamp = datetime(2026, 11, 9, tzinfo=UTC)
    await manager.connect_user(user_id=permitted, websocket=cast(Any, first))
    await manager.connect_user(user_id=permitted, websocket=cast(Any, second))
    await manager.connect_user(user_id=outsider, websocket=cast(Any, hidden))
    await manager.connect(
        session_id=session_id,
        user_id=permitted,
        websocket=cast(Any, session_socket),
    )

    await publisher.publish(SessionsUpdatedEvent(frozenset({permitted}), timestamp))

    expected = sessions_updated_message(timestamp=timestamp)
    assert first.messages == second.messages == [expected]
    assert hidden.messages == []
    assert session_socket.messages == []
    assert await manager.user_connection_count(permitted) == 2


async def test_authorizer_closes_database_session_before_returning() -> None:
    factory = TrackingSessionFactory()
    service = AccessService(factory)
    authorizer = SqlAlchemyWebSocketSessionAuthorizer(
        cast(Any, factory),
        repository_factory=lambda session: session,
        service=cast(Any, service),
    )

    await authorizer.authorize(user_id=uuid4(), session_id=uuid4())

    assert service.authorized_while_open
    assert factory.open_sessions == 0
    assert factory.opened == factory.closed == 1


def test_session_updated_message_contains_only_invalidation_metadata() -> None:
    session_id = uuid4()
    timestamp = datetime(2026, 11, 8, 20, 0, tzinfo=UTC)

    assert session_updated_message(session_id, timestamp=timestamp) == {
        "type": "session.updated",
        "session_id": str(session_id),
        "timestamp": "2026-11-08T20:00:00Z",
    }


class FakeWebSocket:
    def __init__(self, *, fail_send: bool = False) -> None:
        self.fail_send = fail_send
        self.accepted_subprotocol: str | None = None
        self.messages: list[dict[str, object]] = []
        self.closed_with: int | None = None

    async def accept(self, *, subprotocol: str | None = None) -> None:
        self.accepted_subprotocol = subprotocol

    async def send_json(self, message: dict[str, object]) -> None:
        if self.fail_send:
            raise RuntimeError("socket failed")
        self.messages.append(message)

    async def close(self, *, code: int) -> None:
        self.closed_with = code


class TrackingSessionFactory:
    def __init__(self) -> None:
        self.open_sessions = 0
        self.opened = 0
        self.closed = 0

    def __call__(self) -> "TrackingSessionContext":
        return TrackingSessionContext(self)


class TrackingSessionContext:
    def __init__(self, factory: TrackingSessionFactory) -> None:
        self.factory = factory

    async def __aenter__(self) -> "TrackingSessionContext":
        self.factory.open_sessions += 1
        self.factory.opened += 1
        return self

    async def __aexit__(self, *args: object) -> None:
        self.factory.open_sessions -= 1
        self.factory.closed += 1


class AccessService:
    def __init__(self, factory: TrackingSessionFactory) -> None:
        self.factory = factory
        self.authorized_while_open = False

    async def get_session(self, repository: object, **kwargs: object) -> object:
        del repository, kwargs
        self.authorized_while_open = self.factory.open_sessions == 1
        return object()

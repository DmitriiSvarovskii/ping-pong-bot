"""WebSocket ticket and endpoint contract tests."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from pingstats.app import create_app
from pingstats.core.config import AppEnvironment, Settings
from pingstats.modules.auth.dependencies import get_current_user
from pingstats.modules.game_sessions.application.errors import (
    GameSessionNotFoundError,
    GameSessionPermissionError,
)
from pingstats.modules.game_sessions.dependencies import (
    get_game_session_repository,
    get_game_session_service,
)
from pingstats.modules.game_sessions.realtime import (
    WEBSOCKET_PROTOCOL,
    WEBSOCKET_TICKET_PROTOCOL_PREFIX,
    WebSocketTicketStore,
)
from pingstats.modules.users.domain import User

SECRET = "test-secret-that-is-at-least-32-characters-long"


def test_authorized_participant_connects_with_one_use_ticket_and_heartbeat() -> None:
    app, user, access = websocket_app(heartbeat_seconds=1)
    with TestClient(app) as client:
        app.state.websocket_session_authorizer = access
        ticket = issue_ticket(client, user.id, access.session_id)

        with client.websocket_connect(
            f"/api/v1/game-sessions/{access.session_id}/ws",
            subprotocols=[WEBSOCKET_PROTOCOL, f"{WEBSOCKET_TICKET_PROTOCOL_PREFIX}{ticket}"],
        ) as websocket:
            assert websocket.accepted_subprotocol == WEBSOCKET_PROTOCOL
            heartbeat = websocket.receive_json()
            assert heartbeat["type"] == "heartbeat"
            assert heartbeat["session_id"] == str(access.session_id)
            assert (
                client.portal.call(
                    app.state.websocket_connection_manager.connection_count,
                    access.session_id,
                )
                == 1
            )

        assert (
            client.portal.call(
                app.state.websocket_connection_manager.connection_count,
                access.session_id,
            )
            == 0
        )
    assert access.calls == [(user.id, access.session_id)]


def test_ticket_endpoint_requires_valid_access_jwt() -> None:
    settings = make_test_settings()
    app = create_app(settings)
    session_id = uuid4()
    with TestClient(app) as client:
        missing = client.post(f"/api/v1/game-sessions/{session_id}/ws-ticket")
        invalid = client.post(
            f"/api/v1/game-sessions/{session_id}/ws-ticket",
            headers={"Authorization": "Bearer invalid"},
        )

    assert missing.status_code == 401
    assert invalid.status_code == 401


def test_sessions_list_ticket_requires_jwt() -> None:
    app = create_app(make_test_settings())
    with TestClient(app) as client:
        missing = client.post("/api/v1/game-sessions/ws-ticket")
        invalid = client.post(
            "/api/v1/game-sessions/ws-ticket",
            headers={"Authorization": "Bearer invalid"},
        )

    assert missing.status_code == 401
    assert invalid.status_code == 401


def test_sessions_list_supports_multiple_connections_and_cleans_them_up() -> None:
    app, user, _ = websocket_app(heartbeat_seconds=1)
    with TestClient(app) as client:
        first_ticket = issue_list_ticket(client)
        second_ticket = issue_list_ticket(client)

        def protocols(ticket: str) -> list[str]:
            return [
                WEBSOCKET_PROTOCOL,
                f"{WEBSOCKET_TICKET_PROTOCOL_PREFIX}{ticket}",
            ]

        with client.websocket_connect(
            "/api/v1/game-sessions/ws", subprotocols=protocols(first_ticket)
        ) as first:
            with client.websocket_connect(
                "/api/v1/game-sessions/ws", subprotocols=protocols(second_ticket)
            ) as second:
                assert first.accepted_subprotocol == second.accepted_subprotocol
                assert (
                    client.portal.call(
                        app.state.websocket_connection_manager.user_connection_count,
                        user.id,
                    )
                    == 2
                )
                assert first.receive_json()["type"] == "heartbeat"
            assert (
                client.portal.call(
                    app.state.websocket_connection_manager.user_connection_count,
                    user.id,
                )
                == 1
            )
        assert (
            client.portal.call(
                app.state.websocket_connection_manager.user_connection_count,
                user.id,
            )
            == 0
        )


def test_sessions_list_rejects_missing_expired_and_replayed_ticket() -> None:
    now = [10.0]
    app, _, _ = websocket_app()
    app.state.websocket_ticket_store = WebSocketTicketStore(
        ttl_seconds=5,
        clock=lambda: now[0],
    )
    path = "/api/v1/game-sessions/ws"
    with TestClient(app) as client:
        assert_socket_rejected(client, path, [WEBSOCKET_PROTOCOL], 4401)
        expired = issue_list_ticket(client)
        now[0] += 6
        assert_socket_rejected(
            client,
            path,
            [WEBSOCKET_PROTOCOL, f"{WEBSOCKET_TICKET_PROTOCOL_PREFIX}{expired}"],
            4401,
        )
        fresh = issue_list_ticket(client)
        protocols = [WEBSOCKET_PROTOCOL, f"{WEBSOCKET_TICKET_PROTOCOL_PREFIX}{fresh}"]
        with client.websocket_connect(path, subprotocols=protocols):
            pass
        assert_socket_rejected(client, path, protocols, 4401)


def test_websocket_rejects_missing_and_invalid_ticket() -> None:
    app, _, access = websocket_app()
    with TestClient(app) as client:
        app.state.websocket_session_authorizer = access
        assert_socket_rejected(
            client,
            f"/api/v1/game-sessions/{access.session_id}/ws",
            [WEBSOCKET_PROTOCOL],
            4401,
        )
        assert_socket_rejected(
            client,
            f"/api/v1/game-sessions/{access.session_id}/ws",
            [WEBSOCKET_PROTOCOL, f"{WEBSOCKET_TICKET_PROTOCOL_PREFIX}invalid"],
            4401,
        )
    assert access.calls == []


def test_websocket_rejects_expired_and_replayed_ticket() -> None:
    now = [10.0]
    app, user, access = websocket_app()
    app.state.websocket_ticket_store = WebSocketTicketStore(
        ttl_seconds=5,
        clock=lambda: now[0],
    )
    path = f"/api/v1/game-sessions/{access.session_id}/ws"
    with TestClient(app) as client:
        app.state.websocket_session_authorizer = access
        expired = issue_ticket(client, user.id, access.session_id)
        now[0] += 6
        assert_socket_rejected(
            client,
            path,
            [WEBSOCKET_PROTOCOL, f"{WEBSOCKET_TICKET_PROTOCOL_PREFIX}{expired}"],
            4401,
        )

        fresh = issue_ticket(client, user.id, access.session_id)
        protocols = [WEBSOCKET_PROTOCOL, f"{WEBSOCKET_TICKET_PROTOCOL_PREFIX}{fresh}"]
        with client.websocket_connect(path, subprotocols=protocols):
            pass
        assert_socket_rejected(client, path, protocols, 4401)


def test_websocket_rechecks_closed_session_access_before_accepting() -> None:
    app, user, access = websocket_app()
    with TestClient(app) as client:
        app.state.websocket_session_authorizer = access
        ticket = issue_ticket(client, user.id, access.session_id)
        access.forbidden = True
        assert_socket_rejected(
            client,
            f"/api/v1/game-sessions/{access.session_id}/ws",
            [WEBSOCKET_PROTOCOL, f"{WEBSOCKET_TICKET_PROTOCOL_PREFIX}{ticket}"],
            4403,
        )
    assert access.calls == [(user.id, access.session_id)]


def test_websocket_rejects_a_session_removed_after_ticket_issue() -> None:
    app, user, access = websocket_app()
    with TestClient(app) as client:
        app.state.websocket_session_authorizer = access
        ticket = issue_ticket(client, user.id, access.session_id)
        access.not_found = True
        assert_socket_rejected(
            client,
            f"/api/v1/game-sessions/{access.session_id}/ws",
            [WEBSOCKET_PROTOCOL, f"{WEBSOCKET_TICKET_PROTOCOL_PREFIX}{ticket}"],
            4404,
        )


def test_ticket_endpoint_rejects_non_participant_without_storing_ticket() -> None:
    app, _, access = websocket_app()
    access.issue_forbidden = True
    with TestClient(app) as client:
        response = client.post(f"/api/v1/game-sessions/{access.session_id}/ws-ticket")

        assert response.status_code == 403
        assert client.portal.call(app.state.websocket_ticket_store.count) == 0


def websocket_app(*, heartbeat_seconds: int = 25):
    app = create_app(make_test_settings(heartbeat_seconds=heartbeat_seconds))
    user = make_user()
    access = FakeAccess(user_id=user.id)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_game_session_repository] = lambda: object()
    app.dependency_overrides[get_game_session_service] = lambda: access
    return app, user, access


def issue_ticket(client: TestClient, user_id: UUID, session_id: UUID) -> str:
    response = client.post(f"/api/v1/game-sessions/{session_id}/ws-ticket")
    assert response.status_code == 201
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["expires_in"] in {5, 30}
    ticket = response.json()["ticket"]
    assert ticket not in str(response.request.url)
    assert user_id
    return ticket


def issue_list_ticket(client: TestClient) -> str:
    response = client.post("/api/v1/game-sessions/ws-ticket")
    assert response.status_code == 201
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["expires_in"] in {5, 30}
    ticket = response.json()["ticket"]
    assert ticket not in str(response.request.url)
    return ticket


def assert_socket_rejected(
    client: TestClient,
    path: str,
    subprotocols: list[str],
    code: int,
) -> None:
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect(path, subprotocols=subprotocols):
            pass
    assert error.value.code == code


def make_test_settings(*, heartbeat_seconds: int = 25) -> Settings:
    return Settings(
        app_env=AppEnvironment.TESTING,
        jwt_secret_key=SECRET,
        websocket_heartbeat_interval_seconds=heartbeat_seconds,
        _env_file=None,
    )


def make_user() -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid4(),
        telegram_id=123,
        first_name="Realtime",
        last_name="Player",
        username="realtime_player",
        photo_url=None,
        created_at=now,
        updated_at=now,
        last_active_at=now,
    )


class FakeAccess:
    def __init__(self, *, user_id: UUID) -> None:
        self.user_id = user_id
        self.session_id = uuid4()
        self.forbidden = False
        self.not_found = False
        self.issue_forbidden = False
        self.calls: list[tuple[UUID, UUID]] = []

    async def get_session(self, repository: object, *, actor_id: UUID, session_id: UUID) -> object:
        del repository
        assert actor_id == self.user_id
        assert session_id == self.session_id
        if self.issue_forbidden:
            raise GameSessionPermissionError("Only session participants can subscribe")
        return object()

    async def authorize(self, *, user_id: UUID, session_id: UUID) -> None:
        self.calls.append((user_id, session_id))
        if self.not_found:
            raise GameSessionNotFoundError
        if self.forbidden:
            raise GameSessionPermissionError("Only session participants can subscribe")

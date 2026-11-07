"""Friendships HTTP contract tests."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient

from pingstats.app import create_app
from pingstats.core.config import AppEnvironment, Settings
from pingstats.modules.auth.dependencies import get_current_user
from pingstats.modules.friendships.dependencies import (
    get_friendship_repository,
    get_friendships_uow,
)
from pingstats.modules.friendships.domain import Friendship
from pingstats.modules.users.dependencies import get_user_repository
from pingstats.modules.users.domain import User

SECRET = "test-secret-that-is-at-least-32-characters-long"


def make_user(name: str = "Test") -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid4(),
        telegram_id=None,
        first_name=name,
        last_name="Player",
        username=name.lower(),
        photo_url=None,
        created_at=now,
        updated_at=now,
        last_active_at=None,
    )


class AuthRepository:
    async def get_by_id(self, user_id: UUID) -> User | None:
        del user_id
        return None


class ReadRepository:
    def __init__(self, current: User, friend: User) -> None:
        self.current = current
        self.friend = friend

    async def list_incoming(self, user_id: UUID, *, limit: int, offset: int):
        del user_id, limit, offset
        return (), 0

    async def list_outgoing(self, user_id: UUID, *, limit: int, offset: int):
        del user_id, limit, offset
        return (), 0

    async def list_friends(self, user_id: UUID, *, limit: int, offset: int):
        del user_id, limit, offset
        first_id, second_id = Friendship.canonical_pair(self.current.id, self.friend.id)
        friendship = Friendship(
            id=uuid4(),
            user_id=first_id,
            friend_id=second_id,
            created_at=datetime.now(UTC),
        )
        return ((friendship, self.friend),), 1


async def test_all_friendship_routes_require_jwt() -> None:
    app = create_app(
        Settings(app_env=AppEnvironment.TESTING, jwt_secret_key=SECRET, _env_file=None)
    )
    app.dependency_overrides[get_user_repository] = lambda: AuthRepository()
    app.dependency_overrides[get_friendships_uow] = lambda: object()
    app.dependency_overrides[get_friendship_repository] = lambda: object()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        responses = [
            await client.post("/api/v1/friends/requests", json={"receiver_id": str(uuid4())}),
            await client.get("/api/v1/friends/requests/incoming"),
            await client.get("/api/v1/friends/requests/outgoing"),
            await client.post(f"/api/v1/friends/requests/{uuid4()}/accept"),
            await client.post(f"/api/v1/friends/requests/{uuid4()}/reject"),
            await client.post(f"/api/v1/friends/requests/{uuid4()}/cancel"),
            await client.get("/api/v1/friends"),
            await client.delete(f"/api/v1/friends/{uuid4()}"),
        ]

    assert {response.status_code for response in responses} == {401}


async def test_friend_list_returns_user_and_validates_pagination() -> None:
    current = make_user("Alice")
    friend = make_user("Bob")
    app = create_app(
        Settings(app_env=AppEnvironment.TESTING, jwt_secret_key=SECRET, _env_file=None)
    )
    app.dependency_overrides[get_current_user] = lambda: current
    app.dependency_overrides[get_friendship_repository] = lambda: ReadRepository(current, friend)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/friends?limit=10&offset=0")
        invalid = await client.get("/api/v1/friends?limit=0")

    assert response.status_code == 200
    assert response.json()["items"][0]["user"]["id"] == str(friend.id)
    assert response.json()["total"] == 1
    assert invalid.status_code == 422


async def test_self_request_uses_error_envelope() -> None:
    current = make_user()
    app = create_app(
        Settings(app_env=AppEnvironment.TESTING, jwt_secret_key=SECRET, _env_file=None)
    )
    app.dependency_overrides[get_current_user] = lambda: current
    app.dependency_overrides[get_friendships_uow] = lambda: object()
    app.dependency_overrides[get_friendship_repository] = lambda: object()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/friends/requests", json={"receiver_id": str(current.id)}
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "friend_request_to_self"


def test_openapi_contains_all_friendship_routes() -> None:
    app = create_app(
        Settings(app_env=AppEnvironment.TESTING, jwt_secret_key=SECRET, _env_file=None)
    )
    paths = set(app.openapi()["paths"])

    assert {
        "/api/v1/friends/requests",
        "/api/v1/friends/requests/incoming",
        "/api/v1/friends/requests/outgoing",
        "/api/v1/friends/requests/{request_id}/accept",
        "/api/v1/friends/requests/{request_id}/reject",
        "/api/v1/friends/requests/{request_id}/cancel",
        "/api/v1/friends",
        "/api/v1/friends/{friend_id}",
    } <= paths

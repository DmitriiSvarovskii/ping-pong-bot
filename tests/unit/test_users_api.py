"""Protected users API tests."""

import asyncio
from datetime import UTC, datetime
from types import TracebackType
from uuid import UUID, uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from pingstats.app import create_app
from pingstats.core.config import AppEnvironment, Settings
from pingstats.modules.auth.application import TokenService
from pingstats.modules.users.dependencies import get_user_repository, get_users_uow
from pingstats.modules.users.domain import User

SECRET = "test-secret-that-is-at-least-32-characters-long"


def make_user(user_id: UUID | None = None, *, name: str = "Test") -> User:
    now = datetime.now(UTC)
    return User(
        id=user_id or uuid4(),
        telegram_id=None,
        first_name=name,
        last_name="Player",
        username=name.lower(),
        photo_url=None,
        created_at=now,
        updated_at=now,
        last_active_at=None,
    )


class ApiRepository:
    def __init__(self, users: list[User]) -> None:
        self.users = users

    async def get_by_id(self, user_id: UUID) -> User | None:
        return next((user for user in self.users if user.id == user_id), None)

    async def list_users(self, *, limit: int, offset: int):
        return self.users[offset : offset + limit], len(self.users)

    async def search(self, query: str, *, limit: int, offset: int):
        matches = [u for u in self.users if query.casefold() in u.first_name.casefold()]
        return matches[offset : offset + limit], len(matches)


class ConcurrentDevRepository:
    def __init__(self) -> None:
        self.users: dict[UUID, User] = {}
        self.lock = asyncio.Lock()

    async def get_or_create_dev_user(self, user_id: UUID) -> User:
        async with self.lock:
            user = self.users.get(user_id)
            if user is None:
                user = make_user(user_id, name="Test")
                self.users[user_id] = user
            return user


class ConcurrentDevUow:
    def __init__(self, repository: ConcurrentDevRepository) -> None:
        self.users = repository

    async def __aenter__(self):
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass


async def request_client(application: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=application), base_url="http://test")


async def test_protected_endpoint_rejects_missing_token() -> None:
    app = create_app(
        Settings(app_env=AppEnvironment.TESTING, jwt_secret_key=SECRET, _env_file=None)
    )
    app.dependency_overrides[get_user_repository] = lambda: ApiRepository([])

    async with await request_client(app) as client:
        response = await client.get("/api/v1/users/me")

    assert response.status_code == 401


async def test_user_endpoints_with_valid_token() -> None:
    current = make_user(name="Alice")
    other = make_user(name="Bob")
    repository = ApiRepository([current, other])
    settings = Settings(app_env=AppEnvironment.TESTING, jwt_secret_key=SECRET, _env_file=None)
    app = create_app(settings)
    app.dependency_overrides[get_user_repository] = lambda: repository
    token = TokenService(secret=SECRET, algorithm="HS256", expire_minutes=60).issue_access_token(
        current.id
    )
    headers = {"Authorization": f"Bearer {token}"}

    async with await request_client(app) as client:
        me = await client.get("/api/v1/users/me", headers=headers)
        listing = await client.get("/api/v1/users?limit=1&offset=1", headers=headers)
        search = await client.get("/api/v1/users/search?q=Bob", headers=headers)
        profile = await client.get(f"/api/v1/users/{other.id}", headers=headers)
        invalid_page = await client.get("/api/v1/users?limit=0", headers=headers)

    assert me.status_code == 200
    assert me.json()["id"] == str(current.id)
    assert listing.json()["items"][0]["id"] == str(other.id)
    assert listing.json()["total"] == 2
    assert search.json()["items"][0]["first_name"] == "Bob"
    assert profile.status_code == 200
    assert "telegram_id" not in profile.json()
    assert invalid_page.status_code == 422


async def test_token_for_missing_user_is_rejected() -> None:
    app = create_app(
        Settings(app_env=AppEnvironment.TESTING, jwt_secret_key=SECRET, _env_file=None)
    )
    app.dependency_overrides[get_user_repository] = lambda: ApiRepository([])
    token = TokenService(secret=SECRET, algorithm="HS256", expire_minutes=60).issue_access_token(
        uuid4()
    )

    async with await request_client(app) as client:
        response = await client.get(
            "/api/v1/users/me", headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 401


async def test_dev_login_is_concurrently_idempotent() -> None:
    user_id = uuid4()
    settings = Settings(
        app_env=AppEnvironment.DEVELOPMENT,
        jwt_secret_key=SECRET,
        dev_user_id=user_id,
        _env_file=None,
    )
    app = create_app(settings)
    repository = ConcurrentDevRepository()
    uow = ConcurrentDevUow(repository)
    app.dependency_overrides[get_users_uow] = lambda: uow

    async with await request_client(app) as client:
        responses = await asyncio.gather(*(client.post("/api/v1/auth/dev-login") for _ in range(8)))

    assert {response.status_code for response in responses} == {200}
    assert {response.json()["user"]["id"] for response in responses} == {str(user_id)}
    assert len(repository.users) == 1


async def test_dev_login_route_is_absent_outside_development() -> None:
    app = create_app(
        Settings(app_env=AppEnvironment.PRODUCTION, jwt_secret_key=SECRET, _env_file=None)
    )

    paths = set(app.openapi()["paths"])
    async with await request_client(app) as client:
        response = await client.post("/api/v1/auth/dev-login")

    assert "/api/v1/auth/dev-login" not in paths
    assert response.status_code == 404

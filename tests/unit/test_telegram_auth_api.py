"""Telegram Mini App authentication API tests."""

import hashlib
import hmac
import json
from dataclasses import replace
from datetime import UTC, datetime
from types import TracebackType
from urllib.parse import urlencode
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient

from pingstats.app import create_app
from pingstats.core.config import AppEnvironment, Settings
from pingstats.modules.auth.api import (
    REFRESH_COOKIE_NAME,
    REFRESH_CSRF_HEADER,
    REFRESH_CSRF_VALUE,
)
from pingstats.modules.users.dependencies import get_user_repository, get_users_uow
from pingstats.modules.users.domain import TelegramUserData, User

BOT_TOKEN = "123456789:test-bot-token-used-only-in-api-tests"
JWT_SECRET = "test-secret-that-is-at-least-32-characters-long"


def signed_init_data(
    *, telegram_id: int = 123456789, first_name: str = "Alice", username: str = "alice"
) -> str:
    values = {
        "auth_date": str(int(datetime.now(UTC).timestamp())),
        "query_id": "AAE2e2eQuery",
        "user": json.dumps(
            {"id": telegram_id, "first_name": first_name, "username": username},
            separators=(",", ":"),
        ),
    }
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


class FakeUserRepository:
    def __init__(self) -> None:
        self.users: dict[UUID, User] = {}

    async def get_by_id(self, user_id: UUID) -> User | None:
        return self.users.get(user_id)

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        return next((item for item in self.users.values() if item.telegram_id == telegram_id), None)

    async def upsert_by_telegram_id(self, data: TelegramUserData) -> User:
        now = datetime.now(UTC)
        existing = await self.get_by_telegram_id(data.telegram_id)
        if existing is None:
            existing = User(
                id=uuid4(),
                telegram_id=data.telegram_id,
                first_name=data.first_name,
                last_name=data.last_name,
                username=data.username,
                photo_url=data.photo_url,
                created_at=now,
                updated_at=now,
                last_active_at=data.last_active_at,
            )
        else:
            existing = replace(
                existing,
                first_name=data.first_name,
                last_name=data.last_name,
                username=data.username,
                photo_url=data.photo_url,
                updated_at=now,
                last_active_at=data.last_active_at,
            )
        self.users[existing.id] = existing
        return existing


class FakeUsersUow:
    def __init__(self, repository: FakeUserRepository) -> None:
        self.users = repository

    async def __aenter__(self) -> "FakeUsersUow":
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


def make_app(
    repository: FakeUserRepository, *, bot_token: str = BOT_TOKEN, env=AppEnvironment.TESTING
):
    app = create_app(
        Settings(
            app_env=env,
            jwt_secret_key=JWT_SECRET,
            telegram_bot_token=bot_token,
            telegram_init_data_max_age_seconds=300,
            _env_file=None,
        )
    )
    app.dependency_overrides[get_users_uow] = lambda: FakeUsersUow(repository)
    app.dependency_overrides[get_user_repository] = lambda: repository
    return app


async def test_valid_login_syncs_profile_and_issues_compatible_jwt() -> None:
    repository = FakeUserRepository()
    app = make_app(repository)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post("/api/v1/auth/telegram", json={"init_data": signed_init_data()})
        me = await client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        )

    assert login.status_code == 200
    assert set(login.json()) == {"access_token", "token_type", "expires_in"}
    assert login.json()["token_type"] == "bearer"
    assert login.json()["expires_in"] == 3600
    assert "HttpOnly" in login.headers["set-cookie"]
    assert "SameSite=strict" in login.headers["set-cookie"]
    assert "Path=/api/v1/auth" in login.headers["set-cookie"]
    assert "Max-Age=2592000" in login.headers["set-cookie"]
    assert "Secure" not in login.headers["set-cookie"]
    assert me.status_code == 200
    assert me.json()["telegram_id"] == 123456789
    assert me.json()["username"] == "alice"


async def test_repeat_login_updates_profile_without_duplicate_user() -> None:
    repository = FakeUserRepository()
    app = make_app(repository)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/auth/telegram", json={"init_data": signed_init_data()})
        first_user_id = next(iter(repository.users))
        second = await client.post(
            "/api/v1/auth/telegram",
            json={"init_data": signed_init_data(first_name="Alicia", username="new_username")},
        )

    assert first.status_code == second.status_code == 200
    assert len(repository.users) == 1
    assert next(iter(repository.users)) == first_user_id
    assert repository.users[first_user_id].first_name == "Alicia"
    assert repository.users[first_user_id].username == "new_username"


async def test_refresh_cookie_renews_access_without_reusing_init_data() -> None:
    repository = FakeUserRepository()
    app = make_app(repository)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post("/api/v1/auth/telegram", json={"init_data": signed_init_data()})
        refreshed = await client.post(
            "/api/v1/auth/refresh",
            headers={REFRESH_CSRF_HEADER: REFRESH_CSRF_VALUE},
        )
        me = await client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {refreshed.json()['access_token']}"},
        )

    assert REFRESH_COOKIE_NAME in login.cookies
    assert refreshed.status_code == 200
    assert refreshed.json()["expires_in"] == 3600
    assert me.status_code == 200


async def test_refresh_rejects_missing_or_wrong_token_type() -> None:
    repository = FakeUserRepository()
    app = make_app(repository)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post("/api/v1/auth/telegram", json={"init_data": signed_init_data()})
        access_token = login.json()["access_token"]
        client.cookies.clear()
        missing = await client.post(
            "/api/v1/auth/refresh",
            headers={REFRESH_CSRF_HEADER: REFRESH_CSRF_VALUE},
        )
        client.cookies.set(REFRESH_COOKIE_NAME, access_token, path="/api/v1/auth")
        wrong_type = await client.post(
            "/api/v1/auth/refresh",
            headers={REFRESH_CSRF_HEADER: REFRESH_CSRF_VALUE},
        )

    assert missing.status_code == wrong_type.status_code == 401
    assert missing.json()["error"]["code"] == "refresh_authentication_invalid"


async def test_refresh_rejects_cookie_request_without_csrf_header() -> None:
    repository = FakeUserRepository()
    app = make_app(repository)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/auth/telegram", json={"init_data": signed_init_data()})
        rejected = await client.post("/api/v1/auth/refresh")

    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "refresh_csrf_rejected"


async def test_invalid_init_data_and_missing_configuration_are_safe() -> None:
    repository = FakeUserRepository()
    configured = make_app(repository)
    unavailable = make_app(FakeUserRepository(), bot_token="")
    tampered = signed_init_data().replace("alice", "mallory", 1)

    async with AsyncClient(
        transport=ASGITransport(app=configured), base_url="http://test"
    ) as client:
        invalid = await client.post("/api/v1/auth/telegram", json={"init_data": tampered})
        validation = await client.post("/api/v1/auth/telegram", json={"init_data": ""})
    async with AsyncClient(
        transport=ASGITransport(app=unavailable), base_url="http://test"
    ) as client:
        missing_token = await client.post(
            "/api/v1/auth/telegram", json={"init_data": signed_init_data()}
        )

    assert invalid.status_code == 401
    assert invalid.json()["error"] == {
        "code": "telegram_init_data_invalid",
        "message": "Telegram initData is invalid",
        "details": {},
    }
    assert validation.status_code == 422
    assert missing_token.status_code == 503
    assert "token" not in missing_token.text.lower()


async def test_telegram_route_exists_in_production_while_dev_login_does_not() -> None:
    app = make_app(FakeUserRepository(), env=AppEnvironment.PRODUCTION)
    paths = set(app.openapi()["paths"])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        dev = await client.post("/api/v1/auth/dev-login")
        telegram = await client.post(
            "/api/v1/auth/telegram",
            json={"init_data": signed_init_data()},
        )

    assert "/api/v1/auth/telegram" in paths
    assert "/api/v1/auth/dev-login" not in paths
    assert dev.status_code == 404
    assert "Secure" in telegram.headers["set-cookie"]

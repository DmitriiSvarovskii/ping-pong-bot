"""PostgreSQL-backed Telegram authentication and first-login race tests."""

import asyncio
import hashlib
import hmac
import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlencode
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pingstats.app import create_app
from pingstats.core.config import AppEnvironment, Settings
from pingstats.infrastructure.database.engine import Database
from pingstats.modules.auth.api import REFRESH_CSRF_HEADER, REFRESH_CSRF_VALUE
from pingstats.modules.auth.application import TokenService
from pingstats.modules.users.infrastructure.models import UserModel

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="requires an explicitly selected migrated local PostgreSQL database",
)

BOT_TOKEN = "123456789:postgres-test-bot-token"
JWT_SECRET = "postgres-test-secret-that-is-at-least-32-characters"


def signed_init_data(
    telegram_id: int,
    *,
    first_name: str = "Integration",
    username: str = "telegram_integration",
) -> str:
    values = {
        "auth_date": str(int(datetime.now(UTC).timestamp())),
        "query_id": f"query-{uuid4().hex}",
        "user": json.dumps(
            {
                "id": telegram_id,
                "first_name": first_name,
                "username": username,
                "photo_url": "https://example.test/photo.jpg",
            },
            separators=(",", ":"),
        ),
    }
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


@dataclass(slots=True)
class TelegramAuthEnvironment:
    client: AsyncClient
    session_factory: async_sessionmaker[AsyncSession]
    telegram_ids: list[int]


@pytest.fixture
async def telegram_auth_environment() -> AsyncIterator[TelegramAuthEnvironment]:
    database_settings = Settings()
    engine = create_async_engine(database_settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    app = create_app(
        Settings(
            app_env=AppEnvironment.TESTING,
            app_debug=False,
            jwt_secret_key=JWT_SECRET,
            telegram_bot_token=BOT_TOKEN,
            telegram_init_data_max_age_seconds=300,
            _env_file=None,
        )
    )
    app.state.database = Database(engine=engine, session_factory=session_factory)
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    environment = TelegramAuthEnvironment(client, session_factory, [])
    try:
        yield environment
    finally:
        await client.aclose()
        async with session_factory() as session:
            await session.execute(
                delete(UserModel).where(UserModel.telegram_id.in_(environment.telegram_ids))
            )
            await session.commit()
        await engine.dispose()


def unique_telegram_id() -> int:
    return 1_000_000_000_000 + int(uuid4().hex[:10], 16)


async def test_telegram_login_creates_updates_and_reuses_user(
    telegram_auth_environment: TelegramAuthEnvironment,
) -> None:
    env = telegram_auth_environment
    telegram_id = unique_telegram_id()
    env.telegram_ids.append(telegram_id)

    first = await env.client.post(
        "/api/v1/auth/telegram", json={"init_data": signed_init_data(telegram_id)}
    )
    assert first.status_code == 200, first.text
    first_token = first.json()["access_token"]
    first_user_id = TokenService(
        secret=JWT_SECRET, algorithm="HS256", expire_minutes=60
    ).verify_access_token(first_token)

    updated = await env.client.post(
        "/api/v1/auth/telegram",
        json={
            "init_data": signed_init_data(
                telegram_id, first_name="Updated", username="updated_username"
            )
        },
    )
    assert updated.status_code == 200, updated.text
    second_user_id = TokenService(
        secret=JWT_SECRET, algorithm="HS256", expire_minutes=60
    ).verify_access_token(updated.json()["access_token"])
    refreshed = await env.client.post(
        "/api/v1/auth/refresh",
        headers={REFRESH_CSRF_HEADER: REFRESH_CSRF_VALUE},
    )
    assert refreshed.status_code == 200, refreshed.text
    me = await env.client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {refreshed.json()['access_token']}"},
    )

    assert first_user_id == second_user_id
    assert me.status_code == 200
    assert me.json()["first_name"] == "Updated"
    assert me.json()["username"] == "updated_username"
    async with env.session_factory() as session:
        users = (
            await session.scalars(select(UserModel).where(UserModel.telegram_id == telegram_id))
        ).all()
    assert len(users) == 1
    assert users[0].id == first_user_id


@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_CONCURRENCY") != "1",
    reason="requires explicit permission for committed concurrent PostgreSQL rows",
)
async def test_concurrent_first_telegram_login_creates_one_user(
    telegram_auth_environment: TelegramAuthEnvironment,
) -> None:
    env = telegram_auth_environment
    telegram_id = unique_telegram_id()
    env.telegram_ids.append(telegram_id)
    init_data = signed_init_data(telegram_id)

    responses = await asyncio.gather(
        *(env.client.post("/api/v1/auth/telegram", json={"init_data": init_data}) for _ in range(8))
    )

    assert {response.status_code for response in responses} == {200}
    token_service = TokenService(secret=JWT_SECRET, algorithm="HS256", expire_minutes=60)
    user_ids = {
        token_service.verify_access_token(response.json()["access_token"]) for response in responses
    }
    assert len(user_ids) == 1
    async with env.session_factory() as session:
        count = await session.scalar(
            select(func.count()).select_from(UserModel).where(UserModel.telegram_id == telegram_id)
        )
        stored_id = await session.scalar(
            select(UserModel.id).where(UserModel.telegram_id == telegram_id)
        )
    assert count == 1
    assert stored_id == next(iter(user_ids))

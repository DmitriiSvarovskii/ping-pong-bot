"""Development database health endpoint tests."""

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from pingstats.app import create_app
from pingstats.core.config import AppEnvironment, Settings
from pingstats.infrastructure.database.session import get_db_session


async def request_with_session(session: AsyncMock) -> tuple[int, dict]:
    settings = Settings(app_env=AppEnvironment.DEVELOPMENT, _env_file=None)
    application = create_app(settings)

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield session

    application.dependency_overrides[get_db_session] = override_session
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/health/db")
    return response.status_code, response.json()


async def test_database_health_executes_query() -> None:
    session = AsyncMock(spec=AsyncSession)

    status_code, payload = await request_with_session(session)

    assert status_code == 200
    assert payload == {"status": "ok", "database": "connected"}
    session.execute.assert_awaited_once()
    statement = session.execute.await_args.args[0]
    assert str(statement) == "SELECT 1"


async def test_database_health_returns_503_safely() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = SQLAlchemyError("sensitive internal details")

    status_code, payload = await request_with_session(session)

    assert status_code == 503
    assert payload == {"detail": "Database is unavailable"}
    assert "sensitive" not in str(payload)


async def test_database_health_is_absent_in_production() -> None:
    settings = Settings(app_env=AppEnvironment.PRODUCTION, _env_file=None)
    application = create_app(settings)
    transport = ASGITransport(app=application)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/health/db")

    assert response.status_code == 404

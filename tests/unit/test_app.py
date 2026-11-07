"""Application factory tests."""

from unittest.mock import AsyncMock, patch

from fastapi import FastAPI

from pingstats import __version__
from pingstats.app import create_app
from pingstats.core.config import Settings


def test_create_app(settings: Settings) -> None:
    application = create_app(settings)

    assert isinstance(application, FastAPI)
    assert application.title == "PingStats"
    assert application.version == __version__
    assert application.docs_url == "/docs"
    assert application.openapi_url == "/openapi.json"


async def test_lifespan_disposes_database(settings: Settings) -> None:
    database = AsyncMock()

    with patch(
        "pingstats.app.factory.Database.from_settings",
        return_value=database,
    ):
        application = create_app(settings)
        async with application.router.lifespan_context(application):
            assert application.state.database is database

    database.dispose.assert_awaited_once()

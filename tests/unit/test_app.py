"""Application factory tests."""

import asyncio
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI

from pingstats import __version__
from pingstats.app import create_app
from pingstats.core.config import AppEnvironment, Settings


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


async def test_lifespan_runs_and_stops_rating_period_scheduler(settings: Settings) -> None:
    database = AsyncMock()
    started = asyncio.Event()
    stopped = asyncio.Event()

    class Scheduler:
        async def run_forever(self) -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

    scheduler = Scheduler()
    development_settings = settings.model_copy(update={"app_env": AppEnvironment.DEVELOPMENT})
    with (
        patch("pingstats.app.factory.Database.from_settings", return_value=database),
        patch("pingstats.app.factory.RatingPeriodScheduler", return_value=scheduler),
    ):
        application = create_app(development_settings)
        async with application.router.lifespan_context(application):
            await started.wait()
            assert application.state.rating_period_scheduler is scheduler

    assert stopped.is_set()
    database.dispose.assert_awaited_once()

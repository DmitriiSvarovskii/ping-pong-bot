"""Opt-in destructive round-trip check for the ratings migration."""

import asyncio
import os

import pytest
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from pingstats.core.config import Settings

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_MIGRATION") != "1",
    reason="requires permission to downgrade and restore the selected local PostgreSQL database",
)

RATING_TABLES = {
    "ratings",
    "rating_periods",
    "rating_period_match_inputs",
    "rating_period_results",
    "rating_rebuild_jobs",
    "rating_rebuild_periods",
    "rating_recalculation_requests",
}


async def _table_names() -> set[str]:
    engine = create_async_engine(Settings().database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            return set(await connection.run_sync(lambda sync: inspect(sync).get_table_names()))
    finally:
        await engine.dispose()


def test_rating_migration_upgrade_and_downgrade() -> None:
    if not Settings().db_name.endswith("_migration_check"):
        pytest.skip("migration round-trip is allowed only on a dedicated *_migration_check DB")
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    try:
        assert RATING_TABLES <= asyncio.run(_table_names())
        command.downgrade(config, "20261107_0007")
        assert RATING_TABLES.isdisjoint(asyncio.run(_table_names()))
    finally:
        command.upgrade(config, "head")
    assert RATING_TABLES <= asyncio.run(_table_names())

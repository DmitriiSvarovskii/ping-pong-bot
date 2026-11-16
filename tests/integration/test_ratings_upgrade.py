"""Upgrade-only production-head migration check in an isolated temporary database."""

import asyncio
import os
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from pingstats.core.config import Settings

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_UPGRADE_TEST") != "1",
    reason="requires CREATE DATABASE privilege for an isolated temporary database",
)

PRODUCTION_HEAD = "20261107_0007"
CURRENT_HEAD = "20261115_0011"
RATING_TABLES = {
    "ratings",
    "rating_periods",
    "rating_period_match_inputs",
    "rating_period_results",
    "rating_rebuild_jobs",
    "rating_rebuild_periods",
    "rating_recalculation_requests",
}


async def _create_database(name: str) -> None:
    settings = Settings()
    engine = create_async_engine(
        settings.database_url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
    )
    try:
        async with engine.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{name}" TEMPLATE template0'))
    finally:
        await engine.dispose()


async def _drop_database(name: str) -> None:
    settings = Settings()
    engine = create_async_engine(
        settings.database_url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
    )
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    finally:
        await engine.dispose()


async def _schema_state(name: str) -> tuple[str, set[str]]:
    settings = Settings()
    engine = create_async_engine(settings.database_url.set(database=name))
    try:
        async with engine.connect() as connection:
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            tables = set(await connection.run_sync(lambda sync: inspect(sync).get_table_names()))
        return str(revision), tables
    finally:
        await engine.dispose()


def _alembic(name: str, *arguments: str) -> None:
    environment = os.environ.copy()
    environment["DB_NAME"] = name
    subprocess.run(
        (sys.executable, "-m", "alembic", *arguments),
        check=True,
        cwd=os.getcwd(),
        env=environment,
        capture_output=True,
        text=True,
    )


def test_upgrade_from_production_head_in_isolated_database() -> None:
    name = f"pingstats_upgrade_{uuid4().hex}"
    asyncio.run(_create_database(name))
    try:
        _alembic(name, "upgrade", PRODUCTION_HEAD)
        revision, tables = asyncio.run(_schema_state(name))
        assert revision == PRODUCTION_HEAD
        assert RATING_TABLES.isdisjoint(tables)

        _alembic(name, "upgrade", "head")
        revision, tables = asyncio.run(_schema_state(name))
        assert revision == CURRENT_HEAD
        assert RATING_TABLES <= tables
        _alembic(name, "check")
    finally:
        asyncio.run(_drop_database(name))

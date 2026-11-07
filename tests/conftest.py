"""Shared test fixtures."""

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from pingstats.app import create_app
from pingstats.core.config import AppEnvironment, Settings


@pytest.fixture
def settings() -> Settings:
    """Provide deterministic settings isolated from the developer's .env."""
    return Settings(app_env=AppEnvironment.TESTING, _env_file=None)


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    """Build an application for each test."""
    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Call the ASGI app without binding a network port."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client

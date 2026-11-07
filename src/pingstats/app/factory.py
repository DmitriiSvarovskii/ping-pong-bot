"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from pingstats import __version__
from pingstats.api.database_health import development_router
from pingstats.api.router import api_router
from pingstats.core.config import AppEnvironment, Settings, get_settings
from pingstats.core.exceptions import ApplicationError, application_error_handler
from pingstats.core.logging import configure_logging
from pingstats.infrastructure.database.engine import Database
from pingstats.modules.auth.api import development_auth_router, telegram_auth_router
from pingstats.modules.friendships.api import friends_router
from pingstats.modules.game_sessions.api import game_sessions_router
from pingstats.modules.matches.api import matches_router
from pingstats.modules.users.api import users_router


def create_lifespan(settings: Settings):
    """Build a lifespan that owns the process-level database resources."""

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        database = Database.from_settings(settings)
        application.state.database = database
        try:
            yield
        finally:
            await database.dispose()

    return lifespan


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure a PingStats FastAPI application."""
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)

    application = FastAPI(
        title=resolved_settings.app_name,
        version=__version__,
        debug=resolved_settings.app_debug,
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=create_lifespan(resolved_settings),
    )
    application.state.settings = resolved_settings
    application.add_exception_handler(ApplicationError, application_error_handler)
    application.include_router(api_router, prefix=resolved_settings.api_v1_prefix)
    application.include_router(users_router, prefix=resolved_settings.api_v1_prefix)
    application.include_router(friends_router, prefix=resolved_settings.api_v1_prefix)
    application.include_router(matches_router, prefix=resolved_settings.api_v1_prefix)
    application.include_router(game_sessions_router, prefix=resolved_settings.api_v1_prefix)
    application.include_router(telegram_auth_router, prefix=resolved_settings.api_v1_prefix)
    if resolved_settings.app_env is AppEnvironment.DEVELOPMENT:
        application.include_router(
            development_router,
            prefix=resolved_settings.api_v1_prefix,
        )
        application.include_router(
            development_auth_router,
            prefix=resolved_settings.api_v1_prefix,
        )
    return application

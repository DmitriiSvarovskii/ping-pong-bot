"""FastAPI application factory."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

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
from pingstats.modules.game_sessions.realtime import (
    SqlAlchemyWebSocketSessionAuthorizer,
    WebSocketConnectionManager,
    WebSocketSessionsUpdatePublisher,
    WebSocketSessionUpdatePublisher,
    WebSocketTicketStore,
)
from pingstats.modules.leaderboard.api import leaderboard_router
from pingstats.modules.matches.api import matches_router
from pingstats.modules.ratings.api import ratings_router
from pingstats.modules.ratings.infrastructure.scheduler import (
    RatingPeriodScheduler,
    RatingSchedulerWakeup,
)
from pingstats.modules.ratings.infrastructure.uow import SqlAlchemyRatingsUnitOfWork
from pingstats.modules.users.api import users_router


def create_lifespan(settings: Settings):
    """Build a lifespan that owns the process-level database resources."""

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        database = Database.from_settings(settings)
        application.state.database = database
        application.state.websocket_session_authorizer = SqlAlchemyWebSocketSessionAuthorizer(
            database.session_factory
        )
        rating_wakeup = application.state.rating_scheduler_wakeup
        rating_period_task: asyncio.Task[None] | None = None
        if settings.app_env is not AppEnvironment.TESTING:
            scheduler = RatingPeriodScheduler(
                lambda: SqlAlchemyRatingsUnitOfWork(database.session_factory),
                wakeup=rating_wakeup,
            )
            application.state.rating_period_scheduler = scheduler
            rating_period_task = asyncio.create_task(
                scheduler.run_forever(),
                name="rating-workflow",
            )
        try:
            yield
        finally:
            if rating_period_task is not None:
                rating_period_task.cancel()
                with suppress(asyncio.CancelledError):
                    await rating_period_task
            await application.state.websocket_connection_manager.close_all()
            await application.state.websocket_ticket_store.clear()
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
    application.state.rating_scheduler_wakeup = RatingSchedulerWakeup()
    application.state.websocket_ticket_store = WebSocketTicketStore(
        ttl_seconds=resolved_settings.websocket_ticket_ttl_seconds
    )
    application.state.websocket_connection_manager = WebSocketConnectionManager()
    application.state.session_update_publisher = WebSocketSessionUpdatePublisher(
        application.state.websocket_connection_manager
    )
    application.state.sessions_update_publisher = WebSocketSessionsUpdatePublisher(
        application.state.websocket_connection_manager
    )
    application.add_exception_handler(ApplicationError, application_error_handler)
    application.include_router(api_router, prefix=resolved_settings.api_v1_prefix)
    application.include_router(users_router, prefix=resolved_settings.api_v1_prefix)
    application.include_router(friends_router, prefix=resolved_settings.api_v1_prefix)
    application.include_router(matches_router, prefix=resolved_settings.api_v1_prefix)
    application.include_router(game_sessions_router, prefix=resolved_settings.api_v1_prefix)
    application.include_router(ratings_router, prefix=resolved_settings.api_v1_prefix)
    application.include_router(leaderboard_router, prefix=resolved_settings.api_v1_prefix)
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

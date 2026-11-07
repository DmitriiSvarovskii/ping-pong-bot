"""FastAPI dependencies for game sessions."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from pingstats.infrastructure.database.engine import Database
from pingstats.infrastructure.database.session import get_database, get_db_session
from pingstats.modules.game_sessions.application import GameSessionService
from pingstats.modules.game_sessions.infrastructure.repository import (
    SqlAlchemyGameSessionRepository,
)
from pingstats.modules.game_sessions.infrastructure.uow import SqlAlchemyGameSessionsUnitOfWork
from pingstats.modules.matches.infrastructure.repository import SqlAlchemyMatchRepository


def get_game_session_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SqlAlchemyGameSessionRepository:
    return SqlAlchemyGameSessionRepository(session)


def get_game_session_match_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SqlAlchemyMatchRepository:
    return SqlAlchemyMatchRepository(session)


def get_game_session_service() -> GameSessionService:
    return GameSessionService()


def get_game_sessions_uow(
    database: Annotated[Database, Depends(get_database)],
) -> SqlAlchemyGameSessionsUnitOfWork:
    return SqlAlchemyGameSessionsUnitOfWork(database.session_factory)

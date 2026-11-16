"""FastAPI dependencies for matches."""

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from pingstats.infrastructure.database.engine import Database
from pingstats.infrastructure.database.session import get_database, get_db_session
from pingstats.modules.matches.application import MatchService
from pingstats.modules.matches.infrastructure.repository import SqlAlchemyMatchRepository
from pingstats.modules.matches.infrastructure.uow import SqlAlchemyMatchesUnitOfWork


def get_match_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SqlAlchemyMatchRepository:
    return SqlAlchemyMatchRepository(session)


def get_match_service(request: Request) -> MatchService:
    return MatchService(
        rating_rebuild_notifier=request.app.state.rating_scheduler_wakeup,
    )


def get_matches_uow(
    database: Annotated[Database, Depends(get_database)],
) -> SqlAlchemyMatchesUnitOfWork:
    return SqlAlchemyMatchesUnitOfWork(database.session_factory)

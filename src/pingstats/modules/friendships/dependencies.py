"""FastAPI dependencies for friendships."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from pingstats.infrastructure.database.engine import Database
from pingstats.infrastructure.database.session import get_database, get_db_session
from pingstats.modules.friendships.application import FriendshipService
from pingstats.modules.friendships.infrastructure.repository import SqlAlchemyFriendshipRepository
from pingstats.modules.friendships.infrastructure.uow import SqlAlchemyFriendshipsUnitOfWork


def get_friendship_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SqlAlchemyFriendshipRepository:
    return SqlAlchemyFriendshipRepository(session)


def get_friendship_service() -> FriendshipService:
    return FriendshipService()


def get_friendships_uow(
    database: Annotated[Database, Depends(get_database)],
) -> SqlAlchemyFriendshipsUnitOfWork:
    return SqlAlchemyFriendshipsUnitOfWork(database.session_factory)

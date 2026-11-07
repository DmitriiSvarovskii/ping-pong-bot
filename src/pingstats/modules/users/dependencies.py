"""FastAPI dependencies for the users module."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from pingstats.infrastructure.database.engine import Database
from pingstats.infrastructure.database.session import get_database, get_db_session
from pingstats.modules.users.application.service import UsersService
from pingstats.modules.users.infrastructure.repository import SqlAlchemyUserRepository
from pingstats.modules.users.infrastructure.uow import SqlAlchemyUsersUnitOfWork


def get_user_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SqlAlchemyUserRepository:
    return SqlAlchemyUserRepository(session)


def get_users_service() -> UsersService:
    return UsersService()


def get_users_uow(
    database: Annotated[Database, Depends(get_database)],
) -> SqlAlchemyUsersUnitOfWork:
    return SqlAlchemyUsersUnitOfWork(database.session_factory)

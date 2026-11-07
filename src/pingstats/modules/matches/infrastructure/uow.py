"""Matches adapter over the shared SQLAlchemy Unit of Work."""

from pingstats.infrastructure.database.uow import SqlAlchemyUnitOfWork
from pingstats.modules.matches.infrastructure.repository import SqlAlchemyMatchRepository


class SqlAlchemyMatchesUnitOfWork(SqlAlchemyUnitOfWork):
    matches: SqlAlchemyMatchRepository

    async def __aenter__(self) -> "SqlAlchemyMatchesUnitOfWork":
        await super().__aenter__()
        self.matches = SqlAlchemyMatchRepository(self.session)
        return self

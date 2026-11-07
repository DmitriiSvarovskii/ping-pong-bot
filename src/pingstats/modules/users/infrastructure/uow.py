"""Users-specific adapter over the shared SQLAlchemy Unit of Work."""

from pingstats.infrastructure.database.uow import SqlAlchemyUnitOfWork
from pingstats.modules.users.infrastructure.repository import SqlAlchemyUserRepository


class SqlAlchemyUsersUnitOfWork(SqlAlchemyUnitOfWork):
    """Expose the users repository within the shared transaction manager."""

    users: SqlAlchemyUserRepository

    async def __aenter__(self) -> "SqlAlchemyUsersUnitOfWork":
        await super().__aenter__()
        self.users = SqlAlchemyUserRepository(self.session)
        return self

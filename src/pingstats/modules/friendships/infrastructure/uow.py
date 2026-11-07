"""Friendships adapter over the shared SQLAlchemy Unit of Work."""

from pingstats.infrastructure.database.uow import SqlAlchemyUnitOfWork
from pingstats.modules.friendships.infrastructure.repository import SqlAlchemyFriendshipRepository


class SqlAlchemyFriendshipsUnitOfWork(SqlAlchemyUnitOfWork):
    friendships: SqlAlchemyFriendshipRepository

    async def __aenter__(self) -> "SqlAlchemyFriendshipsUnitOfWork":
        await super().__aenter__()
        self.friendships = SqlAlchemyFriendshipRepository(self.session)
        return self

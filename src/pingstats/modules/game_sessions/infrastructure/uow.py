"""Game sessions adapter over the shared SQLAlchemy Unit of Work."""

from pingstats.infrastructure.database.uow import SqlAlchemyUnitOfWork
from pingstats.modules.game_sessions.infrastructure.repository import (
    SqlAlchemyCurrentSessionMatchChecker,
    SqlAlchemyGameSessionRepository,
)
from pingstats.modules.matches.infrastructure.repository import SqlAlchemyMatchRepository


class SqlAlchemyGameSessionsUnitOfWork(SqlAlchemyUnitOfWork):
    game_sessions: SqlAlchemyGameSessionRepository
    current_matches: SqlAlchemyCurrentSessionMatchChecker
    matches: SqlAlchemyMatchRepository

    async def __aenter__(self) -> "SqlAlchemyGameSessionsUnitOfWork":
        await super().__aenter__()
        self.game_sessions = SqlAlchemyGameSessionRepository(self.session)
        self.current_matches = SqlAlchemyCurrentSessionMatchChecker(self.session)
        self.matches = SqlAlchemyMatchRepository(self.session)
        return self

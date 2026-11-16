"""Game sessions adapter over the shared SQLAlchemy Unit of Work."""

from pingstats.infrastructure.database.uow import SqlAlchemyUnitOfWork
from pingstats.modules.game_sessions.infrastructure.repository import (
    SqlAlchemyCurrentSessionMatchChecker,
    SqlAlchemyGameSessionRepository,
)
from pingstats.modules.matches.infrastructure.repository import SqlAlchemyMatchRepository
from pingstats.modules.ratings.infrastructure.repository import (
    SqlAlchemyRatingInputRepository,
    SqlAlchemyRatingPeriodRepository,
    SqlAlchemyRatingRebuildJobRepository,
    SqlAlchemyRatingRebuildPeriodRepository,
    SqlAlchemyRatingRecalculationRepository,
)


class SqlAlchemyGameSessionsUnitOfWork(SqlAlchemyUnitOfWork):
    game_sessions: SqlAlchemyGameSessionRepository
    current_matches: SqlAlchemyCurrentSessionMatchChecker
    matches: SqlAlchemyMatchRepository
    rating_periods: SqlAlchemyRatingPeriodRepository
    rating_inputs: SqlAlchemyRatingInputRepository
    rating_recalculations: SqlAlchemyRatingRecalculationRepository
    rating_rebuild_jobs: SqlAlchemyRatingRebuildJobRepository
    rating_rebuild_periods: SqlAlchemyRatingRebuildPeriodRepository

    async def __aenter__(self) -> "SqlAlchemyGameSessionsUnitOfWork":
        await super().__aenter__()
        self.game_sessions = SqlAlchemyGameSessionRepository(self.session)
        self.current_matches = SqlAlchemyCurrentSessionMatchChecker(self.session)
        self.matches = SqlAlchemyMatchRepository(self.session)
        self.rating_periods = SqlAlchemyRatingPeriodRepository(self.session)
        self.rating_inputs = SqlAlchemyRatingInputRepository(self.session)
        self.rating_recalculations = SqlAlchemyRatingRecalculationRepository(self.session)
        self.rating_rebuild_jobs = SqlAlchemyRatingRebuildJobRepository(self.session)
        self.rating_rebuild_periods = SqlAlchemyRatingRebuildPeriodRepository(self.session)
        return self

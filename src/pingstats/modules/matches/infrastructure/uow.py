"""Matches adapter over the shared SQLAlchemy Unit of Work."""

from pingstats.infrastructure.database.uow import SqlAlchemyUnitOfWork
from pingstats.modules.matches.infrastructure.repository import SqlAlchemyMatchRepository
from pingstats.modules.ratings.infrastructure.repository import (
    SqlAlchemyRatingInputRepository,
    SqlAlchemyRatingPeriodRepository,
    SqlAlchemyRatingRebuildJobRepository,
    SqlAlchemyRatingRebuildPeriodRepository,
    SqlAlchemyRatingRecalculationRepository,
)


class SqlAlchemyMatchesUnitOfWork(SqlAlchemyUnitOfWork):
    matches: SqlAlchemyMatchRepository
    rating_periods: SqlAlchemyRatingPeriodRepository
    rating_inputs: SqlAlchemyRatingInputRepository
    rating_recalculations: SqlAlchemyRatingRecalculationRepository
    rating_rebuild_jobs: SqlAlchemyRatingRebuildJobRepository
    rating_rebuild_periods: SqlAlchemyRatingRebuildPeriodRepository

    async def __aenter__(self) -> "SqlAlchemyMatchesUnitOfWork":
        await super().__aenter__()
        self.matches = SqlAlchemyMatchRepository(self.session)
        self.rating_periods = SqlAlchemyRatingPeriodRepository(self.session)
        self.rating_inputs = SqlAlchemyRatingInputRepository(self.session)
        self.rating_recalculations = SqlAlchemyRatingRecalculationRepository(self.session)
        self.rating_rebuild_jobs = SqlAlchemyRatingRebuildJobRepository(self.session)
        self.rating_rebuild_periods = SqlAlchemyRatingRebuildPeriodRepository(self.session)
        return self

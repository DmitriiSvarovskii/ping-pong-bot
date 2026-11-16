"""Ratings adapter over the shared SQLAlchemy Unit of Work."""

from pingstats.infrastructure.database.uow import SqlAlchemyUnitOfWork
from pingstats.modules.matches.infrastructure.repository import SqlAlchemyMatchRepository
from pingstats.modules.ratings.infrastructure.repository import (
    SqlAlchemyRatingInputRepository,
    SqlAlchemyRatingPeriodRepository,
    SqlAlchemyRatingRebuildJobRepository,
    SqlAlchemyRatingRebuildPeriodRepository,
    SqlAlchemyRatingRecalculationRepository,
    SqlAlchemyRatingRepository,
    SqlAlchemyRatingResultRepository,
)


class SqlAlchemyRatingsUnitOfWork(SqlAlchemyUnitOfWork):
    matches: SqlAlchemyMatchRepository
    ratings: SqlAlchemyRatingRepository
    periods: SqlAlchemyRatingPeriodRepository
    inputs: SqlAlchemyRatingInputRepository
    results: SqlAlchemyRatingResultRepository
    recalculations: SqlAlchemyRatingRecalculationRepository
    rebuild_jobs: SqlAlchemyRatingRebuildJobRepository
    rebuild_periods: SqlAlchemyRatingRebuildPeriodRepository

    async def __aenter__(self) -> "SqlAlchemyRatingsUnitOfWork":
        await super().__aenter__()
        self.matches = SqlAlchemyMatchRepository(self.session)
        self.ratings = SqlAlchemyRatingRepository(self.session)
        self.periods = SqlAlchemyRatingPeriodRepository(self.session)
        self.inputs = SqlAlchemyRatingInputRepository(self.session)
        self.results = SqlAlchemyRatingResultRepository(self.session)
        self.recalculations = SqlAlchemyRatingRecalculationRepository(self.session)
        self.rebuild_jobs = SqlAlchemyRatingRebuildJobRepository(self.session)
        self.rebuild_periods = SqlAlchemyRatingRebuildPeriodRepository(self.session)
        return self

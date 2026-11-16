"""FastAPI dependencies for leaderboard queries."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from pingstats.infrastructure.database.session import get_db_session
from pingstats.modules.leaderboard.application import LeaderboardService
from pingstats.modules.leaderboard.infrastructure import SqlAlchemyLeaderboardRepository
from pingstats.modules.ratings.application.queries import RatingQueryService
from pingstats.modules.ratings.dependencies import get_rating_query_service


def get_leaderboard_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SqlAlchemyLeaderboardRepository:
    return SqlAlchemyLeaderboardRepository(session)


def get_leaderboard_service(
    rating_queries: Annotated[RatingQueryService, Depends(get_rating_query_service)],
) -> LeaderboardService:
    return LeaderboardService(rating_queries)

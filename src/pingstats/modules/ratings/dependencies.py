"""FastAPI dependencies for read-only rating queries."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from pingstats.infrastructure.database.session import get_db_session
from pingstats.modules.ratings.application.match_state import RatingMatchStateService
from pingstats.modules.ratings.application.queries import RatingQueryService
from pingstats.modules.ratings.application.status import RatingStatusService
from pingstats.modules.ratings.infrastructure.repository import (
    SqlAlchemyRatingQueryRepository,
)


def get_rating_query_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SqlAlchemyRatingQueryRepository:
    return SqlAlchemyRatingQueryRepository(session)


def get_rating_query_service() -> RatingQueryService:
    return RatingQueryService()


def get_rating_status_service() -> RatingStatusService:
    return RatingStatusService()


def get_rating_match_state_service() -> RatingMatchStateService:
    return RatingMatchStateService()

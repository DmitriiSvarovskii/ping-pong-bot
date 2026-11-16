"""Authenticated read-only API for active published ratings."""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from pingstats.modules.auth.dependencies import get_current_user
from pingstats.modules.ratings.application.queries import RatingQueryService
from pingstats.modules.ratings.application.status import RatingStatusService
from pingstats.modules.ratings.dependencies import (
    get_rating_query_repository,
    get_rating_query_service,
    get_rating_status_service,
)
from pingstats.modules.ratings.infrastructure.repository import (
    SqlAlchemyRatingQueryRepository,
)
from pingstats.modules.ratings.schemas import (
    CurrentRatingResponse,
    RatingHistoryItemResponse,
    RatingHistoryResponse,
    RatingStatusResponse,
)
from pingstats.modules.users.domain import User

ratings_router = APIRouter(prefix="/ratings", tags=["ratings"])


async def _current_rating(
    user_id: UUID,
    repository: SqlAlchemyRatingQueryRepository,
    service: RatingQueryService,
) -> CurrentRatingResponse:
    return CurrentRatingResponse.from_view(await service.get_current(repository, user_id))


@ratings_router.get("/me", response_model=CurrentRatingResponse)
async def get_own_rating(
    current_user: Annotated[User, Depends(get_current_user)],
    repository: Annotated[SqlAlchemyRatingQueryRepository, Depends(get_rating_query_repository)],
    service: Annotated[RatingQueryService, Depends(get_rating_query_service)],
) -> CurrentRatingResponse:
    return await _current_rating(current_user.id, repository, service)


@ratings_router.get("/me/status", response_model=RatingStatusResponse)
async def get_own_rating_status(
    current_user: Annotated[User, Depends(get_current_user)],
    repository: Annotated[SqlAlchemyRatingQueryRepository, Depends(get_rating_query_repository)],
    service: Annotated[RatingStatusService, Depends(get_rating_status_service)],
) -> RatingStatusResponse:
    return RatingStatusResponse.from_view(
        await service.get_status(repository, user_id=current_user.id)
    )


@ratings_router.get("/{user_id}/history", response_model=RatingHistoryResponse)
async def get_rating_history(
    user_id: UUID,
    _: Annotated[User, Depends(get_current_user)],
    repository: Annotated[SqlAlchemyRatingQueryRepository, Depends(get_rating_query_repository)],
    service: Annotated[RatingQueryService, Depends(get_rating_query_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    from_date: Annotated[
        date | None,
        Query(description="Inclusive UTC date of the first rating period (YYYY-MM-DD)"),
    ] = None,
    to_date: Annotated[
        date | None,
        Query(description="Inclusive UTC date of the last rating period (YYYY-MM-DD)"),
    ] = None,
) -> RatingHistoryResponse:
    if from_date is not None and to_date is not None and from_date > to_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="from_date must be less than or equal to to_date",
        )
    history, total = await service.list_history(
        repository,
        user_id,
        limit=limit,
        offset=offset,
        from_date=from_date,
        to_date=to_date,
    )
    return RatingHistoryResponse(
        items=[RatingHistoryItemResponse.from_view(item) for item in history],
        total=total,
        limit=limit,
        offset=offset,
    )


@ratings_router.get("/{user_id}", response_model=CurrentRatingResponse)
async def get_player_rating(
    user_id: UUID,
    _: Annotated[User, Depends(get_current_user)],
    repository: Annotated[SqlAlchemyRatingQueryRepository, Depends(get_rating_query_repository)],
    service: Annotated[RatingQueryService, Depends(get_rating_query_service)],
) -> CurrentRatingResponse:
    return await _current_rating(user_id, repository, service)

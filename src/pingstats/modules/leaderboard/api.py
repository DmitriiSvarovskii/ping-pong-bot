"""Authenticated global leaderboard API."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from pingstats.modules.auth.dependencies import get_current_user
from pingstats.modules.leaderboard.application import LeaderboardService
from pingstats.modules.leaderboard.dependencies import (
    get_leaderboard_repository,
    get_leaderboard_service,
)
from pingstats.modules.leaderboard.infrastructure import SqlAlchemyLeaderboardRepository
from pingstats.modules.leaderboard.schemas import LeaderboardResponse
from pingstats.modules.users.domain import User

leaderboard_router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])


@leaderboard_router.get("", response_model=LeaderboardResponse)
async def get_leaderboard(
    current_user: Annotated[User, Depends(get_current_user)],
    repository: Annotated[SqlAlchemyLeaderboardRepository, Depends(get_leaderboard_repository)],
    service: Annotated[LeaderboardService, Depends(get_leaderboard_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> LeaderboardResponse:
    page = await service.get_page(
        repository,
        current_user_id=current_user.id,
        limit=limit,
        offset=offset,
    )
    return LeaderboardResponse.from_page(page)

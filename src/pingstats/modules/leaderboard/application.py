"""Application query for the global leaderboard."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from pingstats.modules.ratings.application.queries import RatingQueryService
from pingstats.modules.ratings.domain import PublishedRating, RatingPeriod
from pingstats.modules.users.domain import User


@dataclass(frozen=True, slots=True)
class RankedRating:
    position: int
    user: User
    rating: PublishedRating
    period: RatingPeriod


class LeaderboardRepository(Protocol):
    async def list_ranked(
        self,
        *,
        current_user_id: UUID,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[RankedRating], int, int | None]: ...


@dataclass(frozen=True, slots=True)
class LeaderboardPlayerView:
    position: int
    user_id: UUID
    first_name: str
    last_name: str | None
    username: str | None
    photo_url: str | None
    rating: Decimal
    rd: Decimal
    effective_rd: Decimal
    rated_matches_count: int


@dataclass(frozen=True, slots=True)
class LeaderboardPage:
    items: tuple[LeaderboardPlayerView, ...]
    total: int
    limit: int
    offset: int
    current_user_position: int | None


class LeaderboardService:
    def __init__(self, rating_queries: RatingQueryService) -> None:
        self._rating_queries = rating_queries

    async def get_page(
        self,
        repository: LeaderboardRepository,
        *,
        current_user_id: UUID,
        limit: int,
        offset: int,
    ) -> LeaderboardPage:
        rows, total, current_position = await repository.list_ranked(
            current_user_id=current_user_id,
            limit=limit,
            offset=offset,
        )
        read_time = self._rating_queries.read_time()
        items = tuple(self._to_view(row, at=read_time) for row in rows)
        return LeaderboardPage(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
            current_user_position=current_position,
        )

    def _to_view(self, row: RankedRating, *, at: datetime) -> LeaderboardPlayerView:
        projected = self._rating_queries.project_current(
            row.rating,
            row.period,
            position=row.position,
            at=at,
        )
        return LeaderboardPlayerView(
            position=row.position,
            user_id=row.user.id,
            first_name=row.user.first_name,
            last_name=row.user.last_name,
            username=row.user.username,
            photo_url=row.user.photo_url,
            rating=projected.rating,
            rd=projected.rd,
            effective_rd=projected.effective_rd,
            rated_matches_count=projected.rated_matches_count,
        )

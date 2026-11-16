"""HTTP schemas for the global leaderboard."""

from dataclasses import asdict
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel

from pingstats.modules.leaderboard.application import LeaderboardPage, LeaderboardPlayerView


class LeaderboardPlayerResponse(BaseModel):
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

    @classmethod
    def from_view(cls, view: LeaderboardPlayerView) -> "LeaderboardPlayerResponse":
        return cls(**asdict(view))


class LeaderboardResponse(BaseModel):
    items: list[LeaderboardPlayerResponse]
    total: int
    limit: int
    offset: int
    current_user_position: int | None

    @classmethod
    def from_page(cls, page: LeaderboardPage) -> "LeaderboardResponse":
        return cls(
            items=[LeaderboardPlayerResponse.from_view(item) for item in page.items],
            total=page.total,
            limit=page.limit,
            offset=page.offset,
            current_user_position=page.current_user_position,
        )

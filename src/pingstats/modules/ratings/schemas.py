"""HTTP response schemas for published ratings."""

from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel

from pingstats.modules.ratings.application.queries import (
    CurrentRatingView,
    RatingHistoryView,
)


class CurrentRatingResponse(BaseModel):
    user_id: UUID
    rating: Decimal
    rd: Decimal
    effective_rd: Decimal
    volatility: Decimal
    rated_matches_count: int
    as_of_period_id: UUID
    as_of_period_ends_at: datetime
    inactivity_periods: int
    updated_at: datetime

    @classmethod
    def from_view(cls, view: CurrentRatingView) -> "CurrentRatingResponse":
        return cls(**asdict(view))


class RatingHistoryItemResponse(BaseModel):
    period_id: UUID
    period_starts_at: datetime
    period_ends_at: datetime
    published_at: datetime
    rating_before: Decimal
    rating_after: Decimal
    rd_before: Decimal
    rd_after: Decimal
    volatility_before: Decimal
    volatility_after: Decimal
    games_count: int
    is_active: bool

    @classmethod
    def from_view(cls, view: RatingHistoryView) -> "RatingHistoryItemResponse":
        return cls(**asdict(view))


class RatingHistoryResponse(BaseModel):
    items: list[RatingHistoryItemResponse]
    total: int
    limit: int
    offset: int

"""Structured, persistence-neutral results of one rating period calculation."""

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True, slots=True)
class CalculatedPlayerRating:
    user_id: UUID
    rating_before: Decimal
    rating_after: Decimal
    rd_before: Decimal
    rd_after: Decimal
    volatility_before: Decimal
    volatility_after: Decimal
    games_count: int
    is_active: bool


@dataclass(frozen=True, slots=True)
class RatingPeriodCalculation:
    period_id: UUID
    calculation_version: int
    players: tuple[CalculatedPlayerRating, ...]

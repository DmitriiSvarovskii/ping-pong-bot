"""Public domain API for ratings."""

from pingstats.modules.ratings.domain.entities import RatingOpponent, RatingState
from pingstats.modules.ratings.domain.glicko2 import GLICKO2_RATING_SCALE, Glicko2Strategy
from pingstats.modules.ratings.domain.records import (
    PublishedRating,
    RatingPeriod,
    RatingPeriodMatchInput,
    RatingPeriodResult,
    RatingPeriodStatus,
    RatingRebuildJob,
    RatingRebuildPeriod,
    RatingRebuildPeriodStatus,
    RatingRebuildStatus,
    RatingRecalculationRequest,
    RatingRecalculationStatus,
)
from pingstats.modules.ratings.domain.value_objects import (
    Glicko2ConvergenceError,
    Glicko2Parameters,
    InvalidRatingValueError,
)

__all__ = [
    "GLICKO2_RATING_SCALE",
    "Glicko2ConvergenceError",
    "Glicko2Parameters",
    "Glicko2Strategy",
    "InvalidRatingValueError",
    "PublishedRating",
    "RatingOpponent",
    "RatingPeriod",
    "RatingPeriodMatchInput",
    "RatingPeriodResult",
    "RatingPeriodStatus",
    "RatingRebuildJob",
    "RatingRebuildPeriod",
    "RatingRebuildPeriodStatus",
    "RatingRebuildStatus",
    "RatingRecalculationRequest",
    "RatingRecalculationStatus",
    "RatingState",
]

"""Immutable rating values used by the Glicko-2 domain strategy."""

from dataclasses import dataclass

from pingstats.modules.ratings.domain.value_objects import (
    InvalidRatingValueError,
    finite_float,
)


@dataclass(frozen=True, slots=True)
class RatingState:
    """A player's state at one rating-period boundary."""

    rating: float
    rd: float
    volatility: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "rating", finite_float(self.rating, "rating"))
        object.__setattr__(self, "rd", finite_float(self.rd, "rd", positive=True))
        object.__setattr__(
            self,
            "volatility",
            finite_float(self.volatility, "volatility", positive=True),
        )


@dataclass(frozen=True, slots=True)
class RatingOpponent:
    """An opponent's pre-period state and the observed game score."""

    rating: float
    rd: float
    score: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "rating", finite_float(self.rating, "opponent rating"))
        object.__setattr__(self, "rd", finite_float(self.rd, "opponent rd", positive=True))
        score = finite_float(self.score, "score")
        if score not in {0.0, 0.5, 1.0}:
            raise InvalidRatingValueError("score must be one of 0.0, 0.5, or 1.0")
        object.__setattr__(self, "score", score)

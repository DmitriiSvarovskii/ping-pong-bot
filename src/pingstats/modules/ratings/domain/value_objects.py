"""Validated Glicko-2 parameters and domain errors."""

from dataclasses import dataclass
from math import isfinite
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pingstats.modules.ratings.domain.entities import RatingState


class InvalidRatingValueError(ValueError):
    """Raised when rating input cannot be used safely by Glicko-2."""


class Glicko2ConvergenceError(RuntimeError):
    """Raised when the official volatility iteration does not converge."""


def finite_float(value: object, name: str, *, positive: bool = False) -> float:
    """Return a validated float while rejecting bools and non-finite values."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidRatingValueError(f"{name} must be a finite number")
    result = float(value)
    if not isfinite(result):
        raise InvalidRatingValueError(f"{name} must be a finite number")
    if positive and result <= 0.0:
        raise InvalidRatingValueError(f"{name} must be greater than zero")
    return result


@dataclass(frozen=True, slots=True)
class Glicko2Parameters:
    """Configuration shared by all calculations in one algorithm version."""

    initial_rating: float = 1500.0
    initial_rd: float = 350.0
    initial_volatility: float = 0.06
    tau: float = 0.5
    max_rd: float = 350.0
    convergence_epsilon: float = 0.000001
    max_iterations: int = 100

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "initial_rating", finite_float(self.initial_rating, "initial_rating")
        )
        object.__setattr__(
            self, "initial_rd", finite_float(self.initial_rd, "initial_rd", positive=True)
        )
        object.__setattr__(
            self,
            "initial_volatility",
            finite_float(self.initial_volatility, "initial_volatility", positive=True),
        )
        object.__setattr__(self, "tau", finite_float(self.tau, "tau", positive=True))
        object.__setattr__(self, "max_rd", finite_float(self.max_rd, "max_rd", positive=True))
        object.__setattr__(
            self,
            "convergence_epsilon",
            finite_float(self.convergence_epsilon, "convergence_epsilon", positive=True),
        )
        if isinstance(self.max_iterations, bool) or not isinstance(self.max_iterations, int):
            raise InvalidRatingValueError("max_iterations must be a positive integer")
        if self.max_iterations <= 0:
            raise InvalidRatingValueError("max_iterations must be a positive integer")
        if self.initial_rd > self.max_rd:
            raise InvalidRatingValueError("initial_rd must not exceed max_rd")

    def initial_state(self) -> "RatingState":
        """Create the configured state of a player with no rating history."""
        from pingstats.modules.ratings.domain.entities import RatingState

        return RatingState(
            rating=self.initial_rating,
            rd=self.initial_rd,
            volatility=self.initial_volatility,
        )

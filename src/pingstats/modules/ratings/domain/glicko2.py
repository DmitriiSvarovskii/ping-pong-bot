"""Infrastructure-independent implementation of Mark Glickman's Glicko-2 system."""

from collections.abc import Sequence
from math import exp, isfinite, log, pi, sqrt

from pingstats.modules.ratings.domain.entities import RatingOpponent, RatingState
from pingstats.modules.ratings.domain.value_objects import (
    Glicko2ConvergenceError,
    Glicko2Parameters,
    InvalidRatingValueError,
)

GLICKO2_RATING_SCALE = 173.7178
_GLICKO2_RATING_ORIGIN = 1500.0


class Glicko2Strategy:
    """Calculate one player state from simultaneous results in one rating period."""

    def __init__(self, parameters: Glicko2Parameters | None = None) -> None:
        self.parameters = parameters or Glicko2Parameters()

    def calculate(
        self,
        player: RatingState,
        opponents: Sequence[RatingOpponent],
    ) -> RatingState:
        """Return a new state without mutating the player or opponent values."""
        period_opponents = tuple(opponents)
        mu = self._to_mu(player.rating)
        phi = self._to_phi(player.rd)

        if not period_opponents:
            return self.advance_inactivity(player, periods=1)

        terms = tuple(self._opponent_term(mu, opponent) for opponent in period_opponents)
        variance_sum = sum(
            g_value * g_value * expected * (1.0 - expected) for g_value, expected, _ in terms
        )
        if variance_sum <= 0.0 or not isfinite(variance_sum):
            raise InvalidRatingValueError("opponents do not produce a finite positive variance")
        variance = 1.0 / variance_sum
        delta = variance * sum(g_value * (score - expected) for g_value, expected, score in terms)
        volatility = self._calculate_volatility(
            phi=phi,
            volatility=player.volatility,
            variance=variance,
            delta=delta,
        )
        pre_rating_phi = sqrt(phi * phi + volatility * volatility)
        new_phi = 1.0 / sqrt(1.0 / (pre_rating_phi * pre_rating_phi) + 1.0 / variance)
        new_mu = mu + new_phi * new_phi * sum(
            g_value * (score - expected) for g_value, expected, score in terms
        )
        result = RatingState(
            rating=self._from_mu(new_mu),
            rd=min(self.parameters.max_rd, self._from_phi(new_phi)),
            volatility=volatility,
        )
        if not all(isfinite(value) for value in (result.rating, result.rd, result.volatility)):
            raise InvalidRatingValueError("Glicko-2 calculation produced a non-finite state")
        return result

    def advance_inactivity(self, player: RatingState, *, periods: int) -> RatingState:
        """Evolve an inactive state by whole rating periods in one equivalent step."""
        if isinstance(periods, bool) or not isinstance(periods, int) or periods < 0:
            raise ValueError("periods must be a non-negative integer")
        if periods == 0:
            return player
        phi = self._to_phi(player.rd)
        inactive_phi = sqrt(phi * phi + periods * player.volatility * player.volatility)
        result = RatingState(
            rating=player.rating,
            rd=min(self.parameters.max_rd, self._from_phi(inactive_phi)),
            volatility=player.volatility,
        )
        if not all(isfinite(value) for value in (result.rating, result.rd, result.volatility)):
            raise InvalidRatingValueError(
                "Glicko-2 inactivity evolution produced a non-finite state"
            )
        return result

    @staticmethod
    def _to_mu(rating: float) -> float:
        return (rating - _GLICKO2_RATING_ORIGIN) / GLICKO2_RATING_SCALE

    @staticmethod
    def _to_phi(rd: float) -> float:
        return rd / GLICKO2_RATING_SCALE

    @staticmethod
    def _from_mu(mu: float) -> float:
        return mu * GLICKO2_RATING_SCALE + _GLICKO2_RATING_ORIGIN

    @staticmethod
    def _from_phi(phi: float) -> float:
        return phi * GLICKO2_RATING_SCALE

    def _opponent_term(
        self, player_mu: float, opponent: RatingOpponent
    ) -> tuple[float, float, float]:
        opponent_phi = self._to_phi(opponent.rd)
        g_value = 1.0 / sqrt(1.0 + 3.0 * opponent_phi * opponent_phi / (pi * pi))
        expected = self._logistic(g_value * (player_mu - self._to_mu(opponent.rating)))
        return g_value, expected, opponent.score

    @staticmethod
    def _logistic(value: float) -> float:
        if value >= 0.0:
            return 1.0 / (1.0 + exp(-value))
        exponential = exp(value)
        return exponential / (1.0 + exponential)

    def _calculate_volatility(
        self,
        *,
        phi: float,
        volatility: float,
        variance: float,
        delta: float,
    ) -> float:
        tau = self.parameters.tau
        alpha = 2.0 * log(volatility)

        def objective(value: float) -> float:
            try:
                exponential = exp(value)
            except OverflowError as exc:
                raise InvalidRatingValueError(
                    "volatility iteration exceeded the finite numeric range"
                ) from exc
            denominator = phi * phi + variance + exponential
            return exponential * (delta * delta - phi * phi - variance - exponential) / (
                2.0 * denominator * denominator
            ) - (value - alpha) / (tau * tau)

        lower = alpha
        if delta * delta > phi * phi + variance:
            upper = log(delta * delta - phi * phi - variance)
        else:
            for step in range(1, self.parameters.max_iterations + 1):
                upper = alpha - step * tau
                if objective(upper) >= 0.0:
                    break
            else:
                raise Glicko2ConvergenceError("Glicko-2 volatility bracket did not converge")

        lower_value = objective(lower)
        upper_value = objective(upper)
        for _ in range(self.parameters.max_iterations):
            if abs(upper - lower) <= self.parameters.convergence_epsilon:
                return exp(lower / 2.0)
            value_delta = upper_value - lower_value
            if value_delta == 0.0 or not isfinite(value_delta):
                raise Glicko2ConvergenceError(
                    "Glicko-2 volatility iteration became numerically unstable"
                )
            candidate = lower + (lower - upper) * lower_value / value_delta
            candidate_value = objective(candidate)
            if candidate_value * upper_value <= 0.0:
                lower = upper
                lower_value = upper_value
            else:
                lower_value /= 2.0
            upper = candidate
            upper_value = candidate_value

        raise Glicko2ConvergenceError(
            f"Glicko-2 volatility did not converge in {self.parameters.max_iterations} iterations"
        )

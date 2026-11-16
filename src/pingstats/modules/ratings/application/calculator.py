"""Deterministic calculation of one sealed Glicko-2 rating period."""

from collections import defaultdict
from datetime import UTC
from decimal import ROUND_HALF_EVEN, Decimal
from uuid import UUID

from pingstats.modules.ratings.application.dto import (
    CalculatedPlayerRating,
    RatingPeriodCalculation,
)
from pingstats.modules.ratings.application.errors import (
    RatingHistoryUnavailableError,
    RatingPeriodNotCalculatingError,
    RatingPeriodNotFoundError,
    UnsupportedRatingAlgorithmError,
)
from pingstats.modules.ratings.application.ports import RatingsUnitOfWork
from pingstats.modules.ratings.domain import (
    Glicko2Parameters,
    Glicko2Strategy,
    RatingOpponent,
    RatingPeriod,
    RatingPeriodMatchInput,
    RatingPeriodResult,
    RatingPeriodStatus,
    RatingState,
)

_RATING_QUANTUM = Decimal("0.00001")
_VOLATILITY_QUANTUM = Decimal("0.0000000001")
_SUPPORTED_ALGORITHM = "glicko2"
_LEGACY_ALGORITHM_VERSION = "1"
_CALENDAR_INACTIVITY_ALGORITHM_VERSION = "2"
_SUPPORTED_ALGORITHM_VERSIONS = {
    _LEGACY_ALGORITHM_VERSION,
    _CALENDAR_INACTIVITY_ALGORITHM_VERSION,
}


class RatingPeriodCalculator:
    """Calculate, but do not persist or publish, one sealed rating period."""

    async def calculate(
        self,
        uow: RatingsUnitOfWork,
        *,
        period_id: UUID,
    ) -> RatingPeriodCalculation:
        async with uow:
            return await self.calculate_in_uow(uow, period_id=period_id)

    async def calculate_in_uow(
        self,
        uow: RatingsUnitOfWork,
        *,
        period_id: UUID,
        locked_period: RatingPeriod | None = None,
    ) -> RatingPeriodCalculation:
        """Calculate through an active UoW, optionally reusing its locked period row."""
        period = locked_period or await uow.periods.get(period_id)
        if period is None or period.id != period_id:
            raise RatingPeriodNotFoundError
        if period.status is not RatingPeriodStatus.CALCULATING:
            raise RatingPeriodNotCalculatingError
        previous = await uow.periods.get_previous(period.starts_at)
        previous_version = self._normal_history_version(previous)
        return await self.calculate_version_in_uow(
            uow,
            period_id=period.id,
            calculation_version=period.calculation_version,
            predecessor_period=previous,
            predecessor_version=previous_version,
            locked_period=period,
        )

    async def calculate_version_in_uow(
        self,
        uow: RatingsUnitOfWork,
        *,
        period_id: UUID,
        calculation_version: int,
        predecessor_period: RatingPeriod | None,
        predecessor_version: int | None,
        locked_period: RatingPeriod | None = None,
    ) -> RatingPeriodCalculation:
        """Calculate an explicit immutable branch without selecting active versions implicitly."""
        period = locked_period or await uow.periods.get(period_id)
        if period is None or period.id != period_id:
            raise RatingPeriodNotFoundError
        if period.status is RatingPeriodStatus.OPEN:
            raise RatingPeriodNotCalculatingError
        if calculation_version < 1 or calculation_version > period.calculation_version:
            raise RatingHistoryUnavailableError("Requested rating input version is unavailable")
        if (predecessor_period is None) != (predecessor_version is None):
            raise RatingHistoryUnavailableError("Predecessor period and version must be selected")
        parameters = self.parameters_for_period(period)
        inputs = tuple(await uow.inputs.list_for_period(period.id, calculation_version))
        stored_states = await self._starting_states(
            uow,
            predecessor_period,
            predecessor_version,
        )

        games = self._games_by_player(inputs)
        for user_id in games:
            stored_states.setdefault(user_id, parameters.initial_state())

        strategy = Glicko2Strategy(parameters)
        calculation_states = self._calculation_states(
            stored_states,
            strategy,
            missing_periods=self._missing_calendar_periods(predecessor_period, period)
            if period.algorithm_version == _CALENDAR_INACTIVITY_ALGORITHM_VERSION
            else 0,
        )
        players: list[CalculatedPlayerRating] = []
        for user_id in sorted(stored_states, key=lambda value: value.int):
            stored_before = stored_states[user_id]
            calculation_before = calculation_states[user_id]
            opponents = tuple(
                RatingOpponent(
                    rating=calculation_states[opponent_id].rating,
                    rd=calculation_states[opponent_id].rd,
                    score=float(score),
                )
                for opponent_id, _, score in sorted(
                    games.get(user_id, ()),
                    key=lambda item: (item[0].int, item[1].int, item[2]),
                )
            )
            after = strategy.calculate(calculation_before, opponents)
            players.append(self._player_result(user_id, stored_before, after, len(opponents)))

        return RatingPeriodCalculation(
            period_id=period.id,
            calculation_version=calculation_version,
            players=tuple(players),
        )

    async def _starting_states(
        self,
        uow: RatingsUnitOfWork,
        previous: RatingPeriod | None,
        history_version: int | None,
    ) -> dict[UUID, RatingState]:
        if previous is None:
            return {}
        if history_version is None:
            raise RatingHistoryUnavailableError
        results = tuple(await uow.results.list_for_period(previous.id, history_version))
        previous_inputs = tuple(await uow.inputs.list_for_period(previous.id, history_version))
        result_users = {result.user_id for result in results}
        input_users = {
            user_id for item in previous_inputs for user_id in (item.player1_id, item.player2_id)
        }
        if not input_users.issubset(result_users):
            raise RatingHistoryUnavailableError("Previous rating period results are incomplete")
        return {
            result.user_id: self._state_from_result(result)
            for result in sorted(results, key=lambda item: item.user_id.int)
        }

    @staticmethod
    def _normal_history_version(previous: RatingPeriod | None) -> int | None:
        if previous is None:
            return None
        if previous.status not in {RatingPeriodStatus.CALCULATING, RatingPeriodStatus.PUBLISHED}:
            raise RatingHistoryUnavailableError(
                "The previous rating period has not completed calculation"
            )
        if previous.status is RatingPeriodStatus.PUBLISHED:
            if previous.published_version is None:
                raise RatingHistoryUnavailableError("Published rating period has no active version")
            return previous.published_version
        if previous.calculated_version != previous.calculation_version:
            raise RatingHistoryUnavailableError
        return previous.calculation_version

    @staticmethod
    def parameters_for_period(period: RatingPeriod) -> Glicko2Parameters:
        if (
            period.algorithm != _SUPPORTED_ALGORITHM
            or period.algorithm_version not in _SUPPORTED_ALGORITHM_VERSIONS
        ):
            raise UnsupportedRatingAlgorithmError
        names = (
            "initial_rating",
            "initial_rd",
            "initial_volatility",
            "tau",
            "max_rd",
            "convergence_epsilon",
            "max_iterations",
        )
        values = {name: period.parameters[name] for name in names if name in period.parameters}
        try:
            return Glicko2Parameters(**values)
        except (TypeError, ValueError) as exc:
            raise UnsupportedRatingAlgorithmError from exc

    @staticmethod
    def _missing_calendar_periods(
        previous: RatingPeriod | None,
        current: RatingPeriod,
    ) -> int:
        if previous is None:
            return 0
        previous_end = previous.ends_at
        current_start = current.starts_at
        if (
            previous_end.tzinfo is None
            or previous_end.utcoffset() is None
            or current_start.tzinfo is None
            or current_start.utcoffset() is None
        ):
            raise RatingHistoryUnavailableError("Rating period boundaries must be timezone-aware")
        gap = (current_start.astimezone(UTC).date() - previous_end.astimezone(UTC).date()).days
        if gap < 0:
            raise RatingHistoryUnavailableError("Rating periods overlap")
        return gap

    @staticmethod
    def _calculation_states(
        stored_states: dict[UUID, RatingState],
        strategy: Glicko2Strategy,
        *,
        missing_periods: int,
    ) -> dict[UUID, RatingState]:
        return {
            user_id: strategy.advance_inactivity(state, periods=missing_periods)
            for user_id, state in stored_states.items()
        }

    @staticmethod
    def _games_by_player(
        inputs: tuple[RatingPeriodMatchInput, ...],
    ) -> dict[UUID, list[tuple[UUID, UUID, Decimal]]]:
        games: dict[UUID, list[tuple[UUID, UUID, Decimal]]] = defaultdict(list)
        for item in inputs:
            games[item.player1_id].append((item.player2_id, item.match_id, item.score1))
            games[item.player2_id].append((item.player1_id, item.match_id, item.score2))
        return games

    @staticmethod
    def _state_from_result(result: RatingPeriodResult) -> RatingState:
        return RatingState(
            rating=float(result.rating_after),
            rd=float(result.rd_after),
            volatility=float(result.volatility_after),
        )

    @classmethod
    def _player_result(
        cls,
        user_id: UUID,
        before: RatingState,
        after: RatingState,
        games_count: int,
    ) -> CalculatedPlayerRating:
        return CalculatedPlayerRating(
            user_id=user_id,
            rating_before=cls._decimal(before.rating, _RATING_QUANTUM),
            rating_after=cls._decimal(after.rating, _RATING_QUANTUM),
            rd_before=cls._decimal(before.rd, _RATING_QUANTUM),
            rd_after=cls._decimal(after.rd, _RATING_QUANTUM),
            volatility_before=cls._decimal(before.volatility, _VOLATILITY_QUANTUM),
            volatility_after=cls._decimal(after.volatility, _VOLATILITY_QUANTUM),
            games_count=games_count,
            is_active=games_count > 0,
        )

    @staticmethod
    def _decimal(value: float, quantum: Decimal) -> Decimal:
        return Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_EVEN)

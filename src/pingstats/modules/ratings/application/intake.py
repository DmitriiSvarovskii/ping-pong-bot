"""Atomic registration of completed ranked matches for a UTC rating period."""

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from pingstats.modules.matches.application.dto import MatchView
from pingstats.modules.matches.domain import MatchStatus
from pingstats.modules.ratings.application.errors import (
    InvalidRatingMatchError,
    RatingInputConflictError,
    RatingPeriodUnavailableError,
)
from pingstats.modules.ratings.application.ports import (
    RatingInputRepository,
    RatingPeriodRepository,
)
from pingstats.modules.ratings.domain import (
    GLICKO2_RATING_SCALE,
    Glicko2Parameters,
    RatingPeriod,
    RatingPeriodMatchInput,
    RatingPeriodStatus,
)

RATING_ALGORITHM = "glicko2"
RATING_ALGORITHM_VERSION = "2"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RatingMatchInputRecorder:
    """Persist one immutable input in the match command's current transaction."""

    def __init__(
        self,
        *,
        parameters: Glicko2Parameters | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._parameters = parameters or Glicko2Parameters()
        self._clock = clock

    async def record(
        self,
        periods: RatingPeriodRepository,
        inputs: RatingInputRepository,
        view: MatchView,
        *,
        correction_revision_id: UUID | None = None,
    ) -> RatingPeriodMatchInput | None:
        if view.match.status is not MatchStatus.COMPLETED or not view.match.is_ranked:
            return None
        completed_at = view.match.completed_at
        if completed_at is None or completed_at.tzinfo is None:
            raise InvalidRatingMatchError("Completed ranked match must have a UTC-aware timestamp")
        if view.match.cancelled_at is not None:
            raise InvalidRatingMatchError("A cancelled match cannot be rated")
        if len(view.participants) != 2:
            raise InvalidRatingMatchError("A rated match must have exactly two participants")
        player1_id = view.user_id_at(1)
        player2_id = view.user_id_at(2)
        if player1_id == player2_id:
            raise InvalidRatingMatchError("A rated match must have two different participants")
        winner_id = view.match.winner_id
        if winner_id not in {player1_id, player2_id}:
            raise InvalidRatingMatchError("A rated match must have a participant as winner")
        if not view.sets:
            raise InvalidRatingMatchError("A rated match must contain a validated result")

        period_start = self.period_start(completed_at)
        await periods.acquire_period_lock(period_start)
        period = await periods.get_by_start(period_start, for_update=True)
        if period is None:
            period = await periods.add(self._new_period(period_start))
        if period.status is not RatingPeriodStatus.OPEN:
            raise RatingPeriodUnavailableError

        item = RatingPeriodMatchInput(
            period_id=period.id,
            calculation_version=period.calculation_version,
            match_id=view.match.id,
            source_match_version=view.match.version,
            correction_revision_id=correction_revision_id,
            result_fingerprint=self.fingerprint(view),
            player1_id=player1_id,
            player2_id=player2_id,
            score1=Decimal("1.00") if winner_id == player1_id else Decimal("0.00"),
            score2=Decimal("1.00") if winner_id == player2_id else Decimal("0.00"),
            completed_at=completed_at.astimezone(UTC),
            created_at=self._clock(),
        )
        stored, created = await inputs.add_if_absent(item)
        if created:
            return stored
        if (
            stored.source_match_version == item.source_match_version
            and stored.result_fingerprint == item.result_fingerprint
            and stored.correction_revision_id == item.correction_revision_id
        ):
            return stored
        raise RatingInputConflictError

    @staticmethod
    def period_start(completed_at: datetime) -> datetime:
        utc_completed_at = completed_at.astimezone(UTC)
        return utc_completed_at.replace(hour=0, minute=0, second=0, microsecond=0)

    @staticmethod
    def fingerprint(view: MatchView) -> str:
        completed_at = view.match.completed_at
        if completed_at is None or completed_at.tzinfo is None:
            raise InvalidRatingMatchError("Completed ranked match must have a UTC-aware timestamp")
        canonical = {
            "completed_at": completed_at.astimezone(UTC).isoformat(timespec="microseconds"),
            "match_id": str(view.match.id),
            "players": [str(view.user_id_at(1)), str(view.user_id_at(2))],
            "sets": [
                [item.player1_score, item.player2_score]
                for item in sorted(view.sets, key=lambda item: item.set_number)
            ],
            "winner_id": str(view.match.winner_id),
        }
        encoded = json.dumps(
            canonical,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    def _new_period(self, starts_at: datetime) -> RatingPeriod:
        now = self._clock()
        return RatingPeriod(
            id=uuid4(),
            starts_at=starts_at,
            ends_at=starts_at + timedelta(days=1),
            status=RatingPeriodStatus.OPEN,
            algorithm=RATING_ALGORITHM,
            algorithm_version=RATING_ALGORITHM_VERSION,
            parameters={
                "initial_rating": self._parameters.initial_rating,
                "initial_rd": self._parameters.initial_rd,
                "initial_volatility": self._parameters.initial_volatility,
                "tau": self._parameters.tau,
                "max_rd": self._parameters.max_rd,
                "convergence_epsilon": self._parameters.convergence_epsilon,
                "max_iterations": self._parameters.max_iterations,
                "rating_scale": GLICKO2_RATING_SCALE,
            },
            calculation_version=1,
            calculated_version=None,
            published_version=None,
            published_at=None,
            created_at=now,
            updated_at=now,
            version=1,
        )

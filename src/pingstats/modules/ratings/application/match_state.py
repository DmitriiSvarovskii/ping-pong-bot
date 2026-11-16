"""Read-only projection of a match's published rating state."""

from collections.abc import Mapping, Sequence
from enum import StrEnum
from uuid import UUID

from pingstats.modules.matches.domain import Match, MatchStatus
from pingstats.modules.ratings.application.ports import RatingQueryRepository


class MatchRatingState(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    PENDING = "pending"
    RATED = "rated"
    RECALCULATING = "recalculating"


class RatingMatchStateService:
    """Classify current match versions against the active published branch."""

    async def get_states(
        self,
        repository: RatingQueryRepository,
        matches: Sequence[Match],
    ) -> Mapping[UUID, MatchRatingState]:
        eligible = {
            match.id: match
            for match in matches
            if match.status is MatchStatus.COMPLETED and match.is_ranked
        }
        published_versions = await repository.get_published_match_versions(tuple(eligible))
        return {
            match.id: self._state(
                match,
                published_versions.get(match.id, frozenset()),
            )
            for match in matches
        }

    @staticmethod
    def _state(match: Match, published_versions: frozenset[int]) -> MatchRatingState:
        if match.status is not MatchStatus.COMPLETED or not match.is_ranked:
            return MatchRatingState.NOT_APPLICABLE
        if match.version in published_versions:
            return MatchRatingState.RATED
        if any(version < match.version for version in published_versions):
            return MatchRatingState.RECALCULATING
        return MatchRatingState.PENDING

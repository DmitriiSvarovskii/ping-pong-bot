"""Application tests for match rating-state projection."""

from datetime import UTC, datetime
from uuid import uuid4

from pingstats.modules.matches.domain import Match, MatchContext, MatchStatus
from pingstats.modules.ratings.application.match_state import (
    MatchRatingState,
    RatingMatchStateService,
)
from tests.unit.test_rating_queries import QueryRepository


def make_match(
    *,
    status: MatchStatus = MatchStatus.COMPLETED,
    is_ranked: bool = True,
    version: int = 3,
) -> Match:
    now = datetime(2026, 11, 16, tzinfo=UTC)
    return Match(
        id=uuid4(),
        context=MatchContext.STANDALONE,
        status=status,
        created_by=uuid4(),
        points_to_win=11,
        win_by=2,
        sets_to_win=1,
        is_ranked=is_ranked,
        winner_id=uuid4() if status is MatchStatus.COMPLETED else None,
        created_at=now,
        updated_at=now,
        started_at=now if status is not MatchStatus.PENDING else None,
        completed_at=now if status in {MatchStatus.COMPLETED, MatchStatus.VOIDED} else None,
        cancelled_at=now if status is MatchStatus.CANCELLED else None,
        version=version,
    )


async def test_match_rating_states_use_active_published_source_versions() -> None:
    rated = make_match()
    corrected = make_match()
    pending = make_match()
    unranked = make_match(is_ranked=False)
    voided = make_match(status=MatchStatus.VOIDED)
    cancelled = make_match(status=MatchStatus.CANCELLED)
    active = make_match(status=MatchStatus.ACTIVE)
    repository = QueryRepository(
        published_match_versions={
            rated.id: frozenset({rated.version}),
            corrected.id: frozenset({corrected.version - 1}),
            voided.id: frozenset({voided.version - 1}),
        }
    )

    states = await RatingMatchStateService().get_states(
        repository,
        (rated, corrected, pending, unranked, voided, cancelled, active),
    )

    assert states == {
        rated.id: MatchRatingState.RATED,
        corrected.id: MatchRatingState.RECALCULATING,
        pending.id: MatchRatingState.PENDING,
        unranked.id: MatchRatingState.NOT_APPLICABLE,
        voided.id: MatchRatingState.NOT_APPLICABLE,
        cancelled.id: MatchRatingState.NOT_APPLICABLE,
        active.id: MatchRatingState.NOT_APPLICABLE,
    }
    assert repository.published_match_version_calls == 1


async def test_atomic_cutover_changes_corrected_match_to_rated() -> None:
    corrected = make_match(version=4)
    repository = QueryRepository(published_match_versions={corrected.id: frozenset({3})})
    service = RatingMatchStateService()

    before = await service.get_states(repository, (corrected,))
    repository.published_match_versions[corrected.id] = frozenset({4})
    after = await service.get_states(repository, (corrected,))

    assert before[corrected.id] is MatchRatingState.RECALCULATING
    assert after[corrected.id] is MatchRatingState.RATED

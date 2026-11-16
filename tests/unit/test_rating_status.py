"""Application tests for the public rating update status."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from pingstats.modules.ratings.application.status import (
    RatingStatusFacts,
    RatingStatusService,
    RatingUpdateState,
)
from pingstats.modules.ratings.domain import RatingRebuildStatus
from tests.unit.test_rating_queries import QueryRepository


@pytest.mark.parametrize(
    ("facts", "expected"),
    (
        (
            RatingStatusFacts(2, True, RatingRebuildStatus.PREPARING, True),
            RatingUpdateState.DELAYED,
        ),
        (
            RatingStatusFacts(0, False, RatingRebuildStatus.FAILED, False),
            RatingUpdateState.DELAYED,
        ),
        (
            RatingStatusFacts(1, True, RatingRebuildStatus.PREPARING, False),
            RatingUpdateState.REBUILDING,
        ),
        (RatingStatusFacts(1, True, None, False), RatingUpdateState.CALCULATING),
        (RatingStatusFacts(1, False, None, False), RatingUpdateState.PENDING),
        (RatingStatusFacts(0, False, None, False), RatingUpdateState.UP_TO_DATE),
    ),
)
async def test_status_priority_and_next_calculation(
    facts: RatingStatusFacts,
    expected: RatingUpdateState,
) -> None:
    service = RatingStatusService(clock=lambda: datetime(2026, 11, 16, 12, tzinfo=UTC))

    view = await service.get_status(QueryRepository(status=facts), user_id=uuid4())

    assert view.state is expected
    assert view.pending_matches_count == facts.pending_matches_count
    assert view.next_calculation_at == datetime(2026, 11, 17, 0, 5, tzinfo=UTC)


async def test_next_calculation_uses_same_day_before_utc_deadline() -> None:
    service = RatingStatusService(clock=lambda: datetime(2026, 11, 16, 0, 4, tzinfo=UTC))

    view = await service.get_status(QueryRepository(), user_id=uuid4())

    assert view.next_calculation_at == datetime(2026, 11, 16, 0, 5, tzinfo=UTC)


async def test_next_calculation_moves_to_next_day_at_deadline() -> None:
    service = RatingStatusService(clock=lambda: datetime(2026, 11, 16, 0, 5, tzinfo=UTC))

    view = await service.get_status(QueryRepository(), user_id=uuid4())

    assert view.next_calculation_at == datetime(2026, 11, 17, 0, 5, tzinfo=UTC)

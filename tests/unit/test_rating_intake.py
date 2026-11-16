"""Unit tests for atomic rating match intake decisions."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from pingstats.modules.matches.application.dto import MatchView
from pingstats.modules.matches.domain import (
    Match,
    MatchContext,
    MatchParticipant,
    MatchSet,
    MatchStatus,
)
from pingstats.modules.ratings.application import (
    InvalidRatingMatchError,
    RatingInputConflictError,
    RatingMatchInputRecorder,
    RatingPeriodUnavailableError,
)
from pingstats.modules.ratings.domain import RatingPeriod, RatingPeriodStatus
from pingstats.modules.users.domain import User
from tests.unit.rating_fakes import FakeRatingInputRepository, FakeRatingPeriodRepository


def make_user(user_id: UUID, name: str) -> User:
    now = datetime.now(UTC)
    return User(user_id, None, name, None, name.lower(), None, now, now, None)


def make_view(
    *,
    status: MatchStatus = MatchStatus.COMPLETED,
    is_ranked: bool = True,
    completed_at: datetime | None = None,
    cancelled_at: datetime | None = None,
) -> MatchView:
    first_id, second_id, match_id = uuid4(), uuid4(), uuid4()
    finished_at = completed_at or datetime(2026, 11, 10, 12, 30, tzinfo=UTC)
    now = datetime.now(UTC)
    match = Match(
        id=match_id,
        context=MatchContext.STANDALONE,
        status=status,
        created_by=first_id,
        points_to_win=11,
        win_by=2,
        sets_to_win=1,
        is_ranked=is_ranked,
        winner_id=first_id if status is MatchStatus.COMPLETED else None,
        created_at=now,
        updated_at=now,
        started_at=now,
        completed_at=finished_at if status is MatchStatus.COMPLETED else None,
        cancelled_at=cancelled_at,
        version=2,
    )
    return MatchView(
        match=match,
        participants=(
            (MatchParticipant(uuid4(), match_id, first_id, 1), make_user(first_id, "First")),
            (MatchParticipant(uuid4(), match_id, second_id, 2), make_user(second_id, "Second")),
        ),
        sets=(MatchSet(uuid4(), match_id, 1, 11, 7, finished_at),),
    )


async def test_ranked_match_creates_one_stable_input_and_utc_period() -> None:
    periods = FakeRatingPeriodRepository()
    inputs = FakeRatingInputRepository()
    recorder = RatingMatchInputRecorder(clock=lambda: datetime(2026, 11, 10, 13, tzinfo=UTC))
    completed_at = datetime(2026, 11, 10, 2, 30, tzinfo=timezone(timedelta(hours=3)))
    view = make_view(completed_at=completed_at)

    first = await recorder.record(periods, inputs, view)
    repeated = await recorder.record(periods, inputs, view)

    assert first is not None and repeated == first
    assert len(periods.by_id) == len(inputs.items) == 1
    assert first.period_id == next(iter(periods.by_id))
    assert first.completed_at == datetime(2026, 11, 9, 23, 30, tzinfo=UTC)
    assert first.player1_id == view.user_id_at(1)
    assert first.player2_id == view.user_id_at(2)
    assert (first.score1, first.score2) == (1, 0)
    assert len(first.result_fingerprint) == 64
    assert periods.locked_starts == [
        datetime(2026, 11, 9, tzinfo=UTC),
        datetime(2026, 11, 9, tzinfo=UTC),
    ]


@pytest.mark.parametrize(
    "view",
    [
        make_view(is_ranked=False),
        make_view(status=MatchStatus.ACTIVE),
        make_view(
            status=MatchStatus.CANCELLED,
            cancelled_at=datetime(2026, 11, 10, 12, tzinfo=UTC),
        ),
    ],
    ids=["unranked", "active", "cancelled"],
)
async def test_ineligible_match_is_ignored(view: MatchView) -> None:
    periods = FakeRatingPeriodRepository()
    inputs = FakeRatingInputRepository()

    assert await RatingMatchInputRecorder().record(periods, inputs, view) is None
    assert not periods.by_id and not inputs.items


async def test_different_snapshot_for_existing_match_is_a_controlled_conflict() -> None:
    periods = FakeRatingPeriodRepository()
    inputs = FakeRatingInputRepository()
    recorder = RatingMatchInputRecorder()
    view = make_view()
    await recorder.record(periods, inputs, view)
    changed = replace(view, match=replace(view.match, version=view.match.version + 1))

    with pytest.raises(RatingInputConflictError):
        await recorder.record(periods, inputs, changed)
    assert len(inputs.items) == 1


async def test_non_open_period_rejects_late_intake() -> None:
    periods = FakeRatingPeriodRepository()
    inputs = FakeRatingInputRepository()
    recorder = RatingMatchInputRecorder()
    view = make_view()
    start = recorder.period_start(view.match.completed_at)  # type: ignore[arg-type]
    now = datetime.now(UTC)
    await periods.add(
        RatingPeriod(
            id=uuid4(),
            starts_at=start,
            ends_at=start + timedelta(days=1),
            status=RatingPeriodStatus.PUBLISHED,
            algorithm="glicko2",
            algorithm_version="2",
            parameters={},
            calculation_version=1,
            calculated_version=None,
            published_version=1,
            published_at=now,
            created_at=now,
            updated_at=now,
            version=2,
        )
    )

    with pytest.raises(RatingPeriodUnavailableError):
        await recorder.record(periods, inputs, view)
    assert not inputs.items


@pytest.mark.parametrize(
    "mutator",
    [
        lambda view: replace(view, participants=view.participants[:1]),
        lambda view: replace(view, match=replace(view.match, winner_id=uuid4())),
        lambda view: replace(view, sets=()),
    ],
    ids=["one-participant", "foreign-winner", "missing-result"],
)
async def test_invalid_completed_ranked_match_is_rejected(mutator) -> None:
    view = mutator(make_view())
    with pytest.raises(InvalidRatingMatchError):
        await RatingMatchInputRecorder().record(
            FakeRatingPeriodRepository(), FakeRatingInputRepository(), view
        )

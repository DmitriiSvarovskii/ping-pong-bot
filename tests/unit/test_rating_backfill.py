"""Application tests for safe historical rating backfill."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from pingstats.modules.matches.application.dto import MatchView
from pingstats.modules.matches.domain import MatchStatus
from pingstats.modules.ratings.application.backfill import (
    RatingBackfillBlockedError,
    RatingBackfillConflictError,
    RatingHistoricalBackfill,
)
from pingstats.modules.ratings.application.intake import RatingMatchInputRecorder
from pingstats.modules.ratings.domain import RatingPeriodStatus
from tests.unit.rating_fakes import FakeRatingInputRepository, FakeRatingPeriodRepository
from tests.unit.test_rating_intake import make_view


def historical_view(
    day: int,
    *,
    status: MatchStatus = MatchStatus.COMPLETED,
    is_ranked: bool = True,
) -> MatchView:
    completed_at = datetime(2026, 11, day, 12, tzinfo=UTC)
    view = make_view(completed_at=completed_at, is_ranked=is_ranked)
    return replace(
        view,
        match=replace(
            view.match,
            status=status,
            winner_id=view.user_id_at(1) if status is MatchStatus.COMPLETED else None,
            completed_at=completed_at,
        ),
    )


class MatchRepository:
    def __init__(self, rows: tuple[tuple[MatchView, UUID | None], ...]) -> None:
        self.rows = tuple(
            sorted(rows, key=lambda row: (row[0].match.completed_at, row[0].match.id))
        )

    async def list_for_rating_backfill(
        self,
        *,
        after_completed_at,
        after_match_id,
        completed_from,
        completed_before,
        limit,
    ):
        rows = self.rows
        if completed_from is not None:
            rows = tuple(row for row in rows if row[0].match.completed_at >= completed_from)
        if completed_before is not None:
            rows = tuple(row for row in rows if row[0].match.completed_at < completed_before)
        if after_completed_at is not None:
            rows = tuple(
                row
                for row in rows
                if (row[0].match.completed_at, row[0].match.id)
                > (after_completed_at, after_match_id)
            )
        return rows[:limit]


class RebuildJobs:
    def __init__(self, barrier=None) -> None:
        self.barrier = barrier

    async def get_publication_barrier(self):
        return self.barrier


class BackfillUnitOfWork:
    def __init__(self, rows, *, barrier=None) -> None:
        self.matches = MatchRepository(rows)
        self.periods = FakeRatingPeriodRepository()
        self.inputs = FakeRatingInputRepository()
        self.rebuild_jobs = RebuildJobs(barrier)
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        pass


class Workflow:
    def __init__(self) -> None:
        self.calls = 0

    async def run_periods(self, factory, *, starts_at):
        del factory, starts_at
        self.calls += 1
        return ()


async def test_dry_run_reports_eligible_skipped_periods_and_players_without_writes() -> None:
    ranked = historical_view(1)
    unranked = historical_view(2, is_ranked=False)
    voided = historical_view(3, status=MatchStatus.VOIDED)
    cancelled = historical_view(4, status=MatchStatus.CANCELLED)
    uow = BackfillUnitOfWork(
        ((ranked, None), (unranked, None), (voided, uuid4()), (cancelled, None))
    )
    workflow = Workflow()

    report = await RatingHistoricalBackfill(
        workflow=workflow,
        batch_size=2,
    ).run(lambda: uow, dry_run=True)

    assert report.scanned_matches == 4
    assert report.eligible_matches == report.processed_matches == 1
    assert report.skipped_matches == 3
    assert report.conflicted_matches == 0
    assert report.periods == report.created_periods == 1
    assert report.players == 2
    assert not uow.periods.by_id and not uow.inputs.items
    assert uow.commits == workflow.calls == 0


async def test_apply_uses_current_revision_has_calendar_gaps_and_is_idempotent() -> None:
    first = historical_view(1)
    second = historical_view(10)
    revision_id = uuid4()
    uow = BackfillUnitOfWork(((first, revision_id), (second, None)))
    workflow = Workflow()
    backfill = RatingHistoricalBackfill(workflow=workflow)

    created = await backfill.run(lambda: uow, dry_run=False)
    repeated = await backfill.run(lambda: uow, dry_run=False)

    assert created.processed_matches == 2
    assert created.created_periods == 2
    assert len(uow.periods.by_id) == 2
    assert len(uow.inputs.items) == 2
    assert {period.starts_at.day for period in uow.periods.by_id.values()} == {1, 10}
    first_input = next(
        item for item in uow.inputs.items.values() if item.match_id == first.match.id
    )
    assert first_input.correction_revision_id == revision_id
    assert repeated.processed_matches == 0
    assert repeated.skipped_matches == 2
    assert len(uow.inputs.items) == 2
    assert uow.commits == workflow.calls == 2


async def test_missing_input_in_published_period_is_controlled_conflict() -> None:
    view = historical_view(1)
    uow = BackfillUnitOfWork(((view, None),))
    await RatingMatchInputRecorder().record(uow.periods, uow.inputs, view)
    uow.inputs.items.clear()
    recorder = RatingHistoricalBackfill()
    period_start = RatingMatchInputRecorder.period_start(view.match.completed_at)
    period = uow.periods.by_start[period_start]
    published = replace(
        period,
        status=RatingPeriodStatus.PUBLISHED,
        calculated_version=1,
        published_version=1,
        published_at=period.ends_at,
    )
    uow.periods.by_start[period_start] = published
    uow.periods.by_id[period.id] = published

    with pytest.raises(RatingBackfillConflictError) as raised:
        await recorder.run(lambda: uow, dry_run=False)

    assert raised.value.report.conflicted_matches == 1
    assert not uow.inputs.items
    assert uow.commits == 0


async def test_active_rebuild_blocks_backfill_before_scan() -> None:
    uow = BackfillUnitOfWork(((historical_view(1), None),), barrier=object())

    with pytest.raises(RatingBackfillBlockedError):
        await RatingHistoricalBackfill().run(lambda: uow, dry_run=True)

    assert not uow.inputs.items


async def test_missing_period_before_published_horizon_is_fail_closed() -> None:
    historical = historical_view(1)
    uow = BackfillUnitOfWork(((historical, None),))
    later = historical_view(10)
    await RatingMatchInputRecorder().record(uow.periods, uow.inputs, later)
    later_start = RatingMatchInputRecorder.period_start(later.match.completed_at)
    period = uow.periods.by_start[later_start]
    published = replace(
        period,
        status=RatingPeriodStatus.PUBLISHED,
        calculated_version=1,
        published_version=1,
        published_at=period.ends_at,
    )
    uow.periods.by_start[later_start] = published
    uow.periods.by_id[period.id] = published

    with pytest.raises(RatingBackfillConflictError) as raised:
        await RatingHistoricalBackfill().run(lambda: uow, dry_run=True)

    assert raised.value.report.conflicted_matches == 1
    assert RatingMatchInputRecorder.period_start(historical.match.completed_at) not in (
        uow.periods.by_start
    )

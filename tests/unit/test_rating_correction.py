"""Rating correction coordination tests."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from pingstats.modules.matches.application.dto import MatchView
from pingstats.modules.matches.domain import (
    Match,
    MatchContext,
    MatchParticipant,
    MatchResultRevision,
    MatchRevisionOperation,
    MatchSet,
    MatchStatus,
    ResultSnapshot,
)
from pingstats.modules.ratings.application import (
    RatingCorrectionCoordinator,
    RatingCorrectionHistoryError,
)
from pingstats.modules.ratings.domain import (
    RatingPeriodStatus,
    RatingRebuildJob,
    RatingRebuildPeriod,
    RatingRebuildStatus,
    RatingRecalculationRequest,
)
from pingstats.modules.users.domain import User
from tests.unit.rating_fakes import FakeRatingInputRepository, FakeRatingPeriodRepository
from tests.unit.test_rating_period_calculator import make_input, make_period

NOW = datetime(2026, 11, 15, 12, tzinfo=UTC)


class FakeRecalculationRepository:
    def __init__(self) -> None:
        self.items: dict[UUID, RatingRecalculationRequest] = {}

    async def add(self, request):
        self.items[request.id] = request
        return request

    async def get_by_revision(self, revision_id):
        return next(
            (item for item in self.items.values() if item.caused_by_revision_id == revision_id),
            None,
        )

    async def list_for_job(self, rebuild_job_id):
        return tuple(item for item in self.items.values() if item.rebuild_job_id == rebuild_job_id)

    async def list_by_status(self, status):
        return tuple(item for item in self.items.values() if item.status is status)

    async def link_to_job(self, *, request_id, rebuild_job_id, expected_rebuild_job_id):
        item = self.items[request_id]
        if item.rebuild_job_id != expected_rebuild_job_id:
            return None
        updated = replace(item, rebuild_job_id=rebuild_job_id)
        self.items[item.id] = updated
        return updated

    async def update_status(self, *, request_id, status, started_at, completed_at, error_code):
        item = self.items.get(request_id)
        if item is None:
            return None
        updated = replace(
            item,
            status=status,
            started_at=started_at,
            completed_at=completed_at,
            error_code=error_code,
        )
        self.items[item.id] = updated
        return updated


class FakeRebuildJobRepository:
    def __init__(self) -> None:
        self.items: dict[UUID, RatingRebuildJob] = {}

    async def add(self, job):
        self.items[job.id] = job
        return job

    async def get_active(self, *, for_update=False):
        del for_update
        return next(
            (
                job
                for job in self.items.values()
                if job.status
                in {
                    RatingRebuildStatus.PENDING,
                    RatingRebuildStatus.PREPARING,
                    RatingRebuildStatus.READY,
                    RatingRebuildStatus.ACTIVATING,
                    RatingRebuildStatus.FAILED,
                }
            ),
            None,
        )

    async def get(self, job_id, *, for_update=False):
        del for_update
        return self.items.get(job_id)

    async def get_publication_barrier(self):
        return await self.get_active()

    async def update_status(
        self,
        *,
        job_id,
        status,
        expected_version,
        completed_at,
        error_code,
        started_at=None,
        ready_at=None,
    ):
        job = self.items[job_id]
        if job.version != expected_version:
            return None
        updated = replace(
            job,
            status=status,
            completed_at=completed_at,
            error_code=error_code,
            started_at=started_at if started_at is not None else job.started_at,
            ready_at=ready_at if ready_at is not None else job.ready_at,
            version=job.version + 1,
        )
        self.items[job_id] = updated
        return updated


class FakeRebuildPeriodRepository:
    def __init__(self) -> None:
        self.items: dict[tuple[UUID, UUID], RatingRebuildPeriod] = {}

    async def add(self, checkpoint):
        self.items[(checkpoint.rebuild_job_id, checkpoint.period_id)] = checkpoint
        return checkpoint

    async def get(self, rebuild_job_id, period_id, *, for_update=False):
        del for_update
        return self.items.get((rebuild_job_id, period_id))

    async def list_for_job(self, rebuild_job_id):
        return tuple(item for (job_id, _), item in self.items.items() if job_id == rebuild_job_id)

    async def update_status(
        self,
        *,
        rebuild_job_id,
        period_id,
        status,
        expected_status,
        completed_at,
        error_code,
    ):
        key = (rebuild_job_id, period_id)
        item = self.items.get(key)
        if item is None or item.status is not expected_status:
            return None
        updated = replace(
            item,
            status=status,
            completed_at=completed_at,
            error_code=error_code,
        )
        self.items[key] = updated
        return updated


def make_match(period, *, winner_position: int = 2) -> MatchView:
    first, second, match_id = uuid4(), uuid4(), uuid4()
    users = tuple(
        User(user_id, None, name, None, None, None, NOW, NOW, None)
        for user_id, name in ((first, "First"), (second, "Second"))
    )
    participants = tuple(
        (MatchParticipant(uuid4(), match_id, user.id, position), user)
        for position, user in enumerate(users, start=1)
    )
    winner = participants[winner_position - 1][0].user_id
    match = Match(
        id=match_id,
        context=MatchContext.STANDALONE,
        status=MatchStatus.COMPLETED,
        created_by=first,
        points_to_win=11,
        win_by=2,
        sets_to_win=1,
        is_ranked=True,
        winner_id=winner,
        created_at=period.starts_at,
        updated_at=NOW,
        started_at=period.starts_at,
        completed_at=period.starts_at.replace(hour=12),
        cancelled_at=None,
        version=3,
    )
    score = (4, 11) if winner_position == 2 else (11, 4)
    return MatchView(
        match,
        participants,
        (MatchSet(uuid4(), match_id, 1, *score, NOW),),
    )


def make_revision(view: MatchView, operation=MatchRevisionOperation.CORRECTION):
    snapshot = ResultSnapshot(
        sets=tuple(item.score() for item in view.sets),
        winner_id=view.match.winner_id,
        status=view.match.status,
        completed_at=view.match.completed_at,
        match_version=view.match.version,
    )
    return MatchResultRevision(
        uuid4(), view.match.id, 1, view.match.created_by, snapshot, snapshot, None, NOW, operation
    )


def repositories():
    return (
        FakeRatingPeriodRepository(),
        FakeRatingInputRepository(),
        FakeRecalculationRepository(),
        FakeRebuildJobRepository(),
        FakeRebuildPeriodRepository(),
    )


async def coordinate(coordinator, repos, view, revision):
    periods, inputs, requests, jobs, checkpoints = repos
    await coordinator.coordinate(
        periods=periods,
        inputs=inputs,
        recalculations=requests,
        rebuild_jobs=jobs,
        rebuild_periods=checkpoints,
        match=view,
        revision=revision,
    )


async def test_unpublished_correction_copies_full_snapshot_and_invalidates_marker() -> None:
    repos = repositories()
    periods, inputs, *_ = repos
    period = replace(make_period(15, calculated=True), status=RatingPeriodStatus.CALCULATING)
    await periods.add(period)
    view = make_match(period)
    other = make_input(period, uuid4(), uuid4())
    original = make_input(
        period,
        view.user_id_at(1),
        view.user_id_at(2),
        match_id=view.match.id,
    )
    await inputs.add_if_absent(original)
    await inputs.add_if_absent(other)

    await coordinate(
        RatingCorrectionCoordinator(clock=lambda: NOW), repos, view, make_revision(view)
    )

    updated = periods.by_id[period.id]
    target = await inputs.list_for_period(period.id, 2)
    assert updated.calculation_version == 2 and updated.calculated_version is None
    assert {item.match_id for item in target} == {view.match.id, other.match_id}
    corrected = next(item for item in target if item.match_id == view.match.id)
    assert corrected.source_match_version == view.match.version
    assert corrected.correction_revision_id is not None
    assert len(await inputs.list_for_period(period.id, 1)) == 2


async def test_unpublished_void_excludes_match_and_missing_input_is_rejected() -> None:
    repos = repositories()
    periods, inputs, *_ = repos
    period = make_period(15, status=RatingPeriodStatus.OPEN)
    await periods.add(period)
    completed = make_match(period)
    view = replace(
        completed,
        match=replace(completed.match, status=MatchStatus.VOIDED, winner_id=None),
    )
    # Keep one real source input for the exact match.
    await inputs.add_if_absent(
        make_input(period, view.user_id_at(1), view.user_id_at(2), match_id=view.match.id)
    )
    await coordinate(
        RatingCorrectionCoordinator(clock=lambda: NOW),
        repos,
        view,
        make_revision(view, MatchRevisionOperation.VOID),
    )
    assert await inputs.list_for_period(period.id, 2) == ()

    missing = make_match(period)
    with pytest.raises(RatingCorrectionHistoryError):
        await coordinate(
            RatingCorrectionCoordinator(clock=lambda: NOW),
            repos,
            missing,
            make_revision(missing),
        )


async def test_published_correction_creates_and_supersedes_durable_rebuild() -> None:
    repos = repositories()
    periods, inputs, requests, jobs, checkpoints = repos
    first = replace(
        make_period(14, calculated=True),
        status=RatingPeriodStatus.PUBLISHED,
        published_version=1,
        published_at=NOW,
    )
    second = make_period(15, calculated=True)
    await periods.add(first)
    await periods.add(second)
    view = make_match(first)
    await inputs.add_if_absent(
        make_input(first, view.user_id_at(1), view.user_id_at(2), match_id=view.match.id)
    )
    coordinator = RatingCorrectionCoordinator(clock=lambda: NOW)
    first_revision = make_revision(view)
    await coordinate(coordinator, repos, view, first_revision)
    first_job = await jobs.get_active()
    assert first_job is not None and first_job.through_period_id == second.id
    assert len(checkpoints.items) == 2
    assert periods.by_id[first.id].published_version == 1

    second_revision = replace(first_revision, id=uuid4(), revision_number=2)
    await coordinate(coordinator, repos, view, second_revision)
    second_job = await jobs.get_active()
    assert second_job is not None and second_job.id != first_job.id
    assert jobs.items[first_job.id].status is RatingRebuildStatus.SUPERSEDED
    assert periods.by_id[first.id].calculation_version == 3
    assert all(item.rebuild_job_id == second_job.id for item in requests.items.values())
    assert {item.target_version for item in checkpoints.items.values()} == {2, 3}
    second_checkpoint = checkpoints.items[(second_job.id, second.id)]
    assert second_checkpoint.source_version == 1
    assert second_checkpoint.target_version == 3

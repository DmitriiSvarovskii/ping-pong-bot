"""Application tests for restart-safe shadow rating calculation."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from pingstats.modules.matches.domain import (
    MatchResultRevision,
    MatchRevisionOperation,
    MatchStatus,
)
from pingstats.modules.ratings.application import (
    RatingCorrectionCoordinator,
    RatingCutoverConflictError,
    RatingRebuildCutover,
    RatingRebuildExecutionError,
    RatingRebuildExecutor,
)
from pingstats.modules.ratings.domain import (
    PublishedRating,
    RatingPeriodStatus,
    RatingRebuildPeriodStatus,
    RatingRebuildStatus,
    RatingRecalculationStatus,
)
from tests.unit.rating_fakes import (
    FakeRatingInputRepository,
    FakeRatingPeriodRepository,
    FakeRatingRepository,
    FakeRatingResultRepository,
)
from tests.unit.test_rating_correction import (
    FakeRebuildJobRepository,
    FakeRebuildPeriodRepository,
    FakeRecalculationRepository,
    make_match,
    make_revision,
)
from tests.unit.test_rating_period_calculator import make_input, make_period, make_result

NOW = datetime(2026, 11, 20, 12, tzinfo=UTC)
CUTOVER_AT = datetime(2026, 11, 21, 1, tzinfo=UTC)


class FakeMatchRepository:
    def __init__(self) -> None:
        self.views = {}
        self.revisions: dict[UUID, tuple[MatchResultRevision, ...]] = {}

    async def get_view(self, match_id, *, for_update=False):
        del for_update
        return self.views.get(match_id)

    async def list_revisions(self, match_id, *, limit, offset):
        items = self.revisions.get(match_id, ())
        return items[offset : offset + limit], len(items)


class FakeRatingsUnitOfWork:
    def __init__(self) -> None:
        self.matches = FakeMatchRepository()
        self.ratings = FakeRatingRepository()
        self.periods = FakeRatingPeriodRepository()
        self.inputs = FakeRatingInputRepository()
        self.results = FakeRatingResultRepository()
        self.recalculations = FakeRecalculationRepository()
        self.rebuild_jobs = FakeRebuildJobRepository()
        self.rebuild_periods = FakeRebuildPeriodRepository()
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        pass


async def _published_period(uow: FakeRatingsUnitOfWork, day: int):
    period = replace(
        make_period(day, calculated=True),
        status=RatingPeriodStatus.PUBLISHED,
        published_version=1,
        published_at=NOW,
    )
    await uow.periods.add(period)
    return period


async def test_rebuild_copies_current_matches_calculates_chain_and_is_idempotent() -> None:
    uow = FakeRatingsUnitOfWork()
    previous = await _published_period(uow, 10)
    first = await _published_period(uow, 14)
    second = await _published_period(uow, 18)
    first_view = make_match(first)
    second_view = make_match(second, winner_position=1)
    first_revision = make_revision(first_view)
    uow.matches.views = {
        first_view.match.id: first_view,
        second_view.match.id: second_view,
    }
    uow.matches.revisions[first_view.match.id] = (first_revision,)
    await uow.inputs.add_if_absent(
        make_input(
            first,
            first_view.user_id_at(1),
            first_view.user_id_at(2),
            match_id=first_view.match.id,
        )
    )
    await uow.inputs.add_if_absent(
        make_input(
            second,
            second_view.user_id_at(1),
            second_view.user_id_at(2),
            match_id=second_view.match.id,
        )
    )
    previous_users = first_view.participant_ids()
    uow.results.items.extend(
        make_result(previous, user_id, rd="80.00000") for user_id in previous_users
    )
    uow.ratings.by_user[first_view.user_id_at(1)] = PublishedRating(
        user_id=first_view.user_id_at(1),
        rating=make_result(previous, first_view.user_id_at(1)).rating_after,
        rd=make_result(previous, first_view.user_id_at(1)).rd_after,
        volatility=make_result(previous, first_view.user_id_at(1)).volatility_after,
        as_of_period_id=previous.id,
        rated_matches_count=1,
        updated_at=NOW,
        version=1,
    )
    published_ratings = dict(uow.ratings.by_user)

    await RatingCorrectionCoordinator(clock=lambda: NOW).coordinate(
        periods=uow.periods,
        inputs=uow.inputs,
        recalculations=uow.recalculations,
        rebuild_jobs=uow.rebuild_jobs,
        rebuild_periods=uow.rebuild_periods,
        match=first_view,
        revision=first_revision,
    )
    job = await uow.rebuild_jobs.get_active()
    assert job is not None

    executor = RatingRebuildExecutor(clock=lambda: NOW)
    ready = await executor.run(lambda: uow, job_id=job.id)
    repeated = await executor.run(lambda: uow, job_id=job.id)

    assert ready.status is repeated.status is RatingRebuildStatus.READY
    checkpoints = await uow.rebuild_periods.list_for_job(job.id)
    assert all(item.status is RatingRebuildPeriodStatus.READY for item in checkpoints)
    first_target = await uow.inputs.list_for_period(first.id, 2)
    assert (
        first_target[0].result_fingerprint
        != (await uow.inputs.list_for_period(first.id, 1))[0].result_fingerprint
    )
    assert first_target[0].correction_revision_id == first_revision.id
    assert len(await uow.results.list_for_period(first.id, 2)) == 2
    second_results = await uow.results.list_for_period(second.id, 2)
    assert previous_users.issubset({item.user_id for item in second_results})
    assert uow.periods.by_id[first.id].published_version == 1
    assert uow.periods.by_id[second.id].published_version == 1
    assert uow.ratings.by_user == published_ratings

    source_inputs = []
    target_inputs = []
    for item_period in (first, second):
        source_inputs.extend(await uow.inputs.list_for_period(item_period.id, 1))
        target_inputs.extend(await uow.inputs.list_for_period(item_period.id, 2))
    source_games = {
        user_id: sum(1 for item in source_inputs if user_id in {item.player1_id, item.player2_id})
        for user_id in uow.ratings.by_user
    }
    target_games = {
        user_id: sum(1 for item in target_inputs if user_id in {item.player1_id, item.player2_id})
        for user_id in uow.ratings.by_user
    }
    commits_before = uow.commits
    cutover = RatingRebuildCutover(clock=lambda: CUTOVER_AT)

    completed = await cutover.activate(uow, job_id=job.id)
    repeated_cutover = await cutover.activate(uow, job_id=job.id)

    assert completed.status is repeated_cutover.status is RatingRebuildStatus.COMPLETED
    assert uow.commits == commits_before + 1
    assert uow.periods.by_id[first.id].published_version == 2
    assert uow.periods.by_id[second.id].published_version == 2
    assert uow.periods.by_id[first.id].published_at == NOW
    assert uow.periods.by_id[second.id].published_at == NOW
    assert all(
        item.status is RatingRecalculationStatus.COMPLETED
        for item in uow.recalculations.items.values()
    )
    for user_id, old in published_ratings.items():
        current = uow.ratings.by_user[user_id]
        assert current.rated_matches_count == (
            old.rated_matches_count - source_games[user_id] + target_games[user_id]
        )
        assert current.as_of_period_id == second.id


async def test_voided_first_period_produces_empty_shadow_snapshot() -> None:
    uow = FakeRatingsUnitOfWork()
    period = await _published_period(uow, 14)
    completed = make_match(period)
    voided = replace(
        completed,
        match=replace(completed.match, status=MatchStatus.VOIDED, winner_id=None),
    )
    revision = make_revision(voided, MatchRevisionOperation.VOID)
    uow.matches.views[voided.match.id] = voided
    uow.matches.revisions[voided.match.id] = (revision,)
    await uow.inputs.add_if_absent(
        make_input(
            period,
            voided.user_id_at(1),
            voided.user_id_at(2),
            match_id=voided.match.id,
        )
    )
    for user_id in voided.participant_ids():
        result = make_result(period, user_id)
        uow.results.items.append(result)
        uow.ratings.by_user[user_id] = PublishedRating(
            user_id=user_id,
            rating=result.rating_after,
            rd=result.rd_after,
            volatility=result.volatility_after,
            as_of_period_id=period.id,
            rated_matches_count=1,
            updated_at=NOW,
            version=1,
        )
    await RatingCorrectionCoordinator(clock=lambda: NOW).coordinate(
        periods=uow.periods,
        inputs=uow.inputs,
        recalculations=uow.recalculations,
        rebuild_jobs=uow.rebuild_jobs,
        rebuild_periods=uow.rebuild_periods,
        match=voided,
        revision=revision,
    )
    job = await uow.rebuild_jobs.get_active()
    assert job is not None

    await RatingRebuildExecutor(clock=lambda: NOW).run(lambda: uow, job_id=job.id)

    assert await uow.inputs.list_for_period(period.id, 2) == ()
    assert await uow.results.list_for_period(period.id, 2) == ()
    assert uow.periods.by_id[period.id].calculated_version == 2

    await RatingRebuildCutover(clock=lambda: CUTOVER_AT).activate(uow, job_id=job.id)

    assert uow.ratings.by_user == {}
    assert uow.periods.by_id[period.id].published_version == 2


async def test_superseded_job_is_never_executed() -> None:
    uow = FakeRatingsUnitOfWork()
    period = await _published_period(uow, 14)
    view = make_match(period)
    revision = make_revision(view)
    uow.matches.views[view.match.id] = view
    await uow.inputs.add_if_absent(
        make_input(period, view.user_id_at(1), view.user_id_at(2), match_id=view.match.id)
    )
    await RatingCorrectionCoordinator(clock=lambda: NOW).coordinate(
        periods=uow.periods,
        inputs=uow.inputs,
        recalculations=uow.recalculations,
        rebuild_jobs=uow.rebuild_jobs,
        rebuild_periods=uow.rebuild_periods,
        match=view,
        revision=revision,
    )
    job = await uow.rebuild_jobs.get_active()
    assert job is not None
    uow.rebuild_jobs.items[job.id] = replace(job, status=RatingRebuildStatus.SUPERSEDED)

    with pytest.raises(RatingRebuildExecutionError):
        await RatingRebuildExecutor(clock=lambda: NOW).run(lambda: uow, job_id=job.id)


async def test_cutover_rejects_incomplete_horizon_without_mutation() -> None:
    uow = FakeRatingsUnitOfWork()
    period = await _published_period(uow, 14)
    view = make_match(period)
    revision = make_revision(view)
    uow.matches.views[view.match.id] = view
    uow.matches.revisions[view.match.id] = (revision,)
    await uow.inputs.add_if_absent(
        make_input(period, view.user_id_at(1), view.user_id_at(2), match_id=view.match.id)
    )
    await RatingCorrectionCoordinator(clock=lambda: NOW).coordinate(
        periods=uow.periods,
        inputs=uow.inputs,
        recalculations=uow.recalculations,
        rebuild_jobs=uow.rebuild_jobs,
        rebuild_periods=uow.rebuild_periods,
        match=view,
        revision=revision,
    )
    job = await uow.rebuild_jobs.get_active()
    assert job is not None
    await RatingRebuildExecutor(clock=lambda: NOW).run(lambda: uow, job_id=job.id)
    ready_job = uow.rebuild_jobs.items[job.id]
    uow.rebuild_jobs.items[job.id] = replace(ready_job, status=RatingRebuildStatus.SUPERSEDED)

    with pytest.raises(RatingCutoverConflictError):
        await RatingRebuildCutover(clock=lambda: CUTOVER_AT).activate(uow, job_id=job.id)

    uow.rebuild_jobs.items[job.id] = ready_job
    uow.rebuild_periods.items.clear()

    with pytest.raises(RatingCutoverConflictError, match="horizon is incomplete"):
        await RatingRebuildCutover(clock=lambda: CUTOVER_AT).activate(uow, job_id=job.id)

    assert uow.rebuild_jobs.items[job.id].status is RatingRebuildStatus.READY
    assert uow.periods.by_id[period.id].published_version == 1


async def test_rebuild_resumes_from_pending_checkpoint_after_failure() -> None:
    uow = FakeRatingsUnitOfWork()
    period = await _published_period(uow, 14)
    view = make_match(period)
    revision = make_revision(view)
    await uow.inputs.add_if_absent(
        make_input(period, view.user_id_at(1), view.user_id_at(2), match_id=view.match.id)
    )
    await RatingCorrectionCoordinator(clock=lambda: NOW).coordinate(
        periods=uow.periods,
        inputs=uow.inputs,
        recalculations=uow.recalculations,
        rebuild_jobs=uow.rebuild_jobs,
        rebuild_periods=uow.rebuild_periods,
        match=view,
        revision=revision,
    )
    job = await uow.rebuild_jobs.get_active()
    assert job is not None
    executor = RatingRebuildExecutor(clock=lambda: NOW)

    with pytest.raises(RatingRebuildExecutionError, match="source rating match"):
        await executor.run(lambda: uow, job_id=job.id)
    checkpoint = await uow.rebuild_periods.get(job.id, period.id)
    assert checkpoint is not None and checkpoint.status is RatingRebuildPeriodStatus.PENDING
    assert uow.rebuild_jobs.items[job.id].status is RatingRebuildStatus.PREPARING

    uow.matches.views[view.match.id] = view
    uow.matches.revisions[view.match.id] = (revision,)
    resumed = await executor.run(lambda: uow, job_id=job.id)

    assert resumed.status is RatingRebuildStatus.READY

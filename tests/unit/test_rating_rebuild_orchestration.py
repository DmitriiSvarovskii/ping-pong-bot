"""Unit tests for automatic rating rebuild orchestration."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from pingstats.modules.ratings.application import (
    RatingRebuildExecutionError,
    RatingRebuildOrchestrator,
)
from pingstats.modules.ratings.domain import RatingRebuildJob, RatingRebuildStatus
from pingstats.modules.ratings.infrastructure.scheduler import RatingPeriodScheduler
from tests.unit.test_rating_rebuild import FakeRatingsUnitOfWork

NOW = datetime(2026, 11, 21, 12, tzinfo=UTC)


def make_job(status: RatingRebuildStatus) -> RatingRebuildJob:
    period_id = uuid4()
    return RatingRebuildJob(
        id=uuid4(),
        from_period_id=period_id,
        through_period_id=period_id,
        status=status,
        singleton_scope=1,
        created_at=NOW,
        started_at=NOW if status is not RatingRebuildStatus.PENDING else None,
        ready_at=NOW if status is RatingRebuildStatus.READY else None,
        completed_at=NOW if status is RatingRebuildStatus.FAILED else None,
        error_code="old_failure" if status is RatingRebuildStatus.FAILED else None,
        version=1,
    )


class CompletingExecutor:
    def __init__(self, uow: FakeRatingsUnitOfWork) -> None:
        self.uow = uow
        self.calls = 0

    async def run(self, uow_factory, *, job_id):
        del uow_factory
        self.calls += 1
        current = self.uow.rebuild_jobs.items[job_id]
        ready = replace(
            current,
            status=RatingRebuildStatus.READY,
            ready_at=NOW,
            version=current.version + 1,
        )
        self.uow.rebuild_jobs.items[job_id] = ready
        return ready


class CompletingCutover:
    def __init__(self, uow: FakeRatingsUnitOfWork) -> None:
        self.uow = uow
        self.calls = 0

    async def activate(self, passed_uow, *, job_id):
        del passed_uow
        self.calls += 1
        current = self.uow.rebuild_jobs.items[job_id]
        completed = replace(
            current,
            status=RatingRebuildStatus.COMPLETED,
            completed_at=NOW,
            version=current.version + 1,
        )
        self.uow.rebuild_jobs.items[job_id] = completed
        return completed


@pytest.mark.parametrize(
    "initial_status",
    [RatingRebuildStatus.PENDING, RatingRebuildStatus.PREPARING],
)
async def test_startup_recovery_executes_and_activates_rebuild(initial_status) -> None:
    uow = FakeRatingsUnitOfWork()
    job = make_job(initial_status)
    await uow.rebuild_jobs.add(job)
    executor = CompletingExecutor(uow)
    cutover = CompletingCutover(uow)

    result = await RatingRebuildOrchestrator(
        executor=executor,
        cutover=cutover,
        clock=lambda: NOW,
    ).run_once(lambda: uow)

    assert result is not None and result.status is RatingRebuildStatus.COMPLETED
    assert executor.calls == cutover.calls == 1


async def test_ready_job_goes_directly_to_cutover() -> None:
    uow = FakeRatingsUnitOfWork()
    job = make_job(RatingRebuildStatus.READY)
    await uow.rebuild_jobs.add(job)
    executor = CompletingExecutor(uow)
    cutover = CompletingCutover(uow)

    result = await RatingRebuildOrchestrator(
        executor=executor, cutover=cutover, clock=lambda: NOW
    ).run_once(lambda: uow)

    assert result is not None and result.status is RatingRebuildStatus.COMPLETED
    assert executor.calls == 0
    assert cutover.calls == 1


async def test_business_failure_is_persisted_and_manual_retry_resumes() -> None:
    class FailingExecutor:
        async def run(self, uow_factory, *, job_id):
            del uow_factory, job_id
            raise RatingRebuildExecutionError("invalid shadow snapshot")

    uow = FakeRatingsUnitOfWork()
    job = make_job(RatingRebuildStatus.PENDING)
    await uow.rebuild_jobs.add(job)
    orchestrator = RatingRebuildOrchestrator(
        executor=FailingExecutor(),
        cutover=CompletingCutover(uow),
        clock=lambda: NOW,
    )

    failed = await orchestrator.run_once(lambda: uow)

    assert failed is not None and failed.status is RatingRebuildStatus.FAILED
    assert failed.error_code == "rating_rebuild_execution_conflict"
    assert await uow.rebuild_jobs.get_publication_barrier() == failed

    resumed = await orchestrator.retry_failed(lambda: uow, job_id=job.id)

    assert resumed.status is RatingRebuildStatus.PREPARING
    assert resumed.error_code is None and resumed.completed_at is None


async def test_cutover_business_failure_marks_ready_job_failed() -> None:
    class FailingCutover:
        async def activate(self, uow, *, job_id):
            del uow, job_id
            raise RatingRebuildExecutionError("incomplete cutover")

    uow = FakeRatingsUnitOfWork()
    job = make_job(RatingRebuildStatus.READY)
    await uow.rebuild_jobs.add(job)

    result = await RatingRebuildOrchestrator(
        executor=CompletingExecutor(uow),
        cutover=FailingCutover(),
        clock=lambda: NOW,
    ).run_once(lambda: uow)

    assert result is not None and result.status is RatingRebuildStatus.FAILED
    assert await uow.rebuild_jobs.get_publication_barrier() == result


async def test_infrastructure_failure_is_left_for_scheduler_retry() -> None:
    class UnavailableExecutor:
        async def run(self, uow_factory, *, job_id):
            del uow_factory, job_id
            raise ConnectionError("database unavailable")

    uow = FakeRatingsUnitOfWork()
    job = make_job(RatingRebuildStatus.PENDING)
    await uow.rebuild_jobs.add(job)
    orchestrator = RatingRebuildOrchestrator(
        executor=UnavailableExecutor(),
        cutover=CompletingCutover(uow),
        clock=lambda: NOW,
    )

    with pytest.raises(ConnectionError, match="database unavailable"):
        await orchestrator.run_once(lambda: uow)

    assert uow.rebuild_jobs.items[job.id].status is RatingRebuildStatus.PENDING


async def test_superseded_job_is_skipped_and_replacement_is_processed() -> None:
    uow = FakeRatingsUnitOfWork()
    original = make_job(RatingRebuildStatus.PREPARING)
    replacement = make_job(RatingRebuildStatus.PENDING)
    await uow.rebuild_jobs.add(original)

    class SupersedingExecutor(CompletingExecutor):
        async def run(self, uow_factory, *, job_id):
            if job_id == original.id:
                self.uow.rebuild_jobs.items[original.id] = replace(
                    original, status=RatingRebuildStatus.SUPERSEDED
                )
                await self.uow.rebuild_jobs.add(replacement)
                raise RatingRebuildExecutionError("superseded")
            return await super().run(uow_factory, job_id=job_id)

    result = await RatingRebuildOrchestrator(
        executor=SupersedingExecutor(uow),
        cutover=CompletingCutover(uow),
        clock=lambda: NOW,
    ).run_once(lambda: uow)

    assert result is not None and result.id == replacement.id
    assert result.status is RatingRebuildStatus.COMPLETED


async def test_scheduler_resumes_normal_workflow_only_after_completed_cutover() -> None:
    calls: list[str] = []

    class Rebuild:
        async def run_once(self, uow_factory):
            del uow_factory
            calls.append("rebuild")
            return make_job(RatingRebuildStatus.COMPLETED)

    class Workflow:
        async def run(self, uow_factory):
            del uow_factory
            calls.append("normal")
            return ()

    scheduler = RatingPeriodScheduler(
        lambda: FakeRatingsUnitOfWork(),
        rebuild=Rebuild(),
        workflow=Workflow(),
        clock=lambda: NOW,
    )

    await scheduler.run_cycle()

    assert calls == ["rebuild", "normal"]


async def test_scheduler_does_not_run_normal_workflow_behind_failed_barrier() -> None:
    class Rebuild:
        async def run_once(self, uow_factory):
            del uow_factory
            return make_job(RatingRebuildStatus.FAILED)

    class Workflow:
        async def run(self, uow_factory):
            del uow_factory
            raise AssertionError("normal workflow must remain behind failed barrier")

    scheduler = RatingPeriodScheduler(
        lambda: FakeRatingsUnitOfWork(),
        rebuild=Rebuild(),
        workflow=Workflow(),
        clock=lambda: NOW,
    )

    await scheduler.run_cycle()

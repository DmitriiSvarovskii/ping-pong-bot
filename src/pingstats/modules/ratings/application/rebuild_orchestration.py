"""Automatic recovery, execution, and activation of rating rebuild jobs."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from pingstats.core.exceptions import ApplicationError
from pingstats.modules.ratings.application.closer import RatingsUnitOfWorkFactory
from pingstats.modules.ratings.application.cutover import RatingRebuildCutover
from pingstats.modules.ratings.application.errors import RatingRebuildExecutionError
from pingstats.modules.ratings.application.rebuild import RatingRebuildExecutor
from pingstats.modules.ratings.domain import RatingRebuildJob, RatingRebuildStatus

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RatingRebuildOrchestrator:
    """Advance the durable singleton rebuild without bypassing its barrier."""

    def __init__(
        self,
        *,
        executor: RatingRebuildExecutor | None = None,
        cutover: RatingRebuildCutover | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._executor = executor or RatingRebuildExecutor(clock=clock)
        self._cutover = cutover or RatingRebuildCutover(clock=clock)
        self._clock = clock

    async def run_once(self, uow_factory: RatingsUnitOfWorkFactory) -> RatingRebuildJob | None:
        """Run or resume the active job; business failures become durable FAILED."""
        for _ in range(3):
            job = await self._active_job(uow_factory)
            if job is None:
                return None
            if job.status is RatingRebuildStatus.FAILED:
                self._log(job, "blocked", error_code=job.error_code)
                return job
            self._log(job, "started")
            if job.status in {RatingRebuildStatus.PENDING, RatingRebuildStatus.PREPARING}:
                try:
                    job = await self._executor.run(uow_factory, job_id=job.id)
                except ApplicationError as exc:
                    current = await self._job(uow_factory, job.id)
                    if current is not None and current.status in {
                        RatingRebuildStatus.READY,
                        RatingRebuildStatus.COMPLETED,
                        RatingRebuildStatus.SUPERSEDED,
                    }:
                        self._log(current, "concurrent_progress")
                        continue
                    return await self._fail_business(uow_factory, job.id, exc)
            if job.status is RatingRebuildStatus.READY:
                try:
                    completed = await self._cutover.activate(uow_factory(), job_id=job.id)
                except ApplicationError as exc:
                    current = await self._job(uow_factory, job.id)
                    if current is not None and current.status in {
                        RatingRebuildStatus.COMPLETED,
                        RatingRebuildStatus.SUPERSEDED,
                    }:
                        self._log(current, "concurrent_progress")
                        continue
                    return await self._fail_business(uow_factory, job.id, exc)
            elif job.status in {
                RatingRebuildStatus.COMPLETED,
                RatingRebuildStatus.SUPERSEDED,
            }:
                completed = job
            else:
                error = RatingRebuildExecutionError()
                return await self._fail_business(uow_factory, job.id, error)
            self._log(completed, "completed")
            return completed
        return await self._active_job(uow_factory)

    async def retry_failed(
        self,
        uow_factory: RatingsUnitOfWorkFactory,
        *,
        job_id: UUID,
    ) -> RatingRebuildJob:
        """Explicitly re-open one failed job while preserving its checkpoints."""
        async with uow_factory() as uow:
            await uow.periods.acquire_publication_lock()
            job = await uow.rebuild_jobs.get(job_id, for_update=True)
            active = await uow.rebuild_jobs.get_active(for_update=True)
            if (
                job is None
                or active is None
                or active.id != job.id
                or job.status is not RatingRebuildStatus.FAILED
            ):
                raise RatingRebuildExecutionError("Only the active failed rebuild can be retried")
            resumed = await uow.rebuild_jobs.update_status(
                job_id=job.id,
                status=RatingRebuildStatus.PREPARING,
                expected_version=job.version,
                completed_at=None,
                error_code=None,
            )
            if resumed is None:
                raise RatingRebuildExecutionError("Rating rebuild changed during manual retry")
            await uow.commit()
        self._log(resumed, "manual_retry")
        return resumed

    async def _fail_business(
        self,
        uow_factory: RatingsUnitOfWorkFactory,
        job_id: UUID,
        error: ApplicationError,
    ) -> RatingRebuildJob:
        failed = await self._mark_failed(uow_factory, job_id, error.code)
        self._log(failed, "failed", error_code=error.code)
        return failed

    async def _mark_failed(
        self,
        uow_factory: RatingsUnitOfWorkFactory,
        job_id: UUID,
        error_code: str,
    ) -> RatingRebuildJob:
        async with uow_factory() as uow:
            await uow.periods.acquire_publication_lock()
            job = await uow.rebuild_jobs.get(job_id, for_update=True)
            if job is None:
                raise RatingRebuildExecutionError("Rating rebuild disappeared after failure")
            if job.status in {
                RatingRebuildStatus.COMPLETED,
                RatingRebuildStatus.SUPERSEDED,
                RatingRebuildStatus.FAILED,
            }:
                return job
            failed = await uow.rebuild_jobs.update_status(
                job_id=job.id,
                status=RatingRebuildStatus.FAILED,
                expected_version=job.version,
                completed_at=self._now(),
                error_code=error_code[:64],
            )
            if failed is None:
                raise RatingRebuildExecutionError("Rating rebuild changed while marking failure")
            await uow.commit()
            return failed

    @staticmethod
    async def _active_job(
        uow_factory: RatingsUnitOfWorkFactory,
    ) -> RatingRebuildJob | None:
        async with uow_factory() as uow:
            return await uow.rebuild_jobs.get_active()

    @staticmethod
    async def _job(uow_factory: RatingsUnitOfWorkFactory, job_id: UUID) -> RatingRebuildJob | None:
        async with uow_factory() as uow:
            return await uow.rebuild_jobs.get(job_id)

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Rating rebuild clock must be timezone-aware")
        return now.astimezone(UTC)

    @staticmethod
    def _log(
        job: RatingRebuildJob,
        result: str,
        *,
        error_code: str | None = None,
    ) -> None:
        logger.info(
            "Rating rebuild orchestration stage",
            extra={
                "rebuild_job_id": str(job.id),
                "rebuild_status": job.status.value,
                "stage": "rebuild",
                "result": result,
                "error_code": error_code,
            },
        )

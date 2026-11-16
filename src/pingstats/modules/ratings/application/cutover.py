"""Atomic activation of a complete shadow rating-history branch."""

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from uuid import UUID

from pingstats.modules.ratings.application.errors import (
    RatingCalculationVersionConflictError,
    RatingCutoverConflictError,
)
from pingstats.modules.ratings.application.ports import RatingsUnitOfWork
from pingstats.modules.ratings.application.snapshots import validate_rating_snapshot
from pingstats.modules.ratings.domain import (
    PublishedRating,
    RatingPeriod,
    RatingPeriodResult,
    RatingPeriodStatus,
    RatingRebuildJob,
    RatingRebuildPeriod,
    RatingRebuildPeriodStatus,
    RatingRebuildStatus,
    RatingRecalculationStatus,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RatingRebuildCutover:
    """Switch all rebuilt periods and current ratings in one transaction."""

    def __init__(self, *, clock: Callable[[], datetime] = _utc_now) -> None:
        self._clock = clock

    async def activate(
        self,
        uow: RatingsUnitOfWork,
        *,
        job_id: UUID,
    ) -> RatingRebuildJob:
        async with uow:
            await uow.periods.acquire_publication_lock()
            job = await uow.rebuild_jobs.get(job_id, for_update=True)
            if job is None:
                raise RatingCutoverConflictError("Rating rebuild job was not found")
            if job.status is RatingRebuildStatus.COMPLETED:
                return job
            if job.status is not RatingRebuildStatus.READY:
                raise RatingCutoverConflictError
            active = await uow.rebuild_jobs.get_active()
            if active is None or active.id != job.id:
                raise RatingCutoverConflictError("Rating rebuild job was superseded")

            checkpoints = tuple(await uow.rebuild_periods.list_for_job(job.id))
            periods = await self._lock_and_validate_periods(uow, job, checkpoints)
            await self._require_no_unhandled_corrections(uow, job)
            source_games, target_games, target_results = await self._validate_snapshots(
                uow, periods, checkpoints
            )

            activating = await uow.rebuild_jobs.update_status(
                job_id=job.id,
                status=RatingRebuildStatus.ACTIVATING,
                expected_version=job.version,
                completed_at=None,
                error_code=None,
            )
            if activating is None:
                raise RatingCalculationVersionConflictError

            cutover_at = self._now()
            await self._replace_current_ratings(
                uow,
                periods,
                source_games,
                target_games,
                target_results,
                cutover_at,
            )
            for period, checkpoint in zip(periods, checkpoints, strict=True):
                activated = await uow.periods.activate_rebuild_version(
                    period_id=period.id,
                    calculation_version=checkpoint.target_version,
                    published_at=cutover_at,
                    expected_version=period.version,
                )
                if activated is None:
                    raise RatingCalculationVersionConflictError

            requests = tuple(await uow.recalculations.list_for_job(job.id))
            for request in requests:
                if request.status is RatingRecalculationStatus.COMPLETED:
                    continue
                completed = await uow.recalculations.update_status(
                    request_id=request.id,
                    status=RatingRecalculationStatus.COMPLETED,
                    started_at=request.started_at or cutover_at,
                    completed_at=cutover_at,
                    error_code=None,
                )
                if completed is None:
                    raise RatingCalculationVersionConflictError

            completed_job = await uow.rebuild_jobs.update_status(
                job_id=job.id,
                status=RatingRebuildStatus.COMPLETED,
                expected_version=activating.version,
                completed_at=cutover_at,
                error_code=None,
            )
            if completed_job is None:
                raise RatingCalculationVersionConflictError
            await uow.commit()
            return completed_job

    async def _lock_and_validate_periods(
        self,
        uow: RatingsUnitOfWork,
        job: RatingRebuildJob,
        checkpoints: tuple[RatingRebuildPeriod, ...],
    ) -> tuple[RatingPeriod, ...]:
        if (
            not checkpoints
            or checkpoints[0].period_id != job.from_period_id
            or checkpoints[-1].period_id != job.through_period_id
        ):
            raise RatingCutoverConflictError("Rating rebuild horizon is incomplete")
        periods: list[RatingPeriod] = []
        for checkpoint in checkpoints:
            hint = await uow.periods.get(checkpoint.period_id)
            if hint is None:
                raise RatingCutoverConflictError("Rating rebuild period was not found")
            await uow.periods.acquire_period_lock(hint.starts_at)
            period = await uow.periods.get(hint.id, for_update=True)
            locked_checkpoint = await uow.rebuild_periods.get(job.id, hint.id, for_update=True)
            if (
                period is None
                or locked_checkpoint is None
                or locked_checkpoint != checkpoint
                or checkpoint.status is not RatingRebuildPeriodStatus.READY
                or checkpoint.target_version <= checkpoint.source_version
                or period.calculation_version != checkpoint.target_version
                or period.calculated_version != checkpoint.target_version
                or period.status
                not in {
                    RatingPeriodStatus.CALCULATING,
                    RatingPeriodStatus.PUBLISHED,
                }
            ):
                raise RatingCutoverConflictError("Rating rebuild checkpoint is incomplete")
            if (
                period.status is RatingPeriodStatus.PUBLISHED
                and period.published_version != checkpoint.source_version
            ):
                raise RatingCutoverConflictError("Published history changed during rebuild")
            periods.append(period)
        expected = tuple(
            await uow.periods.list_non_open_between(periods[0].starts_at, periods[-1].starts_at)
        )
        if tuple(item.id for item in expected) != tuple(item.id for item in periods):
            raise RatingCutoverConflictError("Rating rebuild horizon is incomplete")
        return tuple(periods)

    @staticmethod
    async def _require_no_unhandled_corrections(
        uow: RatingsUnitOfWork, job: RatingRebuildJob
    ) -> None:
        requests = tuple(await uow.recalculations.list_for_job(job.id))
        if not requests or any(
            item.status
            not in {
                RatingRecalculationStatus.PENDING,
                RatingRecalculationStatus.PROCESSING,
                RatingRecalculationStatus.COMPLETED,
            }
            for item in requests
        ):
            raise RatingCutoverConflictError("Recalculation requests are incomplete")
        outstanding = tuple(
            await uow.recalculations.list_by_status(RatingRecalculationStatus.PENDING)
        ) + tuple(await uow.recalculations.list_by_status(RatingRecalculationStatus.PROCESSING))
        if any(item.rebuild_job_id != job.id for item in outstanding):
            raise RatingCutoverConflictError("An unassigned correction is waiting for rebuild")

    @staticmethod
    async def _validate_snapshots(
        uow: RatingsUnitOfWork,
        periods: tuple[RatingPeriod, ...],
        checkpoints: tuple[RatingRebuildPeriod, ...],
    ) -> tuple[
        Counter[UUID],
        Counter[UUID],
        tuple[tuple[RatingPeriodResult, ...], ...],
    ]:
        source_games: Counter[UUID] = Counter()
        target_games: Counter[UUID] = Counter()
        target_snapshots: list[tuple[RatingPeriodResult, ...]] = []
        previous_results: tuple[RatingPeriodResult, ...] = ()
        first_previous = await uow.periods.get_previous_published(periods[0].starts_at)
        if first_previous is not None:
            if first_previous.published_version is None:
                raise RatingCutoverConflictError("Published predecessor has no active version")
            previous_results = tuple(
                await uow.results.list_for_period(
                    first_previous.id, first_previous.published_version
                )
            )
        for period, checkpoint in zip(periods, checkpoints, strict=True):
            inputs = tuple(await uow.inputs.list_for_period(period.id, checkpoint.target_version))
            target = tuple(await uow.results.list_for_period(period.id, checkpoint.target_version))
            validate_rating_snapshot(target, inputs, previous_results)
            for item in inputs:
                target_games[item.player1_id] += 1
                target_games[item.player2_id] += 1
            if (
                period.status is RatingPeriodStatus.PUBLISHED
                and period.published_version is not None
            ):
                source_inputs = await uow.inputs.list_for_period(
                    period.id, period.published_version
                )
                for item in source_inputs:
                    source_games[item.player1_id] += 1
                    source_games[item.player2_id] += 1
            target_snapshots.append(target)
            previous_results = target
        return source_games, target_games, tuple(target_snapshots)

    async def _replace_current_ratings(
        self,
        uow: RatingsUnitOfWork,
        periods: tuple[RatingPeriod, ...],
        old_games: Mapping[UUID, int],
        new_games: Mapping[UUID, int],
        target_snapshots: Sequence[Sequence[RatingPeriodResult]],
        cutover_at: datetime,
    ) -> None:
        current = tuple(await uow.ratings.list_all(for_update=True))
        current_by_user = {item.user_id: item for item in current}
        final = {item.user_id: item for item in target_snapshots[-1]}
        for user_id in sorted(final, key=lambda value: value.int):
            result = final[user_id]
            existing = current_by_user.get(user_id)
            count = (
                (existing.rated_matches_count if existing is not None else 0)
                - old_games.get(user_id, 0)
                + new_games.get(user_id, 0)
            )
            if count < 0:
                raise RatingCutoverConflictError("Cumulative rated match count is inconsistent")
            if existing is None:
                await uow.ratings.add(
                    PublishedRating(
                        user_id=user_id,
                        rating=result.rating_after,
                        rd=result.rd_after,
                        volatility=result.volatility_after,
                        as_of_period_id=periods[-1].id,
                        rated_matches_count=count,
                        updated_at=cutover_at,
                        version=1,
                    )
                )
                continue
            updated = await uow.ratings.update_published_state(
                user_id=user_id,
                rating=result.rating_after,
                rd=result.rd_after,
                volatility=result.volatility_after,
                as_of_period_id=periods[-1].id,
                rated_matches_count=count,
                expected_version=existing.version,
            )
            if updated is None:
                raise RatingCalculationVersionConflictError
        for user_id in sorted(set(current_by_user).difference(final), key=lambda value: value.int):
            await uow.ratings.delete(user_id)

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Rating cutover clock must be timezone-aware")
        return now.astimezone(UTC)

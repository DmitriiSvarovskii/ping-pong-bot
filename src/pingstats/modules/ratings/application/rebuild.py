"""Restart-safe preparation and calculation of an immutable shadow rating branch."""

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from pingstats.modules.matches.application.dto import MatchView
from pingstats.modules.matches.domain import MatchStatus
from pingstats.modules.ratings.application.calculator import RatingPeriodCalculator
from pingstats.modules.ratings.application.closer import RatingsUnitOfWorkFactory
from pingstats.modules.ratings.application.dto import CalculatedPlayerRating
from pingstats.modules.ratings.application.errors import (
    RatingCalculationSnapshotConflictError,
    RatingCalculationVersionConflictError,
    RatingRebuildExecutionError,
)
from pingstats.modules.ratings.application.intake import RatingMatchInputRecorder
from pingstats.modules.ratings.application.ports import RatingsUnitOfWork
from pingstats.modules.ratings.domain import (
    RatingPeriod,
    RatingPeriodMatchInput,
    RatingPeriodResult,
    RatingRebuildJob,
    RatingRebuildPeriod,
    RatingRebuildPeriodStatus,
    RatingRebuildStatus,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RatingRebuildExecutor:
    """Prepare and calculate every reserved period of one rebuild job in order."""

    def __init__(
        self,
        *,
        calculator: RatingPeriodCalculator | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._calculator = calculator or RatingPeriodCalculator()
        self._clock = clock

    async def run(
        self,
        uow_factory: RatingsUnitOfWorkFactory,
        *,
        job_id: UUID,
    ) -> RatingRebuildJob:
        job = await self._start_or_resume(uow_factory, job_id)
        if job.status is RatingRebuildStatus.READY:
            return job
        checkpoints = await self._checkpoints(uow_factory, job_id)
        for index, checkpoint in enumerate(checkpoints):
            predecessor = checkpoints[index - 1] if index else None
            await self._process_period(uow_factory, job_id, checkpoint, predecessor)
        return await self._mark_ready(uow_factory, job_id)

    async def _start_or_resume(
        self, uow_factory: RatingsUnitOfWorkFactory, job_id: UUID
    ) -> RatingRebuildJob:
        async with uow_factory() as uow:
            await uow.periods.acquire_publication_lock()
            job = await uow.rebuild_jobs.get(job_id, for_update=True)
            if job is None:
                raise RatingRebuildExecutionError("Rating rebuild job was not found")
            if job.status is RatingRebuildStatus.READY:
                return job
            if job.status is RatingRebuildStatus.PENDING:
                updated = await uow.rebuild_jobs.update_status(
                    job_id=job.id,
                    status=RatingRebuildStatus.PREPARING,
                    expected_version=job.version,
                    started_at=self._now(),
                    completed_at=None,
                    error_code=None,
                )
                if updated is None:
                    raise RatingCalculationVersionConflictError
                await uow.commit()
                return updated
            if job.status is not RatingRebuildStatus.PREPARING:
                raise RatingRebuildExecutionError
            return job

    @staticmethod
    async def _checkpoints(
        uow_factory: RatingsUnitOfWorkFactory, job_id: UUID
    ) -> tuple[RatingRebuildPeriod, ...]:
        async with uow_factory() as uow:
            checkpoints = tuple(await uow.rebuild_periods.list_for_job(job_id))
        if not checkpoints:
            raise RatingRebuildExecutionError("Rating rebuild has no period checkpoints")
        return checkpoints

    async def _process_period(
        self,
        uow_factory: RatingsUnitOfWorkFactory,
        job_id: UUID,
        checkpoint_hint: RatingRebuildPeriod,
        predecessor_checkpoint: RatingRebuildPeriod | None,
    ) -> None:
        async with uow_factory() as uow:
            await uow.periods.acquire_publication_lock()
            job = await uow.rebuild_jobs.get(job_id, for_update=True)
            if job is None or job.status is not RatingRebuildStatus.PREPARING:
                raise RatingRebuildExecutionError
            period_hint = await uow.periods.get(checkpoint_hint.period_id)
            if period_hint is None:
                raise RatingRebuildExecutionError("Rebuild period was not found")
            await uow.periods.acquire_period_lock(period_hint.starts_at)
            period = await uow.periods.get(period_hint.id, for_update=True)
            checkpoint = await uow.rebuild_periods.get(job.id, period_hint.id, for_update=True)
            if period is None or checkpoint is None:
                raise RatingRebuildExecutionError("Rebuild checkpoint disappeared")
            if checkpoint.status is RatingRebuildPeriodStatus.READY:
                return
            if checkpoint.status not in {
                RatingRebuildPeriodStatus.PENDING,
                RatingRebuildPeriodStatus.INPUTS_PREPARED,
                RatingRebuildPeriodStatus.CALCULATED,
            }:
                raise RatingRebuildExecutionError
            if period.calculation_version != checkpoint.target_version:
                raise RatingCalculationVersionConflictError

            desired_inputs = await self._shadow_inputs(uow, period, checkpoint)
            await self._persist_inputs(uow, period, checkpoint, desired_inputs)
            previous_period, previous_version = await self._predecessor(
                uow, period, predecessor_checkpoint
            )
            calculation = await self._calculator.calculate_version_in_uow(
                uow,
                period_id=period.id,
                calculation_version=checkpoint.target_version,
                predecessor_period=previous_period,
                predecessor_version=previous_version,
                locked_period=period,
            )
            await self._persist_results(uow, period, checkpoint, calculation.players)
            marked = await uow.periods.mark_shadow_calculated(
                period_id=period.id,
                calculation_version=checkpoint.target_version,
                expected_version=period.version,
            )
            if marked is None:
                raise RatingCalculationVersionConflictError
            updated_checkpoint = await uow.rebuild_periods.update_status(
                rebuild_job_id=job.id,
                period_id=period.id,
                status=RatingRebuildPeriodStatus.READY,
                expected_status=checkpoint.status,
                completed_at=self._now(),
                error_code=None,
            )
            if updated_checkpoint is None:
                raise RatingCalculationVersionConflictError
            await uow.commit()

    async def _shadow_inputs(
        self,
        uow: RatingsUnitOfWork,
        period: RatingPeriod,
        checkpoint: RatingRebuildPeriod,
    ) -> tuple[RatingPeriodMatchInput, ...]:
        source = tuple(await uow.inputs.list_for_period(period.id, checkpoint.source_version))
        prepared: list[RatingPeriodMatchInput] = []
        for source_item in source:
            view = await uow.matches.get_view(source_item.match_id)
            if view is None:
                raise RatingRebuildExecutionError("A source rating match was not found")
            if view.match.status is MatchStatus.VOIDED:
                continue
            self._require_rating_match(period, view)
            revisions, _ = await uow.matches.list_revisions(view.match.id, limit=1, offset=0)
            prepared.append(
                RatingPeriodMatchInput(
                    period_id=period.id,
                    calculation_version=checkpoint.target_version,
                    match_id=view.match.id,
                    source_match_version=view.match.version,
                    correction_revision_id=(
                        revisions[0].id if revisions else source_item.correction_revision_id
                    ),
                    result_fingerprint=RatingMatchInputRecorder.fingerprint(view),
                    player1_id=view.user_id_at(1),
                    player2_id=view.user_id_at(2),
                    score1=Decimal("1.00")
                    if view.match.winner_id == view.user_id_at(1)
                    else Decimal("0.00"),
                    score2=Decimal("1.00")
                    if view.match.winner_id == view.user_id_at(2)
                    else Decimal("0.00"),
                    completed_at=view.match.completed_at.astimezone(UTC),
                    created_at=self._now(),
                )
            )
        return tuple(sorted(prepared, key=lambda item: (item.completed_at, item.match_id.int)))

    @staticmethod
    def _require_rating_match(period: RatingPeriod, view: MatchView) -> None:
        completed_at = view.match.completed_at
        if (
            view.match.status is not MatchStatus.COMPLETED
            or not view.match.is_ranked
            or completed_at is None
            or len(view.participants) != 2
            or not view.sets
            or view.match.winner_id not in view.participant_ids()
            or RatingMatchInputRecorder.period_start(completed_at) != period.starts_at
        ):
            raise RatingRebuildExecutionError("Current match state is not a valid rating input")

    @classmethod
    async def _persist_inputs(
        cls,
        uow: RatingsUnitOfWork,
        period: RatingPeriod,
        checkpoint: RatingRebuildPeriod,
        desired: tuple[RatingPeriodMatchInput, ...],
    ) -> None:
        existing = tuple(await uow.inputs.list_for_period(period.id, checkpoint.target_version))
        if existing:
            if cls._input_snapshot(existing) != cls._input_snapshot(desired):
                raise RatingCalculationSnapshotConflictError
            return
        if desired:
            await uow.inputs.add_all(desired)

    async def _predecessor(
        self,
        uow: RatingsUnitOfWork,
        period: RatingPeriod,
        predecessor_checkpoint: RatingRebuildPeriod | None,
    ) -> tuple[RatingPeriod | None, int | None]:
        if predecessor_checkpoint is None:
            previous = await uow.periods.get_previous_published(period.starts_at)
            if previous is None:
                return None, None
            if previous.published_version is None:
                raise RatingRebuildExecutionError(
                    "First shadow period has no published predecessor"
                )
            return previous, previous.published_version
        previous = await uow.periods.get(predecessor_checkpoint.period_id)
        stored_checkpoint = await uow.rebuild_periods.get(
            predecessor_checkpoint.rebuild_job_id,
            predecessor_checkpoint.period_id,
        )
        if (
            previous is None
            or stored_checkpoint is None
            or stored_checkpoint.status is not RatingRebuildPeriodStatus.READY
        ):
            raise RatingRebuildExecutionError("Previous shadow period is not ready")
        return previous, stored_checkpoint.target_version

    async def _persist_results(
        self,
        uow: RatingsUnitOfWork,
        period: RatingPeriod,
        checkpoint: RatingRebuildPeriod,
        players: tuple[CalculatedPlayerRating, ...],
    ) -> None:
        existing = tuple(await uow.results.list_for_period(period.id, checkpoint.target_version))
        desired_values = tuple(
            sorted(
                (self._calculated_values(item) for item in players),
                key=lambda item: item[0].int,
            )
        )
        if existing:
            existing_values = tuple(
                sorted(
                    (self._stored_values(item) for item in existing),
                    key=lambda item: item[0].int,
                )
            )
            if existing_values != desired_values:
                raise RatingCalculationSnapshotConflictError
            return
        created_at = self._now()
        rows = tuple(
            RatingPeriodResult(
                id=uuid4(),
                period_id=period.id,
                calculation_version=checkpoint.target_version,
                user_id=item.user_id,
                rating_before=item.rating_before,
                rating_after=item.rating_after,
                rd_before=item.rd_before,
                rd_after=item.rd_after,
                volatility_before=item.volatility_before,
                volatility_after=item.volatility_after,
                games_count=item.games_count,
                is_active=item.is_active,
                created_at=created_at,
            )
            for item in players
        )
        if rows:
            await uow.results.add_all(rows)

    async def _mark_ready(
        self, uow_factory: RatingsUnitOfWorkFactory, job_id: UUID
    ) -> RatingRebuildJob:
        async with uow_factory() as uow:
            await uow.periods.acquire_publication_lock()
            job = await uow.rebuild_jobs.get(job_id, for_update=True)
            if job is None:
                raise RatingRebuildExecutionError("Rating rebuild job was not found")
            if job.status is RatingRebuildStatus.READY:
                return job
            if job.status is not RatingRebuildStatus.PREPARING:
                raise RatingRebuildExecutionError
            checkpoints = tuple(await uow.rebuild_periods.list_for_job(job.id))
            if not checkpoints or any(
                item.status is not RatingRebuildPeriodStatus.READY for item in checkpoints
            ):
                raise RatingRebuildExecutionError("Rating rebuild is incomplete")
            ready_at = self._now()
            updated = await uow.rebuild_jobs.update_status(
                job_id=job.id,
                status=RatingRebuildStatus.READY,
                expected_version=job.version,
                ready_at=ready_at,
                completed_at=None,
                error_code=None,
            )
            if updated is None:
                raise RatingCalculationVersionConflictError
            await uow.commit()
            return updated

    @staticmethod
    def _input_snapshot(
        items: tuple[RatingPeriodMatchInput, ...],
    ) -> tuple[tuple[object, ...], ...]:
        return tuple(
            sorted(
                (
                    item.match_id,
                    item.source_match_version,
                    item.correction_revision_id,
                    item.result_fingerprint,
                    item.player1_id,
                    item.player2_id,
                    item.score1,
                    item.score2,
                    item.completed_at,
                )
                for item in items
            )
        )

    @staticmethod
    def _stored_values(item: RatingPeriodResult) -> tuple[object, ...]:
        return (
            item.user_id,
            item.rating_before,
            item.rating_after,
            item.rd_before,
            item.rd_after,
            item.volatility_before,
            item.volatility_after,
            item.games_count,
            item.is_active,
        )

    @staticmethod
    def _calculated_values(item: CalculatedPlayerRating) -> tuple[object, ...]:
        return (
            item.user_id,
            item.rating_before,
            item.rating_after,
            item.rd_before,
            item.rd_after,
            item.volatility_before,
            item.volatility_after,
            item.games_count,
            item.is_active,
        )

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Rating rebuild clock must be timezone-aware")
        return now.astimezone(UTC)

"""Atomic coordination of rated match corrections with versioned rating history."""

from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from pingstats.modules.matches.application.dto import MatchView
from pingstats.modules.matches.domain import MatchResultRevision, MatchRevisionOperation
from pingstats.modules.ratings.application.errors import (
    RatingCalculationVersionConflictError,
    RatingCorrectionHistoryError,
)
from pingstats.modules.ratings.application.intake import RatingMatchInputRecorder
from pingstats.modules.ratings.application.ports import (
    RatingInputRepository,
    RatingPeriodRepository,
    RatingRebuildJobRepository,
    RatingRebuildPeriodRepository,
    RatingRecalculationRepository,
)
from pingstats.modules.ratings.domain import (
    RatingPeriod,
    RatingPeriodMatchInput,
    RatingPeriodStatus,
    RatingRebuildJob,
    RatingRebuildPeriod,
    RatingRebuildPeriodStatus,
    RatingRebuildStatus,
    RatingRecalculationRequest,
    RatingRecalculationStatus,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RatingCorrectionCoordinator:
    """Prepare a new normal snapshot or a durable rebuild barrier after a revision."""

    def __init__(self, *, clock: Callable[[], datetime] = _utc_now) -> None:
        self._clock = clock

    async def coordinate(
        self,
        *,
        periods: RatingPeriodRepository,
        inputs: RatingInputRepository,
        recalculations: RatingRecalculationRepository,
        rebuild_jobs: RatingRebuildJobRepository,
        rebuild_periods: RatingRebuildPeriodRepository,
        match: MatchView,
        revision: MatchResultRevision,
    ) -> bool:
        if not match.match.is_ranked:
            return False
        completed_at = match.match.completed_at
        if completed_at is None:
            raise RatingCorrectionHistoryError("Rated correction requires completed_at")
        period_start = RatingMatchInputRecorder.period_start(completed_at)

        # Global coordination precedes period advisory/row locks everywhere.
        await periods.acquire_publication_lock()
        active_job = await rebuild_jobs.get_active(for_update=True)
        await periods.acquire_period_lock(period_start)
        period = await periods.get_by_start(period_start, for_update=True)
        if period is None:
            raise RatingCorrectionHistoryError

        active_checkpoint = (
            await rebuild_periods.get(active_job.id, period.id, for_update=True)
            if active_job is not None
            else None
        )
        if period.status in (RatingPeriodStatus.OPEN, RatingPeriodStatus.CALCULATING) and (
            active_checkpoint is None
        ):
            await self._version_unpublished(periods, inputs, period, match, revision)
            return False
        if period.status is not RatingPeriodStatus.PUBLISHED and active_checkpoint is None:
            raise RatingCorrectionHistoryError("Rating period state cannot be corrected safely")
        await self._coordinate_rebuild(
            periods=periods,
            inputs=inputs,
            recalculations=recalculations,
            rebuild_jobs=rebuild_jobs,
            rebuild_periods=rebuild_periods,
            affected_period=period,
            match=match,
            revision=revision,
            active_job=active_job,
            active_checkpoint=active_checkpoint,
        )
        return True

    async def _version_unpublished(
        self,
        periods: RatingPeriodRepository,
        inputs: RatingInputRepository,
        period: RatingPeriod,
        match: MatchView,
        revision: MatchResultRevision,
    ) -> None:
        source_version = period.calculation_version
        source = tuple(await inputs.list_for_period(period.id, source_version))
        if not any(item.match_id == match.match.id for item in source):
            raise RatingCorrectionHistoryError
        target_version = source_version + 1
        created_at = self._now()
        copied = [
            replace(item, calculation_version=target_version, created_at=created_at)
            for item in source
            if item.match_id != match.match.id
        ]
        if revision.operation is MatchRevisionOperation.CORRECTION:
            copied.append(
                self._corrected_input(period.id, target_version, match, revision, created_at)
            )
        elif revision.operation is not MatchRevisionOperation.VOID:
            raise RatingCorrectionHistoryError("Unsupported rating revision operation")
        if copied:
            ordered = tuple(sorted(copied, key=lambda item: (item.completed_at, item.match_id.int)))
            await inputs.add_all(ordered)
        reserved = await periods.reserve_calculation_version(
            period_id=period.id,
            calculation_version=target_version,
            expected_version=period.version,
        )
        if reserved is None:
            raise RatingCalculationVersionConflictError

    async def _coordinate_rebuild(
        self,
        *,
        periods: RatingPeriodRepository,
        inputs: RatingInputRepository,
        recalculations: RatingRecalculationRepository,
        rebuild_jobs: RatingRebuildJobRepository,
        rebuild_periods: RatingRebuildPeriodRepository,
        affected_period: RatingPeriod,
        match: MatchView,
        revision: MatchResultRevision,
        active_job: RatingRebuildJob | None,
        active_checkpoint: RatingRebuildPeriod | None,
    ) -> None:
        source_version = (
            active_checkpoint.source_version
            if active_checkpoint is not None
            else affected_period.published_version
        )
        if (
            source_version is None
            or await inputs.get(affected_period.id, source_version, match.match.id) is None
        ):
            raise RatingCorrectionHistoryError

        earliest = affected_period
        old_requests: Sequence[RatingRecalculationRequest] = ()
        if active_job is not None:
            active_from = await periods.get(active_job.from_period_id)
            if active_from is None:
                raise RatingCorrectionHistoryError("Active rebuild references a missing period")
            if active_from.starts_at < earliest.starts_at:
                earliest = active_from
            old_requests = await recalculations.list_for_job(active_job.id)
            superseded = await rebuild_jobs.update_status(
                job_id=active_job.id,
                status=RatingRebuildStatus.SUPERSEDED,
                expected_version=active_job.version,
                completed_at=self._now(),
                error_code=None,
            )
            if superseded is None:
                raise RatingCalculationVersionConflictError

        horizon = tuple(await periods.list_non_open_from(earliest.starts_at))
        if not horizon:
            raise RatingCorrectionHistoryError("Rebuild horizon is empty")
        now = self._now()
        job = RatingRebuildJob(
            id=uuid4(),
            from_period_id=horizon[0].id,
            through_period_id=horizon[-1].id,
            status=RatingRebuildStatus.PENDING,
            singleton_scope=1,
            created_at=now,
            started_at=None,
            ready_at=None,
            completed_at=None,
            error_code=None,
            version=1,
        )
        await rebuild_jobs.add(job)
        for period in horizon:
            previous_checkpoint = (
                await rebuild_periods.get(active_job.id, period.id, for_update=True)
                if active_job is not None
                else None
            )
            source = (
                previous_checkpoint.source_version
                if previous_checkpoint is not None
                else period.published_version or period.calculation_version
            )
            target = period.calculation_version + 1
            reserved = await periods.reserve_calculation_version(
                period_id=period.id,
                calculation_version=target,
                expected_version=period.version,
            )
            if reserved is None:
                raise RatingCalculationVersionConflictError
            await rebuild_periods.add(
                RatingRebuildPeriod(
                    rebuild_job_id=job.id,
                    period_id=period.id,
                    source_version=source,
                    target_version=target,
                    status=RatingRebuildPeriodStatus.PENDING,
                    created_at=now,
                    completed_at=None,
                    error_code=None,
                )
            )

        request = RatingRecalculationRequest(
            id=uuid4(),
            from_period_id=affected_period.id,
            caused_by_match_id=match.match.id,
            caused_by_revision_id=revision.id,
            rebuild_job_id=job.id,
            status=RatingRecalculationStatus.PENDING,
            created_at=now,
            started_at=None,
            completed_at=None,
            error_code=None,
        )
        await recalculations.add(request)
        for old in old_requests:
            if old.status is RatingRecalculationStatus.COMPLETED:
                continue
            linked = await recalculations.link_to_job(
                request_id=old.id,
                rebuild_job_id=job.id,
                expected_rebuild_job_id=active_job.id if active_job is not None else None,
            )
            if linked is None:
                raise RatingCalculationVersionConflictError

    @staticmethod
    def _corrected_input(
        period_id: UUID,
        calculation_version: int,
        match: MatchView,
        revision: MatchResultRevision,
        created_at: datetime,
    ) -> RatingPeriodMatchInput:
        winner_id = match.match.winner_id
        player1_id = match.user_id_at(1)
        player2_id = match.user_id_at(2)
        if winner_id not in (player1_id, player2_id) or match.match.completed_at is None:
            raise RatingCorrectionHistoryError("Corrected rated match has no valid winner")
        return RatingPeriodMatchInput(
            period_id=period_id,
            calculation_version=calculation_version,
            match_id=match.match.id,
            source_match_version=match.match.version,
            correction_revision_id=revision.id,
            result_fingerprint=RatingMatchInputRecorder.fingerprint(match),
            player1_id=player1_id,
            player2_id=player2_id,
            score1=Decimal("1.00") if winner_id == player1_id else Decimal("0.00"),
            score2=Decimal("1.00") if winner_id == player2_id else Decimal("0.00"),
            completed_at=match.match.completed_at.astimezone(UTC),
            created_at=created_at,
        )

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Rating correction clock must be timezone-aware")
        return now.astimezone(UTC)

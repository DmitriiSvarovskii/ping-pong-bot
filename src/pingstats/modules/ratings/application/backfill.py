"""Safe historical intake followed by the normal rating workflow."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID

from pingstats.modules.matches.application.dto import MatchView
from pingstats.modules.matches.domain import MatchStatus
from pingstats.modules.ratings.application.closer import RatingsUnitOfWorkFactory
from pingstats.modules.ratings.application.intake import RatingMatchInputRecorder
from pingstats.modules.ratings.application.ports import RatingsUnitOfWork
from pingstats.modules.ratings.application.workflow import RatingWorkflowOrchestrator
from pingstats.modules.ratings.domain import RatingPeriodStatus


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class RatingBackfillReport:
    dry_run: bool
    scanned_matches: int = 0
    eligible_matches: int = 0
    processed_matches: int = 0
    skipped_matches: int = 0
    conflicted_matches: int = 0
    periods: int = 0
    created_periods: int = 0
    published_periods: int = 0
    players: int = 0


class RatingBackfillConflictError(Exception):
    def __init__(self, report: RatingBackfillReport) -> None:
        super().__init__("Historical matches conflict with sealed or published rating history")
        self.report = report


class RatingBackfillBlockedError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class _BackfillIntake:
    report: RatingBackfillReport
    period_starts: tuple[datetime, ...]


class RatingHistoricalBackfill:
    """Register current historical results without rewriting published snapshots."""

    def __init__(
        self,
        *,
        recorder: RatingMatchInputRecorder | None = None,
        workflow: RatingWorkflowOrchestrator | None = None,
        clock: Callable[[], datetime] = _utc_now,
        batch_size: int = 500,
        completed_from: datetime | None = None,
        completed_before: datetime | None = None,
    ) -> None:
        if batch_size < 1:
            raise ValueError("Backfill batch size must be positive")
        self._recorder = recorder or RatingMatchInputRecorder(clock=clock)
        self._workflow = workflow or RatingWorkflowOrchestrator(clock=clock)
        self._batch_size = batch_size
        self._completed_from = self._utc_bound(completed_from)
        self._completed_before = self._utc_bound(completed_before)
        if (
            self._completed_from is not None
            and self._completed_before is not None
            and self._completed_from >= self._completed_before
        ):
            raise ValueError("Backfill completed_at range must be increasing")

    async def run(
        self,
        uow_factory: RatingsUnitOfWorkFactory,
        *,
        dry_run: bool,
    ) -> RatingBackfillReport:
        async with uow_factory() as uow:
            intake = await self._intake(uow, dry_run=dry_run)
            report = intake.report
            if report.conflicted_matches:
                raise RatingBackfillConflictError(report)
            if not dry_run:
                await uow.commit()
        if dry_run:
            return report
        published = await self._workflow.run_periods(
            uow_factory,
            starts_at=intake.period_starts,
        )
        return replace(report, published_periods=len(published))

    async def _intake(
        self,
        uow: RatingsUnitOfWork,
        *,
        dry_run: bool,
    ) -> _BackfillIntake:
        await uow.periods.acquire_publication_lock()
        if await uow.rebuild_jobs.get_publication_barrier() is not None:
            raise RatingBackfillBlockedError(
                "Rating rebuild must finish before historical backfill"
            )

        scanned = eligible = processed = skipped = conflicts = 0
        period_starts: set[datetime] = set()
        created_starts: set[datetime] = set()
        players: set[UUID] = set()
        cursor_time: datetime | None = None
        cursor_id: UUID | None = None
        while True:
            batch = tuple(
                await uow.matches.list_for_rating_backfill(
                    after_completed_at=cursor_time,
                    after_match_id=cursor_id,
                    completed_from=self._completed_from,
                    completed_before=self._completed_before,
                    limit=self._batch_size,
                )
            )
            if not batch:
                break
            for view, revision_id in batch:
                scanned += 1
                completed_at = view.match.completed_at
                if completed_at is None:
                    skipped += 1
                    continue
                cursor_time = completed_at
                cursor_id = view.match.id
                if not self._eligible(view):
                    skipped += 1
                    continue
                eligible += 1
                period_start = self._recorder.period_start(completed_at)
                period_starts.add(period_start)
                players.update((view.user_id_at(1), view.user_id_at(2)))
                await uow.periods.acquire_period_lock(period_start)
                period = await uow.periods.get_by_start(period_start, for_update=True)
                if period is None:
                    if await uow.periods.get_next_published(period_start) is not None:
                        conflicts += 1
                        continue
                    processed += 1
                    created_starts.add(period_start)
                    if not dry_run:
                        await self._recorder.record(
                            uow.periods,
                            uow.inputs,
                            view,
                            correction_revision_id=revision_id,
                        )
                    continue

                existing = await uow.inputs.get(
                    period.id,
                    period.calculation_version,
                    view.match.id,
                )
                if existing is not None and self._same_snapshot(
                    existing.source_match_version,
                    existing.result_fingerprint,
                    existing.correction_revision_id,
                    view,
                    revision_id,
                ):
                    skipped += 1
                    continue
                if period.status is not RatingPeriodStatus.OPEN or existing is not None:
                    conflicts += 1
                    continue
                processed += 1
                if not dry_run:
                    await self._recorder.record(
                        uow.periods,
                        uow.inputs,
                        view,
                        correction_revision_id=revision_id,
                    )
            if len(batch) < self._batch_size:
                break

        return _BackfillIntake(
            report=RatingBackfillReport(
                dry_run=dry_run,
                scanned_matches=scanned,
                eligible_matches=eligible,
                processed_matches=processed,
                skipped_matches=skipped,
                conflicted_matches=conflicts,
                periods=len(period_starts),
                created_periods=len(created_starts),
                players=len(players),
            ),
            period_starts=tuple(sorted(period_starts)),
        )

    @staticmethod
    def _eligible(view: MatchView) -> bool:
        return (
            view.match.status is MatchStatus.COMPLETED
            and view.match.is_ranked
            and view.match.cancelled_at is None
        )

    @staticmethod
    def _same_snapshot(
        source_match_version: int,
        fingerprint: str,
        correction_revision_id: UUID | None,
        view: MatchView,
        revision_id: UUID | None,
    ) -> bool:
        return (
            source_match_version == view.match.version
            and fingerprint == RatingMatchInputRecorder.fingerprint(view)
            and correction_revision_id == revision_id
        )

    @staticmethod
    def _utc_bound(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Backfill completed_at bounds must be timezone-aware")
        return value.astimezone(UTC)

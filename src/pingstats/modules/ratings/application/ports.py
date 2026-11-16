"""Persistence ports for future rating-period application services."""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from types import TracebackType
from typing import Protocol
from uuid import UUID

from pingstats.modules.matches.application.dto import MatchView
from pingstats.modules.matches.domain import MatchResultRevision
from pingstats.modules.ratings.domain import (
    PublishedRating,
    RatingPeriod,
    RatingPeriodMatchInput,
    RatingPeriodResult,
    RatingPeriodStatus,
    RatingRebuildJob,
    RatingRebuildPeriod,
    RatingRebuildPeriodStatus,
    RatingRebuildStatus,
    RatingRecalculationRequest,
    RatingRecalculationStatus,
)


class RatingMatchRepository(Protocol):
    async def get_view(self, match_id: UUID, *, for_update: bool = False) -> MatchView | None: ...

    async def list_revisions(
        self, match_id: UUID, *, limit: int, offset: int
    ) -> tuple[Sequence[MatchResultRevision], int]: ...

    async def list_for_rating_backfill(
        self,
        *,
        after_completed_at: datetime | None,
        after_match_id: UUID | None,
        completed_from: datetime | None,
        completed_before: datetime | None,
        limit: int,
    ) -> Sequence[tuple[MatchView, UUID | None]]: ...


class RatingRepository(Protocol):
    async def add(self, rating: PublishedRating) -> PublishedRating: ...

    async def get(self, user_id: UUID, *, for_update: bool = False) -> PublishedRating | None: ...

    async def list_all(self, *, for_update: bool = False) -> Sequence[PublishedRating]: ...

    async def delete(self, user_id: UUID) -> None: ...

    async def update_published_state(
        self,
        *,
        user_id: UUID,
        rating: Decimal,
        rd: Decimal,
        volatility: Decimal,
        as_of_period_id: UUID,
        rated_matches_count: int,
        expected_version: int,
    ) -> PublishedRating | None: ...


class RatingQueryRepository(Protocol):
    async def get_current(self, user_id: UUID) -> tuple[PublishedRating, RatingPeriod] | None: ...

    async def list_history(
        self,
        user_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[tuple[RatingPeriodResult, RatingPeriod]], int]: ...


class RatingPeriodRepository(Protocol):
    async def acquire_publication_lock(self) -> None: ...

    async def acquire_period_lock(self, starts_at: datetime) -> None: ...

    async def add(self, period: RatingPeriod) -> RatingPeriod: ...

    async def get(self, period_id: UUID, *, for_update: bool = False) -> RatingPeriod | None: ...

    async def get_by_start(
        self, starts_at: datetime, *, for_update: bool = False
    ) -> RatingPeriod | None: ...

    async def get_previous(self, starts_at: datetime) -> RatingPeriod | None: ...

    async def get_previous_published(self, starts_at: datetime) -> RatingPeriod | None: ...

    async def get_next_published(self, starts_at: datetime) -> RatingPeriod | None: ...

    async def get_active_published_version(self, period_id: UUID) -> int | None: ...

    async def list_open_ending_at_or_before(self, ends_at: datetime) -> Sequence[RatingPeriod]: ...

    async def list_calculating(self) -> Sequence[RatingPeriod]: ...

    async def list_non_open_from(self, starts_at: datetime) -> Sequence[RatingPeriod]: ...

    async def list_non_open_between(
        self, starts_at: datetime, ends_at: datetime
    ) -> Sequence[RatingPeriod]: ...

    async def reserve_calculation_version(
        self,
        *,
        period_id: UUID,
        calculation_version: int,
        expected_version: int,
    ) -> RatingPeriod | None: ...

    async def update_state(
        self,
        *,
        period_id: UUID,
        status: RatingPeriodStatus,
        calculation_version: int,
        published_at: datetime | None,
        expected_version: int,
    ) -> RatingPeriod | None: ...

    async def mark_calculated(
        self,
        *,
        period_id: UUID,
        calculation_version: int,
        expected_version: int,
    ) -> RatingPeriod | None: ...

    async def mark_shadow_calculated(
        self,
        *,
        period_id: UUID,
        calculation_version: int,
        expected_version: int,
    ) -> RatingPeriod | None: ...

    async def mark_published(
        self,
        *,
        period_id: UUID,
        calculation_version: int,
        published_at: datetime,
        expected_version: int,
    ) -> RatingPeriod | None: ...

    async def activate_rebuild_version(
        self,
        *,
        period_id: UUID,
        calculation_version: int,
        published_at: datetime,
        expected_version: int,
    ) -> RatingPeriod | None: ...


class RatingInputRepository(Protocol):
    async def add(self, item: RatingPeriodMatchInput) -> RatingPeriodMatchInput: ...

    async def add_all(
        self, items: Sequence[RatingPeriodMatchInput]
    ) -> Sequence[RatingPeriodMatchInput]: ...

    async def add_if_absent(
        self, item: RatingPeriodMatchInput
    ) -> tuple[RatingPeriodMatchInput, bool]: ...

    async def get(
        self, period_id: UUID, calculation_version: int, match_id: UUID
    ) -> RatingPeriodMatchInput | None: ...

    async def list_for_period(
        self, period_id: UUID, calculation_version: int
    ) -> Sequence[RatingPeriodMatchInput]: ...


class RatingResultRepository(Protocol):
    async def add(self, result: RatingPeriodResult) -> RatingPeriodResult: ...

    async def add_all(
        self, results: Sequence[RatingPeriodResult]
    ) -> Sequence[RatingPeriodResult]: ...

    async def list_for_period(
        self, period_id: UUID, calculation_version: int
    ) -> Sequence[RatingPeriodResult]: ...

    async def list_for_user(self, user_id: UUID) -> Sequence[RatingPeriodResult]: ...


class RatingRecalculationRepository(Protocol):
    async def add(self, request: RatingRecalculationRequest) -> RatingRecalculationRequest: ...

    async def get_by_revision(self, revision_id: UUID) -> RatingRecalculationRequest | None: ...

    async def list_by_status(
        self, status: RatingRecalculationStatus
    ) -> Sequence[RatingRecalculationRequest]: ...

    async def list_for_job(self, rebuild_job_id: UUID) -> Sequence[RatingRecalculationRequest]: ...

    async def link_to_job(
        self,
        *,
        request_id: UUID,
        rebuild_job_id: UUID,
        expected_rebuild_job_id: UUID | None,
    ) -> RatingRecalculationRequest | None: ...

    async def update_status(
        self,
        *,
        request_id: UUID,
        status: RatingRecalculationStatus,
        started_at: datetime | None,
        completed_at: datetime | None,
        error_code: str | None,
    ) -> RatingRecalculationRequest | None: ...


class RatingRebuildJobRepository(Protocol):
    async def add(self, job: RatingRebuildJob) -> RatingRebuildJob: ...

    async def get(self, job_id: UUID, *, for_update: bool = False) -> RatingRebuildJob | None: ...

    async def get_active(self, *, for_update: bool = False) -> RatingRebuildJob | None: ...

    async def get_publication_barrier(self) -> RatingRebuildJob | None: ...

    async def update_status(
        self,
        *,
        job_id: UUID,
        status: RatingRebuildStatus,
        expected_version: int,
        completed_at: datetime | None,
        error_code: str | None,
        started_at: datetime | None = None,
        ready_at: datetime | None = None,
    ) -> RatingRebuildJob | None: ...


class RatingRebuildPeriodRepository(Protocol):
    async def add(self, checkpoint: RatingRebuildPeriod) -> RatingRebuildPeriod: ...

    async def get(
        self,
        rebuild_job_id: UUID,
        period_id: UUID,
        *,
        for_update: bool = False,
    ) -> RatingRebuildPeriod | None: ...

    async def list_for_job(self, rebuild_job_id: UUID) -> Sequence[RatingRebuildPeriod]: ...

    async def update_status(
        self,
        *,
        rebuild_job_id: UUID,
        period_id: UUID,
        status: RatingRebuildPeriodStatus,
        expected_status: RatingRebuildPeriodStatus,
        completed_at: datetime | None,
        error_code: str | None,
    ) -> RatingRebuildPeriod | None: ...


class RatingsUnitOfWork(Protocol):
    matches: RatingMatchRepository
    ratings: RatingRepository
    periods: RatingPeriodRepository
    inputs: RatingInputRepository
    results: RatingResultRepository
    recalculations: RatingRecalculationRepository
    rebuild_jobs: RatingRebuildJobRepository
    rebuild_periods: RatingRebuildPeriodRepository

    async def __aenter__(self) -> "RatingsUnitOfWork": ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class RatingRebuildNotifier(Protocol):
    async def notify(self) -> None: ...


class NullRatingRebuildNotifier:
    async def notify(self) -> None:
        pass

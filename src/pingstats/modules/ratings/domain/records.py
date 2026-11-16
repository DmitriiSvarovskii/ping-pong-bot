"""Immutable records persisted by the ratings module."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID


class RatingPeriodStatus(StrEnum):
    OPEN = "open"
    CALCULATING = "calculating"
    PUBLISHED = "published"
    STALE = "stale"
    FAILED = "failed"


class RatingRecalculationStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class RatingRebuildStatus(StrEnum):
    PENDING = "pending"
    PREPARING = "preparing"
    READY = "ready"
    ACTIVATING = "activating"
    COMPLETED = "completed"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class RatingRebuildPeriodStatus(StrEnum):
    PENDING = "pending"
    INPUTS_PREPARED = "inputs_prepared"
    CALCULATED = "calculated"
    READY = "ready"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class PublishedRating:
    user_id: UUID
    rating: Decimal
    rd: Decimal
    volatility: Decimal
    as_of_period_id: UUID | None
    rated_matches_count: int
    updated_at: datetime
    version: int


@dataclass(frozen=True, slots=True)
class RatingPeriod:
    id: UUID
    starts_at: datetime
    ends_at: datetime
    status: RatingPeriodStatus
    algorithm: str
    algorithm_version: str
    parameters: dict[str, object]
    calculation_version: int
    calculated_version: int | None
    published_version: int | None
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime
    version: int


@dataclass(frozen=True, slots=True)
class RatingPeriodMatchInput:
    period_id: UUID
    calculation_version: int
    match_id: UUID
    source_match_version: int
    correction_revision_id: UUID | None
    result_fingerprint: str
    player1_id: UUID
    player2_id: UUID
    score1: Decimal
    score2: Decimal
    completed_at: datetime
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RatingPeriodResult:
    id: UUID
    period_id: UUID
    calculation_version: int
    user_id: UUID
    rating_before: Decimal
    rating_after: Decimal
    rd_before: Decimal
    rd_after: Decimal
    volatility_before: Decimal
    volatility_after: Decimal
    games_count: int
    is_active: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RatingRecalculationRequest:
    id: UUID
    from_period_id: UUID
    caused_by_match_id: UUID
    caused_by_revision_id: UUID
    rebuild_job_id: UUID | None
    status: RatingRecalculationStatus
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error_code: str | None


@dataclass(frozen=True, slots=True)
class RatingRebuildJob:
    id: UUID
    from_period_id: UUID
    through_period_id: UUID
    status: RatingRebuildStatus
    singleton_scope: int
    created_at: datetime
    started_at: datetime | None
    ready_at: datetime | None
    completed_at: datetime | None
    error_code: str | None
    version: int


@dataclass(frozen=True, slots=True)
class RatingRebuildPeriod:
    rebuild_job_id: UUID
    period_id: UUID
    source_version: int
    target_version: int
    status: RatingRebuildPeriodStatus
    created_at: datetime
    completed_at: datetime | None
    error_code: str | None

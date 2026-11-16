"""SQLAlchemy models for rating periods, inputs, results, and recalculation."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from pingstats.infrastructure.database.base import Base
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


def _enum_values(enum_type) -> list[str]:
    return [item.value for item in enum_type]


rating_period_status_enum = Enum(
    RatingPeriodStatus,
    name="rating_period_status",
    values_callable=_enum_values,
)
rating_recalculation_status_enum = Enum(
    RatingRecalculationStatus,
    name="rating_recalculation_status",
    values_callable=_enum_values,
)
rating_rebuild_status_enum = Enum(
    RatingRebuildStatus,
    name="rating_rebuild_status",
    values_callable=_enum_values,
)
rating_rebuild_period_status_enum = Enum(
    RatingRebuildPeriodStatus,
    name="rating_rebuild_period_status",
    values_callable=_enum_values,
)

POSITIVE_RATING = "{column} > 0 AND {column} < 'Infinity'::numeric"


class RatingPeriodModel(Base):
    __tablename__ = "rating_periods"
    __table_args__ = (
        CheckConstraint(
            "starts_at = (date_trunc('day', starts_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')",
            name="starts_at_utc_midnight",
        ),
        CheckConstraint(
            "ends_at = (date_trunc('day', ends_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')",
            name="ends_at_utc_midnight",
        ),
        CheckConstraint("ends_at = starts_at + INTERVAL '1 day'", name="one_utc_day"),
        CheckConstraint("calculation_version > 0", name="calculation_version_positive"),
        CheckConstraint(
            "calculated_version IS NULL OR "
            "(calculated_version > 0 AND calculated_version <= calculation_version)",
            name="calculated_version_valid",
        ),
        CheckConstraint(
            "published_version IS NULL OR "
            "(published_version > 0 AND published_version <= calculation_version)",
            name="published_version_valid",
        ),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint("jsonb_typeof(parameters) = 'object'", name="parameters_object"),
        UniqueConstraint("starts_at", name="uq_rating_periods_starts_at"),
        UniqueConstraint("ends_at", name="uq_rating_periods_ends_at"),
        ExcludeConstraint(
            (func.tstzrange(text("starts_at"), text("ends_at"), "[)"), "&&"),
            name="ex_rating_periods_no_overlap",
            using="gist",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[RatingPeriodStatus] = mapped_column(
        rating_period_status_enum,
        nullable=False,
        default=RatingPeriodStatus.OPEN,
        server_default=RatingPeriodStatus.OPEN.value,
    )
    algorithm: Mapped[str] = mapped_column(String(32), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(32), nullable=False)
    parameters: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    calculation_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    calculated_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    published_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    def to_domain(self) -> RatingPeriod:
        return RatingPeriod(
            id=self.id,
            starts_at=self.starts_at,
            ends_at=self.ends_at,
            status=self.status,
            algorithm=self.algorithm,
            algorithm_version=self.algorithm_version,
            parameters=self.parameters,
            calculation_version=self.calculation_version,
            calculated_version=self.calculated_version,
            published_version=self.published_version,
            published_at=self.published_at,
            created_at=self.created_at,
            updated_at=self.updated_at,
            version=self.version,
        )


class RatingModel(Base):
    __tablename__ = "ratings"
    __table_args__ = (
        CheckConstraint(POSITIVE_RATING.format(column="rating"), name="rating_positive_finite"),
        CheckConstraint(POSITIVE_RATING.format(column="rd"), name="rd_positive_finite"),
        CheckConstraint(
            POSITIVE_RATING.format(column="volatility"),
            name="volatility_positive_finite",
        ),
        CheckConstraint("rated_matches_count >= 0", name="rated_matches_count_nonnegative"),
        CheckConstraint("version > 0", name="version_positive"),
        Index(
            "ix_ratings_leaderboard",
            text("rating DESC"),
            text("rd ASC"),
            text("rated_matches_count DESC"),
            "user_id",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    rating: Mapped[Decimal] = mapped_column(Numeric(10, 5), nullable=False)
    rd: Mapped[Decimal] = mapped_column(Numeric(10, 5), nullable=False)
    volatility: Mapped[Decimal] = mapped_column(Numeric(12, 10), nullable=False)
    as_of_period_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("rating_periods.id", ondelete="RESTRICT"),
        nullable=True,
    )
    rated_matches_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    def to_domain(self) -> PublishedRating:
        return PublishedRating(
            user_id=self.user_id,
            rating=self.rating,
            rd=self.rd,
            volatility=self.volatility,
            as_of_period_id=self.as_of_period_id,
            rated_matches_count=self.rated_matches_count,
            updated_at=self.updated_at,
            version=self.version,
        )


class RatingPeriodMatchInputModel(Base):
    __tablename__ = "rating_period_match_inputs"
    __table_args__ = (
        CheckConstraint("calculation_version > 0", name="calculation_version_positive"),
        CheckConstraint("source_match_version > 0", name="source_match_version_positive"),
        CheckConstraint("player1_id <> player2_id", name="players_distinct"),
        CheckConstraint("score1 IN (0, 0.5, 1)", name="score1_valid"),
        CheckConstraint("score2 IN (0, 0.5, 1)", name="score2_valid"),
        CheckConstraint("score1 + score2 = 1", name="scores_complementary"),
        CheckConstraint("char_length(result_fingerprint) = 64", name="fingerprint_length"),
        Index(
            "ix_rating_period_match_inputs_match_version",
            "match_id",
            text("calculation_version DESC"),
        ),
        Index("ix_rating_period_match_inputs_player1_period", "player1_id", "period_id"),
        Index("ix_rating_period_match_inputs_player2_period", "player2_id", "period_id"),
    )

    period_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("rating_periods.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    calculation_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("matches.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    source_match_version: Mapped[int] = mapped_column(Integer, nullable=False)
    correction_revision_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("match_result_revisions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    result_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    player1_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    player2_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    score1: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False)
    score2: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def to_domain(self) -> RatingPeriodMatchInput:
        return RatingPeriodMatchInput(
            period_id=self.period_id,
            calculation_version=self.calculation_version,
            match_id=self.match_id,
            source_match_version=self.source_match_version,
            correction_revision_id=self.correction_revision_id,
            result_fingerprint=self.result_fingerprint,
            player1_id=self.player1_id,
            player2_id=self.player2_id,
            score1=self.score1,
            score2=self.score2,
            completed_at=self.completed_at,
            created_at=self.created_at,
        )


class RatingPeriodResultModel(Base):
    __tablename__ = "rating_period_results"
    __table_args__ = (
        CheckConstraint("calculation_version > 0", name="calculation_version_positive"),
        CheckConstraint(
            POSITIVE_RATING.format(column="rating_before"),
            name="rating_before_positive_finite",
        ),
        CheckConstraint(
            POSITIVE_RATING.format(column="rating_after"),
            name="rating_after_positive_finite",
        ),
        CheckConstraint(
            POSITIVE_RATING.format(column="rd_before"), name="rd_before_positive_finite"
        ),
        CheckConstraint(POSITIVE_RATING.format(column="rd_after"), name="rd_after_positive_finite"),
        CheckConstraint(
            POSITIVE_RATING.format(column="volatility_before"),
            name="volatility_before_positive_finite",
        ),
        CheckConstraint(
            POSITIVE_RATING.format(column="volatility_after"),
            name="volatility_after_positive_finite",
        ),
        CheckConstraint("games_count >= 0", name="games_count_nonnegative"),
        UniqueConstraint(
            "period_id",
            "calculation_version",
            "user_id",
            name="uq_rating_period_results_period_version_user",
        ),
        Index(
            "ix_rating_period_results_user_history",
            "user_id",
            text("period_id DESC"),
            text("calculation_version DESC"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    period_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("rating_periods.id", ondelete="RESTRICT"), nullable=False
    )
    calculation_version: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    rating_before: Mapped[Decimal] = mapped_column(Numeric(10, 5), nullable=False)
    rating_after: Mapped[Decimal] = mapped_column(Numeric(10, 5), nullable=False)
    rd_before: Mapped[Decimal] = mapped_column(Numeric(10, 5), nullable=False)
    rd_after: Mapped[Decimal] = mapped_column(Numeric(10, 5), nullable=False)
    volatility_before: Mapped[Decimal] = mapped_column(Numeric(12, 10), nullable=False)
    volatility_after: Mapped[Decimal] = mapped_column(Numeric(12, 10), nullable=False)
    games_count: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def to_domain(self) -> RatingPeriodResult:
        return RatingPeriodResult(
            id=self.id,
            period_id=self.period_id,
            calculation_version=self.calculation_version,
            user_id=self.user_id,
            rating_before=self.rating_before,
            rating_after=self.rating_after,
            rd_before=self.rd_before,
            rd_after=self.rd_after,
            volatility_before=self.volatility_before,
            volatility_after=self.volatility_after,
            games_count=self.games_count,
            is_active=self.is_active,
            created_at=self.created_at,
        )


class RatingRecalculationRequestModel(Base):
    __tablename__ = "rating_recalculation_requests"
    __table_args__ = (
        UniqueConstraint(
            "caused_by_revision_id",
            name="uq_rating_recalculation_requests_caused_by_revision_id",
        ),
        Index("ix_rating_recalculation_requests_status_created", "status", "created_at"),
        Index(
            "ix_rating_recalculation_requests_rebuild_job_created",
            "rebuild_job_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    from_period_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("rating_periods.id", ondelete="RESTRICT"), nullable=False
    )
    caused_by_match_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("matches.id", ondelete="RESTRICT"), nullable=False
    )
    caused_by_revision_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("match_result_revisions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    rebuild_job_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("rating_rebuild_jobs.id", ondelete="RESTRICT"),
        nullable=True,
    )
    status: Mapped[RatingRecalculationStatus] = mapped_column(
        rating_recalculation_status_enum,
        nullable=False,
        default=RatingRecalculationStatus.PENDING,
        server_default=RatingRecalculationStatus.PENDING.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    def to_domain(self) -> RatingRecalculationRequest:
        return RatingRecalculationRequest(
            id=self.id,
            from_period_id=self.from_period_id,
            caused_by_match_id=self.caused_by_match_id,
            caused_by_revision_id=self.caused_by_revision_id,
            rebuild_job_id=self.rebuild_job_id,
            status=self.status,
            created_at=self.created_at,
            started_at=self.started_at,
            completed_at=self.completed_at,
            error_code=self.error_code,
        )


class RatingRebuildJobModel(Base):
    __tablename__ = "rating_rebuild_jobs"
    __table_args__ = (
        CheckConstraint("singleton_scope = 1", name="singleton_scope_global"),
        CheckConstraint("version > 0", name="version_positive"),
        Index("ix_rating_rebuild_jobs_status_created", "status", "created_at", "id"),
        Index(
            "uq_rating_rebuild_jobs_one_active_scope",
            "singleton_scope",
            unique=True,
            postgresql_where=text(
                "status IN ('pending', 'preparing', 'ready', 'activating', 'failed')"
            ),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    from_period_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("rating_periods.id", ondelete="RESTRICT"), nullable=False
    )
    through_period_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("rating_periods.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[RatingRebuildStatus] = mapped_column(
        rating_rebuild_status_enum,
        nullable=False,
        default=RatingRebuildStatus.PENDING,
        server_default=RatingRebuildStatus.PENDING.value,
    )
    singleton_scope: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    def to_domain(self) -> RatingRebuildJob:
        return RatingRebuildJob(
            id=self.id,
            from_period_id=self.from_period_id,
            through_period_id=self.through_period_id,
            status=self.status,
            singleton_scope=self.singleton_scope,
            created_at=self.created_at,
            started_at=self.started_at,
            ready_at=self.ready_at,
            completed_at=self.completed_at,
            error_code=self.error_code,
            version=self.version,
        )


class RatingRebuildPeriodModel(Base):
    __tablename__ = "rating_rebuild_periods"
    __table_args__ = (
        CheckConstraint("source_version > 0", name="source_version_positive"),
        CheckConstraint("target_version > source_version", name="target_version_after_source"),
        UniqueConstraint(
            "period_id",
            "target_version",
            name="uq_rating_rebuild_periods_period_target_version",
        ),
        Index("ix_rating_rebuild_periods_job_status", "rebuild_job_id", "status"),
    )

    rebuild_job_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("rating_rebuild_jobs.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    period_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("rating_periods.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    target_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[RatingRebuildPeriodStatus] = mapped_column(
        rating_rebuild_period_status_enum,
        nullable=False,
        default=RatingRebuildPeriodStatus.PENDING,
        server_default=RatingRebuildPeriodStatus.PENDING.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    def to_domain(self) -> RatingRebuildPeriod:
        return RatingRebuildPeriod(
            rebuild_job_id=self.rebuild_job_id,
            period_id=self.period_id,
            source_version=self.source_version,
            target_version=self.target_version,
            status=self.status,
            created_at=self.created_at,
            completed_at=self.completed_at,
            error_code=self.error_code,
        )

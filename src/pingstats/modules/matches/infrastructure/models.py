"""SQLAlchemy models for the shared match core."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pingstats.infrastructure.database.base import Base
from pingstats.modules.matches.domain import (
    Match,
    MatchContext,
    MatchParticipant,
    MatchResultRevision,
    MatchSet,
    MatchStatus,
    ResultSnapshot,
)


def _enum_values(enum_type) -> list[str]:
    return [item.value for item in enum_type]


match_context_enum = Enum(
    MatchContext,
    name="match_context",
    values_callable=_enum_values,
)
match_status_enum = Enum(
    MatchStatus,
    name="match_status",
    values_callable=_enum_values,
)


class MatchModel(Base):
    __tablename__ = "matches"
    __table_args__ = (
        CheckConstraint("points_to_win > 0", name="points_to_win_positive"),
        CheckConstraint("win_by > 0", name="win_by_positive"),
        CheckConstraint("sets_to_win > 0", name="sets_to_win_positive"),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint(
            "(context = 'game_session' AND game_session_id IS NOT NULL "
            "AND sequence_number IS NOT NULL AND sequence_number > 0) OR "
            "(context <> 'game_session' AND game_session_id IS NULL "
            "AND sequence_number IS NULL)",
            name="game_session_context_consistent",
        ),
        CheckConstraint(
            "(status = 'pending' AND started_at IS NULL AND completed_at IS NULL "
            "AND cancelled_at IS NULL AND winner_id IS NULL) OR "
            "(status = 'active' AND started_at IS NOT NULL AND completed_at IS NULL "
            "AND cancelled_at IS NULL AND winner_id IS NULL) OR "
            "(status = 'completed' AND started_at IS NOT NULL AND completed_at IS NOT NULL "
            "AND cancelled_at IS NULL AND winner_id IS NOT NULL) OR "
            "(status = 'cancelled' AND completed_at IS NULL AND cancelled_at IS NOT NULL "
            "AND winner_id IS NULL) OR "
            "(status = 'rejected' AND started_at IS NULL AND completed_at IS NULL "
            "AND cancelled_at IS NULL AND winner_id IS NULL)",
            name="lifecycle_consistent",
        ),
        Index("ix_matches_status_created", "status", text("created_at DESC")),
        Index("ix_matches_completed_at_desc", text("completed_at DESC")),
        Index(
            "ix_matches_ranked_completed",
            text("completed_at DESC"),
            postgresql_where=text("is_ranked AND status = 'completed'"),
        ),
        Index(
            "uq_matches_active_game_session",
            "game_session_id",
            unique=True,
            postgresql_where=text("context = 'game_session' AND status = 'active'"),
        ),
        UniqueConstraint(
            "game_session_id",
            "sequence_number",
            name="uq_matches_game_session_sequence",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    context: Mapped[MatchContext] = mapped_column(match_context_enum, nullable=False)
    status: Mapped[MatchStatus] = mapped_column(
        match_status_enum,
        nullable=False,
        default=MatchStatus.PENDING,
        server_default=MatchStatus.PENDING.value,
    )
    created_by: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    points_to_win: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    win_by: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=2, server_default="2")
    sets_to_win: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_ranked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    winner_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    game_session_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("game_sessions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    sequence_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    def to_domain(self) -> Match:
        return Match(
            id=self.id,
            context=self.context,
            status=self.status,
            created_by=self.created_by,
            points_to_win=self.points_to_win,
            win_by=self.win_by,
            sets_to_win=self.sets_to_win,
            is_ranked=self.is_ranked,
            winner_id=self.winner_id,
            created_at=self.created_at,
            updated_at=self.updated_at,
            started_at=self.started_at,
            completed_at=self.completed_at,
            cancelled_at=self.cancelled_at,
            version=self.version,
            game_session_id=self.game_session_id,
            sequence_number=self.sequence_number,
        )


class MatchParticipantModel(Base):
    __tablename__ = "match_participants"
    __table_args__ = (
        CheckConstraint("position IN (1, 2)", name="position_valid"),
        UniqueConstraint("match_id", "user_id", name="uq_match_participants_match_user"),
        UniqueConstraint("match_id", "position", name="uq_match_participants_match_position"),
        Index("ix_match_participants_user_match", "user_id", "match_id"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    match_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("matches.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    def to_domain(self) -> MatchParticipant:
        return MatchParticipant(
            id=self.id,
            match_id=self.match_id,
            user_id=self.user_id,
            position=self.position,
        )


class MatchSetModel(Base):
    __tablename__ = "match_sets"
    __table_args__ = (
        CheckConstraint("set_number > 0", name="set_number_positive"),
        CheckConstraint("player1_score >= 0 AND player2_score >= 0", name="scores_nonnegative"),
        CheckConstraint("player1_score <> player2_score", name="scores_not_tied"),
        UniqueConstraint("match_id", "set_number", name="uq_match_sets_match_number"),
        Index("ix_match_sets_match_number", "match_id", "set_number"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    match_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("matches.id", ondelete="CASCADE"), nullable=False
    )
    set_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    player1_score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    player2_score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def to_domain(self) -> MatchSet:
        return MatchSet(
            id=self.id,
            match_id=self.match_id,
            set_number=self.set_number,
            player1_score=self.player1_score,
            player2_score=self.player2_score,
            created_at=self.created_at,
        )


class MatchResultRevisionModel(Base):
    __tablename__ = "match_result_revisions"
    __table_args__ = (
        CheckConstraint("revision_number > 0", name="revision_number_positive"),
        UniqueConstraint(
            "match_id", "revision_number", name="uq_match_result_revisions_match_number"
        ),
        Index(
            "ix_match_result_revisions_match_created",
            "match_id",
            text("created_at DESC"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    match_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("matches.id", ondelete="RESTRICT"), nullable=False
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    changed_by: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    previous_result: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    new_result: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def to_domain(self) -> MatchResultRevision:
        return MatchResultRevision(
            id=self.id,
            match_id=self.match_id,
            revision_number=self.revision_number,
            changed_by=self.changed_by,
            previous_result=ResultSnapshot.from_json(self.previous_result),
            new_result=ResultSnapshot.from_json(self.new_result),
            reason=self.reason,
            created_at=self.created_at,
        )

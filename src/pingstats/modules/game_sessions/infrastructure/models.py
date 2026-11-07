"""SQLAlchemy persistence models for free game sessions."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pingstats.infrastructure.database.base import Base
from pingstats.modules.game_sessions.domain import (
    GameSession,
    GameSessionEventType,
    GameSessionParticipant,
    GameSessionQueueEvent,
    GameSessionStatus,
)


def _enum_values(enum_type: type[GameSessionStatus]) -> list[str]:
    return [item.value for item in enum_type]


game_session_status_enum = Enum(
    GameSessionStatus,
    name="game_session_status",
    values_callable=_enum_values,
)


class GameSessionModel(Base):
    __tablename__ = "game_sessions"
    __table_args__ = (
        CheckConstraint("points_to_win > 0", name="points_to_win_positive"),
        CheckConstraint("sets_to_win > 0", name="sets_to_win_positive"),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint(
            "completed_at IS NULL OR started_at IS NOT NULL",
            name="completion_requires_start",
        ),
        Index("ix_game_sessions_organizer_created", "organizer_id", text("created_at DESC")),
        Index("ix_game_sessions_status_created", "status", text("created_at DESC")),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organizer_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[GameSessionStatus] = mapped_column(
        game_session_status_enum,
        nullable=False,
        default=GameSessionStatus.PENDING,
        server_default=GameSessionStatus.PENDING.value,
    )
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    points_to_win: Mapped[int] = mapped_column(
        Integer, nullable=False, default=11, server_default="11"
    )
    sets_to_win: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    is_ranked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    def to_domain(self) -> GameSession:
        return GameSession(
            id=self.id,
            organizer_id=self.organizer_id,
            status=self.status,
            name=self.name,
            points_to_win=self.points_to_win,
            sets_to_win=self.sets_to_win,
            is_ranked=self.is_ranked,
            created_at=self.created_at,
            started_at=self.started_at,
            completed_at=self.completed_at,
            version=self.version,
        )


class GameSessionParticipantModel(Base):
    __tablename__ = "game_session_participants"
    __table_args__ = (
        CheckConstraint("left_at IS NULL OR left_at >= joined_at", name="left_not_before_joined"),
        UniqueConstraint(
            "id",
            "session_id",
            name="uq_game_session_participants_id_session",
        ),
        Index("ix_game_session_participants_user_session", "user_id", "session_id"),
        Index(
            "uq_game_session_participants_active_user",
            "session_id",
            "user_id",
            unique=True,
            postgresql_where=text("left_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    session_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("game_sessions.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def to_domain(self) -> GameSessionParticipant:
        return GameSessionParticipant(
            id=self.id,
            session_id=self.session_id,
            user_id=self.user_id,
            joined_at=self.joined_at,
            left_at=self.left_at,
        )


class GameSessionQueueModel(Base):
    __tablename__ = "game_session_queue"
    __table_args__ = (
        CheckConstraint("position > 0", name="position_positive"),
        ForeignKeyConstraint(
            ["participant_id", "session_id"],
            ["game_session_participants.id", "game_session_participants.session_id"],
            name="fk_game_session_queue_participant_session",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "session_id",
            "position",
            name="uq_game_session_queue_session_position",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        UniqueConstraint(
            "session_id",
            "participant_id",
            name="uq_game_session_queue_session_participant",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        Index("ix_game_session_queue_session_position", "session_id", "position"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    session_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("game_sessions.id", ondelete="CASCADE"), nullable=False
    )
    participant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GameSessionCurrentPairModel(Base):
    __tablename__ = "game_session_current_pairs"
    __table_args__ = (
        CheckConstraint(
            "player1_participant_id <> player2_participant_id",
            name="players_distinct",
        ),
        ForeignKeyConstraint(
            ["player1_participant_id", "session_id"],
            ["game_session_participants.id", "game_session_participants.session_id"],
            name="fk_game_session_current_pairs_player1_session",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["player2_participant_id", "session_id"],
            ["game_session_participants.id", "game_session_participants.session_id"],
            name="fk_game_session_current_pairs_player2_session",
            ondelete="RESTRICT",
        ),
    )

    session_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("game_sessions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    player1_participant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    player2_participant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class GameSessionProcessedResultModel(Base):
    __tablename__ = "game_session_processed_results"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "result_id",
            name="uq_game_session_processed_results_session_result",
        ),
        Index(
            "ix_game_session_processed_results_session_processed",
            "session_id",
            text("processed_at DESC"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    session_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("game_sessions.id", ondelete="CASCADE"), nullable=False
    )
    result_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GameSessionQueueEventModel(Base):
    __tablename__ = "game_session_queue_events"
    __table_args__ = (
        CheckConstraint("length(btrim(event_type)) > 0", name="event_type_not_blank"),
        ForeignKeyConstraint(
            ["participant_id", "session_id"],
            ["game_session_participants.id", "game_session_participants.session_id"],
            name="fk_game_session_queue_events_participant_session",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_game_session_queue_events_session_created",
            "session_id",
            text("created_at DESC"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    session_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("game_sessions.id", ondelete="CASCADE"), nullable=False
    )
    participant_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def to_domain(self) -> GameSessionQueueEvent:
        return GameSessionQueueEvent(
            id=self.id,
            session_id=self.session_id,
            participant_id=self.participant_id,
            event_type=GameSessionEventType(self.event_type),
            payload=self.payload,
            created_at=self.created_at,
        )

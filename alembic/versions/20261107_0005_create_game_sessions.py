"""Create game session persistence schema.

Revision ID: 20261107_0005
Revises: 20261107_0004
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20261107_0005"
down_revision: str | None = "20261107_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

game_session_status = postgresql.ENUM(
    "pending",
    "active",
    "completed",
    "cancelled",
    name="game_session_status",
    create_type=False,
)


def upgrade() -> None:
    postgresql.ENUM(
        "pending",
        "active",
        "completed",
        "cancelled",
        name="game_session_status",
    ).create(op.get_bind(), checkfirst=False)

    op.create_table(
        "game_sessions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organizer_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            game_session_status,
            server_default=sa.text("'pending'::game_session_status"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=128), nullable=True),
        sa.Column("points_to_win", sa.Integer(), server_default=sa.text("11"), nullable=False),
        sa.Column("sets_to_win", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("is_ranked", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "completed_at IS NULL OR started_at IS NOT NULL",
            name=op.f("ck_game_sessions_completion_requires_start"),
        ),
        sa.CheckConstraint(
            "points_to_win > 0",
            name=op.f("ck_game_sessions_points_to_win_positive"),
        ),
        sa.CheckConstraint(
            "sets_to_win > 0",
            name=op.f("ck_game_sessions_sets_to_win_positive"),
        ),
        sa.CheckConstraint("version > 0", name=op.f("ck_game_sessions_version_positive")),
        sa.ForeignKeyConstraint(
            ["organizer_id"],
            ["users.id"],
            name=op.f("fk_game_sessions_organizer_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_game_sessions")),
    )
    op.create_index(
        "ix_game_sessions_organizer_created",
        "game_sessions",
        ["organizer_id", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_game_sessions_status_created",
        "game_sessions",
        ["status", sa.literal_column("created_at DESC")],
        unique=False,
    )

    op.create_table(
        "game_session_participants",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "left_at IS NULL OR left_at >= joined_at",
            name=op.f("ck_game_session_participants_left_not_before_joined"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["game_sessions.id"],
            name=op.f("fk_game_session_participants_session_id_game_sessions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_game_session_participants_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_game_session_participants")),
        sa.UniqueConstraint(
            "id",
            "session_id",
            name="uq_game_session_participants_id_session",
        ),
    )
    op.create_index(
        "ix_game_session_participants_user_session",
        "game_session_participants",
        ["user_id", "session_id"],
        unique=False,
    )
    op.create_index(
        "uq_game_session_participants_active_user",
        "game_session_participants",
        ["session_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("left_at IS NULL"),
    )

    op.create_table(
        "game_session_queue",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("participant_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "position > 0",
            name=op.f("ck_game_session_queue_position_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["game_sessions.id"],
            name=op.f("fk_game_session_queue_session_id_game_sessions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["participant_id", "session_id"],
            ["game_session_participants.id", "game_session_participants.session_id"],
            name="fk_game_session_queue_participant_session",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_game_session_queue")),
        sa.UniqueConstraint(
            "session_id",
            "participant_id",
            name="uq_game_session_queue_session_participant",
            deferrable=True,
            initially="IMMEDIATE",
        ),
        sa.UniqueConstraint(
            "session_id",
            "position",
            name="uq_game_session_queue_session_position",
            deferrable=True,
            initially="IMMEDIATE",
        ),
    )
    op.create_index(
        "ix_game_session_queue_session_position",
        "game_session_queue",
        ["session_id", "position"],
        unique=False,
    )

    op.create_table(
        "game_session_queue_events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("participant_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(btrim(event_type)) > 0",
            name=op.f("ck_game_session_queue_events_event_type_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["game_sessions.id"],
            name=op.f("fk_game_session_queue_events_session_id_game_sessions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["participant_id", "session_id"],
            ["game_session_participants.id", "game_session_participants.session_id"],
            name="fk_game_session_queue_events_participant_session",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_game_session_queue_events")),
    )
    op.create_index(
        "ix_game_session_queue_events_session_created",
        "game_session_queue_events",
        ["session_id", sa.literal_column("created_at DESC")],
        unique=False,
    )

    op.add_column("matches", sa.Column("game_session_id", sa.Uuid(), nullable=True))
    op.add_column("matches", sa.Column("sequence_number", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_matches_game_session_id_game_sessions",
        "matches",
        "game_sessions",
        ["game_session_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_matches_game_session_context_consistent"),
        "matches",
        "(context = 'game_session' AND game_session_id IS NOT NULL "
        "AND sequence_number IS NOT NULL AND sequence_number > 0) OR "
        "(context <> 'game_session' AND game_session_id IS NULL "
        "AND sequence_number IS NULL)",
    )
    op.create_unique_constraint(
        "uq_matches_game_session_sequence",
        "matches",
        ["game_session_id", "sequence_number"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_matches_game_session_sequence", "matches", type_="unique")
    op.drop_constraint(
        op.f("ck_matches_game_session_context_consistent"),
        "matches",
        type_="check",
    )
    op.drop_constraint(
        "fk_matches_game_session_id_game_sessions",
        "matches",
        type_="foreignkey",
    )
    op.drop_column("matches", "sequence_number")
    op.drop_column("matches", "game_session_id")

    op.drop_index(
        "ix_game_session_queue_events_session_created",
        table_name="game_session_queue_events",
    )
    op.drop_table("game_session_queue_events")
    op.drop_index(
        "ix_game_session_queue_session_position",
        table_name="game_session_queue",
    )
    op.drop_table("game_session_queue")
    op.drop_index(
        "uq_game_session_participants_active_user",
        table_name="game_session_participants",
    )
    op.drop_index(
        "ix_game_session_participants_user_session",
        table_name="game_session_participants",
    )
    op.drop_table("game_session_participants")
    op.drop_index("ix_game_sessions_status_created", table_name="game_sessions")
    op.drop_index("ix_game_sessions_organizer_created", table_name="game_sessions")
    op.drop_table("game_sessions")
    game_session_status.drop(op.get_bind(), checkfirst=False)

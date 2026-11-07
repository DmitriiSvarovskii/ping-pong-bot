"""Add durable current pair and rotation idempotency.

Revision ID: 20261107_0006
Revises: 20261107_0005
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261107_0006"
down_revision: str | None = "20261107_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "game_session_current_pairs",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("player1_participant_id", sa.Uuid(), nullable=False),
        sa.Column("player2_participant_id", sa.Uuid(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "player1_participant_id <> player2_participant_id",
            name=op.f("ck_game_session_current_pairs_players_distinct"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["game_sessions.id"],
            name=op.f("fk_game_session_current_pairs_session_id_game_sessions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["player1_participant_id", "session_id"],
            ["game_session_participants.id", "game_session_participants.session_id"],
            name="fk_game_session_current_pairs_player1_session",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["player2_participant_id", "session_id"],
            ["game_session_participants.id", "game_session_participants.session_id"],
            name="fk_game_session_current_pairs_player2_session",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("session_id", name=op.f("pk_game_session_current_pairs")),
    )
    op.create_table(
        "game_session_processed_results",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("result_id", sa.Uuid(), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["game_sessions.id"],
            name=op.f("fk_game_session_processed_results_session_id_game_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_game_session_processed_results")),
        sa.UniqueConstraint(
            "session_id",
            "result_id",
            name="uq_game_session_processed_results_session_result",
        ),
    )
    op.create_index(
        "ix_game_session_processed_results_session_processed",
        "game_session_processed_results",
        ["session_id", sa.literal_column("processed_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_game_session_processed_results_session_processed",
        table_name="game_session_processed_results",
    )
    op.drop_table("game_session_processed_results")
    op.drop_table("game_session_current_pairs")

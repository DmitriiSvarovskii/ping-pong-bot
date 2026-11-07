"""Create shared match core tables.

Revision ID: 20261107_0004
Revises: 20261107_0003
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20261107_0004"
down_revision: str | None = "20261107_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

match_context = postgresql.ENUM(
    "standalone", "game_session", "tournament", name="match_context", create_type=False
)
match_status = postgresql.ENUM(
    "pending",
    "active",
    "completed",
    "cancelled",
    "rejected",
    name="match_status",
    create_type=False,
)


def upgrade() -> None:
    postgresql.ENUM("standalone", "game_session", "tournament", name="match_context").create(
        op.get_bind(), checkfirst=False
    )
    postgresql.ENUM(
        "pending",
        "active",
        "completed",
        "cancelled",
        "rejected",
        name="match_status",
    ).create(op.get_bind(), checkfirst=False)
    op.create_table(
        "matches",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("context", match_context, nullable=False),
        sa.Column(
            "status",
            match_status,
            server_default=sa.text("'pending'::match_status"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("points_to_win", sa.SmallInteger(), nullable=False),
        sa.Column("win_by", sa.SmallInteger(), server_default=sa.text("2"), nullable=False),
        sa.Column("sets_to_win", sa.SmallInteger(), nullable=False),
        sa.Column("is_ranked", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("winner_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint("points_to_win > 0", name=op.f("ck_matches_points_to_win_positive")),
        sa.CheckConstraint("win_by > 0", name=op.f("ck_matches_win_by_positive")),
        sa.CheckConstraint("sets_to_win > 0", name=op.f("ck_matches_sets_to_win_positive")),
        sa.CheckConstraint("version > 0", name=op.f("ck_matches_version_positive")),
        sa.CheckConstraint(
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
            name=op.f("ck_matches_lifecycle_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_matches_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["winner_id"],
            ["users.id"],
            name=op.f("fk_matches_winner_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_matches")),
    )
    op.create_index(
        "ix_matches_completed_at_desc",
        "matches",
        [sa.literal_column("completed_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_matches_ranked_completed",
        "matches",
        [sa.literal_column("completed_at DESC")],
        unique=False,
        postgresql_where=sa.text("is_ranked AND status = 'completed'"),
    )
    op.create_index(
        "ix_matches_status_created",
        "matches",
        ["status", sa.literal_column("created_at DESC")],
        unique=False,
    )

    op.create_table(
        "match_participants",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("match_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint("position IN (1, 2)", name=op.f("ck_match_participants_position_valid")),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["matches.id"],
            name=op.f("fk_match_participants_match_id_matches"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_match_participants_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_match_participants")),
        sa.UniqueConstraint("match_id", "position", name="uq_match_participants_match_position"),
        sa.UniqueConstraint("match_id", "user_id", name="uq_match_participants_match_user"),
    )
    op.create_index(
        "ix_match_participants_user_match",
        "match_participants",
        ["user_id", "match_id"],
        unique=False,
    )

    op.create_table(
        "match_sets",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("match_id", sa.Uuid(), nullable=False),
        sa.Column("set_number", sa.SmallInteger(), nullable=False),
        sa.Column("player1_score", sa.SmallInteger(), nullable=False),
        sa.Column("player2_score", sa.SmallInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("set_number > 0", name=op.f("ck_match_sets_set_number_positive")),
        sa.CheckConstraint(
            "player1_score >= 0 AND player2_score >= 0",
            name=op.f("ck_match_sets_scores_nonnegative"),
        ),
        sa.CheckConstraint(
            "player1_score <> player2_score", name=op.f("ck_match_sets_scores_not_tied")
        ),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["matches.id"],
            name=op.f("fk_match_sets_match_id_matches"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_match_sets")),
        sa.UniqueConstraint("match_id", "set_number", name="uq_match_sets_match_number"),
    )
    op.create_index(
        "ix_match_sets_match_number",
        "match_sets",
        ["match_id", "set_number"],
        unique=False,
    )

    op.create_table(
        "match_result_revisions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("match_id", sa.Uuid(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("changed_by", sa.Uuid(), nullable=False),
        sa.Column("previous_result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("new_result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "revision_number > 0",
            name=op.f("ck_match_result_revisions_revision_number_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["changed_by"],
            ["users.id"],
            name=op.f("fk_match_result_revisions_changed_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["matches.id"],
            name=op.f("fk_match_result_revisions_match_id_matches"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_match_result_revisions")),
        sa.UniqueConstraint(
            "match_id",
            "revision_number",
            name="uq_match_result_revisions_match_number",
        ),
    )
    op.create_index(
        "ix_match_result_revisions_match_created",
        "match_result_revisions",
        ["match_id", sa.literal_column("created_at DESC")],
        unique=False,
    )

    op.execute(
        """
        CREATE FUNCTION check_match_participant_count_from_match()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (SELECT count(*) FROM match_participants WHERE match_id = NEW.id) <> 2 THEN
                RAISE EXCEPTION 'match % must have exactly two participants', NEW.id
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_matches_exactly_two_participants
        AFTER INSERT OR UPDATE ON matches
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION check_match_participant_count_from_match()
        """
    )
    op.execute(
        """
        CREATE FUNCTION check_match_participant_count_from_participant()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE target_match_id uuid := COALESCE(NEW.match_id, OLD.match_id);
        BEGIN
            IF EXISTS (SELECT 1 FROM matches WHERE id = target_match_id)
               AND (SELECT count(*) FROM match_participants WHERE match_id = target_match_id) <> 2
            THEN
                RAISE EXCEPTION 'match % must have exactly two participants', target_match_id
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_match_participants_exactly_two
        AFTER INSERT OR UPDATE OR DELETE ON match_participants
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION check_match_participant_count_from_participant()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_match_participants_exactly_two ON match_participants")
    op.execute("DROP FUNCTION check_match_participant_count_from_participant()")
    op.execute("DROP TRIGGER trg_matches_exactly_two_participants ON matches")
    op.execute("DROP FUNCTION check_match_participant_count_from_match()")
    op.drop_index("ix_match_result_revisions_match_created", table_name="match_result_revisions")
    op.drop_table("match_result_revisions")
    op.drop_index("ix_match_sets_match_number", table_name="match_sets")
    op.drop_table("match_sets")
    op.drop_index("ix_match_participants_user_match", table_name="match_participants")
    op.drop_table("match_participants")
    op.drop_index("ix_matches_status_created", table_name="matches")
    op.drop_index("ix_matches_ranked_completed", table_name="matches")
    op.drop_index("ix_matches_completed_at_desc", table_name="matches")
    op.drop_table("matches")
    match_status.drop(op.get_bind(), checkfirst=False)
    match_context.drop(op.get_bind(), checkfirst=False)

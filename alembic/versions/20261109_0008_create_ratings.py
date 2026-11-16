"""Create versioned ratings persistence.

Revision ID: 20261109_0008
Revises: 20261107_0007
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20261109_0008"
down_revision: str | None = "20261107_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

rating_period_status = postgresql.ENUM(
    "open",
    "calculating",
    "published",
    "stale",
    "failed",
    name="rating_period_status",
    create_type=False,
)
rating_recalculation_status = postgresql.ENUM(
    "pending",
    "processing",
    "completed",
    "failed",
    name="rating_recalculation_status",
    create_type=False,
)


def _positive_finite(column: str) -> str:
    return f"{column} > 0 AND {column} < 'Infinity'::numeric"


def upgrade() -> None:
    postgresql.ENUM(
        "open",
        "calculating",
        "published",
        "stale",
        "failed",
        name="rating_period_status",
    ).create(op.get_bind(), checkfirst=False)
    postgresql.ENUM(
        "pending",
        "processing",
        "completed",
        "failed",
        name="rating_recalculation_status",
    ).create(op.get_bind(), checkfirst=False)

    op.create_table(
        "rating_periods",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            rating_period_status,
            server_default=sa.text("'open'::rating_period_status"),
            nullable=False,
        ),
        sa.Column("algorithm", sa.String(length=32), nullable=False),
        sa.Column("algorithm_version", sa.String(length=32), nullable=False),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("calculation_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "starts_at = (date_trunc('day', starts_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')",
            name=op.f("ck_rating_periods_starts_at_utc_midnight"),
        ),
        sa.CheckConstraint(
            "ends_at = (date_trunc('day', ends_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')",
            name=op.f("ck_rating_periods_ends_at_utc_midnight"),
        ),
        sa.CheckConstraint(
            "ends_at = starts_at + INTERVAL '1 day'",
            name=op.f("ck_rating_periods_one_utc_day"),
        ),
        sa.CheckConstraint(
            "calculation_version > 0",
            name=op.f("ck_rating_periods_calculation_version_positive"),
        ),
        sa.CheckConstraint("version > 0", name=op.f("ck_rating_periods_version_positive")),
        sa.CheckConstraint(
            "jsonb_typeof(parameters) = 'object'",
            name=op.f("ck_rating_periods_parameters_object"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rating_periods")),
        sa.UniqueConstraint("starts_at", name="uq_rating_periods_starts_at"),
        sa.UniqueConstraint("ends_at", name="uq_rating_periods_ends_at"),
        postgresql.ExcludeConstraint(
            (sa.text("tstzrange(starts_at, ends_at, '[)')"), "&&"),
            name="ex_rating_periods_no_overlap",
            using="gist",
        ),
    )

    op.create_table(
        "ratings",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("rating", sa.Numeric(precision=10, scale=5), nullable=False),
        sa.Column("rd", sa.Numeric(precision=10, scale=5), nullable=False),
        sa.Column("volatility", sa.Numeric(precision=12, scale=10), nullable=False),
        sa.Column("as_of_period_id", sa.Uuid(), nullable=True),
        sa.Column("rated_matches_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            _positive_finite("rating"), name=op.f("ck_ratings_rating_positive_finite")
        ),
        sa.CheckConstraint(_positive_finite("rd"), name=op.f("ck_ratings_rd_positive_finite")),
        sa.CheckConstraint(
            _positive_finite("volatility"),
            name=op.f("ck_ratings_volatility_positive_finite"),
        ),
        sa.CheckConstraint(
            "rated_matches_count >= 0",
            name=op.f("ck_ratings_rated_matches_count_nonnegative"),
        ),
        sa.CheckConstraint("version > 0", name=op.f("ck_ratings_version_positive")),
        sa.ForeignKeyConstraint(
            ["as_of_period_id"],
            ["rating_periods.id"],
            name=op.f("fk_ratings_as_of_period_id_rating_periods"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_ratings_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_ratings")),
    )
    op.create_index(
        "ix_ratings_leaderboard",
        "ratings",
        [
            sa.literal_column("rating DESC"),
            sa.literal_column("rd ASC"),
            sa.literal_column("rated_matches_count DESC"),
            "user_id",
        ],
        unique=False,
    )

    op.create_table(
        "rating_period_match_inputs",
        sa.Column("period_id", sa.Uuid(), nullable=False),
        sa.Column("calculation_version", sa.Integer(), nullable=False),
        sa.Column("match_id", sa.Uuid(), nullable=False),
        sa.Column("source_match_version", sa.Integer(), nullable=False),
        sa.Column("correction_revision_id", sa.Uuid(), nullable=True),
        sa.Column("result_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("player1_id", sa.Uuid(), nullable=False),
        sa.Column("player2_id", sa.Uuid(), nullable=False),
        sa.Column("score1", sa.Numeric(precision=3, scale=2), nullable=False),
        sa.Column("score2", sa.Numeric(precision=3, scale=2), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "calculation_version > 0",
            name=op.f("ck_rating_period_match_inputs_calculation_version_positive"),
        ),
        sa.CheckConstraint(
            "source_match_version > 0",
            name=op.f("ck_rating_period_match_inputs_source_match_version_positive"),
        ),
        sa.CheckConstraint(
            "player1_id <> player2_id",
            name=op.f("ck_rating_period_match_inputs_players_distinct"),
        ),
        sa.CheckConstraint(
            "score1 IN (0, 0.5, 1)",
            name=op.f("ck_rating_period_match_inputs_score1_valid"),
        ),
        sa.CheckConstraint(
            "score2 IN (0, 0.5, 1)",
            name=op.f("ck_rating_period_match_inputs_score2_valid"),
        ),
        sa.CheckConstraint(
            "score1 + score2 = 1",
            name=op.f("ck_rating_period_match_inputs_scores_complementary"),
        ),
        sa.CheckConstraint(
            "char_length(result_fingerprint) = 64",
            name=op.f("ck_rating_period_match_inputs_fingerprint_length"),
        ),
        sa.ForeignKeyConstraint(
            ["correction_revision_id"],
            ["match_result_revisions.id"],
            name=op.f(
                "fk_rating_period_match_inputs_correction_revision_id_match_result_revisions"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["matches.id"],
            name=op.f("fk_rating_period_match_inputs_match_id_matches"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["period_id"],
            ["rating_periods.id"],
            name=op.f("fk_rating_period_match_inputs_period_id_rating_periods"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["player1_id"],
            ["users.id"],
            name=op.f("fk_rating_period_match_inputs_player1_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["player2_id"],
            ["users.id"],
            name=op.f("fk_rating_period_match_inputs_player2_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "period_id",
            "calculation_version",
            "match_id",
            name=op.f("pk_rating_period_match_inputs"),
        ),
    )
    op.create_index(
        "ix_rating_period_match_inputs_match_version",
        "rating_period_match_inputs",
        ["match_id", sa.literal_column("calculation_version DESC")],
        unique=False,
    )
    op.create_index(
        "ix_rating_period_match_inputs_player1_period",
        "rating_period_match_inputs",
        ["player1_id", "period_id"],
        unique=False,
    )
    op.create_index(
        "ix_rating_period_match_inputs_player2_period",
        "rating_period_match_inputs",
        ["player2_id", "period_id"],
        unique=False,
    )

    op.create_table(
        "rating_period_results",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("period_id", sa.Uuid(), nullable=False),
        sa.Column("calculation_version", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("rating_before", sa.Numeric(precision=10, scale=5), nullable=False),
        sa.Column("rating_after", sa.Numeric(precision=10, scale=5), nullable=False),
        sa.Column("rd_before", sa.Numeric(precision=10, scale=5), nullable=False),
        sa.Column("rd_after", sa.Numeric(precision=10, scale=5), nullable=False),
        sa.Column("volatility_before", sa.Numeric(precision=12, scale=10), nullable=False),
        sa.Column("volatility_after", sa.Numeric(precision=12, scale=10), nullable=False),
        sa.Column("games_count", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "calculation_version > 0",
            name=op.f("ck_rating_period_results_calculation_version_positive"),
        ),
        *(
            sa.CheckConstraint(
                _positive_finite(column),
                name=op.f(f"ck_rating_period_results_{column}_positive_finite"),
            )
            for column in (
                "rating_before",
                "rating_after",
                "rd_before",
                "rd_after",
                "volatility_before",
                "volatility_after",
            )
        ),
        sa.CheckConstraint(
            "games_count >= 0",
            name=op.f("ck_rating_period_results_games_count_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["period_id"],
            ["rating_periods.id"],
            name=op.f("fk_rating_period_results_period_id_rating_periods"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_rating_period_results_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rating_period_results")),
        sa.UniqueConstraint(
            "period_id",
            "calculation_version",
            "user_id",
            name="uq_rating_period_results_period_version_user",
        ),
    )
    op.create_index(
        "ix_rating_period_results_user_history",
        "rating_period_results",
        [
            "user_id",
            sa.literal_column("period_id DESC"),
            sa.literal_column("calculation_version DESC"),
        ],
        unique=False,
    )

    op.create_table(
        "rating_recalculation_requests",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("from_period_id", sa.Uuid(), nullable=False),
        sa.Column("caused_by_match_id", sa.Uuid(), nullable=False),
        sa.Column("caused_by_revision_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            rating_recalculation_status,
            server_default=sa.text("'pending'::rating_recalculation_status"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["caused_by_match_id"],
            ["matches.id"],
            name=op.f("fk_rating_recalculation_requests_caused_by_match_id_matches"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["caused_by_revision_id"],
            ["match_result_revisions.id"],
            name=op.f(
                "fk_rating_recalculation_requests_caused_by_revision_id_match_result_revisions"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["from_period_id"],
            ["rating_periods.id"],
            name=op.f("fk_rating_recalculation_requests_from_period_id_rating_periods"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rating_recalculation_requests")),
        sa.UniqueConstraint(
            "caused_by_revision_id",
            name="uq_rating_recalculation_requests_caused_by_revision_id",
        ),
    )
    op.create_index(
        "ix_rating_recalculation_requests_status_created",
        "rating_recalculation_requests",
        ["status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rating_recalculation_requests_status_created",
        table_name="rating_recalculation_requests",
    )
    op.drop_table("rating_recalculation_requests")
    op.drop_index("ix_rating_period_results_user_history", table_name="rating_period_results")
    op.drop_table("rating_period_results")
    op.drop_index(
        "ix_rating_period_match_inputs_player2_period",
        table_name="rating_period_match_inputs",
    )
    op.drop_index(
        "ix_rating_period_match_inputs_player1_period",
        table_name="rating_period_match_inputs",
    )
    op.drop_index(
        "ix_rating_period_match_inputs_match_version",
        table_name="rating_period_match_inputs",
    )
    op.drop_table("rating_period_match_inputs")
    op.drop_index("ix_ratings_leaderboard", table_name="ratings")
    op.drop_table("ratings")
    op.drop_table("rating_periods")
    rating_recalculation_status.drop(op.get_bind(), checkfirst=False)
    rating_period_status.drop(op.get_bind(), checkfirst=False)

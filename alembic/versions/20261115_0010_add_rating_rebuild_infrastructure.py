"""Add versioned rating rebuild infrastructure.

Revision ID: 20261115_0010
Revises: 20261110_0009
Create Date: 2026-11-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20261115_0010"
down_revision: str | None = "20261110_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

rating_rebuild_status = postgresql.ENUM(
    "pending",
    "preparing",
    "ready",
    "activating",
    "completed",
    "failed",
    "superseded",
    name="rating_rebuild_status",
    create_type=False,
)
rating_rebuild_period_status = postgresql.ENUM(
    "pending",
    "inputs_prepared",
    "calculated",
    "ready",
    "failed",
    name="rating_rebuild_period_status",
    create_type=False,
)


def upgrade() -> None:
    op.add_column("rating_periods", sa.Column("published_version", sa.Integer(), nullable=True))
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM rating_periods
            WHERE status = 'published'::rating_period_status
              AND calculated_version IS DISTINCT FROM calculation_version
          ) THEN
            RAISE EXCEPTION 'published rating periods have invalid completion markers';
          END IF;
        END
        $$
        """
    )
    op.execute(
        """
        UPDATE rating_periods
        SET published_version = calculation_version
        WHERE status = 'published'::rating_period_status
        """
    )
    op.create_check_constraint(
        "ck_rating_periods_published_version_valid",
        "rating_periods",
        "published_version IS NULL OR "
        "(published_version > 0 AND published_version <= calculation_version)",
    )

    postgresql.ENUM(
        "pending",
        "preparing",
        "ready",
        "activating",
        "completed",
        "failed",
        "superseded",
        name="rating_rebuild_status",
    ).create(op.get_bind(), checkfirst=False)
    postgresql.ENUM(
        "pending",
        "inputs_prepared",
        "calculated",
        "ready",
        "failed",
        name="rating_rebuild_period_status",
    ).create(op.get_bind(), checkfirst=False)

    op.create_table(
        "rating_rebuild_jobs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("from_period_id", sa.Uuid(), nullable=False),
        sa.Column("through_period_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            rating_rebuild_status,
            server_default=sa.text("'pending'::rating_rebuild_status"),
            nullable=False,
        ),
        sa.Column("singleton_scope", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "singleton_scope = 1",
            name=op.f("ck_rating_rebuild_jobs_singleton_scope_global"),
        ),
        sa.CheckConstraint("version > 0", name=op.f("ck_rating_rebuild_jobs_version_positive")),
        sa.ForeignKeyConstraint(
            ["from_period_id"],
            ["rating_periods.id"],
            name=op.f("fk_rating_rebuild_jobs_from_period_id_rating_periods"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["through_period_id"],
            ["rating_periods.id"],
            name=op.f("fk_rating_rebuild_jobs_through_period_id_rating_periods"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rating_rebuild_jobs")),
    )
    op.create_index(
        "ix_rating_rebuild_jobs_status_created",
        "rating_rebuild_jobs",
        ["status", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "uq_rating_rebuild_jobs_one_active_scope",
        "rating_rebuild_jobs",
        ["singleton_scope"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('pending', 'preparing', 'ready', 'activating', 'failed')"
        ),
    )

    op.create_table(
        "rating_rebuild_periods",
        sa.Column("rebuild_job_id", sa.Uuid(), nullable=False),
        sa.Column("period_id", sa.Uuid(), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("target_version", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            rating_rebuild_period_status,
            server_default=sa.text("'pending'::rating_rebuild_period_status"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "source_version > 0",
            name=op.f("ck_rating_rebuild_periods_source_version_positive"),
        ),
        sa.CheckConstraint(
            "target_version > source_version",
            name=op.f("ck_rating_rebuild_periods_target_version_after_source"),
        ),
        sa.ForeignKeyConstraint(
            ["period_id"],
            ["rating_periods.id"],
            name=op.f("fk_rating_rebuild_periods_period_id_rating_periods"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rebuild_job_id"],
            ["rating_rebuild_jobs.id"],
            name=op.f("fk_rating_rebuild_periods_rebuild_job_id_rating_rebuild_jobs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "rebuild_job_id",
            "period_id",
            name=op.f("pk_rating_rebuild_periods"),
        ),
        sa.UniqueConstraint(
            "period_id",
            "target_version",
            name="uq_rating_rebuild_periods_period_target_version",
        ),
    )
    op.create_index(
        "ix_rating_rebuild_periods_job_status",
        "rating_rebuild_periods",
        ["rebuild_job_id", "status"],
        unique=False,
    )

    op.add_column(
        "rating_recalculation_requests",
        sa.Column("rebuild_job_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_rating_recalculation_requests_rebuild_job_id_rating_rebuild_jobs"),
        "rating_recalculation_requests",
        "rating_rebuild_jobs",
        ["rebuild_job_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_rating_recalculation_requests_rebuild_job_created",
        "rating_recalculation_requests",
        ["rebuild_job_id", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rating_recalculation_requests_rebuild_job_created",
        table_name="rating_recalculation_requests",
    )
    op.drop_constraint(
        op.f("fk_rating_recalculation_requests_rebuild_job_id_rating_rebuild_jobs"),
        "rating_recalculation_requests",
        type_="foreignkey",
    )
    op.drop_column("rating_recalculation_requests", "rebuild_job_id")

    op.drop_index("ix_rating_rebuild_periods_job_status", table_name="rating_rebuild_periods")
    op.drop_table("rating_rebuild_periods")
    op.drop_index("uq_rating_rebuild_jobs_one_active_scope", table_name="rating_rebuild_jobs")
    op.drop_index("ix_rating_rebuild_jobs_status_created", table_name="rating_rebuild_jobs")
    op.drop_table("rating_rebuild_jobs")
    rating_rebuild_period_status.drop(op.get_bind(), checkfirst=False)
    rating_rebuild_status.drop(op.get_bind(), checkfirst=False)

    op.drop_constraint(
        "ck_rating_periods_published_version_valid",
        "rating_periods",
        type_="check",
    )
    op.drop_column("rating_periods", "published_version")

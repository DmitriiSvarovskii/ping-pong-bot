"""Add audited voiding for completed matches.

Revision ID: 20261115_0011
Revises: 20261115_0010
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261115_0011"
down_revision: str | None = "20261115_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE match_status ADD VALUE IF NOT EXISTS 'voided'")

    operation_enum = sa.Enum("correction", "void", name="match_revision_operation")
    operation_enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "match_result_revisions",
        sa.Column("operation", operation_enum, nullable=True),
    )
    op.drop_constraint("lifecycle_consistent", "matches", type_="check")
    op.create_check_constraint(
        "lifecycle_consistent",
        "matches",
        "(status = 'pending' AND started_at IS NULL AND completed_at IS NULL "
        "AND cancelled_at IS NULL AND winner_id IS NULL) OR "
        "(status = 'active' AND started_at IS NOT NULL AND completed_at IS NULL "
        "AND cancelled_at IS NULL AND winner_id IS NULL) OR "
        "(status = 'completed' AND started_at IS NOT NULL AND completed_at IS NOT NULL "
        "AND cancelled_at IS NULL AND winner_id IS NOT NULL) OR "
        "(status = 'voided' AND started_at IS NOT NULL AND completed_at IS NOT NULL "
        "AND cancelled_at IS NULL AND winner_id IS NULL) OR "
        "(status = 'cancelled' AND completed_at IS NULL AND cancelled_at IS NOT NULL "
        "AND winner_id IS NULL) OR "
        "(status = 'rejected' AND started_at IS NULL AND completed_at IS NULL "
        "AND cancelled_at IS NULL AND winner_id IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("lifecycle_consistent", "matches", type_="check")
    op.create_check_constraint(
        "lifecycle_consistent",
        "matches",
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
    )
    op.drop_column("match_result_revisions", "operation")
    sa.Enum(name="match_revision_operation").drop(op.get_bind(), checkfirst=True)

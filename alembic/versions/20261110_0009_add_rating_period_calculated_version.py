"""Add an explicit completed-calculation version marker.

Revision ID: 20261110_0009
Revises: 20261109_0008
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261110_0009"
down_revision: str | None = "20261109_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "rating_periods",
        sa.Column("calculated_version", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_rating_periods_calculated_version_valid",
        "rating_periods",
        "calculated_version IS NULL OR "
        "(calculated_version > 0 AND calculated_version <= calculation_version)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_rating_periods_calculated_version_valid",
        "rating_periods",
        type_="check",
    )
    op.drop_column("rating_periods", "calculated_version")

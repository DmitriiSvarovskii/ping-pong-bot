"""Initial Alembic baseline without business tables.

Revision ID: 20261107_0001
Revises:
Create Date: 2026-11-07 00:00:00 UTC
"""

from collections.abc import Sequence

revision: str = "20261107_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Record the baseline; no application schema is created."""
    pass


def downgrade() -> None:
    """Remove only the baseline revision marker."""
    pass

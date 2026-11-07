"""Enforce one active match per game session.

Revision ID: 20261107_0007
Revises: 20261107_0006
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261107_0007"
down_revision: str | None = "20261107_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_matches_active_game_session",
        "matches",
        ["game_session_id"],
        unique=True,
        postgresql_where=sa.text("context = 'game_session' AND status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("uq_matches_active_game_session", table_name="matches")

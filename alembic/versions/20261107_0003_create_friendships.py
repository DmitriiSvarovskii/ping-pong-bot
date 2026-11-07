"""Create friend requests and friendships.

Revision ID: 20261107_0003
Revises: 20261107_0002
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20261107_0003"
down_revision: str | None = "20261107_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

friend_request_status = postgresql.ENUM(
    "pending",
    "accepted",
    "rejected",
    "cancelled",
    name="friend_request_status",
    create_type=False,
)


def upgrade() -> None:
    postgresql.ENUM(
        "pending",
        "accepted",
        "rejected",
        "cancelled",
        name="friend_request_status",
    ).create(op.get_bind(), checkfirst=False)
    op.create_table(
        "friend_requests",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("sender_id", sa.Uuid(), nullable=False),
        sa.Column("receiver_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            friend_request_status,
            server_default=sa.text("'pending'::friend_request_status"),
            nullable=False,
        ),
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
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "sender_id <> receiver_id", name=op.f("ck_friend_requests_different_users")
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND resolved_at IS NULL) OR "
            "(status <> 'pending' AND resolved_at IS NOT NULL)",
            name=op.f("ck_friend_requests_resolution_matches_status"),
        ),
        sa.ForeignKeyConstraint(
            ["receiver_id"],
            ["users.id"],
            name=op.f("fk_friend_requests_receiver_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["sender_id"],
            ["users.id"],
            name=op.f("fk_friend_requests_sender_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_friend_requests")),
    )
    op.create_index(
        "ix_friend_requests_receiver_status_created",
        "friend_requests",
        ["receiver_id", "status", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_friend_requests_sender_status_created",
        "friend_requests",
        ["sender_id", "status", sa.literal_column("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "uq_friend_requests_pending_pair",
        "friend_requests",
        [
            sa.text("LEAST(sender_id, receiver_id)"),
            sa.text("GREATEST(sender_id, receiver_id)"),
        ],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.create_table(
        "friendships",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("friend_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("user_id < friend_id", name=op.f("ck_friendships_canonical_user_order")),
        sa.ForeignKeyConstraint(
            ["friend_id"],
            ["users.id"],
            name=op.f("fk_friendships_friend_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_friendships_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_friendships")),
        sa.UniqueConstraint("user_id", "friend_id", name="uq_friendships_user_friend"),
    )
    op.create_index("ix_friendships_friend_id", "friendships", ["friend_id"], unique=False)
    op.create_index("ix_friendships_user_id", "friendships", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_friendships_user_id", table_name="friendships")
    op.drop_index("ix_friendships_friend_id", table_name="friendships")
    op.drop_table("friendships")
    op.drop_index("uq_friend_requests_pending_pair", table_name="friend_requests")
    op.drop_index("ix_friend_requests_sender_status_created", table_name="friend_requests")
    op.drop_index("ix_friend_requests_receiver_status_created", table_name="friend_requests")
    op.drop_table("friend_requests")
    friend_request_status.drop(op.get_bind(), checkfirst=False)

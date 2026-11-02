"""Per-account state for the browser-local stores (My Picks, Arena, XP, the model builder, favourites)

Additive only: one NEW table, user_state, keyed (user_id, store) with a JSON
payload and a version counter. Nothing existing is touched and nothing is
back-filled: a browser imports what it already holds the first time its owner
signs in (client services/userState.ts).

CHAIN: descends from 20261001_0054 (NHL Gen1 research), the production head on
2026-11-02. If another migration lands first, re-point down_revision before release.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261102_0055"
down_revision = "20261001_0054"
branch_labels = None
depends_on = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "user_state",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("store", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "store", name="uq_user_state_user_store"),
    )
    op.create_index("ix_user_state_user_id", "user_state", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_state_user_id", table_name="user_state")
    op.drop_table("user_state")

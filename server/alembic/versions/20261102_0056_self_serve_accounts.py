"""Self-serve accounts: email verification and password-reset tokens

Additive only: users.email_verified_at (nullable, no back-fill: every existing
account counts as unverified until its owner clicks a link) and one NEW table,
user_tokens, holding the SHA-256 of each verification or reset token with its
expiry and use. Scale plan 2026-11-01, Phase 1 item 2.

CHAIN: descends from 20261102_0055 (user_state).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261102_0056"
down_revision = "20261102_0055"
branch_labels = None
depends_on = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("users", sa.Column("email_verified_at", _TS, nullable=True))
    op.create_table(
        "user_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", _TS, nullable=False),
        sa.Column("used_at", _TS, nullable=True),
    )
    op.create_index("ix_user_tokens_user_id", "user_tokens", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_tokens_user_id", table_name="user_tokens")
    op.drop_table("user_tokens")
    op.drop_column("users", "email_verified_at")

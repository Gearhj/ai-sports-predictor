"""Add users.token_version for session-generation token revocation.

Revision ID: 20260818_0016
Revises: 20260815_0015
Create Date: 2026-08-18
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260818_0016"
down_revision = "20260815_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "token_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "token_version")

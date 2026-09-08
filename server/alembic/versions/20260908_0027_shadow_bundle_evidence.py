"""retain immutable import evidence on shadow forecast bundles

Revision ID: 20260908_0027
Revises: 20260904_0026
Create Date: 2026-09-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260908_0027"
down_revision = "20260904_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "shadow_forecast_bundles",
        sa.Column("evidence", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    op.drop_column("shadow_forecast_bundles", "evidence")

"""index for the per-(game, provider) latest / final-pregame snapshot reducers

Revision ID: 20260904_0026
Revises: 20260904_0025
Create Date: 2026-09-04
"""

from __future__ import annotations

from alembic import op

revision = "20260904_0026"
down_revision = "20260904_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_pm_snapshots_game_provider_type_observed",
        "prediction_market_snapshots",
        ["canonical_game_id", "provider", "market_type", "provider_observed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_pm_snapshots_game_provider_type_observed", table_name="prediction_market_snapshots")

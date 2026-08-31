"""Practical pregame policy V2 challenger — frozen policy config + decision evidence.

Two nullable JSON columns, no data rewrites:
- shadow_experiments.policy_config: the immutable frozen policy calibration for
  a challenger experiment (evidence penalties, sample sizes, checksum, evidence
  multiplier, caps). NULL for the pre-existing V1 strict control.
- shadow_experiment_decisions.evidence: the exact per-decision evidence
  breakdown (starter/lineup states, tier, penalties, adjusted probability and
  edge, raw Kelly, evidence multiplier, final stake, policy version). NULL for
  historical V1 decisions, which remain untouched.

Revision ID: 20260831_0024
Revises: 20260830_0023
Create Date: 2026-08-31
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260831_0024"
down_revision = "20260830_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("shadow_experiments", sa.Column("policy_config", sa.JSON(), nullable=True))
    op.add_column("shadow_experiment_decisions", sa.Column("evidence", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("shadow_experiment_decisions", "evidence")
    op.drop_column("shadow_experiments", "policy_config")

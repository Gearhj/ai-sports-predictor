"""Inert shadow threshold ladder.

Records what a minimum edge OTHER than the official 2% would have selected,
from decisions that already existed. Creates no decision and no position, and
the funded experiment cannot observe it.

Revision ID: 20260911_0031
Revises: 20260910_0030
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260911_0031"
down_revision = "20260910_0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shadow_threshold_selections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("ladder_id", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        sa.Column("rung_percentage", sa.Float(), nullable=False),
        sa.Column("policy", sa.String(length=80), nullable=False),
        sa.Column("decision_id", sa.Uuid(),
                  sa.ForeignKey("shadow_experiment_decisions.id"), nullable=False),
        sa.Column("game_id", sa.Uuid(), sa.ForeignKey("games.id"), nullable=False),
        sa.Column("league_code", sa.String(length=16), nullable=False),
        sa.Column("slate_date", sa.Date(), nullable=False),
        sa.Column("selection_label", sa.String(length=120)),
        sa.Column("adjusted_edge_percentage", sa.Float(), nullable=False),
        sa.Column("scheduled_start_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        # One rung records a GAME once per slate. Keying on the decision would
        # record the same game once per re-evaluation and make the rungs
        # incomparable.
        sa.UniqueConstraint("ladder_id", "rung_percentage", "game_id", "slate_date",
                            name="uq_shadow_threshold_selection"),
    )
    op.create_index("ix_shadow_threshold_selections_ladder_id",
                    "shadow_threshold_selections", ["ladder_id"])
    op.create_index("ix_shadow_threshold_selections_rung_percentage",
                    "shadow_threshold_selections", ["rung_percentage"])
    op.create_index("ix_shadow_threshold_selections_decision_id",
                    "shadow_threshold_selections", ["decision_id"])
    op.create_index("ix_shadow_threshold_selections_game_id",
                    "shadow_threshold_selections", ["game_id"])
    op.create_index("ix_shadow_threshold_selections_league_code",
                    "shadow_threshold_selections", ["league_code"])
    op.create_index("ix_shadow_threshold_selections_slate_date",
                    "shadow_threshold_selections", ["slate_date"])
    op.create_index("ix_shadow_threshold_rung_slate",
                    "shadow_threshold_selections", ["rung_percentage", "slate_date"])


def downgrade() -> None:
    op.drop_index("ix_shadow_threshold_rung_slate", table_name="shadow_threshold_selections")
    for column in ("slate_date", "league_code", "game_id", "decision_id",
                   "rung_percentage", "ladder_id"):
        op.drop_index(f"ix_shadow_threshold_selections_{column}",
                      table_name="shadow_threshold_selections")
    op.drop_table("shadow_threshold_selections")

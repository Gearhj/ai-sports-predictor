"""Prospective RFF Core reading snapshots

One new append-only table holding a reading captured at a registered offset, or
the receipt of a capture that arrived too late to be admissible. No existing
table, column, index or constraint is altered.

Nothing in the forecast, decision, staking, bankroll, settlement or performance
code reads this table: it exists so a prospective research study has an
authentic record of what the index said at a stated instant.

Downgrade drops only this table.

Revision ID: 20260917_0045
Revises: 20260917_0044
Create Date: 2026-09-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260917_0045"
down_revision = "20260917_0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rff_reading_snapshots",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("game_id", sa.Uuid(as_uuid=True), sa.ForeignKey("games.id"), nullable=False),
        sa.Column("league_code", sa.String(16), nullable=False),
        sa.Column("target_offset", sa.String(8), nullable=False),
        sa.Column("target_instant_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("captured_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("as_of_utc", sa.DateTime(timezone=True)),
        sa.Column("capture_lag_seconds", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("missed_reason", sa.String(200)),
        # calculation identity
        sa.Column("calculation_version", sa.String(40)),
        sa.Column("calculation_git_sha", sa.String(40)),
        sa.Column("calculation_source_sha256", sa.String(64)),
        sa.Column("weights_sha256", sa.String(64)),
        sa.Column("policy_sha256", sa.String(64)),
        sa.Column("calculation_identity", sa.JSON()),
        # the reading
        sa.Column("components", sa.JSON()),
        sa.Column("known_contribution", sa.Float()),
        sa.Column("evidence_coverage", sa.Float()),
        sa.Column("lower_bound", sa.Float()),
        sa.Column("upper_bound", sa.Float()),
        sa.Column("interval_width", sa.Float()),
        sa.Column("publication_tier", sa.String(24)),
        sa.Column("band_key", sa.String(32)),
        sa.Column("band_label", sa.String(40)),
        sa.Column("emphasize_point", sa.Boolean()),
        sa.Column("rankable", sa.Boolean()),
        sa.Column("available_components", sa.Integer()),
        # provenance
        sa.Column("evidence_health", sa.JSON()),
        sa.Column("source_hashes", sa.JSON()),
        sa.Column("source_row_identities", sa.JSON()),
        # One accepted reading per game per offset, enforced by the database so
        # a retry or a second process cannot produce a competing reading.
        sa.UniqueConstraint("game_id", "target_offset", name="uq_rff_snapshot_game_offset"),
    )
    op.create_index("ix_rff_reading_snapshots_game_id", "rff_reading_snapshots", ["game_id"])
    op.create_index("ix_rff_reading_snapshots_league_code", "rff_reading_snapshots", ["league_code"])
    op.create_index("ix_rff_reading_snapshots_target_offset", "rff_reading_snapshots", ["target_offset"])
    op.create_index("ix_rff_reading_snapshots_status", "rff_reading_snapshots", ["status"])
    op.create_index("ix_rff_reading_snapshots_publication_tier", "rff_reading_snapshots", ["publication_tier"])
    op.create_index("ix_rff_snapshot_offset_status", "rff_reading_snapshots", ["target_offset", "status"])


def downgrade() -> None:
    op.drop_index("ix_rff_snapshot_offset_status", table_name="rff_reading_snapshots")
    op.drop_index("ix_rff_reading_snapshots_publication_tier", table_name="rff_reading_snapshots")
    op.drop_index("ix_rff_reading_snapshots_status", table_name="rff_reading_snapshots")
    op.drop_index("ix_rff_reading_snapshots_target_offset", table_name="rff_reading_snapshots")
    op.drop_index("ix_rff_reading_snapshots_league_code", table_name="rff_reading_snapshots")
    op.drop_index("ix_rff_reading_snapshots_game_id", table_name="rff_reading_snapshots")
    op.drop_table("rff_reading_snapshots")

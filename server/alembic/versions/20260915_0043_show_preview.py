"""AISP DAY-BEFORE SHOW PREVIEW V1 persistence

Preview forecasts (horizon constrained to SHOW_PREVIEW, no wagering
authority), the replaceable draft per slate date, append-only content-addressed
frozen snapshots with their per-game, per-market and disclosure bindings, and
preview-versus-final comparisons. No existing table is altered; nothing here is
read by the final-pregame decision, staking, bankroll or settlement code.

Downgrade drops only these tables. Frozen snapshots are evidence: export them
before downgrading a database that holds real ones.

Revision ID: 20260915_0043
Revises: 20260914_0042
Create Date: 2026-09-15
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260915_0043"
down_revision = "20260914_0042"
branch_labels = None
depends_on = None


def _base() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    ]


def upgrade() -> None:
    op.create_table(
        "show_preview_forecasts", *_base(),
        sa.Column("forecast_id", sa.String(200), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("content_bytes", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(80), nullable=False),
        sa.Column("horizon", sa.String(24), nullable=False),
        sa.Column("sport", sa.String(16), nullable=False),
        sa.Column("league", sa.String(16), nullable=False),
        sa.Column("season", sa.String(16), nullable=False),
        sa.Column("slate_date", sa.Date(), nullable=False),
        sa.Column("canonical_game_id", sa.Uuid(as_uuid=True), sa.ForeignKey("games.id")),
        sa.Column("source_game_ref", sa.String(120), nullable=False),
        sa.Column("away_abbreviation", sa.String(16), nullable=False),
        sa.Column("home_abbreviation", sa.String(16), nullable=False),
        sa.Column("kickoff_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_cutoff_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model_id", sa.String(120), nullable=False),
        sa.Column("model_version", sa.String(160), nullable=False),
        sa.Column("artifact_sha256", sa.String(64), nullable=False),
        sa.Column("expected_away_score", sa.Float(), nullable=False),
        sa.Column("expected_home_score", sa.Float(), nullable=False),
        sa.Column("away_win_probability", sa.Float(), nullable=False),
        sa.Column("home_win_probability", sa.Float(), nullable=False),
        sa.Column("tie_probability", sa.Float(), nullable=False),
        sa.Column("lineup_status", sa.String(24), nullable=False),
        sa.Column("starter_status", sa.String(24), nullable=False),
        sa.Column("grants_wagering_authority", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("imported_utc", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("forecast_id", name="uq_show_preview_forecast_id"),
        sa.CheckConstraint("horizon = 'SHOW_PREVIEW'", name="ck_show_preview_forecast_horizon"),
        sa.CheckConstraint("NOT grants_wagering_authority", name="ck_show_preview_forecast_no_wagering"),
    )
    op.create_index("ix_show_preview_forecasts_game", "show_preview_forecasts", ["canonical_game_id", "created_utc"])
    op.create_index("ix_show_preview_forecasts_slate", "show_preview_forecasts", ["slate_date", "sport"])

    op.create_table(
        "show_board_drafts", *_base(),
        sa.Column("slate_date", sa.Date(), nullable=False),
        sa.Column("built_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_as_of_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint("slate_date", name="uq_show_board_draft_slate"),
    )

    op.create_table(
        "show_preview_snapshots", *_base(),
        sa.Column("snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(80), nullable=False),
        sa.Column("slate_date", sa.Date(), nullable=False),
        sa.Column("frozen_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("planned_freeze_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_as_of_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_bytes", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("predecessor_id", sa.Uuid(as_uuid=True), sa.ForeignKey("show_preview_snapshots.id")),
        sa.Column("supersede_reason", sa.String(400)),
        sa.Column("trigger", sa.String(40), nullable=False),
        sa.UniqueConstraint("snapshot_sha256", name="uq_show_preview_snapshot_sha"),
    )
    op.create_index("ix_show_preview_snapshots_slate", "show_preview_snapshots", ["slate_date", "frozen_utc"])

    op.create_table(
        "show_preview_game_bindings", *_base(),
        sa.Column("snapshot_id", sa.Uuid(as_uuid=True), sa.ForeignKey("show_preview_snapshots.id"), nullable=False),
        sa.Column("game_key", sa.String(120), nullable=False),
        sa.Column("canonical_game_id", sa.Uuid(as_uuid=True), sa.ForeignKey("games.id")),
        sa.Column("sport", sa.String(16), nullable=False),
        sa.Column("kickoff_utc", sa.DateTime(timezone=True)),
        sa.Column("readiness", sa.String(40), nullable=False),
        sa.Column("forecast_row_id", sa.Uuid(as_uuid=True), sa.ForeignKey("show_preview_forecasts.id")),
        sa.Column("forecast_content_sha256", sa.String(64)),
        sa.Column("preview_rank", sa.Integer()),
        sa.Column("preview_selection", sa.String(16)),
        sa.Column("model_probability", sa.Float()),
        sa.Column("no_vig_probability", sa.Float()),
        sa.Column("preview_difference", sa.Float()),
        sa.Column("expected_return", sa.Float()),
        sa.UniqueConstraint("snapshot_id", "game_key", name="uq_show_preview_game_binding"),
    )
    op.create_table(
        "show_preview_market_bindings", *_base(),
        sa.Column("snapshot_id", sa.Uuid(as_uuid=True), sa.ForeignKey("show_preview_snapshots.id"), nullable=False),
        sa.Column("game_key", sa.String(120), nullable=False),
        sa.Column("market_type", sa.String(24), nullable=False),
        sa.Column("line_key", sa.String(24), nullable=False),
        sa.Column("sportsbook_key", sa.String(80), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("classification", sa.String(24), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("stale", sa.Boolean(), nullable=False),
        sa.Column("older_leg_observed_utc", sa.DateTime(timezone=True)),
        sa.Column("legs", sa.JSON(), nullable=False),
        sa.Column("settlement", sa.String(80), nullable=False),
        sa.UniqueConstraint("snapshot_id", "game_key", "market_type", "sportsbook_key", "provider", "line_key",
                            name="uq_show_preview_market_binding"),
    )
    op.create_table(
        "show_preview_disclosures", *_base(),
        sa.Column("snapshot_id", sa.Uuid(as_uuid=True), sa.ForeignKey("show_preview_snapshots.id"), nullable=False),
        sa.Column("game_key", sa.String(120)),
        sa.Column("code", sa.String(60), nullable=False),
        sa.Column("message", sa.String(400), nullable=False),
    )
    op.create_table(
        "show_preview_final_comparisons", *_base(),
        sa.Column("snapshot_id", sa.Uuid(as_uuid=True), sa.ForeignKey("show_preview_snapshots.id"), nullable=False),
        sa.Column("game_key", sa.String(120), nullable=False),
        sa.Column("final_decision_id", sa.Uuid(as_uuid=True), sa.ForeignKey("shadow_experiment_decisions.id")),
        sa.Column("position_id", sa.Uuid(as_uuid=True), sa.ForeignKey("official_asp_positions.id")),
        sa.Column("comparison_sha256", sa.String(64), nullable=False),
        sa.Column("compared_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint("snapshot_id", "game_key", "comparison_sha256", name="uq_show_preview_final_comparison"),
    )


def downgrade() -> None:
    for table in ("show_preview_final_comparisons", "show_preview_disclosures", "show_preview_market_bindings",
                  "show_preview_game_bindings"):
        op.drop_table(table)
    op.drop_index("ix_show_preview_snapshots_slate", table_name="show_preview_snapshots")
    op.drop_table("show_preview_snapshots")
    op.drop_table("show_board_drafts")
    op.drop_index("ix_show_preview_forecasts_slate", table_name="show_preview_forecasts")
    op.drop_index("ix_show_preview_forecasts_game", table_name="show_preview_forecasts")
    op.drop_table("show_preview_forecasts")

"""Prediction Market Observer (Polymarket + Kalshi) — read-only research tables.

Append-only evidence kept strictly separate from odds_snapshots: discovered
events, versioned contracts + semantics, append-only game/semantics mappings,
point-in-time price/quality snapshots, bounded order-book frames, display-only
cross-provider consensus, and research-only alerts.

Revision ID: 20260830_0023
Revises: 20260829_0022
Create Date: 2026-08-30
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260830_0023"
down_revision = "20260829_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prediction_market_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("provider", sa.String(24), nullable=False),
        sa.Column("provider_event_id", sa.String(255), nullable=False),
        sa.Column("sport", sa.String(24), nullable=False),
        sa.Column("league", sa.String(24), nullable=False),
        sa.Column("provider_home_team", sa.String(160), nullable=True),
        sa.Column("provider_away_team", sa.String(160), nullable=True),
        sa.Column("scheduled_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_game_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_checksum", sa.String(64), nullable=False),
        sa.Column("ingestion_run_id", sa.Uuid(), sa.ForeignKey("ingestion_runs.id"), nullable=True),
        sa.UniqueConstraint("provider", "provider_event_id", "content_checksum", name="uq_pm_event_observation"),
    )
    op.create_index("ix_prediction_market_events_provider", "prediction_market_events", ["provider"])
    op.create_index("ix_prediction_market_events_provider_event_id", "prediction_market_events", ["provider_event_id"])
    op.create_index("ix_pm_event_provider_event", "prediction_market_events", ["provider", "provider_event_id"])

    op.create_table(
        "prediction_market_contracts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("provider", sa.String(24), nullable=False),
        sa.Column("provider_event_id", sa.String(255), nullable=False),
        sa.Column("provider_market_id", sa.String(255), nullable=False),
        sa.Column("provider_outcome_id", sa.String(255), nullable=True),
        sa.Column("sport", sa.String(24), nullable=False),
        sa.Column("league", sa.String(24), nullable=False),
        sa.Column("market_type", sa.String(32), nullable=False),
        sa.Column("selection", sa.String(16), nullable=False),
        sa.Column("line_value", sa.Float(), nullable=True),
        sa.Column("outcome_label", sa.String(200), nullable=False),
        sa.Column("provider_home_team", sa.String(160), nullable=True),
        sa.Column("provider_away_team", sa.String(160), nullable=True),
        sa.Column("scheduled_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_game_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("market_question", sa.Text(), nullable=True),
        sa.Column("market_subtitle", sa.Text(), nullable=True),
        sa.Column("resolution_rules", sa.Text(), nullable=True),
        sa.Column("rules_sha256", sa.String(64), nullable=True),
        sa.Column("market_close_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settlement_source", sa.String(255), nullable=True),
        sa.Column("market_status", sa.String(32), nullable=True),
        sa.Column("line_source", sa.String(24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_checksum", sa.String(64), nullable=False),
        sa.Column("adapter_version", sa.String(24), nullable=False),
        sa.Column("ingestion_run_id", sa.Uuid(), sa.ForeignKey("ingestion_runs.id"), nullable=True),
        sa.UniqueConstraint("provider", "provider_market_id", "provider_outcome_id", "content_checksum", name="uq_pm_contract_version"),
    )
    op.create_index("ix_prediction_market_contracts_provider", "prediction_market_contracts", ["provider"])
    op.create_index("ix_prediction_market_contracts_provider_event_id", "prediction_market_contracts", ["provider_event_id"])
    op.create_index("ix_prediction_market_contracts_provider_market_id", "prediction_market_contracts", ["provider_market_id"])
    op.create_index("ix_pm_contract_event", "prediction_market_contracts", ["provider", "provider_event_id"])

    op.create_table(
        "prediction_market_mappings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("provider", sa.String(24), nullable=False),
        sa.Column("provider_event_id", sa.String(255), nullable=False),
        sa.Column("canonical_game_id", sa.Uuid(), sa.ForeignKey("games.id"), nullable=True),
        sa.Column("mapping_status", sa.String(24), nullable=False),
        sa.Column("semantics_status", sa.String(32), nullable=False),
        sa.Column("decision_source", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("start_time_delta_seconds", sa.Integer(), nullable=True),
        sa.Column("candidate_game_ids", sa.JSON(), nullable=True),
        sa.Column("decided_by_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), sa.ForeignKey("prediction_market_mappings.id"), nullable=True),
    )
    op.create_index("ix_prediction_market_mappings_provider", "prediction_market_mappings", ["provider"])
    op.create_index("ix_prediction_market_mappings_provider_event_id", "prediction_market_mappings", ["provider_event_id"])
    op.create_index("ix_prediction_market_mappings_canonical_game_id", "prediction_market_mappings", ["canonical_game_id"])
    op.create_index("ix_pm_mapping_provider_event_decided", "prediction_market_mappings", ["provider", "provider_event_id", "decided_at"])

    op.create_table(
        "prediction_market_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("provider", sa.String(24), nullable=False),
        sa.Column("contract_id", sa.Uuid(), sa.ForeignKey("prediction_market_contracts.id"), nullable=False),
        sa.Column("provider_market_id", sa.String(255), nullable=False),
        sa.Column("provider_outcome_id", sa.String(255), nullable=True),
        sa.Column("canonical_game_id", sa.Uuid(), sa.ForeignKey("games.id"), nullable=True),
        sa.Column("market_type", sa.String(32), nullable=False),
        sa.Column("selection", sa.String(16), nullable=False),
        sa.Column("line_value", sa.Float(), nullable=True),
        sa.Column("best_bid", sa.Float(), nullable=True),
        sa.Column("best_ask", sa.Float(), nullable=True),
        sa.Column("midpoint", sa.Float(), nullable=True),
        sa.Column("microprice", sa.Float(), nullable=True),
        sa.Column("last_trade", sa.Float(), nullable=True),
        sa.Column("bid_ask_spread", sa.Float(), nullable=True),
        sa.Column("top_bid_size", sa.Float(), nullable=True),
        sa.Column("top_ask_size", sa.Float(), nullable=True),
        sa.Column("depth_within_1_percent", sa.Float(), nullable=True),
        sa.Column("depth_within_2_percent", sa.Float(), nullable=True),
        sa.Column("depth_within_5_percent", sa.Float(), nullable=True),
        sa.Column("order_book_imbalance", sa.Float(), nullable=True),
        sa.Column("volume", sa.Float(), nullable=True),
        sa.Column("open_interest", sa.Float(), nullable=True),
        sa.Column("liquidity", sa.Float(), nullable=True),
        sa.Column("provider_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("aisp_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("game_starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("seconds_to_game", sa.Integer(), nullable=True),
        sa.Column("pregame_eligible", sa.Boolean(), nullable=False),
        sa.Column("in_play", sa.Boolean(), nullable=False),
        sa.Column("provider_sequence", sa.BigInteger(), nullable=True),
        sa.Column("timeline_cut", sa.String(32), nullable=True),
        sa.Column("quality_tier", sa.String(32), nullable=False),
        sa.Column("quality_components", sa.JSON(), nullable=True),
        sa.Column("quality_flags", sa.JSON(), nullable=True),
        sa.Column("mapping_status", sa.String(24), nullable=False),
        sa.Column("semantics_status", sa.String(32), nullable=False),
        sa.Column("book_complete", sa.Boolean(), nullable=False),
        sa.Column("quote_fresh", sa.Boolean(), nullable=False),
        sa.Column("market_liquid", sa.Boolean(), nullable=False),
        sa.Column("source_status", sa.String(32), nullable=True),
        sa.Column("raw_payload_sha256", sa.String(64), nullable=False),
        sa.Column("adapter_version", sa.String(24), nullable=False),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_pm_snapshot_idempotency"),
    )
    op.create_index("ix_prediction_market_snapshots_provider", "prediction_market_snapshots", ["provider"])
    op.create_index("ix_prediction_market_snapshots_contract_id", "prediction_market_snapshots", ["contract_id"])
    op.create_index("ix_prediction_market_snapshots_provider_market_id", "prediction_market_snapshots", ["provider_market_id"])
    op.create_index("ix_prediction_market_snapshots_canonical_game_id", "prediction_market_snapshots", ["canonical_game_id"])
    op.create_index("ix_pm_snapshot_game_observed", "prediction_market_snapshots", ["canonical_game_id", "provider_observed_at"])
    op.create_index("ix_pm_snapshot_contract_observed", "prediction_market_snapshots", ["contract_id", "provider_observed_at"])

    op.create_table(
        "prediction_market_orderbook_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), sa.ForeignKey("prediction_market_snapshots.id"), nullable=True),
        sa.Column("contract_id", sa.Uuid(), sa.ForeignKey("prediction_market_contracts.id"), nullable=False),
        sa.Column("provider", sa.String(24), nullable=False),
        sa.Column("bids", sa.JSON(), nullable=False),
        sa.Column("asks", sa.JSON(), nullable=False),
        sa.Column("raw_yes_bids", sa.JSON(), nullable=True),
        sa.Column("raw_no_bids", sa.JSON(), nullable=True),
        sa.Column("depth_within_1_percent", sa.Float(), nullable=True),
        sa.Column("depth_within_2_percent", sa.Float(), nullable=True),
        sa.Column("depth_within_5_percent", sa.Float(), nullable=True),
        sa.Column("provider_sequence", sa.BigInteger(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_payload_sha256", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_pm_orderbook_idempotency"),
    )
    op.create_index("ix_prediction_market_orderbook_snapshots_snapshot_id", "prediction_market_orderbook_snapshots", ["snapshot_id"])
    op.create_index("ix_prediction_market_orderbook_snapshots_contract_id", "prediction_market_orderbook_snapshots", ["contract_id"])

    op.create_table(
        "prediction_market_consensus_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("canonical_game_id", sa.Uuid(), sa.ForeignKey("games.id"), nullable=False),
        sa.Column("market_type", sa.String(32), nullable=False),
        sa.Column("selection", sa.String(16), nullable=False),
        sa.Column("line_value", sa.Float(), nullable=True),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("contributing_providers", sa.JSON(), nullable=False),
        sa.Column("provider_prices", sa.JSON(), nullable=False),
        sa.Column("median_probability", sa.Float(), nullable=True),
        sa.Column("quality_weighted_probability", sa.Float(), nullable=True),
        sa.Column("dispersion", sa.Float(), nullable=True),
        sa.Column("consensus_version", sa.String(48), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_prediction_market_consensus_snapshots_canonical_game_id", "prediction_market_consensus_snapshots", ["canonical_game_id"])
    op.create_index("ix_pm_consensus_game_computed", "prediction_market_consensus_snapshots", ["canonical_game_id", "computed_at"])

    op.create_table(
        "prediction_market_alerts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("alert_type", sa.String(48), nullable=False),
        sa.Column("canonical_game_id", sa.Uuid(), sa.ForeignKey("games.id"), nullable=True),
        sa.Column("provider", sa.String(24), nullable=True),
        sa.Column("market_type", sa.String(32), nullable=True),
        sa.Column("selection", sa.String(16), nullable=True),
        sa.Column("magnitude", sa.Float(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("threshold_version", sa.String(48), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_pm_alert_idempotency"),
    )
    op.create_index("ix_prediction_market_alerts_alert_type", "prediction_market_alerts", ["alert_type"])
    op.create_index("ix_prediction_market_alerts_canonical_game_id", "prediction_market_alerts", ["canonical_game_id"])
    op.create_index("ix_pm_alert_game_type", "prediction_market_alerts", ["canonical_game_id", "alert_type"])


def downgrade() -> None:
    op.drop_table("prediction_market_alerts")
    op.drop_table("prediction_market_consensus_snapshots")
    op.drop_table("prediction_market_orderbook_snapshots")
    op.drop_table("prediction_market_snapshots")
    op.drop_table("prediction_market_mappings")
    op.drop_table("prediction_market_contracts")
    op.drop_table("prediction_market_events")

"""CFB consumer activation -- discovery evidence, finals, settlement adjustments

Additive only. New tables:

* ``cfb_discovery_manifests`` / ``cfb_discovery_observations``: every verified
  discovery manifest and what it said about each package, so present decision
  eligibility is a query over retained evidence, never an overwritten flag.
* ``cfb_final_observations``: retained CFBD completed-game rows bound to an
  AISP game, beside the sport-neutral ``game_state_observations`` row.
* ``position_settlement_adjustments``: a correction beside the original
  settlement, which is never rewritten.

New nullable columns on ``cfb_research_forecasts`` (discovery provenance and
the canonical game) and on ``shadow_experiment_decisions``
(``cfb_research_forecast_id``). Existing rows keep ``origin = RESEARCH_TREE``.
No MLB/NFL row is touched.

Revision ID: 20260918_0047
Revises: 20260918_0046
Create Date: 2026-09-18
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260918_0047"
down_revision = "20260918_0046"
branch_labels = None
depends_on = None


def _uuid() -> sa.types.TypeEngine:
    return sa.Uuid(as_uuid=True)


def upgrade() -> None:
    with op.batch_alter_table("cfb_research_forecasts") as batch:
        batch.add_column(sa.Column("origin", sa.String(40), nullable=False, server_default="RESEARCH_TREE"))
        batch.add_column(sa.Column("publication_clock_source", sa.String(60)))
        batch.add_column(sa.Column("discovery_receipt_sha256", sa.String(64)))
        batch.add_column(sa.Column("revision_key", sa.String(80)))
        batch.add_column(sa.Column("first_manifest_sha256", sa.String(64)))
        batch.add_column(sa.Column("producer_status", sa.String(80)))
        batch.add_column(sa.Column("orientation_check", sa.String(60)))
        batch.add_column(sa.Column("canonical_game_id", _uuid(), sa.ForeignKey("games.id")))
        batch.create_index("ix_cfb_research_forecasts_canonical_game_id", ["canonical_game_id"])

    # The existing research rows used the package-local receipt; say so.
    op.execute("UPDATE cfb_research_forecasts SET publication_clock_source = 'PACKAGE_LOCAL_RECEIPT_PUBLISHED_AT' "
               "WHERE publication_clock_source IS NULL")

    with op.batch_alter_table("shadow_experiment_decisions") as batch:
        batch.add_column(sa.Column("cfb_research_forecast_id", _uuid(), sa.ForeignKey("cfb_research_forecasts.id")))
        batch.create_index("ix_shadow_experiment_decisions_cfb_research_forecast_id", ["cfb_research_forecast_id"])

    op.create_table(
        "cfb_discovery_manifests",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("manifest_bytes", sa.Integer, nullable=False),
        sa.Column("producer_created_utc", sa.DateTime(timezone=True)),
        sa.Column("release_sha256", sa.String(64)),
        sa.Column("predecessor_sha256", sa.String(64)),
        sa.Column("handoff_sha256", sa.String(64)),
        sa.Column("orientation_source", sa.String(80), nullable=False),
        sa.Column("packages_listed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("packages_verified", sa.Integer, nullable=False, server_default="0"),
        sa.Column("packages_current_at_check", sa.Integer, nullable=False, server_default="0"),
        sa.Column("dispositions", sa.JSON),
        sa.Column("first_verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("manifest_sha256", name="uq_cfb_discovery_manifest_sha"),
    )
    op.create_table(
        "cfb_discovery_observations",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("manifest_id", _uuid(), sa.ForeignKey("cfb_discovery_manifests.id"), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("forecast_id", sa.String(200), nullable=False),
        sa.Column("cfb_research_forecast_id", _uuid(), sa.ForeignKey("cfb_research_forecasts.id")),
        sa.Column("source_game_id", sa.String(64), nullable=False),
        sa.Column("package_address", sa.String(64), nullable=False),
        sa.Column("producer_status", sa.String(60)),
        sa.Column("currently_eligible", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("eligibility_valid_until_utc", sa.DateTime(timezone=True)),
        sa.Column("lease_at_check", sa.String(24), nullable=False),
        sa.Column("verified", sa.Boolean, nullable=False),
        sa.Column("problems", sa.JSON),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("manifest_sha256", "forecast_id", name="uq_cfb_discovery_obs"),
    )
    op.create_index("ix_cfb_discovery_observations_manifest_id", "cfb_discovery_observations", ["manifest_id"])
    op.create_index("ix_cfb_discovery_observations_forecast_id", "cfb_discovery_observations", ["forecast_id"])
    op.create_index("ix_cfb_discovery_observations_source_game_id", "cfb_discovery_observations", ["source_game_id"])

    op.create_table(
        "cfb_final_observations",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("game_id", _uuid(), sa.ForeignKey("games.id"), nullable=False),
        sa.Column("game_state_observation_id", _uuid(), sa.ForeignKey("game_state_observations.id"), nullable=False),
        sa.Column("source", sa.String(40), nullable=False, server_default="CFBD"),
        sa.Column("source_game_id", sa.String(64), nullable=False),
        sa.Column("home_source_team_id", sa.String(64), nullable=False),
        sa.Column("away_source_team_id", sa.String(64), nullable=False),
        sa.Column("home_points", sa.Integer, nullable=False),
        sa.Column("away_points", sa.Integer, nullable=False),
        sa.Column("source_completed", sa.Boolean, nullable=False),
        sa.Column("captured_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completion_clock_status", sa.String(60), nullable=False),
        sa.Column("evidence_index_sha256", sa.String(64), nullable=False),
        sa.Column("evidence_artifact_sha256", sa.String(64), nullable=False),
        sa.Column("source_response_sha256", sa.String(64), nullable=False),
        sa.Column("source_record_locator", sa.String(40)),
        sa.Column("raw_record_verified", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("payload_checksum", sa.String(64), nullable=False),
        sa.Column("evidence_row", sa.JSON, nullable=False),
        sa.UniqueConstraint("game_id", "payload_checksum", name="uq_cfb_final_obs"),
    )
    op.create_index("ix_cfb_final_observations_game_id", "cfb_final_observations", ["game_id"])

    op.create_table(
        "position_settlement_adjustments",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("position_id", _uuid(), sa.ForeignKey("official_asp_positions.id"), nullable=False),
        sa.Column("original_settlement_id", _uuid(), sa.ForeignKey("position_results.id"), nullable=False),
        sa.Column("game_state_observation_id", _uuid(), sa.ForeignKey("game_state_observations.id")),
        sa.Column("previous_result", sa.String(16), nullable=False),
        sa.Column("corrected_result", sa.String(16), nullable=False),
        sa.Column("previous_profit_loss", sa.Numeric(12, 2), nullable=False),
        sa.Column("corrected_profit_loss", sa.Numeric(12, 2), nullable=False),
        sa.Column("profit_loss_delta", sa.Numeric(12, 2), nullable=False),
        sa.Column("previous_home_score", sa.Integer),
        sa.Column("previous_away_score", sa.Integer),
        sa.Column("corrected_home_score", sa.Integer),
        sa.Column("corrected_away_score", sa.Integer),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("adjusted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closing_line_value", sa.Float),
        sa.UniqueConstraint("position_id", "game_state_observation_id",
                            name="uq_position_adjustment_observation"),
    )
    op.create_index("ix_position_settlement_adjustments_position_id",
                    "position_settlement_adjustments", ["position_id"])


def downgrade() -> None:
    op.drop_table("position_settlement_adjustments")
    op.drop_table("cfb_final_observations")
    op.drop_table("cfb_discovery_observations")
    op.drop_table("cfb_discovery_manifests")
    with op.batch_alter_table("shadow_experiment_decisions") as batch:
        batch.drop_index("ix_shadow_experiment_decisions_cfb_research_forecast_id")
        batch.drop_column("cfb_research_forecast_id")
    with op.batch_alter_table("cfb_research_forecasts") as batch:
        batch.drop_index("ix_cfb_research_forecasts_canonical_game_id")
        for column in ("canonical_game_id", "orientation_check", "producer_status", "first_manifest_sha256",
                       "revision_key", "discovery_receipt_sha256", "publication_clock_source", "origin"):
            batch.drop_column(column)

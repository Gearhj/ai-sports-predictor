"""NBA Gen1 consumer -- production packages, historical rows, discovery, finals, channel scores

Additive only. New tables:

* ``nba_gen1_forecasts``: verified NBA Gen1 PRODUCTION forecast packages, with
  the three channels (Ridge points, Elo winner probabilities, joint population)
  in separate column groups.
* ``nba_historical_forecasts``: Model Studio's 2025-26 fixed-assessment rows,
  evidence class A (historical reconstruction). Never prospective.
* ``nba_discovery_manifests`` / ``nba_discovery_observations``: every verified
  discovery document and what it said about each package, so present decision
  eligibility is a query over retained evidence.
* ``nba_final_observations``: retained ESPN schedule rows with a completed final,
  bound to an AISP game, beside the sport-neutral ``game_state_observations`` row.
* ``nba_channel_evaluations``: per-channel scores (probability metrics null on the
  Ridge row, point metrics null on the Elo row).

One new nullable column: ``shadow_experiment_decisions.nba_gen1_forecast_id``.
No MLB/NFL/CFB row is touched. Downgrade drops exactly what upgrade adds.

Revision ID: 20260919_0048
Revises: 20260918_0047
Create Date: 2026-09-19
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260919_0048"
down_revision = "20260918_0047"
branch_labels = None
depends_on = None


def _uuid() -> sa.types.TypeEngine:
    return sa.Uuid(as_uuid=True)


def _base() -> list[sa.Column]:
    return [
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    ]


def _channels() -> list[sa.Column]:
    return [
        sa.Column("ridge_home_points", sa.Float),
        sa.Column("ridge_away_points", sa.Float),
        sa.Column("ridge_total_points", sa.Float),
        sa.Column("ridge_home_margin", sa.Float),
        sa.Column("elo_home_win_probability", sa.Float),
        sa.Column("elo_away_win_probability", sa.Float),
        sa.Column("elo_tie_probability", sa.Float),
        sa.Column("elo_method", sa.Text),
        sa.Column("joint_draw_count", sa.Integer),
        sa.Column("joint_home_win_probability", sa.Float),
        sa.Column("joint_away_win_probability", sa.Float),
        sa.Column("joint_home_win_mcse", sa.Float),
        sa.Column("joint_expected_home", sa.Float),
        sa.Column("joint_expected_away", sa.Float),
        sa.Column("joint_expected_total", sa.Float),
        sa.Column("joint_expected_margin", sa.Float),
        sa.Column("joint_summary", sa.JSON),
    ]


def upgrade() -> None:
    op.create_table(
        "nba_gen1_forecasts",
        *_base(),
        *_channels(),
        sa.Column("forecast_id", sa.String(200), nullable=False),
        sa.Column("forecast_sha256", sa.String(64), nullable=False),
        sa.Column("forecast_bytes", sa.Integer, nullable=False),
        sa.Column("forecast_schema", sa.String(80), nullable=False),
        sa.Column("package_index_sha256", sa.String(64), nullable=False),
        sa.Column("package_index_bytes", sa.Integer, nullable=False),
        sa.Column("envelope_schema", sa.String(80), nullable=False),
        sa.Column("revision_key", sa.String(80)),
        sa.Column("evidence_class", sa.String(80), nullable=False),
        sa.Column("configuration_identity", sa.String(120), nullable=False),
        sa.Column("distribution_configuration_identity", sa.String(120), nullable=False),
        sa.Column("handoff_sha256", sa.String(64), nullable=False),
        sa.Column("prospective_effective_at_utc", sa.DateTime(timezone=True)),
        sa.Column("codex_game_id", sa.String(80), nullable=False),
        sa.Column("source_game_id", sa.String(32), nullable=False),
        sa.Column("home_franchise_id", sa.String(80), nullable=False),
        sa.Column("away_franchise_id", sa.String(80), nullable=False),
        sa.Column("home_team_code", sa.String(8), nullable=False),
        sa.Column("away_team_code", sa.String(8), nullable=False),
        sa.Column("season_end", sa.Integer),
        sa.Column("season_type", sa.String(24)),
        sa.Column("neutral_site", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("schedule_source_revision", sa.Text),
        sa.Column("captured_tipoff_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("captured_game_date", sa.Date, nullable=False),
        sa.Column("canonical_game_id", _uuid(), sa.ForeignKey("games.id")),
        sa.Column("population_artifact_sha256", sa.String(64), nullable=False),
        sa.Column("population_logical_sha256", sa.String(64), nullable=False),
        sa.Column("source_cutoff_utc", sa.DateTime(timezone=True)),
        sa.Column("forecast_created_utc", sa.DateTime(timezone=True)),
        sa.Column("producer_published_utc", sa.DateTime(timezone=True)),
        sa.Column("exchange_published_utc", sa.DateTime(timezone=True)),
        sa.Column("publication_clock_source", sa.String(60), nullable=False),
        sa.Column("discovery_receipt_sha256", sa.String(64)),
        sa.Column("first_manifest_sha256", sa.String(64)),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detail", sa.JSON, nullable=False),
        sa.UniqueConstraint("forecast_id", name="uq_nba_gen1_forecast_id"),
        sa.CheckConstraint("joint_draw_count IS NULL OR joint_draw_count = 10000",
                           name="ck_nba_gen1_forecast_draws"),
        sa.CheckConstraint("elo_home_win_probability IS NULL OR "
                           "(elo_home_win_probability >= 0 AND elo_home_win_probability <= 1)",
                           name="ck_nba_gen1_forecast_elo_fraction"),
        sa.CheckConstraint("joint_home_win_probability IS NULL OR "
                           "(joint_home_win_probability >= 0 AND joint_home_win_probability <= 1)",
                           name="ck_nba_gen1_forecast_joint_fraction"),
    )
    op.create_index("ix_nba_gen1_forecasts_source_game_id", "nba_gen1_forecasts", ["source_game_id"])
    op.create_index("ix_nba_gen1_forecasts_canonical_game_id", "nba_gen1_forecasts", ["canonical_game_id"])

    op.create_table(
        "nba_historical_forecasts",
        *_base(),
        *_channels(),
        sa.Column("evidence_class", sa.String(80), nullable=False),
        sa.Column("assessment_receipt_sha256", sa.String(64), nullable=False),
        sa.Column("joint_receipt_sha256", sa.String(64)),
        sa.Column("predictions_sha256", sa.String(64), nullable=False),
        sa.Column("joint_counts_sha256", sa.String(64)),
        sa.Column("joint_counts_int64_le_sha256", sa.String(64)),
        sa.Column("point_model_id", sa.String(40), nullable=False),
        sa.Column("point_model_sha256", sa.String(64), nullable=False),
        sa.Column("elo_model_sha256", sa.String(64), nullable=False),
        sa.Column("joint_configuration", sa.String(120)),
        sa.Column("codex_game_id", sa.String(80), nullable=False),
        sa.Column("source_game_id", sa.String(32), nullable=False),
        sa.Column("home_franchise_id", sa.String(80), nullable=False),
        sa.Column("away_franchise_id", sa.String(80), nullable=False),
        sa.Column("home_team_code", sa.String(8), nullable=False),
        sa.Column("away_team_code", sa.String(8), nullable=False),
        sa.Column("season_end", sa.Integer, nullable=False),
        sa.Column("game_date", sa.Date, nullable=False),
        sa.Column("canonical_game_id", _uuid(), sa.ForeignKey("games.id")),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detail", sa.JSON, nullable=False),
        sa.UniqueConstraint("assessment_receipt_sha256", "codex_game_id", name="uq_nba_historical_row"),
    )
    op.create_index("ix_nba_historical_forecasts_source_game_id", "nba_historical_forecasts", ["source_game_id"])
    op.create_index("ix_nba_historical_forecasts_canonical_game_id", "nba_historical_forecasts",
                    ["canonical_game_id"])

    op.create_table(
        "nba_discovery_manifests",
        *_base(),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("manifest_bytes", sa.Integer, nullable=False),
        sa.Column("schema_version", sa.String(80), nullable=False),
        sa.Column("producer_created_utc", sa.DateTime(timezone=True)),
        sa.Column("predecessor_sha256", sa.String(64)),
        sa.Column("release_sha256", sa.String(64)),
        sa.Column("handoff_sha256", sa.String(64)),
        sa.Column("packages_listed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("packages_verified", sa.Integer, nullable=False, server_default="0"),
        sa.Column("packages_current_at_check", sa.Integer, nullable=False, server_default="0"),
        sa.Column("lease_semantics", sa.String(60), nullable=False),
        sa.Column("dispositions", sa.JSON),
        sa.Column("first_verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("manifest_sha256", name="uq_nba_discovery_manifest_sha"),
    )
    op.create_table(
        "nba_discovery_observations",
        *_base(),
        sa.Column("manifest_id", _uuid(), sa.ForeignKey("nba_discovery_manifests.id"), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("forecast_id", sa.String(200), nullable=False),
        sa.Column("nba_gen1_forecast_id", _uuid(), sa.ForeignKey("nba_gen1_forecasts.id")),
        sa.Column("codex_game_id", sa.String(80), nullable=False),
        sa.Column("package_address", sa.String(64), nullable=False),
        sa.Column("producer_status", sa.String(60)),
        sa.Column("currently_eligible", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("eligibility_valid_until_utc", sa.DateTime(timezone=True)),
        sa.Column("lease_at_check", sa.String(24), nullable=False),
        sa.Column("verified", sa.Boolean, nullable=False),
        sa.Column("problems", sa.JSON),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("manifest_sha256", "forecast_id", name="uq_nba_discovery_obs"),
    )
    op.create_index("ix_nba_discovery_observations_manifest_id", "nba_discovery_observations", ["manifest_id"])
    op.create_index("ix_nba_discovery_observations_forecast_id", "nba_discovery_observations", ["forecast_id"])

    op.create_table(
        "nba_final_observations",
        *_base(),
        sa.Column("game_id", _uuid(), sa.ForeignKey("games.id"), nullable=False),
        sa.Column("game_state_observation_id", _uuid(), sa.ForeignKey("game_state_observations.id"),
                  nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("source_game_id", sa.String(32), nullable=False),
        sa.Column("home_espn_team_id", sa.String(16), nullable=False),
        sa.Column("away_espn_team_id", sa.String(16), nullable=False),
        sa.Column("home_points", sa.Integer, nullable=False),
        sa.Column("away_points", sa.Integer, nullable=False),
        sa.Column("periods", sa.Integer),
        sa.Column("overtime", sa.Boolean),
        sa.Column("source_status", sa.String(40), nullable=False),
        sa.Column("source_object_sha256", sa.String(64), nullable=False),
        sa.Column("source_object_bytes", sa.Integer, nullable=False),
        sa.Column("captured_at_utc", sa.DateTime(timezone=True)),
        sa.Column("completion_clock_status", sa.String(60), nullable=False),
        sa.Column("payload_checksum", sa.String(64), nullable=False),
        sa.Column("evidence_row", sa.JSON, nullable=False),
        sa.UniqueConstraint("game_id", "payload_checksum", name="uq_nba_final_obs"),
    )
    op.create_index("ix_nba_final_observations_game_id", "nba_final_observations", ["game_id"])

    op.create_table(
        "nba_channel_evaluations",
        *_base(),
        sa.Column("evidence_class", sa.String(80), nullable=False),
        sa.Column("channel", sa.String(24), nullable=False),
        sa.Column("forecast_identity", sa.String(200), nullable=False),
        sa.Column("nba_gen1_forecast_id", _uuid(), sa.ForeignKey("nba_gen1_forecasts.id")),
        sa.Column("nba_historical_forecast_id", _uuid(), sa.ForeignKey("nba_historical_forecasts.id")),
        sa.Column("canonical_game_id", _uuid(), sa.ForeignKey("games.id"), nullable=False),
        sa.Column("source_game_id", sa.String(32), nullable=False),
        sa.Column("start_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("forecast_published_utc", sa.DateTime(timezone=True)),
        sa.Column("result_identity", sa.String(200), nullable=False),
        sa.Column("result_source", sa.String(64), nullable=False),
        sa.Column("home_score", sa.Integer, nullable=False),
        sa.Column("away_score", sa.Integer, nullable=False),
        sa.Column("overtime", sa.Boolean),
        sa.Column("home_win_probability", sa.Float),
        sa.Column("probability_of_actual", sa.Float),
        sa.Column("log_loss", sa.Float),
        sa.Column("two_way_brier", sa.Float),
        sa.Column("winner_correct", sa.Boolean),
        sa.Column("predicted_home", sa.Float),
        sa.Column("predicted_away", sa.Float),
        sa.Column("home_abs_error", sa.Float),
        sa.Column("away_abs_error", sa.Float),
        sa.Column("margin_error", sa.Float),
        sa.Column("total_error", sa.Float),
        sa.Column("crps_home", sa.Float),
        sa.Column("crps_away", sa.Float),
        sa.Column("crps_margin", sa.Float),
        sa.Column("crps_total", sa.Float),
        sa.Column("interval_coverage", sa.JSON),
        sa.Column("detail", sa.JSON, nullable=False),
        sa.Column("scoring_version", sa.String(48), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("forecast_identity", "channel", "result_identity", name="uq_nba_channel_eval"),
        sa.CheckConstraint(
            "channel <> 'RIDGE_POINTS' OR (log_loss IS NULL AND two_way_brier IS NULL "
            "AND home_win_probability IS NULL)",
            name="ck_nba_channel_eval_ridge_is_not_a_probability"),
        sa.CheckConstraint(
            "channel <> 'ELO_WINNER' OR (predicted_home IS NULL AND predicted_away IS NULL)",
            name="ck_nba_channel_eval_elo_has_no_points"),
    )
    op.create_index("ix_nba_channel_eval_class_channel", "nba_channel_evaluations", ["evidence_class", "channel"])
    op.create_index("ix_nba_channel_evaluations_canonical_game_id", "nba_channel_evaluations",
                    ["canonical_game_id"])
    op.create_index("ix_nba_channel_evaluations_evidence_class", "nba_channel_evaluations", ["evidence_class"])
    op.create_index("ix_nba_channel_evaluations_channel", "nba_channel_evaluations", ["channel"])

    with op.batch_alter_table("shadow_experiment_decisions") as batch:
        # Named, so SQLite batch mode can recreate the table (an unnamed FK is refused there).
        batch.add_column(sa.Column("nba_gen1_forecast_id", _uuid(), sa.ForeignKey(
            "nba_gen1_forecasts.id", name="fk_shadow_experiment_decisions_nba_gen1_forecast_id")))
        batch.create_index("ix_shadow_experiment_decisions_nba_gen1_forecast_id", ["nba_gen1_forecast_id"])


def downgrade() -> None:
    with op.batch_alter_table("shadow_experiment_decisions") as batch:
        batch.drop_index("ix_shadow_experiment_decisions_nba_gen1_forecast_id")
        batch.drop_column("nba_gen1_forecast_id")
    op.drop_table("nba_channel_evaluations")
    op.drop_table("nba_final_observations")
    op.drop_table("nba_discovery_observations")
    op.drop_table("nba_discovery_manifests")
    op.drop_table("nba_historical_forecasts")
    op.drop_table("nba_gen1_forecasts")

"""Establish Gate 2 point-in-time MLB evidence and feature snapshots.

Revision ID: 20260812_0003
Revises: 20260812_0002
Create Date: 2026-08-12

This is a forward-only addition to the frozen Gate 0/1 baseline. It deliberately
does not backdate mutable legacy game state: ingestion creates new observations
at the time ASP actually receives them.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260812_0003"
down_revision = "20260812_0002"
branch_labels = None
depends_on = None


def _identity_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    ]


def _observation_columns() -> list[sa.Column]:
    return [
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("source_mode", sa.String(length=16), nullable=False),
        sa.Column("source_record_id", sa.String(length=255), nullable=True),
        sa.Column("payload_checksum", sa.String(length=64), nullable=True),
        sa.Column("ingestion_run_id", sa.Uuid(), nullable=True),
        *_identity_columns(),
        sa.ForeignKeyConstraint(["ingestion_run_id"], ["ingestion_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    ]


def upgrade() -> None:
    op.create_table(
        "game_state_observations",
        sa.Column("game_id", sa.Uuid(), nullable=False),
        sa.Column("home_team_id", sa.Uuid(), nullable=True),
        sa.Column("away_team_id", sa.Uuid(), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("is_postponed", sa.Boolean(), nullable=False),
        sa.Column("postponement_reason", sa.Text(), nullable=True),
        sa.Column("venue_id", sa.Uuid(), nullable=True),
        sa.Column("doubleheader_status", sa.String(length=40), nullable=True),
        sa.Column("game_number", sa.Integer(), nullable=True),
        sa.Column("scheduled_innings", sa.Integer(), nullable=True),
        sa.Column("home_probable_pitcher_id", sa.Uuid(), nullable=True),
        sa.Column("away_probable_pitcher_id", sa.Uuid(), nullable=True),
        sa.Column("home_starter_confirmation", sa.String(length=24), nullable=False),
        sa.Column("away_starter_confirmation", sa.String(length=24), nullable=False),
        sa.Column("home_score", sa.Integer(), nullable=True),
        sa.Column("away_score", sa.Integer(), nullable=True),
        sa.Column("final_at", sa.DateTime(timezone=True), nullable=True),
        *_observation_columns(),
        sa.CheckConstraint("game_number IS NULL OR game_number > 0", name="ck_game_state_game_number"),
        sa.ForeignKeyConstraint(["away_probable_pitcher_id"], ["players.id"]),
        sa.ForeignKeyConstraint(["away_team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"]),
        sa.ForeignKeyConstraint(["home_probable_pitcher_id"], ["players.id"]),
        sa.ForeignKeyConstraint(["home_team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["venue_id"], ["venues.id"]),
        sa.UniqueConstraint(
            "game_id", "source", "source_mode", "available_as_of", "payload_checksum",
            name="uq_game_state_obs_evidence",
        ),
    )
    op.create_index("ix_game_state_obs_game_asof", "game_state_observations", ["game_id", "available_as_of"])
    op.create_index("ix_game_state_obs_home_team", "game_state_observations", ["home_team_id"])
    op.create_index("ix_game_state_obs_away_team", "game_state_observations", ["away_team_id"])

    op.create_table(
        "lineup_observations",
        sa.Column("game_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("lineup_status", sa.String(length=24), nullable=False),
        sa.Column("is_complete", sa.Boolean(), nullable=False),
        *_observation_columns(),
        sa.CheckConstraint("lineup_status IN ('PROJECTED', 'EXPECTED', 'CONFIRMED')", name="ck_lineup_obs_status"),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.UniqueConstraint(
            "game_id", "team_id", "source", "source_mode", "available_as_of", "payload_checksum",
            name="uq_lineup_obs_evidence",
        ),
    )
    op.create_index("ix_lineup_obs_game_team_asof", "lineup_observations", ["game_id", "team_id", "available_as_of"])

    op.create_table(
        "lineup_entries",
        sa.Column("lineup_observation_id", sa.Uuid(), nullable=False),
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("batting_order", sa.Integer(), nullable=True),
        sa.Column("defensive_position", sa.String(length=32), nullable=True),
        sa.Column("bats", sa.String(length=8), nullable=True),
        sa.Column("is_starter", sa.Boolean(), nullable=False),
        sa.Column("expected_plate_appearances", sa.Float(), nullable=True),
        sa.Column("expected_plate_appearance_weight", sa.Float(), nullable=True),
        *_identity_columns(),
        sa.CheckConstraint("batting_order IS NULL OR (batting_order >= 1 AND batting_order <= 9)", name="ck_lineup_entry_order"),
        sa.CheckConstraint("expected_plate_appearances IS NULL OR expected_plate_appearances >= 0", name="ck_lineup_entry_pa"),
        sa.CheckConstraint(
            "expected_plate_appearance_weight IS NULL OR (expected_plate_appearance_weight >= 0 AND expected_plate_appearance_weight <= 1)",
            name="ck_lineup_entry_weight",
        ),
        sa.ForeignKeyConstraint(["lineup_observation_id"], ["lineup_observations.id"]),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lineup_observation_id", "batting_order", name="uq_lineup_entry_order"),
        sa.UniqueConstraint("lineup_observation_id", "player_id", name="uq_lineup_entry_player"),
    )
    op.create_index("ix_lineup_entries_observation", "lineup_entries", ["lineup_observation_id"])

    op.create_table(
        "starter_observations",
        sa.Column("game_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("probable_pitcher_id", sa.Uuid(), nullable=True),
        sa.Column("confirmed_pitcher_id", sa.Uuid(), nullable=True),
        sa.Column("confirmation_state", sa.String(length=24), nullable=False),
        sa.Column("throws", sa.String(length=8), nullable=True),
        sa.Column("days_rest", sa.Integer(), nullable=True),
        sa.Column("pitches_previous_day", sa.Integer(), nullable=True),
        sa.Column("pitches_last_start", sa.Integer(), nullable=True),
        sa.Column("workload", sa.JSON(), nullable=True),
        *_observation_columns(),
        sa.CheckConstraint("confirmation_state IN ('UNKNOWN', 'PROBABLE', 'EXPECTED', 'CONFIRMED')", name="ck_starter_obs_confirmation"),
        sa.CheckConstraint("days_rest IS NULL OR days_rest >= 0", name="ck_starter_obs_rest"),
        sa.ForeignKeyConstraint(["confirmed_pitcher_id"], ["players.id"]),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"]),
        sa.ForeignKeyConstraint(["probable_pitcher_id"], ["players.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.UniqueConstraint(
            "game_id", "team_id", "source", "source_mode", "available_as_of", "payload_checksum",
            name="uq_starter_obs_evidence",
        ),
    )
    op.create_index("ix_starter_obs_game_team_asof", "starter_observations", ["game_id", "team_id", "available_as_of"])

    op.create_table(
        "roster_availability_observations",
        sa.Column("season_id", sa.Uuid(), nullable=True),
        sa.Column("team_id", sa.Uuid(), nullable=True),
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("availability_status", sa.String(length=32), nullable=False),
        sa.Column("transaction_status", sa.String(length=32), nullable=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("participation_probability", sa.Float(), nullable=True),
        sa.Column("replacement_player_id", sa.Uuid(), nullable=True),
        sa.Column("active_roster", sa.Boolean(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        *_observation_columns(),
        sa.CheckConstraint(
            "participation_probability IS NULL OR (participation_probability >= 0 AND participation_probability <= 1)",
            name="ck_roster_avail_probability",
        ),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"]),
        sa.ForeignKeyConstraint(["replacement_player_id"], ["players.id"]),
        sa.ForeignKeyConstraint(["season_id"], ["seasons.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.UniqueConstraint(
            "player_id", "team_id", "source", "source_mode", "available_as_of", "payload_checksum",
            name="uq_roster_avail_obs_evidence",
        ),
    )
    op.create_index("ix_roster_avail_player_asof", "roster_availability_observations", ["player_id", "available_as_of"])
    op.create_index("ix_roster_avail_team_asof", "roster_availability_observations", ["team_id", "available_as_of"])
    op.create_index("ix_roster_avail_effective", "roster_availability_observations", ["effective_at"])

    op.create_table(
        "player_talent_estimates",
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False),
        sa.Column("metric_name", sa.String(length=80), nullable=False),
        sa.Column("metric_class", sa.String(length=32), nullable=False),
        sa.Column("prior_value", sa.Float(), nullable=True),
        sa.Column("prior_sample_size", sa.Float(), nullable=True),
        sa.Column("season_value", sa.Float(), nullable=True),
        sa.Column("season_sample_size", sa.Float(), nullable=True),
        sa.Column("recent_value", sa.Float(), nullable=True),
        sa.Column("recent_sample_size", sa.Float(), nullable=True),
        sa.Column("posterior_value", sa.Float(), nullable=False),
        sa.Column("posterior_delta", sa.Float(), nullable=True),
        sa.Column("reliability", sa.Float(), nullable=True),
        sa.Column("evidence_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("method", sa.String(length=80), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=True),
        sa.Column("evidence_references", sa.JSON(), nullable=True),
        *_observation_columns(),
        sa.CheckConstraint("reliability IS NULL OR (reliability >= 0 AND reliability <= 1)", name="ck_player_talent_reliability"),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"]),
        sa.UniqueConstraint(
            "player_id", "role", "metric_name", "version", "source", "source_mode", "available_as_of",
            name="uq_player_talent_estimate",
        ),
    )
    op.create_index("ix_player_talent_player_metric_asof", "player_talent_estimates", ["player_id", "metric_name", "available_as_of"])
    op.create_index("ix_player_talent_evidence_cutoff", "player_talent_estimates", ["evidence_cutoff"])

    op.create_table(
        "bullpen_availability_observations",
        sa.Column("game_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("true_talent_estimate_id", sa.Uuid(), nullable=True),
        sa.Column("role", sa.String(length=40), nullable=True),
        sa.Column("throws", sa.String(length=8), nullable=True),
        sa.Column("pitches_previous_day", sa.Integer(), nullable=True),
        sa.Column("appearances_last_three_days", sa.Integer(), nullable=True),
        sa.Column("innings_last_three_days", sa.Float(), nullable=True),
        sa.Column("used_consecutive_days", sa.Boolean(), nullable=True),
        sa.Column("expected_available", sa.Boolean(), nullable=True),
        sa.Column("participation_probability", sa.Float(), nullable=True),
        sa.Column("workload", sa.JSON(), nullable=True),
        *_observation_columns(),
        sa.CheckConstraint(
            "participation_probability IS NULL OR (participation_probability >= 0 AND participation_probability <= 1)",
            name="ck_bullpen_avail_probability",
        ),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"]),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["true_talent_estimate_id"], ["player_talent_estimates.id"]),
        sa.UniqueConstraint(
            "game_id", "team_id", "player_id", "source", "source_mode", "available_as_of",
            name="uq_bullpen_avail_obs",
        ),
    )
    op.create_index("ix_bullpen_avail_game_team_asof", "bullpen_availability_observations", ["game_id", "team_id", "available_as_of"])

    op.create_table(
        "statcast_player_observations",
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=True),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False),
        sa.Column("evidence_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sample_size", sa.Float(), nullable=True),
        sa.Column("feature_schema_version", sa.String(length=40), nullable=False),
        sa.Column("values", sa.JSON(), nullable=False),
        sa.Column("raw_reference", sa.String(length=500), nullable=True),
        *_observation_columns(),
        sa.CheckConstraint("sample_size IS NULL OR sample_size >= 0", name="ck_statcast_player_sample"),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"]),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.UniqueConstraint(
            "player_id", "role", "season", "source", "source_mode", "available_as_of", "payload_checksum",
            name="uq_statcast_player_obs",
        ),
    )
    op.create_index("ix_statcast_player_role_asof", "statcast_player_observations", ["player_id", "role", "available_as_of"])
    op.create_index("ix_statcast_player_cutoff", "statcast_player_observations", ["evidence_cutoff"])

    op.create_table(
        "park_factor_versions",
        sa.Column("venue_id", sa.Uuid(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("overall_run_factor", sa.Float(), nullable=True),
        sa.Column("home_run_factor", sa.Float(), nullable=True),
        sa.Column("left_handed_run_factor", sa.Float(), nullable=True),
        sa.Column("right_handed_run_factor", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("sample_size", sa.Float(), nullable=True),
        sa.Column("methodology", sa.Text(), nullable=True),
        *_observation_columns(),
        sa.CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_park_factor_confidence"),
        sa.CheckConstraint("home_run_factor IS NULL OR home_run_factor > 0", name="ck_park_factor_hr"),
        sa.CheckConstraint("overall_run_factor IS NULL OR overall_run_factor > 0", name="ck_park_factor_run"),
        sa.ForeignKeyConstraint(["venue_id"], ["venues.id"]),
        sa.UniqueConstraint(
            "venue_id", "season", "version", "source", "source_mode",
            name="uq_park_factor_version",
        ),
    )
    op.create_index("ix_park_factor_venue_asof", "park_factor_versions", ["venue_id", "available_as_of"])

    op.create_table(
        "team_rating_observations",
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("rating", sa.Float(), nullable=False),
        sa.Column("method", sa.String(length=80), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("evidence_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sample_size", sa.Float(), nullable=True),
        sa.Column("components", sa.JSON(), nullable=True),
        *_observation_columns(),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.UniqueConstraint(
            "team_id", "method", "version", "source", "source_mode", "available_as_of",
            name="uq_team_rating_observation",
        ),
    )
    op.create_index("ix_team_rating_team_asof", "team_rating_observations", ["team_id", "available_as_of"])
    op.create_index("ix_team_rating_cutoff", "team_rating_observations", ["evidence_cutoff"])

    op.create_table(
        "source_freshness_policies",
        sa.Column("source_key", sa.String(length=80), nullable=False),
        sa.Column("lifecycle_stage", sa.String(length=32), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("fresh_threshold_minutes", sa.Integer(), nullable=False),
        sa.Column("stale_threshold_minutes", sa.Integer(), nullable=False),
        sa.Column("critical_threshold_minutes", sa.Integer(), nullable=False),
        sa.Column("blocks_final", sa.Boolean(), nullable=False),
        sa.Column("available_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        *_identity_columns(),
        sa.CheckConstraint("critical_threshold_minutes >= stale_threshold_minutes", name="ck_freshness_critical_order"),
        sa.CheckConstraint("fresh_threshold_minutes >= 0", name="ck_freshness_fresh_nonnegative"),
        sa.CheckConstraint("stale_threshold_minutes >= fresh_threshold_minutes", name="ck_freshness_stale_order"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_key", "lifecycle_stage", "version", name="uq_source_freshness_policy"),
    )
    op.create_index("ix_source_freshness_lookup", "source_freshness_policies", ["source_key", "lifecycle_stage", "available_as_of"])

    op.create_table(
        "game_feature_snapshots",
        sa.Column("game_id", sa.Uuid(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("feature_schema_version", sa.String(length=80), nullable=False),
        sa.Column("feature_version_id", sa.Uuid(), nullable=True),
        sa.Column("game_state_observation_id", sa.Uuid(), nullable=True),
        sa.Column("home_lineup_observation_id", sa.Uuid(), nullable=True),
        sa.Column("away_lineup_observation_id", sa.Uuid(), nullable=True),
        sa.Column("home_starter_observation_id", sa.Uuid(), nullable=True),
        sa.Column("away_starter_observation_id", sa.Uuid(), nullable=True),
        sa.Column("weather_snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("park_factor_version_id", sa.Uuid(), nullable=True),
        sa.Column("home_rating_observation_id", sa.Uuid(), nullable=True),
        sa.Column("away_rating_observation_id", sa.Uuid(), nullable=True),
        sa.Column("bullpen_observation_ids", sa.JSON(), nullable=False),
        sa.Column("roster_observation_ids", sa.JSON(), nullable=False),
        sa.Column("statcast_observation_ids", sa.JSON(), nullable=False),
        sa.Column("market_snapshot_ids", sa.JSON(), nullable=False),
        sa.Column("source_evidence", sa.JSON(), nullable=False),
        sa.Column("feature_values", sa.JSON(), nullable=False),
        sa.Column("feature_completeness", sa.Float(), nullable=False),
        sa.Column("missing_features", sa.JSON(), nullable=False),
        sa.Column("stale_features", sa.JSON(), nullable=False),
        sa.Column("provisional_features", sa.JSON(), nullable=False),
        sa.Column("bayesian_metadata", sa.JSON(), nullable=True),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("source_mode", sa.String(length=16), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        *_identity_columns(),
        sa.CheckConstraint("data_as_of <= generated_at", name="ck_game_feature_data_asof"),
        sa.CheckConstraint("feature_completeness >= 0 AND feature_completeness <= 1", name="ck_game_feature_completeness"),
        sa.ForeignKeyConstraint(["away_lineup_observation_id"], ["lineup_observations.id"]),
        sa.ForeignKeyConstraint(["away_rating_observation_id"], ["team_rating_observations.id"]),
        sa.ForeignKeyConstraint(["away_starter_observation_id"], ["starter_observations.id"]),
        sa.ForeignKeyConstraint(["feature_version_id"], ["feature_versions.id"]),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"]),
        sa.ForeignKeyConstraint(["game_state_observation_id"], ["game_state_observations.id"]),
        sa.ForeignKeyConstraint(["home_lineup_observation_id"], ["lineup_observations.id"]),
        sa.ForeignKeyConstraint(["home_rating_observation_id"], ["team_rating_observations.id"]),
        sa.ForeignKeyConstraint(["home_starter_observation_id"], ["starter_observations.id"]),
        sa.ForeignKeyConstraint(["park_factor_version_id"], ["park_factor_versions.id"]),
        sa.ForeignKeyConstraint(["weather_snapshot_id"], ["weather_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("checksum", name="uq_game_feature_snapshots_checksum"),
    )
    op.create_index("ix_game_feature_snapshot_game_asof", "game_feature_snapshots", ["game_id", "data_as_of"])
    op.create_index("ix_game_feature_snapshot_generated", "game_feature_snapshots", ["generated_at"])

    with op.batch_alter_table("forecasts") as batch_op:
        batch_op.add_column(sa.Column("feature_snapshot_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "fk_forecasts_feature_snapshot_id",
            "game_feature_snapshots",
            ["feature_snapshot_id"],
            ["id"],
        )
        batch_op.create_index("ix_forecasts_feature_snapshot_id", ["feature_snapshot_id"])

    op.create_table(
        "forecast_movement_evidence",
        sa.Column("game_id", sa.Uuid(), nullable=False),
        sa.Column("previous_forecast_id", sa.Uuid(), nullable=True),
        sa.Column("current_forecast_id", sa.Uuid(), nullable=False),
        sa.Column("previous_feature_snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("current_feature_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("feature_family", sa.String(length=80), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("previous_value", sa.JSON(), nullable=True),
        sa.Column("current_value", sa.JSON(), nullable=True),
        sa.Column("delta", sa.JSON(), nullable=True),
        sa.Column("associated_input_updates", sa.JSON(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("source_mode", sa.String(length=16), nullable=False),
        *_identity_columns(),
        sa.ForeignKeyConstraint(["current_feature_snapshot_id"], ["game_feature_snapshots.id"]),
        sa.ForeignKeyConstraint(["current_forecast_id"], ["forecasts.id"]),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"]),
        sa.ForeignKeyConstraint(["previous_feature_snapshot_id"], ["game_feature_snapshots.id"]),
        sa.ForeignKeyConstraint(["previous_forecast_id"], ["forecasts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("current_forecast_id", "feature_family", name="uq_forecast_movement_family"),
    )
    op.create_index("ix_forecast_movement_game_asof", "forecast_movement_evidence", ["game_id", "available_as_of"])

    op.create_table(
        "provider_request_ledger",
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("endpoint_key", sa.String(length=120), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("quota_cost", sa.Integer(), nullable=True),
        sa.Column("quota_remaining", sa.Integer(), nullable=True),
        sa.Column("records_received", sa.Integer(), nullable=True),
        sa.Column("controlled_live_request", sa.Boolean(), nullable=False),
        sa.Column("source_mode", sa.String(length=16), nullable=False),
        sa.Column("job_run_id", sa.Uuid(), nullable=True),
        sa.Column("reservation_id", sa.Uuid(), nullable=True),
        sa.Column("error_type", sa.String(length=120), nullable=True),
        sa.Column("response_metadata", sa.JSON(), nullable=True),
        *_identity_columns(),
        sa.CheckConstraint("quota_cost IS NULL OR quota_cost >= 0", name="ck_provider_ledger_quota_cost"),
        sa.CheckConstraint("quota_remaining IS NULL OR quota_remaining >= 0", name="ck_provider_ledger_quota_remaining"),
        sa.CheckConstraint("records_received IS NULL OR records_received >= 0", name="ck_provider_ledger_records"),
        sa.CheckConstraint("status IN ('RESERVED', 'SUCCEEDED', 'FAILED')", name="ck_provider_ledger_status"),
        sa.CheckConstraint(
            "(status = 'RESERVED' AND reservation_id IS NULL AND completed_at IS NULL) OR "
            "(status IN ('SUCCEEDED', 'FAILED') AND reservation_id IS NOT NULL AND completed_at IS NOT NULL)",
            name="ck_provider_ledger_lifecycle",
        ),
        sa.ForeignKeyConstraint(["job_run_id"], ["job_runs.id"]),
        sa.ForeignKeyConstraint(["reservation_id"], ["provider_request_ledger.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reservation_id", name="uq_provider_ledger_terminal_reservation"),
        sa.UniqueConstraint(
            "provider", "endpoint_key", "request_fingerprint", "status",
            name="uq_provider_ledger_request_status",
        ),
    )
    op.create_index("ix_provider_ledger_provider_requested", "provider_request_ledger", ["provider", "requested_at"])
    op.create_index("ix_provider_ledger_job", "provider_request_ledger", ["job_run_id"])
    op.create_index("ix_provider_ledger_reservation", "provider_request_ledger", ["reservation_id"])

    op.create_table(
        "game_refresh_states",
        sa.Column("game_id", sa.Uuid(), nullable=False),
        sa.Column("lifecycle_stage", sa.String(length=32), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_pitch_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("time_to_first_pitch_seconds", sa.Integer(), nullable=False),
        sa.Column("last_source_refresh_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_feature_snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("last_forecast_id", sa.Uuid(), nullable=True),
        sa.Column("next_scheduled_refresh_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("final_forecast_status", sa.String(length=32), nullable=False),
        sa.Column("final_snapshot_frozen", sa.Boolean(), nullable=False),
        sa.Column("freeze_reason", sa.Text(), nullable=True),
        sa.Column("source_mode", sa.String(length=16), nullable=False),
        *_identity_columns(),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"]),
        sa.ForeignKeyConstraint(["last_feature_snapshot_id"], ["game_feature_snapshots.id"]),
        sa.ForeignKeyConstraint(["last_forecast_id"], ["forecasts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "game_id", "evaluated_at", "lifecycle_stage", "source_mode",
            name="uq_game_refresh_state",
        ),
    )
    op.create_index("ix_game_refresh_state_game_asof", "game_refresh_states", ["game_id", "available_as_of"])
    op.create_index("ix_game_refresh_state_next", "game_refresh_states", ["next_scheduled_refresh_at"])


def _drop_table(name: str, indexes: tuple[str, ...]) -> None:
    for index in indexes:
        op.drop_index(index, table_name=name)
    op.drop_table(name)


def downgrade() -> None:
    _drop_table("game_refresh_states", ("ix_game_refresh_state_next", "ix_game_refresh_state_game_asof"))
    _drop_table("provider_request_ledger", ("ix_provider_ledger_job", "ix_provider_ledger_provider_requested"))
    _drop_table("forecast_movement_evidence", ("ix_forecast_movement_game_asof",))

    with op.batch_alter_table("forecasts") as batch_op:
        batch_op.drop_index("ix_forecasts_feature_snapshot_id")
        batch_op.drop_constraint("fk_forecasts_feature_snapshot_id", type_="foreignkey")
        batch_op.drop_column("feature_snapshot_id")

    _drop_table("game_feature_snapshots", ("ix_game_feature_snapshot_generated", "ix_game_feature_snapshot_game_asof"))
    _drop_table("source_freshness_policies", ("ix_source_freshness_lookup",))
    _drop_table("team_rating_observations", ("ix_team_rating_cutoff", "ix_team_rating_team_asof"))
    _drop_table("park_factor_versions", ("ix_park_factor_venue_asof",))
    _drop_table("statcast_player_observations", ("ix_statcast_player_cutoff", "ix_statcast_player_role_asof"))
    _drop_table("bullpen_availability_observations", ("ix_bullpen_avail_game_team_asof",))
    _drop_table("player_talent_estimates", ("ix_player_talent_evidence_cutoff", "ix_player_talent_player_metric_asof"))
    _drop_table(
        "roster_availability_observations",
        ("ix_roster_avail_effective", "ix_roster_avail_team_asof", "ix_roster_avail_player_asof"),
    )
    _drop_table("starter_observations", ("ix_starter_obs_game_team_asof",))
    _drop_table("lineup_entries", ("ix_lineup_entries_observation",))
    _drop_table("lineup_observations", ("ix_lineup_obs_game_team_asof",))
    _drop_table(
        "game_state_observations",
        ("ix_game_state_obs_away_team", "ix_game_state_obs_home_team", "ix_game_state_obs_game_asof"),
    )

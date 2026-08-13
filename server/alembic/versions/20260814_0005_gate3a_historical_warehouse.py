"""Add the governed Gate 3A historical evidence warehouse.

Revision ID: 20260814_0005
Revises: 20260813_0004
Create Date: 2026-08-14

Historical acquisition remains LIVE or DEMO provider evidence. Retrospective
timing is represented independently by ``temporal_semantics`` so these rows
cannot be mistaken for prospectively captured point-in-time observations.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260814_0005"
down_revision = "20260813_0004"
branch_labels = None
depends_on = None


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "historical_source_versions",
        sa.Column("source_key", sa.String(length=80), nullable=False),
        sa.Column("dataset_version", sa.String(length=80), nullable=False),
        sa.Column("provider_name", sa.String(length=120), nullable=False),
        sa.Column("source_mode", sa.String(length=16), nullable=False),
        sa.Column("source_classification", sa.String(length=32), nullable=False),
        sa.Column("temporal_semantics", sa.String(length=40), nullable=False),
        sa.Column("quality_tier", sa.String(length=16), nullable=False),
        sa.Column("training_eligible", sa.Boolean(), nullable=False),
        sa.Column("evaluation_eligible", sa.Boolean(), nullable=False),
        sa.Column("availability_policy", sa.JSON(), nullable=False),
        sa.Column("policy_version", sa.String(length=40), nullable=False),
        sa.Column("governance", sa.JSON(), nullable=True),
        sa.Column("documentation_uri", sa.String(length=500), nullable=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("identity_checksum", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        _created_at(),
        sa.CheckConstraint("source_mode IN ('LIVE', 'DEMO')", name="ck_hist_source_mode"),
        sa.CheckConstraint(
            "source_classification IN ('OFFICIAL_PRIMARY', 'LICENSED_MARKET', "
            "'PUBLIC_SECONDARY', 'DERIVED_INTERNAL', 'SYNTHETIC')",
            name="ck_hist_source_classification",
        ),
        sa.CheckConstraint(
            "temporal_semantics = 'RETROSPECTIVE_RECONSTRUCTION'",
            name="ck_hist_source_temporal_semantics",
        ),
        sa.CheckConstraint(
            "quality_tier IN ('A', 'B', 'C', 'EXCLUDED')",
            name="ck_hist_source_quality_tier",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_hist_source_effective_window",
        ),
        sa.CheckConstraint(
            "training_eligible = false OR evaluation_eligible = true",
            name="ck_hist_source_training_requires_eval",
        ),
        sa.CheckConstraint(
            "(training_eligible = false AND evaluation_eligible = false) OR "
            "(source_mode = 'LIVE' AND source_classification <> 'SYNTHETIC' "
            "AND quality_tier <> 'EXCLUDED')",
            name="ck_hist_source_eligibility",
        ),
        sa.CheckConstraint(
            "length(identity_checksum) = 64",
            name="ck_hist_source_identity_length",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"], ["historical_source_versions.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_key", "dataset_version", "source_mode",
            name="uq_hist_source_key_version_mode",
        ),
        sa.UniqueConstraint("identity_checksum", name="uq_hist_source_identity"),
    )
    op.create_index(
        "ix_hist_source_lookup",
        "historical_source_versions",
        ["source_key", "source_mode", "effective_from"],
    )
    op.create_index(
        "ix_hist_source_eligibility",
        "historical_source_versions",
        ["training_eligible", "quality_tier"],
    )

    op.create_table(
        "history_backfill_checkpoints",
        sa.Column("ingestion_run_id", sa.Uuid(), nullable=False),
        sa.Column("source_version_id", sa.Uuid(), nullable=False),
        sa.Column("parent_checkpoint_id", sa.Uuid(), nullable=True),
        sa.Column("stream_key", sa.String(length=120), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=True),
        sa.Column("event_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cursor_before", sa.JSON(), nullable=True),
        sa.Column("cursor_after", sa.JSON(), nullable=True),
        sa.Column("query_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("records_received", sa.Integer(), nullable=False),
        sa.Column("records_accepted", sa.Integer(), nullable=False),
        sa.Column("records_rejected", sa.Integer(), nullable=False),
        sa.Column("duplicate_count", sa.Integer(), nullable=False),
        sa.Column("raw_references", sa.JSON(), nullable=False),
        sa.Column("raw_checksums", sa.JSON(), nullable=False),
        sa.Column("next_resume_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("code_revision", sa.String(length=120), nullable=False),
        sa.Column("quality_summary", sa.JSON(), nullable=True),
        sa.Column("error_type", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("source_mode", sa.String(length=16), nullable=False),
        sa.Column("temporal_semantics", sa.String(length=40), nullable=False),
        sa.Column("identity_checksum", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        _created_at(),
        sa.CheckConstraint("source_mode IN ('LIVE', 'DEMO')", name="ck_hist_checkpoint_mode"),
        sa.CheckConstraint(
            "temporal_semantics = 'RETROSPECTIVE_RECONSTRUCTION'",
            name="ck_hist_checkpoint_temporal_semantics",
        ),
        sa.CheckConstraint(
            "status IN ('SUCCEEDED', 'FAILED', 'SKIPPED', 'BLOCKED')",
            name="ck_hist_checkpoint_status",
        ),
        sa.CheckConstraint("sequence >= 0", name="ck_hist_checkpoint_sequence"),
        sa.CheckConstraint(
            "season IS NULL OR season >= 1876",
            name="ck_hist_checkpoint_season",
        ),
        sa.CheckConstraint(
            "(event_start_at IS NULL AND event_end_at IS NULL) OR "
            "(event_start_at IS NOT NULL AND event_end_at IS NOT NULL "
            "AND event_end_at >= event_start_at)",
            name="ck_hist_checkpoint_event_window",
        ),
        sa.CheckConstraint(
            "completed_at >= requested_at",
            name="ck_hist_checkpoint_completion",
        ),
        sa.CheckConstraint(
            "attempt_count >= 1 AND retry_count >= 0 AND retry_count < attempt_count",
            name="ck_hist_checkpoint_attempts",
        ),
        sa.CheckConstraint(
            "records_received >= 0 AND records_accepted >= 0 AND "
            "records_rejected >= 0 AND duplicate_count >= 0",
            name="ck_hist_checkpoint_counts",
        ),
        sa.CheckConstraint(
            "records_accepted + records_rejected + duplicate_count <= records_received",
            name="ck_hist_checkpoint_count_balance",
        ),
        sa.CheckConstraint(
            "length(query_fingerprint) = 64 AND length(identity_checksum) = 64",
            name="ck_hist_checkpoint_checksum_lengths",
        ),
        sa.ForeignKeyConstraint(["ingestion_run_id"], ["ingestion_runs.id"]),
        sa.ForeignKeyConstraint(
            ["parent_checkpoint_id"], ["history_backfill_checkpoints.id"]
        ),
        sa.ForeignKeyConstraint(
            ["source_version_id"], ["historical_source_versions.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ingestion_run_id", "stream_key", "sequence",
            name="uq_hist_checkpoint_run_stream_sequence",
        ),
        sa.UniqueConstraint("identity_checksum", name="uq_hist_checkpoint_identity"),
    )
    op.create_index(
        "ix_hist_checkpoint_source_stream",
        "history_backfill_checkpoints",
        ["source_version_id", "stream_key", "sequence"],
    )
    op.create_index(
        "ix_hist_checkpoint_event_window",
        "history_backfill_checkpoints",
        ["event_start_at", "event_end_at"],
    )
    op.create_index(
        "ix_hist_checkpoint_status_completed",
        "history_backfill_checkpoints",
        ["status", "completed_at"],
    )

    op.create_table(
        "game_final_outcomes",
        sa.Column("game_id", sa.Uuid(), nullable=False),
        sa.Column("season_id", sa.Uuid(), nullable=False),
        sa.Column("home_team_id", sa.Uuid(), nullable=False),
        sa.Column("away_team_id", sa.Uuid(), nullable=False),
        sa.Column("venue_id", sa.Uuid(), nullable=True),
        sa.Column("game_state_observation_id", sa.Uuid(), nullable=True),
        sa.Column("source_version_id", sa.Uuid(), nullable=False),
        sa.Column("checkpoint_id", sa.Uuid(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("final_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled_innings", sa.Integer(), nullable=True),
        sa.Column("innings_played", sa.Integer(), nullable=True),
        sa.Column("home_score", sa.Integer(), nullable=False),
        sa.Column("away_score", sa.Integer(), nullable=False),
        sa.Column("outcome_status", sa.String(length=16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("supersedes_outcome_id", sa.Uuid(), nullable=True),
        sa.Column("official", sa.Boolean(), nullable=False),
        sa.Column("source_record_id", sa.String(length=255), nullable=False),
        sa.Column("source_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("label_available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("availability_basis", sa.String(length=80), nullable=False),
        sa.Column("payload_checksum", sa.String(length=64), nullable=False),
        sa.Column("source_mode", sa.String(length=16), nullable=False),
        sa.Column("temporal_semantics", sa.String(length=40), nullable=False),
        sa.Column("identity_checksum", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        _created_at(),
        sa.CheckConstraint("source_mode IN ('LIVE', 'DEMO')", name="ck_game_final_mode"),
        sa.CheckConstraint(
            "temporal_semantics = 'RETROSPECTIVE_RECONSTRUCTION'",
            name="ck_game_final_temporal_semantics",
        ),
        sa.CheckConstraint("outcome_status = 'FINAL'", name="ck_game_final_status"),
        sa.CheckConstraint(
            "home_team_id <> away_team_id",
            name="ck_game_final_distinct_sides",
        ),
        sa.CheckConstraint(
            "home_score >= 0 AND away_score >= 0",
            name="ck_game_final_scores",
        ),
        sa.CheckConstraint(
            "scheduled_innings IS NULL OR scheduled_innings > 0",
            name="ck_game_final_scheduled_innings",
        ),
        sa.CheckConstraint(
            "innings_played IS NULL OR innings_played > 0",
            name="ck_game_final_innings_played",
        ),
        sa.CheckConstraint("final_at >= scheduled_at", name="ck_game_final_time_order"),
        sa.CheckConstraint(
            "retrieved_at >= final_at AND label_available_at >= final_at",
            name="ck_game_final_availability",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_game_final_revision"),
        sa.CheckConstraint(
            "supersedes_outcome_id IS NULL OR supersedes_outcome_id <> id",
            name="ck_game_final_not_self_superseding",
        ),
        sa.CheckConstraint(
            "length(payload_checksum) = 64 AND length(identity_checksum) = 64",
            name="ck_game_final_checksum_lengths",
        ),
        sa.ForeignKeyConstraint(["away_team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["checkpoint_id"], ["history_backfill_checkpoints.id"]),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"]),
        sa.ForeignKeyConstraint(
            ["game_state_observation_id"], ["game_state_observations.id"]
        ),
        sa.ForeignKeyConstraint(["home_team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["season_id"], ["seasons.id"]),
        sa.ForeignKeyConstraint(
            ["source_version_id"], ["historical_source_versions.id"]
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_outcome_id"], ["game_final_outcomes.id"]
        ),
        sa.ForeignKeyConstraint(["venue_id"], ["venues.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "game_id", "source_version_id", "source_mode", "revision",
            name="uq_game_final_source_revision",
        ),
        sa.UniqueConstraint("identity_checksum", name="uq_game_final_identity"),
    )
    op.create_index(
        "ix_game_final_game_available",
        "game_final_outcomes",
        ["game_id", "label_available_at"],
    )
    op.create_index(
        "ix_game_final_season_scheduled",
        "game_final_outcomes",
        ["season_id", "scheduled_at"],
    )
    op.create_index("ix_game_final_checkpoint", "game_final_outcomes", ["checkpoint_id"])

    op.create_table(
        "team_game_history_observations",
        sa.Column("game_id", sa.Uuid(), nullable=False),
        sa.Column("final_outcome_id", sa.Uuid(), nullable=False),
        sa.Column("season_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("opponent_team_id", sa.Uuid(), nullable=False),
        sa.Column("is_home", sa.Boolean(), nullable=False),
        sa.Column("source_version_id", sa.Uuid(), nullable=False),
        sa.Column("checkpoint_id", sa.Uuid(), nullable=False),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("eligible_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("availability_basis", sa.String(length=80), nullable=False),
        sa.Column("evidence_classification", sa.String(length=40), nullable=False),
        sa.Column("quality_tier", sa.String(length=16), nullable=False),
        sa.Column("source_record_id", sa.String(length=255), nullable=False),
        sa.Column("stat_schema_version", sa.String(length=80), nullable=False),
        sa.Column("values", sa.JSON(), nullable=False),
        sa.Column("payload_checksum", sa.String(length=64), nullable=False),
        sa.Column("source_mode", sa.String(length=16), nullable=False),
        sa.Column("temporal_semantics", sa.String(length=40), nullable=False),
        sa.Column("identity_checksum", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        _created_at(),
        sa.CheckConstraint("source_mode IN ('LIVE', 'DEMO')", name="ck_team_game_history_mode"),
        sa.CheckConstraint(
            "temporal_semantics = 'RETROSPECTIVE_RECONSTRUCTION'",
            name="ck_team_game_history_temporal",
        ),
        sa.CheckConstraint(
            "evidence_classification IN ('RECONSTRUCTABLE_POINT_IN_TIME', "
            "'PARTIALLY_RECONSTRUCTABLE', 'FORWARD_ONLY')",
            name="ck_team_game_history_class",
        ),
        sa.CheckConstraint(
            "quality_tier IN ('A', 'B', 'C', 'EXCLUDED')",
            name="ck_team_game_history_tier",
        ),
        sa.CheckConstraint("team_id <> opponent_team_id", name="ck_team_game_history_teams"),
        sa.CheckConstraint(
            "evidence_end_at <= event_at AND eligible_at >= evidence_end_at "
            "AND retrieved_at >= evidence_end_at",
            name="ck_team_game_history_times",
        ),
        sa.CheckConstraint(
            "length(payload_checksum) = 64 AND length(identity_checksum) = 64",
            name="ck_team_game_history_checksums",
        ),
        sa.ForeignKeyConstraint(["checkpoint_id"], ["history_backfill_checkpoints.id"]),
        sa.ForeignKeyConstraint(["final_outcome_id"], ["game_final_outcomes.id"]),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"]),
        sa.ForeignKeyConstraint(["opponent_team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["season_id"], ["seasons.id"]),
        sa.ForeignKeyConstraint(
            ["source_version_id"], ["historical_source_versions.id"]
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "game_id", "team_id", "source_version_id", "source_mode",
            "source_record_id", "payload_checksum",
            name="uq_team_game_history_evidence",
        ),
        sa.UniqueConstraint("identity_checksum", name="uq_team_game_history_identity"),
    )
    op.create_index(
        "ix_team_game_history_game",
        "team_game_history_observations",
        ["game_id", "event_at"],
    )
    op.create_index(
        "ix_team_game_history_team_eligible",
        "team_game_history_observations",
        ["team_id", "eligible_at"],
    )
    op.create_index(
        "ix_team_game_history_checkpoint",
        "team_game_history_observations",
        ["checkpoint_id"],
    )

    op.create_table(
        "player_game_history_observations",
        sa.Column("game_id", sa.Uuid(), nullable=False),
        sa.Column("final_outcome_id", sa.Uuid(), nullable=False),
        sa.Column("season_id", sa.Uuid(), nullable=False),
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("opponent_team_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("started", sa.Boolean(), nullable=True),
        sa.Column("source_version_id", sa.Uuid(), nullable=False),
        sa.Column("checkpoint_id", sa.Uuid(), nullable=False),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("eligible_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("availability_basis", sa.String(length=80), nullable=False),
        sa.Column("evidence_classification", sa.String(length=40), nullable=False),
        sa.Column("quality_tier", sa.String(length=16), nullable=False),
        sa.Column("source_record_id", sa.String(length=255), nullable=False),
        sa.Column("stat_schema_version", sa.String(length=80), nullable=False),
        sa.Column("values", sa.JSON(), nullable=False),
        sa.Column("payload_checksum", sa.String(length=64), nullable=False),
        sa.Column("source_mode", sa.String(length=16), nullable=False),
        sa.Column("temporal_semantics", sa.String(length=40), nullable=False),
        sa.Column("identity_checksum", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        _created_at(),
        sa.CheckConstraint("source_mode IN ('LIVE', 'DEMO')", name="ck_player_game_history_mode"),
        sa.CheckConstraint(
            "temporal_semantics = 'RETROSPECTIVE_RECONSTRUCTION'",
            name="ck_player_game_history_temporal",
        ),
        sa.CheckConstraint(
            "evidence_classification IN ('RECONSTRUCTABLE_POINT_IN_TIME', "
            "'PARTIALLY_RECONSTRUCTABLE', 'FORWARD_ONLY')",
            name="ck_player_game_history_class",
        ),
        sa.CheckConstraint(
            "quality_tier IN ('A', 'B', 'C', 'EXCLUDED')",
            name="ck_player_game_history_tier",
        ),
        sa.CheckConstraint("team_id <> opponent_team_id", name="ck_player_game_history_teams"),
        sa.CheckConstraint(
            "evidence_end_at <= event_at AND eligible_at >= evidence_end_at "
            "AND retrieved_at >= evidence_end_at",
            name="ck_player_game_history_times",
        ),
        sa.CheckConstraint(
            "length(payload_checksum) = 64 AND length(identity_checksum) = 64",
            name="ck_player_game_history_checksums",
        ),
        sa.ForeignKeyConstraint(["checkpoint_id"], ["history_backfill_checkpoints.id"]),
        sa.ForeignKeyConstraint(["final_outcome_id"], ["game_final_outcomes.id"]),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"]),
        sa.ForeignKeyConstraint(["opponent_team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"]),
        sa.ForeignKeyConstraint(["season_id"], ["seasons.id"]),
        sa.ForeignKeyConstraint(
            ["source_version_id"], ["historical_source_versions.id"]
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "game_id", "player_id", "team_id", "role", "source_version_id",
            "source_mode", "source_record_id", "payload_checksum",
            name="uq_player_game_history_evidence",
        ),
        sa.UniqueConstraint("identity_checksum", name="uq_player_game_history_identity"),
    )
    op.create_index(
        "ix_player_game_history_game",
        "player_game_history_observations",
        ["game_id", "event_at"],
    )
    op.create_index(
        "ix_player_game_history_player_eligible",
        "player_game_history_observations",
        ["player_id", "eligible_at"],
    )
    op.create_index(
        "ix_player_game_history_team_eligible",
        "player_game_history_observations",
        ["team_id", "eligible_at"],
    )
    op.create_index(
        "ix_player_game_history_checkpoint",
        "player_game_history_observations",
        ["checkpoint_id"],
    )

    op.create_table(
        "historical_feature_materializations",
        sa.Column("game_id", sa.Uuid(), nullable=False),
        sa.Column("game_feature_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("feature_version_id", sa.Uuid(), nullable=False),
        sa.Column("game_final_outcome_id", sa.Uuid(), nullable=True),
        sa.Column("checkpoint_id", sa.Uuid(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cutoff_policy_version", sa.String(length=40), nullable=False),
        sa.Column("cutoff_policy", sa.JSON(), nullable=False),
        sa.Column("source_bundle", sa.JSON(), nullable=False),
        sa.Column("source_bundle_checksum", sa.String(length=64), nullable=False),
        sa.Column("evidence_manifest", sa.JSON(), nullable=False),
        sa.Column("evidence_manifest_checksum", sa.String(length=64), nullable=False),
        sa.Column("quality_tier", sa.String(length=16), nullable=False),
        sa.Column("quality_score", sa.Float(), nullable=False),
        sa.Column("quality_components", sa.JSON(), nullable=False),
        sa.Column("materializer_version", sa.String(length=80), nullable=False),
        sa.Column("code_revision", sa.String(length=120), nullable=False),
        sa.Column("eligibility_status", sa.String(length=24), nullable=False),
        sa.Column("eligibility_reasons", sa.JSON(), nullable=False),
        sa.Column("training_eligible", sa.Boolean(), nullable=False),
        sa.Column("source_mode", sa.String(length=16), nullable=False),
        sa.Column("temporal_semantics", sa.String(length=40), nullable=False),
        sa.Column("identity_checksum", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        _created_at(),
        sa.CheckConstraint("source_mode IN ('LIVE', 'DEMO')", name="ck_hist_materialization_mode"),
        sa.CheckConstraint(
            "temporal_semantics = 'RETROSPECTIVE_RECONSTRUCTION'",
            name="ck_hist_materialization_temporal",
        ),
        sa.CheckConstraint(
            "quality_tier IN ('A', 'B', 'C', 'EXCLUDED')",
            name="ck_hist_materialization_tier",
        ),
        sa.CheckConstraint(
            "quality_score >= 0 AND quality_score <= 100",
            name="ck_hist_materialization_quality",
        ),
        sa.CheckConstraint(
            "eligibility_status IN ('ELIGIBLE', 'INELIGIBLE', 'INSUFFICIENT_DATA')",
            name="ck_hist_materialization_status",
        ),
        sa.CheckConstraint(
            "cutoff_at < scheduled_at AND generated_at >= cutoff_at",
            name="ck_hist_materialization_times",
        ),
        sa.CheckConstraint(
            "training_eligible = false OR (eligibility_status = 'ELIGIBLE' "
            "AND source_mode = 'LIVE' AND quality_tier IN ('A', 'B'))",
            name="ck_hist_materialization_training",
        ),
        sa.CheckConstraint(
            "length(source_bundle_checksum) = 64 "
            "AND length(evidence_manifest_checksum) = 64 "
            "AND length(identity_checksum) = 64",
            name="ck_hist_materialization_checksums",
        ),
        sa.ForeignKeyConstraint(["checkpoint_id"], ["history_backfill_checkpoints.id"]),
        sa.ForeignKeyConstraint(["feature_version_id"], ["feature_versions.id"]),
        sa.ForeignKeyConstraint(["game_feature_snapshot_id"], ["game_feature_snapshots.id"]),
        sa.ForeignKeyConstraint(["game_final_outcome_id"], ["game_final_outcomes.id"]),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game_feature_snapshot_id"),
        sa.UniqueConstraint("identity_checksum", name="uq_hist_materialization_identity"),
        sa.UniqueConstraint(
            "game_id", "cutoff_at", "feature_version_id", "cutoff_policy_version",
            "source_mode", "materializer_version",
            name="uq_hist_materialization_semantic",
        ),
    )
    op.create_index(
        "ix_hist_materialization_game_cutoff",
        "historical_feature_materializations",
        ["game_id", "cutoff_at"],
    )
    op.create_index(
        "ix_hist_materialization_checkpoint",
        "historical_feature_materializations",
        ["checkpoint_id"],
    )
    op.create_index(
        "ix_hist_materialization_eligibility",
        "historical_feature_materializations",
        ["training_eligible", "quality_tier", "scheduled_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_hist_materialization_eligibility",
        table_name="historical_feature_materializations",
    )
    op.drop_index(
        "ix_hist_materialization_checkpoint",
        table_name="historical_feature_materializations",
    )
    op.drop_index(
        "ix_hist_materialization_game_cutoff",
        table_name="historical_feature_materializations",
    )
    op.drop_table("historical_feature_materializations")

    op.drop_index(
        "ix_player_game_history_checkpoint",
        table_name="player_game_history_observations",
    )
    op.drop_index(
        "ix_player_game_history_team_eligible",
        table_name="player_game_history_observations",
    )
    op.drop_index(
        "ix_player_game_history_player_eligible",
        table_name="player_game_history_observations",
    )
    op.drop_index(
        "ix_player_game_history_game",
        table_name="player_game_history_observations",
    )
    op.drop_table("player_game_history_observations")

    op.drop_index(
        "ix_team_game_history_checkpoint",
        table_name="team_game_history_observations",
    )
    op.drop_index(
        "ix_team_game_history_team_eligible",
        table_name="team_game_history_observations",
    )
    op.drop_index(
        "ix_team_game_history_game",
        table_name="team_game_history_observations",
    )
    op.drop_table("team_game_history_observations")

    op.drop_index("ix_game_final_checkpoint", table_name="game_final_outcomes")
    op.drop_index("ix_game_final_season_scheduled", table_name="game_final_outcomes")
    op.drop_index("ix_game_final_game_available", table_name="game_final_outcomes")
    op.drop_table("game_final_outcomes")

    op.drop_index(
        "ix_hist_checkpoint_status_completed",
        table_name="history_backfill_checkpoints",
    )
    op.drop_index(
        "ix_hist_checkpoint_event_window",
        table_name="history_backfill_checkpoints",
    )
    op.drop_index(
        "ix_hist_checkpoint_source_stream",
        table_name="history_backfill_checkpoints",
    )
    op.drop_table("history_backfill_checkpoints")

    op.drop_index("ix_hist_source_eligibility", table_name="historical_source_versions")
    op.drop_index("ix_hist_source_lookup", table_name="historical_source_versions")
    op.drop_table("historical_source_versions")

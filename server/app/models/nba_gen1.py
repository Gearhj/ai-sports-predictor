"""NBA Gen1 consumer evidence: production packages, historical rows, discovery, finals, scores.

WHY THESE ARE NOT THE RESEARCH TABLES
-------------------------------------
``nba_research_forecasts`` exists because the BOS-DET research package had NO
distribution (drawCount 0), and ``nba_joint_comparison_arms`` holds two research
arms of one comparison. A production package carries all three channels at once
-- Ridge points, Elo winner probabilities and a 10,000-draw joint population --
under a pinned model handoff and a publication receipt. Folding it into either
research table would put a governed prospective forecast and a research example
into the same columns, which is how one gets read as the other.

THREE CHANNELS, THREE COLUMN GROUPS
-----------------------------------
``ridge_*``  expected points (points, including overtime). NEVER a probability.
``elo_*``    chronological Elo winner probabilities (fractions 0-1).
``joint_*``  the paired-residual joint score population: winner probability,
             expected scores, and the PMFs/intervals in ``joint_summary``.
Nothing here derives one channel from another. A Ridge expected margin is never
turned into a win probability, and the joint winner probability is never called
"the Elo number".

HISTORICAL IS ITS OWN TABLE
---------------------------
``nba_historical_forecasts`` holds Model Studio's 2025-26 fixed assessment rows
(evidence class ``A_HISTORICAL_FINALIZED_RECORD_RECONSTRUCTION``). They were
reconstructed from finalized records with the model frozen before scores were
read; they were never published before their games, carry no publication clock,
and are never scored as prospective forecasts. Every read labels them HISTORICAL.

SCORES
------
``nba_channel_evaluations`` scores each channel separately. Probability metrics
are null on the Ridge row and point metrics are null on the Elo row: a column
that cannot honestly be filled stays empty rather than being derived from a
different channel. ``research_forecast_evaluations`` requires a log loss on
every row, which a points-only channel cannot supply without inventing one.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.entities import CreatedAtMixin, UUIDPrimaryKeyMixin

#: Channel identities. The same three strings travel through API and UI.
CHANNEL_RIDGE = "RIDGE_POINTS"
CHANNEL_ELO = "ELO_WINNER"
CHANNEL_JOINT = "JOINT_DISTRIBUTION"
CHANNELS = (CHANNEL_RIDGE, CHANNEL_ELO, CHANNEL_JOINT)

#: Evidence classes. HISTORICAL rows are never counted as prospective.
EVIDENCE_PROSPECTIVE = "PROSPECTIVE_PRODUCTION_PACKAGE"
EVIDENCE_HISTORICAL = "A_HISTORICAL_FINALIZED_RECORD_RECONSTRUCTION"


class _ChannelColumns:
    """The three channel groups, shared by production and historical rows."""

    # -- Ridge: expected points, never a probability ----------------------
    ridge_home_points: Mapped[float | None] = mapped_column(Float)
    ridge_away_points: Mapped[float | None] = mapped_column(Float)
    ridge_total_points: Mapped[float | None] = mapped_column(Float)
    ridge_home_margin: Mapped[float | None] = mapped_column(Float)

    # -- Elo: winner probability, fractions 0-1 ----------------------------
    elo_home_win_probability: Mapped[float | None] = mapped_column(Float)
    elo_away_win_probability: Mapped[float | None] = mapped_column(Float)
    elo_tie_probability: Mapped[float | None] = mapped_column(Float)
    elo_method: Mapped[str | None] = mapped_column(Text)

    # -- Joint: the paired population --------------------------------------
    joint_draw_count: Mapped[int | None] = mapped_column(Integer)
    joint_home_win_probability: Mapped[float | None] = mapped_column(Float)
    joint_away_win_probability: Mapped[float | None] = mapped_column(Float)
    joint_home_win_mcse: Mapped[float | None] = mapped_column(Float)
    joint_expected_home: Mapped[float | None] = mapped_column(Float)
    joint_expected_away: Mapped[float | None] = mapped_column(Float)
    joint_expected_total: Mapped[float | None] = mapped_column(Float)
    joint_expected_margin: Mapped[float | None] = mapped_column(Float)
    #: PMFs (home, away, total, margin as value/count lists), intervals and any
    #: illustrative totals, exactly as recomputed from the verified draws.
    joint_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class NbaGen1Forecast(UUIDPrimaryKeyMixin, CreatedAtMixin, _ChannelColumns, Base):
    """One verified NBA Gen1 PRODUCTION forecast package. Immutable once written."""

    __tablename__ = "nba_gen1_forecasts"

    forecast_id: Mapped[str] = mapped_column(String(200), nullable=False)
    forecast_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    forecast_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    forecast_schema: Mapped[str] = mapped_column(String(80), nullable=False)
    #: The package directory's content address (sha256 of index.json).
    package_index_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    package_index_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    envelope_schema: Mapped[str] = mapped_column(String(80), nullable=False)
    revision_key: Mapped[str | None] = mapped_column(String(80))
    evidence_class: Mapped[str] = mapped_column(String(80), nullable=False, default=EVIDENCE_PROSPECTIVE)

    configuration_identity: Mapped[str] = mapped_column(String(120), nullable=False)
    distribution_configuration_identity: Mapped[str] = mapped_column(String(120), nullable=False)
    handoff_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    prospective_effective_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: Codex's identities, exactly as delivered.
    codex_game_id: Mapped[str] = mapped_column(String(80), nullable=False)
    source_game_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    home_franchise_id: Mapped[str] = mapped_column(String(80), nullable=False)
    away_franchise_id: Mapped[str] = mapped_column(String(80), nullable=False)
    home_team_code: Mapped[str] = mapped_column(String(8), nullable=False)
    away_team_code: Mapped[str] = mapped_column(String(8), nullable=False)
    season_end: Mapped[int | None] = mapped_column(Integer)
    season_type: Mapped[str | None] = mapped_column(String(24))
    neutral_site: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    schedule_source_revision: Mapped[str | None] = mapped_column(Text)
    captured_tipoff_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    captured_game_date: Mapped[date] = mapped_column(Date, nullable=False)

    #: The AISP game, bound by exact ESPN game id. Null until bound.
    canonical_game_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("games.id"), index=True)

    population_artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    population_logical_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    # -- clocks, kept apart ------------------------------------------------
    source_cutoff_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    forecast_created_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: The package's own publishedAtUtc (V1 carries one; V2 is null by design).
    producer_published_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: The DESIGNATED publication clock AISP uses: V2 = the bound publication
    #: receipt's committedAtUtc; V1 = the package's publishedAtUtc.
    exchange_published_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    publication_clock_source: Mapped[str] = mapped_column(String(60), nullable=False)
    discovery_receipt_sha256: Mapped[str | None] = mapped_column(String(64))
    first_manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    #: Limitations, monitoring, bindings, authority and channel semantics as delivered.
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("forecast_id", name="uq_nba_gen1_forecast_id"),
        CheckConstraint("joint_draw_count IS NULL OR joint_draw_count = 10000",
                        name="ck_nba_gen1_forecast_draws"),
        CheckConstraint("elo_home_win_probability IS NULL OR "
                        "(elo_home_win_probability >= 0 AND elo_home_win_probability <= 1)",
                        name="ck_nba_gen1_forecast_elo_fraction"),
        CheckConstraint("joint_home_win_probability IS NULL OR "
                        "(joint_home_win_probability >= 0 AND joint_home_win_probability <= 1)",
                        name="ck_nba_gen1_forecast_joint_fraction"),
    )


class NbaHistoricalForecast(UUIDPrimaryKeyMixin, CreatedAtMixin, _ChannelColumns, Base):
    """One game of Model Studio's 2025-26 fixed assessment. HISTORICAL, never prospective."""

    __tablename__ = "nba_historical_forecasts"

    evidence_class: Mapped[str] = mapped_column(String(80), nullable=False, default=EVIDENCE_HISTORICAL)
    #: The two receipts this row was read through, each verified from bytes.
    assessment_receipt_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    joint_receipt_sha256: Mapped[str | None] = mapped_column(String(64))
    predictions_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    joint_counts_sha256: Mapped[str | None] = mapped_column(String(64))
    #: Model Studio's own per-game digest of the joint cell counts, reproduced here.
    joint_counts_int64_le_sha256: Mapped[str | None] = mapped_column(String(64))

    point_model_id: Mapped[str] = mapped_column(String(40), nullable=False)
    point_model_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    elo_model_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    joint_configuration: Mapped[str | None] = mapped_column(String(120))

    codex_game_id: Mapped[str] = mapped_column(String(80), nullable=False)
    source_game_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    home_franchise_id: Mapped[str] = mapped_column(String(80), nullable=False)
    away_franchise_id: Mapped[str] = mapped_column(String(80), nullable=False)
    home_team_code: Mapped[str] = mapped_column(String(8), nullable=False)
    away_team_code: Mapped[str] = mapped_column(String(8), nullable=False)
    season_end: Mapped[int] = mapped_column(Integer, nullable=False)
    game_date: Mapped[date] = mapped_column(Date, nullable=False)
    canonical_game_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("games.id"), index=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("assessment_receipt_sha256", "codex_game_id", name="uq_nba_historical_row"),
    )


class NbaDiscoveryManifest(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One NBA discovery document AISP verified, once (V1 single-game file or V2 slate manifest)."""

    __tablename__ = "nba_discovery_manifests"

    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    producer_created_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    predecessor_sha256: Mapped[str | None] = mapped_column(String(64))
    release_sha256: Mapped[str | None] = mapped_column(String(64))
    handoff_sha256: Mapped[str | None] = mapped_column(String(64))
    packages_listed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    packages_verified: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    packages_current_at_check: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: V1 discovery carries no eligibility lease at all; say so rather than infer one.
    lease_semantics: Mapped[str] = mapped_column(String(60), nullable=False)
    dispositions: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    first_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (UniqueConstraint("manifest_sha256", name="uq_nba_discovery_manifest_sha"),)


class NbaDiscoveryObservation(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """What one discovery document said about one package, verified from bytes."""

    __tablename__ = "nba_discovery_observations"

    manifest_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nba_discovery_manifests.id"),
                                                   nullable=False, index=True)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    forecast_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    nba_gen1_forecast_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("nba_gen1_forecasts.id"))
    codex_game_id: Mapped[str] = mapped_column(String(80), nullable=False)
    package_address: Mapped[str] = mapped_column(String(64), nullable=False)
    producer_status: Mapped[str | None] = mapped_column(String(60))
    currently_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    eligibility_valid_until_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: CURRENT / EXPIRED_LEASE / HISTORICAL / NO_LEASE_IN_SCHEMA, at AISP's first check.
    lease_at_check: Mapped[str] = mapped_column(String(24), nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    problems: Mapped[list[Any] | None] = mapped_column(JSON)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (UniqueConstraint("manifest_sha256", "forecast_id", name="uq_nba_discovery_obs"),)


class NbaFinalObservation(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One retained ESPN schedule row with a completed final, bound to an AISP game."""

    __tablename__ = "nba_final_observations"

    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), nullable=False, index=True)
    game_state_observation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("game_state_observations.id"), nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    source_game_id: Mapped[str] = mapped_column(String(32), nullable=False)
    home_espn_team_id: Mapped[str] = mapped_column(String(16), nullable=False)
    away_espn_team_id: Mapped[str] = mapped_column(String(16), nullable=False)
    home_points: Mapped[int] = mapped_column(Integer, nullable=False)
    away_points: Mapped[int] = mapped_column(Integer, nullable=False)
    periods: Mapped[int | None] = mapped_column(Integer)
    overtime: Mapped[bool | None] = mapped_column(Boolean)
    source_status: Mapped[str] = mapped_column(String(40), nullable=False)
    #: The retained source object (content address) and the row it was re-read from.
    source_object_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_object_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    #: When the source object was acquired. Never a completion time.
    captured_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completion_clock_status: Mapped[str] = mapped_column(String(60), nullable=False)
    payload_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_row: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    __table_args__ = (UniqueConstraint("game_id", "payload_checksum", name="uq_nba_final_obs"),)


class NbaChannelEvaluation(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One channel of one NBA forecast, scored against one authoritative final."""

    __tablename__ = "nba_channel_evaluations"

    evidence_class: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    forecast_identity: Mapped[str] = mapped_column(String(200), nullable=False)
    nba_gen1_forecast_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("nba_gen1_forecasts.id"))
    nba_historical_forecast_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("nba_historical_forecasts.id"))
    canonical_game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), nullable=False, index=True)
    source_game_id: Mapped[str] = mapped_column(String(32), nullable=False)
    start_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    forecast_published_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    result_identity: Mapped[str] = mapped_column(String(200), nullable=False)
    result_source: Mapped[str] = mapped_column(String(64), nullable=False)
    home_score: Mapped[int] = mapped_column(Integer, nullable=False)
    away_score: Mapped[int] = mapped_column(Integer, nullable=False)
    overtime: Mapped[bool | None] = mapped_column(Boolean)

    # Probability channels only (ELO_WINNER, JOINT_DISTRIBUTION).
    home_win_probability: Mapped[float | None] = mapped_column(Float)
    probability_of_actual: Mapped[float | None] = mapped_column(Float)
    log_loss: Mapped[float | None] = mapped_column(Float)
    two_way_brier: Mapped[float | None] = mapped_column(Float)
    winner_correct: Mapped[bool | None] = mapped_column(Boolean)

    # Point channels only (RIDGE_POINTS, and the joint population's own means).
    predicted_home: Mapped[float | None] = mapped_column(Float)
    predicted_away: Mapped[float | None] = mapped_column(Float)
    home_abs_error: Mapped[float | None] = mapped_column(Float)
    away_abs_error: Mapped[float | None] = mapped_column(Float)
    margin_error: Mapped[float | None] = mapped_column(Float)
    total_error: Mapped[float | None] = mapped_column(Float)

    # Distribution (JOINT_DISTRIBUTION only).
    crps_home: Mapped[float | None] = mapped_column(Float)
    crps_away: Mapped[float | None] = mapped_column(Float)
    crps_margin: Mapped[float | None] = mapped_column(Float)
    crps_total: Mapped[float | None] = mapped_column(Float)
    interval_coverage: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    detail: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    scoring_version: Mapped[str] = mapped_column(String(48), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("forecast_identity", "channel", "result_identity", name="uq_nba_channel_eval"),
        Index("ix_nba_channel_eval_class_channel", "evidence_class", "channel"),
        CheckConstraint(
            "channel <> 'RIDGE_POINTS' OR (log_loss IS NULL AND two_way_brier IS NULL "
            "AND home_win_probability IS NULL)",
            name="ck_nba_channel_eval_ridge_is_not_a_probability"),
        CheckConstraint(
            "channel <> 'ELO_WINNER' OR (predicted_home IS NULL AND predicted_away IS NULL)",
            name="ck_nba_channel_eval_elo_has_no_points"),
    )


__all__ = [
    "CHANNELS", "CHANNEL_ELO", "CHANNEL_JOINT", "CHANNEL_RIDGE", "EVIDENCE_HISTORICAL",
    "EVIDENCE_PROSPECTIVE", "NbaChannelEvaluation", "NbaDiscoveryManifest", "NbaDiscoveryObservation",
    "NbaFinalObservation", "NbaGen1Forecast", "NbaHistoricalForecast",
]

"""NHL Gen1 research forecasts, NHL results and the import receipts for both.

WHY NOT ``shadow_forecasts``
----------------------------
``shadow_forecasts`` mandates a simulator identity (``simulator_id``, version,
seed all NOT NULL) and a two-way MLB-shaped full-game/first-five split. An NHL
Gen1 forecast has neither: its central object is a THREE-way regulation result
(home / tie at 60:00 / away) plus a separate TWO-way final including overtime
and the shootout. Squeezing that into a two-way table either drops the
regulation tie or invents a simulator, so NHL gets its own minimal tables, the
same decision CFB and NBA made (``cfb_research_forecasts``,
``nba_research_forecasts``).

REGULATION AND FINAL ARE DIFFERENT OUTCOMES
-------------------------------------------
They are stored in separately named columns and scored separately. Nothing
pools them, and nothing reuses the NFL tie/push or settlement logic: an NHL
final never ties, a regulation tie goes to OT/SO.

IDENTITY AND HISTORY
--------------------
``forecast_id`` is the producer's content hash (``nhl-gen1-forecast:sha256:...``
over the canonical document minus ``forecastId`` and ``publishedAtUtc``). It is
unique. A new identity for the same game is a REVISION and is kept; the current
forecast for a game is the latest by publication clock. Conflicting bytes under
an identity already held are refused with a receipt, never merged.

CLOCKS
------
Producer clocks (source cutoff, source retrieval, creation, publication) are
stored as delivered and never overwritten. ``available_as_of`` and
``imported_at`` are AISP's own: when AISP could first use the record.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.entities import CreatedAtMixin, UUIDPrimaryKeyMixin

NHL_LEAGUE_CODE = "NHL"
NHL_SPORT_CODE = "HOCKEY"
#: external_id_mappings.source values binding NHL identities to AISP UUIDs.
NHL_GAME_SOURCE = "NHL_STATS_GAME"
NHL_TEAM_SOURCE = "NHL_STATS_TEAM"

_FRACTIONS = (
    "regulation_home_probability", "regulation_tie_probability", "regulation_away_probability",
    "final_home_probability", "final_away_probability",
)


class NhlResearchForecast(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One imported NHL Gen1 research forecast. Immutable; keyed by its content identity."""

    __tablename__ = "nhl_research_forecasts"

    forecast_id: Mapped[str] = mapped_column(String(120), nullable=False)
    #: SHA-256 of the exact object bytes as delivered (the object directory name).
    object_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    object_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    forecast_schema: Mapped[str] = mapped_column(String(60), nullable=False)
    supersedes_forecast_id: Mapped[str | None] = mapped_column(String(120))

    model_id: Mapped[str] = mapped_column(String(120), nullable=False)
    model_version: Mapped[str] = mapped_column(String(60), nullable=False)
    configuration_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    # -- identity -----------------------------------------------------------
    canonical_game_ref: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    source_game_id: Mapped[str] = mapped_column(String(16), nullable=False)
    #: The AISP game, bound through external_id_mappings. Null = UNBOUND_GAME.
    game_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("games.id"), index=True)
    season: Mapped[str] = mapped_column(String(9), nullable=False)
    game_type: Mapped[str] = mapped_column(String(12), nullable=False)
    scheduled_start_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    home_canonical_team_ref: Mapped[str] = mapped_column(String(20), nullable=False)
    away_canonical_team_ref: Mapped[str] = mapped_column(String(20), nullable=False)

    # -- probabilities (fractions) -----------------------------------------
    #: Null when the model is final-only (e.g. Elo): "regulation not modelled",
    #: never zero and never inferred from the final.
    regulation_home_probability: Mapped[float | None] = mapped_column(Float)
    regulation_tie_probability: Mapped[float | None] = mapped_column(Float)
    regulation_away_probability: Mapped[float | None] = mapped_column(Float)
    final_home_probability: Mapped[float] = mapped_column(Float, nullable=False)
    final_away_probability: Mapped[float] = mapped_column(Float, nullable=False)
    overtime_probability: Mapped[float | None] = mapped_column(Float)
    shootout_probability: Mapped[float | None] = mapped_column(Float)

    # -- goals ----------------------------------------------------------------
    regulation_expected_home_goals: Mapped[float | None] = mapped_column(Float)
    regulation_expected_away_goals: Mapped[float | None] = mapped_column(Float)
    final_expected_home_goals: Mapped[float | None] = mapped_column(Float)
    final_expected_away_goals: Mapped[float | None] = mapped_column(Float)

    # -- goalies ----------------------------------------------------------------
    home_goalie_status: Mapped[str] = mapped_column(String(12), nullable=False)
    away_goalie_status: Mapped[str] = mapped_column(String(12), nullable=False)
    goalies: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    missing_inputs: Mapped[list[Any]] = mapped_column(JSON, nullable=False)

    # -- provenance (v1 rev 2) ----------------------------------------------------
    #: CONTEMPORANEOUS_RETAINED | RECONSTRUCTED_HISTORY_CURRENT_FORECAST
    evidence_mode: Mapped[str] = mapped_column(String(48), nullable=False)
    #: TEAM_LEVEL | GOALIE_SPECIFIC
    goalie_feature_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    producer_evidence_ref: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    #: VERIFIED (bytes resolved and matched) | NOT_AVAILABLE_TO_CONSUMER | MISMATCH is refused.
    producer_evidence_status: Mapped[str] = mapped_column(String(32), nullable=False)
    #: FORECAST (a real producer forecast) | PREVIEW_FIXTURE | SYNTHETIC_FIXTURE.
    #: Fixtures are never shown or scored as real forecasts.
    record_class: Mapped[str] = mapped_column(String(20), nullable=False, default="FORECAST")

    #: The whole validated document, for display and audit.
    document: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    # -- clocks -------------------------------------------------------------
    source_cutoff_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_retrieved_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    forecast_created_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Which clock ``published_utc`` holds: FORECAST_PUBLISHED_AT or MANIFEST_RECEIPT_COMMITTED_AT.
    publication_clock_source: Mapped[str] = mapped_column(String(40), nullable=False)
    available_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    research_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = tuple(
        [
            UniqueConstraint("forecast_id", name="uq_nhl_research_forecast_id"),
            CheckConstraint("research_only", name="ck_nhl_research_only"),
            CheckConstraint("game_type IN ('PRESEASON','REGULAR','PLAYOFF')", name="ck_nhl_forecast_game_type"),
            CheckConstraint("record_class IN ('FORECAST','PREVIEW_FIXTURE','SYNTHETIC_FIXTURE')",
                            name="ck_nhl_forecast_record_class"),
            CheckConstraint(
                "(regulation_home_probability IS NULL) = (regulation_tie_probability IS NULL) AND "
                "(regulation_home_probability IS NULL) = (regulation_away_probability IS NULL)",
                name="ck_nhl_regulation_all_or_none"),
            Index("ix_nhl_forecast_game_published", "canonical_game_ref", "published_utc"),
        ]
        + [CheckConstraint(f"{c} >= 0 AND {c} <= 1", name=f"ck_nhl_{c}_fraction") for c in _FRACTIONS]
    )


class NhlGameResult(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One NHL result record. Regulation, OT/SO indicator and official final kept apart.

    Corrections are new rows (``supersedes_result_sha256``); nothing is edited.
    """

    __tablename__ = "nhl_game_results"

    result_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    object_sha256: Mapped[str | None] = mapped_column(String(64))
    supersedes_result_sha256: Mapped[str | None] = mapped_column(String(64))
    canonical_game_ref: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    game_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("games.id"), index=True)
    game_type: Mapped[str] = mapped_column(String(12), nullable=False)

    # -- regulation (60:00) ---------------------------------------------------
    regulation_home_goals: Mapped[int] = mapped_column(Integer, nullable=False)
    regulation_away_goals: Mapped[int] = mapped_column(Integer, nullable=False)
    regulation_result: Mapped[str] = mapped_column(String(4), nullable=False)  # HOME|AWAY|TIE
    regulation_source: Mapped[str] = mapped_column(String(60), nullable=False)
    regulation_observed_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    regulation_retrieved_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # -- how it was decided -----------------------------------------------------
    decided_in: Mapped[str] = mapped_column(String(10), nullable=False)  # REGULATION|OVERTIME|SHOOTOUT
    overtime_home_goals: Mapped[int | None] = mapped_column(Integer)
    overtime_away_goals: Mapped[int | None] = mapped_column(Integer)
    shootout_home_scored: Mapped[int | None] = mapped_column(Integer)
    shootout_away_scored: Mapped[int | None] = mapped_column(Integer)

    # -- official final -----------------------------------------------------------
    final_home_score: Mapped[int] = mapped_column(Integer, nullable=False)
    final_away_score: Mapped[int] = mapped_column(Integer, nullable=False)
    final_winner: Mapped[str] = mapped_column(String(4), nullable=False)  # HOME|AWAY, never TIE
    final_official: Mapped[bool] = mapped_column(Boolean, nullable=False)
    final_source: Mapped[str] = mapped_column(String(60), nullable=False)
    final_observed_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    final_retrieved_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    final_completed_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    document: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    available_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("result_sha256", name="uq_nhl_game_result_sha256"),
        CheckConstraint("regulation_result IN ('HOME','AWAY','TIE')", name="ck_nhl_regulation_result"),
        CheckConstraint("decided_in IN ('REGULATION','OVERTIME','SHOOTOUT')", name="ck_nhl_decided_in"),
        CheckConstraint("final_winner IN ('HOME','AWAY')", name="ck_nhl_final_never_tied"),
        CheckConstraint("final_home_score <> final_away_score", name="ck_nhl_final_score_not_tied"),
        CheckConstraint(
            "(decided_in = 'REGULATION') = (regulation_result <> 'TIE')",
            name="ck_nhl_regulation_tie_iff_extra_time",
        ),
    )


class NhlImportReceipt(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One receipt per object the importer considered, whatever it decided."""

    __tablename__ = "nhl_import_receipts"

    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # FORECAST|RESULT|SCHEDULE
    #: IMPORTED | REVISED | DUPLICATE | REJECTED (forecast/result); IMPORTED | UPDATED | UNCHANGED | REJECTED (schedule)
    disposition: Mapped[str] = mapped_column(String(20), nullable=False)
    identity: Mapped[str | None] = mapped_column(String(120))
    object_sha256: Mapped[str | None] = mapped_column(String(64))
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    canonical_game_ref: Mapped[str | None] = mapped_column(String(40))
    game_bound: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    problems: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    source_label: Mapped[str | None] = mapped_column(String(300))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = [
    "NHL_GAME_SOURCE", "NHL_LEAGUE_CODE", "NHL_SPORT_CODE", "NHL_TEAM_SOURCE",
    "NhlGameResult", "NhlImportReceipt", "NhlResearchForecast",
]

"""The CFB production consumer's own evidence: discovery, finals, corrections.

WHY THREE TABLES AND NOT MORE COLUMNS ON THE FORECAST
-----------------------------------------------------
A forecast is immutable. Whether the producer still vouches for it is not: the
same package is CURRENT in one discovery tick and HISTORICAL in the next, and a
decision taken at 19:02 must be answerable from what AISP had verified at 19:02.
So each verified manifest is a row, each package it lists is a row beside it,
and "is this forecast decision-eligible now" is a query over those rows -- never
a flag written onto the forecast and later overwritten.

Finals are the same shape. CFBD's retained capture says ``completed=true`` and
carries no completion or revision clock, so the capture time is recorded as the
observation time and nothing more. A later capture that disagrees is a second
row, not an edit.

A SETTLEMENT CORRECTION IS AN ADJUSTMENT, NOT A REWRITE
-------------------------------------------------------
``position_results`` holds one row per position and is never updated. When a
final is corrected after a position settled, the original settlement stays as
it was and an adjustment row records the prior and corrected result, the profit
difference and the observation that caused it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.entities import CreatedAtMixin, UUIDPrimaryKeyMixin


class CfbDiscoveryManifest(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One content-addressed discovery manifest AISP verified, once."""

    __tablename__ = "cfb_discovery_manifests"

    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    producer_created_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    release_sha256: Mapped[str | None] = mapped_column(String(64))
    predecessor_sha256: Mapped[str | None] = mapped_column(String(64))
    #: The pinned handoff this manifest was accepted under.
    handoff_sha256: Mapped[str | None] = mapped_column(String(64))
    orientation_source: Mapped[str] = mapped_column(String(80), nullable=False)
    packages_listed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    packages_verified: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    packages_current_at_check: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dispositions: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    first_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (UniqueConstraint("manifest_sha256", name="uq_cfb_discovery_manifest_sha"),)


class CfbDiscoveryObservation(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """What one manifest said about one package, verified from bytes."""

    __tablename__ = "cfb_discovery_observations"

    manifest_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cfb_discovery_manifests.id"), nullable=False, index=True)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    forecast_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    cfb_research_forecast_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cfb_research_forecasts.id"))
    source_game_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    package_address: Mapped[str] = mapped_column(String(64), nullable=False)
    producer_status: Mapped[str | None] = mapped_column(String(60))
    currently_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    eligibility_valid_until_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: CURRENT / EXPIRED_LEASE / HISTORICAL, as read at AISP's first check.
    lease_at_check: Mapped[str] = mapped_column(String(24), nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    problems: Mapped[list[Any] | None] = mapped_column(JSON)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (UniqueConstraint("manifest_sha256", "forecast_id", name="uq_cfb_discovery_obs"),)


class CfbFinalObservation(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One retained CFBD completed-game row, bound to an AISP game."""

    __tablename__ = "cfb_final_observations"

    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), nullable=False, index=True)
    game_state_observation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("game_state_observations.id"), nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="CFBD")
    source_game_id: Mapped[str] = mapped_column(String(64), nullable=False)
    home_source_team_id: Mapped[str] = mapped_column(String(64), nullable=False)
    away_source_team_id: Mapped[str] = mapped_column(String(64), nullable=False)
    home_points: Mapped[int] = mapped_column(Integer, nullable=False)
    away_points: Mapped[int] = mapped_column(Integer, nullable=False)
    source_completed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    #: When the evidence was acquired. Never a completion or revision time.
    captured_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completion_clock_status: Mapped[str] = mapped_column(String(60), nullable=False)
    evidence_index_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_response_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_record_locator: Mapped[str | None] = mapped_column(String(40))
    raw_record_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    payload_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_row: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    __table_args__ = (UniqueConstraint("game_id", "payload_checksum", name="uq_cfb_final_obs"),)


class PositionSettlementAdjustment(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A correction to a settled position, beside -- never instead of -- the original."""

    __tablename__ = "position_settlement_adjustments"

    position_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("official_asp_positions.id"), nullable=False, index=True)
    original_settlement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("position_results.id"), nullable=False)
    game_state_observation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("game_state_observations.id"))
    previous_result: Mapped[str] = mapped_column(String(16), nullable=False)
    corrected_result: Mapped[str] = mapped_column(String(16), nullable=False)
    previous_profit_loss: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    corrected_profit_loss: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    profit_loss_delta: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    previous_home_score: Mapped[int | None] = mapped_column(Integer)
    previous_away_score: Mapped[int | None] = mapped_column(Integer)
    corrected_home_score: Mapped[int | None] = mapped_column(Integer)
    corrected_away_score: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    adjusted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closing_line_value: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (UniqueConstraint("position_id", "game_state_observation_id",
                                       name="uq_position_adjustment_observation"),)


__all__ = ["CfbDiscoveryManifest", "CfbDiscoveryObservation", "CfbFinalObservation",
           "PositionSettlementAdjustment"]

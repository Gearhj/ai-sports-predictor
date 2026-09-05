"""Prediction Market Observer ORM models (Polymarket + Kalshi).

Read-only research evidence, kept strictly separate from OddsSnapshot. Identity
binds to canonical games via ExternalIdMapping / PredictionMarketMapping. The
evidence tables (contracts, mappings, snapshots, orderbook, consensus, alerts)
are append-only and registered with the existing immutability guard.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Date
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class PredictionMarketEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only observation of a discovered provider event (game)."""

    __tablename__ = "prediction_market_events"
    provider: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    provider_event_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    sport: Mapped[str] = mapped_column(String(24), nullable=False)
    league: Mapped[str] = mapped_column(String(24), nullable=False)
    provider_home_team: Mapped[str | None] = mapped_column(String(160))
    provider_away_team: Mapped[str | None] = mapped_column(String(160))
    scheduled_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_game_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str | None] = mapped_column(String(32))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ingestion_runs.id"))
    __table_args__ = (
        UniqueConstraint("provider", "provider_event_id", "content_checksum", name="uq_pm_event_observation"),
        Index("ix_pm_event_provider_event", "provider", "provider_event_id"),
    )


class PredictionMarketContract(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only, versioned canonical contract identity + semantics."""

    __tablename__ = "prediction_market_contracts"
    provider: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    provider_event_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    provider_market_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    provider_outcome_id: Mapped[str | None] = mapped_column(String(255))
    sport: Mapped[str] = mapped_column(String(24), nullable=False)
    league: Mapped[str] = mapped_column(String(24), nullable=False)
    market_type: Mapped[str] = mapped_column(String(32), nullable=False)
    selection: Mapped[str] = mapped_column(String(16), nullable=False)
    line_value: Mapped[float | None] = mapped_column(Float)
    outcome_label: Mapped[str] = mapped_column(String(200), nullable=False)
    provider_home_team: Mapped[str | None] = mapped_column(String(160))
    provider_away_team: Mapped[str | None] = mapped_column(String(160))
    scheduled_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_game_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    market_question: Mapped[str | None] = mapped_column(Text)
    market_subtitle: Mapped[str | None] = mapped_column(Text)
    resolution_rules: Mapped[str | None] = mapped_column(Text)
    rules_sha256: Mapped[str | None] = mapped_column(String(64))
    market_close_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settlement_source: Mapped[str | None] = mapped_column(String(255))
    market_status: Mapped[str | None] = mapped_column(String(32))
    line_source: Mapped[str] = mapped_column(String(24), nullable=False, default="STRUCTURED")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(24), nullable=False, default="1.0.0")
    ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ingestion_runs.id"))
    __table_args__ = (
        UniqueConstraint("provider", "provider_market_id", "provider_outcome_id", "content_checksum", name="uq_pm_contract_version"),
        Index("ix_pm_contract_event", "provider", "provider_event_id"),
    )


class PredictionMarketMapping(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only game-identity + semantics decision. Latest decided_at wins.

    A reversal is a NEW decision row, never a deletion or mutation.
    """

    __tablename__ = "prediction_market_mappings"
    provider: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    provider_event_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    canonical_game_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("games.id"), index=True)
    mapping_status: Mapped[str] = mapped_column(String(24), nullable=False)
    semantics_status: Mapped[str] = mapped_column(String(32), nullable=False, default="SEMANTICS_UNKNOWN")
    decision_source: Mapped[str] = mapped_column(String(16), nullable=False, default="AUTO")
    reason: Mapped[str | None] = mapped_column(Text)
    start_time_delta_seconds: Mapped[int | None] = mapped_column(Integer)
    candidate_game_ids: Mapped[list[Any] | None] = mapped_column(JSON)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("prediction_market_mappings.id"))
    __table_args__ = (
        Index("ix_pm_mapping_provider_event_decided", "provider", "provider_event_id", "decided_at"),
    )


class PredictionMarketSnapshot(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only point-in-time price/quality snapshot for one contract."""

    __tablename__ = "prediction_market_snapshots"
    provider: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    contract_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("prediction_market_contracts.id"), index=True, nullable=False)
    provider_market_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    provider_outcome_id: Mapped[str | None] = mapped_column(String(255))
    canonical_game_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("games.id"), index=True)
    market_type: Mapped[str] = mapped_column(String(32), nullable=False)
    selection: Mapped[str] = mapped_column(String(16), nullable=False)
    line_value: Mapped[float | None] = mapped_column(Float)
    best_bid: Mapped[float | None] = mapped_column(Float)
    best_ask: Mapped[float | None] = mapped_column(Float)
    midpoint: Mapped[float | None] = mapped_column(Float)
    microprice: Mapped[float | None] = mapped_column(Float)
    last_trade: Mapped[float | None] = mapped_column(Float)
    bid_ask_spread: Mapped[float | None] = mapped_column(Float)
    top_bid_size: Mapped[float | None] = mapped_column(Float)
    top_ask_size: Mapped[float | None] = mapped_column(Float)
    depth_within_1_percent: Mapped[float | None] = mapped_column(Float)
    depth_within_2_percent: Mapped[float | None] = mapped_column(Float)
    depth_within_5_percent: Mapped[float | None] = mapped_column(Float)
    order_book_imbalance: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    open_interest: Mapped[float | None] = mapped_column(Float)
    liquidity: Mapped[float | None] = mapped_column(Float)
    provider_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    aisp_received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    game_starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    seconds_to_game: Mapped[int | None] = mapped_column(Integer)
    pregame_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    in_play: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    provider_sequence: Mapped[int | None] = mapped_column(BigInteger)
    timeline_cut: Mapped[str | None] = mapped_column(String(32))
    quality_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    quality_components: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    quality_flags: Mapped[list[Any] | None] = mapped_column(JSON)
    mapping_status: Mapped[str] = mapped_column(String(24), nullable=False)
    semantics_status: Mapped[str] = mapped_column(String(32), nullable=False)
    book_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    quote_fresh: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    market_liquid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_status: Mapped[str | None] = mapped_column(String(32))
    raw_payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(24), nullable=False, default="1.0.0")
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_pm_snapshot_idempotency"),
        Index("ix_pm_snapshot_game_observed", "canonical_game_id", "provider_observed_at"),
        Index("ix_pm_snapshot_contract_observed", "contract_id", "provider_observed_at"),
    )


class PredictionMarketOrderbookSnapshot(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only, bounded order-book frame (material-change or heartbeat)."""

    __tablename__ = "prediction_market_orderbook_snapshots"
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("prediction_market_snapshots.id"), index=True)
    contract_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("prediction_market_contracts.id"), index=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(24), nullable=False)
    bids: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    asks: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    raw_yes_bids: Mapped[list[Any] | None] = mapped_column(JSON)
    raw_no_bids: Mapped[list[Any] | None] = mapped_column(JSON)
    depth_within_1_percent: Mapped[float | None] = mapped_column(Float)
    depth_within_2_percent: Mapped[float | None] = mapped_column(Float)
    depth_within_5_percent: Mapped[float | None] = mapped_column(Float)
    provider_sequence: Mapped[int | None] = mapped_column(BigInteger)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_pm_orderbook_idempotency"),
    )


class PredictionMarketConsensusSnapshot(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only display-only cross-provider consensus (research only)."""

    __tablename__ = "prediction_market_consensus_snapshots"
    canonical_game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), index=True, nullable=False)
    market_type: Mapped[str] = mapped_column(String(32), nullable=False)
    selection: Mapped[str] = mapped_column(String(16), nullable=False)
    line_value: Mapped[float | None] = mapped_column(Float)
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason: Mapped[str | None] = mapped_column(Text)
    contributing_providers: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    provider_prices: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    median_probability: Mapped[float | None] = mapped_column(Float)
    quality_weighted_probability: Mapped[float | None] = mapped_column(Float)
    dispersion: Mapped[float | None] = mapped_column(Float)
    consensus_version: Mapped[str] = mapped_column(String(48), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        Index("ix_pm_consensus_game_computed", "canonical_game_id", "computed_at"),
    )


class PredictionMarketAlert(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only research-only observed market signal. Never executable."""

    __tablename__ = "prediction_market_alerts"
    alert_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    canonical_game_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("games.id"), index=True)
    provider: Mapped[str | None] = mapped_column(String(24))
    market_type: Mapped[str | None] = mapped_column(String(32))
    selection: Mapped[str | None] = mapped_column(String(16))
    magnitude: Mapped[float | None] = mapped_column(Float)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    threshold_version: Mapped[str] = mapped_column(String(48), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_pm_alert_idempotency"),
        Index("ix_pm_alert_game_type", "canonical_game_id", "alert_type"),
    )


class PredictionMarketSemanticTemplate(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Versioned provider-market-template semantic authority (append-only).

    One row per (provider, template_key, version). A human decision appends a
    new version; individual markets inherit SEMANTICS_MATCHED only from an
    APPROVED template whose every condition they satisfy."""

    __tablename__ = "prediction_market_semantic_templates"
    provider: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    template_key: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    sport: Mapped[str] = mapped_column(String(24), nullable=False)
    league: Mapped[str] = mapped_column(String(24), nullable=False)
    market_type: Mapped[str] = mapped_column(String(32), nullable=False)
    conditions: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    effective_date: Mapped[Any] = mapped_column(Date, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    decision_source: Mapped[str] = mapped_column(String(16), nullable=False, default="SEED")
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("prediction_market_semantic_templates.id"))
    __table_args__ = (UniqueConstraint("provider", "template_key", "version", name="uq_pm_semantic_template_version"),)


__all__ = [
    "PredictionMarketSemanticTemplate",
    "PredictionMarketEvent", "PredictionMarketContract", "PredictionMarketMapping",
    "PredictionMarketSnapshot", "PredictionMarketOrderbookSnapshot",
    "PredictionMarketConsensusSnapshot", "PredictionMarketAlert",
]

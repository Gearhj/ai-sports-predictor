"""AISP DAY-BEFORE SHOW PREVIEW V1: persistence.

Two horizons are kept apart by construction, not by labelling:

``SHOW_PREVIEW``
    Day-before editorial research for the 5:00 PM ET recording. Nothing in
    these tables is a wager, a governed decision, a position or final-pregame
    evidence, and no row here is read by the decision, staking, bankroll or
    settlement code.

``FINAL_PREGAME``
    The existing governed workflow (``shadow_experiment_decisions`` and
    ``official_asp_positions``). Untouched by this module.

Tables
------
``show_preview_forecasts``
    Preview forecasts imported under the consumer contract. The horizon column
    is constrained to ``SHOW_PREVIEW``; an ordinary final forecast cannot be
    written here, and nothing copies one in.
``show_board_drafts``
    The latest built draft per slate date. Replaceable by design: a draft is a
    working view, not evidence.
``show_preview_snapshots``
    Frozen Show Boards. Append-only and content-addressed: the snapshot id is
    the SHA-256 of the canonical payload, so any change to the evidence produces
    a different snapshot. A superseded snapshot is never edited; its successor
    points back to it.
``show_preview_game_bindings`` / ``show_preview_market_bindings`` /
``show_preview_disclosures``
    The per-game forecast binding, rank and comparison; each complete sportsbook
    pair bound into the snapshot; and every missing-evidence disclosure. All
    derived from the frozen payload in the same transaction and append-only.
``show_preview_final_comparisons``
    Preview-versus-final pairings recorded once later evidence exists. Neutral
    information; a change is not an error.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Index, Integer, String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.entities import CreatedAtMixin, UUIDPrimaryKeyMixin

SHOW_PREVIEW = "SHOW_PREVIEW"


class ShowPreviewForecast(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A preview-horizon forecast imported under AISP_SHOW_PREVIEW_FORECAST_V1."""

    __tablename__ = "show_preview_forecasts"
    __table_args__ = (
        UniqueConstraint("forecast_id", name="uq_show_preview_forecast_id"),
        CheckConstraint("horizon = 'SHOW_PREVIEW'", name="ck_show_preview_forecast_horizon"),
        CheckConstraint("NOT grants_wagering_authority",
                        name="ck_show_preview_forecast_no_wagering"),
        Index("ix_show_preview_forecasts_game", "canonical_game_id", "created_utc"),
        Index("ix_show_preview_forecasts_slate", "slate_date", "sport"),
    )

    forecast_id: Mapped[str] = mapped_column(String(200), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    horizon: Mapped[str] = mapped_column(String(24), nullable=False, default=SHOW_PREVIEW)
    sport: Mapped[str] = mapped_column(String(16), nullable=False)
    league: Mapped[str] = mapped_column(String(16), nullable=False)
    season: Mapped[str] = mapped_column(String(16), nullable=False)
    slate_date: Mapped[date] = mapped_column(Date, nullable=False)
    canonical_game_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("games.id"))
    source_game_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    away_abbreviation: Mapped[str] = mapped_column(String(16), nullable=False)
    home_abbreviation: Mapped[str] = mapped_column(String(16), nullable=False)
    kickoff_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_cutoff_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    model_id: Mapped[str] = mapped_column(String(120), nullable=False)
    model_version: Mapped[str] = mapped_column(String(160), nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_away_score: Mapped[float] = mapped_column(Float, nullable=False)
    expected_home_score: Mapped[float] = mapped_column(Float, nullable=False)
    away_win_probability: Mapped[float] = mapped_column(Float, nullable=False)
    home_win_probability: Mapped[float] = mapped_column(Float, nullable=False)
    tie_probability: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    lineup_status: Mapped[str] = mapped_column(String(24), nullable=False)
    starter_status: Mapped[str] = mapped_column(String(24), nullable=False)
    grants_wagering_authority: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    imported_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ShowBoardDraft(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """The latest built draft for a slate date. Working state, not evidence."""

    __tablename__ = "show_board_drafts"
    __table_args__ = (UniqueConstraint("slate_date", name="uq_show_board_draft_slate"),)

    slate_date: Mapped[date] = mapped_column(Date, nullable=False)
    built_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence_as_of_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class ShowPreviewSnapshot(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A frozen, content-addressed Show Board. Append-only."""

    __tablename__ = "show_preview_snapshots"
    __table_args__ = (
        UniqueConstraint("snapshot_sha256", name="uq_show_preview_snapshot_sha"),
        Index("ix_show_preview_snapshots_slate", "slate_date", "frozen_utc"),
    )

    snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    slate_date: Mapped[date] = mapped_column(Date, nullable=False)
    frozen_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    planned_freeze_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence_as_of_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    predecessor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("show_preview_snapshots.id"))
    supersede_reason: Mapped[str | None] = mapped_column(String(400))
    trigger: Mapped[str] = mapped_column(String(40), nullable=False)


class ShowPreviewGameBinding(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "show_preview_game_bindings"
    __table_args__ = (UniqueConstraint("snapshot_id", "game_key", name="uq_show_preview_game_binding"),)

    snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("show_preview_snapshots.id"), nullable=False)
    game_key: Mapped[str] = mapped_column(String(120), nullable=False)
    canonical_game_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("games.id"))
    sport: Mapped[str] = mapped_column(String(16), nullable=False)
    kickoff_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    readiness: Mapped[str] = mapped_column(String(40), nullable=False)
    forecast_row_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("show_preview_forecasts.id"))
    forecast_content_sha256: Mapped[str | None] = mapped_column(String(64))
    preview_rank: Mapped[int | None] = mapped_column(Integer)
    preview_selection: Mapped[str | None] = mapped_column(String(16))
    model_probability: Mapped[float | None] = mapped_column(Float)
    no_vig_probability: Mapped[float | None] = mapped_column(Float)
    preview_difference: Mapped[float | None] = mapped_column(Float)
    expected_return: Mapped[float | None] = mapped_column(Float)


class ShowPreviewMarketBinding(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "show_preview_market_bindings"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "game_key", "market_type", "sportsbook_key", "provider", "line_key",
                         name="uq_show_preview_market_binding"),
    )

    snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("show_preview_snapshots.id"), nullable=False)
    game_key: Mapped[str] = mapped_column(String(120), nullable=False)
    market_type: Mapped[str] = mapped_column(String(24), nullable=False)
    line_key: Mapped[str] = mapped_column(String(24), nullable=False)
    sportsbook_key: Mapped[str] = mapped_column(String(80), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    classification: Mapped[str] = mapped_column(String(24), nullable=False)
    complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False)
    older_leg_observed_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    legs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    settlement: Mapped[str] = mapped_column(String(80), nullable=False)


class ShowPreviewDisclosure(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "show_preview_disclosures"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("show_preview_snapshots.id"), nullable=False)
    game_key: Mapped[str | None] = mapped_column(String(120))
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    message: Mapped[str] = mapped_column(String(400), nullable=False)


class ShowPreviewFinalComparison(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Preview versus final for one game, recorded once final evidence exists."""

    __tablename__ = "show_preview_final_comparisons"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "game_key", "comparison_sha256", name="uq_show_preview_final_comparison"),
    )

    snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("show_preview_snapshots.id"), nullable=False)
    game_key: Mapped[str] = mapped_column(String(120), nullable=False)
    final_decision_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("shadow_experiment_decisions.id"))
    position_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("official_asp_positions.id"))
    comparison_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    compared_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


APPEND_ONLY_SHOW_PREVIEW_TYPES = (
    ShowPreviewForecast, ShowPreviewSnapshot, ShowPreviewGameBinding, ShowPreviewMarketBinding,
    ShowPreviewDisclosure, ShowPreviewFinalComparison,
)

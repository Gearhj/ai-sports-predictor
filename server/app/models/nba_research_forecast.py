"""An imported NBA research forecast that no simulator produced.

WHY NOT ``shadow_forecasts``
----------------------------
That table is shaped for simulation-backed forecasts: ``simulator_id``,
``simulator_version`` and ``simulator_seed`` are all NOT NULL, because every
forecast it was built for came out of a Monte Carlo run. This package did not.
It carries ``drawCount = 0`` and every distribution null, and its handoff is
explicit that no simulation count may be attached to it.

Filling those columns with placeholders would turn "there was no simulator" into
"there was a simulator, seeded zero" -- which is the same class of mistake as
turning an unavailable distribution into a flat one. So this forecast gets a
table whose shape matches what it actually is: two labelled point-and-probability
outputs, no draws, no intervals.

TWO MODELS, NOT ONE
-------------------
Ridge expected points and an Elo win probability are separate outputs of
separate models. They are stored in separate columns with separate names and
the card labels them separately, because a reader who assumes the probability
came from the points will read a joint simulation that was never run -- and the
two are allowed to favour different sides.

FOUR CLOCKS
-----------
Kept in four columns. The producer's own ``publishedAtUtc`` is null and is
stored null: it describes the producer's local state. The exchange delivery time
comes from the delivery receipt and never overwrites it.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
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

from app.models.entities import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class NbaResearchForecast(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One imported NBA research forecast, immutable and keyed by its own id."""

    __tablename__ = "nba_research_forecasts"

    #: The producer's own forecast identity, and the digest of the exact bytes.
    #: Conflicting bytes under the same id are rejected at import, never merged.
    forecast_id: Mapped[str] = mapped_column(String(200), nullable=False)
    forecast_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    forecast_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    forecast_schema: Mapped[str] = mapped_column(String(80), nullable=False)

    #: The delivery this arrived in.
    package_index_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    package_index_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    envelope_schema: Mapped[str] = mapped_column(String(80), nullable=False)

    engine_id: Mapped[str] = mapped_column(String(64), nullable=False)
    configuration_identity: Mapped[str | None] = mapped_column(String(160))
    point_configuration_identity: Mapped[str | None] = mapped_column(String(160))

    #: The AISP game, reconciled by source id. Never by team name and date.
    canonical_game_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("games.id"), nullable=False)
    source_game_id: Mapped[str] = mapped_column(String(64), nullable=False)
    home_franchise_id: Mapped[str | None] = mapped_column(String(120))
    away_franchise_id: Mapped[str | None] = mapped_column(String(120))
    schedule_source_revision: Mapped[str | None] = mapped_column(Text)

    #: The schedule as the FORECAST captured it. A later authoritative revision
    #: is recorded beside this, never over it.
    captured_tipoff_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False)
    captured_game_date: Mapped[date] = mapped_column(Date, nullable=False)

    # -- Ridge: expected points -------------------------------------------
    ridge_expected_points_home: Mapped[float | None] = mapped_column(Float)
    ridge_expected_points_away: Mapped[float | None] = mapped_column(Float)
    ridge_expected_total: Mapped[float | None] = mapped_column(Float)
    ridge_home_margin: Mapped[float | None] = mapped_column(Float)

    # -- Elo: win probability, a SEPARATE model ---------------------------
    #: Fractions 0-1 exactly as delivered. The single multiplication by 100
    #: happens at display, so a percentage can never be computed twice.
    elo_win_probability_home: Mapped[float | None] = mapped_column(Float)
    elo_win_probability_away: Mapped[float | None] = mapped_column(Float)
    elo_tie_probability: Mapped[float | None] = mapped_column(Float)
    elo_method: Mapped[str | None] = mapped_column(Text)

    #: Zero here, and the reason the distribution columns do not exist.
    draw_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    distribution_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="UNAVAILABLE")

    # -- four clocks, kept apart ------------------------------------------
    source_cutoff_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    forecast_created_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    local_export_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Null in the artifact and null here. It describes the producer's local
    #: state and is never filled in with the delivery time.
    producer_published_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exchange_delivered_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    #: A later forecast names the one it replaces; both are retained.
    supersedes_forecast_id: Mapped[str | None] = mapped_column(String(200))
    superseded_by_forecast_id: Mapped[str | None] = mapped_column(String(200))

    #: The collapsed-details payload, as delivered: rest support, limitations,
    #: bindings, feature audit, authority.
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("forecast_id", name="uq_nba_research_forecast_id"),
        CheckConstraint("draw_count >= 0", name="ck_nba_research_draw_count"),
        CheckConstraint(
            "elo_win_probability_home IS NULL OR "
            "(elo_win_probability_home >= 0 AND elo_win_probability_home <= 1)",
            name="ck_nba_research_home_probability_is_a_fraction",
        ),
        CheckConstraint(
            "elo_win_probability_away IS NULL OR "
            "(elo_win_probability_away >= 0 AND elo_win_probability_away <= 1)",
            name="ck_nba_research_away_probability_is_a_fraction",
        ),
        Index("ix_nba_research_forecast_game", "canonical_game_id"),
        Index("ix_nba_research_forecast_source_game", "source_game_id"),
    )


__all__ = ["NbaResearchForecast"]

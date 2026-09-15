"""An imported College Football Gen 1 research forecast.

WHY ITS OWN TABLE
-----------------
Neither NBA table fits. ``nba_research_forecasts`` exists *because* its package
had ``drawCount = 0`` and no distributions; this one carries ten thousand paired
score draws, four complete PMFs and twelve retained intervals.
``nba_joint_comparison_arms`` requires ``supplements_forecast_id`` -- it is an
arm of something else, and this forecast supplements nothing.

WHY NO FOREIGN KEY TO ``games``
-------------------------------
AISP has three sports registered and College Football is not one of them. There
is no admitted CFB schedule source, so there is no canonical game row to point
at -- and inventing one would put a sport into the scheduler, scoreboard and
odds feeds that have no adapter for it. The producer's own identifiers are
retained instead: the CFBD game id and both CFBD team ids, which is what a later
reconciliation would join on. Team NAMES are stored for display only; nothing
matches on them.

THREE PROBABILITIES, THREE COLUMNS
----------------------------------
Ridge, Elo and the joint residual simulation are separately estimated outputs of
separate methods, and the producer says so in its own limitations. They are
allowed to disagree -- here they range from 66.93% to 76.92% on the same side --
and a reader who sees one number will assume a precision that does not exist. So
they are stored apart, named apart, and rendered apart. Nothing averages them.

FRACTIONS IN, PERCENTAGES AT THE EDGE
-------------------------------------
Every probability is stored as the fraction the artifact delivered. The single
multiplication by 100 happens in the view, so a percentage cannot be computed
twice.

FOUR CLOCKS
-----------
Source cutoff, forecast creation and exchange publication come from the package
and are never overwritten. The consumer's own import time is a fourth column,
because when AISP learned something is not when the producer knew it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.entities import CreatedAtMixin, UUIDPrimaryKeyMixin

#: The one canonical identifier for this sport inside AISP. CFB and NCAAF are
#: not separate sports; anything that needs a code uses this constant so an
#: alias cannot quietly become a second registry entry.
CFB_SPORT_CODE = "CFB"
CFB_SPORT_LABEL = "College Football"
CFB_RESEARCH_LABEL = "College Football · Research"

_FRACTION_COLUMNS = (
    "ridge_home_win_probability",
    "ridge_away_win_probability",
    "elo_home_win_probability",
    "elo_away_win_probability",
    "joint_home_win_probability",
    "joint_away_win_probability",
    "tie_probability",
)


class CfbResearchForecast(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One imported CFB research forecast, immutable and keyed by its identity."""

    __tablename__ = "cfb_research_forecasts"

    # -- identity and the exact bytes it arrived as ------------------------
    forecast_id: Mapped[str] = mapped_column(String(200), nullable=False)
    forecast_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    forecast_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    forecast_schema: Mapped[str] = mapped_column(String(80), nullable=False)
    package_index_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    package_index_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    publication_receipt_sha256: Mapped[str | None] = mapped_column(String(64))
    #: Retained by digest, not by value: the draws stay in the exchange.
    joint_draws_sha256: Mapped[str | None] = mapped_column(String(64))
    pmf_sha256: Mapped[str | None] = mapped_column(String(64))

    baseline_configuration: Mapped[str] = mapped_column(String(160), nullable=False)

    # -- the producer's own game identity ----------------------------------
    source_provider: Mapped[str] = mapped_column(
        String(40), nullable=False, default="CFBD")
    source_game_id: Mapped[str] = mapped_column(String(64), nullable=False)
    home_source_team_id: Mapped[str] = mapped_column(String(64), nullable=False)
    away_source_team_id: Mapped[str] = mapped_column(String(64), nullable=False)
    home_team_name: Mapped[str] = mapped_column(String(120), nullable=False)
    away_team_name: Mapped[str] = mapped_column(String(120), nullable=False)
    neutral_site: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    conference_competition: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False)
    captured_kickoff_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False)
    schedule_revision_captured_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))

    # -- expected scores ----------------------------------------------------
    expected_away_points: Mapped[float] = mapped_column(Float, nullable=False)
    expected_home_points: Mapped[float] = mapped_column(Float, nullable=False)
    expected_total: Mapped[float] = mapped_column(Float, nullable=False)
    #: home minus away, as the producer defines it.
    expected_home_margin: Mapped[float] = mapped_column(Float, nullable=False)

    # -- three separately estimated win probabilities -----------------------
    ridge_home_win_probability: Mapped[float] = mapped_column(Float, nullable=False)
    ridge_away_win_probability: Mapped[float] = mapped_column(Float, nullable=False)
    elo_home_win_probability: Mapped[float] = mapped_column(Float, nullable=False)
    elo_away_win_probability: Mapped[float] = mapped_column(Float, nullable=False)
    joint_home_win_probability: Mapped[float] = mapped_column(Float, nullable=False)
    joint_away_win_probability: Mapped[float] = mapped_column(Float, nullable=False)
    #: Zero in this artifact, stored rather than assumed.
    tie_probability: Mapped[float] = mapped_column(Float, nullable=False)

    draw_count: Mapped[int] = mapped_column(Integer, nullable=False)

    # -- retained evidence, as delivered ------------------------------------
    #: Four complete PMFs: away_score, home_score, total, home_margin. Each sums
    #: to draw_count. Stored whole so nothing downstream has to convolve
    #: marginals or truncate a tail to draw a chart.
    pmfs: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    intervals: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    illustrative_totals: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    simulation_mcse: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    transformation_accounting: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    feature_values: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    limitations: Mapped[list[Any] | None] = mapped_column(JSON)
    challenger: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    model_bindings: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    source_bindings: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    #: Provider quota measurements, kept as two separate numbers.
    source_usage: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    # -- four clocks --------------------------------------------------------
    source_cutoff_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False)
    forecast_created_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False)
    exchange_published_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
    #: When AISP learned it, which is not when the producer knew it.
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False)

    #: False would be refused at import, not narrowed.
    research_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = tuple(
        [
            UniqueConstraint("forecast_id", name="uq_cfb_research_forecast_id"),
            CheckConstraint("draw_count >= 0", name="ck_cfb_research_draw_count"),
            CheckConstraint("research_only", name="ck_cfb_research_only"),
        ]
        + [
            CheckConstraint(
                f"{column} >= 0 AND {column} <= 1",
                name=f"ck_cfb_research_{column}_fraction",
            )
            for column in _FRACTION_COLUMNS
        ]
    )


__all__ = [
    "CFB_RESEARCH_LABEL",
    "CFB_SPORT_CODE",
    "CFB_SPORT_LABEL",
    "CfbResearchForecast",
]

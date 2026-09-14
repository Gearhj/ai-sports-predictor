"""Two research arms of one NBA joint simulation, stored side by side.

WHY A SEPARATE TABLE
--------------------
`nba_research_forecasts` holds the point/Elo forecast, whose whole character is
that it has NO joint distribution: drawCount zero, intervals null, Over/Under
unavailable. These arms are the opposite -- ten thousand retained draws with
PMFs and intervals -- and the handoff is explicit that importing them must not
change the point/Elo forecast. Widening that table to hold both would put a
forecast that has distributions and one that declares it has none into the same
columns, which is exactly how the second gets read as the first.

WHAT THE ARMS ARE NOT
---------------------
A market. The package's own `illustrativeTotals` carry
`lineClassification: ILLUSTRATIVE_NOT_RECORDED_MARKET`, and that classification
travels with the row so nothing downstream can mistake an illustrative line for
a price AISP was offered.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class NbaJointComparisonArm(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One arm of the NBA Gen-1 joint comparison."""

    __tablename__ = "nba_joint_comparison_arms"

    package_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    forecast_id: Mapped[str] = mapped_column(String(200), nullable=False)
    forecast_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    arm: Mapped[str] = mapped_column(String(40), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    configuration_identity: Mapped[str] = mapped_column(String(120), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)

    canonical_game_id: Mapped[Any] = mapped_column(
        ForeignKey("games.id"), nullable=False)

    #: The point/Elo forecast these SUPPLEMENT. Never replaced by them.
    supplements_forecast_id: Mapped[str] = mapped_column(String(200), nullable=False)
    supplements_forecast_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    draw_count: Mapped[int] = mapped_column(Integer, nullable=False)
    away_win_probability: Mapped[float | None] = mapped_column(Float)
    home_win_probability: Mapped[float | None] = mapped_column(Float)
    tie_probability: Mapped[float | None] = mapped_column(Float)
    expected_away: Mapped[float | None] = mapped_column(Float)
    expected_home: Mapped[float | None] = mapped_column(Float)
    expected_total: Mapped[float | None] = mapped_column(Float)

    #: The full simulation, intervals, PMFs and illustrative totals as supplied.
    joint_simulation: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    ridge_point_expectations: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    separate_elo_probabilities: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    limitations: Mapped[Any | None] = mapped_column(JSON)
    authority: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    population: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    source_cutoff_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    research_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        # Idempotency: the same forecast id cannot be imported twice, and a
        # re-run of the importer finds this row rather than adding a second.
        UniqueConstraint("forecast_id", name="uq_nba_joint_arm_forecast"),
    )

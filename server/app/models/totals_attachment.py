"""Consumer acceptance of a published V18 totals-distribution index.

WHAT THIS ROW IS, AND WHAT IT IS NOT
------------------------------------
It is **not** a distribution. The index does not ship a new PMF; it indexes the
one the ordinary daily import already wrote to
``ShadowForecast.distributions.totalRunDistribution``. Copying those numbers
into a second table would create a second source of truth that could drift from
the forecast it claims to describe.

What this row records is an **acceptance event**: at a named consumer clock, one
forecast identity was bound to one index row, every identity field agreed, and
the stored PMF was compared value-for-value against the indexed bytes and found
identical. That is the fact a reader needs later, and it cannot be re-derived
from the forecast alone.

Because it is an acceptance and not evidence about a game, the row is keyed on
``(index_sha256, forecast_identity)``. Re-running an attachment finds the
existing row instead of writing a second, and attaching a *different* index
records its own separate acceptance rather than overwriting the first.

WHY VERIFICATION STATUS IS ADVISORY, NOT A GATE
-----------------------------------------------
A forecast published after the index's inventory freeze is perfectly usable --
it is the same engine, the same field, the same daily importer. Blocking it
because a snapshot taken earlier did not mention it would hide a current
forecast behind an expired inventory. So the board prices from the stored PMF
either way and reports whether this index verified it; three of September 13's
four snapshot-missing games published within the hour and must not be withheld.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
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
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class TotalsIndexAttachment(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One accepted (index, forecast version) binding."""

    __tablename__ = "totals_index_attachments"

    #: Physical SHA-256 of the index file as delivered, no normalization.
    index_sha256: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    index_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    #: The producer's own derivative publication clock, kept distinct from ours.
    derivative_published_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
    #: When AISP bound and verified it. Never backdated to the producer's clock.
    attached_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False)

    #: The exact ordinary baseline this row binds, by its own identity.
    forecast_identity: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    baseline_object_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    engine_id: Mapped[str] = mapped_column(String(32), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(64), nullable=False)
    simulator_id: Mapped[str | None] = mapped_column(String(64))
    simulator_seed: Mapped[int | None] = mapped_column(Integer)

    canonical_game_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("games.id"), index=True)
    #: Bound by BOTH canonical id and MLBAM gamePk, never by teams and date.
    mlbam_game_pk: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    game_date: Mapped[str] = mapped_column(String(16), nullable=False)

    #: The denominator every probability in the PMF is over.
    original_simulation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    valid_original_draws_used: Mapped[int] = mapped_column(Integer, nullable=False)
    excluded_draw_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    extra_innings_included: Mapped[bool] = mapped_column(Boolean, nullable=False)
    tail_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    score_limited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    #: True only when the stored PMF equalled the indexed bytes value-for-value.
    pmf_matches_stored: Mapped[bool] = mapped_column(Boolean, nullable=False)
    pmf_bin_count: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Full-precision mean recomputed from the stored PMF at attachment.
    recomputed_mean_total_runs: Mapped[float] = mapped_column(Float, nullable=False)
    #: The producer's declared mean, for the record. Equal, or this row is not
    #: written at all -- a disagreement is a rejection, not a footnote.
    declared_mean_total_runs: Mapped[float | None] = mapped_column(Float)

    #: Everything checked, so a later reader can see what "verified" covered.
    verification_detail: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict)
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("index_sha256", "forecast_identity",
                         name="uq_totals_attachment_index_forecast"),
        Index("ix_totals_attachment_game", "canonical_game_id", "index_sha256"),
    )


__all__ = ["TotalsIndexAttachment"]

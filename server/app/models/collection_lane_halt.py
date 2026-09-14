"""A latched stop on one collection lane, with the evidence that caused it.

WHY A TABLE AND NOT A FLAG
--------------------------
A halt has to survive a restart, and it has to carry its own reason. The
condition it exists for -- the provider returning a game other than the one the
request selected -- is exactly the kind of fault that looks like nothing at all
in aggregate: objects are charged, rows are written, the receipt says
``FALLBACK_COLLECTED``. Only the mismatch itself distinguishes it, so the
mismatch is what gets stored, verbatim, next to the halt it produced.

A halt is never cleared automatically. Lifting one is a decision about whether
the provider can be trusted to answer the question that was asked, and nothing
in this process is in a position to make it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Index, JSON, String, Text
from sqlalchemy import text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.entities import CreatedAtMixin, UUIDPrimaryKeyMixin


class CollectionLaneHalt(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One latched stop. Active rows block the lane; lifted rows are history."""

    __tablename__ = "collection_lane_halts"

    lane: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(80), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    halted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: The receipt and the mismatch, kept whole. This is the evidence, not a
    #: summary of it: a later reader must be able to see what was asked for and
    #: what came back without re-deriving either.
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    lifted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lifted_by: Mapped[str | None] = mapped_column(String(120))
    lift_authority: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        # At most one ACTIVE halt per lane, enforced by the database rather than
        # by whoever happens to be writing. Partial on purpose: a lane may
        # accumulate any number of lifted halts, and that history is the point.
        Index("uq_collection_lane_halt_active", "lane",
              unique=True, postgresql_where=text("active")),
    )


__all__ = ["CollectionLaneHalt"]

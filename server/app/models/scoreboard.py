"""Observed game state for the scoreboard: score, period, and what is missing.

WHY THIS IS NOT ``game_state_observations``
-------------------------------------------
That table is load-bearing evidence. The research scorer reads its FINAL rows,
the label gate reads its status, and forecast eligibility is decided against its
``available_as_of``. It is deliberately narrow, and widening it with display
fields would put a two-minute presentation refresh inside the same append-only
stream that decides which forecasts are scoreable. A live poll that writes a row
every two minutes would also multiply that stream by a factor of fifteen.

So this is a separate append-only table with one job: what a reader sees. It
creates no decision, settles nothing, and is never read as a final.

THREE TIMES, KEPT APART
-----------------------
``source_observed_at``   when the SOURCE says the state was true. Both current
                         sources supply nothing here, so it is NULL, and a NULL
                         is what makes the UI say "last checked" instead of
                         "score as of" -- it must never be filled with a
                         retrieval time.
``retrieved_at``         when AISP actually received these bytes.
``checked_at``           the most recent successful check that CONFIRMED this
                         state, whether or not anything changed.

The third exists because of the failure the brief names directly: an unchanged
payload must not look like a stale one, and a stale one must not look fresh. A
row is written once per distinct state (the checksum makes that so) and its
``checked_at`` advances in place while the state holds. That is the only mutable
field here, and it records an observation about freshness, not about the game.

MISSING IS NOT ZERO
-------------------
``periods`` carries one entry per period the source actually reported. An
unplayed period is absent, a scoreless period is present with 0, and a period
the source did not report is absent with ``periods_complete`` false. Nothing is
divided out of a final, and no gap is filled.
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
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base, CreatedAtMixin, UUIDPrimaryKeyMixin

#: Canonical status vocabulary. Mapped from each source's own words, which are
#: preserved alongside in ``status_detail`` -- "In Progress", "Warmup" and
#: "Manager challenge" are all IN_PROGRESS, and losing the distinction would
#: make a delay indistinguishable from ordinary play.
SCOREBOARD_STATUSES = (
    "SCHEDULED",
    "IN_PROGRESS",
    "DELAYED",
    "SUSPENDED",
    "POSTPONED",
    "CANCELLED",
    "FINAL",
    "UNKNOWN",
)


class GameScoreboardObservation(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One observed scoreboard state for one game. Append-only per state."""

    __tablename__ = "game_scoreboard_observations"

    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), nullable=False)
    league_code: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    source_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="LIVE")
    source_game_id: Mapped[str | None] = mapped_column(String(120))

    status: Mapped[str] = mapped_column(String(24), nullable=False)
    #: The source's own status words, never normalised away.
    status_detail: Mapped[str | None] = mapped_column(String(120))

    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)

    #: Which period play is in (inning / quarter). NULL before first pitch or
    #: kickoff, and NULL when the source does not say.
    period_number: Mapped[int | None] = mapped_column(Integer)
    period_label: Mapped[str | None] = mapped_column(String(24))
    #: "Top"/"Bottom"/"Middle"/"End" for baseball; the source's period id for
    #: football. Absent rather than guessed.
    period_state: Mapped[str | None] = mapped_column(String(24))
    in_break: Mapped[bool | None] = mapped_column(Boolean)

    #: Seconds left on the game clock. NULL unless the source supplies a clock:
    #: neither current source does, and a computed clock would be a fiction.
    clock_seconds_remaining: Mapped[int | None] = mapped_column(Integer)

    #: Baseball situation. NULL whenever the source omits it; `bases` is
    #: {"first": name|null, ...} exactly as reported, plus an optional "detail"
    #: entry carrying the source's own live detail for any sport (MLB batter /
    #: on deck / in the hole / pitcher; NFL ESPN down, distance, possession, last
    #: play, win probability). Display only; settlement never reads it.
    outs: Mapped[int | None] = mapped_column(Integer)
    balls: Mapped[int | None] = mapped_column(Integer)
    strikes: Mapped[int | None] = mapped_column(Integer)
    bases: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    #: One entry per period the source reported, in order. Never interpolated.
    periods: Mapped[list[Any] | None] = mapped_column(JSON)
    #: False when the source gave a score but not a full breakdown for it.
    periods_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Whether the periods sum to the reported final. NULL while in progress.
    periods_reconcile: Mapped[bool | None] = mapped_column(Boolean)

    #: When the SOURCE says this state was true. NULL when it does not say --
    #: and then the UI must not present any time as the time the score changed.
    source_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: When AISP received the bytes this row was built from.
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: The latest successful check that found this same state. Advances in place
    #: so an unchanged payload proves freshness instead of looking stale.
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    payload_checksum: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "game_id", "source", "payload_checksum",
            name="uq_scoreboard_obs_state",
        ),
        CheckConstraint(
            "status IN ('SCHEDULED','IN_PROGRESS','DELAYED','SUSPENDED',"
            "'POSTPONED','CANCELLED','FINAL','UNKNOWN')",
            name="ck_scoreboard_obs_status",
        ),
        CheckConstraint(
            "home_score IS NULL OR home_score >= 0", name="ck_scoreboard_obs_home"),
        CheckConstraint(
            "away_score IS NULL OR away_score >= 0", name="ck_scoreboard_obs_away"),
        CheckConstraint(
            "outs IS NULL OR (outs >= 0 AND outs <= 3)", name="ck_scoreboard_obs_outs"),
        Index("ix_scoreboard_obs_game_checked", "game_id", "checked_at"),
        Index("ix_scoreboard_obs_league_checked", "league_code", "checked_at"),
    )


__all__ = ["SCOREBOARD_STATUSES", "GameScoreboardObservation"]

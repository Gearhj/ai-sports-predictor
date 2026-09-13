"""Recorded virtual parlay tickets, and the legs frozen inside them.

WHY A SEPARATE TABLE RATHER THAN THE POSITION LEDGER
----------------------------------------------------
``official_asp_positions`` holds one ``market_outcome_id`` per row and is unique
on ``(shadow_experiment_id, market_outcome_id)``. A parlay is not several
positions: its legs do not settle independently, and writing them as three rows
would both break that constraint and inflate straight-bet counts, win rates and
staked totals with money that was never staked that way.

So parlay tickets live here, and the season account reads them as their own
tagged exposure. One $1,000 contribution, two kinds of exposure against it,
never mixed in a single average.

WHAT IS FROZEN, AND WHY EACH FIELD IS ON THE TICKET
---------------------------------------------------
A ticket recorded before first pitch has to stay readable as what it was, after
the prices have moved and the forecasts have been superseded. Everything needed
to re-derive the decision is copied onto the row -- selections, lines, odds,
price provenance, the probability snapshot, the forecast identities, the stake,
the clock, and the settlement rules themselves. Pointing at live rows instead
would let a later re-publication silently rewrite what the ticket "was".

``ticket_sha256`` is the content address of that frozen decision and is unique.
Recording the same ticket twice therefore returns the first one rather than
creating a second -- the idempotency is structural, not a check that a caller
has to remember to perform.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import (
    Base,
    CreatedAtMixin,
    PositionResult,
    PositionStatus,
    UUIDPrimaryKeyMixin,
    enum_type,
)

#: Where a recorded ticket's stake comes from.
FUNDING_SEASON_ACCOUNT = "FUNDED_SEASON_ACCOUNT"
FUNDING_RESEARCH_ONLY = "RESEARCH_UNFUNDED"


class ParlayTicket(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One recorded virtual multi-leg ticket."""

    __tablename__ = "parlay_tickets"

    #: Content address of the frozen decision. Unique, so recording is idempotent.
    ticket_sha256: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False)
    #: Stable identity of the combination itself (games, selections, book).
    ticket_key: Mapped[str] = mapped_column(Text, nullable=False)

    league: Mapped[str] = mapped_column(String(16), nullable=False, default="MLB")
    sportsbook: Mapped[str] = mapped_column(String(120), nullable=False)
    leg_count: Mapped[int] = mapped_column(Integer, nullable=False)

    #: The season account this stake is drawn against, when it is funded at all.
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("experiment_accounts.id"), index=True)
    funding: Mapped[str] = mapped_column(
        String(32), nullable=False, default=FUNDING_RESEARCH_ONLY)

    stake: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    combined_decimal_odds: Mapped[float] = mapped_column(Float, nullable=False)
    #: RECORDED_SPORTSBOOK_COMBINED_QUOTE or ESTIMATED_FROM_SAME_BOOK_LEG_ODDS.
    #: Carried through settlement and performance reporting, never collapsed.
    price_provenance: Mapped[str] = mapped_column(String(48), nullable=False)

    #: The full priced payload at the instant of recording: every probability,
    #: the independence note, and the definitions of each probability field.
    probability_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    #: The settlement rules in force for THIS ticket, frozen. A later change to
    #: house rules cannot retroactively decide a ticket already recorded.
    settlement_rules: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    recorded_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False)
    earliest_cutoff_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False)

    status: Mapped[PositionStatus] = mapped_column(
        enum_type(PositionStatus), nullable=False, default=PositionStatus.OPEN)
    result: Mapped[PositionResult] = mapped_column(
        enum_type(PositionResult), nullable=False, default=PositionResult.PENDING)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payout: Mapped[float | None] = mapped_column(Numeric(12, 2))
    profit_loss: Mapped[float | None] = mapped_column(Numeric(12, 2))
    #: Which legs survived to price the ticket, and why, in plain words.
    settlement_note: Mapped[str | None] = mapped_column(Text)
    #: The decimal odds the ticket actually paid at, after pushed/void legs were
    #: removed. Differs from combined_decimal_odds whenever a leg dropped out.
    settled_decimal_odds: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        CheckConstraint("stake > 0", name="ck_parlay_ticket_stake_positive"),
        CheckConstraint("leg_count >= 2", name="ck_parlay_ticket_min_legs"),
        Index("ix_parlay_ticket_status_cutoff", "status", "earliest_cutoff_utc"),
    )


class ParlayTicketLeg(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One frozen selection on a recorded ticket."""

    __tablename__ = "parlay_ticket_legs"

    ticket_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("parlay_tickets.id", ondelete="CASCADE"), index=True, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)

    game_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("games.id"), index=True, nullable=False)
    market_outcome_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("market_outcomes.id"), index=True)
    odds_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("odds_snapshots.id"), index=True)

    market: Mapped[str] = mapped_column(String(24), nullable=False)
    selection: Mapped[str] = mapped_column(String(160), nullable=False)
    line: Mapped[float | None] = mapped_column(Float)
    decimal_odds: Mapped[float] = mapped_column(Float, nullable=False)
    american_odds: Mapped[int | None] = mapped_column(Integer)
    sportsbook: Mapped[str] = mapped_column(String(120), nullable=False)
    quote_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    #: The forecast that produced this leg's probability, by its own identity.
    forecast_identity: Mapped[str] = mapped_column(String(120), nullable=False)
    engine_id: Mapped[str] = mapped_column(String(32), nullable=False)

    #: Percent units, matching the payload they were copied from.
    win_probability_pct: Mapped[float] = mapped_column(Float, nullable=False)
    push_probability_pct: Mapped[float] = mapped_column(Float, nullable=False)
    loss_probability_pct: Mapped[float] = mapped_column(Float, nullable=False)

    #: Settlement, per leg. A pushed or voided leg is removed from the ticket
    #: rather than losing it, so which one it was has to be recorded.
    result: Mapped[PositionResult] = mapped_column(
        enum_type(PositionResult), nullable=False, default=PositionResult.PENDING)
    settled_home_score: Mapped[int | None] = mapped_column(Integer)
    settled_away_score: Mapped[int | None] = mapped_column(Integer)
    settled_note: Mapped[str | None] = mapped_column(Text)
    voided: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("ticket_id", "ordinal", name="uq_parlay_leg_ordinal"),
        UniqueConstraint("ticket_id", "game_id", name="uq_parlay_leg_one_per_game"),
    )


__all__ = [
    "FUNDING_RESEARCH_ONLY",
    "FUNDING_SEASON_ACCOUNT",
    "ParlayTicket",
    "ParlayTicketLeg",
]

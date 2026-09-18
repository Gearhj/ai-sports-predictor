"""A House Divided -- a prospective virtual wagering competition.

Three participants -- James (the models), Scotty, and Red Lou (Mom) -- each hold
a separate virtual $1,000 account per sport. Nothing here is real money, and
nothing here touches the $1,000 Experiment: these are NEW accounts, new rows,
and a wager here never creates, changes or settles an official position.

WHAT A WAGER ROW IS
-------------------
One virtual wager with its terms frozen at submission: market, selection, line,
book, American and decimal price, the exact quote it was priced from, the stake,
and when it was submitted. A DRAFT may be edited. Once SUBMITTED (status OPEN)
the terms are locked; the only later change is settlement, which happens
exactly once.

A mirrored model wager additionally keeps the official decision it came from
(`source_decision_id`, unique -- so it is imported once), the experiment, the
model version and the decision's own timestamps. A model decision the account
could not fund is kept as an UNFUNDED row with its reason: "the model wanted
this and could not afford it" is information, not an absence.

ACCOUNTING
----------
Bankroll = starting balance + settled net profit. A stake is a commitment, not
a loss: it reduces AVAILABLE funds while open and never moves the bankroll
until the wager settles, so the bankroll chart cannot dip when a wager is
placed.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, Numeric, String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.entities import CreatedAtMixin, UUIDPrimaryKeyMixin

# -- wager lifecycle ---------------------------------------------------------
WAGER_DRAFT = "DRAFT"
WAGER_OPEN = "OPEN"            # submitted: terms locked, stake committed
WAGER_SETTLED = "SETTLED"
WAGER_UNFUNDED = "UNFUNDED"    # a model decision the account could not fund
WAGER_DISCARDED = "DISCARDED"  # a draft the participant withdrew; never submitted

SOURCE_MODEL = "MODEL_MIRROR"
SOURCE_HUMAN = "HUMAN"


class CompetitionParticipant(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "competition_participants"

    #: Stable identity: JAMES, SCOTTY, RED_LOU.
    key: Mapped[str] = mapped_column(String(24), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    color: Mapped[str] = mapped_column(String(16), nullable=False)
    #: True only for James, whose wagers are mirrored model decisions.
    is_model: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: The AISP login allowed to enter this participant's own picks. Null means
    #: only an administrator may enter them on the participant's behalf.
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class CompetitionAccount(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "competition_accounts"

    participant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("competition_participants.id"), index=True, nullable=False)
    sport: Mapped[str] = mapped_column(String(8), index=True, nullable=False)
    starting_balance: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("1000.00"))
    #: The recorded prospective start for this sport. Null is "Not started":
    #: the balance is intact and nothing before this instant is ever imported.
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))

    __table_args__ = (
        UniqueConstraint("participant_id", "sport", name="uq_competition_account_sport"),
        CheckConstraint("starting_balance > 0", name="ck_competition_account_positive"),
    )


class CompetitionWager(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "competition_wagers"

    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("competition_accounts.id"), index=True, nullable=False)
    participant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("competition_participants.id"), index=True, nullable=False)
    sport: Mapped[str] = mapped_column(String(8), index=True, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), index=True, nullable=False)

    # -- the bet ---------------------------------------------------------
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), index=True, nullable=False)
    market_type: Mapped[str | None] = mapped_column(String(16))
    market_outcome_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("market_outcomes.id"))
    selection_label: Mapped[str | None] = mapped_column(String(80))
    line: Mapped[float | None] = mapped_column(Float)
    sportsbook_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sportsbooks.id"))
    sportsbook_key: Mapped[str | None] = mapped_column(String(80))
    odds_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("odds_snapshots.id"))
    american_odds: Mapped[int | None] = mapped_column(Integer)
    decimal_odds: Mapped[float | None] = mapped_column(Float)
    quote_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quote_available_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stake: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))

    # -- provenance ------------------------------------------------------
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Who actually entered it. For a pick entered on a participant's behalf
    #: this is the administrator; the participant is `participant_id`.
    entered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    #: The official model decision a mirrored wager came from. Unique, so a
    #: decision can be imported exactly once however often the sync runs.
    source_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("shadow_experiment_decisions.id"), unique=True)
    source_experiment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("shadow_experiments.id"))
    model_version: Mapped[str | None] = mapped_column(String(120))
    decision_recommended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    staking_detail: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    unfunded_reason: Mapped[str | None] = mapped_column(String(200))

    # -- settlement (exactly once) ---------------------------------------
    result: Mapped[str | None] = mapped_column(String(8))
    profit_loss: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    payout: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settlement_note: Mapped[str | None] = mapped_column(String(200))
    game_home_score: Mapped[int | None] = mapped_column(Integer)
    game_away_score: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        CheckConstraint("stake >= 0", name="ck_competition_wager_stake_nonnegative"),
        CheckConstraint(
            "status IN ('DRAFT','OPEN','SETTLED','UNFUNDED','DISCARDED')",
            name="ck_competition_wager_status"),
        CheckConstraint(
            "source IN ('MODEL_MIRROR','HUMAN')", name="ck_competition_wager_source"),
    )

"""Sport/season accounts for the $1,000 paper-bankroll program.

THE PROBLEM THESE SOLVE
-----------------------
Capital used to live on ``ShadowExperiment``, which is a **model era**, not a
season. ``current_bankroll`` was ``experiment.starting_bankroll + realized
profit``, and when V18 succeeded Gen1 it opened with 993.52 — the carried
available capital. That number is correct as an era opening balance and wrong
as a season contribution: summing eras would report 1,993.52 of capital where
1,000.00 was ever contributed, and V18 would never see the 2.50 returned when
Gen1's last open position finally settles.

So the season owns the money and eras are things that happen inside it:

    ExperimentAccount            one sport, one season, one $1,000
      └── ExperimentAccountFunding      the opening contribution, once
      └── ExperimentAccountMembership   each model era that traded in it

MEMBERSHIP RATHER THAN A COLUMN
-------------------------------
``ShadowExperiment`` is append-only under ``app/audit/immutability.py``, and it
should stay that way: those rows are the start authority for a funded
experiment. Rather than adding a column and backfilling it — which would mean
rewriting evidence to record something that was always true — membership is its
own additive table. An era's account is stated alongside the era, not written
into it.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base, CreatedAtMixin, StrEnum, UUIDPrimaryKeyMixin, enum_type


class AccountStatus(StrEnum):
    """Where a season account stands.

    ``PLANNED`` is the one that carries the most weight. It means the $1,000 is
    an intention, not capital: nothing is funded, nothing is tracked, and the
    page must never fold it into a funded total.
    """

    PLANNED = "PLANNED"
    ACTIVE = "ACTIVE"
    #: The season's window has closed. No new position may open, but positions
    #: already open are still running and will still settle into this account.
    CLOSED_FOR_NEW_POSITIONS = "CLOSED_FOR_NEW_POSITIONS"
    #: Closed, and everything that could settle has. Kept for the record.
    ARCHIVED = "ARCHIVED"


class MembershipRole(StrEnum):
    """What an era was doing inside its account.

    Only ``FUNDED_ERA`` contributes to the account's bankroll. The others exist
    so that an unfunded control or a research candidate has an honest home
    rather than being silently dropped or silently counted.
    """

    FUNDED_ERA = "FUNDED_ERA"
    #: A control policy run alongside the funded era with no capital behind it.
    UNFUNDED_CONTROL = "UNFUNDED_CONTROL"
    #: A registered policy or model that never took control of funded capital.
    RESEARCH_CANDIDATE = "RESEARCH_CANDIDATE"


class FundingKind(StrEnum):
    #: The one opening contribution a season receives. Exactly once.
    SEASON_OPENING = "SEASON_OPENING"


class ExperimentAccount(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One sport, one season, one paper bankroll.

    Status changes over a season's life, so this row is deliberately not
    append-only. Everything that represents *money* — the contribution, and
    every position and settlement — lives in append-only tables instead.
    """

    __tablename__ = "experiment_accounts"

    sport: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    #: Explicit season identifier: "2026" or "2026-27". Never inferred at read
    #: time from a date, so an account cannot drift between seasons.
    season_id: Mapped[str] = mapped_column(String(16), nullable=False)
    season_label: Mapped[str] = mapped_column(String(120), nullable=False)
    #: Configured accounting boundaries, copied from the season calendar at
    #: creation so a later configuration change cannot silently restate a
    #: season that has already run.
    season_opens_on: Mapped[date] = mapped_column(Date, nullable=False)
    season_closes_on: Mapped[date] = mapped_column(Date, nullable=False)
    boundary_basis: Mapped[str] = mapped_column(Text, nullable=False)
    #: Set when the window is a product convention rather than a league season.
    boundary_convention: Mapped[str | None] = mapped_column(Text)

    status: Mapped[AccountStatus] = mapped_column(
        enum_type(AccountStatus), nullable=False, default=AccountStatus.PLANNED,
    )
    #: What the program intends to contribute. Present on a PLANNED account,
    #: where it is explicitly *not* funded capital.
    planned_contribution: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)

    #: The day this account actually began tracking — the first funded
    #: experiment's start, not the configured season opening. Null until
    #: funded. Nothing is ever drawn before this date.
    tracking_started_on: Mapped[date | None] = mapped_column(Date)
    #: True when tracking began after the season's configured opening, so the
    #: page can say "partial season" instead of implying full coverage.
    partial_season: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Why an account is not funded, in the reader's words.
    status_note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("sport", "season_id", name="uq_experiment_account_sport_season"),
        CheckConstraint("planned_contribution >= 0", name="ck_experiment_account_contribution"),
    )


class ExperimentAccountFunding(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """An actual contribution of virtual capital into a season account.

    Append-only, and unique on ``(account_id, kind)`` so a season can be opened
    exactly once no matter how many times the opening job runs. The receipt
    fields record what actually executed, so "the season was funded" is a claim
    backed by a row rather than by a schedule that was supposed to fire.
    """

    __tablename__ = "experiment_account_fundings"

    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experiment_accounts.id"), nullable=False, index=True,
    )
    kind: Mapped[FundingKind] = mapped_column(
        enum_type(FundingKind), nullable=False, default=FundingKind.SEASON_OPENING,
    )
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    #: When the capital was contributed, and the operational date it counts on.
    funded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_on: Mapped[date] = mapped_column(Date, nullable=False)
    #: Execution receipt: the job run that performed it, and a stable key that
    #: makes a repeat run a no-op rather than a second contribution.
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    job_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("job_runs.id"))
    executed_by: Mapped[str] = mapped_column(String(80), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("account_id", "kind", name="uq_account_funding_once"),
        CheckConstraint("amount > 0", name="ck_account_funding_amount_positive"),
    )


class ExperimentAccountMembership(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One model era's membership of one season account.

    Unique on the experiment, so an era belongs to exactly one account and its
    positions and settlements can therefore be counted exactly once when the
    account aggregates across its eras.
    """

    __tablename__ = "experiment_account_memberships"

    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experiment_accounts.id"), nullable=False, index=True,
    )
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shadow_experiments.id"), nullable=False, unique=True,
    )
    role: Mapped[MembershipRole] = mapped_column(enum_type(MembershipRole), nullable=False)
    #: Why this era was classified the way it was — recorded rather than
    #: recomputed, so a later rule change cannot silently reclassify history.
    role_basis: Mapped[str] = mapped_column(Text, nullable=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_account_membership_account_role", "account_id", "role"),
    )

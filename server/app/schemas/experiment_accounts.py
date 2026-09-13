"""Wire shapes for the per-sport, per-season $1,000 paper accounts.

Two things are deliberately shaped into the schema rather than left to the
page's judgement.

**Planned is not funded.** A planned account carries ``planned_contribution``
and *no* ``original_contribution``. There is no field a client could read that
would let it add an unfunded sport into a funded total by accident, and the
program summary reports the two separately for the same reason.

**An unavailable ratio is null, not zero.** ``settled_stake_roi_pct`` is
``None`` when nothing has settled. Zero would claim a measured break-even
result from no measurements at all.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

AccountStatusLiteral = Literal["PLANNED", "ACTIVE", "CLOSED_FOR_NEW_POSITIONS", "ARCHIVED"]
MembershipRoleLiteral = Literal["FUNDED_ERA", "UNFUNDED_CONTROL", "RESEARCH_CANDIDATE"]


class SeasonBoundary(BaseModel):
    """The configured accounting window, with what it rests on."""

    opens_on: str
    closes_on: str
    #: Where these dates come from. Always shown, so a configured accounting
    #: window is never mistaken for a published league schedule.
    basis: str
    #: Present only when the window is a product convention (the MMA campaign).
    convention: str | None = None


class EraSummary(BaseModel):
    """One model era inside a season account."""

    experiment_id: str
    name: str
    version: int
    model_era_id: str | None
    controlling_engine: str
    decision_policy: str
    started_on: str
    role: MembershipRoleLiteral
    role_basis: str
    #: The era's own active flag. Several eras can carry this at once: a
    #: predecessor stays active so its open positions keep settling.
    is_active: bool
    #: Exactly one era per account, and only when it is a funded era that is
    #: still active. "Active" and "in control" are different facts, and showing
    #: the first for the second put "controlling now" on four eras at once.
    is_controlling: bool = False
    #: Only a FUNDED_ERA contributes these to the account's totals.
    settled_net_pl: float
    settled_stake: float
    reserved_stake: float
    open_positions: int
    settled_positions: int


class AccountPosition(BaseModel):
    id: str
    placed_at: str
    stake: float
    decimal_odds: float
    status: str
    result: str
    #: Which model era placed it. This is what turns the position list into a
    #: record of an era change rather than an undifferentiated stream.
    era: str | None
    profit_loss: float | None
    settled_at: str | None


class EquityPoint(BaseModel):
    date: str
    bankroll: float
    settlements: int


class SeasonAccount(BaseModel):
    """One sport's season account — funded or planned."""

    sport: str
    sport_label: str
    season_id: str
    season_label: str
    status: AccountStatusLiteral
    boundary: SeasonBoundary

    #: Funded accounts only. Null on a PLANNED account, which has no capital.
    original_contribution: float | None = None
    realized_bankroll: float | None = None
    reserved_stake: float | None = None
    available_capital: float | None = None
    settled_net_pl: float | None = None
    settled_stake: float | None = None
    #: Settled net P&L / original contribution.
    season_return_pct: float | None = None
    #: Settled net P&L / settled stakes. Null when no stake has settled — an
    #: unavailable ratio, never 0.0.
    settled_stake_roi_pct: float | None = None

    #: The real first day of tracking. Null when nothing is funded. No chart
    #: point and no result is claimed before it.
    tracking_started_on: str | None = None
    #: True when tracking began after the season's configured opening.
    partial_season: bool = False
    funded_at: str | None = None

    #: What the program intends to contribute if this season starts. On a
    #: PLANNED account this is the only money figure, and it is not capital.
    planned_contribution: float
    #: Why an account is not funded, in plain words.
    status_note: str | None = None

    record: str | None = None
    wins: int | None = None
    losses: int | None = None
    pushes: int | None = None
    open_positions: int | None = None
    settled_positions: int | None = None

    #: The era in control now, if any.
    controlling_engine: str | None = None
    controlling_era_id: str | None = None

    eras: list[EraSummary] = []
    equity_curve: list[EquityPoint] = []
    positions: list[AccountPosition] = []


class ProgramSummary(BaseModel):
    """Program totals, with funded and planned kept apart.

    ``planned_allocation`` is a proposal. Five planned sports at $1,000 each is
    $5,000 of *proposed* paper capital — not $5,000 funded and not $5,000
    available today — and the two totals never share a field.
    """

    funded_accounts: int
    planned_accounts: int
    funded_contribution: float
    funded_realized_bankroll: float
    funded_settled_net_pl: float
    funded_reserved_stake: float
    funded_available_capital: float
    planned_allocation: float
    #: Exactly which accounts the funded totals cover.
    funded_account_keys: list[str]
    planned_account_keys: list[str]
    note: str


class ExperimentAccountsData(BaseModel):
    data_mode: Literal["live", "empty"]
    as_of: str
    program_contribution: float
    summary: ProgramSummary
    accounts: list[SeasonAccount]

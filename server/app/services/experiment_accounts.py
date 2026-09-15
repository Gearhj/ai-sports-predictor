"""Season-account accounting for the $1,000 paper-bankroll program.

THE ONE RULE THAT MATTERS
-------------------------
A season received **one** contribution. Everything else about its balance is
derived from settlements and open stakes, never from another balance.

That rule is what makes the Gen1→V18 transition safe. V18's
``starting_bankroll`` of 993.52 is a *model-era opening balance*: the capital
that was available when the era began, after Gen1's realized loss and with
Gen1's one open stake still reserved. It is not a deposit. Adding it to Gen1's
1,000.00 would report 1,993.52 of capital that was never contributed.

So this module never reads ``starting_bankroll`` for account totals. It reads:

    original contribution   the funding rows — 1,000.00, once
    settled net P&L         every settlement across every funded era, once
    realized bankroll       contribution + settled net P&L
    reserved                every OPEN position's stake across funded eras
    available               realized - reserved

Each position and settlement is attributed to exactly one era, and each era to
exactly one account, so "exactly once" is a property of the schema rather than
a hope about the query.

The same rule fixes the other half of the problem. Under per-era capital, when
Gen1's last open position finally settles, its stake returns to *Gen1* and V18
— whose opening balance already had that stake deducted — never sees it. The
money would simply vanish from the season. Here the settlement lands in the
account, because the account is what holds the money.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.experiments.season_calendar import (
    PROGRAM_CONTRIBUTION,
    SeasonWindow,
    program_season_for,
    supported_sports,
    window_for,
)
from app.models import (
    AccountStatus,
    ExperimentAccount,
    ExperimentAccountFunding,
    ExperimentAccountMembership,
    FundingKind,
    MembershipRole,
    OfficialAspPosition,
    PositionResult,
    PositionSettlement,
    PositionStatus,
    ShadowExperiment,
)

#: The unfunded strict control. It runs beside the funded era to give the
#: decision policy something to be compared against, and it has never held
#: capital, so it must not appear in any funded total.
UNFUNDED_CONTROL_POLICY = "AISP_OPPORTUNITY_POLICY_V1"

#: Why each sport does or does not have funded capital today. These are
#: statements about AISP's actual state, not placeholders.
DEFAULT_UNFUNDED_NOTE = (
    "Planned allocation only. This season has no funded model era attached, so "
    "no capital has been contributed and nothing is being tracked."
)

SPORT_READINESS: dict[str, str] = {
    "MLB": "",
    "NFL": (
        "NFL runs as a research shadow with no wagering authority. No funded "
        "account opens until a governed NFL decision policy is activated."
    ),
    "NBA": (
        "NBA holds imported research forecasts. A paper account opens under "
        "AISP_NBA_PAPER_POLICY_V1; positions additionally require the "
        "producer's eligibility record for the exact forecast."
    ),
    "CFB": (
        "College Football holds an imported research forecast. A paper account "
        "opens under AISP_CFB_PAPER_POLICY_V1; positions additionally require "
        "the producer's eligibility record for the exact forecast."
    ),
    "NHL": "No NHL ingestion, forecasting or decision policy exists yet.",
    "MMA": "No MMA ingestion, forecasting or decision policy exists yet.",
}


@dataclass(frozen=True)
class EraLedger:
    """One model era's contribution to its account."""

    experiment_id: UUID
    name: str
    version: int
    model_era_id: str | None
    decision_policy: str
    controlling_engine: str
    started_on: date
    role: MembershipRole
    is_active: bool
    settled_net_pl: float
    settled_stake: float
    reserved_stake: float
    open_positions: int
    settled_positions: int


@dataclass
class AccountLedger:
    """Everything the page needs about one sport/season account."""

    account: ExperimentAccount
    window: SeasonWindow
    original_contribution: float
    settled_net_pl: float
    realized_bankroll: float
    reserved_stake: float
    available_capital: float
    settled_stake: float
    wins: int
    losses: int
    pushes: int
    open_positions: int
    settled_positions: int
    eras: list[EraLedger] = field(default_factory=list)
    equity_curve: list[dict[str, Any]] = field(default_factory=list)
    positions: list[dict[str, Any]] = field(default_factory=list)
    funded_at: datetime | None = None

    @property
    def is_funded(self) -> bool:
        return self.original_contribution > 0

    @property
    def season_return_pct(self) -> float | None:
        """Settled net P&L over the original contribution.

        The denominator is what was contributed, not what is currently held —
        a season that is down 4% has returned -4% on its $1,000 whatever its
        balance happens to be today.
        """

        if self.original_contribution <= 0:
            return None
        return self.settled_net_pl / self.original_contribution * 100

    @property
    def settled_stake_roi_pct(self) -> float | None:
        """Settled net P&L over settled stakes — a different question.

        Unavailable, not zero, when nothing has settled. Zero would claim a
        measured break-even result from no measurements at all.
        """

        if self.settled_stake <= 0:
            return None
        return self.settled_net_pl / self.settled_stake * 100

    @property
    def record(self) -> str:
        return f"{self.wins}–{self.losses}–{self.pushes}"


class ExperimentAccountService:
    """Reads and maintains sport/season paper-bankroll accounts."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    # -- account resolution ------------------------------------------------

    def account(self, sport: str, season_id: str) -> ExperimentAccount | None:
        return self.session.scalar(
            select(ExperimentAccount).where(
                ExperimentAccount.sport == sport.upper(),
                ExperimentAccount.season_id == season_id,
            )
        )

    def accounts_for(self, sport: str) -> list[ExperimentAccount]:
        """Every season this sport has ever held, newest first."""

        return list(self.session.scalars(
            select(ExperimentAccount)
            .where(ExperimentAccount.sport == sport.upper())
            .order_by(ExperimentAccount.season_opens_on.desc())
        ))

    def current_account(self, sport: str, *, today: date) -> ExperimentAccount | None:
        """The account the overview should show for this sport.

        Prefers a season that is genuinely running over the one the calendar
        says is current, so an account still awaiting final settlement after
        its window closed does not disappear the moment a new season opens.
        """

        window = program_season_for(sport, today)
        exact = self.account(sport, window.season_id)
        if exact is not None:
            return exact
        existing = self.accounts_for(sport)
        return existing[0] if existing else None

    # -- provisioning ------------------------------------------------------

    def ensure_account(
        self, sport: str, season_id: str, *, status_note: str | None = None,
    ) -> ExperimentAccount:
        """Create the season's account row if it does not exist. Idempotent.

        Creating an account does **not** fund it. A new account is PLANNED: the
        $1,000 is an intention until a funding row exists.
        """

        existing = self.account(sport, season_id)
        if existing is not None:
            return existing
        window = window_for(sport, season_id)
        account = ExperimentAccount(
            sport=window.sport,
            season_id=window.season_id,
            season_label=window.label,
            season_opens_on=window.opens_on,
            season_closes_on=window.closes_on,
            boundary_basis=window.basis,
            boundary_convention=window.convention,
            status=AccountStatus.PLANNED,
            planned_contribution=PROGRAM_CONTRIBUTION,
            # A planned account always explains itself. Falling back to a
            # generic reason is better than a blank the reader must interpret.
            status_note=(
                status_note
                or SPORT_READINESS.get(window.sport)
                or DEFAULT_UNFUNDED_NOTE
            ),
        )
        self.session.add(account)
        self.session.flush()
        return account

    def funding(self, account: ExperimentAccount) -> ExperimentAccountFunding | None:
        return self.session.scalar(
            select(ExperimentAccountFunding).where(
                ExperimentAccountFunding.account_id == account.id,
                ExperimentAccountFunding.kind == FundingKind.SEASON_OPENING,
            )
        )

    @staticmethod
    def opening_key(sport: str, season_id: str) -> str:
        """The idempotency key for a season's one opening contribution."""

        return f"SEASON_OPENING:{sport.upper()}:{season_id}"

    def open_season(
        self,
        account: ExperimentAccount,
        *,
        now: datetime,
        executed_by: str,
        tracking_started_on: date | None = None,
        job_run_id: UUID | None = None,
        note: str | None = None,
    ) -> tuple[ExperimentAccountFunding, bool]:
        """Contribute this season's $1,000, once.

        Returns ``(funding, created)``. A repeat call returns the existing row
        with ``created=False`` and writes nothing, so the scheduled opening job
        is safe to run on every tick, to retry, and to run twice concurrently —
        the unique constraint is the backstop behind this check.
        """

        existing = self.funding(account)
        if existing is not None:
            return existing, False

        effective_on = tracking_started_on or account.season_opens_on
        funding = ExperimentAccountFunding(
            account_id=account.id,
            kind=FundingKind.SEASON_OPENING,
            amount=PROGRAM_CONTRIBUTION,
            funded_at=now,
            effective_on=effective_on,
            idempotency_key=self.opening_key(account.sport, account.season_id),
            job_run_id=job_run_id,
            executed_by=executed_by,
            note=note,
        )
        self.session.add(funding)
        account.status = AccountStatus.ACTIVE
        account.tracking_started_on = effective_on
        # A season entered after its configured opening is a partial season,
        # and says so rather than implying it covered the whole thing.
        account.partial_season = effective_on > account.season_opens_on
        account.status_note = None
        self.session.flush()
        return funding, True

    # -- membership --------------------------------------------------------

    def classify(self, experiment: ShadowExperiment) -> tuple[MembershipRole, str]:
        """What role an era played, and why.

        Capital decides, not naming. The test is whether the era ever held a
        position: a registration that was superseded before it ever controlled
        anything contributed nothing and must not appear in funded performance,
        while an era that took even one position owns that position's outcome
        forever — including after it has been superseded itself.

        The strict control is named separately because it is *structurally*
        unfunded rather than merely inactive. It will never hold capital, and
        saying so is more honest than letting it fall through to "no positions".
        """

        if experiment.decision_policy == UNFUNDED_CONTROL_POLICY:
            return (
                MembershipRole.UNFUNDED_CONTROL,
                "Strict control policy. It records decisions for comparison and "
                "has never held capital, so it is excluded from funded totals.",
            )
        from app.services.shadow_experiment_service import SUPERSEDED_POLICIES

        positions = int(self.session.scalar(
            select(func.count()).select_from(OfficialAspPosition).where(
                OfficialAspPosition.shadow_experiment_id == experiment.id
            )
        ) or 0)
        # A policy superseded before activation is excluded from every active
        # read by the experiment service, and its `is_active` flag was simply
        # never cleared. Trusting that flag would file it as funded evidence.
        superseded = experiment.decision_policy in SUPERSEDED_POLICIES
        if positions == 0 and (superseded or not experiment.is_active):
            return (
                MembershipRole.RESEARCH_CANDIDATE,
                "Registered but superseded before it controlled any capital: no "
                "position was ever opened under it, so it contributes nothing to "
                "the account's bankroll.",
            )
        return (
            MembershipRole.FUNDED_ERA,
            "Funded model era under the season's decision policy. Its positions "
            "and settlements are the account's own, and stay with this account "
            "after the era is superseded.",
        )

    def membership(self, experiment_id: UUID) -> ExperimentAccountMembership | None:
        return self.session.scalar(
            select(ExperimentAccountMembership).where(
                ExperimentAccountMembership.experiment_id == experiment_id
            )
        )

    def attach(
        self,
        account: ExperimentAccount,
        experiment: ShadowExperiment,
        *,
        now: datetime,
        role: MembershipRole | None = None,
        role_basis: str | None = None,
    ) -> tuple[ExperimentAccountMembership, bool]:
        """Link one era to one account. Idempotent on the experiment."""

        existing = self.membership(experiment.id)
        if existing is not None:
            return existing, False
        resolved_role, resolved_basis = self.classify(experiment)
        membership = ExperimentAccountMembership(
            account_id=account.id,
            experiment_id=experiment.id,
            role=role or resolved_role,
            role_basis=role_basis or resolved_basis,
            joined_at=now,
        )
        self.session.add(membership)
        self.session.flush()
        return membership, True

    def member_experiments(
        self, account: ExperimentAccount, *, roles: tuple[MembershipRole, ...] | None = None,
    ) -> list[tuple[ShadowExperiment, MembershipRole]]:
        statement = (
            select(ShadowExperiment, ExperimentAccountMembership.role)
            .join(
                ExperimentAccountMembership,
                ExperimentAccountMembership.experiment_id == ShadowExperiment.id,
            )
            .where(ExperimentAccountMembership.account_id == account.id)
            .order_by(ShadowExperiment.version)
        )
        if roles:
            statement = statement.where(ExperimentAccountMembership.role.in_(list(roles)))
        return [(row[0], row[1]) for row in self.session.execute(statement).all()]

    def account_for_experiment(self, experiment_id: UUID) -> ExperimentAccount | None:
        return self.session.scalar(
            select(ExperimentAccount)
            .join(
                ExperimentAccountMembership,
                ExperimentAccountMembership.account_id == ExperimentAccount.id,
            )
            .where(ExperimentAccountMembership.experiment_id == experiment_id)
        )

    # -- capital -----------------------------------------------------------

    def funded_experiment_ids(self, account: ExperimentAccount) -> list[UUID]:
        return list(self.session.scalars(
            select(ExperimentAccountMembership.experiment_id).where(
                ExperimentAccountMembership.account_id == account.id,
                ExperimentAccountMembership.role == MembershipRole.FUNDED_ERA,
            )
        ))

    def original_contribution(self, account: ExperimentAccount) -> float:
        """What was actually contributed. Zero for a planned account."""

        total = self.session.scalar(
            select(func.coalesce(func.sum(ExperimentAccountFunding.amount), 0)).where(
                ExperimentAccountFunding.account_id == account.id
            )
        )
        return float(total or 0.0)

    def settled_net_pl(self, account: ExperimentAccount) -> float:
        """Every settlement in every funded era of this account, once each."""

        ids = self.funded_experiment_ids(account)
        if not ids:
            return 0.0
        total = self.session.scalar(
            select(func.coalesce(func.sum(PositionSettlement.profit_loss), 0))
            .select_from(PositionSettlement)
            .join(OfficialAspPosition, OfficialAspPosition.id == PositionSettlement.position_id)
            .where(OfficialAspPosition.shadow_experiment_id.in_(ids))
        )
        return float(total or 0.0)

    def reserved_stake(self, account: ExperimentAccount) -> float:
        """Stake locked in positions that are still open.

        Open positions only. A settled position's stake was returned or lost
        through its settlement's profit and loss, and deducting it again here
        is the double-subtraction this accounting exists to prevent.
        """

        ids = self.funded_experiment_ids(account)
        if not ids:
            return 0.0
        total = self.session.scalar(
            select(func.coalesce(func.sum(OfficialAspPosition.stake), 0)).where(
                OfficialAspPosition.shadow_experiment_id.in_(ids),
                OfficialAspPosition.status == PositionStatus.OPEN,
            )
        )
        return float(total or 0.0)

    def realized_bankroll(self, account: ExperimentAccount) -> float:
        return self.original_contribution(account) + self.settled_net_pl(account)

    def available_capital(self, account: ExperimentAccount) -> float:
        """What a new position in this account may draw on.

        This is the number that isolates sports from one another: it is
        computed only from this account's own funding, settlements and open
        stakes, so one sport's capital is structurally unreachable from
        another's.
        """

        return max(0.0, self.realized_bankroll(account) - self.reserved_stake(account))

    def available_for_experiment(self, experiment: ShadowExperiment) -> float | None:
        """Account-level available capital for the era about to stake.

        ``None`` when the era has no account, which is the signal to fall back
        to the pre-existing per-era behaviour rather than to invent a balance.
        """

        membership = self.membership(experiment.id)
        if membership is None or membership.role is not MembershipRole.FUNDED_ERA:
            return None
        account = self.session.get(ExperimentAccount, membership.account_id)
        if account is None:
            return None
        return self.available_capital(account)

    def staking_basis(self, experiment: ShadowExperiment) -> dict[str, Any] | None:
        """The capital figures a new position in this era must size against.

        Returns ``None`` when the era has no account, so the caller keeps its
        previous per-era behaviour rather than sizing against a balance that
        does not exist.

        Both figures come from the account:

        ``bankroll``   the season's realized bankroll — contribution plus
                       settled P&L. This is the true capital base for Kelly.
                       The era's own ``starting_bankroll`` is not: for a
                       successor era it is already net of the predecessor's
                       reserved stake, which is an *available* figure, and
                       sizing Kelly on it quietly shrinks the base every time
                       an era changes hands with a position still open.

        ``available``  realized minus every open stake in the account, which is
                       what actually caps the new position. Because it spans
                       eras, the predecessor's open stake is still reserved
                       while it is open — and released to the account, not
                       stranded in the old era, once it settles.
        """

        membership = self.membership(experiment.id)
        # Only a funded era may draw on the season's capital. The strict
        # control and any research candidate keep their own per-era behaviour:
        # letting an unfunded policy size against funded capital would put real
        # season money behind a comparison that is supposed to cost nothing.
        if membership is None or membership.role is not MembershipRole.FUNDED_ERA:
            return None
        account = self.session.get(ExperimentAccount, membership.account_id)
        if account is None:
            return None
        realized = self.realized_bankroll(account)
        reserved = self.reserved_stake(account)
        return {
            "account_id": account.id,
            "sport": account.sport,
            "season_id": account.season_id,
            "bankroll": round(realized, 2),
            "available": round(max(0.0, realized - reserved), 2),
            "reserved": round(reserved, 2),
        }

    def account_stake_used(
        self, account: ExperimentAccount, *, slate_date: date | None = None,
        game_id: UUID | None = None,
    ) -> float:
        """Stake already committed in this account, for a day or for one game.

        Account-wide rather than per-era on purpose. A same-game or daily cap
        that only looked at the current era could be evaded by an era change
        mid-slate: the successor would start from zero used and the account
        could exceed the cap it is supposed to respect.
        """

        from app.models import Market, MarketOutcome, ShadowExperimentDecision

        ids = self.funded_experiment_ids(account)
        if not ids:
            return 0.0
        statement = select(func.coalesce(func.sum(OfficialAspPosition.stake), 0)).where(
            OfficialAspPosition.shadow_experiment_id.in_(ids)
        )
        if slate_date is not None:
            statement = statement.join(
                ShadowExperimentDecision,
                ShadowExperimentDecision.id == OfficialAspPosition.shadow_decision_id,
            ).where(ShadowExperimentDecision.slate_date == slate_date)
        if game_id is not None:
            statement = statement.join(
                MarketOutcome, MarketOutcome.id == OfficialAspPosition.market_outcome_id
            ).join(Market, Market.id == MarketOutcome.market_id).where(Market.game_id == game_id)
        return float(self.session.scalar(statement) or 0.0)

    # -- the full ledger ---------------------------------------------------

    def ledger(self, account: ExperimentAccount) -> AccountLedger:
        window = window_for(account.sport, account.season_id)
        contribution = self.original_contribution(account)
        members = self.member_experiments(account)
        funded_ids = [
            experiment.id for experiment, role in members if role is MembershipRole.FUNDED_ERA
        ]

        eras: list[EraLedger] = []
        for experiment, role in members:
            eras.append(self._era_ledger(experiment, role))

        settled_net = sum(era.settled_net_pl for era in eras if era.role is MembershipRole.FUNDED_ERA)
        settled_stake = sum(era.settled_stake for era in eras if era.role is MembershipRole.FUNDED_ERA)
        reserved = sum(era.reserved_stake for era in eras if era.role is MembershipRole.FUNDED_ERA)
        realized = contribution + settled_net

        wins, losses, pushes = self._record(funded_ids)
        positions = self._positions(funded_ids)
        funding = self.funding(account)

        return AccountLedger(
            account=account,
            window=window,
            original_contribution=contribution,
            settled_net_pl=round(settled_net, 2),
            realized_bankroll=round(realized, 2),
            reserved_stake=round(reserved, 2),
            available_capital=round(max(0.0, realized - reserved), 2),
            settled_stake=round(settled_stake, 2),
            wins=wins, losses=losses, pushes=pushes,
            open_positions=sum(era.open_positions for era in eras),
            settled_positions=sum(era.settled_positions for era in eras),
            eras=eras,
            equity_curve=self._equity_curve(account, contribution, funded_ids),
            positions=positions,
            funded_at=funding.funded_at if funding else None,
        )

    def _era_ledger(self, experiment: ShadowExperiment, role: MembershipRole) -> EraLedger:
        net = float(self.session.scalar(
            select(func.coalesce(func.sum(PositionSettlement.profit_loss), 0))
            .select_from(PositionSettlement)
            .join(OfficialAspPosition, OfficialAspPosition.id == PositionSettlement.position_id)
            .where(OfficialAspPosition.shadow_experiment_id == experiment.id)
        ) or 0.0)
        settled_stake = float(self.session.scalar(
            select(func.coalesce(func.sum(OfficialAspPosition.stake), 0))
            .select_from(OfficialAspPosition)
            .join(PositionSettlement, PositionSettlement.position_id == OfficialAspPosition.id)
            .where(OfficialAspPosition.shadow_experiment_id == experiment.id)
        ) or 0.0)
        reserved = float(self.session.scalar(
            select(func.coalesce(func.sum(OfficialAspPosition.stake), 0)).where(
                OfficialAspPosition.shadow_experiment_id == experiment.id,
                OfficialAspPosition.status == PositionStatus.OPEN,
            )
        ) or 0.0)
        open_count = int(self.session.scalar(
            select(func.count()).select_from(OfficialAspPosition).where(
                OfficialAspPosition.shadow_experiment_id == experiment.id,
                OfficialAspPosition.status == PositionStatus.OPEN,
            )
        ) or 0)
        settled_count = int(self.session.scalar(
            select(func.count()).select_from(PositionSettlement)
            .join(OfficialAspPosition, OfficialAspPosition.id == PositionSettlement.position_id)
            .where(OfficialAspPosition.shadow_experiment_id == experiment.id)
        ) or 0)
        return EraLedger(
            experiment_id=experiment.id,
            name=experiment.name,
            version=experiment.version,
            model_era_id=experiment.model_era_id,
            decision_policy=experiment.decision_policy,
            controlling_engine=experiment.controlling_engine,
            started_on=experiment.start_date,
            role=role,
            is_active=bool(experiment.is_active),
            settled_net_pl=round(net, 2),
            settled_stake=round(settled_stake, 2),
            reserved_stake=round(reserved, 2),
            open_positions=open_count,
            settled_positions=settled_count,
        )

    def _record(self, funded_ids: list[UUID]) -> tuple[int, int, int]:
        """Wins, losses and pushes, using the ledger's existing result values."""

        if not funded_ids:
            return (0, 0, 0)
        rows = self.session.execute(
            select(PositionSettlement.result, func.count())
            .select_from(PositionSettlement)
            .join(OfficialAspPosition, OfficialAspPosition.id == PositionSettlement.position_id)
            .where(OfficialAspPosition.shadow_experiment_id.in_(funded_ids))
            .group_by(PositionSettlement.result)
        ).all()
        counts = {row[0]: int(row[1]) for row in rows}
        return (
            counts.get(PositionResult.WIN, 0),
            counts.get(PositionResult.LOSS, 0),
            counts.get(PositionResult.PUSH, 0) + counts.get(PositionResult.VOID, 0),
        )

    def _equity_curve(
        self, account: ExperimentAccount, contribution: float, funded_ids: list[UUID],
    ) -> list[dict[str, Any]]:
        """Realized bankroll after each settlement, starting at the contribution.

        The curve begins on the account's actual tracking start, never on the
        configured season opening — drawing a flat line back to March for a
        season that began in September would be inventing performance for
        months that were never traded.
        """

        if not account.tracking_started_on or contribution <= 0:
            return []
        points: list[dict[str, Any]] = [{
            "date": account.tracking_started_on.isoformat(),
            "bankroll": round(contribution, 2),
            "settlements": 0,
        }]
        if not funded_ids:
            return points
        rows = list(self.session.execute(
            select(PositionSettlement.settled_at, PositionSettlement.profit_loss)
            .select_from(PositionSettlement)
            .join(OfficialAspPosition, OfficialAspPosition.id == PositionSettlement.position_id)
            .where(OfficialAspPosition.shadow_experiment_id.in_(funded_ids))
            .order_by(PositionSettlement.settled_at)
        ).all())
        running = contribution
        by_day: dict[str, tuple[float, int]] = {}
        for settled_at, profit_loss in rows:
            running += float(profit_loss)
            day = settled_at.date().isoformat()
            previous = by_day.get(day, (running, 0))
            by_day[day] = (running, previous[1] + 1)
        for day in sorted(by_day):
            bankroll, count = by_day[day]
            points.append({"date": day, "bankroll": round(bankroll, 2), "settlements": count})
        return points

    def _positions(self, funded_ids: list[UUID]) -> list[dict[str, Any]]:
        """Open and settled positions for this account, newest first."""

        if not funded_ids:
            return []
        rows = list(self.session.execute(
            select(
                OfficialAspPosition.id,
                OfficialAspPosition.placed_at,
                OfficialAspPosition.stake,
                OfficialAspPosition.decimal_odds,
                OfficialAspPosition.status,
                OfficialAspPosition.result,
                OfficialAspPosition.shadow_experiment_id,
                PositionSettlement.profit_loss,
                PositionSettlement.settled_at,
            )
            .select_from(OfficialAspPosition)
            .outerjoin(PositionSettlement, PositionSettlement.position_id == OfficialAspPosition.id)
            .where(OfficialAspPosition.shadow_experiment_id.in_(funded_ids))
            .order_by(OfficialAspPosition.placed_at.desc())
        ).all())
        era_names = {
            experiment.id: (experiment.model_era_id or experiment.controlling_engine)
            for experiment in self.session.scalars(
                select(ShadowExperiment).where(ShadowExperiment.id.in_(funded_ids))
            )
        }
        # The ledger showed a bare decimal 2.150. The side, the American price,
        # the book and the provider are entry terms, read from the position's own
        # snapshot and decision -- never from a later quote for the same game.
        from app.services.position_entry_terms import entry_terms

        terms = entry_terms(self.session, [row[0] for row in rows])
        return [
            {
                "id": str(row[0]),
                "entry": terms.get(str(row[0])),
                "placed_at": row[1].isoformat(),
                "stake": float(row[2]),
                "decimal_odds": float(row[3]),
                "status": row[4].value if hasattr(row[4], "value") else str(row[4]),
                "result": row[5].value if hasattr(row[5], "value") else str(row[5]),
                "era": era_names.get(row[6]),
                "profit_loss": None if row[7] is None else float(row[7]),
                "settled_at": None if row[8] is None else row[8].isoformat(),
            }
            for row in rows
        ]

    # -- program overview --------------------------------------------------

    def overview(self, *, today: date) -> list[AccountLedger | ExperimentAccount]:
        """One entry per program sport: a ledger if funded, the account if not."""

        results: list[AccountLedger | ExperimentAccount] = []
        for sport in supported_sports():
            account = self.current_account(sport, today=today)
            if account is None:
                continue
            results.append(self.ledger(account))
        return results

    def sync_status(self, account: ExperimentAccount, *, today: date) -> None:
        """Move a funded account through its season states.

        A season past its window stops taking new positions but is not archived
        while anything is still open — "closed" and "finished" are different
        facts, and collapsing them would hide an unsettled liability.
        """

        if account.status is AccountStatus.PLANNED:
            return
        if today <= account.season_closes_on:
            account.status = AccountStatus.ACTIVE
            return
        still_open = self.reserved_stake(account) > 0
        account.status = (
            AccountStatus.CLOSED_FOR_NEW_POSITIONS if still_open else AccountStatus.ARCHIVED
        )


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class ProvisionReport:
    """What a provisioning pass actually did. Empty lists mean a no-op run."""

    accounts_created: list[str] = field(default_factory=list)
    memberships_created: list[str] = field(default_factory=list)
    accounts_funded: list[str] = field(default_factory=list)
    accounts_reviewed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.accounts_created or self.memberships_created or self.accounts_funded)

    def as_dict(self) -> dict[str, Any]:
        return {
            "accounts_created": self.accounts_created,
            "memberships_created": self.memberships_created,
            "accounts_funded": self.accounts_funded,
            "accounts_reviewed": self.accounts_reviewed,
            "notes": self.notes,
            "changed": self.changed,
        }


class ExperimentAccountProvisioner:
    """Brings the program's accounts into line with what actually exists.

    Idempotent from end to end: every step asks whether its row is already
    there. Running it once and running it a hundred times produce the same
    database, which is what lets the scheduled opening job run on a plain
    interval instead of needing a one-shot trigger nobody can re-run safely.

    It never invents history. An account for a season already underway is
    funded with the *real* first funded era's start date, and marked partial,
    rather than being backdated to the configured season opening and pretending
    to have traded months it did not.
    """

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.accounts = ExperimentAccountService(session, settings)

    def run(
        self,
        *,
        today: date,
        now: datetime,
        executed_by: str,
        job_run_id: UUID | None = None,
        open_due_seasons: bool = True,
    ) -> ProvisionReport:
        report = ProvisionReport()
        self._attach_existing_experiments(report, now=now)
        self._ensure_program_accounts(report, today=today)
        if open_due_seasons:
            self._open_due_accounts(
                report, today=today, now=now, executed_by=executed_by, job_run_id=job_run_id,
            )
        self._refresh_statuses(report, today=today)
        return report

    def _attach_existing_experiments(self, report: ProvisionReport, *, now: datetime) -> None:
        """Give every registered era a season account, by its own start date.

        An era's season comes from when it started, never from today, so a
        backfill run in September cannot file a March era into the wrong
        season.
        """

        from app.experiments.season_calendar import season_for_date

        for experiment in self.session.scalars(
            select(ShadowExperiment).order_by(ShadowExperiment.version)
        ):
            if self.accounts.membership(experiment.id) is not None:
                continue
            sport = (experiment.sport or "MLB").upper()
            try:
                window = season_for_date(sport, experiment.start_date)
            except KeyError:
                report.notes.append(
                    f"Experiment v{experiment.version} is for {sport}, which is not a "
                    "program sport; it was left unattached rather than filed arbitrarily."
                )
                continue
            account = self.accounts.ensure_account(sport, window.season_id)
            _, created = self.accounts.attach(account, experiment, now=now)
            if created:
                role, _ = self.accounts.classify(experiment)
                report.memberships_created.append(
                    f"{sport} {window.season_id} <- v{experiment.version} ({role.value})"
                )

    def _ensure_program_accounts(self, report: ProvisionReport, *, today: date) -> None:
        """Every program sport has an account for its current season.

        A sport with nothing to trade still gets one, as PLANNED. That is the
        row the page reads to say "$1,000 planned, not started" with a reason,
        instead of leaving a gap the reader has to interpret.
        """

        for sport in supported_sports():
            window = program_season_for(sport, today)
            existing = self.accounts.account(sport, window.season_id)
            if existing is not None:
                report.accounts_reviewed.append(f"{sport} {window.season_id}")
                continue
            self.accounts.ensure_account(sport, window.season_id)
            report.accounts_created.append(f"{sport} {window.season_id}")

    def _open_due_accounts(
        self,
        report: ProvisionReport,
        *,
        today: date,
        now: datetime,
        executed_by: str,
        job_run_id: UUID | None,
    ) -> None:
        """Fund every account whose season has opened and which has something to run.

        Two conditions, both required. The season must have reached its
        configured opening date — funding a season before it starts would put
        capital against games that cannot exist. And the account must have at
        least one funded era attached, because an account with no era has no
        way to place a position and funding it would report capital at risk
        that nothing can reach.
        """

        for account in self.session.scalars(select(ExperimentAccount)):
            if self.accounts.funding(account) is not None:
                continue
            if today < account.season_opens_on:
                continue
            funded_eras = [
                experiment for experiment, role in self.accounts.member_experiments(
                    account, roles=(MembershipRole.FUNDED_ERA,)
                )
            ]
            if not funded_eras:
                continue
            # The true start: the earliest funded era's own registered start
            # date, not today and not the configured season opening.
            tracking_start = min(experiment.start_date for experiment in funded_eras)
            partial = tracking_start > account.season_opens_on
            note = (
                "Opening contribution for a season already underway; tracking "
                f"starts at the first funded era's own start date ({tracking_start}), "
                "and no result is claimed before it."
            ) if partial else "Season opening contribution."
            self.accounts.open_season(
                account,
                now=now,
                executed_by=executed_by,
                tracking_started_on=tracking_start,
                job_run_id=job_run_id,
                note=note,
            )
            report.accounts_funded.append(
                f"{account.sport} {account.season_id} "
                f"({PROGRAM_CONTRIBUTION:.2f}, tracking from {tracking_start}"
                f"{', partial season' if partial else ''})"
            )

    def _refresh_statuses(self, report: ProvisionReport, *, today: date) -> None:
        for account in self.session.scalars(select(ExperimentAccount)):
            self.accounts.sync_status(account, today=today)


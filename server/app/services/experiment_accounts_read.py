"""Read model for the season-account experiment page.

Its whole job is to present the account ledger without letting a planned
allocation be mistaken for funded capital. Two mechanisms do that, and both are
structural rather than presentational:

- A planned account's money fields are ``None``. Not zero — ``None``. A client
  summing ``original_contribution`` across accounts gets the funded total
  whatever it does, because there is nothing there to add.
- The program summary reports funded and planned in separate fields, names the
  accounts each covers, and carries a sentence saying what the planned figure
  is. "$5,000" appears only as ``planned_allocation``.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.experiments.season_calendar import PROGRAM_CONTRIBUTION, rule_for
from app.models import AccountStatus, ExperimentAccount, MembershipRole
from app.schemas.experiment_accounts import (
    AccountPosition,
    EquityPoint,
    EraSummary,
    ExperimentAccountsData,
    ProgramSummary,
    SeasonAccount,
    SeasonBoundary,
)
from app.services.experiment_accounts import AccountLedger, ExperimentAccountService


class ExperimentAccountReadService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.accounts = ExperimentAccountService(session, settings)

    # -- program overview --------------------------------------------------

    def program(self, *, today: date) -> ExperimentAccountsData:
        ledgers = self.accounts.overview(today=today)
        accounts = [self._account(ledger) for ledger in ledgers]
        return ExperimentAccountsData(
            data_mode="live" if accounts else "empty",
            as_of=today.isoformat(),
            program_contribution=PROGRAM_CONTRIBUTION,
            summary=self._summary(ledgers),
            accounts=accounts,
        )

    def sport(self, sport: str, *, today: date) -> ExperimentAccountsData:
        """Every season this sport has held, newest first."""

        rows = self.accounts.accounts_for(sport)
        ledgers = [self.accounts.ledger(account) for account in rows]
        accounts = [self._account(ledger, detailed=True) for ledger in ledgers]
        return ExperimentAccountsData(
            data_mode="live" if accounts else "empty",
            as_of=today.isoformat(),
            program_contribution=PROGRAM_CONTRIBUTION,
            summary=self._summary(ledgers),
            accounts=accounts,
        )

    def season(self, sport: str, season_id: str, *, today: date) -> ExperimentAccountsData:
        account = self.accounts.account(sport, season_id)
        if account is None:
            return ExperimentAccountsData(
                data_mode="empty", as_of=today.isoformat(),
                program_contribution=PROGRAM_CONTRIBUTION,
                summary=self._summary([]), accounts=[],
            )
        ledger = self.accounts.ledger(account)
        return ExperimentAccountsData(
            data_mode="live", as_of=today.isoformat(),
            program_contribution=PROGRAM_CONTRIBUTION,
            summary=self._summary([ledger]),
            accounts=[self._account(ledger, detailed=True)],
        )

    # -- shaping -----------------------------------------------------------

    def _account(self, ledger: AccountLedger, *, detailed: bool = False) -> SeasonAccount:
        account: ExperimentAccount = ledger.account
        rule = rule_for(account.sport)
        boundary = SeasonBoundary(
            opens_on=account.season_opens_on.isoformat(),
            closes_on=account.season_closes_on.isoformat(),
            basis=account.boundary_basis,
            convention=account.boundary_convention,
        )
        # The controlling era is the LATEST active funded era, not the first.
        # A predecessor keeps is_active so its open positions continue to
        # settle, so "first active" would name the retired era.
        controlling = next(
            (
                era for era in reversed(ledger.eras)
                if era.is_active and era.role is MembershipRole.FUNDED_ERA
            ),
            None,
        )
        controlling_id = controlling.experiment_id if controlling else None
        eras = [
            EraSummary(
                experiment_id=str(era.experiment_id),
                name=era.name,
                version=era.version,
                model_era_id=era.model_era_id,
                controlling_engine=era.controlling_engine,
                decision_policy=era.decision_policy,
                started_on=era.started_on.isoformat(),
                role=era.role.value,
                role_basis=self._role_basis(era.experiment_id),
                is_active=era.is_active,
                is_controlling=era.experiment_id == controlling_id,
                settled_net_pl=era.settled_net_pl,
                settled_stake=era.settled_stake,
                reserved_stake=era.reserved_stake,
                open_positions=era.open_positions,
                settled_positions=era.settled_positions,
            )
            for era in ledger.eras
        ]
        base = SeasonAccount(
            sport=account.sport,
            sport_label=rule.display_name,
            season_id=account.season_id,
            season_label=account.season_label,
            status=account.status.value,
            boundary=boundary,
            planned_contribution=float(account.planned_contribution),
            status_note=account.status_note,
            partial_season=bool(account.partial_season),
            eras=eras if detailed else [],
        )

        if account.status is AccountStatus.PLANNED or not ledger.is_funded:
            # Nothing else is populated. A planned account has no capital, no
            # record and no curve, and leaving those fields null is what stops
            # a client treating its planned $1,000 as money.
            return base

        base.original_contribution = ledger.original_contribution
        base.realized_bankroll = ledger.realized_bankroll
        base.reserved_stake = ledger.reserved_stake
        base.available_capital = ledger.available_capital
        base.settled_net_pl = ledger.settled_net_pl
        base.settled_stake = ledger.settled_stake
        base.season_return_pct = ledger.season_return_pct
        base.settled_stake_roi_pct = ledger.settled_stake_roi_pct
        base.tracking_started_on = (
            account.tracking_started_on.isoformat() if account.tracking_started_on else None
        )
        base.funded_at = ledger.funded_at.isoformat() if ledger.funded_at else None
        base.record = ledger.record
        base.wins, base.losses, base.pushes = ledger.wins, ledger.losses, ledger.pushes
        base.open_positions = ledger.open_positions
        base.settled_positions = ledger.settled_positions
        base.controlling_engine = controlling.controlling_engine if controlling else None
        base.controlling_era_id = controlling.model_era_id if controlling else None
        base.equity_curve = [EquityPoint(**point) for point in ledger.equity_curve]
        if detailed:
            base.positions = [AccountPosition(**row) for row in ledger.positions]
        return base

    def _role_basis(self, experiment_id: Any) -> str:
        membership = self.accounts.membership(experiment_id)
        return membership.role_basis if membership else ""

    def _summary(self, ledgers: list[AccountLedger]) -> ProgramSummary:
        funded = [ledger for ledger in ledgers if ledger.is_funded]
        planned = [ledger for ledger in ledgers if not ledger.is_funded]
        planned_total = sum(float(l.account.planned_contribution) for l in planned)
        funded_keys = [f"{l.account.sport} {l.account.season_id}" for l in funded]
        planned_keys = [f"{l.account.sport} {l.account.season_id}" for l in planned]
        return ProgramSummary(
            funded_accounts=len(funded),
            planned_accounts=len(planned),
            funded_contribution=round(sum(l.original_contribution for l in funded), 2),
            funded_realized_bankroll=round(sum(l.realized_bankroll for l in funded), 2),
            funded_settled_net_pl=round(sum(l.settled_net_pl for l in funded), 2),
            funded_reserved_stake=round(sum(l.reserved_stake for l in funded), 2),
            funded_available_capital=round(sum(l.available_capital for l in funded), 2),
            planned_allocation=round(planned_total, 2),
            funded_account_keys=funded_keys,
            planned_account_keys=planned_keys,
            note=(
                f"Funded totals cover {len(funded)} account(s): "
                f"{', '.join(funded_keys) if funded_keys else 'none'}. "
                f"The planned allocation of {planned_total:,.2f} is proposed paper "
                f"capital for {len(planned)} sport(s) that have not started "
                f"({', '.join(planned_keys) if planned_keys else 'none'}); it is not "
                "funded, not available today, and is never added to the funded figures."
            ),
        )

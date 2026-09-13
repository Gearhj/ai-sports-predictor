"""Recorded tickets, their performance, and how they sit against the one account.

THREE SEPARATIONS THAT ARE STRUCTURAL, NOT PRESENTATIONAL
---------------------------------------------------------
*   **Parlay exposure is not straight-bet exposure.** Parlay tickets live in
    their own table and are summarised on their own. They are never added into
    the straight-bet win rate, staked total, or position count, because a
    three-leg ticket is one decision and folding it in as three would misstate
    both counts and the average stake.
*   **Estimated-price performance is not recorded-quote performance.** A ticket
    priced from the product of a book's leg odds was never offered at that
    price. Its results are reported in their own column and never averaged with
    tickets recorded against a genuine combined quote.
*   **Funded is not research.** Only funded tickets touch the season account's
    capital. Unfunded research tickets are tracked, scored and reported, and
    contribute nothing to bankroll arithmetic.

SHARED LEGS
-----------
Two tickets that both contain "NYY ML" are not two independent bets on it. The
summary lists each selection that appears on more than one open ticket and the
total stake exposed to it, because the alternative -- three $1 tickets that all
need the same leg -- reads as diversification while being a single $3 bet on one
game.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import (
    AccountStatus,
    ExperimentAccount,
    ParlayTicket,
    ParlayTicketLeg,
    PositionResult,
    PositionStatus,
)
from app.models.parlay import FUNDING_SEASON_ACCOUNT
from app.parlay.pricing import PRICE_ESTIMATED_FROM_LEGS, PRICE_RECORDED_COMBINED

SCHEMA_VERSION = "AISP_MLB_PARLAY_TICKETS_V1"


class ParlayPerformanceReadService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def summary(self) -> dict[str, Any]:
        tickets = list(self.session.scalars(
            select(ParlayTicket).order_by(ParlayTicket.recorded_at_utc.desc())))
        legs_by_ticket: dict[Any, list[ParlayTicketLeg]] = defaultdict(list)
        for leg in self.session.scalars(
            select(ParlayTicketLeg).order_by(ParlayTicketLeg.ordinal)
        ):
            legs_by_ticket[leg.ticket_id].append(leg)

        rows = [self._ticket(ticket, legs_by_ticket[ticket.id]) for ticket in tickets]
        return {
            "schema_version": SCHEMA_VERSION,
            "tickets": rows,
            "performance": {
                "by_price_provenance": {
                    PRICE_ESTIMATED_FROM_LEGS: self._bucket(
                        tickets, lambda t: t.price_provenance == PRICE_ESTIMATED_FROM_LEGS),
                    PRICE_RECORDED_COMBINED: self._bucket(
                        tickets, lambda t: t.price_provenance == PRICE_RECORDED_COMBINED),
                },
                "by_funding": {
                    "FUNDED_SEASON_ACCOUNT": self._bucket(
                        tickets, lambda t: t.funding == FUNDING_SEASON_ACCOUNT),
                    "RESEARCH_UNFUNDED": self._bucket(
                        tickets, lambda t: t.funding != FUNDING_SEASON_ACCOUNT),
                },
                "separation_note": (
                    "Parlay results are reported here only. They are not added "
                    "into straight-bet performance: one ticket is one decision, "
                    "and counting its legs as separate positions would misstate "
                    "both the position count and the average stake."
                ),
            },
            "shared_legs": self._shared_legs(tickets, legs_by_ticket),
            "account_reconciliation": self._reconciliation(tickets),
        }

    # -- pieces ------------------------------------------------------------

    def _ticket(
        self, ticket: ParlayTicket, legs: list[ParlayTicketLeg],
    ) -> dict[str, Any]:
        return {
            "id": str(ticket.id),
            "ticket_sha256": ticket.ticket_sha256,
            "sportsbook": ticket.sportsbook,
            "leg_count": ticket.leg_count,
            "stake": float(ticket.stake),
            "funding": ticket.funding,
            "combined_decimal_odds": ticket.combined_decimal_odds,
            "price_provenance": ticket.price_provenance,
            "price_is_an_offer": ticket.price_provenance == PRICE_RECORDED_COMBINED,
            "potential_payout": round(
                float(ticket.stake) * ticket.combined_decimal_odds, 2),
            "probability_snapshot": ticket.probability_snapshot,
            "settlement_rules": ticket.settlement_rules,
            "recorded_at_utc": ticket.recorded_at_utc.isoformat(),
            "earliest_cutoff_utc": ticket.earliest_cutoff_utc.isoformat(),
            "status": ticket.status.value,
            "result": ticket.result.value,
            "settled_at": ticket.settled_at.isoformat() if ticket.settled_at else None,
            "payout": float(ticket.payout) if ticket.payout is not None else None,
            "profit_loss": (
                float(ticket.profit_loss) if ticket.profit_loss is not None else None),
            "settled_decimal_odds": ticket.settled_decimal_odds,
            "settlement_note": ticket.settlement_note,
            "legs": [
                {
                    "ordinal": leg.ordinal,
                    "game_id": str(leg.game_id),
                    "market": leg.market,
                    "selection": leg.selection,
                    "line": leg.line,
                    "decimal_odds": leg.decimal_odds,
                    "american_odds": leg.american_odds,
                    "sportsbook": leg.sportsbook,
                    "win_probability_pct": leg.win_probability_pct,
                    "push_probability_pct": leg.push_probability_pct,
                    "loss_probability_pct": leg.loss_probability_pct,
                    "forecast_identity": leg.forecast_identity,
                    "engine_id": leg.engine_id,
                    "starts_at": leg.starts_at.isoformat(),
                    "result": leg.result.value,
                    "settled_note": leg.settled_note,
                }
                for leg in legs
            ],
        }

    @staticmethod
    def _bucket(tickets: list[ParlayTicket], predicate: Any) -> dict[str, Any]:
        chosen = [t for t in tickets if predicate(t)]
        settled = [t for t in chosen if t.status is PositionStatus.SETTLED]
        staked = sum(float(t.stake) for t in settled)
        net = sum(float(t.profit_loss or 0.0) for t in settled)
        return {
            "tickets": len(chosen),
            "open": len(chosen) - len(settled),
            "settled": len(settled),
            "wins": sum(1 for t in settled if t.result is PositionResult.WIN),
            "losses": sum(1 for t in settled if t.result is PositionResult.LOSS),
            "pushes": sum(1 for t in settled if t.result is PositionResult.PUSH),
            "settled_stake": round(staked, 2),
            "net_profit_loss": round(net, 2),
            # Only meaningful once something has settled; None rather than 0.0,
            # so an untouched bucket cannot read as a break-even record.
            "return_on_settled_stake_pct": (
                round(net / staked * 100.0, 2) if staked > 0 else None),
        }

    def _shared_legs(
        self, tickets: list[ParlayTicket],
        legs_by_ticket: dict[Any, list[ParlayTicketLeg]],
    ) -> list[dict[str, Any]]:
        open_tickets = {
            t.id: t for t in tickets if t.status is PositionStatus.OPEN}
        exposure: dict[tuple[str, str], dict[str, Any]] = {}
        for ticket_id, legs in legs_by_ticket.items():
            ticket = open_tickets.get(ticket_id)
            if ticket is None:
                continue
            for leg in legs:
                key = (str(leg.game_id), leg.selection)
                entry = exposure.setdefault(key, {
                    "game_id": str(leg.game_id),
                    "selection": leg.selection,
                    "market": leg.market,
                    "ticket_count": 0,
                    "total_stake_exposed": 0.0,
                    "ticket_ids": [],
                })
                entry["ticket_count"] += 1
                entry["total_stake_exposed"] += float(ticket.stake)
                entry["ticket_ids"].append(str(ticket_id))
        shared = [
            {**entry, "total_stake_exposed": round(entry["total_stake_exposed"], 2)}
            for entry in exposure.values() if entry["ticket_count"] > 1
        ]
        shared.sort(key=lambda row: -row["total_stake_exposed"])
        return shared

    def _reconciliation(self, tickets: list[ParlayTicket]) -> dict[str, Any]:
        """Where parlay money sits relative to the one season account.

        Reserved is stake on open funded tickets -- committed but not yet
        resolved. Realised is settled funded profit and loss. Both are reported
        as parlay figures against the same account, never merged into the
        straight-bet totals that account already reports.
        """

        account = self.session.scalar(
            select(ExperimentAccount)
            .where(ExperimentAccount.sport == "MLB",
                   ExperimentAccount.status == AccountStatus.ACTIVE)
            .order_by(ExperimentAccount.created_at.desc())
            .limit(1)
        )
        funded = [t for t in tickets if t.funding == FUNDING_SEASON_ACCOUNT]
        reserved = sum(
            float(t.stake) for t in funded if t.status is PositionStatus.OPEN)
        realised = sum(
            float(t.profit_loss or 0.0) for t in funded
            if t.status is PositionStatus.SETTLED)
        return {
            "account_id": str(account.id) if account else None,
            "account_present": account is not None,
            "funded_tickets": len(funded),
            "parlay_reserved_stake": round(reserved, 2),
            "parlay_realized_profit_loss": round(realised, 2),
            "unfunded_research_tickets": len(tickets) - len(funded),
            "note": (
                "These are parlay figures against the existing MLB season "
                "account. The program holds one $1,000 contribution; no second "
                "bankroll was created and no contribution was added. Straight-bet "
                "figures on the experiment page are unchanged by anything here."
            ),
        }


__all__ = ["SCHEMA_VERSION", "ParlayPerformanceReadService"]

"""Recording a virtual parlay ticket, and settling it once against a governed final.

RECORDING IS DELIBERATE AND IDEMPOTENT
--------------------------------------
"Deliberate" means the caller supplies the exact combination and stake, the
service re-derives the price from stored evidence rather than trusting numbers
posted to it, and a ticket past its earliest cutoff is refused. "Idempotent"
means the frozen content is hashed to ``ticket_sha256``, that column is unique,
and a second identical request returns the first ticket instead of writing a
second. Neither property depends on the caller behaving well.

The client cannot dictate the price. It names the legs; the server looks up each
leg's stored quote and the forecast's own probability and prices from those. A
posted probability or price would make the ticket a record of what the browser
believed, not of what AISP held.

SETTLEMENT RULES ARE FROZEN ON THE TICKET
-----------------------------------------
``settlement_rules`` is written at recording and read at settlement. A later
change to how AISP treats a pushed leg cannot reach back into a ticket already
recorded; the ticket settles under the rules it was recorded under.

The rules themselves, v1:

*   A leg wins, loses or pushes against the governed final for its game.
*   A pushed or voided leg is **removed**, and the ticket reprices over the legs
    that remain -- it is not a loss.
*   Any losing leg makes the ticket a loss.
*   All legs pushed means the stake is returned: result PUSH, profit zero.
*   Otherwise the ticket wins at the product of the surviving legs' prices.

WHERE THE REDUCED PAYOUT IS HONESTLY UNKNOWN
--------------------------------------------
An estimated ticket was priced as the product of that book's leg odds, so
removing a leg and multiplying the rest is exactly the same construction and
stays coherent.

A ticket recorded against a real full-ticket combined quote is different. That
quote prices the ticket as a whole; it does not say what the book pays when one
leg pushes, and books differ. So when a recorded-quote ticket has a pushed or
voided leg, this service does **not** invent a reduction. It settles the ticket
as requiring the book's own reduction rule, records exactly that, and leaves the
money unbooked rather than guessing in the ticket's favour.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import (
    Game,
    GameFinalOutcome,
    GameTeam,
    MarketOutcome,
    ParlayTicket,
    ParlayTicketLeg,
    PositionResult,
    PositionStatus,
)
from app.models.parlay import FUNDING_RESEARCH_ONLY, FUNDING_SEASON_ACCOUNT
from app.parlay.pricing import (
    PRICE_ESTIMATED_FROM_LEGS,
    PRICE_RECORDED_COMBINED,
    Leg,
    ParlayPricingError,
    price_ticket,
)

SETTLEMENT_RULES_VERSION = "AISP_PARLAY_SETTLEMENT_RULES_V1"

#: Result codes this module writes beyond the shared PositionResult enum, kept
#: in the note rather than the enum so no existing consumer has to learn a value.
NOTE_AWAITING_BOOK_REDUCTION = (
    "A leg pushed or was voided, and this ticket was recorded against a real "
    "full-ticket combined quote. That quote does not establish what the book pays "
    "once a leg is removed, and books differ, so no reduced payout is assumed. "
    "The ticket stays open pending the book's own reduction rule."
)


class ParlayTicketError(ValueError):
    """A ticket cannot be recorded from the evidence and rules given."""


def settlement_rules() -> dict[str, Any]:
    """The rules frozen onto every ticket recorded by this build."""

    return {
        "version": SETTLEMENT_RULES_VERSION,
        "leg_outcomes": "WIN, LOSS or PUSH against the governed final outcome record.",
        "pushed_leg": (
            "Removed from the ticket. The ticket reprices over the remaining "
            "legs. A pushed leg is not a loss."
        ),
        "voided_leg": "Treated as a pushed leg: removed, not lost.",
        "any_losing_leg": "The ticket loses. Profit is minus the stake.",
        "all_legs_pushed": "Stake returned. Result PUSH, profit zero.",
        "reduced_payout_basis": {
            PRICE_ESTIMATED_FROM_LEGS: (
                "Product of the surviving legs' recorded decimal odds at the same "
                "book. This is the same construction the estimate was built from."
            ),
            PRICE_RECORDED_COMBINED: (
                "Not determined by AISP. A recorded full-ticket quote does not "
                "establish the payout after a leg is removed."
            ),
        },
        "result_source": (
            "game_final_outcomes, latest revision, outcome_status FINAL. The "
            "revision is recorded on settlement."
        ),
        "independence": (
            "Combined probabilities on this ticket assume cross-game "
            "independence. That assumption is recorded, not measured."
        ),
    }


@dataclass(frozen=True)
class LegRequest:
    """What a caller may specify. Deliberately small: identity, not numbers."""

    game_id: str
    market: str           # MONEYLINE | TOTAL
    selection: str        # "NYY ML" | "OVER 8.5"
    sportsbook: str


def ticket_digest(payload: dict[str, Any]) -> str:
    """Content address of a frozen ticket.

    Sorted keys and compact separators, so the same decision always hashes the
    same way regardless of dict ordering, and the digest is over the decision
    -- legs, stake, prices, probabilities, funding -- not over the clock, which
    would make every re-record a different ticket.
    """

    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass
class RecordOutcome:
    ticket: ParlayTicket
    created: bool
    detail: str


class ParlayTicketService:
    def __init__(self, session: Session) -> None:
        self.session = session

    # -- recording ---------------------------------------------------------

    def record(
        self,
        legs: list[Leg],
        *,
        stake: float,
        account_id: uuid.UUID | None,
        funded: bool,
        recorded_combined_decimal_odds: float | None = None,
        now: datetime | None = None,
    ) -> RecordOutcome:
        """Freeze one ticket. Returns the existing one when it already exists."""

        now = now or datetime.now(UTC)
        if stake <= 0:
            raise ParlayTicketError("stake must be positive")
        if funded and account_id is None:
            raise ParlayTicketError(
                "a funded ticket must name the season account its stake is drawn "
                "against; there is no second parlay bankroll"
            )
        try:
            price = price_ticket(
                legs,
                recorded_combined_decimal_odds=recorded_combined_decimal_odds,
            )
        except ParlayPricingError as exc:
            raise ParlayTicketError(str(exc)) from exc

        cutoffs = [_aware(_parse(leg.starts_at)) for leg in legs]
        if any(value is None for value in cutoffs):
            raise ParlayTicketError("every leg must carry its game's start time")
        from app.services.shadow_experiment_service import FINAL_PREGAME_CUTOFF_MINUTES
        from datetime import timedelta
        earliest = min(cutoffs) - timedelta(minutes=FINAL_PREGAME_CUTOFF_MINUTES)
        if now >= earliest:
            raise ParlayTicketError(
                f"the earliest leg is inside the {FINAL_PREGAME_CUTOFF_MINUTES}-minute "
                f"pregame cutoff ({earliest.isoformat()}); this ticket can no longer "
                "be recorded"
            )

        ordered = sorted(legs, key=lambda leg: (leg.game_id, leg.market, leg.selection))
        frozen = {
            "league": "MLB",
            "stake": round(float(stake), 2),
            "funding": FUNDING_SEASON_ACCOUNT if funded else FUNDING_RESEARCH_ONLY,
            "account_id": str(account_id) if account_id else None,
            "sportsbook": ordered[0].sportsbook,
            "price": price.as_dict(),
            "settlement_rules": settlement_rules(),
            "legs": [leg.as_dict() for leg in ordered],
        }
        digest = ticket_digest(frozen)

        existing = self.session.scalar(
            select(ParlayTicket).where(ParlayTicket.ticket_sha256 == digest))
        if existing is not None:
            return RecordOutcome(
                ticket=existing, created=False,
                detail=(
                    "This exact ticket was already recorded at "
                    f"{existing.recorded_at_utc.isoformat()}. Nothing was written."
                ),
            )

        ticket = ParlayTicket(
            ticket_sha256=digest,
            ticket_key="|".join(
                f"{leg.game_id}:{leg.market}:{leg.selection}:{leg.sportsbook}"
                for leg in ordered),
            league="MLB",
            sportsbook=ordered[0].sportsbook,
            leg_count=len(ordered),
            account_id=account_id if funded else None,
            funding=FUNDING_SEASON_ACCOUNT if funded else FUNDING_RESEARCH_ONLY,
            stake=round(float(stake), 2),
            combined_decimal_odds=price.combined_decimal_odds,
            price_provenance=price.price_provenance,
            probability_snapshot=price.as_dict(),
            settlement_rules=settlement_rules(),
            recorded_at_utc=now,
            earliest_cutoff_utc=earliest,
            status=PositionStatus.OPEN,
            result=PositionResult.PENDING,
        )
        self.session.add(ticket)
        self.session.flush()
        for ordinal, leg in enumerate(ordered, start=1):
            self.session.add(ParlayTicketLeg(
                ticket_id=ticket.id,
                ordinal=ordinal,
                game_id=uuid.UUID(leg.game_id),
                market_outcome_id=(
                    uuid.UUID(leg.market_outcome_id) if leg.market_outcome_id else None),
                odds_snapshot_id=(
                    uuid.UUID(leg.odds_snapshot_id) if leg.odds_snapshot_id else None),
                market=leg.market,
                selection=leg.selection,
                line=leg.line,
                decimal_odds=leg.decimal_odds,
                american_odds=leg.american_odds,
                sportsbook=leg.sportsbook,
                quote_observed_at=_aware(_parse(leg.quote_observed_at)),
                starts_at=_aware(_parse(leg.starts_at)),
                forecast_identity=leg.forecast_id,
                engine_id=leg.engine_id,
                win_probability_pct=round(leg.p_win * 100.0, 4),
                push_probability_pct=round(leg.p_push * 100.0, 4),
                loss_probability_pct=round(leg.p_loss * 100.0, 4),
                result=PositionResult.PENDING,
            ))
        self.session.flush()
        return RecordOutcome(
            ticket=ticket, created=True,
            detail=f"Recorded {len(ordered)} legs at {ordered[0].sportsbook}.",
        )

    # -- settlement --------------------------------------------------------

    def settle_open_tickets(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Settle every open ticket whose games all have a governed final.

        A ticket with any leg still missing evidence stays pending. It is never
        partially settled and never settled twice: the status guard is the same
        one the straight-bet ledger uses.
        """

        now = now or datetime.now(UTC)
        settled = 0
        pending = 0
        awaiting_rule = 0
        tickets = list(self.session.scalars(
            select(ParlayTicket).where(ParlayTicket.status == PositionStatus.OPEN)))
        for ticket in tickets:
            outcome = self._settle_one(ticket, now=now)
            if outcome == "SETTLED":
                settled += 1
            elif outcome == "AWAITING_BOOK_REDUCTION":
                awaiting_rule += 1
            else:
                pending += 1
        return {
            "open_tickets": len(tickets),
            "settled": settled,
            "pending_missing_evidence": pending,
            "awaiting_book_reduction_rule": awaiting_rule,
        }

    def _settle_one(self, ticket: ParlayTicket, *, now: datetime) -> str:
        legs = list(self.session.scalars(
            select(ParlayTicketLeg)
            .where(ParlayTicketLeg.ticket_id == ticket.id)
            .order_by(ParlayTicketLeg.ordinal)))
        finals: dict[uuid.UUID, dict[str, Any]] = {}
        for leg in legs:
            final = self._governed_final(leg.game_id)
            if final is None:
                return "PENDING"
            finals[leg.game_id] = final

        rules = dict(ticket.settlement_rules or {})
        surviving_odds = 1.0
        any_loss = False
        all_push = True
        notes: list[str] = []
        for leg in legs:
            final = finals[leg.game_id]
            result, note = self._leg_result(leg, final)
            leg.result = result
            leg.settled_home_score = final["home_score"]
            leg.settled_away_score = final["away_score"]
            leg.settled_note = note
            notes.append(f"{leg.selection}: {note}")
            if result is PositionResult.LOSS:
                any_loss = True
                all_push = False
            elif result is PositionResult.WIN:
                all_push = False
                surviving_odds *= leg.decimal_odds

        stake = float(ticket.stake)
        removed = [leg for leg in legs if leg.result in
                   (PositionResult.PUSH, PositionResult.VOID)]

        if not any_loss and removed and ticket.price_provenance == PRICE_RECORDED_COMBINED:
            # The honest stop. A real combined quote does not tell us the
            # reduced payout, so nothing is booked.
            ticket.settlement_note = NOTE_AWAITING_BOOK_REDUCTION + " " + "; ".join(notes)
            self.session.flush()
            return "AWAITING_BOOK_REDUCTION"

        if any_loss:
            result = PositionResult.LOSS
            payout = 0.0
            profit = -stake
        elif all_push:
            result = PositionResult.PUSH
            payout = stake
            profit = 0.0
        else:
            result = PositionResult.WIN
            payout = stake * surviving_odds
            profit = payout - stake

        ticket.result = result
        ticket.status = PositionStatus.SETTLED
        ticket.settled_at = now
        ticket.payout = round(payout, 2)
        ticket.profit_loss = round(profit, 2)
        ticket.settled_decimal_odds = round(surviving_odds, 4) if not any_loss else None
        ticket.settlement_note = (
            ("; ".join(notes))
            + (f" Reduced to {len(legs) - len(removed)} of {len(legs)} legs; paid at "
               f"{surviving_odds:.4f}. Basis: "
               f"{(rules.get('reduced_payout_basis') or {}).get(ticket.price_provenance, 'n/a')}"
               if removed and not any_loss else "")
        )
        self.session.flush()
        return "SETTLED"

    def _leg_result(
        self, leg: ParlayTicketLeg, final: dict[str, Any],
    ) -> tuple[PositionResult, str]:
        """One leg against the governed final. Pushes are real outcomes here."""

        home, away = final["home_score"], final["away_score"]
        if leg.voided:
            return (PositionResult.VOID,
                    "voided before settlement; removed from the ticket")

        if leg.market == "TOTAL":
            total = home + away
            if leg.line is None:
                return PositionResult.VOID, "no line recorded on this leg; removed"
            side = leg.selection.split()[0].upper()
            if side not in {"OVER", "UNDER"}:
                return PositionResult.VOID, f"unrecognised total side {side!r}; removed"
            if float(total) == float(leg.line):
                # Only reachable on an integer line, and it is a push, not a
                # loss. Collapsing it into a loss is the error this branch exists
                # to prevent.
                return (PositionResult.PUSH,
                        f"total {total} equals the line {leg.line:g}; leg removed")
            went_over = total > leg.line
            won = went_over if side == "OVER" else not went_over
            return (
                PositionResult.WIN if won else PositionResult.LOSS,
                f"total {total} {'over' if went_over else 'under'} {leg.line:g}",
            )

        if leg.market != "MONEYLINE":
            return PositionResult.VOID, f"unrecognised market {leg.market!r}; removed"

        # Which team this leg actually backed, from the stored outcome rather
        # than from parsing the label -- the label is for the reader.
        #
        # Read through the ORM rather than raw SQL: a uuid bound as a string
        # matches on PostgreSQL and silently matches nothing on SQLite, where
        # the column is CHAR(32) with no dashes. That difference would make
        # every leg here unsettleable in tests while looking fine in production.
        team_id = self.session.scalar(
            select(MarketOutcome.participant_team_id)
            .where(MarketOutcome.id == leg.market_outcome_id)
        ) if leg.market_outcome_id else None
        home_team_id = self.session.scalar(
            select(GameTeam.team_id)
            .where(GameTeam.game_id == leg.game_id, GameTeam.is_home.is_(True))
        )
        if team_id is None or home_team_id is None:
            return (PositionResult.VOID,
                    "the team this leg backed cannot be resolved from stored rows; "
                    "removed rather than guessed")
        backed_home = str(team_id) == str(home_team_id)
        if home == away:
            # MLB plays to a decision, so this is evidence of a bad final rather
            # than a tie. It is voided, never silently scored as a loss.
            return (PositionResult.VOID,
                    f"the recorded final is tied {away}-{home}, which MLB does not "
                    "produce; leg removed pending corrected evidence")
        home_won = home > away
        won = home_won if backed_home else not home_won
        return (
            PositionResult.WIN if won else PositionResult.LOSS,
            f"final {away}-{home} (away-home)",
        )

    def _governed_final(self, game_id: uuid.UUID) -> dict[str, Any] | None:
        """The governed outcome record, latest revision, or nothing.

        Deliberately not the ``games`` row's own scores: that row is mutable
        operational state, while this is the revisioned record the rest of AISP
        scores against. A ticket settled off the wrong one could disagree with
        every other surface about the same game.
        """

        row = self.session.scalar(
            select(GameFinalOutcome)
            .where(GameFinalOutcome.game_id == game_id)
            .order_by(GameFinalOutcome.revision.desc())
            .limit(1)
        )
        if row is None or row.outcome_status != "FINAL":
            return None
        if row.home_score is None or row.away_score is None:
            return None
        return {
            "revision": row.revision,
            "outcome_status": row.outcome_status,
            "home_score": int(row.home_score),
            "away_score": int(row.away_score),
        }




__all__ = [
    "NOTE_AWAITING_BOOK_REDUCTION",
    "SETTLEMENT_RULES_VERSION",
    "LegRequest",
    "ParlayTicketError",
    "ParlayTicketService",
    "RecordOutcome",
    "settlement_rules",
    "ticket_digest",
]


def _parse(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)

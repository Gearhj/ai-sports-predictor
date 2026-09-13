"""Turn a named selection into a priced leg, using only what AISP already holds.

This is the boundary where a request stops being trusted. The caller says "the
Over 8.5 at Bovada in this game"; this module finds that quote in
``odds_snapshots``, finds the forecast ``ForecastSelector`` says is current, and
computes the probability itself. If either is missing the recording is refused
with the specific reason -- never completed with a default.

That refusal matters more than it looks. A ticket is meant to be re-readable
months later as evidence of a decision, and a leg priced from a number the
browser supplied would record the browser, not the model.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import AccountStatus, ExperimentAccount, Game
from app.parlay.pricing import Leg
from app.parlay.read_service import MlbParlayReadService
from app.parlay.tickets import ParlayTicketError, ParlayTicketService


class RecordingError(ValueError):
    """The ticket cannot be recorded, and the message says exactly why."""


def _to_date(value: datetime) -> Any:
    return value.date()


def record_from_request(
    session: Session, settings: Settings, payload: Any,
) -> dict[str, Any]:
    """Resolve, price, freeze. Returns the ticket and whether it was created."""

    now = datetime.now(UTC)
    service = MlbParlayReadService(session, settings)

    # Enumerate the slate the legs belong to, then match by identity. Going
    # through the same enumerator the page used is what guarantees a recorded
    # ticket obeys every eligibility gate the page displayed -- forecast
    # currency, quote freshness and the pregame cutoff -- rather than a second,
    # subtly different set of checks written here.
    first_game = session.get(Game, _uuid(payload.legs[0].game_id))
    if first_game is None:
        raise RecordingError(f"unknown game {payload.legs[0].game_id!r}")
    slate = _to_date(_aware(first_game.starts_at))

    available, _excluded = service._eligible_legs(slate, now=now)
    index = {
        (leg.game_id, leg.market.upper(), leg.selection.upper(), leg.sportsbook): leg
        for leg in available
    }

    resolved: list[Leg] = []
    for requested in payload.legs:
        key = (
            str(requested.game_id),
            requested.market.upper(),
            requested.selection.upper(),
            payload.sportsbook,
        )
        leg = index.get(key)
        if leg is None:
            raise RecordingError(
                f"no eligible leg matches {requested.selection!r} "
                f"({requested.market}) at {payload.sportsbook} for game "
                f"{requested.game_id}. It may have no stored quote, a quote too "
                "old to use, no current V18 forecast, or the game may be inside "
                "the pregame cutoff."
            )
        resolved.append(leg)

    account_id = None
    if payload.funded:
        account = _season_account(session, slate)
        if account is None:
            raise RecordingError(
                "no active MLB season account exists to fund this ticket. There is "
                "one $1,000 contribution and no second parlay bankroll, so an "
                "unfunded research ticket is the only option here."
            )
        account_id = account.id

    try:
        outcome = ParlayTicketService(session).record(
            resolved,
            stake=payload.stake,
            account_id=account_id,
            funded=payload.funded,
            recorded_combined_decimal_odds=payload.recorded_combined_decimal_odds,
            now=now,
        )
    except ParlayTicketError as exc:
        raise RecordingError(str(exc)) from exc

    session.commit()
    ticket = outcome.ticket
    return {
        "created": outcome.created,
        "detail": outcome.detail,
        "ticket": {
            "id": str(ticket.id),
            "ticket_sha256": ticket.ticket_sha256,
            "ticket_key": ticket.ticket_key,
            "sportsbook": ticket.sportsbook,
            "leg_count": ticket.leg_count,
            "stake": float(ticket.stake),
            "funding": ticket.funding,
            "account_id": str(ticket.account_id) if ticket.account_id else None,
            "combined_decimal_odds": ticket.combined_decimal_odds,
            "price_provenance": ticket.price_provenance,
            "probability_snapshot": ticket.probability_snapshot,
            "settlement_rules": ticket.settlement_rules,
            "recorded_at_utc": ticket.recorded_at_utc.isoformat(),
            "earliest_cutoff_utc": ticket.earliest_cutoff_utc.isoformat(),
            "status": ticket.status.value,
            "result": ticket.result.value,
        },
    }


def _season_account(session: Session, slate: Any) -> ExperimentAccount | None:
    """The one active MLB season account, or nothing.

    Deliberately does not create an account. The program has a single $1,000
    contribution; opening a second one to hold parlay stakes is exactly the
    thing that must not happen, so this either finds the existing account or
    reports that it cannot fund the ticket.
    """

    return session.scalar(
        select(ExperimentAccount)
        .where(
            ExperimentAccount.sport == "MLB",
            ExperimentAccount.status == AccountStatus.ACTIVE,
        )
        .order_by(ExperimentAccount.created_at.desc())
        .limit(1)
    )


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise RecordingError(f"{value!r} is not a game id") from exc


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


__all__ = ["RecordingError", "record_from_request"]

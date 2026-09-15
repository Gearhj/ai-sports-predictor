"""The terms a position was actually taken at, read from the position itself.

WHY THIS EXISTS
---------------
Daily Decisions showed DEN at KC as "Position recorded" beside "+114 · sgo:fanduel
· 2.3h old". The position was taken at +115 on sgo:espnbet. The +114 was the
latest stored quote for the game -- legitimate market context, and the wrong
number to sit next to a recorded wager, because every reader takes it as the
execution price. The experiment ledger had the opposite gap: it showed only the
decimal 2.150, with no side, no American price, no book and no provenance.

Both came from the same absence: the read models carried a position COUNT and a
latest quote, and nothing that described the entry. This module is the one place
entry terms come from, so the two surfaces cannot drift apart.

THE RULE
--------
Entry terms are read from the persisted position, the odds snapshot the position
itself references, and the decision that produced it. Never from "the latest
quote for this game", which is a different question with a different answer.
Tie treatment is reported only where the decision persisted one; nothing here
supplies a default a policy never recorded.
"""

from __future__ import annotations

from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

TIE_LABELS = {
    "STAKE_REFUNDED_PUSH": "Tie settlement: stake refunded",
}


def _american(value: Any) -> str | None:
    if value is None:
        return None
    number = int(value)
    return f"+{number}" if number > 0 else str(number)


def _provider(source: str | None, book_key: str | None, classification: str | None) -> dict[str, Any]:
    """Provider provenance, from the snapshot's own source and the book's namespace."""

    research = (classification or "").upper() == "RESEARCH" or (book_key or "").startswith("sgo:")
    name = {"SPORTSGAMEODDS": "SportsGameOdds"}.get((source or "").upper(), source)
    return {
        "source": source,
        "name": name,
        "classification": "RESEARCH" if research else (classification or None),
        "label": f"{name} · research provider" if research and name else name,
    }


def entry_terms(session: Session, position_ids: Iterable[UUID | str]) -> dict[str, dict[str, Any]]:
    """Entry terms keyed by position id string. Missing evidence stays None."""

    from app.models import (
        MarketOutcome, OddsSnapshot, OfficialAspPosition, ShadowExperimentDecision,
        Sportsbook, Team,
    )

    ids = [UUID(str(value)) for value in position_ids]
    if not ids:
        return {}

    rows = session.execute(
        select(OfficialAspPosition, OddsSnapshot, Sportsbook, ShadowExperimentDecision, Team)
        .outerjoin(OddsSnapshot, OddsSnapshot.id == OfficialAspPosition.odds_snapshot_id)
        .outerjoin(Sportsbook, Sportsbook.id == OddsSnapshot.sportsbook_id)
        .outerjoin(ShadowExperimentDecision,
                   ShadowExperimentDecision.id == OfficialAspPosition.shadow_decision_id)
        .outerjoin(MarketOutcome, MarketOutcome.id == OfficialAspPosition.market_outcome_id)
        .outerjoin(Team, Team.id == MarketOutcome.participant_team_id)
        .where(OfficialAspPosition.id.in_(ids))
    ).all()

    out: dict[str, dict[str, Any]] = {}
    for position, snapshot, book, decision, team in rows:
        evidence = (decision.evidence or {}) if decision is not None else {}
        selection = evidence.get("selection") if isinstance(evidence, dict) else None
        selection = selection if isinstance(selection, dict) else {}

        # The entry snapshot is the authority for the price; the decision's
        # persisted copy is the fallback. Neither is ever the latest quote.
        american = (snapshot.american_odds if snapshot is not None
                    and snapshot.american_odds is not None
                    else (decision.american_odds if decision is not None else None))
        book_key = book.key if book is not None else selection.get("sportsbook")
        book_name = book.name if book is not None else (decision.sportsbook if decision else None)
        side = team.abbreviation if team is not None and team.abbreviation else (
            team.name if team is not None else (decision.selection if decision else None))
        settlement = selection.get("settlement")
        status = position.status.value if hasattr(position.status, "value") else str(position.status)
        result = position.result.value if hasattr(position.result, "value") else str(position.result)

        out[str(position.id)] = {
            "position_id": str(position.id),
            "decision_id": str(position.shadow_decision_id) if position.shadow_decision_id else None,
            "side": side,
            "market": decision.market if decision is not None else None,
            "american_odds": american,
            "american_label": _american(american),
            "decimal_odds": float(position.decimal_odds),
            "sportsbook_key": book_key,
            "sportsbook_name": book_name,
            "provider": _provider(snapshot.source if snapshot is not None else selection.get("source"),
                                  book_key, selection.get("provider_classification")),
            "entry_quote_observed_at": (snapshot.observed_at.isoformat()
                                        if snapshot is not None and snapshot.observed_at else None),
            "stake": float(position.stake),
            "placed_at": position.placed_at.isoformat(),
            "recorded_at": position.created_at.isoformat() if position.created_at else None,
            "status": status,
            "result": result,
            "tie_settlement": settlement,
            "tie_settlement_label": TIE_LABELS.get(settlement) if settlement else None,
            "headline": " ".join(part for part in (side, _american(american)) if part) or None,
        }
    return out


__all__ = ["TIE_LABELS", "entry_terms"]

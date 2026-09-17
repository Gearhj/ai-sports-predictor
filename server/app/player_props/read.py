"""Player-prop quote read model: resolved quotes by game, player and prop.

Quote fields only. There is deliberately no probability, fair price, edge,
expected return, decision or confidence here -- the payload carries the
producer's INSUFFICIENT_EVIDENCE disposition and the "no AISP assessment" label.
Only RESOLVED rows are listed; quarantined rows are counted by reason.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.player_props import contract as c

#: The only keys a listed quote may carry. Tests assert nothing else appears.
QUOTE_FIELDS = (
    "quote_id", "game_id", "mlb_game_ref", "player_id", "player_name", "provider_player_name", "team_id",
    "team", "market_key", "market_label", "outcome", "provider_outcome", "line", "description",
    "american", "decimal", "sportsbook", "sportsbook_key", "provider", "provider_event_id",
    "observed_at", "ingested_at", "age_minutes", "fresh", "state", "market_status",
    "identity_status", "identity_evidence", "selectable",
)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def player_props_for_games(session: Session, game_ids: list[str], *, now: datetime | None = None) -> dict[str, Any]:
    from app.models import Player, PlayerPropQuote, Team

    now = now or datetime.now(UTC)
    quotes: list[dict[str, Any]] = []
    quarantined: Counter[str] = Counter()
    if game_ids:
        ids = [uuid.UUID(g) for g in game_ids]
        rows = session.execute(
            select(PlayerPropQuote, Player.full_name, Team.name)
            .join(Player, Player.id == PlayerPropQuote.player_id)
            .join(Team, Team.id == PlayerPropQuote.team_id)
            .where(PlayerPropQuote.game_id.in_(ids), PlayerPropQuote.identity_status == "RESOLVED",
                   PlayerPropQuote.source_mode == "LIVE")
            .order_by(PlayerPropQuote.provider_observed_at.desc())
        ).all()
        latest: dict[tuple, dict[str, Any]] = {}
        for q, player_name, team_name in rows:
            key = (q.game_id, q.player_id, q.provider_market_key, q.provider_outcome_name, q.provider_point, q.sportsbook_key)
            if key in latest:
                continue  # newest observation per outcome and book
            age = (now - _aware(q.provider_observed_at)).total_seconds() / 60
            fresh = 0 <= age <= c.QUOTE_FRESHNESS_MINUTES
            latest[key] = {
                "quote_id": str(q.id), "game_id": str(q.game_id), "mlb_game_ref": q.mlb_game_ref,
                "player_id": str(q.player_id), "player_name": player_name,
                "provider_player_name": q.provider_player_name, "team_id": str(q.team_id), "team": team_name,
                "market_key": q.provider_market_key,
                "market_label": c.SUPPORTED_MARKETS.get(q.provider_market_key, {}).get("label", q.provider_market_key),
                "outcome": q.outcome_side, "provider_outcome": q.provider_outcome_name, "line": q.line,
                "description": c.describe(q.provider_market_key, q.provider_outcome_name, q.provider_point),
                "american": q.american_price, "decimal": q.decimal_price,
                "sportsbook": q.sportsbook_title or q.sportsbook_key, "sportsbook_key": q.sportsbook_key,
                "provider": q.provider, "provider_event_id": q.provider_event_id,
                "observed_at": _aware(q.provider_observed_at).isoformat(),
                "ingested_at": _aware(q.ingested_at).isoformat(),
                "age_minutes": round(age, 1), "fresh": fresh,
                "state": "CURRENT" if fresh and q.market_status == "OPEN" else "STORED_REFERENCE_ONLY",
                "market_status": q.market_status, "identity_status": q.identity_status,
                "identity_evidence": q.identity_evidence,
                "selectable": bool(q.selectable and fresh and q.market_status == "OPEN"),
            }
        quotes = list(latest.values())
        for reason, count in session.execute(
            select(PlayerPropQuote.quarantine_reason, func.count())
            .where(PlayerPropQuote.game_id.in_(ids), PlayerPropQuote.identity_status == "QUARANTINED")
            .group_by(PlayerPropQuote.quarantine_reason)
        ):
            quarantined[reason] += count
    return _payload(quotes, dict(quarantined))


def empty_payload() -> dict[str, Any]:
    return _payload([], {})


def _payload(quotes: list[dict[str, Any]], quarantined: dict[str, int]) -> dict[str, Any]:
    return {
        "contract": c.CONTRACT,
        "required_identity": ["game_id", "player_id", "market_key", "outcome", "line", "american",
                              "sportsbook", "observed_at"],
        "status": "AVAILABLE" if quotes else "NOT_STORED",
        "supported_markets": sorted(c.SUPPORTED_MARKETS),
        "quotes": quotes,
        "quarantined": quarantined,
        "assessment": c.NO_ASSESSMENT_LABEL,
        "producer_disposition": c.PRODUCER_DISPOSITION,
        "freshness_window_minutes": c.QUOTE_FRESHNESS_MINUTES,
        "message": ("Sportsbook player-prop quotes, shown as offered. AISP makes no assessment of them."
                    if quotes else
                    "No player-prop quotes are stored for this slate. Collection is inactive until separately authorized."),
    }


def player_props_for_day(session: Session, day: date, *, now: datetime | None = None) -> dict[str, Any]:
    from app.models import Game, League
    from app.services.command_center import ET

    lo = datetime.combine(day, datetime.min.time(), ET)
    ids = [str(g) for g in session.scalars(
        select(Game.id).join(League, League.id == Game.league_id)
        .where(League.code == "MLB", Game.starts_at >= lo, Game.starts_at < lo + timedelta(days=1)))]
    return player_props_for_games(session, ids, now=now)

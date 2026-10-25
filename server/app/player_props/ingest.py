"""Parse a The Odds API single-event odds payload into stored player-prop quotes.

Every provider outcome in a supported market becomes one row: RESOLVED and
selectable when game, player and orientation all bind, otherwise QUARANTINED
with the first failing reason. The raw outcome is hashed, not stored whole, and
no credential ever reaches this module.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.player_props import contract as c
from app.player_props.identity import name_index, resolve_game, resolve_player


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


@dataclass
class RawOutcome:
    sportsbook_key: str
    sportsbook_title: str | None
    market_key: str
    outcome_name: str
    player_name: str
    point: float | None
    american: int | None
    observed_at: datetime | None
    payload_sha256: str


def parse_event_odds(payload: dict[str, Any]) -> tuple[dict[str, Any], list[RawOutcome]]:
    """Event header and every player outcome, exactly as the provider sent them."""

    header = {
        "event_id": str(payload.get("id") or ""),
        "sport_key": str(payload.get("sport_key") or ""),
        "home_team": str(payload.get("home_team") or ""),
        "away_team": str(payload.get("away_team") or ""),
        "commence_time": _parse_time(payload.get("commence_time")),
    }
    outcomes: list[RawOutcome] = []
    for book in payload.get("bookmakers") or []:
        for market in book.get("markets") or []:
            observed = _parse_time(market.get("last_update")) or _parse_time(book.get("last_update"))
            for outcome in market.get("outcomes") or []:
                raw = json.dumps({"book": book.get("key"), "market": market.get("key"), "outcome": outcome},
                                 sort_keys=True, separators=(",", ":")).encode("utf-8")
                price = outcome.get("price")
                outcomes.append(RawOutcome(
                    sportsbook_key=str(book.get("key") or ""),
                    sportsbook_title=book.get("title"),
                    market_key=str(market.get("key") or ""),
                    outcome_name=str(outcome.get("name") or ""),
                    player_name=str(outcome.get("description") or ""),
                    point=float(outcome["point"]) if outcome.get("point") is not None else None,
                    american=int(price) if isinstance(price, (int, float)) and not isinstance(price, bool) else None,
                    observed_at=observed,
                    payload_sha256=hashlib.sha256(raw).hexdigest(),
                ))
    return header, outcomes


def _orientation_conflicts(outcomes: list[RawOutcome]) -> set[int]:
    """Indexes of outcomes whose Over/Under pair disagrees on the line."""

    groups: dict[tuple, list[int]] = defaultdict(list)
    for i, o in enumerate(outcomes):
        if o.outcome_name in ("Over", "Under"):
            groups[(o.sportsbook_key, o.market_key, o.player_name, o.observed_at)].append(i)
    bad: set[int] = set()
    for idxs in groups.values():
        sides = defaultdict(set)
        for i in idxs:
            sides[outcomes[i].outcome_name].add(outcomes[i].point)
        if len(sides.get("Over", set())) > 1 or len(sides.get("Under", set())) > 1 or (
            sides.get("Over") and sides.get("Under") and sides["Over"] != sides["Under"]
        ):
            bad.update(idxs)
    return bad


@dataclass
class IngestResult:
    event_id: str
    game_id: str | None
    resolved: int = 0
    quarantined: int = 0
    duplicates: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"event_id": self.event_id, "game_id": self.game_id, "resolved": self.resolved,
                "quarantined": self.quarantined, "duplicates": self.duplicates, "reasons": dict(self.reasons)}


def ingest_event_odds(session: Session, payload: dict[str, Any], *, ingested_at: datetime | None = None,
                      request_ledger_id: Any = None, source_mode: str = "LIVE") -> IngestResult:
    from app.models import PlayerPropQuote

    now = ingested_at or datetime.now(UTC)
    header, outcomes = parse_event_odds(payload)
    game = None
    if header["sport_key"] != c.SPORT_KEY or header["commence_time"] is None:
        from app.player_props.identity import GameBinding

        game = GameBinding(None, c.GAME_NOT_MLB if header["sport_key"] != c.SPORT_KEY else c.GAME_UNRESOLVED)
    else:
        game = resolve_game(session, event_id=header["event_id"], home_team=header["home_team"],
                            away_team=header["away_team"], commence_time=header["commence_time"])
    result = IngestResult(header["event_id"], str(game.game_id) if game.game_id else None)
    conflicts = _orientation_conflicts(outcomes)
    names = name_index(session) if game.game_id else {}
    for i, o in enumerate(outcomes):
        spec = c.SUPPORTED_MARKETS.get(o.market_key)
        reason: str | None = None
        player = None
        if game.game_id is None:
            reason = game.reason
        elif spec is None:
            reason = c.MARKET_UNSUPPORTED
        elif o.outcome_name not in spec["outcomes"]:
            reason = c.OUTCOME_UNRECOGNIZED
        elif i in conflicts:
            reason = c.ORIENTATION_CONFLICT
        elif o.outcome_name in ("Over", "Under") and o.point is None:
            reason = c.LINE_MISSING
        elif o.american is None or c.decimal_from_american(o.american) is None:
            reason = c.PRICE_MISSING
        else:
            player = resolve_player(session, game, o.player_name, reference=now, names=names)
            reason = player.reason
        observed = o.observed_at or now
        exists = session.scalar(select(PlayerPropQuote.id).where(
            PlayerPropQuote.provider == c.PROVIDER,
            PlayerPropQuote.provider_event_id == header["event_id"],
            PlayerPropQuote.sportsbook_key == o.sportsbook_key,
            PlayerPropQuote.provider_market_key == o.market_key,
            PlayerPropQuote.provider_player_name == o.player_name,
            PlayerPropQuote.provider_outcome_name == o.outcome_name,
            PlayerPropQuote.provider_point.is_(None) if o.point is None else PlayerPropQuote.provider_point == o.point,
            PlayerPropQuote.provider_observed_at == observed,
        ))
        if exists is not None:
            result.duplicates += 1
            continue
        resolved = reason is None and player is not None
        session.add(PlayerPropQuote(
            provider=c.PROVIDER, provider_event_id=header["event_id"], provider_sport_key=header["sport_key"],
            provider_market_key=o.market_key, provider_outcome_name=o.outcome_name,
            provider_player_name=o.player_name, provider_point=o.point,
            sportsbook_key=o.sportsbook_key, sportsbook_title=o.sportsbook_title,
            american_price=o.american, decimal_price=c.decimal_from_american(o.american),
            market_status="OPEN", provider_observed_at=observed, ingested_at=now,
            source_mode=source_mode, payload_sha256=o.payload_sha256, request_ledger_id=request_ledger_id,
            game_id=game.game_id, mlb_game_ref=game.mlb_game_ref,
            player_id=player.player_id if resolved else None,
            team_id=player.team_id if resolved else None,
            outcome_side=c.OUTCOME_SIDE.get(o.outcome_name) if resolved else None,
            line=o.point if resolved else None,
            identity_status="RESOLVED" if resolved else "QUARANTINED",
            identity_evidence=player.evidence if resolved else None,
            quarantine_reason=None if resolved else reason,
            selectable=resolved,
            detail={"name_candidates": player.candidates if player else 0},
        ))
        if resolved:
            result.resolved += 1
        else:
            result.quarantined += 1
            result.reasons[reason] = result.reasons.get(reason, 0) + 1
    session.flush()
    return result

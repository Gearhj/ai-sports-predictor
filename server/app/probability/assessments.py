"""Daily Decisions assessment read model: model masses beside actual quotes.

For each governed-price game on an Eastern slate date (the same games and
quotes ``command_center.market_coverage`` lists) this reports, per quoted
side, the verified population's win/loss/push mass, the push-aware fair
price, the same-book no-vig probability and -- only for a CURRENT quote -- a
research edge and expected return, and only from a provider the decision
policy permits. It never infers a missing side or line:
a side is assessed only where the stored quote pair has it.

Same-game Parlay Lab examples use only lines that were actually quoted, and
carry no edge because no sportsbook parlay price is stored. Research sports
(NBA, College Football) are listed as research comparisons without prices.
Cross-game combinations are never built.

Nothing here requests a price, evaluates a policy or writes a row.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.probability import adapter
from app.probability.adapter import MONEYLINE_TIE_SETTLEMENT, ProbabilityUnavailable
from app.probability.evaluate import leg_masses, run
from app.probability.identity import (
    CROSS_GAME_CALIBRATION_NOT_VALIDATED,
    CROSS_GAME_MESSAGE,
    PARLAY_PRICE_UNAVAILABLE,
)

SCHEMA = "AISP_DAILY_ASSESSMENTS_V1"
RESEARCH_LOOKAHEAD = timedelta(days=45)
_SIDES = {"MONEYLINE": ("HOME", "AWAY"), "SPREAD": ("HOME", "AWAY"), "RUN_LINE": ("HOME", "AWAY"),
          "TOTAL": ("OVER", "UNDER")}


def _leg(market: str, side: str, line: float | None) -> dict[str, Any]:
    if market == "MONEYLINE":
        return {"market": "MONEYLINE", "selection": side, "tieSettlement": MONEYLINE_TIE_SETTLEMENT}
    if market in ("SPREAD", "RUN_LINE"):
        # Coverage reports the HOME line; the AWAY side carries the opposite sign.
        return {"market": "SPREAD", "selection": side, "line": line if side == "HOME" else -line}
    return {"market": "TOTAL", "selection": side, "line": line}


def _quote_assessment(channel: adapter.VerifiedChannel | None, quote: dict[str, Any]) -> dict[str, Any]:
    market = quote["market"]
    sides = _SIDES.get(market)
    out = {k: quote.get(k) for k in ("market", "line", "sportsbook", "sportsbook_key", "provider",
                                     "classification", "observed_at", "age_minutes", "fresh")}
    out["state"] = "CURRENT" if quote.get("fresh") else "STORED_REFERENCE_ONLY"
    out["sides"] = {}
    if sides is None:
        return out
    decimals = {s: (quote.get("sides") or {}).get(s, {}).get("decimal") for s in sides}
    inverse = {s: (1 / d) if d and d > 1 else None for s, d in decimals.items()}
    total_inverse = sum(v for v in inverse.values() if v) if all(inverse.values()) else None
    for side in sides:
        price = (quote.get("sides") or {}).get(side)
        if not price or price.get("decimal") is None:
            continue  # never infer a missing side
        entry: dict[str, Any] = {"american": price.get("american"), "decimal": price.get("decimal"),
                                 "no_vig": (inverse[side] / total_inverse) if total_inverse else None,
                                 "model": None, "edge": None, "expected_return": None, "edge_reason": None}
        if channel is None:
            entry["edge_reason"] = "NO_VERIFIED_POPULATION"
        elif market != "MONEYLINE" and quote.get("line") is None:
            entry["edge_reason"] = "LINE_MISSING"
        else:
            masses = leg_masses(channel, _leg(market, side, quote.get("line")))
            entry["model"] = masses
            if not quote.get("fresh"):
                entry["edge_reason"] = "QUOTE_NOT_CURRENT"
            elif quote.get("provider") not in _permitted_providers():
                entry["edge_reason"] = "PROVIDER_NOT_PERMITTED"
            elif entry["no_vig"] is None or masses["conditional"] is None:
                entry["edge_reason"] = "NO_VIG_UNAVAILABLE"
            else:
                entry["edge"] = masses["conditional"] - entry["no_vig"]
                entry["expected_return"] = masses["win"] * (price["decimal"] - 1) - masses["loss"]
        out["sides"][side] = entry
    return out


def _permitted_providers() -> set[str]:
    """Provider labels the decision policy itself accepts (command_center.PERMITTED_SOURCES)."""

    from app.services.command_center import PERMITTED_SOURCES, _provider_label

    return {_provider_label(source) for source in PERMITTED_SOURCES}


def _consensus_line(quotes: list[dict[str, Any]], market: str) -> float | None:
    lines = Counter(q["line"] for q in quotes if q["market"] == market and q.get("line") is not None)
    if not lines:
        return None
    return sorted(lines.items(), key=lambda kv: (-kv[1], abs(kv[0])))[0][0]


def _favored(channel: adapter.VerifiedChannel, market: str, line: float | None) -> dict[str, Any]:
    a, b = _SIDES[market]
    legs = [_leg(market, a, line), _leg(market, b, line)]
    masses = [leg_masses(channel, leg)["win"] for leg in legs]
    return legs[0] if masses[0] >= masses[1] else legs[1]


def parlay_examples(channel: adapter.VerifiedChannel, quotes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Same-game examples from the model-favored side of each actually quoted line."""

    total = _consensus_line(quotes, "TOTAL")
    spread = _consensus_line(quotes, "SPREAD")
    if spread is None:
        spread = _consensus_line(quotes, "RUN_LINE")
    has_ml = any(q["market"] == "MONEYLINE" for q in quotes)
    combos: list[list[dict[str, Any]]] = []
    if has_ml and total is not None:
        combos.append([_favored(channel, "MONEYLINE", None), _favored(channel, "TOTAL", total)])
    if spread is not None and total is not None:
        combos.append([_favored(channel, "SPREAD", spread), _favored(channel, "TOTAL", total)])
    out = []
    for legs in combos:
        result = run(channel, legs)
        contract = result["contract"]
        out.append({
            "legs": contract["legs"],
            "probabilities": contract["probabilities"],
            "counts": contract["counts"],
            "effective_draw_count": contract["effectiveDrawCount"],
            "mcse": contract["winProbabilityMonteCarloStandardError"],
            "method": contract["method"],
            "display_method": result["display_method"],
            "fair_american": result["fair_american"],
            "population": contract["population"],
            "authority": result["authority"],
            "authority_label": result["authority_label"],
            "parlay": result["parlay"],
            "lines_from": "CONSENSUS_QUOTED_LINES",
        })
    return out


def _teams(session: Session, game_ids: list) -> dict[str, dict[str, Any]]:
    from app.models import GameTeam, Team

    out: dict[str, dict[str, Any]] = {}
    if not game_ids:
        return out
    for game_id, is_home, abbreviation, name in session.execute(
        select(GameTeam.game_id, GameTeam.is_home, Team.abbreviation, Team.name)
        .join(Team, Team.id == GameTeam.team_id).where(GameTeam.game_id.in_(game_ids))
    ):
        out.setdefault(str(game_id), {})["home" if is_home else "away"] = {
            "abbreviation": abbreviation or name, "name": name}
    for value in out.values():
        value["label"] = f"{(value.get('away') or {}).get('abbreviation', '?')} @ {(value.get('home') or {}).get('abbreviation', '?')}"
    return out


def _research_games(session: Session, settings: Settings, lo: datetime) -> list[dict[str, Any]]:
    """The next bound NBA and College Football research game, one per sport."""

    from app.models import ExternalIdMapping, Game, League, NbaJointComparisonArm, NbaResearchForecast

    bound = or_(
        exists().where(NbaJointComparisonArm.canonical_game_id == Game.id),
        exists().where(NbaResearchForecast.canonical_game_id == Game.id),
        exists().where(ExternalIdMapping.entity_id == Game.id, ExternalIdMapping.entity_type == "game",
                       ExternalIdMapping.source == "CFBD"),
    )
    rows = session.execute(
        select(Game.id, League.code, Game.starts_at)
        .join(League, League.id == Game.league_id)
        .where(League.code.in_(("NBA", "CFB")), Game.starts_at >= lo,
               Game.starts_at < lo + RESEARCH_LOOKAHEAD, bound)
        .order_by(Game.starts_at, Game.id)
    ).all()
    first: dict[str, tuple] = {}
    for row in rows:
        first.setdefault(row[1], row)
    chosen = [first[code] for code in sorted(first)]
    teams = _teams(session, [r[0] for r in chosen])
    out = []
    for game_id, league, starts_at in chosen:
        described = adapter.describe(session, settings, game_id)
        matchup = teams.get(str(game_id), {})
        label = matchup.get("label")
        home, away = matchup.get("home") or {}, matchup.get("away") or {}
        if league == "CFB" and home.get("name") and away.get("name"):
            # CFB teams carry CFBD ids as abbreviations; the names read better.
            label = f"{away['name']} @ {home['name']}"
        out.append({
            "game_id": str(game_id), "sport": league, "matchup": label,
            "home": matchup.get("home"), "away": matchup.get("away"),
            "starts_at": starts_at.isoformat() if starts_at else None,
            "channels": described["channels"],
            "price_state": "NO_GOVERNED_PRICE",
            "price_note": "Research sport: AISP collects no governed sportsbook price, so no edge is assessed.",
        })
    return out


def daily_assessments(session: Session, settings: Settings, day: date,
                      now: datetime | None = None) -> dict[str, Any]:
    from app.services.command_center import ET, market_coverage

    now = now or datetime.now(UTC)
    coverage = market_coverage(session, day, now)
    games = []
    for game in coverage["games"]:
        channel = None
        binding: dict[str, Any]
        try:
            channel = adapter.bind(session, settings, uuid.UUID(game["game_id"]))
            binding = channel.describe()
        except ProbabilityUnavailable as exc:
            binding = {"channel": "PAIRED_POPULATION", "status": "UNSUPPORTED", "error": exc.as_dict()}
        quotes = [_quote_assessment(channel, q) for q in game["quotes"]]
        games.append({
            "game_id": game["game_id"], "sport": game["league"],
            "binding": binding,
            "quotes": quotes,
            "parlay_lab": parlay_examples(channel, game["quotes"]) if channel is not None else [],
        })
    lo = datetime.combine(day, datetime.min.time(), ET)
    return {
        "schema_version": SCHEMA,
        "date": day.isoformat(),
        "generated_at": now.isoformat(),
        "moneyline_tie_settlement": MONEYLINE_TIE_SETTLEMENT,
        "games": games,
        "research": _research_games(session, settings, max(lo, now - timedelta(hours=6))),
        "parlay_policy": {
            "same_game": "PAIRED_DRAW_INTERSECTION over one verified population; research only",
            "sportsbook_parlay_quotes_stored": 0,
            "unavailable_message": PARLAY_PRICE_UNAVAILABLE,
            "creates_decisions": False,
            "creates_positions": False,
        },
        "cross_game": {"status": "UNSUPPORTED", "code": CROSS_GAME_CALIBRATION_NOT_VALIDATED,
                       "message": CROSS_GAME_MESSAGE},
        "provider_calls_made": 0,
    }

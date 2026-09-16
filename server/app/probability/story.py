"""Game Center "Game Story" read model: the inputs the page's charts draw.

Everything here is a stored or verified value:

* the exact paired score population the adapter re-verified (cells with
  counts, never re-simulated), with its margin and total distributions;
* win/loss/push masses for the consensus quoted lines, from the producer
  contract (the chart only colours the regions those masses describe);
* the stored quotes for the game with their assessment (model, no-vig, fair
  price, and an edge only where a current permitted quote exists);
* a short list of deterministic facts -- the largest current disagreement,
  data freshness and the most material missing input -- stated from numbers,
  never from causal guesses.

A completed game's final score is included only as an after-the-fact overlay.
No provider is called and nothing is written.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.probability import adapter
from app.probability.adapter import ProbabilityUnavailable
from app.probability.assessments import _consensus_line, _leg, _quote_assessment, _teams
from app.probability.evaluate import leg_masses

SCHEMA = "AISP_GAME_STORY_V1"
MARKET_LABEL = {"MONEYLINE": "moneyline", "SPREAD": "spread", "RUN_LINE": "run line", "TOTAL": "total"}


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _age_text(minutes: float) -> str:
    minutes = max(0, round(minutes))
    if minutes < 90:
        return f"{minutes} min"
    if minutes < 48 * 60:
        return f"{minutes // 60} h {minutes % 60} min"
    return f"{round(minutes / 1440)} days"


def _population_block(channel: adapter.VerifiedChannel) -> dict[str, Any]:
    population = channel.population
    cells = [{"away": c.away_score, "home": c.home_score, "count": c.count} for c in population.cells]
    margin: dict[int, int] = {}
    total: dict[int, int] = {}
    for c in population.cells:
        margin[c.home_score - c.away_score] = margin.get(c.home_score - c.away_score, 0) + c.count
        total[c.home_score + c.away_score] = total.get(c.home_score + c.away_score, 0) + c.count
    ranked = sorted(cells, key=lambda c: (-c["count"], c["away"], c["home"]))
    # The most likely score region: the fewest cells that together hold at
    # least a quarter of the draws.
    region, held = [], 0
    for cell in ranked:
        if held >= population.draw_count * 0.25:
            break
        region.append({"away": cell["away"], "home": cell["home"]})
        held += cell["count"]
    return {
        "draw_count": population.draw_count,
        "population_sha256": population.population_sha256,
        "cells": cells,
        "mode": ranked[0],
        "likely_region": {"cells": region, "count": held},
        "margin": [{"value": k, "count": v} for k, v in sorted(margin.items())],
        "total": [{"value": k, "count": v} for k, v in sorted(total.items())],
        "orientation": {
            "margin": "home score minus away score",
            "spread": "the line is attached to the selected side; HOME wins when home + line > away",
            "total": "OVER wins when away + home > line; equality is a push",
        },
    }


def _line_block(channel: adapter.VerifiedChannel, market: str, line: float | None) -> dict[str, Any] | None:
    if line is None:
        return None
    sides = ("HOME", "AWAY") if market == "SPREAD" else ("OVER", "UNDER")
    return {"line": line, "line_side": "HOME" if market == "SPREAD" else None,
            "sides": {side: leg_masses(channel, _leg(market, side, line)) for side in sides}}


def game_story(session: Session, settings: Settings, game_id: uuid.UUID,
               now: datetime | None = None) -> dict[str, Any]:
    from app.models import Game
    from app.services.command_center import ET, PRICED_LEAGUES, market_coverage

    now = now or datetime.now(UTC)
    game = session.get(Game, game_id)
    if game is None:
        raise ProbabilityUnavailable("GAME_NOT_FOUND", "no canonical game has this id", status=404)
    league = adapter.game_league(session, game_id)
    teams = _teams(session, [game_id]).get(str(game_id), {})
    status = str(getattr(game.status, "value", game.status)).upper()
    final = ({"home": game.home_score, "away": game.away_score}
             if status == "FINAL" and game.home_score is not None and game.away_score is not None else None)

    channel = None
    try:
        channel = adapter.bind(session, settings, game_id)
        channel_block = channel.describe()
    except ProbabilityUnavailable as exc:
        if exc.code == "GAME_NOT_FOUND":
            raise
        channel_block = {"channel": "PAIRED_POPULATION", "status": "UNSUPPORTED", "error": exc.as_dict()}

    starts = _aware(game.starts_at)
    market: dict[str, Any] | None = None
    stored_model: dict[str, Any] | None = None
    raw_quotes: list[dict[str, Any]] = []
    if league in PRICED_LEAGUES and starts is not None:
        coverage = market_coverage(session, starts.astimezone(ET).date(), now)
        row = next((g for g in coverage["games"] if g["game_id"] == str(game_id)), None)
        if row is not None:
            raw_quotes = row["quotes"]
            stored_model = row["model"]
            market = {
                "price_state": row["price_state"],
                "consensus_no_vig_home": row["consensus_no_vig_home"],
                "consensus_is_current": row["consensus_is_current"],
                "best": row["best"],
                "books": row["books"],
                "freshest_observed_at": row["freshest_observed_at"],
                "quotes": [_quote_assessment(channel, q) for q in raw_quotes],
            }

    current = [q for q in raw_quotes if q.get("fresh")]
    pool = current or raw_quotes
    spread_line = _consensus_line(pool, "SPREAD")
    if spread_line is None:
        spread_line = _consensus_line(pool, "RUN_LINE")
    total_line = _consensus_line(pool, "TOTAL")

    winner = (channel_block.get("winner") or {}) if channel_block.get("status") == "SUPPORTED" else {}
    model_source = "VERIFIED_POPULATION_FORECAST" if winner else None
    if not winner and stored_model and stored_model.get("home") is not None:
        # No verified population, but the same controlling forecast row the
        # market board shows still states its winner probabilities.
        winner = {"home": stored_model["home"], "away": stored_model["away"], "tie": None}
        model_source = "STORED_CONTROLLING_FORECAST"
    home_p, away_p = winner.get("home"), winner.get("away")
    two_way_home = (home_p / (home_p + away_p)) if home_p is not None and away_p is not None and home_p + away_p > 0 else None

    story: dict[str, Any] = {
        "schema_version": SCHEMA,
        "game": {
            "game_id": str(game_id), "sport": league, "status": status,
            "starts_at": starts.isoformat() if starts else None,
            "home": teams.get("home"), "away": teams.get("away"), "final": final,
            "final_note": "Actual final score, shown after the game; it was not known when the forecast was made." if final else None,
        },
        "channel": channel_block,
        "population": _population_block(channel) if channel else None,
        "model_vs_market": {
            "model_home": home_p, "model_away": away_p, "model_tie": winner.get("tie"),
            "model_home_two_way": two_way_home, "model_source": model_source,
            "market_home_no_vig": market["consensus_no_vig_home"] if market else None,
            "market_is_current": bool(market and market["consensus_is_current"]),
            "market_books": market["books"] if market else 0,
        },
        "lines": {
            "spread": _line_block(channel, "SPREAD", spread_line) if channel else None,
            "total": _line_block(channel, "TOTAL", total_line) if channel else None,
            "from": "CURRENT_QUOTES" if current else ("STORED_QUOTES" if raw_quotes else None),
        },
        "market": market,
        "facts": _facts(league, channel, channel_block, market, now),
        "provider_calls_made": 0,
    }
    return story


def _facts(league: str | None, channel, channel_block: dict[str, Any], market: dict[str, Any] | None,
           now: datetime) -> list[dict[str, str]]:
    facts: list[dict[str, str]] = []
    # 1. The largest current disagreement.
    best = None
    for quote in (market or {}).get("quotes", []):
        for side, entry in quote["sides"].items():
            if entry.get("edge") is None:
                continue
            if best is None or abs(entry["edge"]) > abs(best[2]["edge"]):
                best = (quote, side, entry)
    if best is not None:
        quote, side, entry = best
        facts.append({"kind": "DISAGREEMENT", "text": (
            f"Largest current model/market difference: {side.lower()} {MARKET_LABEL.get(quote['market'], quote['market'].lower())}"
            f"{'' if quote.get('line') is None else ' ' + str(quote['line'])} at {quote['sportsbook']}: model "
            f"{entry['model']['conditional'] * 100:.1f}% vs no-vig {entry['no_vig'] * 100:.1f}% "
            f"({entry['edge'] * 100:+.1f} pts). This is a research comparison, not a governed decision.")})
    elif channel is not None and market and market["quotes"]:
        facts.append({"kind": "DISAGREEMENT", "text":
                      "No current, permitted two-sided price is stored, so no model/market difference is measured."})

    # 2. Freshness.
    created = channel_block.get("forecast_created_at")
    if created:
        age = (now - _aware(datetime.fromisoformat(created))).total_seconds() / 60
        facts.append({"kind": "FRESHNESS", "text": f"The bound forecast was created {_age_text(age)} ago."})
    if market and market.get("freshest_observed_at"):
        age = (now - _aware(datetime.fromisoformat(market["freshest_observed_at"]))).total_seconds() / 60
        state = "current" if market["price_state"] == "CURRENT_PRICE" else "not current"
        facts.append({"kind": "FRESHNESS", "text": f"The newest stored sportsbook quote was observed {_age_text(age)} ago ({state})."})

    # 3. The most material missing input.
    if channel_block.get("status") != "SUPPORTED":
        error = channel_block.get("error") or {}
        facts.append({"kind": "MISSING", "text": f"No verified score population: {error.get('message', 'unavailable')}."})
    elif league not in ("MLB", "NFL"):
        facts.append({"kind": "MISSING", "text": "Research sport: AISP collects no governed sportsbook price for this game."})
    elif not market or not market["quotes"]:
        facts.append({"kind": "MISSING", "text": "No sportsbook quote is stored for this game yet."})
    else:
        quoted = {("SPREAD" if q["market"] == "RUN_LINE" else q["market"]) for q in market["quotes"]}
        absent = [m for m in ("MONEYLINE", "SPREAD", "TOTAL") if m not in quoted]
        if absent:
            facts.append({"kind": "MISSING", "text": "Not quoted: " + ", ".join(MARKET_LABEL[m] for m in absent) + "."})
        elif market["price_state"] != "CURRENT_PRICE":
            facts.append({"kind": "MISSING", "text": "Stored quotes are outside the freshness window; a current price is missing."})
    return facts

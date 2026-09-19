"""College Football sportsbook quotes as stored, with their three clocks, beside the JOINT model.

WHAT A CFB QUOTE CARRIES
------------------------
Each stored side keeps the book's own ``lastUpdatedAt`` as ``observed_at`` (the
CFB SportsGameOdds path stamps every side separately), the request time as
``available_as_of``/``fetched_at`` and the response's arrival as
``response_received_at``. A pair is as fresh as its OLDER leg -- the paper
policy's rule -- and all three clocks are reported, never one for another.

WHICH MODEL NUMBER A QUOTE IS COMPARED WITH
------------------------------------------
Only the joint paired-residual simulation, the channel the paper checkpoint
decides on:

* moneyline -- the stored joint win probabilities (tie mass excluded, i.e. the
  push-excluded share) against the SAME book's no-vig pair;
* spread and total -- the joint simulation's own ``home_margin`` / ``total``
  distributions (the retained 10,000-draw PMFs), counted at the quoted line
  with push mass kept separate, and only when the forecast carries them.

Ridge expected points and the Elo benchmark are never used here, and nothing
is averaged. These are research comparisons: the paper policy tests only the
moneyline, and only at its checkpoint, where the recorded decision is the
authority.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

QUOTE_FRESHNESS_MINUTES = 240
JOINT_CHANNEL = "JOINT_PAIRED_RESIDUAL_BASELINE"
DISTRIBUTION_CHANNEL = "JOINT_SCORE_DISTRIBUTION"
EXCLUDED_CHANNELS = ("RIDGE_DEVELOPMENT_CALIBRATED", "ELO_DIAGNOSTIC")
CHANNEL_LABEL = {
    JOINT_CHANNEL: "Joint score simulation (paired-residual draws) · the channel the paper policy decides on",
    DISTRIBUTION_CHANNEL: "Joint score simulation · its own margin/total distribution over the retained draws",
}


def _aware(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _iso(value: Any) -> str | None:
    moment = _aware(value)
    return moment.astimezone(UTC).isoformat() if moment else None


def quote_clocks(legs: dict[str, dict[str, Any]], *, now: datetime) -> dict[str, Any]:
    """The three clocks of one stored two-sided quote, each named for what it is.

    ``legs`` maps side -> {observed_at, available_as_of, response_received_at}.
    """

    observed = {side: _aware(leg.get("observed_at")) for side, leg in legs.items()}
    fetched = [_aware(leg.get("available_as_of")) for leg in legs.values() if leg.get("available_as_of")]
    received = [_aware(leg.get("response_received_at")) for leg in legs.values() if leg.get("response_received_at")]
    older = min((v for v in observed.values() if v), default=None)
    newer = max((v for v in observed.values() if v), default=None)
    fetched_at = max((v for v in fetched if v), default=None)
    received_at = max((v for v in received if v), default=None)
    return {
        "quote_clock_basis": "BOOK_LAST_UPDATED_AT_PER_SIDE",
        "legs": {side: _iso(v) for side, v in observed.items()},
        "older_leg_at": _iso(older),
        "leg_skew_seconds": round((newer - older).total_seconds(), 1) if older and newer else None,
        "quote_age_minutes": round((now - older).total_seconds() / 60, 1) if older else None,
        "fetched_at": _iso(fetched_at),
        "fetch_basis": "AISP_REQUEST_SENT (or later review binding)",
        "received_at": _iso(received_at),
        "receipt_basis": "AISP_RESPONSE_RECEIVED" if received_at else None,
        "fetch_age_minutes": round((now - fetched_at).total_seconds() / 60, 1) if fetched_at else None,
        "fresh": bool(older) and 0 <= (now - older).total_seconds() / 60 <= QUOTE_FRESHNESS_MINUTES,
        "usable_until": _iso(older + timedelta(minutes=QUOTE_FRESHNESS_MINUTES)) if older else None,
    }


def stored_quotes(session: Session, game_ids: list[Any], *, now: datetime) -> dict[str, list[dict[str, Any]]]:
    """Newest stored two-sided quote per (market, book, provider) for each game, with clocks."""

    from app.models import GameTeam, Market, MarketOutcome, OddsSnapshot, Sportsbook

    if not game_ids:
        return {}
    homes: dict[str, Any] = {}
    aways: dict[str, Any] = {}
    for g, t, is_home in session.execute(
            select(GameTeam.game_id, GameTeam.team_id, GameTeam.is_home).where(GameTeam.game_id.in_(game_ids))):
        (homes if is_home else aways)[str(g)] = t
    rows = session.execute(
        select(Market.game_id, Market.market_type, Market.period, MarketOutcome.outcome_key,
               MarketOutcome.participant_team_id, Sportsbook.key, Sportsbook.name, OddsSnapshot.source,
               OddsSnapshot.observed_at, OddsSnapshot.available_as_of, OddsSnapshot.american_odds,
               OddsSnapshot.decimal_odds, OddsSnapshot.point, OddsSnapshot.source_mode, OddsSnapshot.raw_payload,
               OddsSnapshot.source_event_id)
        .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
        .join(Market, Market.id == MarketOutcome.market_id)
        .join(Sportsbook, Sportsbook.id == OddsSnapshot.sportsbook_id)
        .where(Market.game_id.in_(game_ids))
        .order_by(OddsSnapshot.observed_at.desc())).all()
    latest: dict[tuple, dict[str, Any]] = {}
    for (game_id, market_type, period, outcome_key, participant, book_key, book_name, source, observed,
         available, american, decimal, point, mode, raw, event_id) in rows:
        if str(mode or "").upper() != "LIVE" or str(period or "").split(".")[-1].lower() != "full_game":
            continue  # a fixture/demo row is never a quote; only the full game is priced here
        market = str(market_type).split(".")[-1].upper()
        key_side = str(outcome_key or "").split(":")[0].upper()
        if participant is not None and market != "TOTAL":
            key_side = ("HOME" if participant == homes.get(str(game_id))
                        else "AWAY" if participant == aways.get(str(game_id)) else key_side)
        if key_side not in ("HOME", "AWAY", "OVER", "UNDER"):
            continue
        line = None
        if market == "TOTAL":
            line = point
        elif market == "SPREAD":
            line = point if key_side == "HOME" else (-point if point is not None else None)
        key = (str(game_id), market, book_key, source, line, key_side)
        if key in latest:
            continue
        latest[key] = {"observed_at": observed, "available_as_of": available, "american": american,
                       "decimal": decimal, "book_name": book_name,
                       "response_received_at": (raw or {}).get("response_received_at") if isinstance(raw, dict) else None,
                       "rebound_from_archive": bool((raw or {}).get("rebound_from_archive")) if isinstance(raw, dict) else False,
                       "event_id": event_id}
    buckets: dict[tuple, dict[str, dict[str, Any]]] = defaultdict(dict)
    for (game_id, market, book_key, source, line, side), leg in latest.items():
        buckets[(game_id, market, book_key, source, line)][side] = leg
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    newest: dict[tuple, dict[str, Any]] = {}
    for (game_id, market, book_key, source, line), legs in buckets.items():
        a, b = ("OVER", "UNDER") if market == "TOTAL" else ("HOME", "AWAY")
        if a not in legs or b not in legs:
            continue   # one side is not a market
        clocks = quote_clocks({s: legs[s] for s in (a, b)}, now=now)
        quote = {"market": market, "line": line, "sportsbook": legs[a]["book_name"], "sportsbook_key": book_key,
                 "provider": source, "observed_at": clocks["older_leg_at"], "age_minutes": clocks["quote_age_minutes"],
                 "fresh": clocks["fresh"], "clocks": clocks, "provider_event_id": legs[a].get("event_id"),
                 "rebound_from_archive": legs[a]["rebound_from_archive"] or legs[b]["rebound_from_archive"],
                 "sides": {s: {"american": legs[s]["american"], "decimal": legs[s]["decimal"]} for s in (a, b)}}
        ident = (game_id, market, book_key, source)
        if ident not in newest or (quote["observed_at"] or "") > (newest[ident]["observed_at"] or ""):
            newest[ident] = quote
    for (game_id, _market, _book, _source), quote in newest.items():
        out[game_id].append(quote)
    for quotes in out.values():
        quotes.sort(key=lambda q: (["MONEYLINE", "SPREAD", "TOTAL"].index(q["market"])
                                   if q["market"] in ("MONEYLINE", "SPREAD", "TOTAL") else 9, q["sportsbook"] or ""))
    return dict(out)


def _no_vig(quote: dict[str, Any], a: str, b: str) -> dict[str, float] | None:
    da, db = (quote["sides"].get(a) or {}).get("decimal"), (quote["sides"].get(b) or {}).get("decimal")
    if not da or not db or da <= 1 or db <= 1:
        return None
    ia, ib = 1 / da, 1 / db
    return {a: ia / (ia + ib), b: ib / (ia + ib), "overround": ia + ib - 1}


def _pmf_split(pmf: dict[str, Any] | None, *, above: float) -> dict[str, float] | None:
    """P(X > above), P(X == above), P(X < above) by counting the retained draws. Never a normal curve."""

    if not pmf or not pmf.get("values") or not pmf.get("counts") or not pmf.get("draw_count"):
        return None
    draws = float(pmf["draw_count"])
    over = sum(c for v, c in zip(pmf["values"], pmf["counts"]) if v > above)
    push = sum(c for v, c in zip(pmf["values"], pmf["counts"]) if v == above)
    under = sum(c for v, c in zip(pmf["values"], pmf["counts"]) if v < above)
    return {"above": over / draws, "equal": push / draws, "below": under / draws, "draws": int(draws)}


def compare(forecast: Any, quote: dict[str, Any]) -> dict[str, Any]:
    """One stored quote against the joint model, for that market only. Research comparison."""

    market = quote["market"]
    base = {"market": market, "line": quote.get("line"), "sportsbook": quote.get("sportsbook"),
            "provider": quote.get("provider"), "current": bool(quote.get("fresh")),
            "excluded_channels": list(EXCLUDED_CHANNELS), "policy_test": market == "MONEYLINE"}
    if forecast is None:
        return {**base, "available": False, "reason": "NO_FORECAST"}
    if market == "MONEYLINE":
        no_vig = _no_vig(quote, "HOME", "AWAY")
        home, away = forecast.joint_home_win_probability, forecast.joint_away_win_probability
        if no_vig is None or home is None or away is None or (home + away) <= 0:
            return {**base, "available": False, "reason": "NO_VIG_UNAVAILABLE" if no_vig is None else "NO_JOINT"}
        model_home = home / (home + away)
        return {**base, "available": True, "channel": JOINT_CHANNEL, "channel_label": CHANNEL_LABEL[JOINT_CHANNEL],
                "model": {"HOME": model_home, "AWAY": 1 - model_home,
                          "tie": getattr(forecast, "tie_probability", None)},
                "no_vig": {"HOME": no_vig["HOME"], "AWAY": no_vig["AWAY"]}, "overround": no_vig["overround"],
                "difference": {"HOME": model_home - no_vig["HOME"], "AWAY": (1 - model_home) - no_vig["AWAY"]},
                "basis": "joint win share (ties excluded) minus the same book's no-vig share"}
    pmfs = getattr(forecast, "pmfs", None) or {}
    line = quote.get("line")
    if line is None:
        return {**base, "available": False, "reason": "LINE_MISSING"}
    if market == "SPREAD":
        a, b = "HOME", "AWAY"
        # Home covers when margin + home_line > 0, i.e. margin > -home_line.
        split = _pmf_split(pmfs.get("home_margin"), above=-float(line))
        labels = {"win_a": "home covers", "push": "push", "win_b": "away covers"}
    elif market == "TOTAL":
        a, b = "OVER", "UNDER"
        split = _pmf_split(pmfs.get("total"), above=float(line))
        labels = {"win_a": "over", "push": "push", "win_b": "under"}
    else:
        return {**base, "available": False, "reason": "MARKET_NOT_COMPARED"}
    if split is None:
        return {**base, "available": False, "reason": "FORECAST_PROVIDES_NO_SCORE_DISTRIBUTION"}
    no_vig = _no_vig(quote, a, b)
    decided = split["above"] + split["below"]
    share = split["above"] / decided if decided else None
    out = {**base, "available": True, "channel": DISTRIBUTION_CHANNEL,
           "channel_label": CHANNEL_LABEL[DISTRIBUTION_CHANNEL],
           "model": {a: split["above"], "PUSH": split["equal"], b: split["below"],
                     f"{a}_if_not_push": share},
           "draws": split["draws"], "labels": labels,
           "no_vig": ({a: no_vig[a], b: no_vig[b]} if no_vig else None),
           "overround": no_vig["overround"] if no_vig else None,
           "difference": ({a: share - no_vig[a], b: (1 - share) - no_vig[b]} if no_vig and share is not None else None),
           "basis": "joint-draw share at the quoted line, pushes excluded, minus the same book's no-vig share"}
    return out


def game_market(session: Session, game_id: Any, forecast: Any, *, now: datetime | None = None,
                price_note: dict[str, Any] | None = None) -> dict[str, Any]:
    """A CFB game's stored quotes, their clocks and the joint comparisons -- or why there are none."""

    moment = now or datetime.now(UTC)
    quotes = stored_quotes(session, [game_id], now=moment).get(str(game_id), [])
    if not quotes:
        return {"state": "NO_QUOTE_ROWS", "quotes": [], "comparisons": [], "coverage": price_note,
                "note": "No sportsbook quote is stored for this game; no market comparison is shown."}
    return {
        "state": "QUOTES_STORED" if any(q["fresh"] for q in quotes) else "STORED_REFERENCE_ONLY",
        "freshness_rule": f"older leg at most {QUOTE_FRESHNESS_MINUTES} minutes old",
        "quotes": quotes,
        "comparisons": [compare(forecast, q) for q in quotes],
        "coverage": price_note,
        "note": ("Research comparison against the joint simulation only. The paper policy tests the moneyline at "
                 "its T-30..T-15 checkpoint; that recorded decision is the authority, not this reading."),
    }


def joint_expected_points(pmfs: dict[str, Any] | None) -> dict[str, float] | None:
    """Mean home/away points of the joint draws themselves (never Ridge's expected points)."""

    def mean(key: str) -> float | None:
        pmf = (pmfs or {}).get(key) or {}
        if not pmf.get("values") or not pmf.get("draw_count"):
            return None
        return sum(v * c for v, c in zip(pmf["values"], pmf["counts"])) / float(pmf["draw_count"])

    home, away = mean("home_score"), mean("away_score")
    return None if home is None or away is None else {"home": home, "away": away}


__all__ = ["CHANNEL_LABEL", "DISTRIBUTION_CHANNEL", "EXCLUDED_CHANNELS", "JOINT_CHANNEL", "compare", "game_market",
           "joint_expected_points", "quote_clocks", "stored_quotes"]

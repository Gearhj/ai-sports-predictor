"""Where each of the four sports stands, stated plainly.

WHY THIS EXISTS
---------------
A sport with no game today used to render as a blank section, which a reader
cannot tell apart from a broken one. Every sport now reports the same fields --
next game, forecast, price, decision window, account, position -- and, when
nothing can happen yet, the exact prerequisite that is missing.

WHAT IT NEVER DOES
------------------
Evaluate a policy, request a price or create a record. It reads what the owners
already persisted and says what they would do next. "Not evaluated" is never
reported as "no bet": a game whose window has not opened has no disposition yet,
and the state says so.

EACH SPORT IS ISOLATED
----------------------
One sport's read failing must not blank the other three, so each is computed
separately and a failure becomes an explicit READ_FAILED state carrying the
error type, not a missing panel.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

ET = ZoneInfo("America/New_York")
SCHEMA_VERSION = "AISP_SPORT_READINESS_V1"

FINAL_WINDOW_OPENS = timedelta(minutes=30)
FINAL_WINDOW_CLOSES = timedelta(minutes=15)


def _et(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    aware = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    return aware.astimezone(ET).strftime("%a %b %-d, %-I:%M %p ET") if hasattr(aware, "strftime") else None


def _et_label(moment: datetime | None) -> str | None:
    """Portable (Windows has no %-d) human ET label."""
    if moment is None:
        return None
    aware = (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).astimezone(ET)
    hour = aware.hour % 12 or 12
    return f"{aware:%a %b} {aware.day}, {hour}:{aware:%M} {'AM' if aware.hour < 12 else 'PM'} ET"


def _iso(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).astimezone(UTC).isoformat()


def _matchup(session: Session, game_id: Any) -> str:
    rows = session.execute(text("""
        select gt.is_home, coalesce(t.abbreviation, t.name) from game_teams gt
        join teams t on t.id = gt.team_id where gt.game_id = :g"""), {"g": str(game_id)}).all()
    home = next((name for is_home, name in rows if is_home), "?")
    away = next((name for is_home, name in rows if not is_home), "?")
    return f"{away} @ {home}"


def _account(session: Session, sport: str) -> dict[str, Any]:
    row = session.execute(text("""
        select a.season_id, a.status, f.idempotency_key, f.amount, f.effective_on
        from experiment_accounts a
        left join experiment_account_fundings f on f.account_id = a.id and f.kind = 'SEASON_OPENING'
        where a.sport = :s order by f.funded_at desc nulls last limit 1"""), {"s": sport}).first()
    if row is None or row[2] is None:
        return {"funded": False, "label": f"No {sport} bankroll"}
    realized = session.execute(text("""
        select coalesce(sum(pr.profit_loss), 0) from position_results pr
        join official_asp_positions p on p.id = pr.position_id
        join shadow_experiments e on e.id = p.shadow_experiment_id
        join experiment_account_memberships m on m.experiment_id = e.id
        join experiment_accounts a on a.id = m.account_id
        where a.sport = :s and m.role = 'FUNDED_ERA'"""), {"s": sport}).scalar() or 0
    reserved = session.execute(text("""
        select coalesce(sum(p.stake), 0) from official_asp_positions p
        join shadow_experiments e on e.id = p.shadow_experiment_id
        join experiment_account_memberships m on m.experiment_id = e.id
        join experiment_accounts a on a.id = m.account_id
        where a.sport = :s and m.role = 'FUNDED_ERA' and p.status = 'OPEN'"""), {"s": sport}).scalar() or 0
    contribution = float(row[3])
    realized_bankroll = round(contribution + float(realized), 2)
    return {
        "funded": True, "season_id": row[0], "status": str(row[1]),
        "funding_key": row[2], "contribution": contribution,
        "effective_on": row[4].isoformat() if row[4] else None,
        "realized_bankroll": realized_bankroll,
        "available_capital": round(realized_bankroll - float(reserved), 2),
        "label": f"${realized_bankroll:,.2f} virtual bankroll",
    }


def _positions(session: Session, sport: str, game_id: Any | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"s": sport}
    game_filter = ""
    if game_id is not None:
        game_filter = "and mk.game_id = :g"
        params["g"] = str(game_id)
    open_count, settled_count = session.execute(text(f"""
        select count(*) filter (where p.status = 'OPEN'), count(*) filter (where p.status <> 'OPEN')
        from official_asp_positions p
        join shadow_experiments e on e.id = p.shadow_experiment_id
        join market_outcomes mo on mo.id = p.market_outcome_id
        join markets mk on mk.id = mo.market_id
        where e.sport = :s {game_filter}"""), params).one()
    return {"open": int(open_count or 0), "settled": int(settled_count or 0)}


def _window(starts_at: datetime) -> dict[str, Any]:
    start = starts_at if starts_at.tzinfo else starts_at.replace(tzinfo=UTC)
    opens, closes = start - FINAL_WINDOW_OPENS, start - FINAL_WINDOW_CLOSES
    return {"opens_at": _iso(opens), "closes_at": _iso(closes),
            "label": f"{_et_label(opens)} – {_et_label(closes).split(', ')[-1]}",
            "_opens": opens, "_closes": closes}


def _state(code: str, label: str, **extra: Any) -> dict[str, Any]:
    return {"state": code, "label": label, **extra}


# ----------------------------------------------------------------- MLB ----

def _mlb(session: Session, now: datetime) -> dict[str, Any]:
    from app.services.slate_status import slate_status

    today = now.astimezone(ET).date()
    slate = slate_status(session, day=today, now=now)
    games = slate.get("games", [])
    upcoming = sorted((g for g in games if datetime.fromisoformat(g["starts_at"]) > now),
                      key=lambda g: g["starts_at"])
    v18 = session.execute(text("""
        select count(distinct sf.canonical_game_id) from shadow_forecasts sf
        join games g on g.id = sf.canonical_game_id
        where sf.engine_id = 'MLB_V18_WHOLE_GAME'
          and g.starts_at >= :lo and g.starts_at < :hi"""),
        {"lo": datetime.combine(today, datetime.min.time(), ET),
         "hi": datetime.combine(today + timedelta(days=1), datetime.min.time(), ET)}).scalar() or 0

    summary = {
        "games": len(games),
        "controlling_forecasts": int(v18),
        "with_fresh_price": sum(1 for g in games if (g.get("quote") or {}).get("satisfies_policy_freshness")),
        "positions": sum(int(g.get("positions") or 0) for g in games),
    }
    base = {"sport": "MLB", "label": "MLB", "policy_version": "AISP_PRACTICAL_PREGAME_POLICY_V2_1",
            "controlling_engine": "MLB_V18_WHOLE_GAME", "slate": summary,
            "account": _account(session, "MLB")}

    if not upcoming:
        return {**base,
                "next_game": None,
                "forecast": _state("NO_GAME_REMAINING", "No MLB game remains today"),
                "price": _state("NO_GAME_REMAINING", "No price collection pending today"),
                "decision": _state("NO_WINDOW_TODAY", "No decision window remains today"),
                "position": _state("POSITIONS_RECORDED" if summary["positions"] else "NONE_TODAY",
                                   f"{summary['positions']} position(s) recorded today"),
                "pipeline": {"ready": True, "blocker": None,
                             "could_qualify": "Next slate evaluates normally."}}

    game = upcoming[0]
    starts = datetime.fromisoformat(game["starts_at"])
    window = _window(starts)
    forecast = game.get("forecast")
    quote = game.get("quote")
    has_v18 = bool(forecast and forecast.get("engine_id") == "MLB_V18_WHOLE_GAME")
    if has_v18:
        fc = _state("CONTROLLING_FORECAST_AVAILABLE", "V18 controlling forecast available",
                    identity=forecast.get("forecast_id"))
    else:
        fc = _state("FORECAST_AWAITING_PRODUCER",
                    "Forecast awaiting final pregame evidence — V18 not yet published for this game")

    collection_opens = window["_closes"] - timedelta(minutes=240)
    if quote and quote.get("satisfies_policy_freshness"):
        price = _state("FRESH_PRICE", f"Fresh price available ({quote.get('sportsbook')}); decision window not open"
                       if now < window["_opens"] else "Fresh price available")
    elif quote:
        price = _state("STORED_PRICE_STALE",
                       f"Stored price is past the freshness limit; collection opens {_et_label(collection_opens)}"
                       if now < collection_opens else "Stored price stale; collection window open",
                       next_collection_at=_iso(collection_opens))
    else:
        price = _state("NO_PRICE_YET", f"Price collection opens at {_et_label(collection_opens)}"
                       if now < collection_opens else "Collection window open; no price stored yet",
                       next_collection_at=_iso(collection_opens))

    if now < window["_opens"]:
        decision = _state("WINDOW_NOT_OPEN", f"Decision window {window['label']}", window=_strip(window))
    elif now <= window["_closes"]:
        decision = _state("WINDOW_OPEN", "Decision window open now", window=_strip(window))
    else:
        decision = _state("WINDOW_CLOSED", "Decision window closed", window=_strip(window))

    blocker = None
    if not has_v18:
        blocker = "No V18 controlling forecast for the next game yet."
    elif not (quote and quote.get("satisfies_policy_freshness")) and now >= collection_opens:
        blocker = "No decision-fresh two-sided price yet."
    return {**base,
            "next_game": {"game_id": game["game_id"], "matchup": _matchup(session, game["game_id"]),
                          "starts_at": _iso(starts), "starts_label": _et_label(starts)},
            "forecast": fc, "price": price, "decision": decision,
            "position": _state("POSITIONS_RECORDED" if summary["positions"] else "NONE_YET",
                               f"{summary['positions']} position(s) recorded today" if summary["positions"]
                               else "No position yet today"),
            "pipeline": {"ready": True, "blocker": blocker,
                         "could_qualify": ("Yes, if a V18 forecast and a fresh two-sided price are present "
                                           "in the window and the adjusted edge clears the policy.")}}


def _strip(window: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in window.items() if not k.startswith("_")}


# ----------------------------------------------------------------- NFL ----

def _nfl(session: Session, now: datetime) -> dict[str, Any]:
    from app.services import nfl_paper_eligibility as eligibility
    from app.services.sgo_nfl_pregame import WINDOW_OPENS_BEFORE_KICKOFF

    base = {"sport": "NFL", "label": "NFL", "policy_version": "AISP_NFL_PAPER_POLICY_V1",
            "controlling_engine": "NFL_M1_XGBOOST", "account": _account(session, "NFL")}
    game = session.execute(text("""
        select g.id, g.starts_at from games g join leagues l on l.id = g.league_id
        where l.code = 'NFL' and g.starts_at > :now order by g.starts_at limit 1"""), {"now": now}).first()
    if game is None:
        return {**base, "next_game": None,
                "forecast": _state("NO_GAME_SCHEDULED", "No NFL game scheduled"),
                "price": _state("NO_GAME_SCHEDULED", "No collection planned"),
                "decision": _state("NO_WINDOW", "No decision window scheduled"),
                "position": _state("NONE", "No open NFL position", **_positions(session, "NFL")),
                "pipeline": {"ready": True, "blocker": "No NFL game scheduled.", "could_qualify": None}}

    gid, starts = game
    window = _window(starts)
    # The same forecast the decision owner will evaluate (FINAL_PREGAME first,
    # then the newest CURRENT), so readiness and the checkpoint cannot disagree
    # about which object the permission has to bind.
    from app.services.nfl_paper_decision import _forecast
    row = _forecast(session, gid)
    forecast = ((row.forecast_id, row.snapshot_type, row.snapshot_version, row.engine_version, row.event_id)
                if row is not None else None)
    if forecast:
        fc = _state("RESEARCH_FORECAST_AVAILABLE",
                    f"NFL M1 {forecast[1]} v{forecast[2]} available; a FINAL_PREGAME successor may be "
                    "generated inside the decision window",
                    identity=forecast[0], engine_version=forecast[3])
    else:
        fc = _state("FORECAST_AWAITING_PRODUCER", "Forecast awaiting producer publication")

    newest = session.execute(text("""
        select max(os.observed_at), string_agg(distinct os.source, ',') from odds_snapshots os
        join market_outcomes mo on mo.id = os.market_outcome_id join markets mk on mk.id = mo.market_id
        where mk.game_id = :g"""), {"g": str(gid)}).first()
    collection_opens = (starts if starts.tzinfo else starts.replace(tzinfo=UTC)) - WINDOW_OPENS_BEFORE_KICKOFF
    if newest and newest[0] and (now - newest[0]).total_seconds() <= 240 * 60:
        price = _state("FRESH_PRICE", "Fresh price available; decision window not open")
    else:
        age_days = (now - newest[0]).days if newest and newest[0] else None
        price = _state(
            "STORED_REFERENCE_ONLY" if age_days is not None else "NO_PRICE_YET",
            (f"Stored reference only ({newest[1]}, {age_days} days old); price collection opens "
             f"{_et_label(collection_opens)}") if age_days is not None
            else f"Price collection opens {_et_label(collection_opens)}",
            next_collection_at=_iso(collection_opens))

    elig = (eligibility.check(session, event_id=forecast[4], forecast_id=forecast[0], experiment_id="readiness")
            if forecast and forecast[4] else {"eligible": False, "reason": "NO_FORECAST"})
    if forecast and forecast[4]:
        elig = eligibility.successor_gap(session, event_id=forecast[4], forecast_id=forecast[0], binding=elig)
    if now < window["_opens"]:
        decision = _state("WINDOW_NOT_OPEN", f"Decision window {window['label']}", window=_strip(window))
    else:
        decision = _state("WINDOW_OPEN" if now <= window["_closes"] else "WINDOW_CLOSED",
                          "Decision window open now" if now <= window["_closes"] else "Decision window closed",
                          window=_strip(window))
    if elig.get("eligible"):
        blocker = None
        could = ("Yes, if the separately collected SportsGameOdds pair (same provider, book and market, "
                 "both sides) is fresh on its older leg inside the window and the tie-adjusted edge clears "
                 "2 points. A FINAL_PREGAME successor generated in the window needs its own supplement.")
    elif elig.get("reason") == "AWAITING_SUCCESSOR_ELIGIBILITY":
        blocker = ("A newer forecast replaced the authorized object; awaiting the producer's successor "
                   "eligibility supplement (AWAITING_SUCCESSOR_ELIGIBILITY).")
        could = "Only once the successor supplement for the newer forecast is imported."
    else:
        blocker = ("Paper policy awaiting the producer's eligibility record for this game's forecast "
                   f"({elig.get('reason')}).")
        could = ("Only once a producer eligibility record binds this game's forecast; then a fresh "
                 "two-sided price inside the window and a qualifying edge.")
    return {**base,
            "next_game": {"game_id": str(gid), "matchup": _matchup(session, gid),
                          "starts_at": _iso(starts), "starts_label": _et_label(starts)},
            "forecast": fc, "price": price, "decision": decision,
            "eligibility": {"eligible": bool(elig.get("eligible")), "reason": elig.get("reason"),
                            "supplement_sha256": elig.get("supplement_sha256"),
                            "forecast_id": forecast[0] if forecast else None},
            "position": _state("NONE_YET", "No position for the next game", **_positions(session, "NFL", gid)),
            "pipeline": {"ready": True, "blocker": blocker, "could_qualify": could}}


# ------------------------------------------------------------ NBA / CFB ----

def _nba(session: Session, now: datetime) -> dict[str, Any]:
    from app.services.research_paper_policy import NBA, eligibility_status

    base = {"sport": "NBA", "label": "NBA", "policy_version": NBA.policy_version,
            "controlling_engine": NBA.controlling_engine,
            "controlling_configuration": NBA.controlling_configuration,
            "account": _account(session, "NBA")}
    arm = session.execute(text("""
        select a.forecast_id, a.home_win_probability, a.away_win_probability, g.id, g.starts_at
        from nba_joint_comparison_arms a join games g on g.id = a.canonical_game_id
        where a.arm = 'baseline' and a.configuration_identity = :c and g.starts_at > :now
        order by g.starts_at limit 1"""), {"c": NBA.controlling_configuration, "now": now}).first()
    if arm is None:
        return {**base, "next_game": None,
                "forecast": _state("NO_ELIGIBLE_BASELINE", "No upcoming NBA baseline forecast imported"),
                "price": _state("NOT_PLANNED", "No collection planned"),
                "decision": _state("NO_WINDOW", "No decision window scheduled"),
                "position": _state("NONE", "No NBA position", **_positions(session, "NBA")),
                "pipeline": {"ready": False, "blocker": "No upcoming NBA baseline forecast.", "could_qualify": None}}
    forecast_id, home_p, away_p, gid, starts = arm
    window = _window(starts)
    elig = eligibility_status(session, NBA, forecast_id=forecast_id, event_id=str(gid))
    collection_opens = window["_closes"] - timedelta(minutes=NBA.max_quote_age_minutes)
    return {**base,
            "next_game": {"game_id": str(gid), "matchup": _matchup(session, gid),
                          "starts_at": _iso(starts), "starts_label": _et_label(starts)},
            "forecast": _state("RESEARCH_FORECAST_AVAILABLE",
                               f"Joint baseline available: home {home_p:.2%} / away {away_p:.2%}",
                               identity=forecast_id),
            "price": _state("COLLECTION_NOT_OPEN",
                            "No price yet. SportsGameOdds' NBA league identity is not in any retained "
                            f"evidence, so no request is planned; the earliest useful collection is "
                            f"{_et_label(collection_opens)}.",
                            next_collection_at=_iso(collection_opens),
                            blocker="SGO_LEAGUE_IDENTITY_UNVERIFIED"),
            "decision": _state("WINDOW_NOT_OPEN", f"Decision window {window['label']}", window=_strip(window)),
            "eligibility": {"eligible": elig["eligible"], "reason": elig["state"], "contract": elig["contract"]},
            "position": _state("NONE_YET", "No NBA position", **_positions(session, "NBA")),
            "pipeline": {"ready": True,
                         "blocker": ("Research forecast available; paper policy awaiting producer eligibility "
                                     f"evidence ({NBA.eligibility_contract}).") if not elig["eligible"] else
                                    ("Eligible forecast; price intake blocked until SportsGameOdds' NBA league "
                                     "identity is verified (SGO_LEAGUE_IDENTITY_UNVERIFIED)."),
                         "could_qualify": (("Not while eligibility is missing. With it: " if not elig["eligible"]
                                            else "Yes, once there is ")
                                           + "a verified SportsGameOdds NBA league identity, a fresh same-book "
                                           "two-sided price inside the window, and an edge of at least 2 points.")}}


def _cfb(session: Session, now: datetime) -> dict[str, Any]:
    from app.models import CfbResearchForecast
    from app.services.research_paper_policy import CFB, eligibility_status

    base = {"sport": "CFB", "label": "College Football", "policy_version": CFB.policy_version,
            "controlling_engine": CFB.controlling_engine,
            "controlling_configuration": CFB.controlling_configuration,
            "account": _account(session, "CFB")}
    row = session.scalar(select(CfbResearchForecast)
                         .where(CfbResearchForecast.captured_kickoff_utc > now)
                         .order_by(CfbResearchForecast.captured_kickoff_utc).limit(1))
    if row is None:
        return {**base, "next_game": None,
                "forecast": _state("NO_FORECAST", "No upcoming College Football research forecast"),
                "price": _state("NOT_PLANNED", "No collection planned"),
                "decision": _state("NO_WINDOW", "No decision window scheduled"),
                "position": _state("NONE", "No College Football position"),
                "pipeline": {"ready": False, "blocker": "No upcoming forecast.", "could_qualify": None}}
    window = _window(row.captured_kickoff_utc)
    elig = eligibility_status(session, CFB, forecast_id=row.forecast_id, event_id=row.source_game_id)
    game_row = session.execute(text("""
        select entity_id from external_id_mappings where entity_type = 'game' and source = 'CFBD'
          and external_id = :x limit 1"""), {"x": row.source_game_id}).first()
    has_game_row = game_row is not None
    blockers = []
    if not elig["eligible"]:
        blockers.append(f"research only: no producer paper-eligibility record binds this exact forecast "
                        f"({CFB.eligibility_contract})")
    if not has_game_row:
        blockers.append("no canonical College Football game identity, because AISP has no admitted "
                        "College Football schedule source — a quote cannot be bound or a position recorded")
    return {**base,
            "next_game": {"source_game_id": row.source_game_id,
                          "matchup": f"{row.away_team_name} @ {row.home_team_name}",
                          "starts_at": _iso(row.captured_kickoff_utc),
                          "starts_label": _et_label(row.captured_kickoff_utc),
                          "canonical_game_id": str(game_row[0]) if has_game_row else None,
                          "research_href": f"/college-football/games/{row.source_game_id}"},
            "forecast": _state("RESEARCH_FORECAST_AVAILABLE",
                               f"Research pick: {row.home_team_name if row.joint_home_win_probability >= 0.5 else row.away_team_name} "
                               f"(joint {max(row.joint_home_win_probability, row.joint_away_win_probability):.2%})",
                               identity=row.forecast_id),
            "price": (_state("COLLECTION_BLOCKED",
                             "No price. No request is made: SportsGameOdds' College Football league identity "
                             "(documented as NCAAF) is not verified in retained evidence. The game itself is "
                             "bound to its CFBD id, so a verified quote could be matched to it.",
                             blocker="SGO_LEAGUE_IDENTITY_UNVERIFIED")
                      if has_game_row else
                      _state("COLLECTION_BLOCKED",
                             "No price. No request is made: SportsGameOdds' College Football league identity "
                             "is not in retained evidence, and without a canonical game identity a returned "
                             "quote could not be bound.",
                             blocker="SGO_LEAGUE_IDENTITY_UNVERIFIED;NO_CANONICAL_GAME_IDENTITY")),
            "decision": _state("WINDOW_NOT_OPEN", f"Decision window {window['label']}", window=_strip(window)),
            "eligibility": {"eligible": elig["eligible"], "reason": elig["state"], "contract": elig["contract"]},
            "position": _state("NONE_YET", "No College Football position", **_positions(session, "CFB")),
            "pipeline": {"ready": not blockers,
                         "blocker": ("Research forecast available; " + "; ".join(blockers) + ".") if blockers else None,
                         "could_qualify": ("No. Even with eligibility, a position needs a canonical game and a "
                                           "bound two-sided price.") if not has_game_row
                                 else ("No. This forecast is research only, and no verified College Football "
                                       "price source exists.") if not elig["eligible"]
                                 else "Only with a verified College Football price source and a fresh "
                                      "two-sided quote inside the window."}}


BUILDERS = (("MLB", _mlb), ("NFL", _nfl), ("NBA", _nba), ("CFB", _cfb))


def sport_readiness(session: Session, *, now: datetime | None = None) -> dict[str, Any]:
    moment = now or datetime.now(UTC)
    sports = []
    for code, builder in BUILDERS:
        try:
            sports.append(builder(session, moment))
        except Exception as error:  # noqa: BLE001 - one sport must not blank the rest
            session.rollback()
            sports.append({"sport": code, "label": code,
                           "read_state": "READ_FAILED", "error_type": type(error).__name__,
                           "pipeline": {"ready": False,
                                        "blocker": f"The {code} readiness read failed ({type(error).__name__}).",
                                        "could_qualify": None}})
    return {"schema_version": SCHEMA_VERSION, "generated_at": _iso(moment),
            "authority": "Virtual research only. No real-money wagering and no execution outside AISP.",
            "sports": sports}


__all__ = ["SCHEMA_VERSION", "sport_readiness"]

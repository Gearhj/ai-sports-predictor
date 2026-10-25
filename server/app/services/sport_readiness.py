"""Where each paper sport (MLB, NFL, NBA, College Football, NHL) stands, stated plainly.

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
One sport's read failing must not blank the others, so each is computed
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
    # Corrections recorded beside a settlement (position_settlement_adjustments),
    # counted exactly as the account staking basis counts them.
    realized = float(realized) + float(session.execute(text("""
        select coalesce(sum(adj.profit_loss_delta), 0) from position_settlement_adjustments adj
        join official_asp_positions p on p.id = adj.position_id
        join shadow_experiments e on e.id = p.shadow_experiment_id
        join experiment_account_memberships m on m.experiment_id = e.id
        join experiment_accounts a on a.id = m.account_id
        where a.sport = :s and m.role = 'FUNDED_ERA'"""), {"s": sport}).scalar() or 0)
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
        # A raw-SQL date is a date on PostgreSQL and ISO text on SQLite; both read the same.
        "effective_on": (row[4].isoformat() if hasattr(row[4], "isoformat") else str(row[4])[:10]) if row[4] else None,
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
    # slate_status returns every league's scheduled games; this card is MLB's only
    # (a CFB game must not appear as MLB's next game or add to MLB's counts).
    games = [g for g in slate.get("games", []) if g.get("league_code") == "MLB"]
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
    from app.services.nfl_paper_decision import operational_start,paired_quotes,clock_bound_pairs
    operative = operational_start(session,gid,moment=now)
    window = _window(operative)
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

    from app.services.nfl_paper_policy import PERMITTED_QUOTE_PROVIDERS
    asof=min(now,operative-FINAL_WINDOW_CLOSES)
    pairs=[pair for pair in paired_quotes(session,gid,moment=asof) if (PERMITTED_QUOTE_PROVIDERS.get(pair["source"]) or {}).get("permitted")]
    qualified,refused=clock_bound_pairs(session,gid,pairs,moment=asof)
    fresh=[pair for pair in qualified if 0 <= (asof-datetime.fromisoformat(pair["pair_observed_at"])).total_seconds() <= 240*60]
    collection_opens=operative-WINDOW_OPENS_BEFORE_KICKOFF
    if fresh:
        price=_state("FRESH_PRICE",f"{len(fresh)} identity-bound same-book pair(s) fresh on BOTH original legs",pairs=len(fresh),evaluated_asof=_iso(asof))
    else:
        price=_state("QUOTE_EVENT_CLOCK_UNVERIFIED" if refused else "NO_USABLE_PAIR",f"No currently identity-bound same-book pair; collection opens {_et_label(collection_opens)}",next_collection_at=_iso(collection_opens),clock_refusals=refused)

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
                          "starts_at": _iso(starts), "starts_label": _et_label(starts), "operational_start":_iso(operative)},
            "forecast": fc, "price": price, "decision": decision,
            "eligibility": {"eligible": bool(elig.get("eligible")), "reason": elig.get("reason"),
                            "supplement_sha256": elig.get("supplement_sha256"),
                            "forecast_id": forecast[0] if forecast else None},
            "position": _state("NONE_YET", "No position for the next game", **_positions(session, "NFL", gid)),
            "pipeline": {"ready": True, "blocker": blocker, "could_qualify": could}}


# ------------------------------------------------------------ NBA / CFB ----

def _nba(session: Session, now: datetime) -> dict[str, Any]:
    from app.core.config import get_settings
    from app.nba.gen1_read import readiness as gen1_readiness
    from app.services.research_paper_policy import NBA, eligibility_status

    settings = get_settings()
    base = {"sport": "NBA", "label": "NBA", "policy_version": NBA.policy_version,
            "controlling_engine": NBA.controlling_engine,
            "controlling_configuration": NBA.controlling_configuration,
            "controlling_channel": "JOINT_DISTRIBUTION",
            "account": _account(session, "NBA")}
    # Per-component truth for the Gen1 consumer: handoff, forecast, eligibility,
    # price, decision and outcomes, each with what is present and what is due.
    try:
        base["components"] = gen1_readiness(session, settings, now=now)
    except Exception as error:  # noqa: BLE001 -- NBA's new tables may not be migrated yet
        session.rollback()
        base["components"] = {"read_state": "READ_FAILED", "error_type": type(error).__name__}
    production = _nba_production(session, now, base, settings)
    if production is not None:
        return production
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


def _nba_production(session: Session, now: datetime, base: dict[str, Any], settings: Any) -> dict[str, Any] | None:
    """The next NBA game carrying a verified PRODUCTION package, or None to fall back to research."""

    from app.models import NbaGen1Forecast
    from app.nba.gen1_production import present_eligibility
    from app.nba.gen1_read import channels, price_note
    from app.services.research_paper_policy import NBA, eligibility_status

    try:
        row = session.scalar(select(NbaGen1Forecast).where(
            NbaGen1Forecast.captured_tipoff_utc > now + timedelta(minutes=15))
            .order_by(NbaGen1Forecast.captured_tipoff_utc, NbaGen1Forecast.exchange_published_utc.desc()).limit(1))
    except Exception:  # noqa: BLE001 -- table absent before the migration: fall back
        session.rollback()
        return None
    if row is None or row.canonical_game_id is None:
        return None
    window = _window(row.captured_tipoff_utc)
    lease = present_eligibility(session, row, now=now)
    elig = eligibility_status(session, NBA, forecast_id=row.forecast_id, event_id=row.codex_game_id)
    note = price_note(session, settings)
    # The Odds API lane, reported by its own switches (collector, consumer,
    # credit lane, source amendment clock). Only when every one is in force does
    # it speak for the price; otherwise the SportsGameOdds note stands and the
    # lane's state rides beside it, so an off lane is never mistaken for a plan.
    try:
        odds_api = _nba_odds_api_price(session, row.canonical_game_id, now, settings)
    except Exception as error:  # noqa: BLE001 -- explanatory only; never blanks the NBA panel
        session.rollback()
        odds_api = {"lane": _state("READ_FAILED", "The NBA Odds API lane state could not be read.",
                                   source="THE_ODDS_API", error_type=type(error).__name__), "price": None}
    positions_on = bool(getattr(settings, "nba_paper_decisions_enabled", False))
    blockers = []
    if not positions_on:
        blockers.append("the NBA paper decision owner is not active")
    if not lease.get("eligible"):
        blockers.append(f"discovery lease {lease.get('state')}")
    if not elig["eligible"]:
        blockers.append(f"no producer paper-eligibility record yet ({NBA.eligibility_contract})")
    if odds_api.get("price") is not None:
        price = odds_api["price"]
        if price.get("blocker"):
            blockers.append(f"no NBA price ({price['blocker']})")
    else:
        price = _state("COLLECTION_BLOCKED", f"No price. {note.get('detail', '')}",
                       blocker=("SGO_LEAGUE_IDENTITY_UNVERIFIED" if not (note.get("binding") or {}).get("resolved")
                                else note.get("reason")), odds_api_lane=odds_api["lane"])
        blockers.append(f"no NBA price ({note.get('reason')}; The Odds API lane {odds_api['lane']['state']})")
    if price.get("state") != "PRICE_HELD" and not price.get("blocker"):
        blockers.append("a fresh paired NBA quote is not held yet")
    components = {**(base.get("components") or {}),
                  "eligibility": _state(lease.get("state", "UNKNOWN"), lease.get("detail", "Current discovery lease state"),
                                        paper_record_state=elig.get("state"), paper_record_eligible=elig.get("eligible")),
                  "price": price}
    ch = channels(row)
    joint, elo = ch["JOINT_DISTRIBUTION"], ch["ELO_WINNER"]
    favourite = row.home_team_code if (joint["home"] or 0) >= 0.5 else row.away_team_code
    return {**base, "components": components,
            "next_game": {"game_id": str(row.canonical_game_id),
                          "matchup": f"{row.away_team_code} @ {row.home_team_code}",
                          "starts_at": _iso(row.captured_tipoff_utc), "starts_label": _et_label(row.captured_tipoff_utc),
                          "research_href": f"/nba/games/{row.canonical_game_id}"},
            "forecast": _state("PRODUCTION_PACKAGE_AVAILABLE",
                               f"Production package: {favourite} joint {max(joint['home'], joint['away']):.2%} · "
                               f"Elo {max(elo['home'], elo['away']):.2%} (separate channels) · "
                               f"discovery {str(lease.get('state', '')).replace('_', ' ').lower()}",
                               identity=row.forecast_id),
            "price": price,
            "decision": _state("WINDOW_NOT_OPEN" if positions_on else "OWNER_INACTIVE",
                               f"Decision window {window['label']}" if positions_on
                               else "The NBA paper checkpoint is not active", window=_strip(window)),
            "eligibility": {"eligible": elig["eligible"], "reason": elig["state"], "contract": elig["contract"],
                            "discovery_lease": lease.get("state")},
            "position": _state("NONE_YET", "No NBA position", **_positions(session, "NBA")),
            "pipeline": {"ready": not blockers, "blocker": "; ".join(blockers).capitalize() + ".",
                         "could_qualify": ("Pending: " + "; ".join(blockers) + ".") if blockers else
                                           "Current prerequisites are present; the natural checkpoint decides qualification and funding."}}


def _cfb(session: Session, now: datetime) -> dict[str, Any]:
    from app.cfb.discovery_projection import projected_eligibility
    from app.cfb.production_import import current_forecast_for_game
    from app.core.config import get_settings
    from app.models import CfbResearchForecast
    from app.services.research_paper_policy import CFB, eligibility_status

    settings = get_settings()
    active = bool(settings.cfb_production_consumer_enabled)
    positions_on = active and bool(settings.cfb_paper_decisions_enabled)
    base = {"sport": "CFB", "label": "College Football", "policy_version": CFB.policy_version,
            "controlling_engine": CFB.controlling_engine,
            "controlling_configuration": CFB.controlling_configuration,
            "controlling_channel": "JOINT_PAIRED_RESIDUAL_BASELINE",
            "account": _account(session, "CFB")}
    upcoming = session.scalar(select(CfbResearchForecast.source_game_id)
                              .where(CfbResearchForecast.captured_kickoff_utc > now + timedelta(minutes=15))
                              .order_by(CfbResearchForecast.captured_kickoff_utc).limit(1))
    row = current_forecast_for_game(session, upcoming) if upcoming else None
    if row is None:
        return {**base, "next_game": None,
                "forecast": _state("NO_FORECAST", "No upcoming College Football forecast"),
                "price": _state("NOT_PLANNED", "No collection planned"),
                "decision": _state("NO_WINDOW", "No decision window scheduled"),
                "position": _state("NONE", "No College Football position", **_positions(session, "CFB")),
                "pipeline": {"ready": False, "blocker": "No upcoming forecast.", "could_qualify": None}}
    window = _window(row.captured_kickoff_utc)
    elig = eligibility_status(session, CFB, forecast_id=row.forecast_id, event_id=f"cfbd:{row.source_game_id}")
    # The lease the checkpoint would read if it ran now: the recorded manifest, or -- between imports --
    # the producer's newer manifest verified from bytes at its pointer (read-only; nothing is imported).
    lease = projected_eligibility(session, settings, row, now=now) if row.origin == "PRODUCTION_DISCOVERY" else {
        "state": "NOT_IN_PRODUCTION_DISCOVERY", "eligible": False}
    game_row = session.execute(text("""
        select entity_id from external_id_mappings where entity_type = 'game' and source = 'CFBD'
          and external_id = :x limit 1"""), {"x": row.source_game_id}).first()
    # The decision engine's own authorities, evaluated at `now`: the pinned
    # handoff's virtual-policy grant and the policy's effective quote sources
    # (the frozen SportsGameOdds record plus any source amendment in force).
    from app.cfb.paper_decision import _grant
    from app.services.research_paper_policy import effective_quote_sources

    grant = _grant(settings)
    grant_expired = bool(grant.get("valid_until") and now >= grant["valid_until"])
    sources, _amendments = effective_quote_sources(CFB, settings, moment=now)
    # Retained-pair usability depends on source permission, not on whether the
    # collector is switched on; the collector's state is reported separately.
    if "THE_ODDS_API" in sources:
        price = _cfb_odds_api_price(session, game_row[0] if game_row else None, now, sources=sources,
                                    collector_enabled=bool(getattr(settings, "cfb_odds_api_enabled", False)))
        price_blocker = price.get("blocker")
    else:
        try:
            from app.services.command_center import _cfb_price_note

            price_note = _cfb_price_note(session)
        except Exception:  # noqa: BLE001
            price_note = {"reason": "PRICE_STATE_UNAVAILABLE", "detail": "Price state could not be read."}
        price = _state("COLLECTION_BLOCKED", f"No price. {price_note.get('detail', '')}",
                       blocker=price_note.get("reason"))
        price_blocker = price_note.get("reason")
    blockers = []
    if not positions_on:
        blockers.append("the CFB paper decision owner is not active")
    if grant_expired:
        blockers.append(f"the virtual-policy grant expired at {_iso(grant['valid_until'])}")
    if not lease.get("eligible"):
        blockers.append(f"discovery lease {lease.get('label') or lease.get('state')}"
                        if (lease.get("renewal") or {}).get("state") == "RENEWAL_DUE"
                        else f"discovery lease {lease.get('state')}")
    if not elig["eligible"]:
        blockers.append(f"no producer paper-eligibility record yet ({CFB.eligibility_contract})")
    if price_blocker:
        blockers.append(f"no governed price ({price_blocker})")
    counts = _positions(session, "CFB")
    game_counts = _positions(session, "CFB", game_row[0]) if game_row else {"open": 0, "settled": 0}
    favourite = row.home_team_name if row.joint_home_win_probability >= 0.5 else row.away_team_name
    return {**base,
            "next_game": {"source_game_id": row.source_game_id,
                          "matchup": f"{row.away_team_name} @ {row.home_team_name}",
                          "starts_at": _iso(row.captured_kickoff_utc),
                          "starts_label": _et_label(row.captured_kickoff_utc),
                          "canonical_game_id": str(game_row[0]) if game_row else None,
                          "research_href": f"/college-football/games/{row.source_game_id}"},
            "forecast": _state("PROSPECTIVE_FORECAST_AVAILABLE",
                               f"Forecast: {favourite} "
                               f"(joint {max(row.joint_home_win_probability, row.joint_away_win_probability):.2%}) · "
                               f"discovery {lease.get('label') or lease.get('state', '').replace('_', ' ').lower()}",
                               identity=row.forecast_id),
            "price": price,
            "decision": _cfb_decision(session, game_row[0] if game_row else None, window, now, positions_on),
            "eligibility": {"eligible": elig["eligible"], "reason": elig["state"], "contract": elig["contract"],
                            "discovery_lease": lease.get("state"), "evaluated_at": _iso(now),
                            "discovery": {k: lease.get(k) for k in ("state", "basis", "label", "pointer", "renewal",
                                                                    "lease_valid_until", "manifest_sha256",
                                                                    "manifest_created_utc", "recorded")
                                          if lease.get(k) is not None}},
            "authority": {"grant_sha256": grant.get("sha256"),
                          "grant_valid_until": _iso(grant["valid_until"]) if grant.get("valid_until") else None,
                          "grant_state": "EXPIRED" if grant_expired else ("VALID" if grant.get("sha256") else "NOT_PINNED"),
                          "permitted_quote_sources": list(sources)},
            # Sport-wide counts keep their existing top-level keys; the selected game's own counts are
            # reported separately so one game's state is never read as the sport's.
            "position": _state("OPEN_POSITIONS" if counts.get("open") else "NONE_YET",
                               (f"{counts.get('open')} open College Football position"
                                f"{'' if counts.get('open') == 1 else 's'} (all games); "
                                f"{game_counts.get('open')} on the next game")
                               if counts.get("open") else "No College Football position",
                               **counts, scope="SPORT", next_game=game_counts),
            "pipeline": {"ready": positions_on and not blockers,
                         "blocker": ("; ".join(blockers).capitalize() + ".") if blockers else None,
                         "could_qualify": (("Yes, if the model's adjusted edge against the freshest same-book "
                                            "moneyline pair clears the policy minimum at T-30..T-15 and the stake "
                                            "limits leave room; otherwise the checkpoint records a decline.")
                                           if not blockers else
                                           ("No: without a governed College Football price the checkpoint records "
                                            "NO_TWO_SIDED_QUOTE, a legitimate no-position result.") if price_blocker
                                           else "Not until the blockers above clear.")}}


def _cfb_odds_api_price(session: Session, game_id: Any, now: datetime, *, sources: tuple[str, ...] | list[str],
                        collector_enabled: bool) -> dict[str, Any]:
    """Governed CFB quote state for one game at ``now``, by the decision engine's own gates (read-only).

    Same order as the checkpoint: ``kickoff_bound_pairs`` (one provider and book,
    both moneyline legs retained at the game's CURRENT kickoff, older leg
    governing) -> permitted sources -> older-leg age within the policy maximum.
    The outcome names map to the engine's: NO_TWO_SIDED_QUOTE,
    NO_PERMITTED_PROVIDER, QUOTE_TOO_STALE.

    Three things stay distinct: a presently usable pair (no blocker); a future
    collection the enabled collector will still make before its window closes
    (no blocker yet, stored over-age pairs reported as reference only); and
    neither (blocker). The collector switch never makes a retained pair unusable;
    reading a retained pair authorizes no provider request. Nothing here writes.
    """

    from app.cfb.odds_api_price import BUY_CLOSES, BUY_OPENS
    from app.cfb.paper_decision import _current_game, kickoff_bound_pairs
    from app.services.research_paper_policy import CFB

    kickoff, _status = _current_game(session, game_id) if game_id is not None else (None, None)
    if kickoff is None:
        return {**_state("NO_CANONICAL_GAME", "No canonical game is bound for this forecast.",
                         source="THE_ODDS_API"), "blocker": "NO_CANONICAL_GAME"}
    opens, closes = kickoff - BUY_OPENS, kickoff - BUY_CLOSES
    collection = {"enabled": collector_enabled, "opens_at": _iso(opens), "closes_at": _iso(closes),
                  "ahead": collector_enabled and now < closes}
    common = {"source": "THE_ODDS_API", "retained_kickoff": _iso(kickoff), "collection": collection,
              "evaluated_at": _iso(now)}
    pairs = kickoff_bound_pairs(session, game_id, kickoff, moment=now)
    permitted = [p for p in pairs if p["source"] in sources]
    max_age = timedelta(minutes=CFB.max_quote_age_minutes)
    fresh = [p for p in permitted if now - datetime.fromisoformat(p["pair_observed_at"]) <= max_age]
    if fresh:
        newest = datetime.fromisoformat(fresh[0]["pair_observed_at"])
        age = int((now - newest).total_seconds() // 60)
        return _state("PRICE_HELD", f"{len(fresh)} usable same-book The Odds API moneyline pair(s) at the current "
                      f"kickoff; newest pair {_et_label(newest)} ({age} min old on its older leg).",
                      pairs=len(fresh), newest_pair_observed_at=_iso(newest), **common)
    if collection["ahead"]:
        detail = (f"; {len(permitted)} stored pair(s) older than {CFB.max_quote_age_minutes} min are reference only"
                  if permitted else "")
        return _state("COLLECTION_SCHEDULED", f"The Odds API collection {_et_label(opens)} – {_et_label(closes)}"
                      f"{detail}.", stale_reference_pairs=len(permitted), **common)
    if permitted:
        oldest_ok = datetime.fromisoformat(permitted[0]["pair_observed_at"])
        return {**_state("PRICE_HELD_STALE", f"{len(permitted)} stored pair(s), newest {_et_label(oldest_ok)}, all "
                         f"older than the {CFB.max_quote_age_minutes}-minute limit on their older leg; reference only.",
                         stale_reference_pairs=len(permitted), newest_pair_observed_at=_iso(oldest_ok), **common),
                "blocker": "QUOTE_TOO_STALE"}
    if pairs:
        return {**_state("NO_PERMITTED_PROVIDER", f"Stored pairs come only from {sorted({p['source'] for p in pairs})}.",
                         **common), "blocker": "NO_PERMITTED_PROVIDER"}
    return {**_state("NO_PRICE_HELD", "No same-book The Odds API moneyline pair was retained at this game's "
                     "current kickoff.", **common), "blocker": "NO_TWO_SIDED_QUOTE"}


def _cfb_decision(session: Session, game_id: Any, window: dict[str, Any], now: datetime,
                  owner_active: bool) -> dict[str, Any]:
    """Window phase at the evaluation clock, plus the persisted checkpoint record for the game, if any."""

    phase = ("OWNER_INACTIVE" if not owner_active else "WINDOW_NOT_OPEN" if now < window["_opens"]
             else "WINDOW_OPEN" if now <= window["_closes"] else "WINDOW_CLOSED")
    recorded = None
    if game_id is not None:
        row = session.execute(text("""
            select d.id, d.recommendation, coalesce(d.qualification_reason, d.recommendation_reason), d.created_at
              from shadow_experiment_decisions d
             where d.game_id = :g and d.sport = 'CFB'
             order by d.created_at desc limit 1"""), {"g": str(game_id)}).first()
        if row is not None:
            recorded = {"decision_id": str(row[0]), "recommendation": row[1], "reason": row[2],
                        "recorded_at": _iso(row[3])}
    labels = {"OWNER_INACTIVE": "The CFB paper checkpoint is not active",
              "WINDOW_NOT_OPEN": f"Decision window {window['label']}",
              "WINDOW_OPEN": f"Decision window open now ({window['label']})",
              "WINDOW_CLOSED": f"Decision window closed ({window['label']})"}
    label = labels[phase]
    if recorded:
        label += f" · recorded {recorded['recommendation']} ({recorded['reason']})"
    return _state(phase, label, window=_strip(window), recorded=recorded, evaluated_at=_iso(now))


# ----------------------------------------------------------------- NHL ----

def _nhl(session: Session, now: datetime) -> dict[str, Any]:
    """NHL under AISP_NHL_PAPER_POLICY_V1, read the way its checkpoint reads (read-only).

    The forecast is the one ``paper.pregame_forecast`` would hold now (an AISP
    in-process Gen1 row, or a producer-published one), its eligibility is the
    route the checkpoint takes for that row (``paper.eligibility_record``: the
    in-process contract, or the producer's record), and the price is
    ``paper.native_pairs`` at ``now`` under the policy's effective sources and
    240-minute freshness. The shared $100 allowance is read WITHOUT its lock
    (``is_effective`` + ``entered_detail``); ``room()`` is never called here,
    because it takes the cross-sport advisory lock the checkpoint relies on.
    Nothing is evaluated as a decision, requested, or written.
    """

    from app.core.config import get_settings
    from app.models import ExternalIdMapping, Game, GameStatus, League
    from app.models.nhl_research import NHL_GAME_SOURCE, NHL_LEAGUE_CODE, NhlResearchForecast
    from app.nhl import paper
    from app.services import shared_daily_allowance as shared
    from app.services.research_paper_policy import NHL

    settings = get_settings()
    era = paper.experiment(session)
    checkpoint_on = bool(getattr(settings, "nhl_paper_checkpoint_enabled", False))
    decisions_on = bool(getattr(settings, "nhl_paper_decisions_enabled", False))
    owner_active = era is not None and checkpoint_on
    base = {"sport": "NHL", "label": "NHL", "policy_version": NHL.policy_version,
            "controlling_engine": NHL.controlling_engine,
            "controlling_configuration": NHL.controlling_configuration,
            "controlling_channel": paper.CHANNEL,
            "era": {"experiment_id": str(era.id), "name": era.name} if era is not None else None,
            "account": _account(session, "NHL")}

    # Today's slate (Eastern), so the strip can say how many games hold a forecast.
    today = now.astimezone(ET).date()
    lo = datetime.combine(today, datetime.min.time(), ET).astimezone(UTC)
    hi = datetime.combine(today + timedelta(days=1), datetime.min.time(), ET).astimezone(UTC)
    slate_games = session.execute(
        select(Game.id, Game.starts_at).join(League, League.id == Game.league_id)
        .where(League.code == NHL_LEAGUE_CODE, Game.starts_at >= lo, Game.starts_at < hi)).all()
    with_forecast = 0
    if slate_games:
        starts = {gid: (s if s.tzinfo else s.replace(tzinfo=UTC)) for gid, s in slate_games}
        for gid, original in session.execute(
                select(NhlResearchForecast.game_id, NhlResearchForecast.scheduled_start_utc).where(
                    NhlResearchForecast.game_id.in_(list(starts)), NhlResearchForecast.record_class == "FORECAST",
                    NhlResearchForecast.model_id == paper.MODEL_ID, NhlResearchForecast.imported_at <= now)).all():
            original = original if original.tzinfo else original.replace(tzinfo=UTC)
            if starts.get(gid) == original:
                starts.pop(gid)
                with_forecast += 1
    base["slate"] = {"games": len(slate_games), "with_forecast": with_forecast,
                     "positions": _positions(session, "NHL")["open"]}

    game = session.execute(
        select(Game.id, Game.starts_at).join(League, League.id == Game.league_id)
        .where(League.code == NHL_LEAGUE_CODE, Game.status == GameStatus.SCHEDULED,
               Game.starts_at > now + FINAL_WINDOW_CLOSES)
        .order_by(Game.starts_at, Game.id).limit(1)).first()
    counts = _positions(session, "NHL")
    if game is None:
        return {**base, "next_game": None,
                "forecast": _state("NO_GAME_SCHEDULED", "No upcoming NHL game is scheduled"),
                "price": _state("NOT_PLANNED", "No collection planned"),
                "decision": _state("NO_WINDOW", "No decision window scheduled"),
                "position": _state("OPEN_POSITIONS" if counts["open"] else "NONE",
                                   f"{counts['open']} open NHL position(s)" if counts["open"] else "No open NHL position",
                                   **counts),
                "pipeline": {"ready": owner_active, "blocker": "No upcoming NHL game is scheduled.",
                             "could_qualify": None}}

    gid, starts_at = game
    start = starts_at if starts_at.tzinfo else starts_at.replace(tzinfo=UTC)
    window = _window(start)
    mapping = session.scalar(select(ExternalIdMapping.external_id).where(
        ExternalIdMapping.entity_type == "game", ExternalIdMapping.entity_id == gid,
        ExternalIdMapping.source == NHL_GAME_SOURCE).limit(1))
    source_game_id = str(mapping).rsplit(":", 1)[-1] if mapping else None

    # Forecast: what the checkpoint would hold if it ran now (its own admissibility rules).
    forecast, excluded = paper.pregame_forecast(session, gid, moment=now, starts_at=start)
    generation_on = bool(getattr(settings, "nhl_forecast_generation_enabled", False))
    if forecast is not None:
        in_process = forecast.producer_evidence_status == paper.IN_PROCESS_EVIDENCE_STATUS
        home, away = forecast.final_home_probability, forecast.final_away_probability
        published = forecast.published_utc or forecast.forecast_created_utc
        fc = _state("IN_PROCESS_FORECAST_AVAILABLE" if in_process else "PRODUCER_FORECAST_AVAILABLE",
                    f"{'AISP in-process Gen1' if in_process else 'Producer Gen1'} forecast held: final two-way "
                    f"(OT/SO included) home {home:.2%} / away {away:.2%} · published {_et_label(published)}",
                    identity=forecast.forecast_id, route=paper.ROUTE_IN_PROCESS if in_process else paper.ROUTE_PRODUCER,
                    published_at=_iso(published), imported_at=_iso(forecast.imported_at))
    else:
        # The generator runs at 09:20 and 15:20 ET (app/jobs/scheduler.py) for today and tomorrow ET.
        next_run = None
        if generation_on:
            local = now.astimezone(ET)
            for day_offset in (0, 1):
                day = local.date() + timedelta(days=day_offset)
                for hour in (9, 15):
                    slot = datetime(day.year, day.month, day.day, hour, 20, tzinfo=ET)
                    if slot > local:
                        next_run = slot
                        break
                if next_run:
                    break
        why = sorted({e["why"] for e in excluded})
        fc = _state("FORECAST_AWAITING_GENERATOR",
                    ("No admissible NHL forecast held yet" + (f" ({', '.join(why)})" if why else "")
                     + (f"; next in-process generation {_et_label(next_run)}" if next_run
                        else "; in-process generation is off")),
                    excluded=[{k: e.get(k) for k in ("forecast_id", "route", "why")} for e in excluded],
                    next_generation_at=_iso(next_run))

    # Eligibility: the route the checkpoint takes for the held row.
    if forecast is not None:
        permission = paper.eligibility_record(session, forecast, moment=now, starts_at=start)
        route_in_process = permission.get("route") == paper.ROUTE_IN_PROCESS
        eligibility = {"eligible": bool(permission.get("eligible")), "reason": permission.get("state"),
                       "contract": permission.get("contract"), "route": permission.get("route"),
                       "violations": permission.get("violations") or [], "forecast_id": forecast.forecast_id,
                       "label": (("AISP in-process eligibility" if route_in_process else "Producer eligibility present")
                                 if permission.get("eligible") else
                                 f"Not eligible ({permission.get('state')})")}
    else:
        eligibility = {"eligible": False, "reason": "NO_FORECAST", "contract": paper.IN_PROCESS_ELIGIBILITY_CONTRACT,
                       "route": None, "violations": [], "forecast_id": None, "label": "No forecast held yet"}

    price = _nhl_price(session, settings, gid, start, forecast, now)

    # Funding: the shared $100 allowance, read without its lock.
    slate_day = paper.slate_date(start)
    allowance_on = shared.is_effective(settings, now)
    funding: dict[str, Any] = {"shared_allowance_effective": allowance_on, "slate_date": slate_day.isoformat(),
                               "limit_usd": shared.SHARED_DAILY_LIMIT_USD}
    if allowance_on:
        entered = shared.entered_detail(session, slate_day)["entered_usd"]
        funding.update({"entered_usd": entered,
                        "room_usd": round(max(0.0, shared.SHARED_DAILY_LIMIT_USD - entered), 2)})

    decision = _nhl_decision(session, gid, window, now, owner_active=owner_active, decisions_on=decisions_on)
    game_counts = _positions(session, "NHL", gid)

    blockers = []
    if era is None:
        blockers.append("no NHL paper era is open")
    if not checkpoint_on:
        blockers.append("the NHL paper checkpoint is not active")
    elif not decisions_on:
        blockers.append("NHL_PAPER_DECISIONS_ENABLED is off (the checkpoint records decisions, no position)")
    if forecast is None:
        blockers.append("no admissible NHL forecast held yet")
    elif not eligibility["eligible"]:
        blockers.append(f"eligibility refused ({eligibility['reason']})")
    if price.get("blocker"):
        blockers.append(f"no governed price ({price['blocker']})")
    if not allowance_on:
        blockers.append("the shared $100 daily allowance is not in force")
    ready = owner_active and decisions_on and not blockers
    return {**base,
            "next_game": {"game_id": str(gid), "source_game_id": source_game_id,
                          "matchup": _matchup(session, gid), "starts_at": _iso(start),
                          "starts_label": _et_label(start),
                          "research_href": f"/nhl/games/{source_game_id}" if source_game_id else "/nhl/research"},
            "forecast": fc, "price": price, "decision": decision, "eligibility": eligibility,
            "funding": funding,
            "position": _state("OPEN_POSITIONS" if counts["open"] else "NONE_YET",
                               (f"{counts['open']} open NHL position{'' if counts['open'] == 1 else 's'} (all games); "
                                f"{game_counts['open']} on the next game") if counts["open"] else "No NHL position yet",
                               **counts, scope="SPORT", next_game=game_counts),
            "pipeline": {"ready": ready,
                         # First letter only: the blockers name settings and contracts in their own case.
                         "blocker": (("; ".join(blockers))[:1].upper() + ("; ".join(blockers))[1:] + ".")
                         if blockers else None,
                         "could_qualify": ("Yes, if the final two-way edge against the freshest same-book The Odds API "
                                           "pair clears 2 points with positive expected return at T-30..T-15 and the "
                                           "shared $100 allowance has room; otherwise the checkpoint records a decline.")
                         if not blockers else "Not until the blockers above clear."}}


def _nhl_price(session: Session, settings: Any, game_id: Any, start: datetime, forecast: Any,
               now: datetime) -> dict[str, Any]:
    """The NHL native pair state at ``now`` by the checkpoint's own gates (read-only).

    ``paper.native_pairs`` (one provider, book, event and two-way full-game
    market, legs bound to the game's teams at its current start) -> permitted
    sources -> older-leg age within 240 minutes. Collection is The Odds API lane's
    T-75..T-35 buy window; a pair is never requested here.
    """

    from app.nhl import paper
    from app.nhl.odds_api_price import BUY_CLOSES, BUY_OPENS
    from app.services.research_paper_policy import NHL, effective_quote_sources

    opens, closes = start - BUY_OPENS, start - BUY_CLOSES
    enabled = bool(getattr(settings, "nhl_odds_api_enabled", False))
    collection = {"enabled": enabled, "opens_at": _iso(opens), "closes_at": _iso(closes),
                  "ahead": enabled and now < closes}
    original = forecast.scheduled_start_utc if forecast is not None else start
    original = original if original.tzinfo else original.replace(tzinfo=UTC)
    pairs, refused = paper.native_pairs(session, game_id, moment=now, starts_at=start, original_start=original)
    sources, _amendments = effective_quote_sources(NHL, settings, moment=now)
    permitted = [p for p in pairs if p["source"] in sources]
    max_age = timedelta(minutes=NHL.max_quote_age_minutes)
    fresh = sorted((p for p in permitted if now - datetime.fromisoformat(p["pair_observed_at"]) <= max_age),
                   key=lambda p: p["pair_observed_at"], reverse=True)
    common = {"source": "THE_ODDS_API", "collection": collection, "evaluated_at": _iso(now),
              "refused_legs": len(refused)}
    if fresh:
        newest = datetime.fromisoformat(fresh[0]["pair_observed_at"])
        age = int((now - newest).total_seconds() // 60)
        # Fresh NOW is not fresh at the decision: a pair older than 240 minutes at T-15 is refused,
        # so while the lane's own window is ahead the label says it re-prices the game then.
        expires = newest + max_age
        later = (f"; usable until {_et_label(expires).split(', ')[-1]}, and The Odds API collection "
                 f"{_et_label(opens).split(', ')[-1]} – {_et_label(closes).split(', ')[-1]} re-prices it "
                 "before the decision window") if collection["ahead"] else ""
        return _state("FRESH_PRICE", f"{len(fresh)} usable same-book The Odds API pair(s); newest "
                      f"{_et_label(newest)} ({age} min old on its older leg){later}",
                      pairs=len(fresh), newest_pair_observed_at=_iso(newest), fresh_until=_iso(expires), **common)
    if collection["ahead"]:
        detail = (f"; {len(permitted)} stored pair(s) older than {NHL.max_quote_age_minutes} min are reference only"
                  if permitted else "")
        return _state("COLLECTION_SCHEDULED", f"The Odds API collection {_et_label(opens)} – "
                      f"{_et_label(closes).split(', ')[-1]}{detail}", stale_reference_pairs=len(permitted), **common)
    if permitted:
        return {**_state("STORED_REFERENCE_ONLY", f"{len(permitted)} stored pair(s), all older than the "
                         f"{NHL.max_quote_age_minutes}-minute limit on their older leg; reference only",
                         stale_reference_pairs=len(permitted), **common), "blocker": "QUOTE_TOO_STALE"}
    if pairs:
        return {**_state("NO_PERMITTED_PROVIDER", f"Stored pairs come only from {sorted({p['source'] for p in pairs})}",
                         **common), "blocker": "NO_PERMITTED_PROVIDER"}
    if not enabled:
        return {**_state("COLLECTOR_OFF", "No NHL price held and the The Odds API NHL lane is off", **common),
                "blocker": "NHL_ODDS_API_DISABLED"}
    return {**_state("NO_PRICE_YET", "No two-way full-game NHL pair was retained in the collection window",
                     **common), "blocker": "NO_TWO_SIDED_QUOTE"}


def _nhl_decision(session: Session, game_id: Any, window: dict[str, Any], now: datetime, *,
                  owner_active: bool, decisions_on: bool) -> dict[str, Any]:
    """Window phase at ``now`` plus the latest recorded NHL checkpoint decision for the game, if any."""

    from app.models import ShadowExperimentDecision

    phase = ("OWNER_INACTIVE" if not owner_active else "WINDOW_NOT_OPEN" if now < window["_opens"]
             else "WINDOW_OPEN" if now <= window["_closes"] else "WINDOW_CLOSED")
    row = session.execute(
        select(ShadowExperimentDecision.id, ShadowExperimentDecision.recommendation,
               ShadowExperimentDecision.qualification_reasons, ShadowExperimentDecision.recommended_at)
        .where(ShadowExperimentDecision.game_id == game_id, ShadowExperimentDecision.sport == "NHL")
        .order_by(ShadowExperimentDecision.recommended_at.desc()).limit(1)).first()
    recorded = None
    if row is not None:
        reasons = row[2] if isinstance(row[2], dict) else {}
        recorded = {"decision_id": str(row[0]), "recommendation": row[1], "reason": reasons.get("reason"),
                    "recorded_at": _iso(row[3])}
    labels = {"OWNER_INACTIVE": "The NHL paper checkpoint is not active",
              "WINDOW_NOT_OPEN": f"Decision window {window['label']}",
              "WINDOW_OPEN": f"Decision window open now ({window['label']})",
              "WINDOW_CLOSED": f"Decision window closed ({window['label']})"}
    label = labels[phase]
    if owner_active and not decisions_on:
        label += " · positions off (decisions recorded only)"
    if recorded:
        label += f" · recorded {recorded['recommendation']} ({recorded['reason']})"
    return _state(phase, label, window=_strip(window), recorded=recorded, evaluated_at=_iso(now))


BUILDERS = (("MLB", _mlb), ("NFL", _nfl), ("NBA", _nba), ("CFB", _cfb), ("NHL", _nhl))


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


def _nba_odds_api_lane(settings: Any, now: datetime) -> dict[str, Any]:
    """The NBA Odds API price lane's switches at ``now``: the first gate that stops it, and all of them."""

    from app.services.research_paper_policy import NBA, effective_quote_sources

    enabled = bool(getattr(settings, "nba_odds_api_enabled", False))
    consumer = bool(getattr(settings, "nba_production_consumer_enabled", False))
    monthly = int(getattr(settings, "odds_nba_lane_monthly_credits", 0) or 0)
    daily = int(getattr(settings, "odds_nba_lane_daily_credits", 0) or 0)
    sources, _amendments = effective_quote_sources(NBA, settings, moment=now)
    effective = "THE_ODDS_API" in sources
    clock = getattr(settings, "nba_odds_api_source_effective_from", None)
    if not enabled:
        code, label = "DISABLED", "The NBA Odds API lane is off (NBA_ODDS_API_ENABLED=false); nothing is requested."
    elif not consumer:
        code, label = "CONSUMER_OFF", "The NBA production consumer is off, so the NBA Odds API collector is not scheduled."
    elif monthly <= 0 or daily <= 0:
        code, label = "UNFUNDED", ("The NBA_RESEARCH credit lane is unfunded (ODDS_NBA_LANE_MONTHLY_CREDITS / "
                                   "ODDS_NBA_LANE_DAILY_CREDITS); every request is refused before it is sent.")
    elif not effective:
        code, label = "SOURCE_NOT_EFFECTIVE", ("The NBA source amendment admitting The Odds API is not effective "
                                               "(NBA_ODDS_API_SOURCE_EFFECTIVE_FROM unset or later than now); "
                                               "the checkpoint still permits SportsGameOdds only.")
    else:
        code, label = "ACTIVE", "The Odds API NBA h2h lane is on, funded and admitted for decisions."
    return _state(code, label, source="THE_ODDS_API", enabled=enabled, consumer_enabled=consumer,
                  monthly_credits=monthly, daily_credits=daily, source_effective=effective,
                  source_effective_from=_iso(clock) if isinstance(clock, datetime) else (str(clock) if clock else None))


def _nba_odds_api_price(session: Session, game_id: Any, now: datetime, settings: Any) -> dict[str, Any]:
    """The lane state and -- only when the lane is ACTIVE -- the governed price state for one game (read-only).

    Same order as the checkpoint: same-book pairs -> permitted sources -> both
    legs retained at the game's CURRENT tip-off -> older leg within the policy
    maximum. Nothing here writes or requests.
    """

    from app.models import Game
    from app.nba.gen1_paper import _tipoff_bound
    from app.nba.odds_api_price import BUY_CLOSES, BUY_OPENS
    from app.services.nfl_paper_decision import paired_quotes
    from app.services.research_paper_policy import NBA, effective_quote_sources

    lane = _nba_odds_api_lane(settings, now)
    if lane["state"] != "ACTIVE":
        return {"lane": lane, "price": None}
    game = session.get(Game, game_id) if game_id is not None else None
    if game is None or game.starts_at is None:
        return {"lane": lane, "price": {**_state("NO_CANONICAL_GAME", "No canonical game is bound for this forecast.",
                                                 source="THE_ODDS_API", odds_api_lane=lane),
                                        "blocker": "NO_CANONICAL_GAME"}}
    tipoff = game.starts_at if game.starts_at.tzinfo else game.starts_at.replace(tzinfo=UTC)
    opens, closes = tipoff - BUY_OPENS, tipoff - BUY_CLOSES
    sources, _amendments = effective_quote_sources(NBA, settings, moment=now)
    candidates = [p for p in paired_quotes(session, game_id) if p["source"] == "THE_ODDS_API" and p["source"] in sources]
    permitted, _refused = _tipoff_bound(session, candidates, tipoff, game_id=game_id, moment=now)
    max_age = timedelta(minutes=NBA.max_quote_age_minutes)
    fresh = [p for p in permitted if now - datetime.fromisoformat(p["pair_observed_at"]) <= max_age]
    common = {"source": "THE_ODDS_API", "current_tipoff": _iso(tipoff), "evaluated_at": _iso(now),
              "collection": {"opens_at": _iso(opens), "closes_at": _iso(closes), "ahead": now < closes},
              "odds_api_lane": lane}
    if fresh:
        return {"lane": lane, "price": _state(
            "PRICE_HELD", f"{len(fresh)} usable same-book The Odds API moneyline pair(s) at the current tip-off.",
            pairs=len(fresh), **common)}
    if now < closes:
        return {"lane": lane, "price": _state(
            "COLLECTION_SCHEDULED", f"The Odds API collection {_et_label(opens)} – {_et_label(closes)}.",
            stale_reference_pairs=len(permitted), **common)}
    return {"lane": lane, "price": {**_state(
        "NO_PRICE_HELD", "No usable same-book The Odds API moneyline pair at the current tip-off.",
        stale_reference_pairs=len(permitted), **common),
        "blocker": "QUOTE_TOO_STALE" if permitted else "NO_TWO_SIDED_QUOTE"}}

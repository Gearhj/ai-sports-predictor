"""AISP_CFB_PAPER_POLICY_V1 -- the CFB paper checkpoint, unchanged policy, gated activation.

THE POLICY IS THE OWNER'S, NOT THIS MODULE'S
--------------------------------------------
Every number comes from ``research_paper_policy.CFB`` -- the terms James set on
2026-09-15 (moneyline only, quarter Kelly, 0.5% / $5 / 1% / 2% caps, a two-point
minimum edge against the same book's no-vig price, zero tie probability, no MLB
penalties, T-30 to T-15; quotes from ``CFB.permitted_quote_sources`` -- SportsGameOdds
-- plus any APPENDED prospective source amendment effective at the decision clock,
e.g. The Odds API, authorized 2026-09-26) -- and the stake
is ``research_paper_policy.moneyline_terms`` under the frozen caps plus any
APPENDED prospective staking amendment effective at the decision clock (the
flat $50 daily limit counting every stake entered that ET date, authorized
2026-09-26). Nothing here tunes, rounds or
re-derives a threshold.

THE GATES, IN ORDER, AND WHY THAT ORDER
---------------------------------------
1. The game is in its window (T-30..T-15) and still SCHEDULED.
2. A forecast exists, and the NEWEST verified discovery manifest lists that
   exact forecast as currently eligible with an unexpired lease
   (``production_import.present_eligibility``). An expired lease is a block,
   not a stale-but-usable forecast.
3. The producer's per-forecast ``ASP_CFB_PAPER_RESEARCH_ELIGIBILITY_V1`` record
   binds this policy to that forecast.
4. A two-sided moneyline from one permitted provider, book and market, no
   older than 240 minutes on its STALER leg.
5. The policy's own edge and expected-return rule.
6. The activation control (``CFB_PAPER_DECISIONS_ENABLED``). Everything above is
   evaluated and recorded either way; only a position waits on this switch.

Eligibility precedes arithmetic so a missing permission is never "rescued" by a
large number. Every evaluation is written with its reason: a checkpoint that
wrote nothing cannot be told apart from one that never ran.

CONTROLLING PROBABILITY
-----------------------
The joint paired-residual draw probability of the eligible baseline forecast.
Ridge (Development-calibrated) and Elo are recorded beside it for the reader and
never used here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from app.services.event_time_reconciliation import pair_clock_verdict, MATCH_WINDOW, reconcile_clock_identity

from app.models import (
    CfbResearchForecast, ExternalIdMapping, Game, GameStatus, League, OfficialAspPosition, PositionResult,
    PositionStatus, Season, ShadowExperiment, ShadowExperimentDecision,
)
from app.services.research_paper_policy import CFB as POLICY
from app.services.research_paper_policy import eligibility_status, moneyline_terms

PHASE = "FINAL_PREGAME"
WINDOW_OPENS = POLICY.window_opens
WINDOW_CLOSES = POLICY.window_closes
CONTROLLING_CHANNEL = "JOINT_PAIRED_RESIDUAL_BASELINE"

#: Evaluations that end on a missing prerequisite. A later tick inside the same
#: window may record a new decision if the prerequisite has since arrived.
PREREQUISITE_REASONS = frozenset({
    "NO_FORECAST", "DISCOVERY_NOT_CURRENT", "AWAITING_PRODUCER_ELIGIBILITY", "ELIGIBILITY_RECORD_INVALID",
    "NO_TWO_SIDED_QUOTE", "NO_PERMITTED_PROVIDER", "QUOTE_TOO_STALE",
    # A schedule change invalidated every held Odds API match; a later tick may
    # record a new decision once a quote retained at the CURRENT kickoff arrives.
    "QUOTE_KICKOFF_MISMATCH",
    # The shared $100's last room was held for priority-sport games still to
    # decide (services/shared_priority_reservation.py); the hold releases as they decide, so
    # a later tick may fund the value that qualified. A FULLY ENTERED allowance
    # (SHARED_ALLOWANCE_EXHAUSTED) never frees up and is final.
    "SHARED_ROOM_RESERVED_FOR_PRIORITY_SPORTS",
})

#: Quotes whose provider retained a kickoff: The Odds API rows carry
#: ``raw_payload.event_commence_time``. SportsGameOdds rows are unchanged.
KICKOFF_BOUND_SOURCES = frozenset({"THE_ODDS_API"})
#: Refusals at position creation (the decision row is still recorded).
QUOTE_KICKOFF_MISMATCH = "QUOTE_KICKOFF_MISMATCH"
PAST_DECISION_CUTOFF = "PAST_DECISION_CUTOFF"
GAME_NO_LONGER_SCHEDULED = "GAME_NO_LONGER_SCHEDULED"


#: What a recorded checkpoint MEANS, read from its own recommendation and
#: reason. Three results a reader must never confuse:
#:   NO_BET    evaluated against a usable same-book price; no side cleared the
#:             policy's edge/return test -- a valid pass;
#:   NO_PRICE  evaluated, but no usable two-sided price existed (none stored,
#:             provider not permitted, or its older leg too old) -- DECLINED;
#:   BLOCKED   not evaluated at all: a prerequisite (forecast, discovery lease,
#:             eligibility record, policy grant) was missing.
#:   NO_ROOM   evaluated at a usable price and the value QUALIFIED, but the
#:             shared $100 left no room (fully entered, or held for priority-sport
#:             games still to decide) -- declined for budget, NEVER a no-bet.
#: Derived for display; the recorded row is never rewritten and no threshold moves.
NO_PRICE_REASONS = frozenset({"NO_TWO_SIDED_QUOTE", "NO_PERMITTED_PROVIDER", "QUOTE_TOO_STALE",
                              "QUOTE_KICKOFF_MISMATCH"})
NO_ROOM_REASONS = frozenset({"SHARED_ALLOWANCE_EXHAUSTED", "SHARED_ROOM_RESERVED_FOR_PRIORITY_SPORTS"})
#: Rows recorded before the honest budget reasons existed (e.g. Oct 10): a value
#: decline whose only cause was the daily cap was stored as NO_QUALIFYING_EDGE
#: with this phrase from ``moneyline_terms`` in its detail.
LEGACY_ZERO_CAP_PHRASE = "every cap resolved to zero"
CHECKPOINT_TEXT = {
    "NO_BET": "Evaluated at a usable price: no side cleared the policy's 2-point edge test (a valid no-bet)",
    "NO_PRICE": "Evaluated, but no usable two-sided price was held (declined, not a no-bet)",
    "NO_ROOM": ("Evaluated at a usable price and the value qualified, but the daily stake allowance had no room "
                "left (fully entered, or under the shared $100 held for priority-sport games still to decide); "
                "declined for budget, not a no-bet"),
    "BLOCKED": "Not evaluated: a prerequisite was missing at the checkpoint",
    "QUALIFIED": "Every gate passed; a paper position was recorded",
    "QUALIFIED_NOT_ACTIVATED": "Every gate passed, but the activation control was off; no position was recorded",
    "UNKNOWN": "Recorded result not recognised",
}


def checkpoint_semantics(recommendation: str | None, reason: str | None,
                         detail: str | None = None) -> dict[str, Any]:
    """The class of a recorded CFB checkpoint (NO_BET / NO_PRICE / NO_ROOM / BLOCKED / ...), from its own fields.

    ``detail`` (the row's ``recommendation_reason``) lets a legacy budget-only
    decline, stored as NO_QUALIFYING_EDGE with "every cap resolved to zero",
    read as NO_ROOM instead of a no-bet.
    """

    rec, why = str(recommendation or "").upper(), str(reason or "").upper()
    if rec == "BLOCKED":
        kind = "BLOCKED"
    elif why in NO_ROOM_REASONS or (why == "NO_QUALIFYING_EDGE" and LEGACY_ZERO_CAP_PHRASE in str(detail or "")):
        kind = "NO_ROOM"
    elif why == "NO_QUALIFYING_EDGE":
        kind = "NO_BET"
    elif why in NO_PRICE_REASONS:
        kind = "NO_PRICE"
    elif why == "ACTIVATION_CONTROL_OFF":
        kind = "QUALIFIED_NOT_ACTIVATED"
    elif rec in ("QUALIFIED_BET", "QUALIFIED") or why == "ALL_GATES_PASSED":
        kind = "QUALIFIED"
    else:
        kind = "UNKNOWN"
    return {"class": kind, "reason": reason, "evaluated": kind not in ("BLOCKED", "UNKNOWN"),
            "priced": kind in ("NO_BET", "NO_ROOM", "QUALIFIED", "QUALIFIED_NOT_ACTIVATED"),
            "text": CHECKPOINT_TEXT[kind]}


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def utc_instant(value: Any) -> datetime | None:
    """One normalized UTC-aware instant from a datetime (naive = UTC, or any zone, e.g. the
    session's America/New_York) or an ISO string (``Z`` or an offset). Unreadable -> None."""

    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _iso_or_missing(value: datetime | None) -> str:
    return value.isoformat() if value is not None else "missing"


def _current_game(session: Session, game_id: Any) -> tuple[datetime | None, Any]:
    """The canonical game's CURRENT kickoff (normalized) and status, read now, not at bind time."""

    row = session.execute(select(Game.starts_at, Game.status).where(Game.id == game_id)).first()
    return (utc_instant(row[0]), row[1]) if row else (None, None)


def _snapshot_kickoffs(session: Session, snapshot_ids: list[Any]) -> dict[Any, dict[str, Any]]:
    from app.models import OddsSnapshot

    out: dict[Any, dict[str, Any]] = {}
    if not snapshot_ids:
        return out
    for snap_id, raw in session.execute(select(OddsSnapshot.id, OddsSnapshot.raw_payload)
                                        .where(OddsSnapshot.id.in_(snapshot_ids))).all():
        raw = raw if isinstance(raw, dict) else {}
        out[snap_id] = {"retained_kickoff": utc_instant(raw.get("event_commence_time")),
                        "quote_clock_source": raw.get("quote_clock_source") or "MARKET_LAST_UPDATE"}
    return out


def kickoff_bound_pairs(session: Session, game_id: Any, kickoff: datetime | None, *,
                        moment: datetime | None = None, close_before: timedelta | None = None) -> list[dict[str, Any]]:
    """Both retained legs of one exact current event, held at the actual read/decision clock."""
    from app.models import GameTeam, Market, MarketOutcome, OddsSnapshot, Sportsbook
    if kickoff is None: return []
    from app.services.event_time_reconciliation import legacy_clock_tolerance
    moment = utc_instant(moment or datetime.now(UTC))
    legacy_tolerance = legacy_clock_tolerance("CFB")     # legacy legs: identity exact, clock cushioned (HIGH-4)
    sides = {s.is_home: s.team_id for s in session.scalars(select(GameTeam).where(GameTeam.game_id == game_id))}
    rows = session.execute(select(Market.id, Sportsbook.key, OddsSnapshot.source, MarketOutcome.outcome_key,
        OddsSnapshot.observed_at, OddsSnapshot.american_odds, MarketOutcome.id, OddsSnapshot.id,
        OddsSnapshot.raw_payload, OddsSnapshot.available_as_of, OddsSnapshot.source_event_id,
        MarketOutcome.participant_team_id)
        .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
        .join(Market, Market.id == MarketOutcome.market_id)
        .join(Sportsbook, Sportsbook.id == OddsSnapshot.sportsbook_id)
        .where(Market.game_id == game_id, Market.market_type == "MONEYLINE", Market.period == "full_game",
               OddsSnapshot.source.in_(sorted(KICKOFF_BOUND_SOURCES)))
        .order_by(OddsSnapshot.observed_at.desc(), OddsSnapshot.available_as_of.desc())).all()
    groups = {}
    for mid,book,source,key,observed,american,oid,sid,raw,available,event_id,team_id in rows:
        side = str(key).split(":")[0].upper()
        observed, available = utc_instant(observed), utc_instant(available)
        if (side not in ("HOME","AWAY") or american is None or not isinstance(raw,dict)
                or observed is None or available is None or not observed <= available <= moment
                or team_id != sides.get(side == "HOME") or raw.get("side") != side
                or raw.get("sport_key") != "americanfootball_ncaaf" or raw.get("market") != "h2h"
                or not event_id or str(raw.get("provider_event_id")) != str(event_id)): continue
        groups.setdefault((mid,book,source,str(event_id)),{}).setdefault(side,[]).append({
            "observed_at":observed,"available_as_of":available,"american_odds":int(american),
            "outcome_id":oid,"snapshot_id":sid,"raw":raw})
    event_ids={}
    for mid,book,source,event in groups: event_ids.setdefault(source,set()).add(event)
    pairs=[]
    for (mid,book,source,event),legs in groups.items():
        if set(legs) != {"HOME","AWAY"} or len(event_ids[source]) != 1: continue
        # The newest leg PAIR that passes the clock verdict. Taking the newest leg per side and
        # verifying afterwards let a stale leg retained at an OLD kickoff (same book clock as the
        # fresh one after a reschedule) win the tie and refuse the whole book, which is the v3_a
        # 2026-09-26 correction the live tree had lost (hotfix 2026-10-24, HIGH-3 verification).
        chosen=None
        for home in legs['HOME']:
            for away in legs['AWAY']:
                verdict=pair_clock_verdict(home['raw'],away['raw'],game_id=game_id,sport='CFB',
                    home_id=sides.get(True),away_id=sides.get(False),canonical_start=kickoff,
                    moment=moment,close_before=close_before,legacy_tolerance=legacy_tolerance)
                if verdict['accepted']:
                    chosen=(home,away,verdict); break
            if chosen: break
        if chosen is None: continue
        home,away,verdict=chosen
        older=min(home['observed_at'],away['observed_at'])
        pair={'market_id':str(mid),'sportsbook':book,'source':source,'provider_event_id':event,
            'pair_observed_at':older.isoformat(),'older_leg':'HOME' if home['observed_at']==older else 'AWAY',
            'legs_apart_minutes':abs((home['observed_at']-away['observed_at']).total_seconds())/60,
            'retained_kickoff':home['raw'].get('event_commence_time') or verdict.get('provider_start'),
            'event_clock':verdict}
        for side,leg in [('home',home),('away',away)]:
            pair.update({side+'_american':leg['american_odds'],side+'_outcome_id':leg['outcome_id'],
                side+'_snapshot_id':leg['snapshot_id'],side+'_observed_at':leg['observed_at'].isoformat(),
                side+'_available_as_of':leg['available_as_of'].isoformat(),
                side+'_quote_clock_source':leg['raw'].get('quote_clock_source') or 'MARKET_LAST_UPDATE'})
        pairs.append(pair)
    return sorted(pairs,key=lambda p:p['pair_observed_at'],reverse=True)


#: The sizing limits moneyline_terms always records; all must be present to prove
#: that the daily cap ALONE blocked a value decline.
CANONICAL_LIMIT_KEYS = frozenset({"quarter_kelly", "position_cap", "max_stake", "same_game_cap", "daily_cap"})


def budget_only_successor(prior_evidence: Any, decision: dict[str, Any], *,
                          prior_recommendation: Any = None, prior_reason: Any = None) -> bool:
    """May a new natural evaluation append a successor to a prior NO_QUALIFYING_EDGE row?

    Only when ALL hold (MAIN CFB_CAP_MISSED_ROWS_AND_STICKY_DECLINE_REVIEW 556b9a42):
    * the prior row is a NO_QUALIFYING_EDGE decline whose retained best terms show
      a positive qualifying value -- edge >= the policy minimum and positive
      expected return -- and an exhausted daily cap (<= 0) as the ONLY binding
      constraint (every other limit still positive);
    * the new evaluation genuinely QUALIFIED; and
    * it did so under a currently effective shared allowance that differs from
      the policy the prior row was decided under (none, or another clock).
    True no-edge, missing pair, same-policy and not-yet-effective cases return
    False; the caller already refuses rows that hold a position, and only runs
    inside a natural checkpoint window.
    """

    # Fail closed (MAIN S1, ac546a1c): the cause must be affirmatively recorded --
    # the row's own columns AND its retained evidence -- never assumed from a gap;
    # every nested object must have the right type; every canonical sizing limit
    # must be present and numeric. Anything else is unknown -> reuse the old row.
    try:
        if not (isinstance(prior_evidence, dict) and isinstance(decision, dict)
                and prior_recommendation == "DECLINED" and prior_reason == "NO_QUALIFYING_EDGE"
                and prior_evidence.get("outcome") == "DECLINED"
                and prior_evidence.get("reason") == "NO_QUALIFYING_EDGE"
                and decision.get("outcome") == "QUALIFIED"):
            return False
        best = prior_evidence.get("best")
        terms = best.get("terms") if isinstance(best, dict) else None
        limits = terms.get("limits") if isinstance(terms, dict) else None
        if not isinstance(limits, dict) or not CANONICAL_LIMIT_KEYS <= set(limits):
            return False
        values = {key: limits[key] for key in CANONICAL_LIMIT_KEYS}
        numbers = [terms.get("edge"), terms.get("expected_return_per_dollar"), *values.values()]
        if not all(isinstance(n, (int, float)) and not isinstance(n, bool) for n in numbers):
            return False
        if not (float(terms["edge"]) >= POLICY.min_adjusted_edge and float(terms["expected_return_per_dollar"]) > 0
                and float(values["daily_cap"]) <= 0
                and all(float(v) > 0 for k, v in values.items() if k != "daily_cap")):
            return False
        current = decision.get("shared_daily_allowance")
        if not isinstance(current, dict) or not isinstance(current.get("effective_from"), str) \
                or not current["effective_from"]:
            return False
        prior_policy = prior_evidence.get("shared_daily_allowance")
        if prior_policy is not None and not isinstance(prior_policy, dict):
            return False
        prior_clock = prior_policy.get("effective_from") if isinstance(prior_policy, dict) else None
        return current["effective_from"] != prior_clock
    except Exception:  # noqa: BLE001 -- unknown evidence never opens a successor
        return False


def position_guard(session: Session, game: dict[str, Any], selection: dict[str, Any],
                   moment: datetime) -> dict[str, Any] | None:
    from app.cfb.odds_api_price import effective_start
    kickoff,status=_current_game(session,game['game_id'])
    moment=utc_instant(moment)
    if kickoff is None or status != GameStatus.SCHEDULED:
        return {'reason':GAME_NO_LONGER_SCHEDULED}
    start=effective_start(session,game['game_id'],kickoff,moment=moment)
    if not start-WINDOW_OPENS <= moment <= start-WINDOW_CLOSES:
        return {'reason':PAST_DECISION_CUTOFF,'current_kickoff':kickoff.isoformat(),
                'operational_start':start.isoformat(),'cutoff':(start-WINDOW_CLOSES).isoformat(),
                'decided_at':moment.isoformat()}
    if selection.get('source') in KICKOFF_BOUND_SOURCES:
        pairs=kickoff_bound_pairs(session,game['game_id'],kickoff,moment=moment,close_before=WINDOW_CLOSES)
        matching=[p for p in pairs if all(str(p.get(k))==str(selection.get(k))
            for k in ('home_snapshot_id','away_snapshot_id'))]
        if not matching or selection.get('odds_snapshot_id') not in (
                matching[0]['home_snapshot_id'],matching[0]['away_snapshot_id']):
            return {'reason':QUOTE_KICKOFF_MISMATCH,'detail':'Both selected original legs must still bind this event.'}
        age=(moment-datetime.fromisoformat(matching[0]['pair_observed_at'])).total_seconds()/60
        if not 0 <= age <= POLICY.max_quote_age_minutes: return {'reason':'QUOTE_TOO_STALE'}
    return None


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def slate_date(game: dict[str, Any]) -> date:
    """The game's Eastern calendar date -- the day Daily Decisions files it under.

    A Saturday 8 PM ET kickoff is Sunday in UTC; filing its decision by UTC
    would move it to the wrong day's board and the wrong day's exposure cap.
    """

    from zoneinfo import ZoneInfo

    return game["starts_at"].astimezone(ZoneInfo("America/New_York")).date()


def american_to_decimal(american: int) -> float:
    return 1 + (american / 100.0 if american > 0 else 100.0 / abs(american))


def experiment(session: Session) -> ShadowExperiment | None:
    return session.scalar(select(ShadowExperiment).where(
        ShadowExperiment.sport == POLICY.sport, ShadowExperiment.decision_policy == POLICY.policy_version,
        ShadowExperiment.is_active.is_(True)))


def games_at_checkpoint(session: Session, *, now: datetime) -> list[dict[str, Any]]:
    rows = session.execute(
        select(Game.id, Game.starts_at, ExternalIdMapping.external_id)
        .join(Season, Season.id == Game.season_id).join(League, League.id == Season.league_id)
        .join(ExternalIdMapping, (ExternalIdMapping.entity_id == Game.id)
              & (ExternalIdMapping.entity_type == "game") & (ExternalIdMapping.source == "CFBD"))
        .where(League.code == "CFB", Game.league_id == League.id, Game.status == GameStatus.SCHEDULED,
               Game.starts_at >= now + WINDOW_CLOSES, Game.starts_at <= now + WINDOW_OPENS + MATCH_WINDOW)
        .order_by(Game.starts_at)).all()
    from app.cfb.odds_api_price import effective_start
    return [{"game_id":gid,"starts_at":_aware(starts),"source_game_id":str(ext),
             "operational_start":effective_start(session,gid,_aware(starts),moment=now)}
            for gid,starts,ext in rows
            if now+WINDOW_CLOSES <= effective_start(session,gid,_aware(starts),moment=now) <= now+WINDOW_OPENS]


def _bankroll(session: Session, settings: Any, era: ShadowExperiment) -> float:
    from app.services.shadow_experiment_service import ShadowExperimentService

    return ShadowExperimentService(session, settings).current_bankroll(era)


def _exposure(session: Session, era: ShadowExperiment, game_id: Any, day: date) -> tuple[float, float]:
    rows = session.execute(
        select(OfficialAspPosition.stake, ShadowExperimentDecision.game_id, ShadowExperimentDecision.slate_date)
        .join(ShadowExperimentDecision, ShadowExperimentDecision.id == OfficialAspPosition.shadow_decision_id)
        .where(OfficialAspPosition.shadow_experiment_id == era.id,
               OfficialAspPosition.status == PositionStatus.OPEN)).all()
    return (sum(float(s) for s, gid, _ in rows if gid == game_id),
            sum(float(s) for s, _, slate in rows if slate == day))


def _entered_on(session: Session, era: ShadowExperiment, day: date) -> float:
    """Every stake entered under this game-slate date (as ``slate_date``) -- open, settled or void."""

    rows = session.execute(
        select(OfficialAspPosition.stake)
        .join(ShadowExperimentDecision, ShadowExperimentDecision.id == OfficialAspPosition.shadow_decision_id)
        .where(OfficialAspPosition.shadow_experiment_id == era.id,
               ShadowExperimentDecision.slate_date == day)).all()
    return sum(float(stake) for (stake,) in rows)


def _grant(settings: Any) -> dict[str, Any]:
    """The pinned handoff's virtual-policy grant: its SHA and expiry (empty when not pinned)."""

    from app.cfb.production_discovery import production_status

    try:
        detail = production_status(settings).detail or {}
    except Exception:  # noqa: BLE001 -- an unreadable pin is treated as no grant
        return {"sha256": None, "valid_until": None, "state": "UNREADABLE"}
    until = detail.get("grant_valid_until_utc")
    return {"sha256": detail.get("grant_sha256"),
            "valid_until": datetime.fromisoformat(str(until).replace("Z", "+00:00")) if until else None}


def _supplement_published(session: Session, record_id: Any) -> datetime | None:
    from app.models import ResearchSupplement

    if not record_id:
        return None
    row = session.get(ResearchSupplement, uuid.UUID(str(record_id)))
    return _aware(row.published_utc) if row is not None else None


def decide(session: Session, settings: Any, era: ShadowExperiment, game: dict[str, Any], moment: datetime) -> dict[str, Any]:
    from app.cfb.production_import import current_forecast_for_game, present_eligibility
    from app.services.nfl_paper_decision import paired_quotes
    from app.services.research_paper_policy import DAILY_BASIS_ENTERED, effective_daily_limit, effective_quote_sources

    base: dict[str, Any] = {"game_id": str(game["game_id"]), "source_game_id": game["source_game_id"],
                            "starts_at": game["starts_at"].isoformat(), "evaluated_at": moment.isoformat(),
                            "policy_version": POLICY.policy_version, "controlling_channel": CONTROLLING_CHANNEL}
    from app.cfb.odds_api_price import effective_start
    canonical,status=_current_game(session,game['game_id'])
    if canonical is None or status != GameStatus.SCHEDULED:
        return {**base,'outcome':'BLOCKED','reason':GAME_NO_LONGER_SCHEDULED}
    operational=effective_start(session,game['game_id'],canonical,moment=moment)
    base['operational_start']=operational.isoformat()
    if not operational-WINDOW_OPENS <= moment <= operational-WINDOW_CLOSES:
        return {**base,'outcome':'BLOCKED','reason':PAST_DECISION_CUTOFF}
    forecast = current_forecast_for_game(session, game["source_game_id"])
    if forecast is None:
        return {**base, "outcome": "BLOCKED", "reason": "NO_FORECAST",
                "detail": "AISP holds no verified CFB forecast for this game."}
    base.update({"forecast_id": forecast.forecast_id, "cfb_research_forecast_id": forecast.id,
                 "matchup": f"{forecast.away_team_name} at {forecast.home_team_name}",
                 "channels": {"joint": {"home": forecast.joint_home_win_probability,
                                        "away": forecast.joint_away_win_probability},
                              "ridge_development_calibrated": {"home": forecast.ridge_home_win_probability,
                                                               "away": forecast.ridge_away_win_probability},
                              "elo_diagnostic": {"home": forecast.elo_home_win_probability,
                                                 "away": forecast.elo_away_win_probability}}})

    lease = present_eligibility(session, forecast, now=moment)
    if not lease["eligible"]:
        return {**base, "outcome": "BLOCKED", "reason": "DISCOVERY_NOT_CURRENT",
                "detail": f"{lease['state']}: {lease['detail']}", "discovery": lease}
    base["discovery"] = lease

    permission = eligibility_status(session, POLICY, forecast_id=forecast.forecast_id,
                                    event_id=f"cfbd:{forecast.source_game_id}")
    if not permission["eligible"]:
        reason = ("AWAITING_PRODUCER_ELIGIBILITY" if permission["state"] == "AWAITING_PRODUCER_ELIGIBILITY"
                  else "ELIGIBILITY_RECORD_INVALID")
        return {**base, "outcome": "BLOCKED", "reason": reason, "detail": permission.get("detail")
                or f"violations {permission.get('violations')} missing {permission.get('missing')}",
                "eligibility": permission}
    base["eligibility"] = {k: permission.get(k) for k in ("state", "record_id", "contract", "experiment_key")}

    # The owner's 2026-09-18 grant extends the unchanged policy only to
    # supplements published before the game's T-30, and only until it expires.
    grant = _grant(settings)
    base["grant"] = grant
    if grant.get("valid_until") and moment >= grant["valid_until"]:
        return {**base, "outcome": "BLOCKED", "reason": "POLICY_GRANT_EXPIRED",
                "detail": f"The virtual-policy grant expired at {grant['valid_until'].isoformat()}."}
    published = _supplement_published(session, permission.get("record_id"))
    if published is None or published >= operational - WINDOW_OPENS:
        return {**base, "outcome": "BLOCKED", "reason": "ELIGIBILITY_RECORD_INVALID",
                "detail": ("The producer's eligibility record was not published before T-30 "
                           f"({published.isoformat() if published else 'no publication time'}).")}

    pairs = paired_quotes(session, game["game_id"])
    # Odds API quotes count only when both legs retained the game's CURRENT
    # kickoff (normalized UTC). A schedule change invalidates the old match
    # until a quote retained at the new kickoff arrives; SGO pairs are untouched.
    current_kickoff, _status = _current_game(session, game["game_id"])
    bound_pairs = kickoff_bound_pairs(session, game["game_id"], current_kickoff, moment=moment, close_before=WINDOW_CLOSES)
    unmatched = [p for p in pairs if p["source"] in KICKOFF_BOUND_SOURCES
                 and not any(b["sportsbook"] == p["sportsbook"] and b["market_id"] == p["market_id"]
                             and b["source"] == p["source"] for b in bound_pairs)]
    pairs = [p for p in pairs if p["source"] not in KICKOFF_BOUND_SOURCES] + bound_pairs
    if unmatched:
        kicks = _snapshot_kickoffs(session, [p[k] for p in unmatched for k in ("home_snapshot_id", "away_snapshot_id")])
        base["kickoff_refusals"] = [{
            "reason": QUOTE_KICKOFF_MISMATCH, "source": p["source"], "sportsbook": p["sportsbook"],
            "current_kickoff": current_kickoff.isoformat() if current_kickoff else None,
            "retained_kickoffs": sorted({_iso_or_missing(kicks.get(p[k], {}).get("retained_kickoff"))
                                         for k in ("home_snapshot_id", "away_snapshot_id")})} for p in unmatched]
        if not pairs:
            return {**base, "outcome": "DECLINED", "reason": QUOTE_KICKOFF_MISMATCH,
                    "detail": ("Every held Odds API pair was retained at a kickoff other than the game's current "
                               f"{current_kickoff.isoformat() if current_kickoff else 'unknown'} kickoff; "
                               "the old match is invalid until a quote at the current kickoff is obtained.")}
    if not pairs:
        return {**base, "outcome": "DECLINED", "reason": "NO_TWO_SIDED_QUOTE",
                "detail": ("AISP holds no moneyline pair for this game from one provider, book and market. "
                           "No governed CFB price was collected; a reference line is never a price.")}
    # The frozen SportsGameOdds-only record plus any prospective source
    # amendment effective at this decision's clock (research_paper_policy).
    sources, amendments = effective_quote_sources(POLICY, settings, moment=moment)
    base["permitted_quote_sources"] = list(sources)
    if amendments:
        base["quote_source_amendments"] = amendments
    permitted = [p for p in pairs if p["source"] in sources]
    if not permitted:
        return {**base, "outcome": "DECLINED", "reason": "NO_PERMITTED_PROVIDER",
                "detail": f"Stored pairs come only from {sorted({p['source'] for p in pairs})}."}
    fresh, stale = [], []
    for pair in permitted:
        age = (moment - datetime.fromisoformat(pair["pair_observed_at"])).total_seconds() / 60
        (fresh if 0 <= age <= POLICY.max_quote_age_minutes else stale).append({**pair, "pair_age_minutes": round(age, 1)})
    if not fresh:
        return {**base, "outcome": "DECLINED", "reason": "QUOTE_TOO_STALE",
                "detail": f"No permitted pair is within {POLICY.max_quote_age_minutes} minutes on its older leg.",
                "pairs_considered": stale}

    bankroll = _bankroll(session, settings, era)
    same_game, same_day = _exposure(session, era, game["game_id"], slate_date(game))
    # The frozen caps plus any prospective staking amendment effective at this
    # decision's clock (only the daily limit can change). The frozen rule counts
    # OPEN positions on this game-slate date; the owner's flat $50 limit counts every
    # stake entered on it, whatever its status, including ones placed before the
    # effective clock.
    daily_limit_usd, daily_basis, staking_amendments = effective_daily_limit(POLICY, settings, moment=moment)
    if staking_amendments:
        if daily_basis == DAILY_BASIS_ENTERED:
            same_day = _entered_on(session, era, slate_date(game))
        base["staking_amendments"] = staking_amendments
        base["daily_exposure"] = {"basis": daily_basis, "stake_entered_usd": round(same_day, 2),
                                  "limit_usd": daily_limit_usd}
    # The owner's shared cross-sport allowance, once effective, replaces the CFB
    # daily ceiling: one $100 per Eastern slate date across all official sports,
    # every entered stake counted. Taken under the cross-sport lock, held until
    # the position (if any) is committed in record().
    # Since 2026-10-16 (owner: priority sports before CFB; shared_priority_reservation)
    # CFB, a non-priority sport, sizes against the room left after every priority game still waiting
    # for its decision holds its policy maximum: exposure = entered + reserved.
    from app.services.shared_daily_allowance import room as shared_room

    shared = shared_room(session, settings, day=slate_date(game), moment=moment, sport="CFB",
                         game_id=game["game_id"])
    if shared is not None:
        daily_limit_usd, same_day = shared["limit_usd"], shared["exposure_counted_usd"]
        base["shared_daily_allowance"] = shared
        base["daily_exposure"] = {"basis": shared["basis"], "stake_entered_usd": shared["entered_usd"],
                                  "limit_usd": shared["limit_usd"]}
    candidates = []
    for pair in fresh:
        for side, win, american, other in (
                ("AWAY", forecast.joint_away_win_probability, pair["away_american"], pair["home_american"]),
                ("HOME", forecast.joint_home_win_probability, pair["home_american"], pair["away_american"])):
            terms = moneyline_terms(POLICY, win_probability=float(win), decimal_odds=american_to_decimal(int(american)),
                                    opposite_decimal_odds=american_to_decimal(int(other)), bankroll=bankroll,
                                    same_game_exposure=same_game, daily_exposure=same_day,
                                    daily_limit_usd=daily_limit_usd)
            candidates.append({"side": side, "american_odds": int(american), "opposite_american": int(other), "win_probability": float(win),
                               "sportsbook": pair["sportsbook"], "source": pair["source"],
                               "market_outcome_id": pair[f"{side.lower()}_outcome_id"],
                               "odds_snapshot_id": pair[f"{side.lower()}_snapshot_id"],
                               "observed_at": pair[f"{side.lower()}_observed_at"],
                               "pair_observed_at": pair["pair_observed_at"], "older_leg": pair["older_leg"],
                               "pair_age_minutes": pair["pair_age_minutes"], "terms": terms,
                               "home_snapshot_id": pair["home_snapshot_id"], "away_snapshot_id": pair["away_snapshot_id"],
                               **({"retained_kickoff": pair["retained_kickoff"],
                                   "quote_clock_source": pair[f"{side.lower()}_quote_clock_source"]}
                                  if "retained_kickoff" in pair else {})})
    qualifying = [c for c in candidates if c["terms"]["qualifies"]]
    best = max(qualifying or candidates, key=lambda c: c["terms"]["expected_return_per_dollar"])
    if not qualifying:
        from app.services.shared_daily_allowance import budget_only, zero_room_detail, zero_room_reason

        # Honest reasons (audit item 13): value that qualified but was sized to $0
        # by the shared allowance alone is a budget decline, never a no-edge.
        budget = [c for c in candidates if budget_only(c["terms"], POLICY.min_adjusted_edge)]
        if shared is not None and budget:
            best = max(budget, key=lambda c: c["terms"]["expected_return_per_dollar"])
            return {**base, "outcome": "DECLINED", "reason": zero_room_reason(shared),
                    "detail": zero_room_detail(shared, "CFB"), "best": best, "bankroll": bankroll}
        return {**base, "outcome": "DECLINED", "reason": "NO_QUALIFYING_EDGE",
                "detail": f"Best side {best['side']} at {best['sportsbook']}: {best['terms']['reason']}.",
                "best": best, "bankroll": bankroll}
    if not getattr(settings, "cfb_paper_decisions_enabled", False):
        return {**base, "outcome": "DECLINED", "reason": "ACTIVATION_CONTROL_OFF",
                "detail": "Every gate passed, but CFB_PAPER_DECISIONS_ENABLED is off; no position is recorded.",
                "selection": best, "bankroll": bankroll}
    return {**base, "outcome": "QUALIFIED", "reason": "ALL_GATES_PASSED", "selection": best, "bankroll": bankroll}


def record(session: Session, era: ShadowExperiment, game: dict[str, Any], decision: dict[str, Any],
           moment: datetime, *, settings: Any = None, clock: Any = None) -> dict[str, Any]:
    # The record clock is the injected ``clock`` when the caller supplies one (``evaluate`` always
    # does: the wall clock in production, the test's clock under ``now=``), else the ``moment`` the
    # caller passed. MAIN's version read the wall clock unconditionally, which silently ignored
    # every injected clock and killed the fixture-dated regression tests (review HIGH-3).
    moment = utc_instant(moment) if moment is not None else utc_instant((clock or _wall_clock)())
    prior = session.scalar(select(ShadowExperimentDecision).where(
        ShadowExperimentDecision.experiment_id == era.id, ShadowExperimentDecision.game_id == game["game_id"],
        ShadowExperimentDecision.phase == PHASE).order_by(ShadowExperimentDecision.recommended_at.desc()).limit(1))
    if prior is not None:
        position = session.scalar(select(OfficialAspPosition).where(
            OfficialAspPosition.shadow_decision_id == prior.id))
        prior_reason = (prior.qualification_reasons or {}).get("reason")
        if position is None and budget_only_successor(prior.evidence, decision,
                                                      prior_recommendation=prior.recommendation,
                                                      prior_reason=prior_reason):
            # A value decline caused ONLY by an exhausted daily cap, now genuinely
            # qualified under a different, currently effective shared allowance:
            # append a successor row; the old row and its clocks stay as they are.
            decision = {**decision, "supersedes_decision_id": str(prior.id),
                        "successor_basis": "BUDGET_ONLY_DECLINE_UNDER_DIFFERENT_EFFECTIVE_SHARED_ALLOWANCE"}
        elif position is not None or prior_reason == decision["reason"] or prior_reason not in PREREQUISITE_REASONS:
            return _jsonable({**decision, "persisted": "REUSED_EXISTING_DECISION", "decision_id": prior.id,
                              "position_id": position.id if position else None})
    selection = decision.get("selection") or decision.get("best") or {}
    if decision["outcome"] == "QUALIFIED":
        refusal = position_guard(session, game, selection, moment)
        if refusal is not None:
            decision = {**decision, "outcome": "DECLINED", "reason": refusal["reason"],
                        "detail": f"Refused at position creation: {refusal}", "position_guard": refusal}
    from app.services.owner_funding_restrictions import apply_to_decision
    decision = apply_to_decision(decision, "CFB", moment, settings)
    qualified = decision["outcome"] == "QUALIFIED"
    row = ShadowExperimentDecision(
        experiment_id=era.id, game_id=game["game_id"], slate_date=slate_date(game), phase=PHASE, sport="CFB",
        market="MONEYLINE", period="full_game_including_overtime",
        selection=str(selection.get("side") or "NONE"), sportsbook=selection.get("sportsbook"),
        american_odds=selection.get("american_odds"),
        decimal_odds=american_to_decimal(int(selection["american_odds"])) if selection.get("american_odds") else None,
        market_outcome_id=selection.get("market_outcome_id"), odds_snapshot_id=selection.get("odds_snapshot_id"),
        quote_observed_at=datetime.fromisoformat(selection["observed_at"]) if selection.get("observed_at") else None,
        cfb_research_forecast_id=decision.get("cfb_research_forecast_id"), recommended_at=moment,
        model_probability=selection.get("win_probability"),
        expected_value=(selection.get("terms") or {}).get("expected_return_per_dollar"),
        recommendation="QUALIFIED_BET" if qualified else decision["outcome"],
        recommendation_reason=str(decision.get("detail") or decision["reason"]),
        qualification_reasons={"reason": decision["reason"], "outcome": decision["outcome"]},
        source_mode="LIVE", evidence=_jsonable({k: v for k, v in decision.items() if k != "eligibility"}))
    session.add(row)
    session.flush()
    position_id = None
    if qualified:
        stake = float(selection["terms"]["stake"])
        if stake > 0:
            position = OfficialAspPosition(
                shadow_forecast_id=None, market_outcome_id=selection["market_outcome_id"],
                odds_snapshot_id=selection["odds_snapshot_id"], placed_at=moment, stake=stake,
                decimal_odds=american_to_decimal(int(selection["american_odds"])),
                result=PositionResult.PENDING, status=PositionStatus.OPEN, strategy=era.staking_policy_version,
                shadow_experiment_id=era.id, shadow_decision_id=row.id,
                bankroll_before_position=round(float(decision.get("bankroll") or era.starting_bankroll), 2),
                recommendation_policy_version=era.recommendation_policy_version,
                staking_policy_version=era.staking_policy_version)
            session.add(position)
            session.flush()
            position_id = position.id
    return _jsonable({**decision, "persisted": "RECORDED", "decision_id": row.id, "position_id": position_id})


def evaluate(session: Session, settings: Any, *, now: datetime | None = None, clock: Any = None) -> dict[str, Any]:
    """The checkpoint. One recorded outcome per game in its window, always with a reason."""

    # An injected ``now`` IS the clock unless a clock is injected too (review HIGH-3).
    clock = clock or ((lambda: now) if now is not None else _wall_clock)
    moment = utc_instant(now or clock())
    era = experiment(session)
    if era is None:
        return {"records": 0, "reason": "CFB_PAPER_POLICY_IS_NOT_ACTIVE", "decisions": []}
    due = games_at_checkpoint(session, now=moment)
    if not due:
        return {"records": 0, "reason": "NO_CFB_GAME_AT_ITS_CHECKPOINT", "decisions": [],
                "experiment_id": str(era.id), "activation": bool(getattr(settings, "cfb_paper_decisions_enabled", False))}
    discovery = None
    if getattr(settings, "cfb_production_consumer_enabled", False):
        from app.cfb.production_import import consume_discovery

        try:
            discovery = consume_discovery(session, settings, now=moment)
            session.commit()
            discovery = {k: discovery.get(k) for k in ("status", "manifest_sha256", "currently_eligible", "imported")}
        except Exception as error:  # noqa: BLE001 -- a stale manifest is a block below, never a crash
            session.rollback()
            discovery = {"status": f"FAILED:{type(error).__name__}"}
    decisions = []
    for game in due:
        try:
            decision=decide(session,settings,era,game,utc_instant(clock()))
            with session.begin_nested():
                outcome=record(session,era,game,decision,utc_instant(clock()),settings=settings,clock=clock)
                if outcome.get('position_id') and outcome.get('persisted') == 'RECORDED':
                    refusal=position_guard(session,game,decision.get('selection') or {},utc_instant(clock()))
                    if refusal: raise _LatePosition(refusal)
            session.commit()
            decisions.append(outcome)
        except _LatePosition as error:
            session.rollback()
            declined={**decision,'outcome':'DECLINED','reason':error.detail['reason'],'position_guard':error.detail}
            decisions.append(record(session,era,game,declined,utc_instant(clock()),settings=settings,clock=clock))
            session.commit()
        except Exception as error:
            session.rollback()
            decisions.append({'game_id':str(game['game_id']),'outcome':'BLOCKED',
                              'reason':'GAME_EVALUATION_FAILED','error_type':type(error).__name__,'position_id':None})
    return _jsonable({"records": sum(1 for d in decisions if d.get("position_id")), "reason": "CHECKPOINT_EVALUATED",
                      "experiment_id": era.id, "policy_version": POLICY.policy_version,
                      "activation": bool(getattr(settings, "cfb_paper_decisions_enabled", False)),
                      "discovery_refresh": discovery, "decisions": decisions})


__all__ = ["CHECKPOINT_TEXT", "KICKOFF_BOUND_SOURCES", "NO_PRICE_REASONS", "PAST_DECISION_CUTOFF", "PHASE",
           "QUOTE_KICKOFF_MISMATCH", "WINDOW_CLOSES", "WINDOW_OPENS", "checkpoint_semantics", "decide", "evaluate",
           "games_at_checkpoint", "kickoff_bound_pairs", "position_guard", "record", "utc_instant"]


def _wall_clock():
    return datetime.now(UTC)

class _LatePosition(Exception):
    def __init__(self, detail): self.detail=detail

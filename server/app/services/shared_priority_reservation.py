"""AISP_SHARED_100_PRIORITY_MLB_NHL_V1 -- MLB and NHL come first inside the shared $100 (owner, 2026-10-16).

THE OWNER'S DECISION
--------------------
James, 2026-10-16 21:20Z, on the shared $100 running out on College Football
before the evening MLB and NHL games: "Split by sport" and "I want to prioritize
MLB and NHL over CFB." The daily total is unchanged: ONE $100 per Eastern slate
date across every official paper sport (``shared_daily_allowance``). What
changes is who may use the last of it.

THE RULE
--------
MLB and NHL are PRIORITY sports. Every priority game on the slate date that is
still waiting for its own decision RESERVES its policy's maximum stake:

* NHL: ``research_paper_policy.NHL.max_stake`` ($5).
* MLB: the funding era's ``max_position_percentage`` x its ``starting_bankroll``
  (the MLB policy states its cap as a share of the bankroll, not in dollars).

A NON-priority sport (CFB, NFL, NBA, anything else) may use only
``$100 - entered - every reservation``. A priority game may use
``$100 - entered - the reservations of the OTHER priority games ordered before
it`` (start time, then game id). Its own reservation is what it is consuming.
The ordering only matters when the priority games alone want more than the room
left: under the literal "every other priority game" rule two games of one
start-time cluster would each hold the other's reservation and NEITHER could
bet; ordering makes it first-come, first-served among priority games, and is
identical to the literal rule whenever the reservations fit.

WHAT "WAITING FOR ITS DECISION" MEANS (when a reservation is held)
-----------------------------------------------------------------
A priority game reserves while ALL hold:
(a) it is SCHEDULED and starts after the decision moment, on this Eastern date;
(b) it has no final decision yet -- a bet (an official position) or a pass. A
    decision a later tick may still revisit is NOT final:
    * NHL: the latest FINAL_PREGAME row's reason is one of ``app.nhl.paper``'s
      PREREQUISITE_REASONS (a missing price, a missing forecast, ...).
    * MLB: rows written T-30..T-15 are provisional (a later tick can append a
      new one and the position is only created after T-15 from the last row at
      or before the cutoff). A pass is final only after the cutoff with no
      QUALIFIED_BET at or before it; a QUALIFIED_BET holds until its position
      exists or the game starts.
(c) it can actually be decided and funded: the sport's paper switches are on,
    its era is active, and a forecast is held --
    * NHL: an admissible forecast (``app.nhl.paper.pregame_forecast``), and for
      an AISP in-process forecast its in-process eligibility.
    * MLB: a ready V18 forecast, OR -- because V18 forecasts are published only
      2-3 hours before first pitch, after lineups, which is after the afternoon
      CFB windows -- a game still awaiting its V18 publication
      (``shared_daily_priority_mlb_reserve_before_forecast``, default on; off =
      a held V18 forecast is required). Without this MLB would never be
      protected against the afternoon CFB cohorts, which is what the owner asked.

A reservation therefore releases by itself when the game is decided (bet or
pass) or starts. Nothing is written: reservations are read, under the shared
lock, at every request.

SAFETY
------
The total can never exceed $100: every request is capped at
``$100 - entered - (reservations >= 0)``, the entered total is read after the
cross-sport advisory lock, and every official position writer holds that lock
until it commits. A wrong reservation can only move room between sports, never
create it. ``shared_daily_priority_reservation_enabled=false`` turns this off
and restores the plain first-come rule. A defect while reading the holds fails
OPEN to that same plain rule (inside a SAVEPOINT, logged, and recorded as
``state: ERROR:<type>`` in the decision evidence) rather than closing every
sport's allowance.
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

POLICY = "AISP_SHARED_100_PRIORITY_MLB_NHL_V1"
OWNER_DECISION = ("JAMES_OWNER_DECISION_2026-10-16T21:20Z: 'Split by sport' and "
                  "'I want to prioritize MLB and NHL over CFB.'")
PRIORITY_SPORTS = ("MLB", "NHL")
ENABLED_SETTING = "shared_daily_priority_reservation_enabled"
MLB_BEFORE_FORECAST_SETTING = "shared_daily_priority_mlb_reserve_before_forecast"
ORDERING = "PRIORITY_GAMES_FIRST_COME_BY_START_THEN_GAME_ID"
ET = ZoneInfo("America/New_York")
#: MLB: the position is created from the last FINAL_PREGAME row at or before first pitch - 15 min.
MLB_CUTOFF = timedelta(minutes=15)

_SPORT_ALIASES = {"BASEBALL_MLB": "MLB", "MLB": "MLB", "NHL": "NHL", "ICE_HOCKEY_NHL": "NHL"}


def sport_key(sport: Any) -> str | None:
    if sport in (None, ""):
        return None
    text = str(sport).strip().upper()
    return _SPORT_ALIASES.get(text, text)


def is_priority(sport: Any) -> bool:
    return sport_key(sport) in PRIORITY_SPORTS


def enabled(settings: Any) -> bool:
    return bool(getattr(settings, ENABLED_SETTING, True)) if settings is not None else True


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """[midnight ET of ``day``, midnight ET of the next day) in UTC (DST-correct)."""

    nxt = day + timedelta(days=1)
    return (datetime(day.year, day.month, day.day, tzinfo=ET).astimezone(UTC),
            datetime(nxt.year, nxt.month, nxt.day, tzinfo=ET).astimezone(UTC))


def _empty(settings: Any, *, sport: Any, game_id: Any, state: str) -> dict[str, Any]:
    return {"policy": POLICY, "owner_decision": OWNER_DECISION, "state": state,
            "priority_sports": list(PRIORITY_SPORTS), "requesting_sport": sport_key(sport),
            "requesting_game_id": str(game_id) if game_id else None, "priority": is_priority(sport),
            "ordering": ORDERING, "reserved_usd": 0.0, "reserved_games": 0, "pending_priority_games": 0,
            "reservations": []}


# ----------------------------------------------------------------------------- NHL --
def _nhl_pending(session: Session, settings: Any, games: list[tuple], moment: datetime) -> list[dict[str, Any]]:
    if not (getattr(settings, "nhl_paper_checkpoint_enabled", False)
            and getattr(settings, "nhl_paper_decisions_enabled", False)):
        return []
    from app.models import OfficialAspPosition, ShadowExperimentDecision
    from app.nhl import paper as nhl

    era = nhl.experiment(session)
    if era is None:
        return []
    ids = [g[0] for g in games]
    latest: dict[Any, tuple[datetime | None, str | None]] = {}
    for gid, at, reasons in session.execute(
            select(ShadowExperimentDecision.game_id, ShadowExperimentDecision.recommended_at,
                   ShadowExperimentDecision.qualification_reasons)
            .where(ShadowExperimentDecision.experiment_id == era.id, ShadowExperimentDecision.phase == nhl.PHASE,
                   ShadowExperimentDecision.game_id.in_(ids))).all():
        at = _utc(at)
        reason = (reasons or {}).get("reason") if isinstance(reasons, dict) else None
        if gid not in latest or (at is not None and (latest[gid][0] is None or at > latest[gid][0])):
            latest[gid] = (at, reason)
    positioned = {gid for (gid,) in session.execute(
        select(ShadowExperimentDecision.game_id)
        .join(OfficialAspPosition, OfficialAspPosition.shadow_decision_id == ShadowExperimentDecision.id)
        .where(ShadowExperimentDecision.experiment_id == era.id,
               ShadowExperimentDecision.game_id.in_(ids))).all()}
    reserve = round(float(nhl.POLICY.max_stake), 2)
    out = []
    for gid, starts_at, _code, _mode in games:
        if gid in positioned:
            continue
        prior = latest.get(gid)
        if prior is not None and prior[1] not in nhl.PREREQUISITE_REASONS:
            continue                                             # decided: a pass (or any final outcome)
        start = _utc(starts_at)
        forecast, _excluded = nhl.pregame_forecast(session, gid, moment=moment, starts_at=start)
        if forecast is None:
            continue
        if nhl._route(forecast) == nhl.ROUTE_IN_PROCESS and not nhl.in_process_eligibility(
                forecast, moment=moment, starts_at=start)["eligible"]:
            continue
        out.append({"sport": "NHL", "game_id": str(gid), "starts_at": start.isoformat(), "reserve_usd": reserve,
                    "reserve_basis": "AISP_NHL_PAPER_POLICY_V1.max_stake",
                    "state": "AWAITING_DECISION" if prior is None else f"REVISITABLE:{prior[1]}",
                    "forecast": {"state": "HELD", "forecast_id": forecast.forecast_id,
                                 "route": nhl._route(forecast)}})
    return out


# ----------------------------------------------------------------------------- MLB --
def _mlb_funding_experiments(session: Session, settings: Any, moment: datetime) -> list[Any]:
    from app.models import ShadowExperiment

    active = [e for e in session.scalars(select(ShadowExperiment).where(
        ShadowExperiment.sport == "MLB", ShadowExperiment.is_active.is_(True)))
        if _utc(e.started_at) is None or _utc(e.started_at) <= moment]
    eras = [e for e in active if e.model_era_id]
    if eras:
        # A started model era closes every predecessor to new positions; while the
        # V18 funding pause is on, the era funds nothing, so nothing is reserved.
        return [] if getattr(settings, "mlb_v18_funding_pause_enabled", False) else eras
    return active


def _mlb_pending(session: Session, settings: Any, games: list[tuple], moment: datetime) -> list[dict[str, Any]]:
    from app.models import ExperimentDecisionPhase, OfficialAspPosition, ShadowExperimentDecision
    from app.services.v18_forecast_view import latest_v18_forecast

    funding = _mlb_funding_experiments(session, settings, moment)
    if not funding:
        return []
    ids = [g[0] for g in games]
    exp_ids = [e.id for e in funding]
    reserve = max(round(float(e.max_position_percentage) * float(e.starting_bankroll), 2) for e in funding)
    from app.models import PositionStatus
    from app.services.shared_daily_allowance import eastern_date

    occurrence = {g[0]: eastern_date(g[1]) for g in games if g[1] is not None}
    # AISP_MLB_POSTPONED_NOT_PLAYED_ON_ORIGINAL_ET_DATE_V1: a rescheduled game
    # keeps its games row. Only a decision made for THIS occurrence (its slate
    # date is the Eastern date of the current start) can fund it -- the
    # position path enforces that -- so only such a decision holds room. A
    # VOID position of ANOTHER occurrence decides nothing about this one when
    # the occurrence-aware duplicate guard can actually let the makeup through
    # (its flag on and migration 20261019_0055 applied); otherwise any
    # position still decides the game, exactly as before.
    reopen_void = bool(getattr(settings, "mlb_rescheduled_occurrence_positions_enabled", False))
    positioned: set[Any] = set()
    voided_elsewhere: set[Any] = set()
    for gid, status, slate in session.execute(
            select(ShadowExperimentDecision.game_id, OfficialAspPosition.status, ShadowExperimentDecision.slate_date)
            .join(OfficialAspPosition, OfficialAspPosition.shadow_decision_id == ShadowExperimentDecision.id)
            .where(OfficialAspPosition.shadow_experiment_id.in_(exp_ids),
                   ShadowExperimentDecision.game_id.in_(ids))).all():
        if reopen_void and status == PositionStatus.VOID and slate is not None and slate != occurrence.get(gid):
            voided_elsewhere.add(gid)
        else:
            positioned.add(gid)
    if voided_elsewhere - positioned:
        from app.services.shadow_experiment_service import live_outcome_index_present

        if not live_outcome_index_present(session):
            positioned |= voided_elsewhere
    qualified: dict[Any, list[datetime]] = {}
    for gid, at, slate in session.execute(
            select(ShadowExperimentDecision.game_id, ShadowExperimentDecision.recommended_at,
                   ShadowExperimentDecision.slate_date)
            .where(ShadowExperimentDecision.experiment_id.in_(exp_ids),
                   ShadowExperimentDecision.game_id.in_(ids),
                   ShadowExperimentDecision.phase == ExperimentDecisionPhase.FINAL_PREGAME,
                   ShadowExperimentDecision.recommendation == "QUALIFIED_BET")).all():
        if slate is not None and gid in occurrence and slate != occurrence[gid]:
            continue                                             # another occurrence's decision
        qualified.setdefault(gid, []).append(_utc(at))
    strict = not bool(getattr(settings, MLB_BEFORE_FORECAST_SETTING, True))
    engines = sorted({e.controlling_engine for e in funding if e.model_era_id and e.controlling_engine})
    from app.services.owner_funding_restrictions import mlb_record_only_active

    out = []
    for gid, starts_at, _code, _mode in games:
        if gid in positioned:
            continue                                             # decided: a bet
        start = _utc(starts_at)
        if mlb_record_only_active(start, moment=moment, settings=settings):
            # Owner Oct19: the rest of MLB 2026 is RECORD-ONLY. A game that can only
            # ever be recorded (mlb_position_restriction refuses every position for
            # it) is not waiting for funding, so it holds no room back from CFB/NHL
            # (hotfix 2026-10-24, review MEDIUM-3). Nothing about the decision,
            # the receipt or the record-only disposition itself changes.
            continue
        cutoff = start - MLB_CUTOFF
        qualified_by_cutoff = any(at is not None and at <= cutoff for at in qualified.get(gid, []))
        if moment > cutoff and not qualified_by_cutoff:
            continue                                             # decided: a pass (final after the cutoff)
        forecast_state = "NOT_REQUIRED_GEN1_ERA"
        if engines:
            held = None
            for engine in engines:
                row = latest_v18_forecast(session, gid, engine_id=engine)
                if row is not None and row.is_ready:
                    held = row
                    break
            forecast_state = "V18_FORECAST_HELD" if held is not None else "AWAITING_V18_PUBLICATION"
            if strict and held is None:
                continue
        out.append({"sport": "MLB", "game_id": str(gid), "starts_at": start.isoformat(), "reserve_usd": reserve,
                    "reserve_basis": "funding era max_position_percentage x starting_bankroll",
                    "state": "QUALIFIED_AWAITING_POSITION" if moment > cutoff else "AWAITING_FINAL_DECISION",
                    "forecast": {"state": forecast_state}})
    return out


# ------------------------------------------------------------------------ the rule --
def reservations(session: Session, settings: Any, *, day: date, moment: datetime, sport: Any = None,
                 game_id: Any = None) -> dict[str, Any]:
    """The room held back from this request for undecided priority games on Eastern ``day``.

    Pure read. ``room()`` calls it after taking the cross-sport lock; a read-only
    replay may call it directly without the lock.
    """

    if not enabled(settings):
        return _empty(settings, sport=sport, game_id=game_id, state="OFF")
    from app.models import Game, GameStatus, League

    moment = _utc(moment)
    lo, hi = day_bounds(day)
    rows = session.execute(
        select(Game.id, Game.starts_at, League.code, Game.source_mode)
        .join(League, League.id == Game.league_id)
        .where(League.code.in_(PRIORITY_SPORTS), Game.status == GameStatus.SCHEDULED,
               Game.starts_at > moment, Game.starts_at >= lo, Game.starts_at < hi)).all()
    out = _empty(settings, sport=sport, game_id=game_id, state="ON")
    if not rows:
        return out
    nhl_games = [r for r in rows if r[2] == "NHL"]
    mlb_games = [r for r in rows if r[2] == "MLB" and str(r[3] or "LIVE").upper() == "LIVE"]
    try:
        # A SAVEPOINT keeps a failed read from poisoning the caller's transaction (and its lock).
        nested = session.begin_nested()
        try:
            pending = ((_nhl_pending(session, settings, nhl_games, moment) if nhl_games else [])
                       + (_mlb_pending(session, settings, mlb_games, moment) if mlb_games else []))
            nested.commit()
        except Exception:
            nested.rollback()
            raise
    except Exception as error:  # noqa: BLE001 -- degrade to the plain shared $100, visibly
        # Fail OPEN to the rule production ran before this policy (no holds), never to a
        # closed allowance: a defect here must not stop every sport from betting. The
        # state is recorded in each decision's evidence, and the total is still capped.
        logger.exception("shared priority reservations unavailable; no holds applied")
        return {**out, "state": f"ERROR:{type(error).__name__}"}
    own = str(game_id) if game_id else None
    counted = [p for p in pending if p["game_id"] != own]
    if is_priority(sport) and own is not None:
        own_start = next((_utc(r[1]) for r in rows if str(r[0]) == own), None)
        if own_start is not None:
            mine = (own_start, own)
            counted = [p for p in counted if (datetime.fromisoformat(p["starts_at"]), p["game_id"]) < mine]
    out.update({"reserved_usd": round(sum(p["reserve_usd"] for p in counted), 2), "reserved_games": len(counted),
                "pending_priority_games": len(pending), "reservations": counted})
    return out


__all__ = ["ENABLED_SETTING", "MLB_BEFORE_FORECAST_SETTING", "OWNER_DECISION", "POLICY", "PRIORITY_SPORTS",
           "day_bounds", "enabled", "is_priority", "reservations", "sport_key"]

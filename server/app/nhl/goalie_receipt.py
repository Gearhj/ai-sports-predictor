"""NHL starting goalies and injuries on every NHL paper decision (owner 2026-10-28: "collect both and implement any
changes that could possibly provide an edge").

WHY
---
AISP's in-process NHL Gen1 is a team-level model: its own document says HOME_GOALIE_UNKNOWN, AWAY_GOALIE_UNKNOWN and
INJURIES_UNAVAILABLE, and its goalie feature mode is TEAM_LEVEL. The market prices tonight's actual starter; the
model prices the team's average goalie. When the side AISP would back is starting its BACKUP, the model's "edge" is
largely the goalie it does not see, in exactly the direction that flatters the bet.

THE RECEIPT (every NHL decision, nonfunding)
--------------------------------------------
From files the ESPN collector (``collect-availability``) already holds at the decision clock -- no request here:
* each side's listed starting goalie and ESPN's status for it (CONFIRMED / PROBABLE / none listed);
* each side's usual starter: the goalie with the most starts in the team's last 5 completed games (box scores held),
  frozen at T-90 like every key-player rule;
* ``backup_starting`` = a listed starter who is not that usual starter;
* the key skaters (top-2 points per game) with their injury status, and the notable injured list.

THE RULE (AISP_OWNER_NHL_BACKUP_GOALIE_RECORD_ONLY_V1, active from ``NHL_BACKUP_GOALIE_GATE_EFFECTIVE_FROM``)
---------------------------------------------------------------------------------------------------------
A QUALIFIED NHL decision whose SELECTED team is starting its backup goalie (listed CONFIRMED or PROBABLE, snapshot
no more than 45 minutes old) is recorded RECORD_ONLY: prices, value and selection kept, no position. It never
changes a probability, a selection or a price, never adds a bet, and an unknown starter never blocks (ESPN often
lists no goalie until late; the receipt says so). The opponent starting its backup does not block: that error runs
against the bet, not for it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from app.nba.availability import ROLE_FREEZE, _aware, _when

RULE = "AISP_OWNER_NHL_BACKUP_GOALIE_RECORD_ONLY_V1"
REASON = "OWNER_NHL_BACKUP_GOALIE_RECORD_ONLY"
SETTING = "nhl_backup_goalie_gate_effective_from"
SCHEMA = "AISP_NHL_GOALIE_INJURY_RECEIPT_V1"
SNAPSHOT_MAX_AGE = timedelta(minutes=45)
MATCH_WINDOW = timedelta(hours=3)
LISTED = ("CONFIRMED", "PROBABLE")


def _teams(session: Any, game_id: Any) -> dict[str, str | None]:
    from sqlalchemy import select

    from app.models import GameTeam, Team

    rows = session.execute(select(GameTeam.is_home, Team.abbreviation).join(Team, Team.id == GameTeam.team_id)
                           .where(GameTeam.game_id == game_id)).all()
    return {("home" if is_home else "away"): (str(abbr).upper() if abbr else None) for is_home, abbr in rows}


def find_event(settings: Any, home: str | None, away: str | None, starts_at: datetime) -> tuple[str, dict[str, Any]] | None:
    """The one ESPN event in the collector's day index with these two teams in this orientation near this start."""

    from app.nhl.espn_results import nhl_code
    from app.services.availability_board import ET, _lg_root

    out = _lg_root(settings, "NHL")
    found: list[tuple[str, dict[str, Any]]] = []
    days = sorted({(starts_at + d).astimezone(ET).date().isoformat() for d in (timedelta(hours=-6), timedelta(0), timedelta(hours=6))})
    for day in days:
        p = out / "index" / f"{day}.json"
        if not p.exists():
            continue
        for eid, meta in json.loads(p.read_bytes()).items():
            t = meta.get("teams") or {}
            start = _when(meta.get("starts_at"))
            if (nhl_code((t.get("home") or {}).get("abbreviation")) == home and nhl_code((t.get("away") or {}).get("abbreviation")) == away
                    and start is not None and abs(start - starts_at) <= MATCH_WINDOW and eid not in {f[0] for f in found}):
                found.append((eid, meta))
    return found[0] if len(found) == 1 else None


def assess(session: Any, settings: Any, game: dict[str, Any], moment: datetime) -> dict[str, Any]:
    from app.services.availability_board import _latest, _lg_root, _nhl_goalie, game_view

    moment = _aware(moment)
    starts = _aware(game["starts_at"])
    receipt: dict[str, Any] = {"schema": SCHEMA, "rule": RULE, "source": "ESPN_PUBLIC", "funding_authority": False,
                               "evaluated_at_utc": moment.isoformat(), "model_goalie_feature_mode": "TEAM_LEVEL"}
    sides = _teams(session, game["game_id"])
    found = find_event(settings, sides.get("home"), sides.get("away"), starts)
    if found is None:
        return {**receipt, "state": "UNKNOWN", "reason": "ESPN_EVENT_NOT_MATCHED", "teams": sides}
    eid, meta = found
    out = _lg_root(settings, "NHL")
    snap = _latest(out / "snapshots" / eid, moment)
    if snap is None:
        return {**receipt, "state": "UNKNOWN", "reason": "NO_SNAPSHOT_HELD", "espn_event_id": eid}
    age = moment - snap["_observed"]
    stale = age > SNAPSHOT_MAX_AGE
    view = game_view(settings, "NHL", eid, meta, moment)
    freeze = starts - ROLE_FREEZE
    teams: dict[str, Any] = {}
    flags: list[str] = []
    for side in ("away", "home"):
        team = (snap.get("teams") or {}).get(side) or {}
        tid = str(team.get("espn_team_id"))
        listed = [p for p in (snap.get("probables") or {}).get(tid, []) if "goalie" in str(p.get("kind") or "goalie").lower()]
        starter = listed[0] if listed else None
        usual = _nhl_goalie(out, tid, freeze)
        status = (starter or {}).get("status") if starter else None
        backup = bool(starter and usual and starter["espn_athlete_id"] != usual["espn_athlete_id"])
        v = (view.get("teams") or {}).get(side) or {}
        teams[side] = {"abbreviation": team.get("abbreviation"), "espn_team_id": tid,
                       "starting_goalie": starter and {"espn_athlete_id": starter["espn_athlete_id"], "name": starter["name"], "status": status},
                       "usual_goalie": usual, "backup_starting": backup,
                       "key_skaters": [k for k in v.get("key_players") or [] if not str(k.get("role", "")).startswith("G1")],
                       "notable_injuries": v.get("notable") or []}
        if backup:
            flags.append(f"{team.get('abbreviation')}_BACKUP_GOALIE_{status or 'LISTED'}")
        if starter is None:
            flags.append(f"{team.get('abbreviation')}_STARTER_NOT_LISTED")
    if view.get("state") == "KEY_OUT":
        flags.append(f"KEY_SKATER_OUT:{view.get('reason')}")
    known = all((teams[s]["starting_goalie"] or {}).get("status") in LISTED for s in teams)
    confirmed = all((teams[s]["starting_goalie"] or {}).get("status") == "CONFIRMED" for s in teams)
    state = "UNKNOWN" if stale else ("STARTERS_CONFIRMED" if confirmed else "STARTERS_LISTED" if known else "STARTER_UNKNOWN")
    return {**receipt, "state": state, "reason": "SNAPSHOT_STALE" if stale else ",".join(flags) or "USUAL_STARTERS",
            "espn_event_id": eid, "snapshot_observed_at_utc": snap["_observed"].isoformat(), "snapshot_sha256": snap["_sha256"],
            "snapshot_age_minutes": round(age.total_seconds() / 60, 1), "role_freeze_utc": freeze.isoformat(),
            "flags": flags, "teams": teams, "injury_state": view.get("state"), "injury_reason": view.get("reason")}


def gate_active(settings: Any, moment: datetime) -> bool:
    clock = _when(getattr(settings, SETTING, None)) if getattr(settings, SETTING, None) else None
    return clock is not None and _aware(moment) >= clock


def apply(session: Any, decision: dict[str, Any], game: dict[str, Any], moment: datetime, settings: Any) -> dict[str, Any]:
    """Attach the receipt to every NHL decision; while the rule is in force a QUALIFIED decision backing a team that
    starts its backup goalie becomes RECORD_ONLY (prices and value kept, no position)."""

    try:
        receipt = assess(session, settings, game, moment)
    except Exception as error:  # noqa: BLE001 - an unreadable source is UNKNOWN, never a reason to bet differently
        receipt = {"schema": SCHEMA, "rule": RULE, "funding_authority": False, "state": "UNKNOWN",
                   "reason": f"ASSESSMENT_FAILED:{type(error).__name__}"}
    result = {**decision, "goalie_injury": receipt}
    if decision.get("outcome") != "QUALIFIED" or settings is None or not gate_active(settings, moment):
        return result
    selection = decision.get("selection") or decision.get("best") or {}
    side = str(selection.get("side") or "").lower()
    picked = (receipt.get("teams") or {}).get(side) or {}
    starter = picked.get("starting_goalie") or {}
    if receipt.get("state") == "UNKNOWN" or not picked.get("backup_starting") or starter.get("status") not in LISTED:
        return result
    restriction = {"rule": RULE, "record_only": True, "reason": REASON, "sport": "NHL",
                   "effective_from_utc": _when(getattr(settings, SETTING)).isoformat(),
                   "selected_side": side.upper(), "starting_goalie": starter, "usual_goalie": picked.get("usual_goalie"),
                   "original_qualification": {"outcome": decision.get("outcome"), "reason": decision.get("reason")},
                   "sportsbook": selection.get("sportsbook"), "snapshot_id": str(selection.get("odds_snapshot_id") or ""),
                   "funding_checked_at_utc": _aware(moment).isoformat()}
    usual = (picked.get("usual_goalie") or {}).get("name")
    return {**result, "funding_restriction": restriction, "outcome": "RECORD_ONLY", "reason": REASON,
            "detail": (f"Owner backup-goalie rule: {picked.get('abbreviation')} starts {starter.get('name')} ({starter.get('status')}), "
                       f"not usual starter {usual}; the team-level model cannot see it. Retained decision and prices; no position. {REASON}")}

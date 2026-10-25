"""In-game NFL score from ESPN's free public scoreboard (display only).

WHY THIS EXISTS
---------------
The governed in-game source (SportsGameOdds, ``nfl_live``) bills per event
object, and its allowance has been paused since its free fallback window
closed on 2026-09-30. So on a Sunday with eight open NFL positions the
scoreboard showed kickoff times instead of scores. ESPN publishes the whole
slate -- score, quarter, game clock and per-quarter line -- as one public JSON
document with no key, no account and no charge. The owner approved this
source on 2026-10-11 as a free, unofficial in-game display feed.

WHAT IT IS NOT
--------------
It is written ONLY to ``game_scoreboard_observations``, the presentation table
that "creates no decision, settles nothing, and is never read as a final".
Settlement and scoring keep reading governed finals (nflverse / The Odds API).
Every row says ``source = "ESPN_PUBLIC_SCOREBOARD"`` so a reader can see where
the number came from.

COST AND CADENCE
----------------
One GET per cycle covers every NFL game on the date, and a cycle makes no
request at all unless some NFL game is between five minutes before kickoff and
four and a half hours after it with no final recorded. Two-minute cadence on a
Sunday afternoon is about one hundred requests to a free public endpoint.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Game, League

SOURCE = "ESPN_PUBLIC_SCOREBOARD"
SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
DEFAULT_CADENCE_MINUTES = 2
WATCH_BEFORE_KICKOFF = timedelta(minutes=5)
WATCH_AFTER_KICKOFF = timedelta(hours=4, minutes=30)
#: An ESPN event is matched to an AISP game by both clubs plus a kickoff within
#: this distance. Wide enough for a flexed kickoff (CHI@GB moved 3h25m on
#: 2026-10-11), narrow enough that a rematch weeks apart can never collide.
KICKOFF_TOLERANCE = timedelta(hours=12)

_STATUS = {
    "STATUS_SCHEDULED": "SCHEDULED",
    "STATUS_IN_PROGRESS": "IN_PROGRESS",
    "STATUS_HALFTIME": "IN_PROGRESS",
    "STATUS_END_PERIOD": "IN_PROGRESS",
    "STATUS_END_OF_REGULATION": "IN_PROGRESS",
    "STATUS_OVERTIME": "IN_PROGRESS",
    "STATUS_FINAL": "FINAL",
    "STATUS_FINAL_OVERTIME": "FINAL",
    "STATUS_DELAYED": "DELAYED",
    "STATUS_RAIN_DELAY": "DELAYED",
    "STATUS_SUSPENDED": "SUSPENDED",
    "STATUS_POSTPONED": "POSTPONED",
    "STATUS_CANCELED": "CANCELLED",
}
_BREAKS = {"STATUS_HALFTIME", "STATUS_END_PERIOD", "STATUS_END_OF_REGULATION"}


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _when(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def classify(status: dict[str, Any]) -> str:
    kind = (status.get("type") or {})
    mapped = _STATUS.get(str(kind.get("name") or ""))
    if mapped:
        return mapped
    state = str(kind.get("state") or "")
    if kind.get("completed") or state == "post":
        return "FINAL"
    if state == "in":
        return "IN_PROGRESS"
    if state == "pre":
        return "SCHEDULED"
    return "UNKNOWN"


def _period_label(number: int) -> str:
    return f"{number}Q" if number <= 4 else ("OT" if number == 5 else f"{number - 4}OT")


def _float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def situation_detail(competition: dict[str, Any], sides: dict[str, Any]) -> dict[str, Any] | None:
    """ESPN's ``competitions[0].situation`` as delivered: down and distance, possession, last play
    and ESPN's own win probability. Display only (step 9); never read by settlement. None when ESPN
    sends no situation, so a row without one is exactly as before.
    """

    situation = competition.get("situation")
    if not isinstance(situation, dict) or not situation:
        return None
    possession = str(situation.get("possession") or "") or None
    team_by_id = {str((c.get("team") or {}).get("id") or ""): c for c in sides.values() if c}
    holder = team_by_id.get(possession or "")
    last_play = situation.get("lastPlay") or {}
    probability = last_play.get("probability") or {}
    return {
        "source": "ESPN",
        "down": _int(situation.get("down")),
        "distance": _int(situation.get("distance")),
        "yard_line": _int(situation.get("yardLine")),
        "down_distance_text": str(situation.get("downDistanceText") or "") or None,
        "is_red_zone": bool(situation["isRedZone"]) if situation.get("isRedZone") is not None else None,
        "possession": {
            "team_id": possession,
            "abbreviation": str((holder.get("team") or {}).get("abbreviation") or "") or None if holder else None,
            "home_away": holder.get("homeAway") if holder else None,
        } if possession else None,
        "last_play": {
            "text": str(last_play.get("text") or "") or None,
            "home_win_percentage": _float(probability.get("homeWinPercentage")),
            "away_win_percentage": _float(probability.get("awayWinPercentage")),
            "probability_source": "ESPN" if probability else None,
        } if last_play else None,
    }


def state_from_event(event: dict[str, Any], *, retrieved_at: datetime) -> dict[str, Any] | None:
    """One scoreboard row's worth of fields from one ESPN event, or None."""

    competition = (event.get("competitions") or [{}])[0]
    sides = {c.get("homeAway"): c for c in competition.get("competitors") or []}
    home, away = sides.get("home"), sides.get("away")
    if not home or not away:
        return None
    status_node = competition.get("status") or event.get("status") or {}
    status = classify(status_node)
    kind = status_node.get("type") or {}
    started = status not in ("SCHEDULED", "POSTPONED", "CANCELLED", "UNKNOWN")

    period = _int(status_node.get("period")) if started else None
    home_lines = [_int(x.get("value")) for x in home.get("linescores") or []]
    away_lines = [_int(x.get("value")) for x in away.get("linescores") or []]
    periods = [
        {"number": i + 1, "label": _period_label(i + 1), "away": a, "home": h}
        for i, (a, h) in enumerate(zip(away_lines, home_lines))
    ]
    complete = bool(periods) and period is not None and len(periods) >= period
    home_score = _int(home.get("score")) if started else None
    away_score = _int(away.get("score")) if started else None
    reconcile = None
    if status == "FINAL" and complete and home_score is not None and away_score is not None:
        reconcile = (sum(p["home"] or 0 for p in periods) == home_score
                     and sum(p["away"] or 0 for p in periods) == away_score)
    clock = _int(status_node.get("clock")) if status == "IN_PROGRESS" else None
    # Down/distance/possession/last play while live, in the existing JSON column (no migration).
    detail = situation_detail(competition, sides) if status == "IN_PROGRESS" else None

    state = {
        "status": status,
        "status_detail": (str(kind.get("shortDetail") or kind.get("detail") or "") or None),
        "away_score": away_score,
        "home_score": home_score,
        "period_number": period,
        "period_label": _period_label(period) if period else None,
        "period_state": None,
        "in_break": (str(kind.get("name") or "") in _BREAKS) if started else None,
        "clock_seconds_remaining": clock,
        "outs": None,
        "balls": None,
        "strikes": None,
        "bases": {"detail": detail} if detail else None,
        "periods": periods,
        "periods_complete": complete,
        "periods_reconcile": reconcile,
    }
    checksum = hashlib.sha256(json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()
    return {
        **state,
        "league_code": "NFL",
        "source": SOURCE,
        "source_mode": "LIVE",
        "source_game_id": str(event.get("id") or "") or None,
        # ESPN publishes no time at which the score became true.
        "source_observed_at": None,
        "retrieved_at": retrieved_at,
        "checked_at": retrieved_at,
        "payload_checksum": checksum,
    }


def clubs(event: dict[str, Any]) -> tuple[str | None, str | None]:
    """(away, home) abbreviations exactly as ESPN sends them."""

    competition = (event.get("competitions") or [{}])[0]
    sides = {c.get("homeAway"): ((c.get("team") or {}).get("abbreviation"))
             for c in competition.get("competitors") or []}
    return sides.get("away"), sides.get("home")


def games_in_window(session: Session, *, now: datetime) -> list[tuple[UUID, datetime]]:
    """NFL games between T-5 minutes and T+4h30 with no final observation yet."""

    from app.models.scoreboard import GameScoreboardObservation

    rows = session.execute(
        select(Game.id, Game.starts_at)
        .join(League, League.id == Game.league_id)
        .where(League.code == "NFL",
               Game.starts_at >= now - WATCH_AFTER_KICKOFF,
               Game.starts_at <= now + WATCH_BEFORE_KICKOFF)
    ).all()
    if not rows:
        return []
    finished = set(session.scalars(
        select(GameScoreboardObservation.game_id).where(
            GameScoreboardObservation.game_id.in_([r[0] for r in rows]),
            GameScoreboardObservation.status.in_(("FINAL", "CANCELLED", "POSTPONED")))
    ).all())
    return [(gid, _aware(start)) for gid, start in rows if gid not in finished]


def _fetch(dates: str) -> dict[str, Any]:
    """One public GET. No key, no retry; a failed cycle just waits for the next."""

    import httpx

    with httpx.Client(timeout=15.0, follow_redirects=False,
                      headers={"Accept": "application/json",
                               "User-Agent": "AISP-scoreboard/1.0"}) as client:
        response = client.get(SCOREBOARD_URL, params={"dates": dates})
    if response.status_code != 200:
        raise RuntimeError(f"ESPN scoreboard returned HTTP {response.status_code}")
    return response.json()


def collect_nfl_live_espn(session: Session, settings: Any, *, now: datetime | None = None,
                          fetch: Any = None) -> dict[str, Any]:
    """One cycle: record ESPN's current state for every NFL game in its window."""

    from app.scoreboard.collect import _club, _nfl_identity
    from app.scoreboard.store import record_many

    moment = now or datetime.now(UTC)
    if not getattr(settings, "nfl_espn_live_scores_enabled", True):
        return {"records": 0, "league": "NFL", "requests": 0, "source": SOURCE,
                "reason": "NFL_ESPN_LIVE_SCORES_DISABLED"}
    window = games_in_window(session, now=moment)
    if not window:
        return {"records": 0, "league": "NFL", "requests": 0, "source": SOURCE,
                "games_in_window": 0, "reason": "NO_NFL_GAME_IN_ITS_WATCH_WINDOW"}

    # ESPN's scoreboard is keyed by the US Eastern calendar date of the slate.
    from zoneinfo import ZoneInfo

    eastern = ZoneInfo("America/New_York")
    dates = sorted({start.astimezone(eastern).strftime("%Y%m%d") for _, start in window})
    events: list[dict[str, Any]] = []
    try:
        for day in dates:
            events.extend((fetch or _fetch)(day).get("events") or [])
    except Exception as error:  # noqa: BLE001 - a failed cycle is reported, never raised
        return {"records": 0, "league": "NFL", "requests": len(dates), "source": SOURCE,
                "games_in_window": len(window), "reason": "REQUEST_FAILED",
                "error_type": type(error).__name__}

    index: dict[tuple[str, str], list[tuple[UUID, datetime]]] = {}
    for game_id, start in window:
        sides = _nfl_identity(session, game_id)
        index.setdefault((_club(sides.get("away")), _club(sides.get("home"))), []).append((game_id, start))

    pairs: list[tuple[UUID, dict[str, Any]]] = []
    unmatched: list[str] = []
    for event in events:
        away, home = clubs(event)
        kickoff = _when(event.get("date"))
        candidates = index.get((_club(away), _club(home))) or []
        match = next((gid for gid, start in candidates
                      if kickoff is not None and abs(start - kickoff) <= KICKOFF_TOLERANCE), None)
        if match is None:
            continue  # not a game AISP is watching right now
        state = state_from_event(event, retrieved_at=moment)
        if state is None:
            unmatched.append(f"{away}@{home}")
            continue
        pairs.append((match, state))

    result = record_many(session, pairs, checked_at=moment)
    session.commit()
    matched = {gid for gid, _ in pairs}
    return {
        "records": result["observations_written"],
        "league": "NFL",
        "source": SOURCE,
        "requests": len(dates),
        "costUsd": 0,
        "events": len(events),
        "games_in_window": len(window),
        "games_matched": len(matched),
        "games_unmatched": [str(gid) for gid, _ in window if gid not in matched],
        "malformed_events": unmatched,
        "states_confirmed": result["states_confirmed"],
        "in_progress": sum(1 for _, s in pairs if s["status"] == "IN_PROGRESS"),
        "cadence_minutes": DEFAULT_CADENCE_MINUTES,
    }


__all__ = [
    "DEFAULT_CADENCE_MINUTES", "SCOREBOARD_URL", "SOURCE", "classify", "clubs",
    "collect_nfl_live_espn", "games_in_window", "state_from_event",
]

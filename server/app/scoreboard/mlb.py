"""MLB scoreboard state, from a response AISP was already paying nothing for.

THE FIELDS WERE ALREADY ARRIVING
--------------------------------
``MLBStatsAPIAdapter.get_schedule`` hydrates ``linescore`` and has done all
along; the DTO then kept two integers out of it and dropped the rest. So the
per-inning breakdown, the current inning and its half, outs, the count and the
base runners have been landing in the archive and going straight in the bin.
Reading them costs no additional request, no additional key and no allowance:
this parses the same bytes the existing refresh already fetched.

WHAT IT REFUSES TO INFER
------------------------
*   **A clock.** Baseball has none and the feed supplies none. The field stays
    NULL rather than being filled with something plausible.
*   **A final.** A stopped clock is not a result. Only the source's own
    ``abstractGameState`` of ``Final`` produces FINAL here, and this module is
    display-only regardless: the governed final ingest is untouched.
*   **An unplayed inning.** The home half of the ninth is genuinely not played
    when the home team is ahead, and the feed omits ``runs`` for it. That is an
    absent value, recorded as absent -- never a 0, which would read as "they
    batted and did not score".
*   **A score timestamp.** The schedule feed carries none, so
    ``source_observed_at`` is NULL and the reader is told the age of the CHECK
    instead of being told a retrieval time as though it were the score's time.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

#: MLB's coded states, mapped to the canonical vocabulary. The detailed state
#: travels alongside, because "Warmup", "Manager challenge" and "In Progress"
#: are all play as far as status goes and are very different to look at.
_CODED_STATUS = {
    "S": "SCHEDULED",   # Scheduled
    "P": "SCHEDULED",   # Pre-Game / Warmup / Delayed Start
    "PW": "IN_PROGRESS",
    "I": "IN_PROGRESS",
    "M": "IN_PROGRESS",  # under review; the game is live
    "F": "FINAL",
    "O": "FINAL",        # Game Over
    "D": "POSTPONED",
    "C": "CANCELLED",
    "U": "SUSPENDED",
    "T": "SUSPENDED",
}

#: A delay is not a status of its own in MLB's coding -- it is carried in the
#: detailed state. These are the exact strings the retained captures contain.
_DELAY_WORDS = ("delayed", "postponed", "suspended", "cancelled", "canceled")


def _sha(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def classify(status: dict[str, Any]) -> tuple[str, str | None]:
    """Canonical status plus the source's own words, never one without the other."""

    detailed = str(status.get("detailedState") or "").strip() or None
    code = str(status.get("statusCode") or status.get("codedGameState") or "").strip()
    canonical = _CODED_STATUS.get(code) or _CODED_STATUS.get(code[:1], "UNKNOWN")

    lowered = (detailed or "").lower()
    if canonical in {"SCHEDULED", "IN_PROGRESS"} and any(
            word in lowered for word in _DELAY_WORDS):
        # "Delayed Start" before first pitch and "Delayed" mid-game are both
        # delays; which one is visible from the period, not from the status.
        if "postponed" in lowered:
            canonical = "POSTPONED"
        elif "suspended" in lowered:
            canonical = "SUSPENDED"
        elif "cancel" in lowered:
            canonical = "CANCELLED"
        else:
            canonical = "DELAYED"
    return canonical, detailed


def periods_from(linescore: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    """One entry per inning the feed reported, and whether the set is complete.

    An inning's half is present with ``runs`` only when it was played. The home
    ninth of a game the home team led is simply absent from the feed, and it
    stays absent here: writing 0 would say they batted and did not score.
    """

    out: list[dict[str, Any]] = []
    for entry in linescore.get("innings") or []:
        if not isinstance(entry, dict):
            continue
        away = entry.get("away") if isinstance(entry.get("away"), dict) else {}
        home = entry.get("home") if isinstance(entry.get("home"), dict) else {}
        out.append({
            "number": _int(entry.get("num")),
            "label": str(entry.get("ordinalNum") or entry.get("num") or ""),
            # `runs` absent means not played, and None carries that through.
            "away": _int(away.get("runs")),
            "home": _int(home.get("runs")),
            "away_hits": _int(away.get("hits")),
            "home_hits": _int(home.get("hits")),
        })
    scheduled = _int(linescore.get("scheduledInnings")) or 9
    current = _int(linescore.get("currentInning")) or 0
    # Complete means the feed described every inning that has been reached --
    # including extras, where the played count exceeds the scheduled nine.
    complete = bool(out) and len(out) >= min(current, max(current, scheduled))
    return out, complete


def scoreboard_from_schedule_game(
    game: dict[str, Any], *, retrieved_at: datetime | None = None,
) -> dict[str, Any]:
    """One scoreboard row's worth of fields, from one schedule game node."""

    moment = retrieved_at or datetime.now(UTC)
    status_node = game.get("status") or {}
    status, detail = classify(status_node)
    teams = game.get("teams") or {}
    away_score = _int((teams.get("away") or {}).get("score"))
    home_score = _int((teams.get("home") or {}).get("score"))

    linescore = game.get("linescore") or {}
    periods, complete = periods_from(linescore)

    inning = _int(linescore.get("currentInning"))
    # Before first pitch the feed carries a linescore skeleton with no innings;
    # reporting "1st" then would invent play that has not happened.
    if not periods and status in {"SCHEDULED", "POSTPONED", "CANCELLED"}:
        inning = None

    offense = linescore.get("offense") or {}
    bases = None
    if status == "IN_PROGRESS":
        bases = {
            base: ((offense.get(base) or {}).get("fullName")
                   if isinstance(offense.get(base), dict) else None)
            for base in ("first", "second", "third")
        }

    reconcile: bool | None = None
    if status == "FINAL" and complete and away_score is not None and home_score is not None:
        away_sum = sum(p["away"] or 0 for p in periods)
        home_sum = sum(p["home"] or 0 for p in periods)
        reconcile = (away_sum == away_score) and (home_sum == home_score)

    state = {
        "status": status,
        "status_detail": detail,
        "away_score": away_score,
        "home_score": home_score,
        "period_number": inning,
        "period_label": str(linescore.get("currentInningOrdinal") or "") or None,
        "period_state": str(linescore.get("inningState") or "") or None,
        "in_break": str(linescore.get("inningState") or "").lower() in {"middle", "end"},
        # Baseball has no clock and the feed offers none.
        "clock_seconds_remaining": None,
        "outs": _int(linescore.get("outs")) if status == "IN_PROGRESS" else None,
        "balls": _int(linescore.get("balls")) if status == "IN_PROGRESS" else None,
        "strikes": _int(linescore.get("strikes")) if status == "IN_PROGRESS" else None,
        "bases": bases,
        "periods": periods,
        "periods_complete": complete,
        "periods_reconcile": reconcile,
    }
    return {
        **state,
        "league_code": "MLB",
        "source": "MLB_STATS_API",
        "source_mode": "LIVE",
        "source_game_id": str(game.get("gamePk") or "") or None,
        # The schedule feed publishes no score timestamp. NULL is the honest
        # value and is what makes the UI say "last checked".
        "source_observed_at": None,
        "retrieved_at": moment,
        "checked_at": moment,
        "payload_checksum": _sha(state),
    }


def scoreboards_from_schedule(
    raw: dict[str, Any], *, retrieved_at: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    """Every game in one schedule response, keyed by its MLB gamePk."""

    out: dict[str, dict[str, Any]] = {}
    for date_node in (raw.get("dates") or []):
        for game in (date_node.get("games") or []):
            key = str(game.get("gamePk") or "")
            if not key:
                continue
            out[key] = scoreboard_from_schedule_game(game, retrieved_at=retrieved_at)
    return out


__all__ = [
    "classify",
    "periods_from",
    "scoreboard_from_schedule_game",
    "scoreboards_from_schedule",
]

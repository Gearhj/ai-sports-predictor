"""NFL scoreboard state from a SportsGameOdds event object.

WHAT THE EVENT ACTUALLY CARRIES
-------------------------------
Verified against a live response, not against documentation: the event has a
``status`` block (started / live / completed / ended / cancelled / delayed /
inBreak, plus ``currentPeriodID`` and the set of periods that started and
ended), ``teams.home/away.score``, and a ``results`` map keyed by period --
``1q``, ``2q``, ``3q``, ``4q``, then the ``game`` and ``reg`` aggregates -- each
side carrying ``points``. On the game checked, the four quarters summed exactly
to the aggregate and to the displayed score.

WHAT IT DOES NOT CARRY
----------------------
*   **A game clock.** There is no clock field anywhere in the event. So NFL
    shows the period and no time remaining, and the payload says the clock is
    unavailable rather than leaving a reader to assume it is zero.
*   **A score timestamp.** Nothing says when the score was true, so
    ``source_observed_at`` is NULL and freshness is expressed as the age of
    AISP's own check with source latency reported as unknown.

WHY THE BREAKDOWN IS FETCHED ONCE, AFTER THE GAME
-------------------------------------------------
Billing is per event object against a 2,500-object 30-day allowance with a
reserve that is never spent. Polling a thirteen-game Sunday for live scores
would consume the remaining allowance in a single afternoon and leave nothing
for the rest of the window. One object per completed game -- about sixteen a
week -- buys the real quarter-by-quarter breakdown and leaves the allowance
intact. The live NFL score is therefore not collected, and the surface says so
rather than showing a stale number as though it were current.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

#: Aggregates rather than periods. They are the checksum for the breakdown, not
#: rows in it.
AGGREGATE_PERIODS = {"game", "reg", "1h", "2h"}

#: Canonical order. Overtime periods sort after regulation by their number.
_ORDER = {"1q": 1, "2q": 2, "3q": 3, "4q": 4}


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _sha(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _period_sort_key(period_id: str) -> tuple[int, int]:
    if period_id in _ORDER:
        return (0, _ORDER[period_id])
    if period_id.endswith("ot"):
        # "1ot", "2ot"; anything unparseable sorts last rather than crashing.
        digits = "".join(ch for ch in period_id if ch.isdigit())
        return (1, int(digits) if digits else 99)
    return (2, 0)


def classify(status: dict[str, Any]) -> tuple[str, str | None]:
    """Canonical status, plus the provider's own display words."""

    detail = str(status.get("displayLong") or status.get("displayShort") or "") or None
    if status.get("cancelled"):
        return "CANCELLED", detail
    if status.get("completed") or status.get("ended") or status.get("finalized"):
        return "FINAL", detail
    if status.get("delayed"):
        return "DELAYED", detail
    if status.get("live"):
        return "IN_PROGRESS", detail
    if status.get("started"):
        # Started but not live and not ended: a break or a stoppage. It is NOT
        # a final -- a stopped clock is not a result.
        return "IN_PROGRESS", detail
    return "SCHEDULED", detail


def periods_from(event: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    """The per-period points, and whether every period that started is present.

    A period that has not started is absent, not zero. ``status.periods.started``
    is what says which ones exist, so a quarter the source simply failed to
    report is distinguishable from one that has not been played.
    """

    results = event.get("results") or {}
    status = event.get("status") or {}
    started = {
        str(period) for period in ((status.get("periods") or {}).get("started") or [])
        if str(period) not in AGGREGATE_PERIODS
    }

    rows: list[dict[str, Any]] = []
    for period_id in sorted(
            (p for p in results if p not in AGGREGATE_PERIODS), key=_period_sort_key):
        node = results.get(period_id) or {}
        if not isinstance(node, dict):
            continue
        away = node.get("away") if isinstance(node.get("away"), dict) else {}
        home = node.get("home") if isinstance(node.get("home"), dict) else {}
        rows.append({
            "number": _period_sort_key(period_id)[1],
            "label": period_id.upper(),
            "away": _int(away.get("points")),
            "home": _int(home.get("points")),
        })

    reported = {row["label"].lower() for row in rows}
    complete = bool(rows) and started.issubset(reported)
    return rows, complete


def scoreboard_from_event(
    event: dict[str, Any], *, retrieved_at: datetime | None = None,
) -> dict[str, Any]:
    """One scoreboard row's worth of fields from one SGO NFL event object."""

    moment = retrieved_at or datetime.now(UTC)
    status_node = event.get("status") or {}
    status, detail = classify(status_node)
    teams = event.get("teams") or {}
    away_score = _int((teams.get("away") or {}).get("score"))
    home_score = _int((teams.get("home") or {}).get("score"))

    periods, complete = periods_from(event)
    current = str(status_node.get("currentPeriodID") or "").strip() or None

    reconcile: bool | None = None
    if status == "FINAL" and complete and away_score is not None and home_score is not None:
        reconcile = (
            sum(row["away"] or 0 for row in periods) == away_score
            and sum(row["home"] or 0 for row in periods) == home_score
        )

    state = {
        "status": status,
        "status_detail": detail,
        "away_score": away_score,
        "home_score": home_score,
        "period_number": (_period_sort_key(current)[1] if current else None),
        "period_label": current.upper() if current else None,
        "period_state": None,
        "in_break": bool(status_node.get("inBreak")) if status_node else None,
        # No clock field exists in this payload. NULL, and the UI shows nothing.
        "clock_seconds_remaining": None,
        "outs": None,
        "balls": None,
        "strikes": None,
        "bases": None,
        "periods": periods,
        "periods_complete": complete,
        "periods_reconcile": reconcile,
    }
    return {
        **state,
        "league_code": "NFL",
        "source": "SPORTSGAMEODDS",
        "source_mode": "LIVE",
        "source_game_id": str(event.get("eventID") or "") or None,
        # The event object publishes no score timestamp.
        "source_observed_at": None,
        "retrieved_at": moment,
        "checked_at": moment,
        "payload_checksum": _sha(state),
    }


def team_abbreviations(event: dict[str, Any]) -> dict[str, str | None]:
    """Home/away short names, used only to verify orientation before storing."""

    teams = event.get("teams") or {}
    return {
        side: ((teams.get(side) or {}).get("names") or {}).get("short")
        for side in ("home", "away")
    }


__all__ = [
    "AGGREGATE_PERIODS",
    "classify",
    "periods_from",
    "scoreboard_from_event",
    "team_abbreviations",
]

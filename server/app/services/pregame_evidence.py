"""Concise, user-facing pregame evidence for one game evaluation.

Turns the governed StarterEvidence / LineupEvidence states into the compact
form the Daily Command Center and the V2.1 funnel display:

    Starter: PROBABLE · observed 42m before reference · FRESH
    Lineup:  NOT YET PUBLISHED

plus the exact starter block category used for funnel reconciliation. Pure
function of the evaluation — no session, no provider, no mutation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.evidence_states import (
    FRESHNESS_FRESH,
    FRESHNESS_INVALID,
    FRESHNESS_MISSING,
    FRESHNESS_STALE,
    LINEUP_SOURCE_NOT_YET_PUBLISHED,
    STARTER_CONFIRMED,
    STARTER_CONFLICT,
    STARTER_MISSING,
    STARTER_PROBABLE_STABLE,
)

# Exactly one category per game. Sums to the scheduled-game count.
STARTER_BLOCK_CATEGORIES = (
    "PASS",              # both sides CONFIRMED / PROBABLE_STABLE
    "STALE",             # a side's latest observation is older than the current-minutes limit at the reference
    "MISSING",           # no governed observation / no pitcher identity for a side
    "UNSTABLE",          # named pitcher but the stability contract is not met (streak, spacing, forecast currency)
    "CONFLICT",          # competing identities observed simultaneously
    "EVENT",             # unresolved material availability event / game started
    "NOT_EVALUATED",     # no forecast yet — the evaluation window has not opened
)

LINEUP_LABELS = {
    "EXPECTED_NOT_YET_PUBLISHED": "NOT YET PUBLISHED",
    "PARTIAL": "PARTIAL",
    "AVAILABLE": "PUBLISHED",
    "CONFIRMED": "CONFIRMED",
}


def _age_text(minutes: float | None) -> str:
    if minutes is None:
        return "no observation"
    m = int(round(minutes))
    if m < 0:
        return "future-dated"
    h, r = divmod(m, 60)
    return f"{h}h {r:02d}m" if h else f"{r}m"


def _starter_side(side: Any, cutoff: datetime, reference: datetime) -> dict[str, Any]:
    if side is None:
        return {"state": None, "status": None, "source": None, "observed_at": None,
                "age_minutes_at_reference": None, "freshness": FRESHNESS_MISSING,
                "label": "Starter: NOT EVALUATED", "reasons": []}
    status = (side.source_status or "UNKNOWN").upper()
    age = side.age_minutes_at_reference
    label = f"Starter: {status} · observed {_age_text(age)} before reference · {side.freshness}"
    return {
        "state": side.state, "status": status, "source": side.source,
        "observed_at": side.latest_observed_at, "age_minutes_at_reference": age,
        "freshness": side.freshness, "stable_since": side.stable_since,
        "label": label, "reasons": list(side.reasons),
    }


def _category(v2: Any) -> str:
    if v2.starter_home is None or v2.starter_away is None:
        return "NOT_EVALUATED"
    sides = (v2.starter_home, v2.starter_away)
    if all(s.eligible for s in sides):
        return "PASS"
    if any(s.state == STARTER_CONFLICT for s in sides):
        return "CONFLICT"
    if any(s.state == STARTER_MISSING for s in sides):
        return "MISSING"
    if any(s.freshness in (FRESHNESS_STALE, FRESHNESS_INVALID) for s in sides):
        return "STALE"
    if any(any("availability event" in r or "has started" in r for r in s.reasons) for s in sides):
        return "EVENT"
    return "UNSTABLE"


def pregame_evidence_summary(v2: Any, *, cutoff: datetime, reference: datetime) -> dict[str, Any]:
    lineup = v2.lineup
    if lineup is None:
        lineup_block: dict[str, Any] = {"state": None, "label": "Lineup: NOT EVALUATED",
                                        "home": None, "away": None, "source": None}
    else:
        def team(status_state: str, observed: str | None) -> dict[str, Any]:
            return {"source_state": status_state, "label": LINEUP_LABELS.get(status_state, status_state),
                    "observed_at": observed}
        home = team(lineup.home_source_state, lineup.home_observed_at)
        away = team(lineup.away_source_state, lineup.away_observed_at)
        both_unpublished = (lineup.home_source_state == LINEUP_SOURCE_NOT_YET_PUBLISHED
                            and lineup.away_source_state == LINEUP_SOURCE_NOT_YET_PUBLISHED)
        summary = "NOT YET PUBLISHED" if both_unpublished else f"home {home['label']} · away {away['label']}"
        lineup_block = {"state": lineup.state, "label": f"Lineup: {summary}", "home": home, "away": away,
                        "source": lineup.source, "reasons": list(lineup.reasons)}
    category = _category(v2)
    starter_home = _starter_side(v2.starter_home, cutoff, reference)
    starter_away = _starter_side(v2.starter_away, cutoff, reference)
    block_reason = None
    if category not in ("PASS", "NOT_EVALUATED"):
        offending = [s for s in (starter_home, starter_away) if s["state"] not in (STARTER_CONFIRMED, STARTER_PROBABLE_STABLE)]
        block_reason = "; ".join(r for s in offending for r in s["reasons"]) or category
    return {
        "reference_at": reference.isoformat(),
        "cutoff_at": cutoff.isoformat(),
        "reference_is_cutoff": reference >= cutoff,
        "starter_home": starter_home,
        "starter_away": starter_away,
        "starter_block_category": category,
        "starter_block_reason": block_reason,
        "lineup": lineup_block,
        # MLB's schedule feed never confirms a starter before first pitch;
        # PROBABLE is the terminal pregame source state (policy audit).
        "source_note": "MLB_STATS_API publishes probable starters only; a PROBABLE starter is the normal, terminal pregame state.",
        "any_fresh": any(s["freshness"] == FRESHNESS_FRESH for s in (starter_home, starter_away)),
    }


__all__ = ["STARTER_BLOCK_CATEGORIES", "pregame_evidence_summary"]

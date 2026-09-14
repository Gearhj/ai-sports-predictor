"""What each sport's score feed actually supplies, and what it does not.

This exists because the limits are the part a reader is most likely to be
misled about. "No clock shown" could mean the game is at 0:00, or that AISP is
broken, or that the source has never published one -- and only the third is
true for both current feeds. Stating it in the product, from the same constants
the collectors use, means the answer cannot drift away from the behaviour.

Every field here was verified against a real payload, not against a vendor
document. The measured cadence and delay come from the observations actually
stored, so a claim about freshness is a measurement rather than a promise.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.scoreboard import GameScoreboardObservation

SCHEMA_VERSION = "AISP_SCOREBOARD_COVERAGE_V1"

#: Per-sport coverage, as verified. `available` is what the payload carries;
#: `unavailable` is what it does not, each with the reason it does not.
COVERAGE: dict[str, dict[str, Any]] = {
    "MLB": {
        "source": "MLB Stats API",
        "source_key": "MLB_STATS_API",
        "cost": "Free public endpoint. No key, no metered allowance, no quota.",
        "live_score": True,
        "period_breakdown": True,
        "period_label": "innings, extra innings included",
        "situation": True,
        "clock": False,
        "source_score_timestamp": False,
        "player_stats": False,
        "target_cadence_seconds": 120,
        "cadence_note": (
            "One request covers the whole slate, every two minutes while any "
            "game is in its watch window. No request is made when nothing is."
        ),
        "available_fields": [
            "current score", "game status and the source's own status words",
            "current inning and half", "per-inning runs for both sides",
            "hits and errors", "outs", "balls and strikes", "base runners",
        ],
        "unavailable_fields": {
            "game clock": "Baseball has no game clock and the feed publishes none.",
            "score timestamp": (
                "The schedule feed carries no time at which the score was true, "
                "so AISP can report when it last checked but not when the score "
                "changed. Source latency is unknown."
            ),
            "player statistics": (
                "Available from this source's box-score endpoint, which is not "
                "collected for the scoreboard. Not shown rather than zeroed."
            ),
        },
    },
    "NFL": {
        "source": "nflverse schedules release (finals) + SportsGameOdds (breakdown)",
        "source_key": "SPORTSGAMEODDS",
        "cost": (
            "nflverse is free and unmetered. SportsGameOdds bills per event "
            "object against a 2,500-object rolling 30-day allowance with 400 "
            "held permanently in reserve."
        ),
        "live_score": False,
        "period_breakdown": True,
        "period_label": "quarters, overtime included",
        "situation": False,
        "clock": False,
        "source_score_timestamp": False,
        "player_stats": True,
        "target_cadence_seconds": 3600,
        "cadence_note": (
            "Final score hourly from the free release. The quarter breakdown is "
            "fetched once per completed game, one event object each. In-game "
            "score is NOT collected: polling a thirteen-game Sunday would spend "
            "the entire remaining allowance in one afternoon."
        ),
        "available_fields": [
            "final score", "game status flags (started, live, ended, delayed)",
            "which periods started and ended", "per-quarter points for both sides",
            "overtime periods", "stable player ids and per-player game statistics",
        ],
        "unavailable_fields": {
            "in-game score": (
                "Available from the provider but not collected: the remaining "
                "object allowance cannot sustain polling a full slate. The final "
                "is shown when the free feed publishes it."
            ),
            "game clock": (
                "The provider's event object carries no clock field at all, so "
                "no time remaining can be shown for any NFL game."
            ),
            "score timestamp": (
                "No time-of-score is published, so freshness is the age of "
                "AISP's own check and source latency is unknown."
            ),
            "per-period player statistics": (
                "Player statistics are published for the game as a whole only."
            ),
        },
    },
    "NBA": {
        "source": None,
        "source_key": None,
        "cost": None,
        "live_score": False,
        "period_breakdown": False,
        "period_label": "quarters and each overtime period",
        "situation": False,
        "clock": False,
        "source_score_timestamp": False,
        "player_stats": False,
        "target_cadence_seconds": None,
        "cadence_note": (
            "No NBA score feed is collected. The scoreboard handles NBA the "
            "moment one is: nothing in the storage, the read service or the "
            "display is baseball- or football-specific, and quarters plus each "
            "overtime period are the same period rows the other sports use."
        ),
        "available_fields": [],
        "unavailable_fields": {
            "everything": (
                "NBA scores are not collected. This is a source gap, not a "
                "model gap: the scoreboard does not require a forecasting model "
                "and would show NBA scores as soon as a feed is authorized."
            ),
        },
    },
}


def measured_freshness(
    session: Session, *, league: str, now: datetime | None = None,
) -> dict[str, Any]:
    """What the stored observations actually show, not what was intended.

    Measured over the last day of live play, because a cadence claim is only
    worth anything if the rows agree with it.
    """

    moment = now or datetime.now(UTC)
    since = moment - timedelta(days=1)
    row = session.execute(
        select(
            func.count(GameScoreboardObservation.id),
            func.max(GameScoreboardObservation.checked_at),
            func.count(GameScoreboardObservation.source_observed_at),
        ).where(
            GameScoreboardObservation.league_code == league,
            GameScoreboardObservation.checked_at >= since,
        )
    ).first()
    observations, latest, with_source_time = row or (0, None, 0)
    age = None
    if latest is not None:
        aware = latest if latest.tzinfo else latest.replace(tzinfo=UTC)
        age = round((moment - aware).total_seconds(), 1)
    return {
        "observations_last_24h": int(observations or 0),
        "last_checked_at": latest.isoformat() if latest else None,
        "last_check_age_seconds": age,
        "observations_with_source_timestamp": int(with_source_time or 0),
    }


def coverage_report(session: Session, *, now: datetime | None = None) -> dict[str, Any]:
    """Declared coverage beside measured behaviour, per sport."""

    moment = now or datetime.now(UTC)
    sports = {}
    for league, declared in COVERAGE.items():
        sports[league] = {
            **declared,
            "measured": measured_freshness(session, league=league, now=moment),
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": moment.isoformat(),
        "sports": sports,
        "note": (
            "Score collection does not depend on a forecast, a sportsbook quote "
            "or a qualifying opportunity. A game with none of those still gets "
            "its score."
        ),
    }


__all__ = ["COVERAGE", "SCHEMA_VERSION", "coverage_report", "measured_freshness"]

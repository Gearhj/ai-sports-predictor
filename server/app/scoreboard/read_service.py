"""The one scoreboard payload every surface renders.

WHY ONE SERVICE
---------------
Daily Decisions, the experiment's positions, a parlay ticket's legs, game
analysis and a historical wager all have to answer the same question: what is
the score, how far along is the game, and how much should I trust that. Five
implementations of that would drift, and they would drift in the direction of
looking more confident than the evidence is. So there is one.

FRESHNESS IS A FIRST-CLASS ANSWER, NOT A TIMESTAMP
--------------------------------------------------
Three facts are kept apart because they answer different questions:

*   ``source_observed_at`` -- when the source says the state held. NULL for both
    current sources. When it is NULL the payload says so explicitly and the UI
    is told to say "last checked", never "score as of".
*   ``retrieved_at`` -- when these exact bytes arrived.
*   ``checked_at`` -- the last successful check that CONFIRMED this state.

A page reload does not change any of them. If collection stops, ``checked_at``
stops advancing and the payload's own age grows, so the last known score stays
on screen with its real age attached and a DELAYED or UNAVAILABLE freshness
label -- rather than a fresh-looking number that is actually an hour old.

A FINAL HERE IS NOT A SETTLEMENT
--------------------------------
``status`` is what the score feed says. Whether a wager is settled is the
settlement owner's answer, and the two are reported separately and can disagree:
a game can read FINAL while confirmation is still pending, and that is a real
state a reader needs to see rather than a contradiction to hide.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable, Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Game, GameTeam, League, Team
from app.models.scoreboard import GameScoreboardObservation

SCHEMA_VERSION = "AISP_GAME_SCOREBOARD_V1"

#: Past these, a live game's evidence stops being presented as current. They are
#: generous multiples of the two-minute cadence: one missed cycle is not an
#: outage, and calling it one would train a reader to ignore the label.
FRESH_WITHIN = timedelta(minutes=6)
DELAYED_WITHIN = timedelta(minutes=30)

#: What "actually happening now" means for freshness. A final does not go stale:
#: a game that ended three hours ago is still over.
LIVE_STATUSES = {"IN_PROGRESS", "DELAYED", "SUSPENDED"}


@dataclass(frozen=True)
class Freshness:
    """How much a reader should trust this reading, and why."""

    state: str
    detail: str
    checked_age_seconds: float
    source_timestamp_available: bool


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def assess_freshness(
    row: GameScoreboardObservation, *, now: datetime | None = None,
) -> Freshness:
    """Freshness of the CHECK, never of an invented score timestamp."""

    moment = now or datetime.now(UTC)
    checked = _aware(row.checked_at) or moment
    age = max(0.0, (moment - checked).total_seconds())
    has_source_time = _aware(row.source_observed_at) is not None

    if row.status in {"FINAL", "POSTPONED", "CANCELLED"}:
        return Freshness(
            state="SETTLED_STATE",
            detail=(
                f"This game is {row.status.lower()}; the reading does not go "
                "stale. Last checked "
                f"{_ago(age)}."
            ),
            checked_age_seconds=age,
            source_timestamp_available=has_source_time,
        )

    if row.status not in LIVE_STATUSES:
        return Freshness(
            state="NOT_STARTED",
            detail=f"The game has not started. Last checked {_ago(age)}.",
            checked_age_seconds=age,
            source_timestamp_available=has_source_time,
        )

    if age <= FRESH_WITHIN.total_seconds():
        state, detail = "CURRENT", f"Checked {_ago(age)}."
    elif age <= DELAYED_WITHIN.total_seconds():
        state, detail = "DELAYED", (
            f"This is the last score AISP could confirm, {_ago(age)}. Updates "
            "are running behind; the game may have moved on."
        )
    else:
        state, detail = "UNAVAILABLE", (
            # "for 9.0 hours ago" -- _ago() already ends in "ago".
            f"No update has reached AISP since {_ago(age)}. The score below is "
            "the last one confirmed and is probably out of date."
        )
    return Freshness(
        state=state, detail=detail, checked_age_seconds=age,
        source_timestamp_available=has_source_time,
    )


def _ago(seconds: float) -> str:
    if seconds < 90:
        return f"{int(seconds)} seconds ago"
    minutes = seconds / 60.0
    if minutes < 90:
        return f"{int(round(minutes))} minutes ago"
    return f"{minutes / 60.0:.1f} hours ago"


def latest_for(
    session: Session, game_ids: Sequence[UUID],
) -> dict[UUID, GameScoreboardObservation]:
    """The most recently CONFIRMED state for each game.

    Ordered by check time, not by insertion: a re-confirmed older row is the
    current truth, and a newer row that has not been re-confirmed since is not.
    """

    if not game_ids:
        return {}
    ranked = (
        select(
            GameScoreboardObservation,
            func.row_number().over(
                partition_by=GameScoreboardObservation.game_id,
                order_by=(
                    GameScoreboardObservation.checked_at.desc(),
                    GameScoreboardObservation.retrieved_at.desc(),
                ),
            ).label("rank"),
        )
        .where(GameScoreboardObservation.game_id.in_(list(game_ids)))
        .subquery()
    )
    rows = session.scalars(
        select(GameScoreboardObservation)
        .join(ranked, ranked.c.id == GameScoreboardObservation.id)
        .where(ranked.c.rank == 1)
    ).all()
    return {row.game_id: row for row in rows}


def _teams(session: Session, game_ids: Sequence[UUID]) -> dict[UUID, dict[str, Any]]:
    """Home and away identity, so a score is never shown against the wrong club."""

    if not game_ids:
        return {}
    rows = session.execute(
        select(GameTeam.game_id, GameTeam.is_home, Team.abbreviation, Team.name)
        .join(Team, Team.id == GameTeam.team_id)
        .where(GameTeam.game_id.in_(list(game_ids)))
    ).all()
    out: dict[UUID, dict[str, Any]] = {}
    for game_id, is_home, abbreviation, name in rows:
        side = "home" if is_home else "away"
        out.setdefault(game_id, {})[side] = {
            "abbreviation": abbreviation, "name": name}
    return out


def _period_rows(row: GameScoreboardObservation) -> list[dict[str, Any]]:
    """The breakdown as stored, with absence preserved as absence."""

    return [
        {
            "number": entry.get("number"),
            "label": entry.get("label"),
            "away": entry.get("away"),
            "home": entry.get("home"),
            # An explicit flag beats making the UI infer meaning from a null.
            "away_played": entry.get("away") is not None,
            "home_played": entry.get("home") is not None,
        }
        for entry in (row.periods or [])
    ]


def _situation(row: GameScoreboardObservation) -> dict[str, Any] | None:
    """Baseball outs and base state, only where the source actually supplied it."""

    if row.outs is None and row.bases is None:
        return None
    bases = row.bases or {}
    occupied = [base for base in ("first", "second", "third") if bases.get(base)]
    return {
        "outs": row.outs,
        "balls": row.balls,
        "strikes": row.strikes,
        "bases_occupied": occupied,
        "runners": {base: bases.get(base) for base in ("first", "second", "third")},
    }


def scoreboard_for(
    session: Session, game_ids: Iterable[UUID], *, now: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    """The shared payload, keyed by game id as a string."""

    ids = [gid for gid in dict.fromkeys(game_ids)]
    if not ids:
        return {}
    moment = now or datetime.now(UTC)
    rows = latest_for(session, ids)
    teams = _teams(session, ids)
    leagues = dict(session.execute(
        select(Game.id, League.code).join(League, League.id == Game.league_id)
        .where(Game.id.in_(ids))
    ).all())

    governed = _governed_finals(session, ids)

    payload: dict[str, dict[str, Any]] = {}
    for game_id in ids:
        row = rows.get(game_id)
        identity = teams.get(game_id, {})
        final = governed.get(game_id)
        if row is None:
            payload[str(game_id)] = {
                "schema_version": SCHEMA_VERSION,
                "game_id": str(game_id),
                "league_code": leagues.get(game_id),
                "available": False,
                # "No score has been collected" was true of the scoreboard and
                # false of the game: DEN at KC was officially final 10-31 and
                # already settled while this line still said it. With a governed
                # final present the reason names what is actually missing.
                "unavailable_reason": (
                    "No live scoreboard was collected for this game; the official "
                    "final is shown from the governed record."
                    if final else "No score has been collected for this game yet."),
                "teams": identity or None,
                "governed_final": final,
            }
            continue

        freshness = assess_freshness(row, now=moment)
        periods = _period_rows(row)
        payload[str(game_id)] = {
            "schema_version": SCHEMA_VERSION,
            "game_id": str(game_id),
            "league_code": row.league_code,
            "available": True,
            "teams": identity or None,
            "status": row.status,
            "status_detail": row.status_detail,
            "is_final": row.status == "FINAL",
            "is_live": row.status in LIVE_STATUSES,
            "away_score": row.away_score,
            "home_score": row.home_score,
            "period": {
                "number": row.period_number,
                "label": row.period_label,
                "state": row.period_state,
                "in_break": row.in_break,
            },
            # NULL means the source publishes no clock. The UI must show nothing
            # rather than a plausible-looking countdown nobody measured.
            "clock_seconds_remaining": row.clock_seconds_remaining,
            "clock_available": row.clock_seconds_remaining is not None,
            "situation": _situation(row),
            "periods": periods,
            "periods_complete": row.periods_complete,
            "periods_reconcile": row.periods_reconcile,
            "periods_note": _periods_note(row, periods),
            "freshness": {
                "state": freshness.state,
                "detail": freshness.detail,
                "checked_age_seconds": round(freshness.checked_age_seconds, 1),
                "source_timestamp_available": freshness.source_timestamp_available,
                "source_latency": (
                    "unknown" if not freshness.source_timestamp_available else "known"),
            },
            "source": {
                "name": row.source,
                "mode": row.source_mode,
                "source_game_id": row.source_game_id,
                # Kept distinct on purpose. Relabelling retrieval as the score's
                # own time is the exact misstatement this payload prevents.
                "source_observed_at": _iso(row.source_observed_at),
                "retrieved_at": _iso(row.retrieved_at),
                "last_checked_at": _iso(row.checked_at),
                "timestamp_note": (
                    "This source publishes no score timestamp, so AISP can show "
                    "when it last checked but not when the score changed. Source "
                    "latency is unknown."
                    if row.source_observed_at is None else
                    "The source supplied its own observation time."
                ),
            },
            "settlement_note": (
                "This is the observed score. Whether a wager is settled is "
                "recorded separately and may still be pending."
            ),
            # Kept beside the observation, never merged into it. The scoreboard
            # can stop before a game ends -- SEA at LAA's last observation is the
            # 9th inning -- and relabelling that snapshot FINAL would be
            # inventing an observation. So the official result travels as its
            # own block, and `scoreboard_behind_final` says the two disagree.
            "governed_final": final,
            "scoreboard_behind_final": bool(final) and row.status != "FINAL",
        }
    return payload


def _governed_finals(session: Session, ids: Sequence[UUID]) -> dict[UUID, dict[str, Any]]:
    """The official final for each game that has one -- settlement's evidence.

    Read from the canonical game row and the final game-state observation both
    sports' final ingests write. Nothing here reads the scoreboard, so a
    scoreboard FINAL can never appear in this block by relabelling.
    """

    from app.models import GameStateObservation

    finals: dict[UUID, dict[str, Any]] = {}
    games = session.execute(
        select(Game.id, Game.status, Game.away_score, Game.home_score, Game.final_at)
        .where(Game.id.in_(ids))
    ).all()
    for game_id, status, away, home, final_at in games:
        label = status.value if hasattr(status, "value") else str(status)
        if label.upper() != "FINAL" or away is None or home is None:
            continue
        evidence = session.execute(
            select(GameStateObservation.id, GameStateObservation.source,
                   GameStateObservation.observed_at)
            .where(GameStateObservation.game_id == game_id,
                   GameStateObservation.status == "final")
            .order_by(GameStateObservation.observed_at)
            .limit(1)
        ).first()
        finals[game_id] = {
            "away_score": away,
            "home_score": home,
            # The event's own completion time as the finals path recorded it --
            # not when AISP retrieved it, and not when anything was settled.
            "final_at": _iso(final_at),
            "evidence_observation_id": str(evidence[0]) if evidence else None,
            "evidence_source": evidence[1] if evidence else None,
            "first_confirmed_at": _iso(evidence[2]) if evidence else None,
            "label": "Official final · governed record",
        }
    return finals


def _periods_note(
    row: GameScoreboardObservation, periods: list[dict[str, Any]],
) -> str | None:
    """What is missing from the breakdown, named rather than papered over."""

    if not periods:
        if row.status == "FINAL":
            return (
                "The final score is available; the period breakdown was not "
                "published for this game."
            )
        return None
    if not row.periods_complete:
        return (
            "Some periods were not reported by the source. The missing ones are "
            "shown as unavailable rather than filled in."
        )
    if row.periods_reconcile is False:
        return (
            "The reported period scores do not add up to the reported final. "
            "Both are shown as published; neither was adjusted to fit."
        )
    return None


def _iso(value: datetime | None) -> str | None:
    aware = _aware(value)
    return aware.isoformat() if aware else None


__all__ = [
    "DELAYED_WITHIN",
    "FRESH_WITHIN",
    "LIVE_STATUSES",
    "SCHEMA_VERSION",
    "Freshness",
    "assess_freshness",
    "latest_for",
    "scoreboard_for",
]

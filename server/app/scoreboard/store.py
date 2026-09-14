"""Writing observed scoreboard state without inflating the evidence stream.

ONE STATE, ONE ROW, A MOVING CHECK TIME
---------------------------------------
A two-minute poll over a three-hour game is ninety checks, and in most of them
nothing has changed. Writing ninety rows would make the table mostly noise and
would make "when did this score become true" unanswerable. So the checksum over
the state decides: a state AISP has already seen advances that row's
``checked_at`` and writes nothing else; a state it has not seen is a new row.

That is exactly the distinction the brief asks for. The reader can be told the
score is confirmed as of thirty seconds ago AND that it has stood since the
seventh inning, because both facts are stored separately.

A CORRECTION IS A NEW STATE, NOT AN EDIT
----------------------------------------
If a source revises a score, the checksum changes and a new row appears beside
the old one. Nothing is overwritten, so the earlier reading -- the one a reader
may have acted on -- is still there to be compared against.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.scoreboard import GameScoreboardObservation

#: Fields a caller may supply. Anything else is a programming error rather than
#: something to silently drop.
_FIELDS = {
    "league_code", "source", "source_mode", "source_game_id",
    "status", "status_detail", "home_score", "away_score",
    "period_number", "period_label", "period_state", "in_break",
    "clock_seconds_remaining", "outs", "balls", "strikes", "bases",
    "periods", "periods_complete", "periods_reconcile",
    "source_observed_at", "retrieved_at", "checked_at", "payload_checksum",
}


def _aware(value: datetime | None) -> datetime | None:
    """UTC-aware, whatever the backend handed back."""

    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def record_observation(
    session: Session, *, game_id: UUID, state: dict[str, Any],
    checked_at: datetime | None = None,
) -> tuple[GameScoreboardObservation, bool]:
    """Persist one observed state. Returns the row and whether it is new.

    Idempotent on (game, source, state checksum): re-observing a state AISP has
    already recorded moves its check time forward and adds no row.
    """

    unknown = set(state) - _FIELDS
    if unknown:
        raise ValueError(f"unknown scoreboard fields: {sorted(unknown)}")

    moment = checked_at or state.get("checked_at") or datetime.now(UTC)
    existing = session.scalar(
        select(GameScoreboardObservation).where(
            GameScoreboardObservation.game_id == game_id,
            GameScoreboardObservation.source == state["source"],
            GameScoreboardObservation.payload_checksum == state["payload_checksum"],
        )
    )
    if existing is not None:
        # The only mutation in this module, and it is about the check rather
        # than about the game: this state is still what the source reports.
        #
        # The stored value is normalised first. Some backends return naive
        # datetimes for a timezone-aware column, and comparing one against an
        # aware `moment` raises -- which would take down the collector on the
        # ordinary path where nothing has changed.
        current = _aware(existing.checked_at)
        if current is None or current < moment:
            existing.checked_at = moment
        return existing, False

    row = GameScoreboardObservation(
        game_id=game_id,
        **{key: value for key, value in state.items() if key != "checked_at"},
        checked_at=moment,
    )
    session.add(row)
    session.flush()
    return row, True


def record_many(
    session: Session, states: Iterable[tuple[UUID, dict[str, Any]]],
    *, checked_at: datetime | None = None,
) -> dict[str, int]:
    """Record a slate's worth of states; reports new rows against confirmations."""

    moment = checked_at or datetime.now(UTC)
    created = confirmed = 0
    for game_id, state in states:
        _, is_new = record_observation(
            session, game_id=game_id, state=state, checked_at=moment)
        if is_new:
            created += 1
        else:
            confirmed += 1
    return {"observations_written": created, "states_confirmed": confirmed}


__all__ = ["record_observation", "record_many"]

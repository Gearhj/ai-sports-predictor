"""Scoreboard state for games that finished before the collector existed.

WHY THIS IS FREE
----------------
Every MLB schedule response AISP has ever fetched was written to the raw
archive, linescore and all. Two thousand captures are sitting on disk with the
per-inning breakdown of every game of the last fortnight in them. So a completed
game's final and innings do not need a request -- they need a read.

That is also the right order of preference on principle: retained evidence
first, new collection only for what the retained evidence does not contain.

WHAT IT WILL NOT DO
-------------------
It writes the same append-only rows the live collector writes, through the same
store, so a backfilled state and a live-observed one are indistinguishable in
kind -- and both carry the retrieval time of the capture they came from, not the
time the backfill ran. Backdating a check would be the one genuinely dishonest
thing available here, and the check time is the capture's own.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExternalIdMapping, Game, League
from app.scoreboard.mlb import scoreboards_from_schedule
from app.scoreboard.store import record_observation

#: Captures are one JSON document per fetch, partitioned by source and UTC date.
MLB_ARCHIVE = "mlb_stats_api"


def _captures(root: Path, *, since: datetime) -> Iterable[tuple[datetime, dict[str, Any]]]:
    """Every retained MLB schedule capture at or after ``since``, oldest first.

    Ordered oldest-first so that when several captures describe one game, the
    newest state is recorded last and therefore reads as current.
    """

    base = Path(root) / MLB_ARCHIVE
    if not base.is_dir():
        return
    for path in sorted(base.glob("*/*/*/*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        stamp = _capture_time(document, path)
        if stamp is None or stamp < since:
            continue
        payload = document.get("payload")
        schedule = payload.get("schedule") if isinstance(payload, dict) else None
        if isinstance(schedule, dict) and schedule.get("dates"):
            yield stamp, schedule


def _capture_time(document: dict[str, Any], path: Path) -> datetime | None:
    """When the capture was taken, from the document or its own filename."""

    for key in ("ingested_at", "timestamp", "fetched_at", "retrieved_at"):
        value = document.get(key)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
            except ValueError:
                continue
    # The archive's own layout: <root>/<source>/YYYY/MM/DD/HHMMSS_micros_hex.json
    try:
        day = path.parent
        stamp = f"{day.parent.parent.name}-{day.parent.name}-{day.name}"
        clock = path.name.split("_")[0]
        return datetime.strptime(f"{stamp} {clock}", "%Y-%m-%d %H%M%S").replace(tzinfo=UTC)
    except (ValueError, IndexError):
        return None


def backfill_mlb_scoreboard(
    session: Session, *, raw_root: Path | str, days: int = 14,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Replay retained captures into scoreboard observations. No provider call."""

    moment = now or datetime.now(UTC)
    since = moment - timedelta(days=days)

    mapping = {
        str(external): game_id
        for external, game_id in session.execute(
            select(ExternalIdMapping.external_id, Game.id)
            .join(Game, Game.id == ExternalIdMapping.entity_id)
            .join(League, League.id == Game.league_id)
            .where(
                League.code == "MLB",
                ExternalIdMapping.entity_type == "game",
                ExternalIdMapping.source == "MLB_STATS_API",
                Game.starts_at >= since,
            )
        ).all()
    }
    if not mapping:
        return {"records": 0, "captures_read": 0, "reason": "NO_MLB_GAMES_IN_RANGE"}

    captures = written = seen = 0
    touched: set[UUID] = set()
    for stamp, schedule in _captures(Path(raw_root), since=since):
        captures += 1
        for source_id, state in scoreboards_from_schedule(
                schedule, retrieved_at=stamp).items():
            game_id = mapping.get(source_id)
            if game_id is None:
                continue
            seen += 1
            _, is_new = record_observation(
                session, game_id=game_id, state=state, checked_at=stamp)
            written += int(is_new)
            touched.add(game_id)
    session.commit()
    return {
        "records": written,
        "captures_read": captures,
        "states_seen": seen,
        "games_covered": len(touched),
        "provider_cost": "none (retained captures only)",
        "since": since.isoformat(),
    }


__all__ = ["backfill_mlb_scoreboard"]

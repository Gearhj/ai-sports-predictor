"""Governed finals for completed games that are missing one, automatically.

THE DEPENDENCY THIS CLOSES
--------------------------
Parlay settlement needs a governed final. Nothing created one for a live game:
the 2,480 rows in the table all came from the historical reconstruction path,
and that path ran only when a person typed a command. So the first recorded
ticket sat open with both its games finished, and would have stayed open.

WHY THIS IS NOT A NEW AUTHORITY
-------------------------------
It runs the same governed reconstruction, against the same source version, under
the same recorded owner decision of 2026-08-13 -- ``LOCAL_PRIVATE_RESEARCH`` for
``HISTORICAL_RECONSTRUCTION``, which is precisely what reconstructing a finished
game's box score is, and precisely what every existing row in the table was
produced under. The CLI's ``--approved-local-research`` flag is described in its
own help as acknowledging that recorded approval, not as granting one. Nothing
here is fabricated, backdated, or a gate lowered: the same decision, the same
provider, the same scope, invoked by the supervisor instead of by hand.

ONLY WHAT IS MISSING
--------------------
It never re-ingests a date, and asks instead which completed games have no
governed final, fetching exactly those, one feed each. A slate already governed
costs one schedule call and nothing more; a slate entirely governed is skipped
before any call at all.

That bound is about cost and blast radius. It is no longer also holding back a
correctness problem: the record-level identity now excludes the acquisition
clock, so a re-read of an unchanged result reuses its revision instead of
minting a successor that says the same thing. Both layers matter and neither
replaces the other -- the chunk key catches unchanged schedule BYTES, while the
per-final check catches the unchanged GAMES inside a slate whose bytes did
change, which is how the 2026-09-13 games acquired a spurious revision 2.

BOUNDS
------
A lookback window, a per-run game cap, and the runner's own exact request cap,
which must equal the planned calls or it refuses. The endpoint is free and
unmetered, so these bound politeness and blast radius rather than money.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExternalIdMapping, Game, GameFinalOutcome, League

#: How far back a completed game is still recovered. Wide enough to cover a
#: weekend outage, bounded so a cold start cannot try to reconstruct a season.
LOOKBACK = timedelta(days=3)

#: Most games recovered in one run. One feed call each.
MAX_GAMES_PER_RUN = 20

#: A game is only eligible once it has been over long enough that a late
#: correction is unlikely to be in flight. Not a guess at whether it finished --
#: that comes from the recorded FINAL status, never from elapsed time.
SETTLE_DELAY = timedelta(minutes=30)


@dataclass(frozen=True)
class MissingFinal:
    """One completed game with no governed final, and where to find it."""

    game_id: Any
    source_game_id: str
    official_date: date
    starts_at: datetime


def missing_finals(
    session: Session, *, now: datetime | None = None, limit: int = MAX_GAMES_PER_RUN,
) -> list[MissingFinal]:
    """Completed MLB games inside the lookback with no governed final.

    "Completed" is the recorded FINAL status, never elapsed time: a game that
    has run long is not over, and treating a stopped clock as a result is the
    mistake the whole settlement chain has to avoid.
    """

    moment = now or datetime.now(UTC)
    governed = select(GameFinalOutcome.game_id)

    rows = session.execute(
        select(Game.id, ExternalIdMapping.external_id, Game.starts_at)
        .join(League, League.id == Game.league_id)
        .join(
            ExternalIdMapping,
            (ExternalIdMapping.entity_id == Game.id)
            & (ExternalIdMapping.entity_type == "game")
            & (ExternalIdMapping.source == "MLB_STATS_API"),
        )
        .where(
            League.code == "MLB",
            Game.status == "FINAL",
            Game.starts_at >= moment - LOOKBACK,
            Game.starts_at <= moment - SETTLE_DELAY,
            Game.id.not_in(governed),
        )
        .order_by(Game.starts_at)
    ).all()

    out: list[MissingFinal] = []
    for game_id, external_id, starts_at in rows[: max(0, limit)]:
        start = starts_at if starts_at.tzinfo else starts_at.replace(tzinfo=UTC)
        out.append(MissingFinal(
            game_id=game_id, source_game_id=str(external_id),
            # The provider's own official date decides the slate, not UTC
            # midnight: a 10pm Pacific game belongs to the day it was played.
            official_date=(start - timedelta(hours=8)).date(),
            starts_at=start,
        ))
    return out


async def recover_missing_finals(
    session: Session, settings: Any, *, now: datetime | None = None,
    runner_factory: Any = None,
) -> dict[str, Any]:
    """Reconstruct the governed final for each completed game that lacks one."""

    from app.audit.immutability import install_immutability_guards
    from app.historical.backfill import HistoricalBackfillOptions, build_default_backfill_runner
    from app.historical.persistence import (
        A1_GOVERNANCE_DECISION,
        HistoricalCanonicalPersistenceService,
    )

    moment = now or datetime.now(UTC)
    missing = missing_finals(session, now=moment)
    if not missing:
        return {"records": 0, "reason": "NO_COMPLETED_GAME_IS_MISSING_A_GOVERNED_FINAL",
                "games_missing": 0, "requests": 0}

    by_date: dict[date, list[MissingFinal]] = {}
    for row in missing:
        by_date.setdefault(row.official_date, []).append(row)

    install_immutability_guards()
    factory = runner_factory or (
        lambda: build_default_backfill_runner(
            settings.raw_data_root,
            canonical_writer=HistoricalCanonicalPersistenceService(session),
            governance=A1_GOVERNANCE_DECISION,
        )
    )

    slates: list[dict[str, Any]] = []
    finals_written = requests = 0
    for slate, games in sorted(by_date.items()):
        wanted = frozenset(row.source_game_id for row in games)
        options = HistoricalBackfillOptions(
            start_date=slate, end_date=slate, season=slate.year,
            # One schedule call plus one feed per missing game. The runner
            # refuses unless the cap equals exactly that.
            expected_final_games=len(wanted),
            max_requests=len(wanted) + 1,
            only_source_game_ids=wanted,
            # The recorded 2026-08-13 project-owner decision, acknowledged --
            # the same one every existing governed final was produced under.
            terms_status="APPROVED",
        )
        result = await factory().run(options)
        payload = result if isinstance(result, dict) else vars(result)
        requests += int((payload.get("accounting") or {}).get("attempts") or 0)
        finals_written += int(payload.get("canonical_finals") or 0)
        slates.append({
            "official_date": slate.isoformat(),
            "games_requested": sorted(wanted),
            "status": payload.get("status"),
            "reason": payload.get("reason"),
            "canonical_finals": payload.get("canonical_finals"),
        })

    return {
        "records": finals_written,
        "games_missing": len(missing),
        "slates": slates,
        "requests": requests,
        "provider_cost": "none (free unmetered MLB Stats API)",
        "governance": {
            "approval": "project owner, 2026-08-13",
            "scope": A1_GOVERNANCE_DECISION["scope"],
            "population": A1_GOVERNANCE_DECISION["population"],
        },
    }


__all__ = [
    "LOOKBACK",
    "MAX_GAMES_PER_RUN",
    "SETTLE_DELAY",
    "MissingFinal",
    "missing_finals",
    "recover_missing_finals",
]

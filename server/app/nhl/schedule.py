"""NHL_GEN1_SCHEDULE_V1 (PROPOSED extension) -- schedule coverage that does not depend on forecasts.

WHY
---
A game must appear on the AISP schedule whether or not a forecast exists. AISP has
no admitted NHL schedule source and does not fetch one; the schedule therefore
arrives as a content-addressed exchange package, exactly like forecasts and
results, published by the producer from its own retained, authorised schedule
evidence (Model Studio already retains official NHL schedule metadata).

SHAPE (one object = one schedule snapshot)
------------------------------------------
``{schema: NHL_GEN1_SCHEDULE_V1, canonicalization, league: NHL, sport: HOCKEY,
   scheduleId: nhl-gen1-schedule:sha256:<hash of canonical doc minus scheduleId and publishedAtUtc>,
   season, seasonStartYear, coverage: {fromDate, toDate, complete},
   source: {source, observedAtUtc, retrievedAtUtc, evidenceRef: {path, sha256, bytes} | null},
   createdAtUtc, publishedAtUtc | null, researchStatus: RESEARCH_ONLY,
   games: [<the forecast contract's game block> + status: SCHEDULED | POSTPONED | CANCELLED]}``

Packaged under ``objects/sha256/<sha>/schedule.json`` and listed by a
``NHL_GEN1_SCHEDULE_MANIFEST_V1`` manifest; the current pointer gains an optional
``scheduleManifest`` binding.

WHAT AN IMPORT DOES, PER GAME (one receipt each)
------------------------------------------------
``IMPORTED``   game unknown to AISP: created (games/teams/game_teams + NHL id mappings).
``UPDATED``    game known: scheduled start or status changed (never once it is final).
``UNCHANGED``  game known and identical: no write.
``REJECTED``   orientation conflicts with the AISP game, or a team tri-code is held
               without an NHL binding. Nothing is written for that game.

A schedule never writes a result, a forecast, a price, a decision or a position.
"""

from __future__ import annotations

import copy
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import Game, GameStatus
from app.nhl import contract as c

SCHEDULE_SCHEMA = "NHL_GEN1_SCHEDULE_V1"
SCHEDULE_MANIFEST_SCHEMA = "NHL_GEN1_SCHEDULE_MANIFEST_V1"
SCHEDULE_ID_PREFIX = "nhl-gen1-schedule:sha256:"
SOURCE_MODE = "SCHEDULE_PACKAGE"
STATUSES = {"SCHEDULED": GameStatus.SCHEDULED, "POSTPONED": GameStatus.POSTPONED, "CANCELLED": GameStatus.CANCELED}
KEYS = frozenset({"schema", "canonicalization", "league", "sport", "scheduleId", "season", "seasonStartYear",
                  "coverage", "source", "createdAtUtc", "publishedAtUtc", "researchStatus", "games"})
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ID = re.compile(r"^nhl-gen1-schedule:sha256:[0-9a-f]{64}$")


def schedule_identity(document: dict[str, Any]) -> str:
    body = {k: v for k, v in document.items() if k not in ("scheduleId", "publishedAtUtc")}
    return SCHEDULE_ID_PREFIX + c.sha256_hex(c.canonical_bytes(body, trailing_newline=False))


def with_schedule_identity(document: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(document)
    out["scheduleId"] = schedule_identity(out)
    return out


def validate_schedule(document: Any) -> list[str]:
    problems: list[str] = []
    if not c._exact_keys(document, KEYS, "schedule", problems) and not isinstance(document, dict):
        return problems
    if document.get("schema") != SCHEDULE_SCHEMA:
        problems.append("SCHEMA_INVALID:schema")
    if document.get("canonicalization") != c.CANONICALIZATION or document.get("league") != "NHL" \
            or document.get("sport") != "HOCKEY":
        problems.append("SCHEMA_INVALID:canonicalization_league_sport")
    sid = document.get("scheduleId")
    if not isinstance(sid, str) or not _ID.match(sid) or sid != schedule_identity(document):
        problems.append("SCHEDULE_ID_NOT_CONTENT_HASH")
    if document.get("researchStatus") != "RESEARCH_ONLY":
        problems.append("RESEARCH_STATUS_INVALID")
    coverage = document.get("coverage")
    if c._exact_keys(coverage, {"fromDate", "toDate", "complete"}, "coverage", problems):
        if not (_DATE.match(str(coverage.get("fromDate"))) and _DATE.match(str(coverage.get("toDate")))) \
                or str(coverage["fromDate"]) > str(coverage["toDate"]) or not isinstance(coverage.get("complete"), bool):
            problems.append("SCHEMA_INVALID:coverage")
    source = document.get("source")
    if c._exact_keys(source, {"source", "observedAtUtc", "retrievedAtUtc", "evidenceRef"}, "source", problems):
        if not source.get("source") or c.parse_utc(source.get("retrievedAtUtc")) is None:
            problems.append("SCHEMA_INVALID:source")
        ref = source.get("evidenceRef")
        if ref is not None and not c._exact_keys(ref, {"path", "sha256", "bytes"}, "source.evidenceRef", problems):
            problems.append("SCHEMA_INVALID:source.evidenceRef")
    created = c.parse_utc(document.get("createdAtUtc"))
    if created is None:
        problems.append("CLOCK_ORDER:createdAtUtc")
    published = document.get("publishedAtUtc")
    if published is not None and (c.parse_utc(published) is None or (created and c.parse_utc(published) < created)):
        problems.append("CLOCK_ORDER:publishedAtUtc")
    games = document.get("games")
    if not isinstance(games, list) or not games:
        problems.append("SCHEMA_INVALID:games_empty")
        return list(dict.fromkeys(problems))
    seen: set[str] = set()
    for index, game in enumerate(games):
        label = f"games[{index}]"
        if not isinstance(game, dict):
            problems.append(f"SCHEMA_INVALID:{label}")
            continue
        status = game.get("status")
        if status not in STATUSES:
            problems.append(f"SCHEMA_INVALID:{label}.status")
        sub: list[str] = []
        c.check_game({k: v for k, v in game.items() if k != "status"}, sub)
        problems.extend(p.replace("game.", f"{label}.") for p in sub)
        ref = game.get("canonicalGameId")
        if ref in seen:
            problems.append(f"DUPLICATE_GAME:{ref}")
        seen.add(ref)
        if game.get("season") != document.get("season"):
            problems.append(f"CANONICAL_ID_MISMATCH:{label}.season_differs_from_schedule")
    return list(dict.fromkeys(problems))


def apply_game(session: Session, game: dict[str, Any]) -> tuple[str, list[str]]:
    """Admit or update one scheduled game. Returns (disposition, notes)."""

    from app.nhl.gen1_import import bind_game
    from app.nhl.identity import ensure_game

    game_block = {k: v for k, v in game.items() if k != "status"}
    game_id, orientation = bind_game(session, game["canonicalGameId"], game["home"]["canonicalTeamId"],
                                     game["away"]["canonicalTeamId"])
    if orientation:
        return "REJECTED", orientation
    status = STATUSES[game["status"]]
    start = c.parse_utc(game["scheduledStartUtc"])
    if game_id is None:
        try:
            game_id = ensure_game(session, game_block, source_mode=SOURCE_MODE)
        except ValueError as exc:
            return "REJECTED", [str(exc)]
        row = session.get(Game, game_id)
        if row is not None and status != GameStatus.SCHEDULED:
            row.status = status
        return "IMPORTED", []
    row = session.get(Game, game_id)
    if row is None:
        return "REJECTED", ["GAME_MAPPING_DANGLING"]
    if row.status == GameStatus.FINAL:
        return "UNCHANGED", ["GAME_FINAL_SCHEDULE_IGNORED"]
    notes: list[str] = []
    held = row.starts_at if row.starts_at.tzinfo else row.starts_at.replace(tzinfo=UTC)
    if held != start:
        notes.append(f"START_MOVED:{held.isoformat()}->{start.isoformat()}")
        row.starts_at = start
    if row.status != status:
        notes.append(f"STATUS:{row.status.value}->{status.value}")
        row.status = status
    return ("UPDATED", notes) if notes else ("UNCHANGED", [])


def import_schedule_document(session: Session, document: dict[str, Any], *, receipt, now: datetime | None = None) -> dict[str, int]:
    """Validate and apply one schedule document. ``receipt(disposition, game_ref, notes)`` records each outcome."""

    counts = {"IMPORTED": 0, "UPDATED": 0, "UNCHANGED": 0, "REJECTED": 0}
    problems = validate_schedule(document)
    if problems:
        receipt("REJECTED", None, problems)
        counts["REJECTED"] += 1
        return counts
    for game in document["games"]:
        disposition, notes = apply_game(session, game)
        receipt(disposition, game["canonicalGameId"], notes)
        counts[disposition] += 1
    session.flush()
    return counts


__all__ = ["SCHEDULE_MANIFEST_SCHEMA", "SCHEDULE_SCHEMA", "SOURCE_MODE", "apply_game", "import_schedule_document",
           "schedule_identity", "validate_schedule", "with_schedule_identity"]

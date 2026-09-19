"""Retained ESPN-derived NBA schedule objects -- the only place NBA finals are read from.

WHAT THESE ARE
--------------
Model Studio captured SportsDataverse/hoopR ESPN season exports (CC BY 4.0) under
its own reviewed acquisition receipts and stores each parquet at a content
address (``.../source/sha256/<sha256>/nba_schedule_<season>.parquet``):

* ``nba_schedule_2026.parquet`` (``b3884aa4...``), acquired 2026-09-14T12:52:46Z:
  the completed 2025-26 season, finals included.
* ``nba_schedule_2027.parquet`` (``d4ffe0d9...``), captured 2026-09-14T13:04:43Z:
  the 2026-27 schedule. The production forecast names this object in its own
  ``source_revision``. No 2026-27 game has a final in it yet.

AISP makes NO request for these. ``NBA_FINAL_SOURCE_OBJECTS`` names each object
as ``<parquet path>|<sha256>[|<receipt path>|<receipt sha256>]``. The object must
hash to its SHA-256 (and sit under a directory of that name); the optional
receipt must hash to its own pin and name the object's SHA-256, and supplies the
ACQUISITION clock. That clock is when the bytes were captured -- never a
completion time, which the export does not carry.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field
from datetime import datetime  # noqa: F401 -- used in annotations
from typing import Any

from app.nba import gen1_common as common

SOURCE = "ESPN_SPORTSDATAVERSE"
COMPLETION_CLOCK_ABSENT = "ABSENT_FROM_RETAINED_SOURCE_SCHEMA"
COLUMNS = ("game_id", "season", "season_type", "type_abbreviation", "start_date", "neutral_site",
           "status_type_name", "status_type_completed", "status_period", "home_id", "away_id",
           "home_abbreviation", "away_abbreviation", "home_display_name", "away_display_name",
           "home_score", "away_score")


@dataclass
class SourceObject:
    path: pathlib.Path
    sha256: str
    bytes: int
    captured_at: datetime | None
    capture_clock_status: str
    receipt_sha256: str | None
    rows: dict[str, dict[str, Any]] = field(default_factory=dict)

    def describe(self) -> dict[str, Any]:
        return {"path": str(self.path), "sha256": self.sha256, "bytes": self.bytes,
                "captured_at_utc": common.iso(self.captured_at), "capture_clock_status": self.capture_clock_status,
                "receipt_sha256": self.receipt_sha256, "rows": len(self.rows)}


def _read_rows(path: pathlib.Path) -> dict[str, dict[str, Any]]:
    import polars as pl

    frame = pl.read_parquet(path)
    missing = [c for c in COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"source object lacks columns {missing}")
    out: dict[str, dict[str, Any]] = {}
    for row in frame.select(list(COLUMNS)).iter_rows(named=True):
        key = str(row["game_id"])
        if key in out:
            raise ValueError(f"source object lists ESPN game {key} twice")
        out[key] = {**row, "game_id": key, "home_id": str(row["home_id"]), "away_id": str(row["away_id"])}
    return out


def load_objects(settings: Any) -> tuple[list[SourceObject], list[str]]:
    """Every configured object, verified; problems for any that are not."""

    objects: list[SourceObject] = []
    problems: list[str] = []
    for raw in str(getattr(settings, "nba_final_source_objects", "") or "").split(";"):
        entry = raw.strip()
        if not entry:
            continue
        parts = [p.strip() for p in entry.split("|")]
        if len(parts) not in (2, 4):
            problems.append(f"MALFORMED_ENTRY:{entry[:80]}")
            continue
        path, sha = pathlib.Path(parts[0]), parts[1].lower()
        if not path.is_file():
            problems.append(f"OBJECT_MISSING:{path}")
            continue
        body = path.read_bytes()
        if common.sha256_bytes(body) != sha:
            problems.append(f"OBJECT_SHA256_MISMATCH:{path.name}")
            continue
        if path.parent.name.lower() != sha:
            problems.append(f"OBJECT_NOT_CONTENT_ADDRESSED:{path.name}")
            continue
        captured, status, receipt_sha = None, "ACQUISITION_CLOCK_NOT_BOUND", None
        if len(parts) == 4:
            receipt_path, receipt_sha = pathlib.Path(parts[2]), parts[3].lower()
            try:
                receipt_raw = receipt_path.read_bytes()
            except OSError:
                problems.append(f"RECEIPT_MISSING:{receipt_path}")
                continue
            if common.sha256_bytes(receipt_raw) != receipt_sha:
                problems.append(f"RECEIPT_SHA256_MISMATCH:{receipt_path.name}")
                continue
            if sha not in receipt_raw.decode("utf-8", errors="replace"):
                problems.append(f"RECEIPT_DOES_NOT_NAME_OBJECT:{receipt_path.name}")
                continue
            receipt = json.loads(receipt_raw)
            captured = common.ts(receipt.get("acquiredAtUtc") or receipt.get("capturedAtUtc"))
            status = "ACQUISITION_CLOCK_FROM_BOUND_RECEIPT" if captured else "RECEIPT_HAS_NO_CLOCK"
        try:
            rows = _read_rows(path)
        except Exception as exc:  # noqa: BLE001 -- an unreadable object is a problem, not a crash
            problems.append(f"OBJECT_UNREADABLE:{path.name}:{type(exc).__name__}:{exc}"[:200])
            continue
        objects.append(SourceObject(path=path, sha256=sha, bytes=len(body), captured_at=captured,
                                    capture_clock_status=status, receipt_sha256=receipt_sha, rows=rows))
    return objects, problems


def row_for(objects: list[SourceObject], espn_game_id: str) -> tuple[SourceObject | None, dict[str, Any] | None]:
    """The newest-captured object holding this ESPN game id (by acquisition clock)."""

    holding = [(obj, obj.rows[str(espn_game_id)]) for obj in objects if str(espn_game_id) in obj.rows]
    if not holding:
        return None, None
    holding.sort(key=lambda pair: (pair[0].captured_at is not None,
                                   pair[0].captured_at.timestamp() if pair[0].captured_at else 0.0))
    return holding[-1]


def final_facts(row: dict[str, Any]) -> dict[str, Any] | None:
    """The row's final, or None unless the source literally says it is a completed final."""

    if row.get("status_type_completed") is not True or row.get("status_type_name") != "STATUS_FINAL":
        return None
    if row.get("home_score") is None or row.get("away_score") is None:
        return None
    periods = int(row["status_period"]) if row.get("status_period") is not None else None
    return {"source": SOURCE, "sourceGameId": str(row["game_id"]), "homeEspnTeamId": str(row["home_id"]),
            "awayEspnTeamId": str(row["away_id"]), "homePoints": int(row["home_score"]),
            "awayPoints": int(row["away_score"]), "completed": True, "periods": periods,
            "overtime": (periods > 4) if periods is not None else None}


__all__ = ["COMPLETION_CLOCK_ABSENT", "SOURCE", "SourceObject", "final_facts", "load_objects", "row_for"]

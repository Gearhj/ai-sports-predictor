"""Codex's NBA injury FEATURES as a nonfunding research receipt on every NBA decision (owner via Codex, 2026-10-27).

``codex_injury_v1.py`` is a byte-identical copy of the producer's ``source/server/sports_nba_gen1/injury.py`` from
asp-model-exchange/injury-availability-evaluation-20261027-v1 (sha256 pinned below; a mismatch makes the receipt
UNKNOWN). This module only builds that function's inputs from AISP's own retained ESPN files -- no new request,
parser or importer -- and returns its output with the input basis beside it.

It has NO wagering authority: nothing on the funding path reads ``injury_features`` (the owner star-out gate in
``availability.py`` decides record-only on its own receipt). Any failure is an UNKNOWN receipt, never an exception.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PRODUCER_SHA256 = "cc3a8ba5f9df16bfcd75beaf5de83f27620e4357741e07204fb2efdc5dd55060"
PRODUCER_PATH = Path(__file__).with_name("codex_injury_v1.py")
PACKET = "injury-availability-evaluation-20261027-v1"


def _held(path: Path, doc: dict[str, Any]) -> tuple[str, str]:
    """When AISP first held this file: the stamped field, else the write-once file's own write time."""
    if doc.get("held_at_utc"):
        return str(doc["held_at_utc"]), "STAMPED_AT_WRITE"
    return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(), "WRITE_ONCE_FILE_TIME"


def _inputs(root: Path, espn_game_id: str) -> tuple[list[dict], dict[str, dict], dict[str, dict], dict[str, Any]]:
    snaps = []
    for p in sorted((root / "snapshots" / espn_game_id).glob("*.json")):
        raw = p.read_bytes()
        d = json.loads(raw)
        d["source_sha256"] = hashlib.sha256(raw).hexdigest()
        snaps.append(d)
    teams = [str(t.get("espn_team_id")) for t in ((snaps[-1].get("teams") or {}).values() if snaps else []) if t.get("espn_team_id")]
    schedules, bases = {}, {}
    for tid in teams:
        files = sorted((root / "schedules" / tid).glob("*.json"))
        if not files:
            continue
        doc = json.loads(files[-1].read_bytes())
        doc["held_at_utc"], bases[f"schedule:{tid}"] = _held(files[-1], doc)
        doc.setdefault("season_type", 2)  # the collector reads seasontype=2 (regular season) only
        schedules[tid] = doc
    boxes, box_basis = {}, set()
    for p in (root / "box").glob("*.json"):
        doc = json.loads(p.read_bytes())
        doc["held_at_utc"], basis = _held(p, doc)
        box_basis.add(basis)
        boxes[str(doc.get("espn_event_id"))] = doc
    return snaps, schedules, boxes, {"schedules": bases, "boxes": sorted(box_basis), "snapshots": len(snaps), "box_count": len(boxes)}


def features(game: dict[str, Any], moment: datetime, root: Path) -> dict[str, Any]:
    """The producer's NBA_INJURY_FEATURES_V1 for one game at ``moment``, or an UNKNOWN receipt saying why."""
    base = {"schema": "AISP_NBA_INJURY_RESEARCH_RECEIPT_V1", "packet": PACKET, "funding_authority": False,
            "evaluated_at_utc": moment.astimezone(UTC).isoformat()}
    try:
        actual = hashlib.sha256(PRODUCER_PATH.read_bytes()).hexdigest()
        if actual != PRODUCER_SHA256:
            return {**base, "state": "UNKNOWN", "reason": "PRODUCER_HASH_MISMATCH", "producer_sha256": actual}
        from app.nba import codex_injury_v1 as producer

        eid = str(game.get("espn_game_id") or "")
        snaps, schedules, boxes, basis = _inputs(root, eid)
        if not snaps:
            return {**base, "state": "UNKNOWN", "reason": "NO_SNAPSHOT_HELD", "producer_sha256": actual}
        teams = {side: {"espn_team_id": str(((snaps[-1].get("teams") or {}).get(side) or {}).get("espn_team_id"))} for side in ("home", "away")}
        starts = game.get("starts_at")
        starts_iso = starts.astimezone(UTC).isoformat() if isinstance(starts, datetime) else str(starts)
        out = producer.assess_supplied_game({"espn_event_id": eid, "starts_at": starts_iso, "teams": teams},
                                            schedules, boxes, snaps, as_of=moment.astimezone(UTC).isoformat())
        return {**base, "state": out.get("state"), "producer_sha256": actual, "input_basis": basis, "features": out}
    except Exception as error:  # noqa: BLE001 - research only: an unreadable input is UNKNOWN, never a failed decision
        return {**base, "state": "UNKNOWN", "reason": f"FEATURES_FAILED:{type(error).__name__}:{str(error)[:160]}"}

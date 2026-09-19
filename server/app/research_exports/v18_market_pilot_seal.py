"""Write-once sealing of V18 market-pilot packets, and the scheduled tick.

WHAT IS WRITTEN
---------------
For each game whose research lock (T-15) falls in the frozen enrollment window
[2026-09-19 04:00Z, 2026-09-28 04:00Z), one packet is sealed between T-15 and
T-5 (and before first pitch) into ``settings.v18_market_pilot_packet_dir``:

* ``<episodeKey>.evidence-<sha16>.json`` -- the retained source rows the packet
  refs point into (identity columns only; no outcome column);
* ``<episodeKey>.json`` -- the packet (PACKET_SCHEMA.json, frozen);
* ``<episodeKey>.sha256`` -- sha256sum lines for both files;
* ``attempt-ledger.jsonl`` -- append-only, hash-chained: the attempt universe
  as first seen, every attempt, every seal, every refusal and every miss.

Every file is created exclusively and made read-only; nothing is ever
overwritten, replaced, backfilled or backdated. A game whose seal deadline has
passed is recorded as missed, never sealed. A late-discovered game is a
coverage exclusion, not an enrollment.

WHAT IT MAY NOT DO
------------------
It reads the database through the orchestrator's job-local session and never
adds, flushes or commits a row of its own (a dirty session is refused). It
never calls a provider and never touches a decision or position.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import logger
from app.models import Game, GameStatus, JobRun
from app.research_exports import v18_market_pilot as pilot

LEDGER_NAME = "attempt-ledger.jsonl"
JOB_NAME = "seal-v18-market-pilot-packets"
SCHEDULER_ID = "asp-rx-v18-market-pilot-seal"
#: How far ahead the attempt universe is recorded (the next slate).
UNIVERSE_HORIZON = timedelta(hours=36)
#: A freeze run still in flight at the lock is waited for, but never past this
#: much before the seal deadline (the sealer ticks every one or two minutes).
LAST_CHANCE_BEFORE_DEADLINE = timedelta(seconds=150)


class SealRefused(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------
def write_once(path: Path, data: bytes) -> None:
    """Create ``path`` with ``data`` exactly once; never overwrite; read-only after."""

    if path.exists():
        raise FileExistsError(str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(temp, "xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temp, path)  # atomic, and fails if the target exists
        except FileExistsError:
            raise
        except OSError:
            with open(path, "xb") as stream:  # exclusive create fallback
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
    finally:
        try:
            os.chmod(temp, stat.S_IWRITE | stat.S_IREAD)
            temp.unlink()
        except OSError:
            pass
    try:
        os.chmod(path, stat.S_IREAD)
    except OSError:
        pass


class AttemptLedger:
    """Append-only, hash-chained JSONL. One writer: the scheduler owner's lane."""

    def __init__(self, directory: Path, name: str = LEDGER_NAME) -> None:
        self.directory = Path(directory)
        self.path = self.directory / name

    def entries(self) -> list[dict[str, Any]]:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return []
        return [json.loads(line) for line in raw.splitlines() if line.strip()]

    def append(self, record: dict[str, Any], *, now: datetime) -> dict[str, Any]:
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            raw = b""
        lines = [line for line in raw.splitlines() if line.strip()]
        previous = hashlib.sha256(lines[-1]).hexdigest() if lines else None
        entry = {"seq": len(lines) + 1, "prevLineSha256": previous, "recordedAtUtc": pilot.iso(now),
                 "protocolSha256": pilot.PROTOCOL_SHA256, **record}
        pilot.assert_no_outcome_fields(entry, "attempt-ledger")
        line = pilot.canonical_bytes(entry)
        if raw and not raw.endswith(b"\n"):
            line = b"\n" + line
        with open(self.path, "ab") as stream:
            stream.write(line + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        body = line.lstrip(b"\n")
        return {"path": LEDGER_NAME if self.path.name == LEDGER_NAME else self.path.name,
                "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body),
                "locator": f"{self.path.name}#seq={entry['seq']}"}


def packet_path(directory: Path, key: str) -> Path:
    return Path(directory) / f"{key}.json"


# ---------------------------------------------------------------------------
# Sealing one game
# ---------------------------------------------------------------------------
@dataclass
class SealOutcome:
    episode_key: str
    sealed: bool
    code: str
    packet_sha256: str | None = None
    evidence_name: str | None = None
    detail: str = ""


def seal_game(
    session: Session,
    game: pilot.GameIdentity,
    *,
    inputs: pilot.BuildInputs,
    directory: Path,
    clock: Callable[[], datetime],
    ledger: AttemptLedger | None = None,
) -> SealOutcome:
    """Build and seal one packet, or refuse by name. Never overwrites."""

    directory = Path(directory)
    ledger = ledger or AttemptLedger(directory)
    start = pilot.aware(game.starts_at)
    lock = pilot.research_lock(start)
    deadline = pilot.seal_deadline(start)
    game_pk = _game_pk(session, game.id)
    key = pilot.episode_key(game.id, game_pk, lock)
    now = pilot.aware(clock())

    def refuse(code: str, detail: str = "") -> SealOutcome:
        ledger.append({"kind": "SEAL_REFUSED", "episodeKey": key, "code": code, "detail": detail,
                       "canonicalGameId": game.id, "researchLockUtc": pilot.iso(lock),
                       "sealDeadlineUtc": pilot.iso(deadline)}, now=pilot.aware(clock()))
        return SealOutcome(key, False, code, detail=detail)

    if now < lock:
        return SealOutcome(key, False, "NOT_YET_LOCKED")
    if packet_path(directory, key).exists():
        return refuse("ALREADY_SEALED_NO_OVERWRITE")
    if now > deadline:
        return refuse("AFTER_SEAL_DEADLINE", f"now {pilot.iso(now)} > T-5 {pilot.iso(deadline)}")
    if now >= start:
        return refuse("AT_OR_AFTER_SCHEDULED_START")
    if not pilot.in_enrollment_window(lock):
        return refuse("LOCK_OUTSIDE_ENROLLMENT_WINDOW")

    attempt_ref = ledger.append({
        "kind": "SEAL_ATTEMPT", "episodeKey": key, "canonicalGameId": game.id, "mlbGamePk": game_pk,
        "scheduledStartUtc": pilot.iso(start), "researchLockUtc": pilot.iso(lock),
        "sealDeadlineUtc": pilot.iso(deadline), "mode": inputs.mode,
    }, now=now)
    dirty_before = (len(session.new), len(session.dirty), len(session.deleted))
    build = pilot.build_packet(session, game, inputs=inputs, built_at=now, attempt_ref=attempt_ref)
    if (len(session.new), len(session.dirty), len(session.deleted)) != dirty_before:
        raise RuntimeError("The packet builder modified the session; refusing to seal")
    if build.schema_errors:
        return refuse("SCHEMA_INVALID", "; ".join(build.schema_errors[:10]))
    if build.episode_key != key:
        return refuse("EPISODE_KEY_MISMATCH", f"{build.episode_key} != {key}")
    if pilot.aware(clock()) > deadline:
        return refuse("SEAL_DEADLINE_PASSED_DURING_BUILD")

    evidence_path = directory / build.evidence_name
    try:
        write_once(evidence_path, build.evidence_bytes)
    except FileExistsError:
        if evidence_path.read_bytes() != build.evidence_bytes:
            return refuse("EVIDENCE_NAME_COLLISION_WITH_DIFFERENT_BYTES")
    try:
        write_once(packet_path(directory, key), build.packet_bytes)
    except FileExistsError:
        return refuse("ALREADY_SEALED_NO_OVERWRITE")
    packet_sha = hashlib.sha256(build.packet_bytes).hexdigest()
    evidence_sha = hashlib.sha256(build.evidence_bytes).hexdigest()
    sums = f"{packet_sha}  {key}.json\n{evidence_sha}  {build.evidence_name}\n".encode("ascii")
    try:
        write_once(directory / f"{key}.sha256", sums)
    except FileExistsError:
        return refuse("SHA256_FILE_ALREADY_EXISTS")
    written = pilot.aware(clock())
    late = written > deadline or written >= start
    ledger.append({
        "kind": "SEALED_LATE_NOT_PROSPECTIVE" if late else "SEALED",
        "episodeKey": key, "packetSha256": packet_sha, "packetBytes": len(build.packet_bytes),
        "evidenceFile": build.evidence_name, "evidenceSha256": evidence_sha,
        "sealedAtUtc": build.packet["sealedAtUtc"], "writtenAtUtc": pilot.iso(written),
        "sealDeadlineUtc": pilot.iso(deadline), "validator": build.validator,
        "anchorKind": build.packet["anchor"]["kind"],
        "selection": build.packet["selection"]["statusAtResearchLock"],
        "marketStatus": build.packet["market"]["status"],
        "firstFailure": build.packet["eligibility"]["firstFailure"],
    }, now=written)
    return SealOutcome(key, not late, "SEALED_LATE_NOT_PROSPECTIVE" if late else "SEALED",
                       packet_sha256=packet_sha, evidence_name=build.evidence_name)


def _game_pk(session: Session, game_id: str) -> str | None:
    from app.models import ExternalIdMapping

    values = session.scalars(
        select(ExternalIdMapping.external_id).where(
            ExternalIdMapping.entity_type == "game", ExternalIdMapping.source == "MLB_STATS_API",
            ExternalIdMapping.entity_id == pilot._uuid(game_id))
    ).all()
    return values[0] if len(values) == 1 else None


# ---------------------------------------------------------------------------
# The scheduled tick
# ---------------------------------------------------------------------------
def build_inputs(settings: Settings) -> pilot.BuildInputs:
    protocol = pilot.load_frozen_protocol(settings.v18_market_pilot_protocol_dir or None)
    def _root(value: str | None) -> Path | None:
        return Path(value) if value else None

    return pilot.BuildInputs(
        protocol=protocol,
        quarantine_ledger_path=Path(settings.v18_market_pilot_quarantine_ledger_path),
        raw_data_root=Path(settings.raw_data_root),
        mode=pilot.MODE_PROSPECTIVE,
        # Model Studio's already-readable originals for the SAME bytes the
        # ACL-restricted exchange package holds. Read-only, and only ever used
        # when the physical digest and length equal the bound identity.
        canonical_object_root=_root(settings.v18_market_pilot_canonical_object_root),
        canonical_handoff_root=_root(settings.v18_market_pilot_canonical_handoff_root),
        publication_receipt_root=_root(settings.v18_market_pilot_publication_receipt_root),
    )


def _window_games(session: Session, now: datetime) -> list[pilot.GameIdentity]:
    start = max(pilot.WINDOW_OPEN_UTC, now - timedelta(minutes=30))
    end = min(pilot.WINDOW_CLOSE_UTC, now + UNIVERSE_HORIZON)
    if end <= start:
        return []
    return pilot.mlb_games_with_locks_between(session, start, end)


def _freeze_in_flight(session: Session, lock: datetime) -> bool:
    return session.scalar(
        select(JobRun.id).where(JobRun.job_name == pilot.FREEZE_JOB, JobRun.started_at <= lock,
                                JobRun.started_at >= lock - timedelta(minutes=40),
                                JobRun.completed_at.is_(None)).limit(1)
    ) is not None


def work_due(session: Session, settings: Settings, *, now: datetime | None = None) -> bool:
    """Is there a universe addition or a seal to make? Read-only, no job row."""

    if not settings.v18_market_pilot_export_enabled:
        return False
    now = pilot.aware(now or datetime.now(UTC))
    directory = Path(settings.v18_market_pilot_packet_dir)
    ledger = AttemptLedger(directory)
    seen = {e.get("episodeKey") for e in ledger.entries() if e.get("kind") == "UNIVERSE_FIRST_SEEN"}
    done = {e.get("episodeKey") for e in ledger.entries()
            if e.get("kind") in ("SEALED", "SEALED_LATE_NOT_PROSPECTIVE", "MISSED_SEAL_DEADLINE")}
    for game in _window_games(session, now):
        lock = pilot.research_lock(game.starts_at)
        key = pilot.episode_key(game.id, _game_pk(session, game.id), lock)
        if key not in seen:
            return True
        if key not in done and lock <= now and not packet_path(directory, key).exists():
            return True
    return False


def run_seal_tick(session: Session, settings: Settings, *, now: datetime | None = None,
                  clock: Callable[[], datetime] | None = None) -> dict[str, Any]:
    """One tick: record first-seen games, seal every due packet, record misses."""

    if not settings.v18_market_pilot_export_enabled:
        return {"records": 0, "enabled": False,
                "note": "V18_MARKET_PILOT_EXPORT_ENABLED is off; nothing read or written"}
    clock = clock or (lambda: datetime.now(UTC))
    now = pilot.aware(now or clock())
    directory = Path(settings.v18_market_pilot_packet_dir)
    ledger = AttemptLedger(directory)
    try:
        inputs = build_inputs(settings)
    except pilot.FrozenProtocolError as error:
        ledger.append({"kind": "SYSTEMIC_STOP", "code": "FROZEN_PROTOCOL_MISMATCH", "detail": str(error)},
                      now=now)
        logger.error("v18_market_pilot_frozen_protocol_mismatch", error=str(error))
        return {"records": 0, "enabled": True, "refused": "FROZEN_PROTOCOL_MISMATCH", "detail": str(error)}

    entries = ledger.entries()
    seen = {e.get("episodeKey") for e in entries if e.get("kind") == "UNIVERSE_FIRST_SEEN"}
    finished = {e.get("episodeKey") for e in entries
                if e.get("kind") in ("SEALED", "SEALED_LATE_NOT_PROSPECTIVE", "MISSED_SEAL_DEADLINE",
                                     "REFUSED_KNOWN_STARTED")}
    summary: dict[str, Any] = {"enabled": True, "protocolSha256": pilot.PROTOCOL_SHA256,
                               "externalProtocolCheck": inputs.protocol.external_check,
                               "directory": str(directory), "universeRecorded": [], "sealed": [],
                               "refused": [], "missed": [], "deferred": [], "pending": 0}
    for game in _window_games(session, now):
        start = pilot.aware(game.starts_at)
        lock = pilot.research_lock(start)
        deadline = pilot.seal_deadline(start)
        game_pk = _game_pk(session, game.id)
        key = pilot.episode_key(game.id, game_pk, lock)
        if key not in seen:
            ledger.append({"kind": "UNIVERSE_FIRST_SEEN", "episodeKey": key, "canonicalGameId": game.id,
                           "mlbGamePk": game_pk, "scheduledStartUtc": pilot.iso(start),
                           "researchLockUtc": pilot.iso(lock), "sealDeadlineUtc": pilot.iso(deadline),
                           "firstSeenBySealerUtc": pilot.iso(now),
                           "lateDiscovery": now > deadline}, now=now)
            seen.add(key)
            summary["universeRecorded"].append(key)
        if key in finished or packet_path(directory, key).exists():
            continue
        if now < lock:
            summary["pending"] += 1
            continue
        if now > deadline or now >= start:
            ledger.append({"kind": "MISSED_SEAL_DEADLINE", "episodeKey": key, "canonicalGameId": game.id,
                           "sealDeadlineUtc": pilot.iso(deadline), "observedAtUtc": pilot.iso(now)}, now=now)
            finished.add(key)
            summary["missed"].append(key)
            continue
        status = session.scalar(select(Game.status).where(Game.id == pilot._uuid(game.id)))
        if status in (GameStatus.LIVE, GameStatus.FINAL):
            ledger.append({"kind": "REFUSED_KNOWN_STARTED", "episodeKey": key, "canonicalGameId": game.id,
                           "observedAtUtc": pilot.iso(now)}, now=now)
            finished.add(key)
            summary["refused"].append({"episodeKey": key, "code": "KNOWN_STARTED"})
            continue
        if _freeze_in_flight(session, lock) and now < deadline - LAST_CHANCE_BEFORE_DEADLINE:
            summary["deferred"].append(key)
            continue
        try:
            outcome = seal_game(session, game, inputs=inputs, directory=directory, clock=clock, ledger=ledger)
        except pilot.OutcomeFieldError as error:
            ledger.append({"kind": "SYSTEMIC_STOP", "code": "OUTCOME_FIELD_IN_PACKET", "episodeKey": key,
                           "detail": str(error)}, now=pilot.aware(clock()))
            summary["refused"].append({"episodeKey": key, "code": "OUTCOME_FIELD_IN_PACKET"})
            break
        if outcome.sealed:
            summary["sealed"].append({"episodeKey": key, "packetSha256": outcome.packet_sha256})
            finished.add(key)
        else:
            summary["refused"].append({"episodeKey": key, "code": outcome.code, "detail": outcome.detail})
    summary["records"] = len(summary["sealed"])
    return summary


__all__ = ["AttemptLedger", "JOB_NAME", "SCHEDULER_ID", "SealOutcome", "SealRefused", "build_inputs",
           "packet_path", "run_seal_tick", "seal_game", "work_due", "write_once"]

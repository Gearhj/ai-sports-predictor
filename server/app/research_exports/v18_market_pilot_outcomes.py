"""Consumer-admitted outcomes for the FROZEN V18 vs fresh-market pilot.

WHAT THIS IS
------------
Model Studio's "Same-final export for scheduled assessments" contract
(``MODEL_STUDIO_INTEGRATION_STATUS.md``, 2026-09-19) under protocol
``f75aeccd...``. The module writes one immutable record per ADMITTED revision of
a sealed game's outcome:

    <v18_market_pilot_outcomes_dir>/<canonicalGameId>/<physical-sha256>.json

The same record supplies both comparison arms (V18 and market). A correction
is a new file that names ``supersedesRevisionId``, and earlier revisions are
kept. This module is separate from the pregame packet builder and its sealer
(:mod:`app.research_exports.v18_market_pilot`, ``..._seal``). Those modules
must never read an outcome, and neither imports this one.

SCOPE
-----
A game is in scope only if its schedule episode has an authentic completed
seal. That means:

* a ``SEALED`` row in ``consumer-packets/attempt-ledger.jsonl``, with the
  ledger's hash chain intact;
* the packet file exists and matches the row's sha256 and byte count;
* the packet's ``.sha256`` sidecar agrees.

A refused, missed or late (``SEALED_LATE_NOT_PROSPECTIVE``) episode gets no
record. Identity is copied from the sealed packet: ``gameId`` (= packet
``canonicalGameId``), ``sourceGameIds``, ``homeId`` and ``awayId``. It is
copied only after the same identity has been re-derived from AISP's rows the
way the packet builder derives it. Any disagreement is a refusal, never a
guess. Every record also carries the episode's ``episodeKey`` and
``scheduledStartUtc``. A postponed game replayed under the same canonical id is
a different schedule episode.

HOW AISP ADMITS AN MLB FINAL
----------------------------
The source capture is the MLB Stats API ``/schedule`` response (linescore
hydrated). ``refresh-schedule``, which ``finalize-games`` and the daily
pipeline run first, fetches it. :class:`app.ingestion.RawArchive` retains the
raw response under ``<raw_data_root>/mlb_stats_api/YYYY/MM/DD/``, and an
``ingestion_runs`` row names that file (``raw_reference``,
``started_at = fetch clock``). The normalizer appends one
``game_state_observations`` row per game. That row holds AISP's canonical
status, both scores, ``payload_checksum`` = sha256 of the schedule DTO, and
``available_as_of`` = the fetch clock. ``games`` is only a compatibility
projection, frozen at its first FINAL. ``game_final_outcomes`` is never read
here. It holds RETROSPECTIVE_RECONSTRUCTION rows: the 2025 training labels,
plus the governed finals that ``recover-governed-finals`` reconstructs for
parlay settlement. Neither is the research admission.

AISP's research admission of a final is the V18 scorer's rule
(``ForecastEvaluationService.evaluate_game``), and this module calls it rather
than copying it:

* the ``games`` status must not be LIVE, POSTPONED or CANCELED; and
* ``result_for`` must find a FINAL observation with both scores. When several
  exist, it takes the best provenance tier, then the latest
  ``available_as_of``, then the checksum. That row's identity is
  ``source:payload_checksum``, so a corrected final is a new identity.

Settlement (``finalize-games``) reads the ``games`` projection instead. When
the projection disagrees with the admitted observation, the record says so in
``reasonCodes``.

STATE MAPPING (S = the sealed packet's ``game.scheduledStartUtc``)
------------------------------------------------------------------
The episode's state is read from LIVE-mode observations whose
``scheduled_at`` equals S. MLB keeps a postponed game's original ``gameDate``,
so a postponement is observed at S and the makeup is not. The admitted final
itself is the scorer's, whatever its ``scheduled_at``, once play at S is on
record; a suspended game resumed later is still the same episode. AISP's
canonical statuses come from ``normalizer._game_status``: a
detailedState containing "final" or "completed" maps to final, one containing
"progress", "live", "Game Over", a challenge or review, or a mid-game "Delayed"
maps to live, "postpon" maps to postponed, "cancel" maps to canceled, and
everything else, including "Suspended" and "Delayed Start", maps to scheduled.

====================================================  ===========  =========================
AISP admitted state for the episode                   status       notes
====================================================  ===========  =========================
scorer admits a final; a live or final observation    DECISIVE     integer homeScore/awayScore
  exists at S; home != away                                        from the admitted row
same, home == away                                    TIE          integer scores
canonical ``postponed`` observed at S (latest of      VOID         the episode was not played
  postponed/canceled at S)                                         at its sealed start; may be
                                                                   known before first pitch
canonical ``canceled`` observed at S (latest of       NO_CONTEST   never played
  postponed/canceled at S)
canonical ``live`` observed at S, no admitted final   PENDING      play started, not final;
                                                                   finalAtUtc null, no scores
postponed/canceled AND live/final both at S           no record    conflict, ledger reason
admitted final, but no live/final observation at S    no record    final belongs to another
                                                                   start, ledger reason
only ``scheduled`` (incl. Suspended/Delayed Start)    no record    ledger reason
no completed admission receipt yet                    no record    retried each tick
PUSH, REFUND                                          never        wager settlements, not game
                                                                   states AISP admits
====================================================  ===========  =========================

The verbatim source state goes into ``admission.sourceState``. AISP's
canonical value is always there. The provider's own status object
(abstractGameState, codedGameState, detailedState, statusCode, reason) is
there when the linked raw capture is readable and consistent with the row.
Nothing is ever inferred from a position, a settlement or a profit/loss. This
module does not read those tables, and a P/L-shaped key in a record or
evidence document is refused.

REVISIONS
---------
``contentId`` is sha256 over the admitted content: identity, episode, status,
scores, ``sourceRevisionIdentity``, canonical status, admission rule and the
three clocks. Every part is read from the database, so change detection never
re-reads a raw file. ``revisionId`` = sha256({contentId, supersedesRevisionId}),
which is deterministic, and a return to an earlier state is still a new
revision in the chain. A new file is written only when ``contentId`` differs
from the episode's latest written revision. Records are canonical JSON with no
export clock, so their bytes are reproducible. A revision whose file already
exists is adopted, never written twice.

Each status is anchored on a stable row, so repeated schedule refreshes do not
produce new revisions:

* DECISIVE/TIE: the first capture of the scorer's identity.
* VOID/NO_CONTEST: the first observation at S of the latest void status.
* PENDING: the first live observation at S.

CLOCKS
------
sourceCapturedAtUtc
    The anchor row's ``available_as_of``. It is the MLB ``/schedule`` fetch
    clock (``envelope.fetched_at``): when AISP captured that source revision.
admittedAtUtc
    A completed receipt: ``job_runs.completed_at`` of the observation-writer
    run(s) whose execution window contains the anchor row's fetch clock and
    transaction start. If writer runs overlap, it is the latest of them, an
    upper bound on the commit. It is never ``created_at``, which is the start
    of the inserting transaction. If a candidate writer run is still in
    flight, or none exists, the record waits.
finalAtUtc
    The admitted row's ``final_at``. The MLB schedule feed supplies no
    trustworthy completion time and AISP stores null rather than first pitch,
    so for MLB this is null, with ``FINAL_AT_NOT_PROVIDED_BY_SOURCE`` in
    ``reasonCodes``. It is always null for VOID, NO_CONTEST and PENDING.

FILES (all write-once, read-only after; nothing is ever overwritten)
--------------------------------------------------------------------
A game directory holds only ``<sha>.json`` records and one ``evidence/``
subdirectory, so a reader that lists the records sees nothing else.

``<gameId>/<sha>.json``
    The record.
``<gameId>/evidence/<sha>.json``
    The admitting rows, limited to identity, score, status and clock columns,
    that the refs point into by JSON pointer. A ref's ``path`` is relative to
    the record's directory.
``<gameId>/evidence/<recordSha>.sha256``
    sha256sum lines for the record (``../<sha>.json``) and its evidence
    document.
``outcomes-ledger.jsonl``
    Append-only and hash-chained, with no scores. It records every written or
    adopted revision, every distinct reason for writing nothing, and every
    systemic stop.

WHAT IT MAY NOT DO
------------------
* Every read happens in a dedicated transaction (``SET TRANSACTION READ ONLY``
  on PostgreSQL) that is always rolled back. A dirty session is refused.
* It never calls a provider, and never creates or alters a decision, position,
  settlement or policy.
* It never writes inside the pregame packets directory; an overlapping
  configuration is refused.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import logger
# AISP's own canonical mapping of an MLB detailedState. Used only to check that
# a raw capture is the one behind an observation; never to admit anything.
from app.ingestion.normalizer import _game_status as aisp_canonical_status
from app.models import ExternalIdMapping, Game, GameStateObservation, GameStatus, GameTeam, IngestionRun, JobRun
from app.research_exports import v18_market_pilot as pilot
from app.research_exports.v18_market_pilot_seal import LEDGER_NAME as ATTEMPT_LEDGER_NAME
from app.research_exports.v18_market_pilot_seal import AttemptLedger, packet_path, write_once
# The research scorer's admission gate and final selection, reused verbatim.
from app.services.forecast_evaluation import _NONFINAL_STATUSES as SCORER_NONFINAL_STATUSES
from app.services.forecast_evaluation import ForecastEvaluationService

RECORD_TYPE = "MLB_V18_MARKET_ADMITTED_OUTCOME_V1"
EXPORT_IDENTITY = "AISP_V18_MARKET_PILOT_CONSUMER_OUTCOME_EXPORT_V1"
EVIDENCE_SCHEMA = "AISP_V18_MARKET_PILOT_OUTCOME_EVIDENCE_V1"
LEDGER_NAME = "outcomes-ledger.jsonl"
EVIDENCE_DIR = "evidence"
JOB_NAME = "export-v18-market-pilot-outcomes"
SCHEDULER_ID = "asp-rx-v18-market-pilot-outcomes"
INTERVAL_MINUTES = 10

DECISIVE, TIE, PUSH, REFUND, NO_CONTEST, VOID, PENDING = (
    "DECISIVE", "TIE", "PUSH", "REFUND", "NO_CONTEST", "VOID", "PENDING")
STATUSES = (DECISIVE, TIE, PUSH, REFUND, NO_CONTEST, VOID, PENDING)
PLAYED_STATUSES = frozenset({DECISIVE, TIE})

#: Every record carries these (homeScore/awayScore are integers for DECISIVE/TIE
#: and explicit nulls otherwise; supersedesRevisionId is null on a first revision).
REQUIRED_FIELDS = (
    "recordType", "gameId", "sourceGameIds", "homeId", "awayId", "revisionId", "sourceRevisionIdentity",
    "consumerAdmissionVerified", "sourceCapturedAtUtc", "admittedAtUtc", "finalAtUtc", "status",
    "homeScore", "awayScore", "supersedesRevisionId", "sourceRefs", "admissionRefs",
)

RULE_FINAL = "AISP_RESEARCH_SCORER_FINAL_ATTRIBUTED_TO_SEALED_START"
RULE_VOID = "AISP_CANONICAL_POSTPONED_OR_CANCELED_AT_SEALED_START"
RULE_PENDING = "AISP_CANONICAL_LIVE_AT_SEALED_START_NO_ADMITTED_FINAL"

#: AISP canonical observation statuses (GameStatus values, lowercase).
_FINAL, _LIVE = GameStatus.FINAL.value, GameStatus.LIVE.value
_POSTPONED, _CANCELED = GameStatus.POSTPONED.value, GameStatus.CANCELED.value
VOID_STATUS_FOR = {_POSTPONED: VOID, _CANCELED: NO_CONTEST}
PLAY_STATUSES = frozenset({_LIVE, _FINAL})

#: Jobs that write MLB game_state_observations through the governed normalizer
#: (refresh_schedule and the availability heartbeat / event bridge). Only their
#: completed runs can be the admission receipt of an observation.
OBSERVATION_WRITER_JOBS = frozenset({
    "refresh-schedule", "finalize-games", "daily-pipeline", "poll-availability-events",
    "bridge-mlb-canonical-events", "controlled-live-validation",
})
#: How far before an observation's fetch clock a writer run may have started.
WRITER_RUN_LOOKBACK = timedelta(hours=12)

#: Keys that would carry a wager's result or P/L. None may appear in a record,
#: its evidence or the ledger: an outcome is never inferred from a position.
PNL_KEYS = frozenset({
    "profit_loss", "profitLoss", "payout", "stake", "settlement", "settlements", "settled_at",
    "settledAt", "positionResult", "position_result", "bankroll_before", "bankroll_after",
    "bankrollBefore", "bankrollAfter", "winner", "result", "results", "closing_line_value",
})

CLOCK_MEANINGS = {
    "sourceCapturedAtUtc": (
        "When AISP received the MLB Stats API /schedule response that first carried this admitted "
        "source revision: envelope.fetched_at, stored as game_state_observations.available_as_of of "
        "the anchor observation."),
    "admittedAtUtc": (
        "Completed receipt: job_runs.completed_at of the observation-writer run whose execution "
        "window contains the anchor observation's fetch clock and transaction start (the latest such "
        "run when runs overlapped). The commit happened no later than this. Never created_at, which "
        "is the start of the inserting transaction."),
    "finalAtUtc": (
        "game_state_observations.final_at of the admitted observation, as supplied by the source. "
        "The MLB schedule feed supplies no trustworthy completion time and AISP stores null rather "
        "than reuse first pitch, so null here means unknown, not omitted. Always null for VOID, "
        "NO_CONTEST and PENDING."),
}


class OutcomeExportRefused(RuntimeError):
    """A systemic condition under which no record may be written."""


# ---------------------------------------------------------------------------
# Read-only transaction
# ---------------------------------------------------------------------------
@contextmanager
def read_only(session: Session) -> Iterator[Session]:
    """Every read in one READ ONLY transaction (PostgreSQL), always rolled back."""

    if session.new or session.dirty or session.deleted:
        raise OutcomeExportRefused("Refusing a session with pending changes")
    try:
        with session.no_autoflush:
            if session.get_bind().dialect.name == "postgresql":
                session.execute(text("SET TRANSACTION READ ONLY"))
                confirmed = str(session.execute(text("SHOW transaction_read_only")).scalar()).lower()
                if confirmed not in ("on", "true", "1"):
                    raise OutcomeExportRefused("PostgreSQL did not confirm a read-only transaction")
            yield session
            if session.new or session.dirty or session.deleted:
                raise OutcomeExportRefused("The outcome reader modified the session")
    finally:
        session.rollback()


def assert_no_pnl_fields(value: Any, where: str = "record") -> None:
    stack: list[tuple[Any, str]] = [(value, where)]
    while stack:
        item, path = stack.pop()
        if isinstance(item, dict):
            for key, child in item.items():
                if key in PNL_KEYS:
                    raise OutcomeExportRefused(f"P/L-shaped key {key!r} at {path}")
                stack.append((child, f"{path}/{key}"))
        elif isinstance(item, list):
            stack.extend((child, f"{path}[{i}]") for i, child in enumerate(item))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Sealed episodes (the only games in scope)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SealedEpisode:
    episode_key: str
    game_id: str
    scheduled_start: datetime
    sealed_at: str | None
    packet: dict[str, Any]
    packet_ref: dict[str, Any]


@dataclass
class SealScan:
    episodes: list[SealedEpisode] = field(default_factory=list)
    #: (episodeKey, code, detail) for SEALED rows that failed verification.
    refused: list[tuple[str, str, str]] = field(default_factory=list)
    systemic: tuple[str, str] | None = None


def sealed_episodes(packet_dir: str | Path) -> SealScan:
    """Authentic completed seals, verified against the attempt ledger's hash chain."""

    packet_dir = Path(packet_dir)
    scan = SealScan()
    try:
        raw = (packet_dir / ATTEMPT_LEDGER_NAME).read_bytes()
    except FileNotFoundError:
        return scan
    except OSError as error:
        scan.systemic = ("ATTEMPT_LEDGER_UNREADABLE", str(error))
        return scan
    entries: list[dict[str, Any]] = []
    previous: str | None = None
    for number, line in enumerate((l for l in raw.splitlines() if l.strip()), start=1):
        try:
            entry = json.loads(line)
        except ValueError:
            scan.systemic = ("ATTEMPT_LEDGER_LINE_UNPARSEABLE", f"line {number}")
            return scan
        if entry.get("seq") != number or entry.get("prevLineSha256") != previous:
            scan.systemic = ("ATTEMPT_LEDGER_HASH_CHAIN_BROKEN", f"line {number}")
            return scan
        previous = _sha(line)
        entries.append(entry)

    sealed: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        if entry.get("kind") == "SEALED" and entry.get("episodeKey"):
            sealed.setdefault(str(entry["episodeKey"]), []).append(entry)
    for key in sorted(sealed):
        rows = sealed[key]
        if len(rows) != 1:
            scan.refused.append((key, "DUPLICATE_SEALED_LEDGER_ROWS", f"{len(rows)} SEALED rows"))
            continue
        entry = rows[0]
        path = packet_path(packet_dir, key)
        try:
            data = path.read_bytes()
        except OSError:
            scan.refused.append((key, "SEALED_PACKET_FILE_UNREADABLE", path.name))
            continue
        digest = _sha(data)
        if digest != entry.get("packetSha256"):
            scan.refused.append((key, "SEALED_PACKET_SHA256_MISMATCH", f"ledger {entry.get('packetSha256')}"))
            continue
        if len(data) != entry.get("packetBytes"):
            scan.refused.append((key, "SEALED_PACKET_BYTES_MISMATCH", f"ledger {entry.get('packetBytes')}"))
            continue
        try:
            sums = (packet_dir / f"{key}.sha256").read_text(encoding="ascii").split()
        except OSError:
            scan.refused.append((key, "SEALED_PACKET_SHA256_SIDECAR_UNREADABLE", f"{key}.sha256"))
            continue
        if not sums or sums[0] != digest:
            scan.refused.append((key, "SEALED_PACKET_SHA256_SIDECAR_MISMATCH", f"{key}.sha256"))
            continue
        try:
            packet = json.loads(data)
        except ValueError:
            scan.refused.append((key, "SEALED_PACKET_UNPARSEABLE", path.name))
            continue
        game = packet.get("game") or {}
        start = pilot.aware(game.get("scheduledStartUtc"))
        if (packet.get("protocolSha256") != pilot.PROTOCOL_SHA256 or packet.get("episodeKey") != key
                or not game.get("canonicalGameId") or start is None):
            scan.refused.append((key, "SEALED_PACKET_IDENTITY_INCOMPLETE", "protocol/episode/game block"))
            continue
        scan.episodes.append(SealedEpisode(
            episode_key=key, game_id=str(game["canonicalGameId"]), scheduled_start=start,
            sealed_at=packet.get("sealedAtUtc"), packet=packet,
            packet_ref={"path": str(path), "sha256": digest, "bytes": len(data),
                        "locator": f"{ATTEMPT_LEDGER_NAME}#seq={entry.get('seq')}"}))
    return scan


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Observation:
    id: str
    status: str
    is_postponed: bool
    postponement_reason: str | None
    scheduled_at: datetime | None
    home_team_id: str | None
    away_team_id: str | None
    home_score: int | None
    away_score: int | None
    final_at: datetime | None
    observed_at: datetime | None
    available_as_of: datetime
    created_at: datetime | None
    source: str
    source_mode: str
    source_record_id: str | None
    payload_checksum: str | None
    ingestion_run_id: str | None

    @property
    def identity(self) -> str:
        """The research scorer's result identity (``source:payload_checksum``)."""

        return f"{self.source}:{self.payload_checksum}"

    def evidence(self) -> dict[str, Any]:
        return {
            "id": self.id, "status": self.status, "is_postponed": self.is_postponed,
            "postponement_reason": self.postponement_reason, "scheduled_at": pilot.iso(self.scheduled_at),
            "home_team_id": self.home_team_id, "away_team_id": self.away_team_id,
            "home_score": self.home_score, "away_score": self.away_score, "final_at": pilot.iso(self.final_at),
            "observed_at": pilot.iso(self.observed_at), "available_as_of": pilot.iso(self.available_as_of),
            "created_at_transaction_start": pilot.iso(self.created_at), "source": self.source,
            "source_mode": self.source_mode, "source_record_id": self.source_record_id,
            "payload_checksum": self.payload_checksum, "ingestion_run_id": self.ingestion_run_id,
        }


def _opt(value: Any) -> str | None:
    return None if value is None else str(value)


def _observations(session: Session, game_id: Any) -> list[Observation]:
    rows = session.execute(
        select(GameStateObservation.id, GameStateObservation.status, GameStateObservation.is_postponed,
               GameStateObservation.postponement_reason, GameStateObservation.scheduled_at,
               GameStateObservation.home_team_id, GameStateObservation.away_team_id,
               GameStateObservation.home_score, GameStateObservation.away_score, GameStateObservation.final_at,
               GameStateObservation.observed_at, GameStateObservation.available_as_of,
               GameStateObservation.created_at, GameStateObservation.source, GameStateObservation.source_mode,
               GameStateObservation.source_record_id, GameStateObservation.payload_checksum,
               GameStateObservation.ingestion_run_id)
        .where(GameStateObservation.game_id == pilot._uuid(game_id))
    ).all()
    result = [Observation(
        id=str(r.id), status=str(r.status or "").lower(), is_postponed=bool(r.is_postponed),
        postponement_reason=r.postponement_reason, scheduled_at=pilot.aware(r.scheduled_at),
        home_team_id=_opt(r.home_team_id), away_team_id=_opt(r.away_team_id),
        home_score=r.home_score, away_score=r.away_score, final_at=pilot.aware(r.final_at),
        observed_at=pilot.aware(r.observed_at), available_as_of=pilot.aware(r.available_as_of),
        created_at=pilot.aware(r.created_at), source=str(r.source), source_mode=str(r.source_mode or ""),
        source_record_id=r.source_record_id, payload_checksum=r.payload_checksum,
        ingestion_run_id=_opt(r.ingestion_run_id),
    ) for r in rows]
    return sorted(result, key=lambda o: (o.available_as_of, o.id))


def current_identity(session: Session, game_id: Any) -> dict[str, Any]:
    """The game identity exactly as the pregame packet builder derives it."""

    uid = pilot._uuid(game_id)
    source_ids: dict[str, Any] = {}
    for m in session.execute(
        select(ExternalIdMapping.source, ExternalIdMapping.external_id)
        .where(ExternalIdMapping.entity_type == "game", ExternalIdMapping.entity_id == uid)
        .order_by(ExternalIdMapping.source, ExternalIdMapping.external_id)
    ).all():
        source_ids.setdefault(m.source, []).append(m.external_id)
    source_ids = {k: (v[0] if len(v) == 1 else v) for k, v in source_ids.items()}
    sides = session.execute(select(GameTeam.team_id, GameTeam.is_home).where(GameTeam.game_id == uid)).all()
    home = next((str(s.team_id) for s in sides if s.is_home), None)
    away = next((str(s.team_id) for s in sides if not s.is_home), None)
    return {"canonicalGameId": str(game_id), "sourceGameIds": source_ids, "homeId": home or "",
            "awayId": away or ""}


def identity_disagreement(packet_game: dict[str, Any], current: dict[str, Any]) -> str | None:
    """None when AISP's rows still carry the sealed identity (new mappings may be added)."""

    if str(packet_game.get("canonicalGameId")) != current["canonicalGameId"]:
        return "canonicalGameId"
    if not current["homeId"] or packet_game.get("homeId") != current["homeId"]:
        return "homeId"
    if not current["awayId"] or packet_game.get("awayId") != current["awayId"]:
        return "awayId"
    sealed_ids = packet_game.get("sourceGameIds") or {}
    for source, value in sealed_ids.items():
        if current["sourceGameIds"].get(source) != value:
            return f"sourceGameIds.{source}"
    return None


def _game_status(value: Any) -> GameStatus:
    return value if isinstance(value, GameStatus) else GameStatus(str(value).lower())


@dataclass(frozen=True)
class Receipt:
    at: datetime | None
    runs: tuple[dict[str, Any], ...]
    code: str | None                      # None when established
    reason_codes: tuple[str, ...] = ()


def admission_receipt(session: Session, anchor: Observation) -> Receipt:
    """The completed writer run(s) that committed ``anchor``; an upper bound on its commit."""

    fetched = anchor.available_as_of
    rows = session.execute(
        select(JobRun.id, JobRun.job_name, JobRun.status, JobRun.started_at, JobRun.completed_at)
        .where(JobRun.job_name.in_(sorted(OBSERVATION_WRITER_JOBS)),
               JobRun.started_at >= fetched - WRITER_RUN_LOOKBACK,
               JobRun.started_at <= fetched + timedelta(seconds=1))
        .order_by(JobRun.started_at, JobRun.id)
    ).all()
    candidates = []
    for r in rows:
        started, completed = pilot.aware(r.started_at), pilot.aware(r.completed_at)
        if started is None or started > fetched:
            continue
        if anchor.created_at is not None and started > anchor.created_at:
            continue
        if completed is not None and (completed < fetched
                                      or (anchor.created_at is not None and completed < anchor.created_at)):
            continue
        candidates.append({"id": str(r.id), "job_name": r.job_name, "status": getattr(r.status, "value", r.status),
                           "started_at": pilot.iso(started), "completed_at": pilot.iso(completed)})
    if not candidates:
        return Receipt(None, (), "ADMISSION_RECEIPT_NO_WRITER_RUN_FOUND")
    if any(c["completed_at"] is None for c in candidates):
        return Receipt(None, tuple(candidates), "ADMISSION_RECEIPT_WRITER_RUN_IN_FLIGHT")
    at = max(pilot.aware(c["completed_at"]) for c in candidates)
    codes = ("ADMISSION_RECEIPT_UPPER_BOUND_OVER_OVERLAPPING_WRITER_RUNS",) if len(candidates) > 1 else ()
    return Receipt(at, tuple(candidates), None, codes)


def ingestion_link(session: Session, anchor: Observation) -> tuple[dict[str, Any] | None, str | None]:
    """The ingestion run that archived the anchor's raw capture (fetch clock == started_at)."""

    query = select(IngestionRun.id, IngestionRun.source, IngestionRun.job_name, IngestionRun.status,
                   IngestionRun.started_at, IngestionRun.completed_at, IngestionRun.raw_reference,
                   IngestionRun.records_processed)
    if anchor.ingestion_run_id:
        rows = session.execute(query.where(IngestionRun.id == pilot._uuid(anchor.ingestion_run_id))).all()
    else:
        fetched = anchor.available_as_of
        rows = [r for r in session.execute(
            query.where(IngestionRun.source == anchor.source,
                        IngestionRun.started_at >= fetched - timedelta(seconds=1),
                        IngestionRun.started_at <= fetched + timedelta(seconds=1))
        ).all() if pilot.aware(r.started_at) == fetched and r.raw_reference]
    if not rows:
        return None, "RAW_SOURCE_CAPTURE_NOT_LINKED"
    if len(rows) > 1:
        return None, "RAW_SOURCE_CAPTURE_LINK_AMBIGUOUS"
    r = rows[0]
    return {"id": str(r.id), "source": r.source, "job_name": r.job_name,
            "status": getattr(r.status, "value", r.status), "started_at": pilot.iso(r.started_at),
            "completed_at_before_commit": pilot.iso(r.completed_at), "raw_reference": r.raw_reference,
            "records_processed": r.records_processed}, None


# ---------------------------------------------------------------------------
# One episode's admitted state
# ---------------------------------------------------------------------------
@dataclass
class Disposition:
    episode: SealedEpisode
    code: str | None = None               # None: a record is due
    detail: str = ""
    status: str | None = None
    rule: str | None = None
    anchor: Observation | None = None
    scorer_row: Observation | None = None
    first_play: Observation | None = None
    void_rows: list[Observation] = field(default_factory=list)
    receipt: Receipt | None = None
    ingestion: dict[str, Any] | None = None
    ingestion_code: str | None = None
    identity: dict[str, Any] | None = None
    game_row: dict[str, Any] | None = None
    scorer_gate: str | None = None
    reason_codes: list[str] = field(default_factory=list)
    content: dict[str, Any] | None = None
    content_id: str | None = None

    @property
    def is_record(self) -> bool:
        return self.code is None


def _no_record(episode: SealedEpisode, code: str, detail: str = "", **extra: Any) -> Disposition:
    return Disposition(episode=episode, code=code, detail=detail, **extra)


def admitted_state(session: Session, episode: SealedEpisode) -> Disposition:
    """What AISP has admitted for this sealed episode. Reads only."""

    packet_game = episode.packet.get("game") or {}
    identity = current_identity(session, episode.game_id)
    mismatch = identity_disagreement(packet_game, identity)
    if mismatch:
        return _no_record(episode, "IDENTITY_DIFFERS_FROM_SEALED_PACKET", mismatch)
    game = session.get(Game, pilot._uuid(episode.game_id))
    if game is None:
        return _no_record(episode, "GAME_ROW_MISSING")
    gate = _game_status(game.status)
    game_row = {"id": str(game.id), "status": gate.value, "starts_at": pilot.iso(game.starts_at),
                "home_score": game.home_score, "away_score": game.away_score,
                "final_at": pilot.iso(game.final_at), "source_mode": game.source_mode}

    observations = _observations(session, episode.game_id)
    live_mode = [o for o in observations if o.source_mode == "LIVE"]
    at_start = [o for o in live_mode if o.scheduled_at == episode.scheduled_start]
    void_rows = [o for o in at_start if o.status in VOID_STATUS_FOR]
    play_rows = [o for o in at_start if o.status in PLAY_STATUSES]
    first_play = play_rows[0] if play_rows else None
    latest = live_mode[-1].status if live_mode else "NONE"

    scorer = None
    if gate in SCORER_NONFINAL_STATUSES:
        scorer_gate = f"SCORER_GATE_REFUSED_GAME_STATUS_{gate.value.upper()}"
    else:
        scorer = ForecastEvaluationService(session).result_for(game)
        scorer_gate = "SCORER_ADMITTED_FINAL" if scorer else "SCORER_NO_FINAL_OBSERVATION_WITH_BOTH_SCORES"
    common = {"identity": identity, "game_row": game_row, "scorer_gate": scorer_gate, "void_rows": void_rows,
              "first_play": first_play}

    scorer_row: Observation | None = None
    if void_rows and play_rows:
        return _no_record(episode, "EPISODE_STATE_CONFLICT_VOID_AND_PLAY_AT_SEALED_START",
                          f"void {sorted({o.status for o in void_rows})}; "
                          f"play {sorted({o.status for o in play_rows})}", **common)
    if void_rows:
        category = void_rows[-1].status
        anchor = next(o for o in void_rows if o.status == category)
        status, rule = VOID_STATUS_FOR[category], RULE_VOID
    elif scorer is not None:
        if not play_rows:
            return _no_record(episode, "ADMITTED_FINAL_NOT_ATTRIBUTABLE_TO_SEALED_START",
                              f"no live/final observation scheduled at {pilot.iso(episode.scheduled_start)}",
                              **common)
        same = [o for o in observations if o.status == _FINAL and o.identity == scorer.identity
                and o.home_score is not None and o.away_score is not None]
        scorer_row = same[-1] if same else None
        if not same or any((o.home_score, o.away_score) != scorer.scores for o in same):
            return _no_record(episode, "SCORER_IDENTITY_NOT_REPRODUCED_FROM_OBSERVATIONS", scorer.identity,
                              **common)
        anchor = same[0]
        status = TIE if scorer.home_score == scorer.away_score else DECISIVE
        rule = RULE_FINAL
    elif any(o.status == _LIVE for o in play_rows):
        anchor = next(o for o in play_rows if o.status == _LIVE)
        status, rule = PENDING, RULE_PENDING
    else:
        return _no_record(episode, "NO_ADMITTED_OUTCOME_STATE",
                          f"latest AISP canonical status {latest}; {scorer_gate}", **common)

    if ((anchor.home_team_id and anchor.home_team_id != packet_game.get("homeId"))
            or (anchor.away_team_id and anchor.away_team_id != packet_game.get("awayId"))):
        return _no_record(episode, "ADMITTED_OBSERVATION_ORIENTATION_DIFFERS_FROM_SEALED_PACKET", anchor.id,
                          **common)
    receipt = admission_receipt(session, anchor)
    if receipt.code:
        return _no_record(episode, receipt.code, anchor.id, receipt=receipt, anchor=anchor, **common)
    ingestion, ingestion_code = ingestion_link(session, anchor)

    reasons: list[str] = list(receipt.reason_codes)
    played = status in PLAYED_STATUSES
    final_at = anchor.final_at if played else None
    if played and final_at is None:
        reasons.append("FINAL_AT_NOT_PROVIDED_BY_SOURCE")
    if played and (game_row["status"] != _FINAL or (game_row["home_score"], game_row["away_score"])
                   != (anchor.home_score, anchor.away_score)):
        reasons.append("GAMES_PROJECTION_DIFFERS_FROM_ADMITTED_OBSERVATION")
    extra_ids = sorted(set(identity["sourceGameIds"]) - set(packet_game.get("sourceGameIds") or {}))
    if extra_ids:
        reasons.append("LIMITATION_CURRENT_MAPPINGS_EXTEND_SEALED_SOURCE_IDS")
    content = {
        "recordType": RECORD_TYPE,
        "protocolSha256": pilot.PROTOCOL_SHA256,
        "gameId": episode.game_id,
        "episodeKey": episode.episode_key,
        "scheduledStartUtc": pilot.iso(episode.scheduled_start),
        "sourceGameIds": packet_game.get("sourceGameIds") or {},
        "homeId": packet_game.get("homeId"),
        "awayId": packet_game.get("awayId"),
        "status": status,
        "homeScore": int(anchor.home_score) if played else None,
        "awayScore": int(anchor.away_score) if played else None,
        "sourceRevisionIdentity": anchor.identity,
        "aispCanonicalStatus": anchor.status,
        "admissionRule": rule,
        "sourceCapturedAtUtc": pilot.iso(anchor.available_as_of),
        "admittedAtUtc": pilot.iso(receipt.at),
        "finalAtUtc": pilot.iso(final_at),
    }
    return Disposition(
        episode=episode, status=status, rule=rule, anchor=anchor, scorer_row=scorer_row, receipt=receipt,
        ingestion=ingestion, ingestion_code=ingestion_code, reason_codes=reasons, content=content,
        content_id=_sha(pilot.canonical_bytes(content)), **common)


def revision_id(content_id: str, supersedes: str | None) -> str:
    return _sha(pilot.canonical_bytes({"contentId": content_id, "supersedesRevisionId": supersedes}))


# ---------------------------------------------------------------------------
# Raw capture (read only when a record is written)
# ---------------------------------------------------------------------------
_PROVIDER_STATUS_KEYS = ("abstractGameState", "abstractGameCode", "codedGameState", "detailedState",
                         "statusCode", "reason")
_PROVIDER_SCHEDULE_KEYS = ("gameDate", "officialDate", "resumeDate", "resumeGameDate", "rescheduleDate",
                           "rescheduleGameDate", "rescheduledFrom", "rescheduledFromDate", "isTie")


def _int_or_none(value: Any) -> int | None:
    try:
        return None if value is None or isinstance(value, bool) else int(value)
    except (TypeError, ValueError):
        return None


def raw_capture(ingestion: dict[str, Any] | None, anchor: Observation, game_pk: Any
                ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
    """(ref, verbatim provider facts, reason) from the retained raw schedule capture."""

    if ingestion is None or not ingestion.get("raw_reference"):
        return None, None, "RAW_SOURCE_CAPTURE_NOT_LINKED"
    path = Path(str(ingestion["raw_reference"]))
    try:
        data = path.read_bytes()
        document = json.loads(data)
    except (OSError, ValueError):
        return None, None, "RAW_SOURCE_CAPTURE_UNREADABLE"
    metadata = document.get("metadata") or {}
    payload = document.get("payload")
    schedule = payload.get("schedule") if isinstance(payload, dict) else None
    games = [g for day in (schedule or {}).get("dates") or [] for g in day.get("games") or []
             if str(g.get("gamePk")) == str(game_pk)]
    if len(games) != 1:
        return None, None, "RAW_SOURCE_CAPTURE_GAME_NOT_FOUND" if not games else "RAW_SOURCE_CAPTURE_GAME_AMBIGUOUS"
    item = games[0]
    status = item.get("status") or {}
    teams = item.get("teams") or {}
    scores = (_int_or_none((teams.get("home") or {}).get("score")),
              _int_or_none((teams.get("away") or {}).get("score")))
    detailed = str(status.get("detailedState") or "")
    consistent = (str(metadata.get("source") or "") == anchor.source
                  and aisp_canonical_status(detailed or "Scheduled").value == anchor.status
                  and scores == (anchor.home_score, anchor.away_score))
    facts = {
        "status": {k: status.get(k) for k in _PROVIDER_STATUS_KEYS},
        "schedule": {k: item.get(k) for k in _PROVIDER_SCHEDULE_KEYS},
        "scores": {"home": scores[0], "away": scores[1]},
        "gamePk": item.get("gamePk"),
        "archivedAtUtc": pilot.iso(metadata.get("ingestion_timestamp")),
        "payloadChecksum": metadata.get("checksum"),
        "file": {"path": str(path), "sha256": _sha(data), "bytes": len(data)},
        "consistentWithAdmittedObservation": consistent,
    }
    if not consistent:
        return None, facts, "RAW_SOURCE_CAPTURE_INCONSISTENT_WITH_ADMITTED_OBSERVATION"
    ref = {"path": str(path), "sha256": _sha(data), "bytes": len(data),
           "locator": f"payload.schedule.dates[].games[gamePk={game_pk}]"}
    return ref, facts, None


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
def _evidence_document(d: Disposition, provider: dict[str, Any] | None) -> dict[str, Any]:
    rows: dict[str, dict[str, Any]] = {"game_state_observations": {}, "job_runs": {}, "ingestion_runs": {},
                                       "games": {}, "external_id_mappings": {}}
    for obs in (d.anchor, d.scorer_row, d.first_play, *d.void_rows):
        if obs is not None:
            rows["game_state_observations"][obs.id] = obs.evidence()
    for run in (d.receipt.runs if d.receipt else ()):
        rows["job_runs"][run["id"]] = dict(run)
    if d.ingestion:
        rows["ingestion_runs"][d.ingestion["id"]] = dict(d.ingestion)
    if d.game_row:
        rows["games"][d.game_row["id"]] = {**d.game_row, "note": (
            "Compatibility projection read by settlement; frozen at its first FINAL. The scorer gates on "
            "its status only; the admitted evidence is the observation.")}
    for source, value in (d.identity or {}).get("sourceGameIds", {}).items():
        rows["external_id_mappings"][f"game:{source}"] = {"entity_type": "game", "entity_id": d.episode.game_id,
                                                          "source": source, "external_id": value}
    admission = {
        "rule": d.rule,
        "status": d.status,
        "scorerGate": d.scorer_gate,
        "scorerIdentity": d.scorer_row.identity if d.scorer_row else None,
        "scorerChosenObservationId": d.scorer_row.id if d.scorer_row else None,
        "anchorObservationId": d.anchor.id if d.anchor else None,
        "anchorMeaning": {
            RULE_FINAL: "first capture of the scorer's admitted source revision",
            RULE_VOID: "first observation at the sealed start of the latest postponed/canceled status",
            RULE_PENDING: "first observation at the sealed start with canonical status live",
        }.get(d.rule or "", None),
        "attribution": {
            "sealedScheduledStartUtc": pilot.iso(d.episode.scheduled_start),
            "firstPlayObservationIdAtSealedStart": d.first_play.id if d.first_play else None,
            "voidObservationIdsAtSealedStart": [o.id for o in d.void_rows],
        },
        "receipt": {"admittedAtUtc": pilot.iso(d.receipt.at) if d.receipt else None,
                    "writerRunIds": [r["id"] for r in (d.receipt.runs if d.receipt else ())],
                    "writerJobs": sorted(OBSERVATION_WRITER_JOBS)},
        "rawCaptureLink": d.ingestion_code or "LINKED",
        "contentId": d.content_id,
        "gameStatusProjectionAtExport": (d.game_row or {}).get("status"),
    }
    return {
        "schemaVersion": EVIDENCE_SCHEMA,
        "exportIdentity": EXPORT_IDENTITY,
        "protocolSha256": pilot.PROTOCOL_SHA256,
        "episodeKey": d.episode.episode_key,
        "gameId": d.episode.game_id,
        "clockConventions": {
            "created_at_transaction_start": "PostgreSQL now(): the start of the inserting transaction; "
                                            "not an insertion, commit or admission time",
            "available_as_of": "MLB /schedule fetch clock (envelope.fetched_at)",
            "ingestion_runs.completed_at_before_commit": "Python clock taken inside the inserting "
                                                         "transaction, before its commit; not a receipt",
            "job_runs.completed_at": "set after the run's work committed: the completed receipt",
        },
        "rows": rows,
        "notes": {"admission": admission, "providerState": provider},
    }


def _record(d: Disposition, revision: str, supersedes: str | None, provider: dict[str, Any] | None,
            raw_ref: dict[str, Any] | None, raw_code: str | None, evidence_ref: dict[str, Any]
            ) -> dict[str, Any]:
    content = d.content or {}
    reasons = list(d.reason_codes)
    if raw_code:
        reasons.append(raw_code)

    def at(locator: str) -> dict[str, Any]:
        return {**evidence_ref, "locator": locator}

    source_refs = ([raw_ref] if raw_ref else []) + [
        at(f"/rows/game_state_observations/{d.anchor.id}")]
    admission_refs = [at("/notes/admission")] + [
        at(f"/rows/job_runs/{run['id']}") for run in (d.receipt.runs if d.receipt else ())]
    return {
        "recordType": RECORD_TYPE,
        "protocolSha256": pilot.PROTOCOL_SHA256,
        "exportIdentity": EXPORT_IDENTITY,
        "gameId": content["gameId"],
        "sourceGameIds": content["sourceGameIds"],
        "homeId": content["homeId"],
        "awayId": content["awayId"],
        "episodeKey": content["episodeKey"],
        "scheduledStartUtc": content["scheduledStartUtc"],
        "pregamePacketRef": d.episode.packet_ref,
        "revisionId": revision,
        "supersedesRevisionId": supersedes,
        "contentId": d.content_id,
        "sourceRevisionIdentity": content["sourceRevisionIdentity"],
        "consumerAdmissionVerified": True,
        "status": content["status"],
        "homeScore": content["homeScore"],
        "awayScore": content["awayScore"],
        "sourceCapturedAtUtc": content["sourceCapturedAtUtc"],
        "admittedAtUtc": content["admittedAtUtc"],
        "finalAtUtc": content["finalAtUtc"],
        "clockMeanings": CLOCK_MEANINGS,
        "admission": {
            "rule": content["admissionRule"],
            "sourceState": {
                "aispCanonicalStatus": content["aispCanonicalStatus"],
                "provider": (provider or {}).get("status") if raw_ref else None,
            },
            "note": ("AISP's own admission: the research scorer's final (games status gate + "
                     "best-provenanced FINAL game_state_observation), or AISP's canonical state observed "
                     "at the sealed start. Never inferred from a position, settlement or profit/loss."),
        },
        "reasonCodes": list(dict.fromkeys(reasons)),
        "sourceRefs": source_refs,
        "admissionRefs": admission_refs,
    }


# ---------------------------------------------------------------------------
# Planning and writing
# ---------------------------------------------------------------------------
@dataclass
class Plan:
    systemic: tuple[str, str] | None = None
    #: (kind, payload) in the order they are applied.
    actions: list[tuple[str, Any]] = field(default_factory=list)
    episodes: int = 0


def _last_by_episode(entries: list[dict[str, Any]]) -> tuple[dict[str, dict], dict[str, dict]]:
    latest_revision: dict[str, dict] = {}
    latest_entry: dict[str, dict] = {}
    for entry in entries:
        key = entry.get("episodeKey")
        if not key:
            continue
        if entry.get("kind") == "REVISION_WRITTEN":
            latest_revision[key] = entry
        latest_entry[key] = entry
    return latest_revision, latest_entry


def _plan(session: Session, packet_dir: Path, entries: list[dict[str, Any]]) -> Plan:
    plan = Plan()
    scan = sealed_episodes(packet_dir)
    if scan.systemic:
        plan.systemic = scan.systemic
        last = entries[-1] if entries else {}
        if not (last.get("kind") == "SYSTEMIC_STOP" and (last.get("code"), last.get("detail")) == scan.systemic):
            plan.actions.append(("SYSTEMIC_STOP", scan.systemic))
        return plan
    latest_revision, latest_entry = _last_by_episode(entries)
    for key, code, detail in scan.refused:
        last = latest_entry.get(key) or {}
        if not (last.get("kind") == "SEALED_PACKET_NOT_VERIFIED" and (last.get("code"), last.get("detail"))
                == (code, detail)):
            plan.actions.append(("SEALED_PACKET_NOT_VERIFIED", (key, code, detail)))
    plan.episodes = len(scan.episodes)
    for episode in scan.episodes:
        disposition = admitted_state(session, episode)
        last = latest_entry.get(episode.episode_key) or {}
        if not disposition.is_record:
            if not (last.get("kind") == "NO_RECORD"
                    and (last.get("code"), last.get("detail")) == (disposition.code, disposition.detail)):
                plan.actions.append(("NO_RECORD", disposition))
            continue
        previous = latest_revision.get(episode.episode_key)
        if previous and previous.get("contentId") == disposition.content_id:
            # Unchanged. Only say so when a "nothing admitted" entry came after it.
            if last.get("kind") in ("NO_RECORD", "SEALED_PACKET_NOT_VERIFIED"):
                plan.actions.append(("STATE_RETURNED_TO_LATEST_REVISION", (disposition, previous)))
            continue
        plan.actions.append(("WRITE_REVISION", (disposition, previous.get("revisionId") if previous else None)))
    return plan


def _find_revision(game_dir: Path, revision: str) -> tuple[Path, bytes] | None:
    try:
        candidates = sorted(game_dir.glob("*.json"))
    except OSError:
        return None
    for path in candidates:
        try:
            data = path.read_bytes()
            if json.loads(data).get("revisionId") == revision:
                return path, data
        except (OSError, ValueError, AttributeError):
            continue
    return None


def _write_same(path: Path, data: bytes) -> None:
    """Write-once; an existing file is accepted only when it holds the same bytes."""

    try:
        write_once(path, data)
    except FileExistsError:
        if path.read_bytes() != data:
            raise OutcomeExportRefused(f"{path.name} exists with different bytes; never overwritten")


def _sidecar_path(game_dir: Path, record_sha: str) -> Path:
    """Kept under evidence/ so the game directory holds nothing but <sha>.json records."""

    return game_dir / EVIDENCE_DIR / f"{record_sha}.sha256"


def _sidecar_bytes(record_sha: str, evidence_sha: str | None) -> bytes:
    """sha256sum lines, relative to the sidecar's own directory."""

    lines = [f"{record_sha}  ../{record_sha}.json"]
    if evidence_sha:
        lines.append(f"{evidence_sha}  {evidence_sha}.json")
    return ("\n".join(lines) + "\n").encode("ascii")


def _restore_sidecar(game_dir: Path, record_path: Path, data: bytes) -> None:
    """Write an adopted record's missing .sha256 sidecar (never replaces one)."""

    record_sha = _sha(data)
    if _sidecar_path(game_dir, record_sha).exists():
        return
    evidence_sha = next((ref.get("sha256") for ref in json.loads(data).get("sourceRefs") or []
                         if str(ref.get("path", "")).startswith(f"{EVIDENCE_DIR}/")), None)
    _write_same(_sidecar_path(game_dir, record_sha), _sidecar_bytes(record_sha, evidence_sha))


def write_revision(directory: Path, d: Disposition, supersedes: str | None, *, ledger: AttemptLedger,
                   clock: Callable[[], datetime]) -> dict[str, Any]:
    game_dir = Path(directory) / d.episode.game_id
    revision = revision_id(d.content_id or "", supersedes)
    base = {"kind": "REVISION_WRITTEN", "episodeKey": d.episode.episode_key,
            "canonicalGameId": d.episode.game_id, "revisionId": revision, "supersedesRevisionId": supersedes,
            "contentId": d.content_id, "status": d.status,
            "sourceRevisionIdentity": (d.content or {}).get("sourceRevisionIdentity"),
            "sourceCapturedAtUtc": (d.content or {}).get("sourceCapturedAtUtc"),
            "admittedAtUtc": (d.content or {}).get("admittedAtUtc")}
    existing = _find_revision(game_dir, revision)
    if existing is not None:
        # Written by an earlier tick that stopped before its ledger line.
        path, data = existing
        _restore_sidecar(game_dir, path, data)
        ledger.append({**base, "recordFile": f"{d.episode.game_id}/{path.name}", "recordSha256": _sha(data),
                       "recordBytes": len(data), "adoptedExistingFile": True}, now=pilot.aware(clock()))
        return {"revisionId": revision, "file": path.name, "status": d.status, "adopted": True,
                "supersedesRevisionId": supersedes}

    game_pk = (d.identity or {}).get("sourceGameIds", {}).get("MLB_STATS_API")
    if d.ingestion_code:
        raw_ref, provider, raw_code = None, None, d.ingestion_code
    else:
        raw_ref, provider, raw_code = raw_capture(d.ingestion, d.anchor, game_pk)
    evidence = _evidence_document(d, provider)
    assert_no_pnl_fields(evidence, "evidence")
    evidence_bytes = pilot.canonical_bytes(evidence)
    evidence_sha = _sha(evidence_bytes)
    evidence_rel = f"{EVIDENCE_DIR}/{evidence_sha}.json"
    evidence_ref = {"path": evidence_rel, "sha256": evidence_sha, "bytes": len(evidence_bytes)}
    record = _record(d, revision, supersedes, provider, raw_ref, raw_code, evidence_ref)
    assert_no_pnl_fields(record, "record")
    missing = [k for k in REQUIRED_FIELDS if k not in record]
    if missing:
        raise OutcomeExportRefused(f"record lacks required fields {missing}")
    record_bytes = pilot.canonical_bytes(record)
    record_sha = _sha(record_bytes)
    _write_same(game_dir / evidence_rel, evidence_bytes)
    _write_same(game_dir / f"{record_sha}.json", record_bytes)
    _write_same(_sidecar_path(game_dir, record_sha), _sidecar_bytes(record_sha, evidence_sha))
    ledger.append({**base, "recordFile": f"{d.episode.game_id}/{record_sha}.json", "recordSha256": record_sha,
                   "recordBytes": len(record_bytes), "evidenceFile": f"{d.episode.game_id}/{evidence_rel}",
                   "evidenceSha256": evidence_sha, "reasonCodes": record["reasonCodes"]},
                  now=pilot.aware(clock()))
    return {"revisionId": revision, "file": f"{record_sha}.json", "status": d.status,
            "supersedesRevisionId": supersedes}


# ---------------------------------------------------------------------------
# The scheduled tick
# ---------------------------------------------------------------------------
def _overlap(outcomes_dir: Path, packet_dir: Path) -> bool:
    a, b = Path(outcomes_dir).resolve(), Path(packet_dir).resolve()
    return a == b or b in a.parents or a in b.parents


def work_due(session: Session, settings: Settings) -> bool:
    """Is a record, a reason or a stop due? Read-only; writes nothing, creates no job row."""

    if not settings.v18_market_pilot_outcomes_export_enabled:
        return False
    directory = Path(settings.v18_market_pilot_outcomes_dir)
    packet_dir = Path(settings.v18_market_pilot_packet_dir)
    if _overlap(directory, packet_dir):
        return False
    entries = AttemptLedger(directory, name=LEDGER_NAME).entries()
    with read_only(session):
        plan = _plan(session, packet_dir, entries)
    return bool(plan.actions)


def run_outcomes_tick(session: Session, settings: Settings, *, now: datetime | None = None,
                      clock: Callable[[], datetime] | None = None) -> dict[str, Any]:
    """Export every due admitted-outcome revision for sealed pilot episodes."""

    if not settings.v18_market_pilot_outcomes_export_enabled:
        return {"records": 0, "enabled": False,
                "note": "V18_MARKET_PILOT_OUTCOMES_EXPORT_ENABLED is off; nothing read or written"}
    clock = clock or (lambda: datetime.now(UTC))
    directory = Path(settings.v18_market_pilot_outcomes_dir)
    packet_dir = Path(settings.v18_market_pilot_packet_dir)
    if _overlap(directory, packet_dir):
        logger.error("v18_market_pilot_outcomes_dir_overlaps_packets", outcomes=str(directory),
                     packets=str(packet_dir))
        return {"records": 0, "enabled": True, "refused": "OUTCOMES_DIR_OVERLAPS_PACKET_DIR"}
    ledger = AttemptLedger(directory, name=LEDGER_NAME)
    with read_only(session):
        plan = _plan(session, packet_dir, ledger.entries())
    summary: dict[str, Any] = {"enabled": True, "protocolSha256": pilot.PROTOCOL_SHA256,
                               "directory": str(directory), "sealedEpisodes": plan.episodes, "written": [],
                               "noRecord": [], "notVerified": [], "systemic": plan.systemic}
    for kind, payload in plan.actions:
        at = pilot.aware(now or clock())
        if kind == "SYSTEMIC_STOP":
            code, detail = payload
            ledger.append({"kind": "SYSTEMIC_STOP", "code": code, "detail": detail}, now=at)
            logger.error("v18_market_pilot_outcomes_systemic_stop", code=code, detail=detail)
        elif kind == "SEALED_PACKET_NOT_VERIFIED":
            key, code, detail = payload
            ledger.append({"kind": kind, "episodeKey": key, "code": code, "detail": detail}, now=at)
            summary["notVerified"].append({"episodeKey": key, "code": code})
        elif kind == "NO_RECORD":
            d = payload
            ledger.append({"kind": "NO_RECORD", "episodeKey": d.episode.episode_key,
                           "canonicalGameId": d.episode.game_id, "code": d.code, "detail": d.detail}, now=at)
            summary["noRecord"].append({"episodeKey": d.episode.episode_key, "code": d.code})
        elif kind == "STATE_RETURNED_TO_LATEST_REVISION":
            d, previous = payload
            ledger.append({"kind": kind, "episodeKey": d.episode.episode_key, "canonicalGameId": d.episode.game_id,
                           "revisionId": previous.get("revisionId"), "contentId": d.content_id}, now=at)
        elif kind == "WRITE_REVISION":
            d, supersedes = payload
            summary["written"].append({"episodeKey": d.episode.episode_key,
                                       **write_revision(directory, d, supersedes, ledger=ledger, clock=clock)})
    summary["records"] = len(summary["written"])
    return summary


__all__ = [
    "DECISIVE", "EXPORT_IDENTITY", "INTERVAL_MINUTES", "JOB_NAME", "LEDGER_NAME", "NO_CONTEST", "PENDING",
    "RECORD_TYPE", "REQUIRED_FIELDS", "SCHEDULER_ID", "STATUSES", "TIE", "VOID", "Disposition",
    "OutcomeExportRefused", "SealedEpisode", "admitted_state", "assert_no_pnl_fields", "read_only",
    "revision_id", "run_outcomes_tick", "sealed_episodes", "work_due", "write_revision",
]

"""V2 consumer outcome export: source-confirmed terminal, capture-bound.

WHAT THIS IS
------------
The consumer side of Model Studio's accepted outcome amendment
``7508ae728cc852812607c68222587f382b7d722b4a03d6a1f780033a5c6ebf2f``
(``MLB_V18_MARKET_SOURCE_CONFIRMED_TERMINAL_BOUND_V2``; contract ``CONTRACT.md``
sha256 ``99c07ea9...``). James accepted it on 2026-09-19. It authorizes only
the outcome contract.

Its one scientific change: a genuinely source-confirmed terminal state may
carry ``finalAtUtc: null`` with ``finalAtBasis:
SOURCE_CONFIRMED_TERMINAL_COMPLETION_TIME_UNAVAILABLE``. Availability by an
assessment cutoff is established instead by the retained capture, archive and
successful admission bounds. None of those clocks is ever described as the time
play ended.

The V1 export (:mod:`app.research_exports.v18_market_pilot_outcomes`) stays
exactly as it is: the ``consumer-outcomes`` lane, the withheld lane and its
strict null-clock refusal. This module writes a separate lane and never moves,
overwrites or relabels a V1 or withheld revision:

    <v18_market_pilot_outcomes_v2_dir>/<canonicalGameId>/<physical-sha256>.json
    <v18_market_pilot_outcomes_v2_dir>/<canonicalGameId>/evidence/<physical-sha256>.json
    <v18_market_pilot_outcomes_v2_dir>/outcomes-ledger.jsonl

Every file is write-once and hash-named. A correction is a new revision that
names ``supersedesRevisionId``. Each record is exactly the object Model
Studio's reference adapter ``build_capture_bound_outcome`` rebuilds from its
evidence (see the compatibility test).

WHAT IS ADMITTED (everything else stays UNRESOLVED in the V2 ledger, with a reason)
-----------------------------------------------------------------------------------
The starting point is the same AISP admission as V1
(:func:`v18_market_pilot_outcomes.admitted_state`): only authentic SEALED
packets, identity re-derived and equal to the packet, the research scorer's
final or AISP's canonical postponed/canceled state at the sealed start S, and
never a position or P/L. V2 then also requires all of the following.

1. **Terminal.** DECISIVE, TIE, VOID (postponed at S) or NO_CONTEST (canceled at
   S). PENDING is never exported to V2. ``GAME_OVER`` is not an official final:
   the retained provider state must be ``Final*`` or ``Completed*`` for
   DECISIVE/TIE, ``Postponed`` for VOID and ``Cancelled`` for NO_CONTEST.
2. **Schedule UNCHANGED.** The admitting observation's actual ``scheduled_at``
   equals the sealed start. A resumed game would additionally need physical
   original-play and explicit source-resumption evidence, which AISP does not
   build, so it stays unresolved. A makeup is never substituted.
3. **Verified from the original source.** The retained raw ``/schedule``
   capture named by the ingestion run is read. It must hold the gamePk exactly
   once, with AISP's canonical status and both scores. Re-deriving AISP's
   schedule DTO from it must reproduce the observation's ``payload_checksum``
   exactly. (A read-only check reproduced 400 of 400 live observations of
   2026-09-18/19.)
4. **Capture receipt.** Basis ``RETAINED_SOURCE_FETCH_COMPLETED``.
   ``sourceCapturedAtUtc`` is the observation's ``available_as_of`` (the fetch
   clock). ``archivedAtUtc`` is the raw file's own archive stamp, which is
   later. The ingestion run is the observation's own foreign key. Where that
   key is NULL, as it is for every ``refresh-schedule`` observation, the key
   stays null and the link is ``UNIQUE_RETAINED_SOURCE_AND_EXACT_FETCH_CLOCK``:
   exactly one retained SUCCEEDED ``refresh-schedule`` ingestion run with the
   same source, the same raw reference (verified above) and ``started_at``
   equal to the fetch clock.
5. **Admission receipt.** Exactly one job run is found among the known
   observation writers whose execution window contains the fetch clock and the
   inserting transaction's start. It must be SUCCEEDED and completed. Its name
   must be a reviewed parent of the ingestion job: ``refresh-schedule`` within
   ``refresh-schedule``, ``finalize-games`` or ``daily-pipeline``. Missing,
   failed, in-flight, ambiguous or unknown-parent writers stay unresolved.
   ``commitBoundBasis`` is ``SUCCESSFUL_MATCHED_JOB_COMPLETED``: a conservative
   commit bound, not a foreign key and not an exact commit time.
6. **Chronology.** capture <= archive <= admission bound <= ``admittedAtUtc``
   <= now, with capture strictly before 2026-10-01T00:00Z. For DECISIVE/TIE the
   capture is also at or after the sealed start. ``admittedAtUtc`` is the
   admission bound itself.

``finalAtUtc`` is the source's own completion field and nothing else. The MLB
schedule feed has none, and no retained live feed carries
``gameData.datetime.gameEndTime``. So it is null, with the null basis. Last
play, scheduled start, snapshot stamps, file times and ``created_at`` are never
used.

EVIDENCE
--------
Every record points (``terminalEvidenceRef``) to an
``MLB_V18_MARKET_TERMINAL_EVIDENCE_V2`` document. That document repeats the
identity and revision fields and holds ``sourceProjection``, ``captureReceipt``
and ``admissionReceipt``. Its RFC 6901 ``observationRef``, ``ingestionRef`` and
``jobRef`` point into a second hash-named document in the same ``evidence/``
directory. That second document holds the retained admitting rows (with the
observation's actual ``game_id``). All evidence paths are absolute and resolve
inside that game's ``evidence/`` directory. Raw archive references are
disclosed but not copied.

The module never calls a provider, reads the database only inside a READ ONLY
transaction that is rolled back, and never writes into the packets, V1 or
withheld directories.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import logger
from app.models import GameStateObservation, IngestionRun, JobRun, RunStatus
from app.research_exports import v18_market_pilot as pilot
from app.research_exports import v18_market_pilot_outcomes as v1
from app.research_exports.v18_market_pilot_seal import AttemptLedger

AMENDMENT_SHA256 = "7508ae728cc852812607c68222587f382b7d722b4a03d6a1f780033a5c6ebf2f"
AMENDMENT_ID = "MLB_V18_MARKET_SOURCE_CONFIRMED_TERMINAL_BOUND_V2"
CONTRACT_SHA256 = "99c07ea9cdef4f7e4299b9e35e1288983243512c089c62bc68dd322230619395"
RECORD_TYPE = "MLB_V18_MARKET_ADMITTED_OUTCOME_V2"
EVIDENCE_TYPE = "MLB_V18_MARKET_TERMINAL_EVIDENCE_V2"
ROWS_TYPE = "AISP_V18_MARKET_RETAINED_ADMISSION_ROWS_V2"
EXPORT_IDENTITY = "AISP_V18_MARKET_PILOT_CONSUMER_OUTCOME_EXPORT_V2"
NULL_BASIS = "SOURCE_CONFIRMED_TERMINAL_COMPLETION_TIME_UNAVAILABLE"
CAPTURE_BASIS = "RETAINED_SOURCE_FETCH_COMPLETED"
LINK_FOREIGN_KEY = "OBSERVATION_INGESTION_FOREIGN_KEY"
LINK_UNIQUE_FETCH_CLOCK = "UNIQUE_RETAINED_SOURCE_AND_EXACT_FETCH_CLOCK"
RECEIPT_LINKAGE = "UNIQUE_SUCCESSFUL_SOURCE_WRITER_WINDOW"
COMMIT_BOUND_BASIS = "SUCCESSFUL_MATCHED_JOB_COMPLETED"
SCHEDULE_UNCHANGED = "UNCHANGED"
#: The reviewed writer mapping: ingestion job -> parent jobs that call it.
INGESTION_JOB = "refresh-schedule"
WRITER_PARENTS = frozenset({"refresh-schedule", "finalize-games", "daily-pipeline"})
CAPTURE_AUTHORITY_EXPIRES = datetime(2026, 10, 1, tzinfo=UTC)
LEDGER_NAME = "outcomes-ledger.jsonl"
EVIDENCE_DIR = "evidence"
JOB_NAME = "export-v18-market-pilot-outcomes-v2"
SCHEDULER_ID = "asp-rx-v18-market-pilot-outcomes-v2"
INTERVAL_MINUTES = 10

TERMINAL = frozenset({v1.DECISIVE, v1.TIE, v1.VOID, v1.NO_CONTEST})
#: Normalised provider state each admitted status may rest on (reference adapter's table).
SOURCE_STATUS_FOR = {
    v1.DECISIVE: frozenset({"FINAL", "COMPLETED"}), v1.TIE: frozenset({"FINAL", "COMPLETED"}),
    v1.VOID: frozenset({"POSTPONED"}), v1.NO_CONTEST: frozenset({"CANCELLED"}),
}

#: The keys of a V2 record: exactly what ``build_capture_bound_outcome`` returns.
RECORD_FIELDS = (
    "gameId", "homeId", "awayId", "sourceGameIds", "episodeKey", "packetSha256", "scheduledStartUtc",
    "revisionId", "sourceRevisionIdentity", "recordType", "baseProtocolSha256", "outcomeAmendmentSha256",
    "consumerAdmissionVerified", "sourceConfirmedTerminal", "sourceCapturedAtUtc", "admittedAtUtc",
    "finalAtUtc", "finalAtBasis", "status", "homeScore", "awayScore", "terminalEvidenceRef",
    "supersedesRevisionId",
)

CLOCK_MEANINGS = {
    "sourceCapturedAtUtc": "MLB /schedule fetch completed (envelope.fetched_at = observation available_as_of "
                           "= ingestion_runs.started_at); an availability bound, not when play ended",
    "archivedAtUtc": "the retained raw file's own archive stamp (metadata.ingestion_timestamp), written after the "
                     "fetch",
    "admittedAtUtc": "the successful matched writer job's completed_at: a conservative commit bound "
                     "(SUCCESSFUL_MATCHED_JOB_COMPLETED), not a foreign key and not an exact commit time",
    "finalAtUtc": "the source's own completion field only; null with SOURCE_CONFIRMED_TERMINAL_COMPLETION_TIME_"
                  "UNAVAILABLE because the MLB schedule feed has none. Never last play, scheduled start, "
                  "snapshot stamp, file time or created_at",
    "created_at_transaction_start": "PostgreSQL now() at the inserting transaction's start; used only to "
                                    "confirm the writer window, never as a clock of admission",
}


class Unresolved(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _require(condition: bool, code: str, detail: str = "") -> None:
    if not condition:
        raise Unresolved(code, detail)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pointer(*tokens: str) -> str:
    """An RFC 6901 JSON pointer."""

    return "".join("/" + str(t).replace("~", "~0").replace("/", "~1") for t in tokens)


def source_status(detailed_state: str | None) -> str:
    """The provider's own terminal state, normalised; ``GAME_OVER`` is not a final."""

    text = str(detailed_state or "").strip().casefold()
    if text.startswith("final"):
        return "FINAL"
    if text.startswith("completed"):
        return "COMPLETED"
    if text.startswith("game over"):
        return "GAME_OVER"
    if text.startswith("postponed"):
        return "POSTPONED"
    if text.startswith("cancelled") or text.startswith("canceled"):
        return "CANCELLED"
    return "NOT_TERMINAL"


# ---------------------------------------------------------------------------
# Retained rows (read-only), with the observation's actual game_id
# ---------------------------------------------------------------------------
def _observation_row(session: Session, observation_id: str) -> dict[str, Any]:
    r = session.execute(
        select(GameStateObservation.id, GameStateObservation.game_id, GameStateObservation.home_team_id,
               GameStateObservation.away_team_id, GameStateObservation.scheduled_at, GameStateObservation.status,
               GameStateObservation.is_postponed, GameStateObservation.home_score, GameStateObservation.away_score,
               GameStateObservation.final_at, GameStateObservation.observed_at, GameStateObservation.available_as_of,
               GameStateObservation.created_at, GameStateObservation.source, GameStateObservation.source_mode,
               GameStateObservation.source_record_id, GameStateObservation.payload_checksum,
               GameStateObservation.ingestion_run_id)
        .where(GameStateObservation.id == pilot._uuid(observation_id))
    ).one()
    return {
        "id": str(r.id), "game_id": str(r.game_id), "home_team_id": v1._opt(r.home_team_id),
        "away_team_id": v1._opt(r.away_team_id), "scheduled_at": pilot.iso(r.scheduled_at),
        "status": str(r.status or "").lower(), "is_postponed": bool(r.is_postponed), "home_score": r.home_score,
        "away_score": r.away_score, "final_at": pilot.iso(r.final_at), "observed_at": pilot.iso(r.observed_at),
        "available_as_of": pilot.iso(r.available_as_of), "created_at_transaction_start": pilot.iso(r.created_at),
        "source": r.source, "source_mode": r.source_mode, "source_record_id": r.source_record_id,
        "payload_checksum": r.payload_checksum, "ingestion_run_id": v1._opt(r.ingestion_run_id),
    }


def _ingestion_row(r: Any) -> dict[str, Any]:
    return {"id": str(r.id), "source": r.source, "job_name": r.job_name,
            "status": getattr(r.status, "value", r.status), "started_at": pilot.iso(r.started_at),
            "completed_at_before_commit": pilot.iso(r.completed_at), "raw_reference": r.raw_reference,
            "records_processed": r.records_processed}


def _succeeded(status: Any) -> bool:
    return str(getattr(status, "value", status) or "").lower() == RunStatus.SUCCEEDED.value


def capture_link(session: Session, observation: dict[str, Any]) -> tuple[dict[str, Any], str, list[str]]:
    """(ingestion row, linkage basis, candidate ids). Never invents a foreign key."""

    fetched = pilot.aware(observation["available_as_of"])
    columns = (IngestionRun.id, IngestionRun.source, IngestionRun.job_name, IngestionRun.status,
               IngestionRun.started_at, IngestionRun.completed_at, IngestionRun.raw_reference,
               IngestionRun.records_processed)
    if observation["ingestion_run_id"]:
        rows = session.execute(select(*columns).where(
            IngestionRun.id == pilot._uuid(observation["ingestion_run_id"]))).all()
        _require(len(rows) == 1, "V2_INGESTION_FOREIGN_KEY_ROW_MISSING", observation["ingestion_run_id"])
        basis, candidates = LINK_FOREIGN_KEY, rows
    else:
        found = session.execute(select(*columns).where(
            IngestionRun.source == observation["source"],
            IngestionRun.started_at >= fetched - timedelta(seconds=1),
            IngestionRun.started_at <= fetched + timedelta(seconds=1))).all()
        candidates = [r for r in found if pilot.aware(r.started_at) == fetched and r.raw_reference]
        _require(len(candidates) >= 1, "V2_INGESTION_LINK_NOT_FOUND", "no retained run at the exact fetch clock")
        _require(len(candidates) == 1, "V2_INGESTION_LINK_AMBIGUOUS", f"{len(candidates)} runs at the fetch clock")
        basis = LINK_UNIQUE_FETCH_CLOCK
    row = _ingestion_row(candidates[0])
    _require(_succeeded(candidates[0].status), "V2_INGESTION_RUN_NOT_SUCCESSFUL", row["status"])
    _require(row["job_name"] == INGESTION_JOB, "V2_INGESTION_JOB_UNSUPPORTED", row["job_name"])
    _require(bool(row["raw_reference"]), "V2_INGESTION_RAW_REFERENCE_MISSING")
    _require(row["source"] == observation["source"], "V2_INGESTION_SOURCE_MISMATCH")
    _require(pilot.aware(row["started_at"]) == fetched, "V2_INGESTION_FETCH_CLOCK_MISMATCH")
    return row, basis, [row["id"]]


def admission_job(session: Session, observation: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The one successful reviewed writer whose window holds the fetch and the insert."""

    fetched = pilot.aware(observation["available_as_of"])
    created = pilot.aware(observation["created_at_transaction_start"])
    writers = sorted(v1.OBSERVATION_WRITER_JOBS | WRITER_PARENTS)
    rows = session.execute(
        select(JobRun.id, JobRun.job_name, JobRun.status, JobRun.started_at, JobRun.completed_at)
        .where(JobRun.job_name.in_(writers), JobRun.started_at >= fetched - v1.WRITER_RUN_LOOKBACK,
               JobRun.started_at <= fetched + timedelta(seconds=1))
        .order_by(JobRun.started_at, JobRun.id)
    ).all()
    containing = []
    for r in rows:
        started, completed = pilot.aware(r.started_at), pilot.aware(r.completed_at)
        if started is None or started > fetched or (created is not None and started > created):
            continue
        if completed is not None and (completed < fetched or (created is not None and completed < created)):
            continue
        containing.append(r)
    ids = [str(r.id) for r in containing]
    _require(bool(containing), "V2_ADMISSION_JOB_NOT_FOUND", "no writer run contains the fetch clock")
    _require(len(containing) == 1, "V2_ADMISSION_JOB_AMBIGUOUS", ",".join(ids))
    job = containing[0]
    _require(job.job_name in WRITER_PARENTS, "V2_ADMISSION_JOB_PARENT_UNSUPPORTED", job.job_name)
    _require(job.completed_at is not None, "V2_ADMISSION_JOB_IN_FLIGHT", str(job.id))
    _require(_succeeded(job.status), "V2_ADMISSION_JOB_NOT_SUCCESSFUL",
             f"{job.id} {getattr(job.status, 'value', job.status)}")
    return {"id": str(job.id), "job_name": job.job_name, "status": getattr(job.status, "value", job.status),
            "started_at": pilot.iso(job.started_at), "completed_at": pilot.iso(job.completed_at)}, ids


# ---------------------------------------------------------------------------
# The original source, verified
# ---------------------------------------------------------------------------
def recompute_schedule_checksum(item: dict[str, Any], event_date: date, people: Any) -> str:
    """AISP's observation ``payload_checksum``, re-derived from the retained raw schedule item.

    The same DTO construction the MLB adapter performs and the same checksum
    the normalizer stores. A mismatch means the capture is not the admitting
    source, so the outcome stays unresolved.
    """

    from app.data_sources.base import ScheduleGameDTO, VenueDTO
    from app.data_sources.mlb import MLBStatsAPIAdapter, _to_int
    from app.ingestion.normalizer import _checksum

    hands = {str(p["id"]): str((p.get("pitchHand") or {}).get("code", "")).upper()
             for p in (people or {}).get("people", []) or []
             if p.get("id") is not None and (p.get("pitchHand") or {}).get("code")}
    teams = item.get("teams") or {}
    venue = item.get("venue") or {"name": "Venue unavailable"}
    dto = ScheduleGameDTO(
        source_game_id=str(item["gamePk"]), scheduled_at=item["gameDate"], event_date=event_date,
        status=(item.get("status") or {}).get("detailedState", "Scheduled"),
        away_team=MLBStatsAPIAdapter._team(teams.get("away") or {}),
        home_team=MLBStatsAPIAdapter._team(teams.get("home") or {}),
        away_pitcher=MLBStatsAPIAdapter._pitcher(teams.get("away") or {}, hands),
        home_pitcher=MLBStatsAPIAdapter._pitcher(teams.get("home") or {}, hands),
        venue=VenueDTO(source_id=str(venue.get("id", "")) or None, name=venue["name"]),
        away_score=_to_int((teams.get("away") or {}).get("score")),
        home_score=_to_int((teams.get("home") or {}).get("score")),
        final_at=None, doubleheader_status=item.get("doubleHeader"), game_number=_to_int(item.get("gameNumber")),
        scheduled_innings=_to_int(item.get("scheduledInnings")) or 9,
    )
    payload = ScheduleGameDTO.model_validate(dto.model_dump(mode="json")).model_dump(mode="json")
    return _checksum(payload)


def verify_original_source(raw_reference: str, game_pk: str, observation: dict[str, Any],
                           packet_game: dict[str, Any]) -> dict[str, Any]:
    """Open the retained raw capture once; prove it is the admitting source for this game."""

    path = Path(raw_reference)
    try:
        data = path.read_bytes()
        document = json.loads(data)
    except (OSError, ValueError) as error:
        raise Unresolved("V2_ORIGINAL_SOURCE_UNREADABLE", type(error).__name__) from None
    metadata = document.get("metadata") or {}
    payload = document.get("payload")
    dates = ((payload.get("schedule") or {}).get("dates") or []) if isinstance(payload, dict) else []
    found = [(i, j, day, g) for i, day in enumerate(dates) for j, g in enumerate(day.get("games") or [])
             if str(g.get("gamePk")) == str(game_pk)]
    _require(len(found) == 1, "V2_ORIGINAL_SOURCE_GAME_NOT_UNIQUE", f"{len(found)} entries for gamePk {game_pk}")
    i, j, day, item = found[0]
    _require(str(metadata.get("source") or "") == observation["source"], "V2_ORIGINAL_SOURCE_NOT_THIS_SOURCE")
    status = item.get("status") or {}
    teams = item.get("teams") or {}
    scores = (v1._int_or_none((teams.get("home") or {}).get("score")),
              v1._int_or_none((teams.get("away") or {}).get("score")))
    _require(v1.aisp_canonical_status(str(status.get("detailedState") or "Scheduled")).value == observation["status"],
             "V2_ORIGINAL_SOURCE_STATUS_DISAGREES", str(status.get("detailedState")))
    _require(scores == (observation["home_score"], observation["away_score"]), "V2_ORIGINAL_SOURCE_SCORES_DISAGREE")
    for side, key in (("home", "homeSourceIds"), ("away", "awaySourceIds")):
        sealed = (packet_game.get(key) or {}).get("MLB_STATS_API")
        actual = str(((teams.get(side) or {}).get("team") or {}).get("id") or "")
        _require(not sealed or not actual or str(sealed) == actual, "V2_ORIGINAL_SOURCE_TEAM_DISAGREES", side)
    try:
        event_date = date.fromisoformat(str(day.get("date")))
        reproduced = recompute_schedule_checksum(item, event_date, payload.get("people"))
    except Exception as error:  # noqa: BLE001 -- any failure to re-derive is an unresolved source
        raise Unresolved("V2_ORIGINAL_SOURCE_CHECKSUM_NOT_REPRODUCIBLE", type(error).__name__) from None
    _require(reproduced == observation["payload_checksum"], "V2_ORIGINAL_SOURCE_CHECKSUM_NOT_REPRODUCED")
    archived = pilot.aware(metadata.get("ingestion_timestamp"))
    _require(archived is not None, "V2_ORIGINAL_SOURCE_ARCHIVE_STAMP_MISSING")
    return {
        "ref": {"path": raw_reference, "sha256": _sha(data), "bytes": len(data)},
        "locator": pointer("payload", "schedule", "dates", str(i), "games", str(j)),
        "archivedAt": archived,
        "archivePayloadChecksum": metadata.get("checksum"),
        "providerStatus": {k: status.get(k) for k in v1._PROVIDER_STATUS_KEYS},
        "providerSchedule": {k: item.get(k) for k in v1._PROVIDER_SCHEDULE_KEYS},
        "sourceStatus": source_status(status.get("detailedState")),
        "gamePk": item.get("gamePk"),
        "eventDate": str(day.get("date")),
    }


# ---------------------------------------------------------------------------
# One episode
# ---------------------------------------------------------------------------
@dataclass
class Built:
    episode: v1.SealedEpisode
    status: str
    content: dict[str, Any]
    content_id: str
    admission_key: str
    observation: dict[str, Any]
    ingestion: dict[str, Any]
    linkage_basis: str
    ingestion_candidates: list[str]
    job: dict[str, Any]
    job_candidates: list[str]
    source: dict[str, Any]


@dataclass
class V2Disposition:
    episode: v1.SealedEpisode
    built: Built | None = None
    code: str | None = None
    detail: str = ""
    admission_key: str | None = None


def admission_key(parts: dict[str, Any]) -> str:
    return _sha(pilot.canonical_bytes(parts))


def build_episode(session: Session, episode: v1.SealedEpisode) -> V2Disposition:
    """Everything V2 needs for one sealed episode, or the exact reason it stays unresolved. Reads only."""

    d = v1.admitted_state(session, episode)
    if not d.is_record:
        return V2Disposition(episode, code=f"V2_NO_V1_ADMISSION:{d.code}", detail=d.detail)
    try:
        _require(d.status in TERMINAL, "V2_NOT_SOURCE_CONFIRMED_TERMINAL", d.status or "")
        observation = _observation_row(session, d.anchor.id)
        _require(observation["game_id"] == episode.game_id, "V2_OBSERVATION_GAME_IDENTITY_MISMATCH")
        _require(pilot.aware(observation["scheduled_at"]) == episode.scheduled_start,
                 "V2_SCHEDULE_NOT_UNCHANGED_RESUMPTION_EVIDENCE_NOT_ESTABLISHED",
                 f"observation scheduled_at {observation['scheduled_at']}")
        ingestion, basis, ingestion_ids = capture_link(session, observation)
        job, job_ids = admission_job(session, observation)
        key = admission_key({"observation": observation["id"], "checksum": observation["payload_checksum"],
                             "status": d.status, "ingestion": ingestion["id"], "job": job["id"],
                             "jobCompletedAt": job["completed_at"], "packetSha256": episode.packet_ref["sha256"]})
    except Unresolved as error:
        return V2Disposition(episode, code=error.code, detail=error.detail)
    return V2Disposition(episode, admission_key=key, built=Built(
        episode=episode, status=d.status, content={}, content_id="", admission_key=key, observation=observation,
        ingestion=ingestion, linkage_basis=basis, ingestion_candidates=ingestion_ids, job=job,
        job_candidates=job_ids, source={}))


def complete(built: Built, *, now: datetime) -> None:
    """Verify the original source and the chronology; fill the admitted content. Raises Unresolved."""

    episode, observation, job = built.episode, built.observation, built.job
    packet_game = episode.packet.get("game") or {}
    game_pk = (packet_game.get("sourceGameIds") or {}).get("MLB_STATS_API")
    _require(bool(game_pk) and isinstance(game_pk, str), "V2_SOURCE_GAME_ID_NOT_UNIQUE")
    source = verify_original_source(built.ingestion["raw_reference"], game_pk, observation, packet_game)
    _require(source["sourceStatus"] != "GAME_OVER", "V2_GAME_OVER_IS_NOT_OFFICIAL_FINAL")
    _require(source["sourceStatus"] in SOURCE_STATUS_FOR[built.status], "V2_SOURCE_STATE_NOT_TERMINAL_FOR_STATUS",
             f"{built.status} from {source['providerStatus'].get('detailedState')}")
    if built.status in v1.PLAYED_STATUSES:
        _require(observation["status"] == "final", "V2_ADMITTED_OBSERVATION_NOT_FINAL")
        _require(isinstance(observation["home_score"], int) and isinstance(observation["away_score"], int),
                 "V2_FINAL_SCORES_MISSING")
    final_at = observation["final_at"]
    _require(final_at is None, "V2_SOURCE_COMPLETION_FIELD_NOT_ESTABLISHED",
             "a non-null final_at would need a genuine gameEndTime source field")

    captured = pilot.aware(observation["available_as_of"])
    archived = source["archivedAt"]
    started, completed = pilot.aware(job["started_at"]), pilot.aware(job["completed_at"])
    _require(captured < CAPTURE_AUTHORITY_EXPIRES, "V2_SOURCE_CAPTURE_AUTHORITY_EXPIRED")
    _require(started <= captured <= completed, "V2_ADMISSION_WINDOW_DOES_NOT_CONTAIN_CAPTURE")
    _require(captured <= archived <= completed <= pilot.aware(now), "V2_CHRONOLOGY_INVALID",
             f"capture {pilot.iso(captured)} archive {pilot.iso(archived)} admission {pilot.iso(completed)}")
    if built.status in v1.PLAYED_STATUSES:
        _require(captured >= episode.scheduled_start, "V2_FINAL_CAPTURE_PRECEDES_ORIGINAL_START")

    built.source = source
    built.content = {
        "recordType": RECORD_TYPE, "baseProtocolSha256": pilot.PROTOCOL_SHA256,
        "outcomeAmendmentSha256": AMENDMENT_SHA256, "gameId": episode.game_id,
        "sourceGameIds": packet_game.get("sourceGameIds"), "homeId": packet_game.get("homeId"),
        "awayId": packet_game.get("awayId"), "episodeKey": episode.episode_key,
        "packetSha256": episode.packet_ref["sha256"], "scheduledStartUtc": packet_game.get("scheduledStartUtc"),
        "status": built.status, "homeScore": observation["home_score"], "awayScore": observation["away_score"],
        "sourceRevisionIdentity": f"{observation['source']}:{observation['payload_checksum']}",
        "sourceCapturedAtUtc": pilot.iso(captured), "admittedAtUtc": pilot.iso(completed), "finalAtUtc": None,
        "finalAtBasis": NULL_BASIS, "sourceStatus": source["sourceStatus"], "archivedAtUtc": pilot.iso(archived),
        "ingestionRunId": built.ingestion["id"], "jobId": job["id"],
        "originalSourceSha256": source["ref"]["sha256"],
    }
    built.content_id = _sha(pilot.canonical_bytes(built.content))


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
def _rows_document(built: Built) -> dict[str, Any]:
    return {
        "recordType": ROWS_TYPE, "exportIdentity": EXPORT_IDENTITY, "gameId": built.episode.game_id,
        "episodeKey": built.episode.episode_key,
        "note": "Retained admitting rows, identity/score/status/clock columns only, as read from AISP's database "
                "inside a READ ONLY transaction. created_at is a transaction start, never a commit or admission.",
        "game_state_observations": {built.observation["id"]: built.observation},
        "ingestion_runs": {built.ingestion["id"]: built.ingestion},
        "job_runs": {built.job["id"]: built.job},
    }


def _ref(path: Path, data: bytes, locator: str | None = None) -> dict[str, Any]:
    ref = {"path": str(path.resolve()), "sha256": _sha(data), "bytes": len(data)}
    if locator is not None:
        ref["locator"] = locator
    return ref


def _evidence_document(built: Built, revision: str, supersedes: str | None, rows_path: Path,
                       rows_bytes: bytes) -> dict[str, Any]:
    c, source, observation, ingestion, job = built.content, built.source, built.observation, built.ingestion, built.job
    identity = {k: c[k] for k in ("gameId", "homeId", "awayId", "sourceGameIds", "episodeKey", "packetSha256",
                                  "scheduledStartUtc")}
    return {
        "recordType": EVIDENCE_TYPE, **identity,
        "revisionId": revision, "sourceRevisionIdentity": c["sourceRevisionIdentity"],
        "supersedesRevisionId": supersedes, "contentId": built.content_id,
        "baseProtocolSha256": pilot.PROTOCOL_SHA256, "outcomeAmendmentSha256": AMENDMENT_SHA256,
        "amendmentId": AMENDMENT_ID, "contractSha256": CONTRACT_SHA256, "exportIdentity": EXPORT_IDENTITY,
        "pregamePacketRef": built.episode.packet_ref,
        "scheduleDisposition": SCHEDULE_UNCHANGED, "originalScheduledStartUtc": c["scheduledStartUtc"],
        "clockMeanings": CLOCK_MEANINGS,
        "sourceProjection": {
            "verifiedFromOriginalSource": True,
            "verification": {
                "method": "retained raw /schedule capture read once by the consumer; gamePk found exactly once; "
                          "AISP canonical status and both scores agree; AISP's schedule DTO re-derived from the "
                          "item reproduces the observation payload_checksum",
                "payloadChecksumReproduced": True,
                "authority": "consumer source admission, not an independent raw-source replay",
            },
            "sourceConfirmedTerminal": True, "sourceStatus": source["sourceStatus"],
            "providerStatus": source["providerStatus"], "providerSchedule": source["providerSchedule"],
            "canonicalStatus": observation["status"], "status": c["status"], "homeScore": c["homeScore"],
            "awayScore": c["awayScore"], "finalAtUtc": None, "completionField": None,
            "completionClockSemantics": "UNAVAILABLE_IN_SOURCE",
            "sourceGameIds": c["sourceGameIds"], "sourceRevisionIdentity": c["sourceRevisionIdentity"],
            "originalSourceRef": source["ref"], "sourceLocator": source["locator"],
            "payloadChecksum": observation["payload_checksum"],
            "archivePayloadChecksum": source["archivePayloadChecksum"], "gamePk": source["gamePk"],
            "eventDate": source["eventDate"],
        },
        "captureReceipt": {
            "basis": CAPTURE_BASIS, "sourceCapturedAtUtc": c["sourceCapturedAtUtc"],
            "archivedAtUtc": c["archivedAtUtc"], "originalSourceSha256": source["ref"]["sha256"],
            "ingestionRunId": ingestion["id"], "linkageBasis": built.linkage_basis,
            "candidateIngestionRunIds": built.ingestion_candidates,
            "observationIngestionForeignKey": observation["ingestion_run_id"],
            "observationRef": _ref(rows_path, rows_bytes, pointer("game_state_observations", observation["id"])),
            "ingestionRef": _ref(rows_path, rows_bytes, pointer("ingestion_runs", ingestion["id"])),
        },
        "admissionReceipt": {
            "status": "SUCCESS", "commitBoundBasis": COMMIT_BOUND_BASIS,
            "commitBoundMeaning": "a single successful containing writer window; a conservative commit bound, not "
                                  "a direct database foreign key or exact commit timestamp",
            "matchedIngestionRunId": ingestion["id"], "sourceRevisionIdentity": c["sourceRevisionIdentity"],
            "jobId": job["id"], "jobName": job["job_name"], "startedAtUtc": job["started_at"],
            "completedAtUtc": job["completed_at"],
            "jobRef": _ref(rows_path, rows_bytes, pointer("job_runs", job["id"])),
            "candidateJobIds": built.job_candidates, "candidateReceiptLinkage": RECEIPT_LINKAGE,
            "writerMapping": {"ingestionJob": ingestion["job_name"], "parentJob": job["job_name"],
                              "permittedParents": sorted(WRITER_PARENTS)},
        },
    }


def record_for(built: Built, revision: str, supersedes: str | None, evidence_ref: dict[str, Any]) -> dict[str, Any]:
    c = built.content
    record = {
        "gameId": c["gameId"], "homeId": c["homeId"], "awayId": c["awayId"], "sourceGameIds": c["sourceGameIds"],
        "episodeKey": c["episodeKey"], "packetSha256": c["packetSha256"], "scheduledStartUtc": c["scheduledStartUtc"],
        "revisionId": revision, "sourceRevisionIdentity": c["sourceRevisionIdentity"], "recordType": RECORD_TYPE,
        "baseProtocolSha256": pilot.PROTOCOL_SHA256, "outcomeAmendmentSha256": AMENDMENT_SHA256,
        "consumerAdmissionVerified": True, "sourceConfirmedTerminal": True,
        "sourceCapturedAtUtc": c["sourceCapturedAtUtc"], "admittedAtUtc": c["admittedAtUtc"], "finalAtUtc": None,
        "finalAtBasis": NULL_BASIS, "status": c["status"], "homeScore": c["homeScore"], "awayScore": c["awayScore"],
        "terminalEvidenceRef": evidence_ref, "supersedesRevisionId": supersedes,
    }
    assert tuple(record) == RECORD_FIELDS
    return record


def write_revision(directory: Path, built: Built, supersedes: str | None, *, ledger: AttemptLedger,
                   clock: Callable[[], datetime]) -> dict[str, Any]:
    game_dir = Path(directory) / built.episode.game_id
    evidence_dir = game_dir / EVIDENCE_DIR
    revision = v1.revision_id(built.content_id, supersedes)
    base = {"kind": "REVISION_WRITTEN", "episodeKey": built.episode.episode_key,
            "canonicalGameId": built.episode.game_id, "revisionId": revision, "supersedesRevisionId": supersedes,
            "contentId": built.content_id, "admissionKey": built.admission_key, "status": built.status,
            "sourceRevisionIdentity": built.content["sourceRevisionIdentity"],
            "sourceCapturedAtUtc": built.content["sourceCapturedAtUtc"],
            "admittedAtUtc": built.content["admittedAtUtc"]}
    existing = v1._find_revision(game_dir, revision)
    if existing is not None:                       # written before a crash, never twice
        path, data = existing
        ledger.append({**base, "recordFile": f"{built.episode.game_id}/{path.name}", "recordSha256": _sha(data),
                       "recordBytes": len(data), "adoptedExistingFile": True}, now=pilot.aware(clock()))
        return {"revisionId": revision, "file": path.name, "status": built.status, "adopted": True}
    rows_bytes = pilot.canonical_bytes(_rows_document(built))
    rows_path = evidence_dir / f"{_sha(rows_bytes)}.json"
    evidence = _evidence_document(built, revision, supersedes, rows_path, rows_bytes)
    v1.assert_no_pnl_fields(evidence, "evidence")
    evidence_bytes = pilot.canonical_bytes(evidence)
    evidence_path = evidence_dir / f"{_sha(evidence_bytes)}.json"
    record = record_for(built, revision, supersedes, _ref(evidence_path, evidence_bytes))
    v1.assert_no_pnl_fields(record, "record")
    record_bytes = pilot.canonical_bytes(record)
    record_sha = _sha(record_bytes)
    v1._write_same(rows_path, rows_bytes)
    v1._write_same(evidence_path, evidence_bytes)
    v1._write_same(game_dir / f"{record_sha}.json", record_bytes)
    ledger.append({**base, "recordFile": f"{built.episode.game_id}/{record_sha}.json", "recordSha256": record_sha,
                   "recordBytes": len(record_bytes),
                   "evidenceFile": f"{built.episode.game_id}/{EVIDENCE_DIR}/{evidence_path.name}",
                   "rowsFile": f"{built.episode.game_id}/{EVIDENCE_DIR}/{rows_path.name}"},
                  now=pilot.aware(clock()))
    return {"revisionId": revision, "file": f"{record_sha}.json", "status": built.status,
            "supersedesRevisionId": supersedes}


# ---------------------------------------------------------------------------
# Planning and the tick
# ---------------------------------------------------------------------------
@dataclass
class Plan:
    systemic: tuple[str, str] | None = None
    actions: list[tuple[str, Any]] = field(default_factory=list)
    episodes: int = 0


def _latest(entries: list[dict[str, Any]]) -> tuple[dict[str, dict], dict[str, dict]]:
    revision: dict[str, dict] = {}
    last: dict[str, dict] = {}
    for entry in entries:
        key = entry.get("episodeKey")
        if not key:
            continue
        if entry.get("kind") == "REVISION_WRITTEN":
            revision[key] = entry
        last[key] = entry
    return revision, last


def _plan(session: Session, packet_dir: Path, entries: list[dict[str, Any]], now: datetime) -> Plan:
    plan = Plan()
    scan = v1.sealed_episodes(packet_dir)
    if scan.systemic:
        plan.systemic = scan.systemic
        tail = entries[-1] if entries else {}
        if not (tail.get("kind") == "SYSTEMIC_STOP" and (tail.get("code"), tail.get("detail")) == scan.systemic):
            plan.actions.append(("SYSTEMIC_STOP", scan.systemic))
        return plan
    latest_revision, latest_entry = _latest(entries)
    for key, code, detail in scan.refused:
        last = latest_entry.get(key) or {}
        if not (last.get("kind") == "SEALED_PACKET_NOT_VERIFIED" and (last.get("code"), last.get("detail"))
                == (code, detail)):
            plan.actions.append(("SEALED_PACKET_NOT_VERIFIED", (key, code, detail)))
    plan.episodes = len(scan.episodes)
    for episode in scan.episodes:
        key = episode.episode_key
        last = latest_entry.get(key) or {}
        previous = latest_revision.get(key)
        disposition = build_episode(session, episode)
        if disposition.built is not None:
            # Same admitting rows as the revision the ledger last stood on: nothing to re-read or
            # write. (An unresolved source is re-checked every tick; it is never assumed.)
            if last.get("admissionKey") == disposition.admission_key and last.get("kind") in (
                    "REVISION_WRITTEN", "STATE_RETURNED_TO_LATEST_REVISION"):
                continue
            try:
                complete(disposition.built, now=now)
            except Unresolved as error:
                disposition = V2Disposition(episode, code=error.code, detail=error.detail,
                                            admission_key=disposition.admission_key)
        if disposition.built is None:
            if not (last.get("kind") == "UNRESOLVED"
                    and (last.get("code"), last.get("detail")) == (disposition.code, disposition.detail)):
                plan.actions.append(("UNRESOLVED", disposition))
            continue
        head = previous.get("revisionId") if previous else None
        if previous and previous.get("contentId") == disposition.built.content_id:
            if last.get("revisionId") != head:
                plan.actions.append(("STATE_RETURNED_TO_LATEST_REVISION", (disposition, previous)))
            continue
        plan.actions.append(("WRITE_REVISION", (disposition.built, head)))
    return plan


def directories(settings: Settings) -> tuple[Path, Path, str | None]:
    """(V2 lane, packets, refusal or None). V2 may not overlap the packets, V1 or withheld directory."""

    v2 = Path(settings.v18_market_pilot_outcomes_v2_dir)
    packets = Path(settings.v18_market_pilot_packet_dir)
    others = (packets, Path(settings.v18_market_pilot_outcomes_dir),
              Path(settings.v18_market_pilot_outcomes_withheld_dir))
    if any(v1._overlap(v2, other) for other in others):
        return v2, packets, "V2_DIR_OVERLAPS_PACKET_V1_OR_WITHHELD_DIR"
    return v2, packets, None


def work_due(session: Session, settings: Settings, *, now: datetime | None = None) -> bool:
    """Is a V2 revision, reason or stop due? Read-only; writes nothing."""

    if not settings.v18_market_pilot_outcomes_v2_enabled:
        return False
    v2, packets, refused = directories(settings)
    if refused:
        return False
    entries = AttemptLedger(v2, name=LEDGER_NAME).entries()
    with v1.read_only(session):
        plan = _plan(session, packets, entries, pilot.aware(now or datetime.now(UTC)))
    return bool(plan.actions)


def run_outcomes_v2_tick(session: Session, settings: Settings, *, now: datetime | None = None,
                         clock: Callable[[], datetime] | None = None) -> dict[str, Any]:
    """Export every due V2 revision for sealed pilot episodes; record every unresolved reason."""

    if not settings.v18_market_pilot_outcomes_v2_enabled:
        return {"records": 0, "enabled": False,
                "note": "V18_MARKET_PILOT_OUTCOMES_V2_ENABLED is off; nothing read or written"}
    clock = clock or (lambda: datetime.now(UTC))
    moment = pilot.aware(now or clock())
    v2, packets, refused = directories(settings)
    if refused:
        logger.error("v18_market_pilot_outcomes_v2_directories_overlap", code=refused, v2=str(v2))
        return {"records": 0, "enabled": True, "refused": refused}
    ledger = AttemptLedger(v2, name=LEDGER_NAME)
    with v1.read_only(session):
        plan = _plan(session, packets, ledger.entries(), moment)
    summary: dict[str, Any] = {"enabled": True, "amendmentSha256": AMENDMENT_SHA256, "directory": str(v2),
                               "sealedEpisodes": plan.episodes, "written": [], "unresolved": [],
                               "notVerified": [], "systemic": plan.systemic}
    for kind, payload in plan.actions:
        at = pilot.aware(clock())
        if kind == "SYSTEMIC_STOP":
            code, detail = payload
            ledger.append({"kind": kind, "code": code, "detail": detail}, now=at)
        elif kind == "SEALED_PACKET_NOT_VERIFIED":
            key, code, detail = payload
            ledger.append({"kind": kind, "episodeKey": key, "code": code, "detail": detail}, now=at)
            summary["notVerified"].append({"episodeKey": key, "code": code})
        elif kind == "UNRESOLVED":
            d = payload
            ledger.append({"kind": kind, "episodeKey": d.episode.episode_key, "canonicalGameId": d.episode.game_id,
                           "code": d.code, "detail": d.detail, "admissionKey": d.admission_key}, now=at)
            summary["unresolved"].append({"episodeKey": d.episode.episode_key, "code": d.code, "detail": d.detail})
        elif kind == "STATE_RETURNED_TO_LATEST_REVISION":
            d, previous = payload
            ledger.append({"kind": kind, "episodeKey": d.episode.episode_key, "canonicalGameId": d.episode.game_id,
                           "revisionId": previous.get("revisionId"), "contentId": d.built.content_id,
                           "admissionKey": d.admission_key}, now=at)
        elif kind == "WRITE_REVISION":
            built, supersedes = payload
            summary["written"].append({"episodeKey": built.episode.episode_key,
                                       **write_revision(v2, built, supersedes, ledger=ledger, clock=clock)})
    summary["records"] = len(summary["written"])
    return summary


__all__ = [
    "AMENDMENT_SHA256", "EVIDENCE_TYPE", "INTERVAL_MINUTES", "JOB_NAME", "LEDGER_NAME", "NULL_BASIS",
    "RECORD_FIELDS", "RECORD_TYPE", "SCHEDULER_ID", "Unresolved", "build_episode", "complete", "directories",
    "pointer", "recompute_schedule_checksum", "run_outcomes_v2_tick", "source_status", "verify_original_source",
    "work_due", "write_revision",
]

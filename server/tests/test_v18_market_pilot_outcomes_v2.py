"""V2 consumer outcome export for amendment 7508ae72... (source-confirmed terminal, capture-bound).

Fixtures are synthetic, but the rows are made by AISP's real code paths. The
raw ``/schedule`` payload is turned into DTOs by the MLB adapter's own
``get_schedule`` (its HTTP call stubbed). The observation, and therefore its
``payload_checksum``, is written by the real normalizer. That lets the export
prove the retained capture reproduces the observation. Model Studio's reference
adapter and loader are imported read-only, when present, to show the records
validate.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.data_sources.base import ScheduleGameDTO
from app.data_sources.mlb import MLBStatsAPIAdapter
from app.ingestion import CanonicalNormalizer
from app.jobs import lanes
from app.jobs.scheduler import build_scheduler
from app.models import ExternalIdMapping, Game, GameStateObservation, GameTeam, IngestionRun, JobRun, RunStatus
from app.research_exports import v18_market_pilot as pilot
from app.research_exports import v18_market_pilot_outcomes as outcomes_v1
from app.research_exports import v18_market_pilot_outcomes_v2 as v2
from app.research_exports import v18_market_pilot_seal as seal

GAME_PK = "825001"
START = datetime(2026, 9, 21, 20, 5, tzinfo=UTC)
SEALED_AT = START - timedelta(minutes=10)
FINAL_AT = START + timedelta(hours=3)          # the fetch that first carries the final
LOOK = "2026-09-22T16:00:00Z"
MODEL_STUDIO_SCRIPTS = Path(r"C:\Users\james\asp-model-studio-source-mlb-market-outcomes\scripts")

STATES = {
    "Scheduled": ("Preview", "S", "S"), "In Progress": ("Live", "I", "I"), "Game Over": ("Final", "O", "O"),
    "Final": ("Final", "F", "F"), "Completed Early: Rain": ("Final", "F", "FR"),
    "Postponed": ("Final", "D", "DR"), "Cancelled": ("Final", "C", "CR"),
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class World:
    """One MLB game written by AISP's own adapter + normalizer, sealed, then captured again."""

    def __init__(self, session: Session, root: Path, monkeypatch) -> None:
        self.session, self.root, self.monkeypatch = session, root, monkeypatch
        self.packets = root / "consumer-packets"
        self.v1 = root / "consumer-outcomes"
        self.withheld = root / "consumer-outcomes-withheld"
        self.v2 = root / "consumer-outcomes-v2"
        self.capture("Scheduled", START - timedelta(days=1), job="refresh-schedule")
        self.game_id = str(session.scalar(select(ExternalIdMapping.entity_id).where(
            ExternalIdMapping.entity_type == "game", ExternalIdMapping.external_id == GAME_PK)))
        sides = session.execute(select(GameTeam.team_id, GameTeam.is_home).where(
            GameTeam.game_id == uuid.UUID(self.game_id))).all()
        self.home_id = next(str(s.team_id) for s in sides if s.is_home)
        self.away_id = next(str(s.team_id) for s in sides if not s.is_home)

    # -- one schedule refresh, the way refresh_schedule performs it ------------------------------
    def capture(self, detailed: str, at: datetime, home: int | None = None, away: int | None = None, *,
                game_date: datetime = START, job: str | None = "finalize-games",
                job_status: RunStatus = RunStatus.SUCCEEDED, in_flight: bool = False,
                ingestion: str = "one", foreign_key: bool = False, archive_delay: timedelta = timedelta(seconds=1),
                ) -> dict[str, Any]:
        abstract, coded, code = STATES[detailed]
        item = {
            "gamePk": int(GAME_PK), "gameDate": pilot.iso(game_date), "officialDate": f"{game_date:%Y-%m-%d}",
            "status": {"abstractGameState": abstract, "codedGameState": coded, "detailedState": detailed,
                       "statusCode": code, "abstractGameCode": abstract[0]},
            "teams": {
                "home": {"team": {"id": 110, "name": "Baltimore Orioles", "abbreviation": "BAL"}, "score": home,
                         "leagueRecord": {"wins": 80, "losses": 70}},
                "away": {"team": {"id": 158, "name": "Milwaukee Brewers", "abbreviation": "MIL"}, "score": away,
                         "leagueRecord": {"wins": 85, "losses": 65}},
            },
            "venue": {"id": 2, "name": "Oriole Park at Camden Yards"},
            "doubleHeader": "N", "gameNumber": 1, "scheduledInnings": 9, "gameType": "R",
        }
        schedule = {"dates": [{"date": f"{game_date:%Y-%m-%d}", "games": [item]}]}

        async def fake_get(adapter, path, params, **kwargs):
            return schedule
        self.monkeypatch.setattr(MLBStatsAPIAdapter, "_get", fake_get)
        response = asyncio.run(MLBStatsAPIAdapter().get_schedule(date.fromisoformat(f"{game_date:%Y-%m-%d}")))
        dtos = [ScheduleGameDTO.model_validate(x) for x in response.envelope.data]
        payload = {"schedule": schedule, "people": {}}

        folder = self.root / "raw" / "mlb_stats_api" / f"{at:%Y}" / f"{at:%m}" / f"{at:%d}"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{at:%H%M%S_%f}_{uuid.uuid4().hex[:10]}.json"
        path.write_text(json.dumps({"metadata": {
            "source": "MLB_STATS_API", "ingestion_timestamp": (at + archive_delay).isoformat(),
            "checksum": _sha(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()),
            "status": "success", "record_count": 1, "schema_version": "1", "historical": False,
        }, "payload": payload}, indent=2, sort_keys=True), encoding="utf-8")

        run = None
        if job:
            run = JobRun(job_name=job, status=RunStatus.RUNNING if in_flight else job_status,
                         started_at=at - timedelta(seconds=20),
                         completed_at=None if in_flight else at + timedelta(seconds=90))
            self.session.add(run)
        ingestion_rows = []
        for _ in range({"none": 0, "one": 1, "two": 2}[ingestion]):
            row = IngestionRun(source="MLB_STATS_API", job_name="refresh-schedule", status=RunStatus.SUCCEEDED,
                               started_at=at, completed_at=at + timedelta(seconds=2), records_processed=1,
                               raw_reference=str(path))
            self.session.add(row)
            ingestion_rows.append(row)
        self.session.flush()
        CanonicalNormalizer(self.session).ingest_schedule(
            dtos, "MLB_STATS_API", available_as_of=at,
            ingestion_run_id=ingestion_rows[0].id if (foreign_key and ingestion_rows) else None)
        self.session.flush()
        # PostgreSQL stamps created_at with the inserting transaction's start; SQLite would stamp
        # the wall clock, so the fixture records the transaction start the job would have had.
        for obs in self.session.scalars(select(GameStateObservation)):
            if pilot.aware(obs.available_as_of) == at:
                self.session.execute(update(GameStateObservation).where(GameStateObservation.id == obs.id)
                                     .values(created_at=at + timedelta(seconds=1)))
        self.session.flush()
        return {"path": path, "run": run, "ingestion": ingestion_rows}

    # -- the sealed pregame packet ---------------------------------------------------------------
    def seal(self, *, kind: str = "SEALED") -> str:
        key = pilot.episode_key(self.game_id, GAME_PK, pilot.research_lock(START))
        packet = {"protocolSha256": pilot.PROTOCOL_SHA256, "episodeKey": key, "sealedAtUtc": pilot.iso(SEALED_AT),
                  "game": {"canonicalGameId": self.game_id, "sourceGameIds": {"MLB_STATS_API": GAME_PK},
                           "homeId": self.home_id, "awayId": self.away_id,
                           "homeSourceIds": {"MLB_STATS_API": "110"}, "awaySourceIds": {"MLB_STATS_API": "158"},
                           "scheduledStartUtc": pilot.iso(START)}}
        data = pilot.canonical_bytes(packet)
        ledger = seal.AttemptLedger(self.packets)
        ledger.append({"kind": "SEAL_ATTEMPT", "episodeKey": key}, now=SEALED_AT - timedelta(seconds=5))
        seal.write_once(self.packets / f"{key}.json", data)
        seal.write_once(self.packets / f"{key}.sha256", f"{_sha(data)}  {key}.json\n".encode("ascii"))
        ledger.append({"kind": kind, "episodeKey": key, "packetSha256": _sha(data), "packetBytes": len(data)},
                      now=SEALED_AT)
        self.packet_sha, self.episode_key = _sha(data), key
        return key

    def reader_row(self) -> dict[str, Any]:
        """The row Model Studio's reader derives from the sealed packet."""

        packet = json.loads((self.packets / f"{self.episode_key}.json").read_bytes())
        game = packet["game"]
        return {"gameId": game["canonicalGameId"], "homeId": game["homeId"], "awayId": game["awayId"],
                "sourceGameIds": game["sourceGameIds"], "episodeKey": packet["episodeKey"],
                "packetSha256": self.packet_sha, "scheduledStartUtc": game["scheduledStartUtc"],
                "forecastEligible": True}

    def settings(self, base, **extra):
        return base.model_copy(update={
            "v18_market_pilot_outcomes_v2_enabled": True, "v18_market_pilot_outcomes_v2_dir": str(self.v2),
            "v18_market_pilot_outcomes_dir": str(self.v1),
            "v18_market_pilot_outcomes_withheld_dir": str(self.withheld),
            "v18_market_pilot_packet_dir": str(self.packets), **extra})

    def tick(self, base, at: datetime = FINAL_AT + timedelta(hours=1)):
        self.session.commit()
        return v2.run_outcomes_v2_tick(self.session, self.settings(base), clock=lambda: at)

    def ledger(self) -> list[dict]:
        return seal.AttemptLedger(self.v2, name=v2.LEDGER_NAME).entries()

    def records(self) -> list[tuple[Path, dict]]:
        found = []
        for entry in self.ledger():
            if entry["kind"] == "REVISION_WRITTEN":
                path = self.v2 / entry["recordFile"]
                found.append((path, json.loads(path.read_bytes())))
        return found

    def unresolved(self) -> list[str]:
        return [e["code"] for e in self.ledger() if e["kind"] == "UNRESOLVED"]


@pytest.fixture
def world(session: Session, tmp_path: Path, monkeypatch) -> World:
    return World(session, tmp_path, monkeypatch)


def _final(world: World, home: int = 5, away: int = 3, **kwargs):
    world.seal()
    world.capture("In Progress", START + timedelta(minutes=30), 1, 0)
    return world.capture("Final", FINAL_AT, home, away, **kwargs)


def _evidence(path: Path, record: dict) -> dict:
    return json.loads(Path(record["terminalEvidenceRef"]["path"]).read_bytes())


def _resolve(evidence_ref: dict) -> dict:
    value = json.loads(Path(evidence_ref["path"]).read_bytes())
    for token in evidence_ref["locator"][1:].split("/"):
        value = value[token.replace("~1", "/").replace("~0", "~")]
    return value


# ---------------------------------------------------------------------------
# The record and its evidence
# ---------------------------------------------------------------------------
def test_a_source_confirmed_final_is_one_v2_record_carrying_every_contract_field(world, settings):
    capture = _final(world)
    summary = world.tick(settings)
    assert summary["records"] == 1 and summary["unresolved"] == []
    [(path, record)] = world.records()
    data = path.read_bytes()
    assert path.parent == world.v2 / world.game_id and path.name == f"{_sha(data)}.json"
    assert not os.access(path, os.W_OK)
    assert set(record) == set(v2.RECORD_FIELDS) and len(record) == len(v2.RECORD_FIELDS)
    assert record["recordType"] == "MLB_V18_MARKET_ADMITTED_OUTCOME_V2"
    assert record["baseProtocolSha256"] == pilot.PROTOCOL_SHA256
    assert record["outcomeAmendmentSha256"] == "7508ae728cc852812607c68222587f382b7d722b4a03d6a1f780033a5c6ebf2f"
    row = world.reader_row()
    for key in ("gameId", "homeId", "awayId", "sourceGameIds", "episodeKey", "packetSha256", "scheduledStartUtc"):
        assert record[key] == row[key], key
    assert record["consumerAdmissionVerified"] is True and record["sourceConfirmedTerminal"] is True
    assert (record["status"], record["homeScore"], record["awayScore"]) == ("DECISIVE", 5, 3)
    assert type(record["homeScore"]) is int
    assert record["finalAtUtc"] is None
    assert record["finalAtBasis"] == "SOURCE_CONFIRMED_TERMINAL_COMPLETION_TIME_UNAVAILABLE"
    assert record["sourceCapturedAtUtc"] == pilot.iso(FINAL_AT)
    assert record["admittedAtUtc"] == pilot.iso(FINAL_AT + timedelta(seconds=90))   # the writer job's completion
    assert record["supersedesRevisionId"] is None and len(record["revisionId"]) == 64

    ref = record["terminalEvidenceRef"]
    evidence_path = Path(ref["path"])
    assert evidence_path.is_absolute() and evidence_path.parent == (world.v2 / world.game_id / "evidence").resolve()
    evidence_bytes = evidence_path.read_bytes()
    assert (evidence_path.stem, ref["bytes"]) == (_sha(evidence_bytes), len(evidence_bytes)) == (ref["sha256"],
                                                                                                    ref["bytes"])
    evidence = json.loads(evidence_bytes)
    assert evidence["recordType"] == "MLB_V18_MARKET_TERMINAL_EVIDENCE_V2"
    for key in ("gameId", "homeId", "awayId", "sourceGameIds", "episodeKey", "packetSha256", "scheduledStartUtc",
                "revisionId", "sourceRevisionIdentity"):
        assert evidence[key] == record[key], key
    assert evidence["scheduleDisposition"] == "UNCHANGED"
    assert evidence["originalScheduledStartUtc"] == row["scheduledStartUtc"]

    source = evidence["sourceProjection"]
    assert source["verifiedFromOriginalSource"] is True and source["sourceConfirmedTerminal"] is True
    assert source["verification"]["payloadChecksumReproduced"] is True
    assert (source["sourceStatus"], source["canonicalStatus"]) == ("FINAL", "final")
    raw = capture["path"].read_bytes()
    assert source["originalSourceRef"] == {"path": str(capture["path"]), "sha256": _sha(raw), "bytes": len(raw)}
    assert source["sourceLocator"] == "/payload/schedule/dates/0/games/0"
    located = json.loads(raw)["payload"]["schedule"]["dates"][0]["games"][0]
    assert located["gamePk"] == int(GAME_PK)

    receipt = evidence["captureReceipt"]
    assert receipt["basis"] == "RETAINED_SOURCE_FETCH_COMPLETED"
    assert receipt["sourceCapturedAtUtc"] <= receipt["archivedAtUtc"] <= record["admittedAtUtc"]
    assert receipt["originalSourceSha256"] == _sha(raw)
    [ingestion] = capture["ingestion"]
    assert receipt["ingestionRunId"] == str(ingestion.id)
    assert receipt["linkageBasis"] == "UNIQUE_RETAINED_SOURCE_AND_EXACT_FETCH_CLOCK"
    assert receipt["candidateIngestionRunIds"] == [str(ingestion.id)]
    observation = _resolve(receipt["observationRef"])
    assert observation["game_id"] == world.game_id                     # the actual identity column
    assert observation["ingestion_run_id"] is None                      # a NULL key is kept null
    assert (observation["home_team_id"], observation["away_team_id"]) == (world.home_id, world.away_id)
    assert observation["payload_checksum"] == source["payloadChecksum"]
    assert observation["available_as_of"] == record["sourceCapturedAtUtc"]
    ingestion_row = _resolve(receipt["ingestionRef"])
    assert ingestion_row["raw_reference"] == str(capture["path"]) and ingestion_row["job_name"] == "refresh-schedule"
    for pointer_ref in (receipt["observationRef"], receipt["ingestionRef"]):
        assert Path(pointer_ref["path"]).parent == evidence_path.parent
        assert Path(pointer_ref["path"]).stem == pointer_ref["sha256"]

    admission = evidence["admissionReceipt"]
    run = capture["run"]
    assert admission["status"] == "SUCCESS" and admission["commitBoundBasis"] == "SUCCESSFUL_MATCHED_JOB_COMPLETED"
    assert admission["candidateReceiptLinkage"] == "UNIQUE_SUCCESSFUL_SOURCE_WRITER_WINDOW"
    assert admission["candidateJobIds"] == [str(run.id)] and admission["jobId"] == str(run.id)
    assert admission["matchedIngestionRunId"] == str(ingestion.id)
    assert admission["writerMapping"]["parentJob"] == "finalize-games"
    assert _resolve(admission["jobRef"])["status"] == "succeeded"

    # V1 and its withheld lane are neither read into nor written by the V2 job.
    assert not world.v1.exists() and not world.withheld.exists()


def test_the_record_and_evidence_validate_through_model_studios_reference_adapter(world, settings, monkeypatch,
                                                                                    tmp_path):
    if not (MODEL_STUDIO_SCRIPTS / "mlb_v18_market_outcome_contract_v2.py").exists():
        pytest.skip("Model Studio's reference adapter is not on this machine")
    _final(world)
    world.tick(settings)
    [(_, record)] = world.records()
    monkeypatch.syspath_prepend(str(MODEL_STUDIO_SCRIPTS))           # read-only import, this test only
    import mlb_v18_market_outcome_contract_v2 as reference
    import mlb_v18_market_outcomes_v2 as loader

    row = world.reader_row()
    built = reference.build_capture_bound_outcome(row, record["terminalEvidenceRef"], v2.AMENDMENT_SHA256,
                                                  record["admittedAtUtc"])
    assert built == record
    reference.validate_bound_v2(record, row, LOOK, LOOK, v2.AMENDMENT_SHA256,
                                evidence_root=world.v2 / world.game_id / "evidence")
    accepted, exclusions, blocked = loader.load_outcomes([row], world.v2, LOOK, LOOK, tmp_path / "ms-archive",
                                                         mode="CAPTURE_BOUND_V2", amendment_sha=v2.AMENDMENT_SHA256)
    assert len(accepted) == 1 and not exclusions and not blocked
    assert accepted[0]["revisionId"] == record["revisionId"]
    # The strict V1 reader never admits a null completion clock from this lane.
    strict, _, strict_blocked = loader.load_outcomes([row], world.v2, LOOK, LOOK)
    assert not strict and strict_blocked == {world.game_id}


@pytest.mark.parametrize("detailed, home, away, status, source_status", [
    ("Final", 4, 4, "TIE", "FINAL"),
    ("Completed Early: Rain", 2, 1, "DECISIVE", "COMPLETED"),
])
def test_tie_and_completed_early_are_terminal(world, settings, monkeypatch, tmp_path, detailed, home, away, status,
                                              source_status):
    world.seal()
    world.capture("In Progress", START + timedelta(minutes=30), 0, 0)
    world.capture(detailed, FINAL_AT, home, away)
    world.tick(settings)
    [(_, record)] = world.records()
    assert (record["status"], record["homeScore"], record["awayScore"]) == (status, home, away)
    assert _evidence(Path(), record)["sourceProjection"]["sourceStatus"] == source_status
    _validate_with_reference(world, record, monkeypatch, tmp_path)


@pytest.mark.parametrize("detailed, status, source_status", [("Postponed", "VOID", "POSTPONED"),
                                                             ("Cancelled", "NO_CONTEST", "CANCELLED")])
def test_postponed_and_cancelled_at_the_sealed_start(world, settings, monkeypatch, tmp_path, detailed, status,
                                                     source_status):
    world.seal()
    world.capture(detailed, START + timedelta(minutes=40))
    world.tick(settings)
    [(_, record)] = world.records()
    assert record["status"] == status and record["finalAtUtc"] is None
    evidence = _evidence(Path(), record)
    assert evidence["sourceProjection"]["sourceStatus"] == source_status
    assert evidence["scheduleDisposition"] == "UNCHANGED"               # observed at the sealed start itself
    _validate_with_reference(world, record, monkeypatch, tmp_path)


def test_a_makeup_final_is_never_substituted_for_a_postponed_episode(world, settings):
    world.seal()
    world.capture("Postponed", START + timedelta(minutes=40))
    world.tick(settings)
    makeup = START + timedelta(days=1)
    world.capture("In Progress", makeup + timedelta(minutes=30), 0, 1, game_date=makeup)
    world.capture("Final", makeup + timedelta(hours=3), 2, 6, game_date=makeup)
    world.tick(settings, makeup + timedelta(hours=4))
    assert [r["status"] for _, r in world.records()] == ["VOID"]


def _validate_with_reference(world, record, monkeypatch, tmp_path):
    """Rebuild through the reference adapter and load through the V2 loader (when present)."""

    if not (MODEL_STUDIO_SCRIPTS / "mlb_v18_market_outcome_contract_v2.py").exists():
        return None
    monkeypatch.syspath_prepend(str(MODEL_STUDIO_SCRIPTS))
    import mlb_v18_market_outcome_contract_v2 as reference
    import mlb_v18_market_outcomes_v2 as loader

    row = world.reader_row()
    assert reference.build_capture_bound_outcome(row, record["terminalEvidenceRef"], v2.AMENDMENT_SHA256,
                                                 record["admittedAtUtc"]) == record
    accepted, exclusions, blocked = loader.load_outcomes([row], world.v2, LOOK, LOOK, tmp_path / "ms-archive",
                                                         mode="CAPTURE_BOUND_V2", amendment_sha=v2.AMENDMENT_SHA256)
    assert record["revisionId"] in [a["revisionId"] for a in accepted] and not exclusions and not blocked
    latest, _, selection_blocked = loader.select_outcomes(accepted, LOOK, mode="CAPTURE_BOUND_V2",
                                                          amendment_sha=v2.AMENDMENT_SHA256)
    assert not selection_blocked
    return latest[world.game_id]


# ---------------------------------------------------------------------------
# What stays unresolved (ledger reason, never a record)
# ---------------------------------------------------------------------------
def test_pending_and_game_over_never_enter_v2(world, settings):
    world.seal()
    world.capture("In Progress", START + timedelta(minutes=30), 1, 0)
    world.tick(settings)
    world.capture("Game Over", START + timedelta(hours=3), 3, 1)      # AISP: live, not an official final
    world.tick(settings)
    assert world.records() == []
    assert world.unresolved() == ["V2_NOT_SOURCE_CONFIRMED_TERMINAL"]   # said once, not every tick
    assert v2.source_status("Game Over") == "GAME_OVER" and v2.source_status("Final: Tied") == "FINAL"


def test_a_game_over_source_state_is_refused_even_if_canonical_final(world, settings, monkeypatch):
    _final(world)
    original = v2.verify_original_source

    def game_over(*args, **kwargs):
        facts = original(*args, **kwargs)
        return {**facts, "sourceStatus": "GAME_OVER"}
    monkeypatch.setattr(v2, "verify_original_source", game_over)
    world.tick(settings)
    assert world.records() == [] and world.unresolved() == ["V2_GAME_OVER_IS_NOT_OFFICIAL_FINAL"]


@pytest.mark.parametrize("case, code", [
    ("failed_job", "V2_ADMISSION_JOB_NOT_SUCCESSFUL"),
    ("in_flight", "V2_NO_V1_ADMISSION:ADMISSION_RECEIPT_WRITER_RUN_IN_FLIGHT"),
    ("ambiguous_job", "V2_ADMISSION_JOB_AMBIGUOUS"),
    ("unknown_parent", "V2_ADMISSION_JOB_PARENT_UNSUPPORTED"),
    ("no_ingestion", "V2_INGESTION_LINK_NOT_FOUND"),
    ("two_ingestions", "V2_INGESTION_LINK_AMBIGUOUS"),
    ("archive_before_capture", "V2_CHRONOLOGY_INVALID"),
])
def test_missing_failed_in_flight_ambiguous_or_disordered_linkage_stays_unresolved(world, settings, case, code):
    kwargs: dict[str, Any] = {
        "failed_job": {"job_status": RunStatus.FAILED}, "in_flight": {"in_flight": True},
        "unknown_parent": {"job": "controlled-live-validation"}, "no_ingestion": {"ingestion": "none"},
        "two_ingestions": {"ingestion": "two"}, "archive_before_capture": {"archive_delay": timedelta(seconds=-5)},
    }.get(case, {})
    capture = _final(world, **kwargs)
    if case == "ambiguous_job":
        world.session.add(JobRun(job_name="refresh-schedule", status=RunStatus.SUCCEEDED,
                                 started_at=FINAL_AT - timedelta(seconds=5), completed_at=FINAL_AT + timedelta(seconds=30)))
    assert capture
    summary = world.tick(settings)
    assert summary["records"] == 0 and world.records() == []
    assert world.unresolved() == [code]
    assert not (world.v2 / world.game_id).exists()


def test_a_capture_on_or_after_the_authority_expiry_is_not_admitted(world, settings):
    world.seal()
    late = datetime(2026, 10, 1, 0, 0, 30, tzinfo=UTC)
    world.capture("In Progress", START + timedelta(minutes=30), 0, 0)
    world.capture("Final", late, 5, 3)
    world.tick(settings, late + timedelta(hours=1))
    assert world.records() == [] and world.unresolved() == ["V2_SOURCE_CAPTURE_AUTHORITY_EXPIRED"]


def test_a_tampered_or_different_original_source_is_not_verified(world, settings):
    capture = _final(world)
    document = json.loads(capture["path"].read_bytes())
    document["payload"]["schedule"]["dates"][0]["games"][0]["venue"]["name"] = "Somewhere else"
    capture["path"].write_text(json.dumps(document), encoding="utf-8")
    world.tick(settings)
    assert world.records() == [] and world.unresolved() == ["V2_ORIGINAL_SOURCE_CHECKSUM_NOT_REPRODUCED"]


def test_a_resumed_or_rescheduled_final_without_resumption_evidence_stays_unresolved(world, settings):
    world.seal()
    world.capture("In Progress", START + timedelta(minutes=30), 1, 0)            # play began at the sealed start
    resumed = START + timedelta(days=1)
    world.capture("Final", resumed + timedelta(hours=1), 4, 2, game_date=resumed)
    world.tick(settings, resumed + timedelta(hours=2))
    assert world.records() == []
    assert world.unresolved() == ["V2_SCHEDULE_NOT_UNCHANGED_RESUMPTION_EVIDENCE_NOT_ESTABLISHED"]


@pytest.mark.parametrize("kind", ["SEAL_REFUSED", "MISSED_SEAL_DEADLINE", "SEALED_LATE_NOT_PROSPECTIVE"])
def test_only_authentic_sealed_packets_are_read(world, settings, kind):
    world.seal(kind=kind)
    world.capture("Final", FINAL_AT, 5, 3)
    summary = world.tick(settings)
    assert summary["sealedEpisodes"] == 0 and not (world.v2 / world.game_id).exists()


# ---------------------------------------------------------------------------
# Foreign key, corrections, write-once
# ---------------------------------------------------------------------------
def test_an_observation_with_its_own_ingestion_key_uses_it(world, settings, monkeypatch, tmp_path):
    capture = _final(world, foreign_key=True)
    world.tick(settings)
    [(_, record)] = world.records()
    receipt = _evidence(Path(), record)["captureReceipt"]
    assert receipt["linkageBasis"] == "OBSERVATION_INGESTION_FOREIGN_KEY"
    assert _resolve(receipt["observationRef"])["ingestion_run_id"] == str(capture["ingestion"][0].id)
    _validate_with_reference(world, record, monkeypatch, tmp_path)


def test_a_score_correction_is_a_new_v2_revision_and_the_original_is_kept(world, settings, monkeypatch, tmp_path):
    _final(world)
    world.tick(settings)
    [(first_path, first)] = world.records()
    before = first_path.read_bytes()
    world.capture("Final", FINAL_AT + timedelta(hours=6), 5, 4)
    world.tick(settings, FINAL_AT + timedelta(hours=7))
    (path_a, a), (path_b, b) = world.records()
    assert path_a == first_path and path_a.read_bytes() == before
    assert b["supersedesRevisionId"] == first["revisionId"] and (b["homeScore"], b["awayScore"]) == (5, 4)
    assert _evidence(Path(), b)["supersedesRevisionId"] == first["revisionId"]
    latest = _validate_with_reference(world, b, monkeypatch, tmp_path)
    if latest is not None:                         # Model Studio binds the latest admissible revision
        assert latest["revisionId"] == b["revisionId"]


def test_v2_is_write_once_and_an_unchanged_state_writes_nothing(world, settings):
    _final(world)
    world.tick(settings)
    [(path, _)] = world.records()
    ledger_before = world.ledger()
    files_before = sorted(p.relative_to(world.v2) for p in world.v2.rglob("*") if p.is_file())
    world.capture("Final", FINAL_AT + timedelta(minutes=30), 5, 3)     # a re-capture of the same revision
    world.tick(settings, FINAL_AT + timedelta(hours=2))
    assert world.ledger() == ledger_before
    assert sorted(p.relative_to(world.v2) for p in world.v2.rglob("*") if p.is_file()) == files_before
    assert v2.work_due(world.session, world.settings(settings), now=FINAL_AT + timedelta(hours=2)) is False
    with pytest.raises(FileExistsError):
        seal.write_once(path, b"{}")


def test_running_v1_beside_v2_leaves_each_lane_to_itself(world, settings):
    _final(world)
    world.session.commit()
    on = world.settings(settings, v18_market_pilot_outcomes_export_enabled=True)
    outcomes_v1.run_outcomes_tick(world.session, on, clock=lambda: FINAL_AT + timedelta(hours=1))
    world.tick(settings)
    # V1 withheld the null-clock final (its strict rule); V2 admitted it under the amendment.
    assert not list(world.v1.glob("*/*.json"))
    assert [p.parent.name for p in world.withheld.glob("*/*.json")] == [world.game_id]
    assert [r["status"] for _, r in world.records()] == ["DECISIVE"]


# ---------------------------------------------------------------------------
# Isolation, job, lane
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("place", ["packets", "v1", "withheld", "inside_v1", "parent"])
def test_the_v2_directory_never_overlaps_packets_v1_or_withheld(world, settings, place):
    _final(world)
    world.session.commit()
    target = {"packets": world.packets, "v1": world.v1, "withheld": world.withheld,
              "inside_v1": world.v1 / "v2", "parent": world.root}[place]
    bad = world.settings(settings, v18_market_pilot_outcomes_v2_dir=str(target))
    before = {p.name: p.read_bytes() for p in world.packets.iterdir()}
    assert v2.run_outcomes_v2_tick(world.session, bad)["refused"] == "V2_DIR_OVERLAPS_PACKET_V1_OR_WITHHELD_DIR"
    assert v2.work_due(world.session, bad) is False
    assert {p.name: p.read_bytes() for p in world.packets.iterdir()} == before


def test_the_v2_tick_is_inert_when_disabled(world, settings):
    _final(world)
    world.session.commit()
    off = world.settings(settings, v18_market_pilot_outcomes_v2_enabled=False)
    assert v2.run_outcomes_v2_tick(world.session, off)["enabled"] is False
    assert v2.work_due(world.session, off) is False and not world.v2.exists()


def test_the_v2_job_runs_on_the_research_export_lane_only_when_enabled(settings):
    assert v2.SCHEDULER_ID == "asp-rx-v18-market-pilot-outcomes-v2"
    assert lanes.lane_for(v2.SCHEDULER_ID) == lanes.RESEARCH_EXPORT_LANE
    assert build_scheduler(settings.model_copy(update={
        "scheduler_core_jobs_enabled": True, "v18_market_pilot_outcomes_v2_enabled": False})).get_job(
        v2.SCHEDULER_ID) is None
    on = build_scheduler(settings.model_copy(update={
        "scheduler_core_jobs_enabled": True, "v18_market_pilot_outcomes_v2_enabled": True}))
    job = on.get_job(v2.SCHEDULER_ID)
    assert job is not None and getattr(job.func, "__aisp_job_lane__") == lanes.RESEARCH_EXPORT_LANE
    assert job.trigger.interval == timedelta(minutes=v2.INTERVAL_MINUTES) == timedelta(minutes=10)
    assert job.max_instances == 1
    assert build_scheduler(settings.model_copy(update={
        "scheduler_core_jobs_enabled": False, "v18_market_pilot_outcomes_v2_enabled": True})).get_job(
        v2.SCHEDULER_ID) is None
    from app.jobs.orchestrator import AVAILABLE_JOBS, JobOrchestrator

    assert v2.JOB_NAME in AVAILABLE_JOBS
    assert inspect.iscoroutinefunction(JobOrchestrator.export_v18_market_pilot_outcomes_v2)


def test_the_pregame_modules_and_v1_do_not_import_v2():
    for module in (pilot, seal, outcomes_v1):
        assert "v18_market_pilot_outcomes_v2" not in inspect.getsource(module)


def test_rfc6901_pointers_escape_tildes_and_slashes():
    assert v2.pointer("rows", "a/b", "c~d") == "/rows/a~1b/c~0d"


def test_no_last_play_schedule_or_proxy_clock_can_become_final_at(world, settings):
    _final(world)
    world.tick(settings)
    [(_, record)] = world.records()
    evidence = _evidence(Path(), record)
    assert record["finalAtUtc"] is None and evidence["sourceProjection"]["completionField"] is None
    assert record["finalAtUtc"] not in (record["sourceCapturedAtUtc"], record["admittedAtUtc"],
                                        record["scheduledStartUtc"])
    source = inspect.getsource(v2)
    assert "allPlays" not in source and "gameEndTime\"" not in source

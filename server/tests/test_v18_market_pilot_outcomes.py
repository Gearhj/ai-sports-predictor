"""V18 vs fresh-market pilot: consumer-admitted outcome export (Model Studio's same-final contract).

Pins what the contract and the owner's brief state:

* only authentic sealed episodes are read;
* identity is the sealed packet's own;
* AISP's own admission rule, not a position's profit/loss, decides the status;
* files are write-once, and a correction is a new file that supersedes;
* the clocks are completed receipts, never a transaction start;
* the job is inert when off and runs on the research-export lane;
* the pregame modules never import this one.

Synthetic fixtures only.
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.jobs import lanes
from app.jobs.scheduler import build_scheduler
from app.models import (
    ExternalIdMapping, Game, GameStateObservation, GameStatus, GameTeam, IngestionRun, JobRun, League, Market,
    MarketOutcome, MarketType, OfficialAspPosition, PositionResult, PositionSettlement, PositionStatus, RunStatus,
    Season, Sport, Team,
)
from app.research_exports import v18_market_pilot as pilot
from app.research_exports import v18_market_pilot_outcomes as outcomes
from app.research_exports import v18_market_pilot_seal as seal

START = datetime(2026, 9, 21, 20, 5, tzinfo=UTC)
SEALED_AT = START - timedelta(minutes=10)
GAME_PK = "825001"

VERBATIM = {
    "final": {"abstractGameState": "Final", "codedGameState": "F", "detailedState": "Final", "statusCode": "F"},
    "live": {"abstractGameState": "Live", "codedGameState": "I", "detailedState": "In Progress",
             "statusCode": "I"},
    "postponed": {"abstractGameState": "Final", "codedGameState": "D", "detailedState": "Postponed",
                  "statusCode": "DR", "reason": "Rain"},
    "canceled": {"abstractGameState": "Final", "codedGameState": "C", "detailedState": "Cancelled",
                 "statusCode": "CR", "reason": "Rain"},
    "scheduled": {"abstractGameState": "Preview", "codedGameState": "S", "detailedState": "Scheduled",
                  "statusCode": "S"},
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Retained evidence helpers (usable on any world that has a game and two teams)
# ---------------------------------------------------------------------------
def add_observation(session: Session, root: Path, game: Game, home: Team, away: Team, status: str,
                    at: datetime, *, home_score: int | None = None, away_score: int | None = None,
                    scheduled: datetime = START, tag: str = "", job: str = "finalize-games",
                    in_flight: bool = False, raw: bool = True, game_pk: str = GAME_PK
                    ) -> tuple[GameStateObservation, JobRun]:
    """One schedule refresh: the observation, its writer job run, its ingestion run and raw capture."""

    checksum = _sha(f"{status}|{home_score}|{away_score}|{scheduled.isoformat()}|{tag}".encode())
    observation = GameStateObservation(
        game_id=game.id, home_team_id=home.id, away_team_id=away.id, scheduled_at=scheduled, status=status,
        is_postponed=status == "postponed", home_score=home_score, away_score=away_score, final_at=None,
        observed_at=at, available_as_of=at, source="MLB_STATS_API", source_mode="LIVE",
        source_record_id=game_pk, payload_checksum=checksum, created_at=at + timedelta(seconds=1))
    run = JobRun(job_name=job, status=RunStatus.RUNNING if in_flight else RunStatus.SUCCEEDED,
                 started_at=at - timedelta(seconds=20),
                 completed_at=None if in_flight else at + timedelta(seconds=90))
    session.add_all([observation, run])
    if raw:
        folder = root / "raw" / "mlb_stats_api" / f"{at:%Y}" / f"{at:%m}" / f"{at:%d}"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{at:%H%M%S}_{at:%f}_{uuid.uuid4().hex[:10]}.json"
        item = {"gamePk": int(game_pk), "gameDate": pilot.iso(scheduled), "status": VERBATIM[status],
                "teams": {"home": {"score": home_score, "team": {"id": 110}},
                          "away": {"score": away_score, "team": {"id": 158}}}}
        path.write_text(json.dumps({
            "metadata": {"source": "MLB_STATS_API", "ingestion_timestamp": (at + timedelta(seconds=1)).isoformat(),
                         "checksum": "0" * 64},
            "payload": {"schedule": {"dates": [{"date": f"{scheduled:%Y-%m-%d}", "games": [item]}]}, "people": {}},
        }), encoding="utf-8")
        session.add(IngestionRun(source="MLB_STATS_API", job_name="refresh-schedule", status=RunStatus.SUCCEEDED,
                                 started_at=at, completed_at=at + timedelta(seconds=2), records_processed=1,
                                 raw_reference=str(path)))
    session.flush()
    return observation, run


def seal_packet(packet_dir: Path, game_id: str, home_id: str, away_id: str, *, start: datetime = START,
                kind: str = "SEALED", ledger_sha: str | None = None) -> str:
    """A sealed packet as the sealer leaves it: packet, .sha256 and hash-chained ledger rows."""

    key = pilot.episode_key(game_id, GAME_PK, pilot.research_lock(start))
    packet = {"protocolSha256": pilot.PROTOCOL_SHA256, "episodeKey": key, "sealedAtUtc": pilot.iso(SEALED_AT),
              "game": {"canonicalGameId": game_id, "sourceGameIds": {"MLB_STATS_API": GAME_PK},
                       "homeId": home_id, "awayId": away_id, "scheduledStartUtc": pilot.iso(start)}}
    data = pilot.canonical_bytes(packet)
    ledger = seal.AttemptLedger(packet_dir)
    ledger.append({"kind": "SEAL_ATTEMPT", "episodeKey": key}, now=SEALED_AT - timedelta(seconds=5))
    seal.write_once(packet_dir / f"{key}.json", data)
    seal.write_once(packet_dir / f"{key}.sha256", f"{_sha(data)}  {key}.json\n".encode("ascii"))
    ledger.append({"kind": kind, "episodeKey": key, "packetSha256": ledger_sha or _sha(data),
                   "packetBytes": len(data)}, now=SEALED_AT)
    return key


class World:
    def __init__(self, session: Session, root: Path) -> None:
        self.session = session
        self.root = root
        self.packets = root / "consumer-packets"
        self.outcomes = root / "consumer-outcomes"
        sport = Sport(code="BASEBALL", name="Baseball")
        session.add(sport)
        session.flush()
        league = League(sport_id=sport.id, code="MLB", name="MLB")
        session.add(league)
        session.flush()
        season = Season(league_id=league.id, name="2026", starts_at=datetime(2026, 3, 1, tzinfo=UTC),
                        ends_at=datetime(2026, 11, 30, tzinfo=UTC))
        self.home = Team(league_id=league.id, name="Baltimore Orioles", abbreviation="BAL")
        self.away = Team(league_id=league.id, name="Milwaukee Brewers", abbreviation="MIL")
        session.add_all([season, self.home, self.away])
        session.flush()
        self.game = Game(league_id=league.id, season_id=season.id, starts_at=START, status=GameStatus.SCHEDULED,
                         source_mode="LIVE", scheduled_innings=9)
        session.add(self.game)
        session.flush()
        session.add_all([
            GameTeam(game_id=self.game.id, team_id=self.home.id, is_home=True),
            GameTeam(game_id=self.game.id, team_id=self.away.id, is_home=False),
            ExternalIdMapping(entity_type="game", entity_id=self.game.id, source="MLB_STATS_API",
                              external_id=GAME_PK),
        ])
        session.flush()
        self.game_id, self.home_id, self.away_id = str(self.game.id), str(self.home.id), str(self.away.id)
        add_observation(session, root, self.game, self.home, self.away, "scheduled", START - timedelta(days=1),
                        job="refresh-schedule")

    def seal(self, **kwargs) -> str:
        return seal_packet(self.packets, self.game_id, self.home_id, self.away_id, **kwargs)

    def observe(self, status: str, at: datetime, home: int | None = None, away: int | None = None, **kwargs):
        return add_observation(self.session, self.root, self.game, self.home, self.away, status, at,
                               home_score=home, away_score=away, **kwargs)

    def project(self, status: GameStatus, home: int | None = None, away: int | None = None) -> None:
        """The games-row compatibility projection the normalizer maintains."""

        game = self.session.get(Game, self.game.id)
        game.status, game.home_score, game.away_score = status, home, away
        self.session.flush()

    def settings(self, base, **extra):
        return base.model_copy(update={
            "v18_market_pilot_outcomes_export_enabled": True, "v18_market_pilot_outcomes_dir": str(self.outcomes),
            "v18_market_pilot_packet_dir": str(self.packets), **extra})

    def tick(self, base, at: datetime):
        self.session.commit()
        return outcomes.run_outcomes_tick(self.session, self.settings(base), clock=lambda: at)

    def records(self) -> list[tuple[Path, dict]]:
        entries = [e for e in seal.AttemptLedger(self.outcomes, name=outcomes.LEDGER_NAME).entries()
                   if e["kind"] == "REVISION_WRITTEN"]
        found = []
        for entry in entries:
            path = self.outcomes / entry["recordFile"]
            found.append((path, json.loads(path.read_bytes())))
        return found

    def ledger(self) -> list[dict]:
        return seal.AttemptLedger(self.outcomes, name=outcomes.LEDGER_NAME).entries()


@pytest.fixture
def world(session: Session, tmp_path: Path) -> World:
    return World(session, tmp_path)


AFTER = START + timedelta(hours=4)


# ---------------------------------------------------------------------------
# Mappings
# ---------------------------------------------------------------------------
def test_a_decisive_final_is_one_record_with_every_contract_field(world, settings):
    world.seal()
    world.observe("live", START + timedelta(minutes=30), 1, 0)
    final, run = world.observe("final", START + timedelta(hours=3), 5, 3)
    world.observe("final", START + timedelta(hours=3, minutes=30), 5, 3)    # the same revision, re-captured
    world.project(GameStatus.FINAL, 5, 3)
    summary = world.tick(settings, AFTER)
    assert summary["records"] == 1

    [(path, record)] = world.records()
    data = path.read_bytes()
    assert path.name == f"{_sha(data)}.json" and path.parent.name == world.game_id
    assert data == pilot.canonical_bytes(record) and data.decode("utf-8")
    for name in outcomes.REQUIRED_FIELDS:
        assert name in record, name
    assert record["recordType"] == "MLB_V18_MARKET_ADMITTED_OUTCOME_V1"
    assert (record["gameId"], record["homeId"], record["awayId"]) == (world.game_id, world.home_id, world.away_id)
    assert record["sourceGameIds"] == {"MLB_STATS_API": GAME_PK}
    assert record["status"] == "DECISIVE" and record["consumerAdmissionVerified"] is True
    assert (record["homeScore"], record["awayScore"]) == (5, 3)
    assert type(record["homeScore"]) is int and type(record["awayScore"]) is int
    assert record["supersedesRevisionId"] is None and len(record["revisionId"]) == 64
    assert record["sourceRevisionIdentity"] == f"MLB_STATS_API:{final.payload_checksum}"
    # The first capture of the admitted revision, and its completed writer run.
    assert record["sourceCapturedAtUtc"] == pilot.iso(START + timedelta(hours=3))
    assert record["admittedAtUtc"] == pilot.iso(START + timedelta(hours=3, seconds=90))
    assert record["finalAtUtc"] is None and "FINAL_AT_NOT_PROVIDED_BY_SOURCE" in record["reasonCodes"]
    assert set(record["clockMeanings"]) == {"sourceCapturedAtUtc", "admittedAtUtc", "finalAtUtc"}
    assert record["admission"]["sourceState"]["aispCanonicalStatus"] == "final"
    assert record["admission"]["sourceState"]["provider"]["detailedState"] == "Final"

    raw_ref, evidence_ref = record["sourceRefs"]
    raw_bytes = Path(raw_ref["path"]).read_bytes()
    assert (raw_ref["sha256"], raw_ref["bytes"]) == (_sha(raw_bytes), len(raw_bytes))
    assert raw_ref["locator"] == f"payload.schedule.dates[].games[gamePk={GAME_PK}]"
    evidence_path = path.parent / evidence_ref["path"]
    evidence_bytes = evidence_path.read_bytes()
    assert (evidence_ref["sha256"], evidence_ref["bytes"]) == (_sha(evidence_bytes), len(evidence_bytes))
    evidence = json.loads(evidence_bytes)
    assert evidence_ref["locator"] == f"/rows/game_state_observations/{final.id}"
    assert evidence["rows"]["game_state_observations"][str(final.id)]["home_score"] == 5
    assert str(run.id) in evidence["rows"]["job_runs"]
    assert all(ref["sha256"] == evidence_ref["sha256"] for ref in record["admissionRefs"])
    sums = (path.parent / "evidence" / f"{path.stem}.sha256").read_text().split()
    assert sums == [_sha(data), f"../{path.name}", evidence_ref["sha256"], f"{evidence_ref['sha256']}.json"]
    # The game directory holds only records (and the evidence subdirectory).
    assert sorted(p.name for p in path.parent.iterdir() if p.is_file()) == [path.name]
    packet_ref = record["pregamePacketRef"]
    assert _sha(Path(packet_ref["path"]).read_bytes()) == packet_ref["sha256"]


def test_a_tie_is_tie_with_integer_scores(world, settings):
    world.seal()
    world.observe("final", START + timedelta(hours=3), 4, 4)
    world.project(GameStatus.FINAL, 4, 4)
    world.tick(settings, AFTER)
    [(_, record)] = world.records()
    assert record["status"] == "TIE" and (record["homeScore"], record["awayScore"]) == (4, 4)


def test_postponed_at_the_sealed_start_is_void_known_before_first_pitch(world, settings):
    world.seal()
    world.observe("postponed", START - timedelta(minutes=8))
    world.project(GameStatus.POSTPONED)
    world.tick(settings, START - timedelta(minutes=6))              # before scheduled first pitch
    [(_, record)] = world.records()
    assert record["status"] == "VOID"
    assert (record["homeScore"], record["awayScore"], record["finalAtUtc"]) == (None, None, None)
    state = record["admission"]["sourceState"]
    assert state["aispCanonicalStatus"] == "postponed"
    assert state["provider"]["detailedState"] == "Postponed" and state["provider"]["reason"] == "Rain"

    # The makeup is another schedule episode: its final never attaches to this one.
    makeup = START + timedelta(days=1)
    world.observe("live", makeup + timedelta(minutes=20), 0, 1, scheduled=makeup)
    world.observe("final", makeup + timedelta(hours=3), 2, 6, scheduled=makeup)
    world.project(GameStatus.FINAL, 2, 6)
    world.tick(settings, makeup + timedelta(hours=4))
    assert [r["status"] for _, r in world.records()] == ["VOID"]


def test_cancelled_is_no_contest(world, settings):
    world.seal()
    world.observe("canceled", START + timedelta(minutes=40))
    world.project(GameStatus.CANCELED)
    world.tick(settings, AFTER)
    [(_, record)] = world.records()
    assert record["status"] == "NO_CONTEST"
    assert record["admission"]["sourceState"]["provider"]["detailedState"] == "Cancelled"


def test_live_is_pending_then_the_admitted_final_supersedes_it(world, settings):
    world.seal()
    world.observe("live", START + timedelta(minutes=25), 0, 0)
    world.project(GameStatus.LIVE, 0, 0)
    world.tick(settings, START + timedelta(minutes=40))
    [(pending_path, pending)] = world.records()
    before = pending_path.read_bytes()
    assert pending["status"] == "PENDING" and pending["finalAtUtc"] is None
    assert (pending["homeScore"], pending["awayScore"]) == (None, None)

    world.observe("live", START + timedelta(hours=1), 3, 1)          # still the same PENDING revision
    world.tick(settings, START + timedelta(hours=1, minutes=10))
    assert len(world.records()) == 1

    world.observe("final", START + timedelta(hours=3), 3, 2)
    world.project(GameStatus.FINAL, 3, 2)
    world.tick(settings, AFTER)
    (_, first), (_, second) = world.records()
    assert first["status"] == "PENDING" and second["status"] == "DECISIVE"
    assert second["supersedesRevisionId"] == pending["revisionId"]
    assert pending_path.read_bytes() == before


def test_suspended_or_only_scheduled_writes_nothing_but_says_why_once(world, settings):
    world.seal()
    # MLB "Suspended: Rain" maps to AISP's canonical "scheduled": not a positive admission.
    world.observe("scheduled", START + timedelta(minutes=50))
    world.tick(settings, AFTER)
    world.tick(settings, AFTER + timedelta(minutes=10))
    assert world.records() == []
    reasons = [e for e in world.ledger() if e["kind"] == "NO_RECORD"]
    assert [e["code"] for e in reasons] == ["NO_ADMITTED_OUTCOME_STATE"]


def test_a_final_waits_for_its_writer_run_to_complete(world, settings):
    world.seal()
    _, run = world.observe("final", START + timedelta(hours=3), 7, 2, in_flight=True)
    world.project(GameStatus.FINAL, 7, 2)
    world.tick(settings, AFTER)
    assert world.records() == []
    assert world.ledger()[-1]["code"] == "ADMISSION_RECEIPT_WRITER_RUN_IN_FLIGHT"
    done = START + timedelta(hours=3, minutes=6)
    stored = world.session.get(JobRun, run.id)
    stored.status, stored.completed_at = RunStatus.SUCCEEDED, done
    world.tick(settings, AFTER)
    [(_, record)] = world.records()
    assert record["admittedAtUtc"] == pilot.iso(done)


def test_a_final_with_no_play_at_the_sealed_start_is_not_attributed(world, settings):
    world.seal()
    other = START + timedelta(hours=5)                    # rescheduled without a postponement on record
    world.observe("final", other + timedelta(hours=3), 1, 0, scheduled=other)
    world.project(GameStatus.FINAL, 1, 0)
    world.tick(settings, other + timedelta(hours=4))
    assert world.records() == []
    assert world.ledger()[-1]["code"] == "ADMITTED_FINAL_NOT_ATTRIBUTABLE_TO_SEALED_START"


# ---------------------------------------------------------------------------
# Scope: authentic seals only
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("kind", ["SEAL_REFUSED", "MISSED_SEAL_DEADLINE", "SEALED_LATE_NOT_PROSPECTIVE"])
def test_an_unsealed_game_gets_no_record(world, settings, kind):
    world.seal(kind=kind)
    world.observe("final", START + timedelta(hours=3), 5, 3)
    world.project(GameStatus.FINAL, 5, 3)
    summary = world.tick(settings, AFTER)
    assert summary["sealedEpisodes"] == 0 and summary["records"] == 0
    assert not (world.outcomes / world.game_id).exists()


def test_no_attempt_ledger_means_nothing_is_read(world, settings):
    world.observe("final", START + timedelta(hours=3), 5, 3)
    world.project(GameStatus.FINAL, 5, 3)
    assert world.tick(settings, AFTER)["records"] == 0
    assert not (world.outcomes / world.game_id).exists()


def test_a_seal_whose_packet_does_not_match_its_ledger_row_is_not_read(world, settings):
    key = world.seal(ledger_sha="f" * 64)
    world.observe("final", START + timedelta(hours=3), 5, 3)
    world.project(GameStatus.FINAL, 5, 3)
    world.tick(settings, AFTER)
    world.tick(settings, AFTER + timedelta(minutes=10))
    assert world.records() == []
    refusals = [e for e in world.ledger() if e["kind"] == "SEALED_PACKET_NOT_VERIFIED"]
    assert [(e["episodeKey"], e["code"]) for e in refusals] == [(key, "SEALED_PACKET_SHA256_MISMATCH")]


def test_a_broken_attempt_ledger_chain_stops_every_record(world, settings):
    world.seal()
    with open(world.packets / seal.LEDGER_NAME, "ab") as stream:
        stream.write(b'{"seq":3,"prevLineSha256":"forged","kind":"SEALED"}\n')
    world.observe("final", START + timedelta(hours=3), 5, 3)
    world.project(GameStatus.FINAL, 5, 3)
    summary = world.tick(settings, AFTER)
    assert summary["records"] == 0 and summary["systemic"][0] == "ATTEMPT_LEDGER_HASH_CHAIN_BROKEN"
    world.tick(settings, AFTER + timedelta(minutes=10))
    assert [e["kind"] for e in world.ledger()] == ["SYSTEMIC_STOP"]


def test_identity_that_no_longer_matches_the_sealed_packet_is_refused(world, settings):
    world.seal()
    mapping = world.session.scalar(select(ExternalIdMapping).where(ExternalIdMapping.entity_type == "game"))
    mapping.external_id = "999999"
    world.observe("final", START + timedelta(hours=3), 5, 3)
    world.project(GameStatus.FINAL, 5, 3)
    world.tick(settings, AFTER)
    assert world.records() == []
    assert world.ledger()[-1]["code"] == "IDENTITY_DIFFERS_FROM_SEALED_PACKET"


# ---------------------------------------------------------------------------
# Write-once, corrections
# ---------------------------------------------------------------------------
def test_records_are_write_once_and_an_unchanged_state_writes_nothing(world, settings):
    world.seal()
    world.observe("final", START + timedelta(hours=3), 5, 3)
    world.project(GameStatus.FINAL, 5, 3)
    world.tick(settings, AFTER)
    [(path, _)] = world.records()
    before, ledger_before = path.read_bytes(), world.ledger()
    assert not os.access(path, os.W_OK)
    world.observe("final", START + timedelta(hours=4), 5, 3)          # another refresh, same revision
    second = world.tick(settings, AFTER + timedelta(hours=1))
    assert second["records"] == 0 and world.ledger() == ledger_before
    assert path.read_bytes() == before
    assert sorted(p.name for p in path.parent.glob("*.json")) == [path.name]
    with pytest.raises(FileExistsError):
        seal.write_once(path, b"{}")
    assert outcomes.work_due(world.session, world.settings(settings)) is False


def test_a_revision_written_before_a_crash_is_adopted_not_written_twice(world, settings, monkeypatch):
    world.seal()
    world.observe("final", START + timedelta(hours=3), 5, 3)
    world.project(GameStatus.FINAL, 5, 3)
    original = seal.AttemptLedger.append

    def crash(self, record, *, now):
        if record.get("kind") == "REVISION_WRITTEN":
            raise RuntimeError("process died before the ledger line")
        return original(self, record, now=now)

    monkeypatch.setattr(seal.AttemptLedger, "append", crash)
    with pytest.raises(RuntimeError):
        world.tick(settings, AFTER)
    monkeypatch.setattr(seal.AttemptLedger, "append", original)
    game_dir = world.outcomes / world.game_id
    [orphan] = list(game_dir.glob("*.json"))
    world.tick(settings, AFTER + timedelta(minutes=10))
    [(path, _)] = world.records()
    assert path == orphan and list(game_dir.glob("*.json")) == [orphan]
    assert world.ledger()[-1]["adoptedExistingFile"] is True


def test_a_score_correction_is_a_new_file_that_supersedes_and_keeps_the_original(world, settings):
    world.seal()
    world.observe("final", START + timedelta(hours=3), 5, 3)
    world.project(GameStatus.FINAL, 5, 3)                             # frozen at the first FINAL
    world.tick(settings, AFTER)
    [(first_path, first)] = world.records()
    first_bytes = first_path.read_bytes()
    corrected, _ = world.observe("final", START + timedelta(hours=9), 5, 4)
    world.tick(settings, START + timedelta(hours=10))
    (path_a, a), (path_b, b) = world.records()
    assert path_a == first_path and path_a.read_bytes() == first_bytes
    assert b["supersedesRevisionId"] == first["revisionId"] and b["revisionId"] != first["revisionId"]
    assert (b["homeScore"], b["awayScore"]) == (5, 4)
    assert b["sourceRevisionIdentity"] == f"MLB_STATS_API:{corrected.payload_checksum}"
    assert "GAMES_PROJECTION_DIFFERS_FROM_ADMITTED_OBSERVATION" in b["reasonCodes"]
    assert outcomes.revision_id(b["contentId"], first["revisionId"]) == b["revisionId"]


# ---------------------------------------------------------------------------
# Never from P/L
# ---------------------------------------------------------------------------
def test_no_winner_or_score_is_inferred_from_a_position(world, settings):
    world.seal()
    market = Market(game_id=world.game.id, market_type=MarketType.MONEYLINE, period="full_game")
    world.session.add(market)
    world.session.flush()
    outcome = MarketOutcome(market_id=market.id, outcome_key="bal", label="BAL", participant_team_id=world.home.id)
    world.session.add(outcome)
    world.session.flush()
    position = OfficialAspPosition(market_outcome_id=outcome.id, placed_at=START - timedelta(minutes=20), stake=10,
                                   decimal_odds=1.9, result=PositionResult.WIN, status=PositionStatus.SETTLED,
                                   strategy="S")
    world.session.add(position)
    world.session.flush()
    world.session.add(PositionSettlement(position_id=position.id, result=PositionResult.WIN,
                                         settled_at=AFTER, profit_loss=9, game_home_score=9, game_away_score=1))
    world.project(GameStatus.FINAL, 9, 1)            # the projection settlement reads -- not admitted evidence
    world.tick(settings, AFTER)
    assert world.records() == []
    assert world.ledger()[-1]["code"] == "NO_ADMITTED_OUTCOME_STATE"
    source = inspect.getsource(outcomes)
    for name in ("OfficialAspPosition", "PositionSettlement", "position_results", "official_asp_positions"):
        assert name not in source
    with pytest.raises(outcomes.OutcomeExportRefused):
        outcomes.assert_no_pnl_fields({"sourceRefs": [{"profit_loss": 9}]})


# ---------------------------------------------------------------------------
# The job, the lane, isolation
# ---------------------------------------------------------------------------
def test_the_tick_is_inert_when_disabled(world, settings):
    world.seal()
    world.observe("final", START + timedelta(hours=3), 5, 3)
    world.session.commit()
    off = world.settings(settings, v18_market_pilot_outcomes_export_enabled=False)
    result = outcomes.run_outcomes_tick(world.session, off)
    assert result["enabled"] is False and result["records"] == 0
    assert not world.outcomes.exists()
    assert outcomes.work_due(world.session, off) is False


def test_work_is_due_until_the_record_is_written(world, settings):
    world.seal()
    world.observe("final", START + timedelta(hours=3), 5, 3)
    world.project(GameStatus.FINAL, 5, 3)
    world.session.commit()
    on = world.settings(settings)
    assert outcomes.work_due(world.session, on) is True
    assert not world.outcomes.exists()                                   # the check writes nothing
    world.tick(settings, AFTER)
    assert outcomes.work_due(world.session, on) is False


def test_the_outcomes_job_runs_on_the_research_export_lane_only_when_enabled(settings):
    assert outcomes.SCHEDULER_ID == "asp-rx-v18-market-pilot-outcomes"
    assert lanes.lane_for(outcomes.SCHEDULER_ID) == lanes.RESEARCH_EXPORT_LANE
    off = build_scheduler(settings.model_copy(update={"scheduler_core_jobs_enabled": True,
                                                      "v18_market_pilot_outcomes_export_enabled": False}))
    assert off.get_job(outcomes.SCHEDULER_ID) is None
    on = build_scheduler(settings.model_copy(update={"scheduler_core_jobs_enabled": True,
                                                     "v18_market_pilot_export_enabled": False,
                                                     "v18_market_pilot_outcomes_export_enabled": True}))
    job = on.get_job(outcomes.SCHEDULER_ID)
    assert job is not None and inspect.iscoroutinefunction(job.func)
    assert getattr(job.func, "__aisp_job_lane__") == lanes.RESEARCH_EXPORT_LANE
    assert getattr(job.func, "__aisp_off_event_loop__") is True
    assert job.trigger.interval == timedelta(minutes=outcomes.INTERVAL_MINUTES) == timedelta(minutes=10)
    assert job.max_instances == 1
    core_only = build_scheduler(settings.model_copy(update={"scheduler_core_jobs_enabled": False,
                                                            "v18_market_pilot_outcomes_export_enabled": True}))
    assert core_only.get_job(outcomes.SCHEDULER_ID) is None


def test_the_job_is_a_registered_orchestrator_handler():
    from app.jobs.orchestrator import AVAILABLE_JOBS, JobOrchestrator

    assert outcomes.JOB_NAME in AVAILABLE_JOBS
    assert inspect.iscoroutinefunction(JobOrchestrator.export_v18_market_pilot_outcomes)


async def test_the_orchestrator_runs_the_export_in_its_own_read_only_session(world, settings):
    from app.jobs.orchestrator import JobOrchestrator

    world.seal()
    world.observe("final", START + timedelta(hours=3), 5, 3)
    world.project(GameStatus.FINAL, 5, 3)
    world.session.commit()
    run = await JobOrchestrator(world.session, world.settings(settings)).run(outcomes.JOB_NAME)
    assert run.status == RunStatus.SUCCEEDED and run.result["records"] == 1
    assert [r["status"] for _, r in world.records()] == ["DECISIVE"]


def test_the_pregame_modules_never_import_the_outcomes_module():
    for module in (pilot, seal):
        source = inspect.getsource(module)
        assert "v18_market_pilot_outcomes" not in source
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom):
                assert "outcomes" not in (node.module or "") and all("outcomes" not in a.name for a in node.names)
            elif isinstance(node, ast.Import):
                assert all("outcomes" not in a.name for a in node.names)


def test_nothing_is_written_into_the_packets_directory(world, settings):
    world.seal()
    world.observe("final", START + timedelta(hours=3), 5, 3)
    world.project(GameStatus.FINAL, 5, 3)
    before = {p.name: p.read_bytes() for p in world.packets.iterdir()}
    world.tick(settings, AFTER)
    assert {p.name: p.read_bytes() for p in world.packets.iterdir()} == before
    world.session.commit()
    same = world.settings(settings, v18_market_pilot_outcomes_dir=str(world.packets))
    assert outcomes.run_outcomes_tick(world.session, same)["refused"] == "OUTCOMES_DIR_OVERLAPS_PACKET_DIR"
    inside = world.settings(settings, v18_market_pilot_outcomes_dir=str(world.packets / "outcomes"))
    assert outcomes.run_outcomes_tick(world.session, inside)["refused"] == "OUTCOMES_DIR_OVERLAPS_PACKET_DIR"
    assert {p.name: p.read_bytes() for p in world.packets.iterdir()} == before


def test_reads_run_in_a_read_only_transaction_that_is_rolled_back():
    class Dialect:
        name = "postgresql"

    class Bind:
        dialect = Dialect()

    class Result:
        def scalar(self):
            return "on"

    class FakeSession:
        new, dirty, deleted = (), (), ()
        statements: list[str] = []
        rolled_back = False

        def get_bind(self):
            return Bind()

        def execute(self, statement):
            self.statements.append(str(statement))
            return Result()

        def rollback(self):
            self.rolled_back = True

        @property
        def no_autoflush(self):
            from contextlib import nullcontext
            return nullcontext()

    fake = FakeSession()
    with outcomes.read_only(fake):
        assert fake.statements[0] == "SET TRANSACTION READ ONLY"
    assert fake.rolled_back is True
    dirty = FakeSession()
    dirty.new = (object(),)
    with pytest.raises(outcomes.OutcomeExportRefused):
        with outcomes.read_only(dirty):
            pass


# ---------------------------------------------------------------------------
# With a packet sealed by the real sealer
# ---------------------------------------------------------------------------
def test_a_really_sealed_packet_binds_its_own_identity(session, settings, tmp_path):
    from tests.test_v18_market_pilot_export import GAME_PK as PILOT_PK, LOCK as PILOT_LOCK, START as PILOT_START
    from tests.test_v18_market_pilot_export import World as PilotWorld

    (tmp_path / "pilot").mkdir()
    pw = PilotWorld(session, tmp_path / "pilot")
    game = pilot.read_game(session, pw.game.id)
    sealed = seal.seal_game(session, game, inputs=pw.inputs(), directory=pw.packets,
                            clock=lambda: PILOT_LOCK + timedelta(minutes=1))
    assert sealed.sealed
    add_observation(session, tmp_path, pw.game, pw.home, pw.away, "final", PILOT_START + timedelta(hours=3),
                    home_score=6, away_score=2, scheduled=PILOT_START, game_pk=PILOT_PK)
    stored = session.get(Game, pw.game.id)
    stored.status, stored.home_score, stored.away_score = GameStatus.FINAL, 6, 2
    session.commit()
    on = settings.model_copy(update={
        "v18_market_pilot_outcomes_export_enabled": True, "v18_market_pilot_packet_dir": str(pw.packets),
        "v18_market_pilot_outcomes_dir": str(tmp_path / "consumer-outcomes")})
    summary = outcomes.run_outcomes_tick(session, on, clock=lambda: PILOT_START + timedelta(hours=4))
    assert summary["records"] == 1
    packet = json.loads((pw.packets / f"{sealed.episode_key}.json").read_bytes())
    [record_path] = list((tmp_path / "consumer-outcomes" / packet["game"]["canonicalGameId"]).glob("*.json"))
    record = json.loads(record_path.read_bytes())
    for field_name in ("sourceGameIds", "homeId", "awayId"):
        assert record[field_name] == packet["game"][field_name]
    assert record["gameId"] == packet["game"]["canonicalGameId"]
    assert record["episodeKey"] == packet["episodeKey"]
    assert record["pregamePacketRef"]["sha256"] == sealed.packet_sha256
    assert (record["status"], record["homeScore"], record["awayScore"]) == ("DECISIVE", 6, 2)

"""Prospective model-market capture, pinned against the FROZEN consumer contract.

These tests pin the properties that make the exporter's claims true rather than
merely intended, one group per obligation:

* the record IS ``MODEL_MARKET_OBSERVATION_V1`` and BINDS both frozen hashes,
  re-hashed from the files on disk;
* ONE canonical record per game and checkpoint -- an exclusion is that same
  record with ``admission.state = EXCLUDED``, never a second competing file;
* ``positionId`` is JSON null and the ``NOT_APPLICABLE`` sentinel is gone;
* a null genuine completion time keeps the one accepted basis;
* home, away AND tie channels as published, with MLB tie zero and NFL carrying a
  separately named decisive-conditioned home probability;
* ``checkpointAtUtc`` is a fixed T-15 and the decision's clock stays separate;
* ambiguous probability channels are EXCLUDED and never resolved by recency;
* book selection is deterministic, model-independent, and publishes the COMPLETE
  candidate list;
* missing settlement semantics yield UNPRICED, never a guess;
* the read-only guarantee's five properties, none of which is a row count.
"""
from __future__ import annotations

import ast
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models import (
    ExperimentDecisionPhase, Game, GameStateObservation, GameStatus, GameTeam, League, Market,
    MarketOutcome, MarketType, NflFinalObservation, OddsSnapshot, OfficialAspPosition,
    PositionResult, PositionStatus, Season, ShadowExperiment, ShadowExperimentDecision,
    ShadowForecast, ShadowForecastBundle, Sport, Sportsbook, Team,
)
from app.research_exports import prospective_capture as capture

#: Post-freeze on purpose: FROZEN_PROTOCOL.json froze at 2026-09-21T18:32:03.746810Z
#: and enrollment closes 2026-10-01, so this slate is inside the window.
KICKOFF = datetime(2026, 9, 23, 23, 0, tzinfo=UTC)
CHECKPOINT = KICKOFF - timedelta(minutes=15)
#: When the exporter observes. Always AFTER the checkpoint plus its settling
#: margin: a record may only be written once the checkpoint's evidence is fixed.
CAPTURE_AT = CHECKPOINT + capture.CAPTURE_SETTLING
QUOTE_AT = CHECKPOINT - timedelta(minutes=5)
WINDOW = (KICKOFF - timedelta(hours=6), KICKOFF + timedelta(hours=6))
FROZEN_DIR = Path(
    r"C:\Users\james\asp-model-studio\outputs\model-market-prospective-comparison-20260921-v1")

#: A convention set a BOOK has stated, used only to exercise the priced path.
#: Keyed by sportsbook, as production is: the feed relays several books and each
#: settles by its own rules. A separate test pins what production binds.
_TEST_NFL = {"settlementRule": "TWO_WAY_MONEYLINE_OFFICIAL_TIE_REFUNDS",
             "overtimeRule": "INCLUDED"}
_TEST_MLB = {"settlementRule": "WHOLE_GAME_WINNER", "extraInningsRule": "INCLUDED",
             "pitcherActionRule": "ACTION"}
TEST_CONVENTIONS = {
    **{("NFL", book): dict(_TEST_NFL) for book in ("sgo:alpha", "sgo:bravo", "sgo:charlie")},
    **{("MLB", book): dict(_TEST_MLB) for book in ("sgo:alpha", "sgo:bravo", "sgo:charlie")},
}


@pytest.fixture
def bound_conventions(monkeypatch) -> None:
    monkeypatch.setattr(capture, "BOUND_CONVENTIONS", dict(TEST_CONVENTIONS))


# ---------------------------------------------------------------------------
# A small retained world, for both sports
# ---------------------------------------------------------------------------
class World:
    """One slate per sport: funded, qualified-but-unfunded, declined, and bare."""

    def __init__(self, session: Session, root: Path, *, league: str = "NFL") -> None:
        self.session = session
        self.dir = root / "capture"
        self.league_code = league
        sport = Sport(code="FOOTBALL" if league == "NFL" else "BASEBALL", name=league)
        session.add(sport)
        session.flush()
        self.league = League(sport_id=sport.id, code=league, name=league)
        session.add(self.league)
        session.flush()
        self.season = Season(league_id=self.league.id, name="2026",
                             starts_at=KICKOFF - timedelta(days=30),
                             ends_at=KICKOFF + timedelta(days=120))
        session.add(self.season)
        self.books: dict[str, Sportsbook] = {}
        for key in ("sgo:alpha", "sgo:bravo", "sgo:charlie"):
            book = Sportsbook(key=key, name=key, region="us", active=True)
            session.add(book)
            self.books[key] = book
        session.flush()
        self.experiment = self._experiment("PRIMARY", version=1)
        self.bundle = ShadowForecastBundle(
            bundle_sha256=uuid.uuid4().hex, bundle_id=f"b-{uuid.uuid4().hex[:8]}",
            logical_uri="test://bundle", source_fixture_id="fix-1", slate_date=KICKOFF.date(),
            league=league, sport=sport.code, status="IMPORTED", research_shadow_only=True,
            generated_at=KICKOFF, forecast_count=1, quote_count=0, candidate_count=1,
            imported_at=KICKOFF, contract_version="v1", evidence={})
        session.add(self.bundle)
        session.flush()
        self.games: dict[str, dict] = {}

    def _experiment(self, name: str, *, version: int) -> ShadowExperiment:
        row = ShadowExperiment(
            name=f"{self.league_code} {name}", version=version,
            started_at=KICKOFF - timedelta(days=7), start_date=date(2026, 9, 13),
            starting_bankroll=1000, sport=self.league_code,
            recommendation_policy_version="AISP_PAPER_POLICY_V1",
            staking_policy_version="AISP_QUARTER_KELLY_V1",
            decision_policy="AISP_PAPER_POLICY_V1", controlling_engine="M1",
            supported_markets=["MONEYLINE"], kelly_fraction=0.25, unit_percentage=0.01,
            max_position_percentage=0.005, max_same_game_percentage=0.01,
            max_daily_percentage=0.02, code_commit="4e2f296", code_identity="test",
            is_active=True)
        self.session.add(row)
        self.session.flush()
        return row

    # -- building blocks ---------------------------------------------------
    def add_game(self, tag: str, *, priced: bool = True, starts_at: datetime = KICKOFF) -> dict:
        home = Team(league_id=self.league.id, name=f"{tag} home", abbreviation=f"{tag}H")
        away = Team(league_id=self.league.id, name=f"{tag} away", abbreviation=f"{tag}A")
        self.session.add_all([home, away])
        self.session.flush()
        game = Game(league_id=self.league.id, season_id=self.season.id, starts_at=starts_at,
                    status=GameStatus.SCHEDULED)
        self.session.add(game)
        self.session.flush()
        self.session.add_all([
            GameTeam(game_id=game.id, team_id=home.id, is_home=True),
            GameTeam(game_id=game.id, team_id=away.id, is_home=False)])
        entry: dict = {"game": game, "home": home, "away": away, "starts_at": starts_at}
        if priced:
            market = Market(game_id=game.id, market_type=MarketType.MONEYLINE, period="full_game")
            self.session.add(market)
            self.session.flush()
            home_outcome = MarketOutcome(market_id=market.id, outcome_key="home:None",
                                         label="home", participant_team_id=home.id)
            away_outcome = MarketOutcome(market_id=market.id, outcome_key="away:None",
                                         label="away", participant_team_id=away.id)
            self.session.add_all([home_outcome, away_outcome])
            self.session.flush()
            entry |= {"market": market, "home_outcome": home_outcome,
                      "away_outcome": away_outcome, "snapshots": {}}
            self.price(entry, "sgo:alpha", home=-142, away=124, observed_at=QUOTE_AT)
        self.session.flush()
        self.games[tag] = entry
        return entry

    def price(self, entry: dict, book_key: str, *, home: int, away: int,
              observed_at: datetime, home_observed_at: datetime | None = None,
              receipt_at: datetime | None = None, source: str = "SPORTSGAMEODDS",
              away_source: str | None = None,
              away_receipt_at: datetime | None = None,
              event_id: str | None = None) -> dict[str, OddsSnapshot]:
        """One book's two legs, from one fetch unless a test says otherwise."""

        receipt = receipt_at or observed_at
        event = event_id or f"evt-{entry['game'].id}"
        pair = {
            "HOME": self._quote(entry["home_outcome"], book_key, home,
                                home_observed_at or observed_at, receipt, source, event),
            "AWAY": self._quote(entry["away_outcome"], book_key, away, observed_at,
                                away_receipt_at or receipt, away_source or source, event),
        }
        entry.setdefault("snapshots", {})[book_key] = pair
        return pair

    def _quote(self, outcome: MarketOutcome, book_key: str, american: int, observed_at: datetime,
               receipt_at: datetime, source: str, event_id: str) -> OddsSnapshot:
        snapshot = OddsSnapshot(
            market_outcome_id=outcome.id, sportsbook_id=self.books[book_key].id,
            observed_at=observed_at, available_as_of=receipt_at, american_odds=american,
            decimal_odds=1 + (american / 100 if american > 0 else 100 / abs(american)),
            source=source, source_event_id=event_id, source_mode="LIVE",
            payload_checksum=uuid.uuid4().hex)
        self.session.add(snapshot)
        self.session.flush()
        return snapshot

    def forecast(self, entry: dict, *, exposed_at: datetime = CHECKPOINT - timedelta(minutes=20),
                 p_home: float = 0.42, p_away: float | None = None,
                 snapshot_type: str = "FINAL_PREGAME") -> ShadowForecast:
        away = (1 - p_home) if p_away is None else p_away
        row = ShadowForecast(
            bundle_id=self.bundle.id,
            forecast_id=f"asp-sports-game-forecast:sha256:{uuid.uuid4().hex}",
            engine_id="M1", engine_version="2026.09.1",
            model_artifact_sha256=uuid.uuid4().hex, simulator_id="sim", simulator_version="1",
            simulator_seed=1, event_id=f"e-{uuid.uuid4().hex[:8]}",
            source_event_id=f"src-{uuid.uuid4().hex[:8]}", event_date=KICKOFF.date(),
            season=2026, home_team_id="H", away_team_id="A", is_ready=True, readiness_score=99.0,
            readiness_threshold=50.0, status="READY", full_home_win_probability=p_home,
            full_away_win_probability=away, full_expected_home_score=21.0,
            full_expected_away_score=23.0, distributions={}, uncertainty={}, readiness_detail={},
            forecast_schema_version="1", snapshot_type=snapshot_type, snapshot_version=1,
            identity_evidence={}, lineage_detail={}, canonical_game_id=entry["game"].id,
            available_as_of_utc=exposed_at,
            forecast_cutoff_utc=exposed_at - timedelta(minutes=10))
        self.session.add(row)
        self.session.flush()
        return row

    def decide(self, entry: dict, *, forecast: ShadowForecast | None = None,
               qualification: str | None = "QUALIFIED", execution: str | None = "EXECUTED",
               recommendation: str = "QUALIFIED_BET", selection: str = "AWAY",
               at: datetime = CHECKPOINT - timedelta(minutes=2),
               imported_at: datetime | None = None, no_import: bool = False,
               model_probability: float | None = 0.58, evidence: dict | None = None,
               experiment: ShadowExperiment | None = None,
               sportsbook: str = "sgo:alpha",
               supplement: str | None = "supplement-1",
               names_snapshot: bool = True) -> ShadowExperimentDecision:
        snapshots = entry.get("snapshots", {}).get(sportsbook, {})
        named = snapshots.get("AWAY" if selection == "AWAY" else "HOME")
        row = ShadowExperimentDecision(
            experiment_id=(experiment or self.experiment).id, game_id=entry["game"].id,
            slate_date=KICKOFF.date(), phase=ExperimentDecisionPhase.FINAL_PREGAME,
            sport=self.league_code, market="MONEYLINE", period="full_game", selection=selection,
            sportsbook=sportsbook, american_odds=124, decimal_odds=2.24,
            market_outcome_id=entry["away_outcome"].id if "away_outcome" in entry else None,
            odds_snapshot_id=named.id if (named is not None and names_snapshot) else None,
            quote_observed_at=QUOTE_AT,
            shadow_forecast_id=forecast.id if forecast is not None else None,
            recommended_at=at, model_probability=model_probability, expected_value=0.05,
            recommendation=recommendation, recommendation_reason="test",
            qualification_reasons={}, qualification_outcome=qualification,
            qualification_reason="value", execution_outcome=execution,
            execution_reason="staking",
            controlling_forecast_identity=forecast.forecast_id if forecast is not None else None,
            forecast_snapshot_phase="FINAL_PREGAME",
            forecast_published_at=at - timedelta(minutes=40),
            forecast_imported_at=(None if no_import
                                  else (imported_at or (at - timedelta(minutes=30)))),
            eligibility_supplement_id=supplement,
            source_mode="LIVE", evidence=evidence or {})
        self.session.add(row)
        self.session.flush()
        return row

    def fund(self, entry: dict, decision: ShadowExperimentDecision) -> OfficialAspPosition:
        position = OfficialAspPosition(
            shadow_experiment_id=self.experiment.id, shadow_decision_id=decision.id,
            shadow_forecast_id=decision.shadow_forecast_id,
            market_outcome_id=entry["away_outcome"].id,
            odds_snapshot_id=entry["snapshots"]["sgo:alpha"]["AWAY"].id, placed_at=CHECKPOINT,
            stake=5.0, decimal_odds=2.24, result=PositionResult.PENDING,
            status=PositionStatus.OPEN,
            recommendation_policy_version="AISP_PAPER_POLICY_V1",
            staking_policy_version="AISP_QUARTER_KELLY_V1")
        self.session.add(position)
        self.session.flush()
        return position

    def nfl_final(self, entry: dict, *, home_score: int = 17, away_score: int = 24,
                  status: str = "FINAL", retrieved_at: datetime | None = None,
                  overtime: bool = False) -> NflFinalObservation:
        row = NflFinalObservation(
            game_id=entry["game"].id, source_event_id="2026_03_AA_HH", source="NFLVERSE",
            source_mode="RESEARCH_SHADOW", result_tier=1, status=status, home_score=home_score,
            away_score=away_score, overtime=overtime, season=2026, game_type="REG", week=3,
            source_last_updated="2026-09-23 21:44:03 US/Eastern",
            retrieved_at=retrieved_at or (KICKOFF + timedelta(hours=4)),
            payload_checksum=uuid.uuid4().hex, feed_checksum=uuid.uuid4().hex, raw_row={})
        self.session.add(row)
        self.session.flush()
        return row

    def state_final(self, entry: dict, *, home_score: int = 5, away_score: int = 3,
                    final_at: datetime | None = None,
                    available_as_of: datetime | None = None) -> GameStateObservation:
        row = GameStateObservation(
            game_id=entry["game"].id, scheduled_at=entry["starts_at"], status="FINAL",
            home_score=home_score, away_score=away_score, final_at=final_at,
            observed_at=KICKOFF + timedelta(hours=3),
            available_as_of=available_as_of or (KICKOFF + timedelta(hours=3)),
            source="MLB_STATS_API", source_mode="LIVE", payload_checksum=uuid.uuid4().hex)
        self.session.add(row)
        self.session.flush()
        return row


@pytest.fixture
def world(session: Session, settings, artifact_root: Path) -> World:
    built = World(session, artifact_root, league="NFL")
    settings.prospective_capture_enabled = True
    settings.prospective_capture_dir = str(built.dir)
    built.settings = settings
    return built


@pytest.fixture
def mlb(session: Session, settings, artifact_root: Path) -> World:
    built = World(session, artifact_root, league="MLB")
    settings.prospective_capture_enabled = True
    settings.prospective_capture_dir = str(built.dir)
    built.settings = settings
    return built


def run(world: World, **kwargs) -> dict:
    # The exporter's read transaction always rolls back, so the retained world has
    # to be committed first -- otherwise the ROLLBACK, not the exporter, is what
    # empties the fixture, and a second tick would see an empty slate.
    world.session.commit()
    return capture.run_prospective_capture(
        world.session, world.settings,
        **{"now": CAPTURE_AT, "window_start": WINDOW[0], "window_end": WINDOW[1], **kwargs})


def records_on_disk(world: World, game_id: str) -> list[Path]:
    directory = world.dir / capture.RECORDS_DIR / game_id
    return sorted(directory.glob("*.json")) if directory.exists() else []


def only_record(world: World, entry: dict) -> dict:
    paths = records_on_disk(world, str(entry["game"].id))
    assert len(paths) == 1, [p.name for p in paths]
    return json.loads(paths[0].read_bytes())


def ready_game(world: World, tag: str, **decide) -> dict:
    entry = world.add_game(tag)
    forecast = world.forecast(entry)
    entry["forecast"] = forecast
    entry["decision"] = world.decide(entry, forecast=forecast, **decide)
    return entry


# ==========================================================================
# It is OFF, it binds the frozen contract, and it owns no scheduler
# ==========================================================================

def test_it_is_disabled_by_default_and_inert_when_off(session: Session, settings, artifact_root):
    settings.prospective_capture_enabled = False
    settings.prospective_capture_dir = str(artifact_root / "capture")
    summary = capture.run_prospective_capture(session, settings)
    assert summary["enabled"] is False
    assert summary["records"] == 0
    assert summary["contract"] == capture.CONTRACT
    assert not (artifact_root / "capture").exists()


def test_the_default_setting_is_false():
    from app.core.config import Settings

    assert Settings.model_fields["prospective_capture_enabled"].default is False


def test_the_record_type_and_both_frozen_hashes_are_bound_and_verified_from_disk():
    """Disposition 1. The binding is a CHECK, not a claim: the files are re-hashed."""

    assert capture.CONTRACT == "MODEL_MARKET_OBSERVATION_V1"
    checked = capture.verify_contract_files(FROZEN_DIR)
    assert checked[capture.CONTRACT_FILE]["sha256"] == capture.CONTRACT_SHA256
    assert checked[capture.PROTOCOL_FILE]["sha256"] == capture.PROTOCOL_SHA256


def test_a_tampered_contract_file_is_refused(tmp_path: Path):
    (tmp_path / capture.CONTRACT_FILE).write_bytes(b"not the frozen contract")
    (tmp_path / capture.PROTOCOL_FILE).write_bytes(b"{}")
    with pytest.raises(capture.CaptureRefused) as error:
        capture.verify_contract_files(tmp_path)
    assert error.value.code == "FROZEN_CONTRACT_FILE_HASH_MISMATCH"


def test_it_registers_no_scheduled_job_and_never_takes_the_decision_lane():
    """It reuses an EXISTING research-export owner; it creates no owner of its own."""

    from app.jobs import lanes, scheduler

    import ast

    source = Path(scheduler.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    # No job of its own: nothing the scheduler registers names the capture.
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "add_job":
            text = ast.get_source_segment(source, node) or ""
            assert "prospective" not in text.lower(), text
    # The scheduler never imports the capture module; the owner's orchestrator does.
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [getattr(node, "module", "") or ""] + [a.name for a in node.names]
            assert not any("prospective_capture" in name for name in names), names
    # The ONE permitted mention: the existing owner reads the enable flag so it
    # still fires when no MLB V2 outcome work is due. Anywhere else would mean
    # the capture had grown a scheduling surface of its own.
    owner = next(node for node in ast.walk(tree)
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.name == "execute_v18_market_pilot_outcomes_v2")
    owner_text = ast.get_source_segment(source, owner) or ""
    outside = source.replace(owner_text, "")
    assert "prospective_capture" not in outside
    assert "prospective-capture" not in source
    assert "prospective_capture_enabled" in owner_text
    assert capture.INVOCATION_STATE == "PREPARED_NOT_ACTIVATED"
    assert capture.INVOCATION_LANE == lanes.RESEARCH_EXPORT_LANE
    assert capture.INVOCATION_JOB_PREFIX == lanes.RESEARCH_EXPORT_JOB_PREFIX
    # The owner is an existing registered job, and it is NOT on the decision lane.
    assert lanes.lane_for(capture.INVOCATION_OWNER_SCHEDULER_ID) == lanes.RESEARCH_EXPORT_LANE
    assert lanes.lane_for(capture.INVOCATION_OWNER_SCHEDULER_ID) != lanes.DECISION_CHECKPOINT_LANE
    assert capture.INVOCATION_OWNER_SCHEDULER_ID in source


def test_the_prepared_invocation_hook_is_inert_while_the_flag_is_off(session: Session, settings):
    settings.prospective_capture_enabled = False
    result = capture.research_export_tick_hook(session, settings)
    assert result == {
        "enabled": False, "state": "PREPARED_NOT_ACTIVATED", "lane": "research-export",
        "owner": capture.INVOCATION_OWNER_JOB,
        "note": "Prepared invocation path; the flag is off so this tick did nothing."}


def test_the_existing_owner_calls_the_hook_and_a_hook_failure_never_fails_the_owner():
    from app.jobs import orchestrator

    source = Path(orchestrator.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    handler = next(node for node in ast.walk(tree)
                   if isinstance(node, ast.AsyncFunctionDef)
                   and node.name == "export_v18_market_pilot_outcomes_v2")
    body = ast.dump(handler)
    assert "research_export_tick_hook" in body
    assert "ExceptHandler" in body  # the hook cannot fail the owner


def test_the_exporter_imports_no_provider_client():
    """Checked structurally: prose may NAME a provider, an import may not exist."""

    tree = ast.parse(capture.module_source())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported |= {node.module or ""}
    for name in imported:
        assert not name.startswith(("httpx", "requests", "aiohttp", "app.data_sources",
                                    "app.clients", "app.providers")), name


# ==========================================================================
# ONE canonical record per game and checkpoint (dispositions 2 and 9)
# ==========================================================================

def test_the_checkpoint_is_a_fixed_t_minus_15_and_the_decision_clock_stays_separate(world: World):
    """Disposition 7."""

    entry = ready_game(world, "A")
    run(world)
    record = only_record(world, entry)
    assert record["checkpointAtUtc"] == "2026-09-23T22:45:00Z"
    assert record["scheduledStartUtc"] == "2026-09-23T23:00:00Z"
    assert record["checkpointBasis"] == "SCHEDULED_START_MINUS_15_MINUTES"
    # The decision was recorded two minutes before the checkpoint; it is carried,
    # separately, and it is NOT the checkpoint.
    assert record["decision"]["recommendedAtUtc"] == "2026-09-23T22:43:00Z"
    assert record["decision"]["recommendedAtUtc"] != record["checkpointAtUtc"]
    assert capture.checkpoint_for(KICKOFF) == CHECKPOINT


def test_a_second_run_writes_no_second_record_and_mutates_nothing(world: World):
    entry = ready_game(world, "A")
    first = run(world)
    before = [(path.name, path.read_bytes()) for path in records_on_disk(world, str(entry["game"].id))]
    second = run(world)
    after = [(path.name, path.read_bytes()) for path in records_on_disk(world, str(entry["game"].id))]
    assert before == after
    assert first["created"] == 1
    assert second["created"] == 0


def test_an_exclusion_is_the_same_canonical_record_not_a_second_file(world: World):
    """Disposition 2: never an observation AND a competing exclusion."""

    entry = world.add_game("A")  # priced, but no decision at all
    run(world)
    paths = records_on_disk(world, str(entry["game"].id))
    assert len(paths) == 1
    record = json.loads(paths[0].read_bytes())
    assert record["contract"] == capture.CONTRACT
    assert record["admission"]["state"] == capture.ADMISSION_EXCLUDED
    assert "NO_FINAL_PREGAME_DECISION_AT_CHECKPOINT" in record["admission"]["reasonCodes"]
    # Same identity and clock envelope as any other record.
    assert record["checkpointAtUtc"] == "2026-09-23T22:45:00Z"
    assert record["observationId"] == capture.observation_id("NFL", str(entry["game"].id),
                                                             CHECKPOINT)


def test_the_observation_id_is_the_canonical_game_and_checkpoint_key(world: World):
    entry = ready_game(world, "A")
    run(world)
    record = only_record(world, entry)
    assert record["observationId"].startswith("obs-")
    assert record["observationId"] == capture.observation_id(
        "NFL", str(entry["game"].id), CHECKPOINT)


def test_a_second_canonical_record_for_one_game_and_checkpoint_is_never_created(world: World):
    entry = ready_game(world, "A")
    run(world)
    ledger = capture.AttemptLedger(world.dir, name=capture.LEDGER_NAME)
    ledger_ids = capture._admitted_observation_ids(ledger)
    record = only_record(world, entry)
    assert record["observationId"] in ledger_ids

    mutated = {**record, "status": capture.STATUS_SYNTHETIC}  # different bytes, same id
    result = capture.write_observation(world.dir, mutated, ledger=ledger, now=CHECKPOINT,
                                       admitted=set(ledger_ids))
    assert result["skipped"] is True
    assert result["created"] is False
    # Still exactly one canonical record, byte-identical to the first.
    paths = records_on_disk(world, str(entry["game"].id))
    assert len(paths) == 1
    assert json.loads(paths[0].read_bytes()) == record
    # And the skip is recorded rather than silent.
    assert ledger.entries()[-1]["kind"] == \
        "OBSERVATION_ALREADY_ADMITTED_LATER_EVIDENCE_NOT_WRITTEN"


def test_the_canonical_bytes_carry_no_export_clock(world: World):
    entry = ready_game(world, "A")
    run(world)
    path = records_on_disk(world, str(entry["game"].id))[0]
    raw = path.read_text(encoding="utf-8")
    assert path.stem == __import__("hashlib").sha256(path.read_bytes()).hexdigest()
    for stamp in ("recordedAtUtc", "exportedAtUtc", "exportClock", "capturedAtUtc"):
        assert stamp not in raw


def test_the_ledger_is_append_only_and_hash_chained(world: World):
    ready_game(world, "A")
    ready_game(world, "B")
    run(world)
    ledger = capture.AttemptLedger(world.dir, name=capture.LEDGER_NAME)
    entries = ledger.entries()
    assert [row["seq"] for row in entries] == list(range(1, len(entries) + 1))
    assert entries[0]["prevLineSha256"] is None
    assert all(row["prevLineSha256"] for row in entries[1:])
    assert {row["contractSha256"] for row in entries
            if row["kind"].startswith("OBSERVATION")} == {capture.CONTRACT_SHA256}


def test_an_interrupted_run_recovers_idempotently(world: World):
    entry = ready_game(world, "A")
    run(world)
    # Simulate an interruption: the ledger survives, the run repeats.
    again = run(world)
    assert again["created"] == 0
    assert len(records_on_disk(world, str(entry["game"].id))) == 1


# ==========================================================================
# Disposition 3: positionId is JSON null
# ==========================================================================

def test_a_game_without_a_position_carries_a_json_null_position_id(world: World):
    entry = ready_game(world, "A", execution="NOT_EXECUTED", recommendation="DECLINED")
    run(world)
    record = only_record(world, entry)
    assert record["decision"]["positionId"] is None
    assert record["decision"]["funded"] is False
    assert record["decision"]["qualificationOutcome"] == "QUALIFIED"
    assert record["decision"]["executionOutcome"] == "NOT_EXECUTED"


def test_a_funded_game_carries_the_position_id_as_a_string(world: World):
    entry = ready_game(world, "A")
    position = world.fund(entry, entry["decision"])
    run(world)
    record = only_record(world, entry)
    assert record["decision"]["positionId"] == str(position.id)
    assert record["decision"]["funded"] is True


def test_the_not_applicable_sentinel_never_appears_anywhere(world: World):
    ready_game(world, "A", execution="NOT_EXECUTED")
    world.add_game("B")
    run(world)
    for game_dir in (world.dir / capture.RECORDS_DIR).iterdir():
        for path in game_dir.glob("*.json"):
            record = json.loads(path.read_bytes())
            capture.assert_no_not_applicable_sentinel(record)
            assert "NOT_APPLICABLE" not in path.read_text(encoding="utf-8")


def test_declined_and_unfunded_games_remain_in_the_population(world: World):
    declined = ready_game(world, "D", qualification="NOT_QUALIFIED", execution="NOT_EXECUTED",
                          recommendation="NO_BET")
    unfunded = ready_game(world, "U", qualification="QUALIFIED", execution="NOT_EXECUTED",
                          recommendation="DECLINED")
    run(world)
    for entry in (declined, unfunded):
        record = only_record(world, entry)
        assert record["decision"]["positionId"] is None
        assert record["decision"] is not None
        assert "declinedUnfundedNote" in record["decision"]


# ==========================================================================
# Probability channels (dispositions 5, 6 and the no-substitution rule)
# ==========================================================================

def test_mlb_publishes_home_away_and_a_zero_tie(mlb: World, bound_conventions):
    entry = mlb.add_game("M")
    forecast = mlb.forecast(entry, p_home=0.55)
    mlb.decide(entry, forecast=forecast, supplement=None)
    run(mlb)
    record = only_record(mlb, entry)
    channels = record["forecast"]
    assert channels["homeWinProbabilityRaw"] == pytest.approx(0.55)
    assert channels["awayWinProbabilityRaw"] == pytest.approx(0.45)
    assert channels["tieProbabilityRaw"] == 0.0
    assert channels["tieProbabilityBasis"] == "MLB_WHOLE_GAME_MONEYLINE_HAS_NO_TIE_STATED_ZERO"
    assert channels["decisiveConditionedHomeProbability"] is None


def test_nfl_publishes_the_three_way_vector_and_a_separate_decisive_channel(world: World):
    entry = world.add_game("A")
    forecast = world.forecast(entry, p_home=0.45, p_away=0.52)
    world.decide(entry, forecast=forecast)
    run(world)
    channels = only_record(world, entry)["forecast"]
    assert channels["homeWinProbabilityRaw"] == pytest.approx(0.45)
    assert channels["awayWinProbabilityRaw"] == pytest.approx(0.52)
    assert channels["tieProbabilityRaw"] == pytest.approx(0.03)
    # Separately NAMED, and equal to pHome/(pHome+pAway) -- never folded in.
    assert channels["decisiveConditionedHomeProbability"] == pytest.approx(0.45 / 0.97)
    assert channels["decisiveConditionedBasis"] == \
        "FROZEN_PROTOCOL_ARM_A_P_HOME_OVER_P_HOME_PLUS_P_AWAY"


def test_an_mlb_pair_that_does_not_sum_to_one_is_excluded_not_normalised(mlb: World):
    entry = mlb.add_game("M")
    forecast = mlb.forecast(entry, p_home=0.55, p_away=0.50)
    mlb.decide(entry, forecast=forecast, supplement=None)
    run(mlb)
    record = only_record(mlb, entry)
    assert record["admission"]["state"] == capture.ADMISSION_EXCLUDED
    assert "PUBLISHED_PROBABILITIES_DO_NOT_SUM_TO_ONE" in record["admission"]["reasonCodes"]


def test_selected_side_orientation_is_additional_only(world: World):
    entry = world.add_game("A")
    forecast = world.forecast(entry, p_home=0.45, p_away=0.52)
    world.decide(entry, forecast=forecast, selection="AWAY",
                 evidence={"raw_model_probability": 0.52, "adjusted_probability": 0.49})
    run(world)
    channels = only_record(world, entry)["forecast"]
    assert channels["homeWinProbabilityRaw"] == pytest.approx(0.45)  # still home-oriented
    extra = channels["selectedSideAdditional"]
    assert extra["selectedSide"] == "AWAY"
    assert extra["adjustedProbability"] == pytest.approx(0.49)


def test_an_away_selected_adjusted_channel_is_null_with_a_basis_never_a_complement(world: World):
    """Raw, adjusted and controlling WITHOUT substitution."""

    entry = world.add_game("A")
    forecast = world.forecast(entry, p_home=0.45, p_away=0.52)
    world.decide(entry, forecast=forecast, selection="AWAY", model_probability=0.52,
                 evidence={"adjusted_probability": 0.49})
    run(world)
    channels = only_record(world, entry)["forecast"]
    assert channels["homeWinProbabilityAdjusted"] is None
    assert "AWAY" in channels["homeWinProbabilityAdjustedBasis"]
    assert "complement" in channels["homeWinProbabilityAdjustedBasis"]
    assert channels["homeWinProbabilityControlling"] is None
    # And nothing was borrowed from the raw channel.
    assert channels["homeWinProbabilityRaw"] == pytest.approx(0.45)


def test_a_home_selected_adjusted_channel_is_carried_as_home_oriented(world: World):
    entry = world.add_game("A")
    forecast = world.forecast(entry, p_home=0.55, p_away=0.42)
    world.decide(entry, forecast=forecast, selection="HOME", model_probability=0.57,
                 evidence={"adjusted_probability": 0.53})
    run(world)
    channels = only_record(world, entry)["forecast"]
    assert channels["homeWinProbabilityAdjusted"] == pytest.approx(0.53)
    assert channels["homeWinProbabilityControlling"] == pytest.approx(0.57)


def test_a_channel_that_retains_nothing_is_null_with_a_basis(world: World):
    entry = world.add_game("A")
    forecast = world.forecast(entry)
    world.decide(entry, forecast=forecast, selection="HOME", model_probability=None,
                 evidence={"market_probability": 0.5})
    run(world)
    channels = only_record(world, entry)["forecast"]
    assert channels["homeWinProbabilityAdjusted"] is None
    assert channels["homeWinProbabilityAdjustedBasis"].startswith("ADJUSTED_NOT_RETAINED")
    assert channels["homeWinProbabilityControllingBasis"].startswith("CONTROLLING_NOT_RETAINED")


# ==========================================================================
# Disposition 8: ambiguity is an exclusion, never resolved by recency
# ==========================================================================

def test_two_probability_bearing_channels_are_excluded_not_resolved_by_recency(world: World):
    entry = world.add_game("A")
    forecast = world.forecast(entry)
    other = world._experiment("SECOND", version=2)
    world.decide(entry, forecast=forecast, at=CHECKPOINT - timedelta(minutes=5))
    world.decide(entry, forecast=forecast, at=CHECKPOINT - timedelta(minutes=1),
                 experiment=other)
    run(world)
    record = only_record(world, entry)
    assert record["admission"]["state"] == capture.ADMISSION_EXCLUDED
    assert "AMBIGUOUS_PROBABILITY_BEARING_DECISION_CHANNELS" in record["admission"]["reasonCodes"]
    assert record["decision"] is None  # no channel was chosen at all


def test_the_probability_bearing_channel_is_chosen_not_the_latest_row(world: World):
    """A V1-strict NO_BET row with no probabilities must not win on recency."""

    entry = world.add_game("A")
    forecast = world.forecast(entry)
    strict = world._experiment("V1_STRICT", version=3)
    bearing = world.decide(entry, forecast=forecast, at=CHECKPOINT - timedelta(minutes=5),
                           evidence={"raw_model_probability": 0.52})
    world.decide(entry, forecast=forecast, at=CHECKPOINT - timedelta(minutes=1),
                 experiment=strict, model_probability=None, evidence={},
                 recommendation="NO_BET")
    run(world)
    record = only_record(world, entry)
    assert record["decision"]["decisionId"] == str(bearing.id)


# ==========================================================================
# Deterministic, model-independent book selection
# ==========================================================================

def test_the_selected_book_is_the_minimum_older_leg_age_then_key(world: World):
    entry = world.add_game("A")  # sgo:alpha already priced at QUOTE_AT (300s old)
    world.price(entry, "sgo:bravo", home=-150, away=130,
                observed_at=CHECKPOINT - timedelta(minutes=2))
    world.price(entry, "sgo:charlie", home=-148, away=128,
                observed_at=CHECKPOINT - timedelta(minutes=8))
    forecast = world.forecast(entry)
    world.decide(entry, forecast=forecast, sportsbook="sgo:charlie")
    run(world)
    market = only_record(world, entry)["market"]
    keys = [row["sportsbookKey"] for row in market["eligibleBookCandidates"]]
    assert keys == ["sgo:bravo", "sgo:alpha", "sgo:charlie"]   # 120s, 300s, 480s
    assert market["sportsbookKey"] == "sgo:bravo"
    assert market["olderLegAgeSecondsAtCheckpoint"] == 120.0


def test_a_tie_on_age_is_broken_by_sportsbook_key_ascending(world: World):
    entry = world.add_game("A")
    world.price(entry, "sgo:bravo", home=-150, away=130, observed_at=QUOTE_AT)
    world.price(entry, "sgo:charlie", home=-148, away=128, observed_at=QUOTE_AT)
    forecast = world.forecast(entry)
    world.decide(entry, forecast=forecast)
    run(world)
    market = only_record(world, entry)["market"]
    assert [row["sportsbookKey"] for row in market["eligibleBookCandidates"]] == \
        ["sgo:alpha", "sgo:bravo", "sgo:charlie"]
    assert market["sportsbookKey"] == "sgo:alpha"


def test_the_selection_does_not_move_when_the_decision_names_another_book(world: World):
    """Model-independence, pinned: the decision's own book is never consulted."""

    entry = world.add_game("A")
    world.price(entry, "sgo:bravo", home=-150, away=130,
                observed_at=CHECKPOINT - timedelta(minutes=2))
    forecast = world.forecast(entry)
    world.decide(entry, forecast=forecast, sportsbook="sgo:alpha")
    selected_when_alpha_named = capture.eligible_book_candidates(
        world.session,
        capture.universe(world.session, window_start=WINDOW[0], window_end=WINDOW[1])[0],
        CHECKPOINT)[0].sportsbook_key
    entry["decision"] = None
    world.session.execute(text(
        "update shadow_experiment_decisions set sportsbook = 'sgo:bravo'"))
    selected_when_bravo_named = capture.eligible_book_candidates(
        world.session,
        capture.universe(world.session, window_start=WINDOW[0], window_end=WINDOW[1])[0],
        CHECKPOINT)[0].sportsbook_key
    assert selected_when_alpha_named == selected_when_bravo_named == "sgo:bravo"


def test_the_complete_candidate_list_is_emitted_with_reasons(world: World):
    entry = world.add_game("A")
    # Stale on one leg -> ineligible, but still published with its reason.
    world.price(entry, "sgo:bravo", home=-150, away=130, observed_at=QUOTE_AT,
                home_observed_at=CHECKPOINT - timedelta(minutes=40))
    forecast = world.forecast(entry)
    world.decide(entry, forecast=forecast)
    run(world)
    market = only_record(world, entry)["market"]
    assert market["booksExamined"] == 2
    ineligible = {row["sportsbookKey"]: row["reasonCodes"]
                  for row in market["ineligibleBookCandidates"]}
    assert ineligible == {"sgo:bravo": ["BOOK_LEG_QUOTE_AGE_OVER_FIFTEEN_MINUTES"]}
    # The published legs carry enough to re-derive the ordering without the DB.
    candidate = market["eligibleBookCandidates"][0]
    assert candidate["homeLeg"]["ageSecondsAtCheckpoint"] == 300.0
    assert candidate["orderingKey"] == [300.0, "sgo:alpha"]


def test_a_leg_observed_after_the_checkpoint_makes_its_book_ineligible(world: World):
    entry = world.add_game("A", priced=True)
    entry["snapshots"].clear()
    world.price(entry, "sgo:bravo", home=-150, away=130,
                observed_at=CHECKPOINT + timedelta(minutes=1),
                receipt_at=CHECKPOINT - timedelta(minutes=1))
    forecast = world.forecast(entry)
    world.decide(entry, forecast=forecast)
    run(world)
    market = only_record(world, entry)["market"]
    reasons = {row["sportsbookKey"]: row["reasonCodes"]
               for row in market["ineligibleBookCandidates"]}
    assert "BOOK_LEG_OBSERVED_AFTER_CHECKPOINT" in reasons["sgo:bravo"]


def test_legs_from_different_fetches_are_not_one_quote(world: World):
    entry = world.add_game("A")
    world.price(entry, "sgo:bravo", home=-150, away=130, observed_at=QUOTE_AT,
                receipt_at=QUOTE_AT, away_receipt_at=QUOTE_AT + timedelta(seconds=30))
    forecast = world.forecast(entry)
    world.decide(entry, forecast=forecast)
    run(world)
    market = only_record(world, entry)["market"]
    reasons = {row["sportsbookKey"]: row["reasonCodes"]
               for row in market["ineligibleBookCandidates"]}
    assert reasons["sgo:bravo"] == ["BOOK_LEGS_FROM_DIFFERENT_FETCH_IDENTITY"]


def test_legs_from_different_providers_are_not_one_quote(world: World):
    entry = world.add_game("A")
    world.price(entry, "sgo:bravo", home=-150, away=130, observed_at=QUOTE_AT,
                source="SPORTSGAMEODDS", away_source="THE_ODDS_API")
    forecast = world.forecast(entry)
    world.decide(entry, forecast=forecast)
    run(world)
    reasons = {row["sportsbookKey"]: row["reasonCodes"]
               for row in only_record(world, entry)["market"]["ineligibleBookCandidates"]}
    assert "BOOK_LEGS_FROM_DIFFERENT_PROVIDER" in reasons["sgo:bravo"]


def test_a_game_with_no_complete_pair_at_the_checkpoint_is_unpriced(world: World):
    entry = world.add_game("A", priced=True)
    entry["snapshots"].clear()
    world.session.execute(text("delete from odds_snapshots"))
    forecast = world.forecast(entry)
    world.decide(entry, forecast=forecast, names_snapshot=False)
    run(world)
    record = only_record(world, entry)
    assert record["admission"]["state"] == capture.ADMISSION_UNPRICED
    assert "NO_COMPLETE_SAME_BOOK_PAIR_AT_CHECKPOINT" in record["admission"]["reasonCodes"]


def test_the_common_response_digest_is_null_with_a_stated_basis(world: World):
    entry = ready_game(world, "A")
    run(world)
    market = only_record(world, entry)["market"]
    assert market["commonResponseSha256"] is None
    assert "payload_checksum is a PER-ROW checksum" in market["commonResponseBasis"]
    identity = market["exactFetchIdentity"]
    assert identity["provider"] == "SPORTSGAMEODDS"
    assert identity["consumerReceiptAtUtc"] == "2026-09-23T22:40:00Z"


def test_the_no_vig_arithmetic_is_shown_and_uses_both_selected_legs(world: World):
    entry = ready_game(world, "A")
    run(world)
    vig = only_record(world, entry)["market"]["noVig"]
    assert vig["homeDecimalOdds"] == pytest.approx(1 + 100 / 142)
    assert vig["awayDecimalOdds"] == pytest.approx(2.24)
    assert vig["homeNoVigProbability"] + vig["awayNoVigProbability"] == pytest.approx(1.0)
    assert vig["overround"] > 0


# ==========================================================================
# Conventions: missing semantics yield UNPRICED
# ==========================================================================

def test_the_production_registry_binds_only_what_a_retained_document_states():
    """Exactly one binding, and it carries its own citation.

    Nothing is asserted on a book's behalf. FanDuel's published American
    Football rules state overtime, ties, postponement and suspension; every
    other book the feed relays is either a legacy brand whose operator the feed
    does not document, offshore, or had no retrievable rule text -- so it stays
    UNPRICED. MLB has no retained extra-innings or listed-pitcher rule at all.
    """

    assert set(capture.BOUND_CONVENTIONS) == {("NFL", "sgo:fanduel")}
    entry = capture.BOUND_CONVENTIONS[("NFL", "sgo:fanduel")]
    for name in capture.REQUIRED_CONVENTIONS["NFL"]:
        assert entry[name], name
        assert entry["verbatim"][name], f"{name} has no verbatim source sentence"
    document = entry["document"]
    assert document["url"].startswith("https://www.fanduel.com/")
    assert len(document["sha256"]) == 64
    assert document["retrievedAtUtc"].endswith("Z")
    # A single state's page, and the feed never says which state a quote is from.
    assert document["quoteJurisdiction"] == "NOT_STATED_BY_PROVIDER"
    for unbound in ("sgo:espnbet", "sgo:williamhill", "sgo:pointsbet", "sgo:unibet",
                    "sgo:bovada", "sgo:draftkings", "sgo:betmgm", "sgo:caesars"):
        assert ("NFL", unbound) not in capture.BOUND_CONVENTIONS, unbound
    assert not any(sport == "MLB" for sport, _ in capture.BOUND_CONVENTIONS)
    # Never keyed by the aggregator: that would stamp one book's rules on nine.
    assert not any(book == "SPORTSGAMEODDS" for _, book in capture.BOUND_CONVENTIONS)


def test_conventions_follow_the_selected_book_not_the_feed(monkeypatch):
    """A bound book on the same feed does not lend its rules to an unbound one."""

    monkeypatch.setattr(capture, "BOUND_CONVENTIONS",
                        {("NFL", "sgo:bravo"): dict(_TEST_NFL)})
    unbound = capture.conventions_for("NFL", "sgo:alpha", "SPORTSGAMEODDS")
    bound = capture.conventions_for("NFL", "sgo:bravo", "SPORTSGAMEODDS")

    assert unbound["source"] == "NOT_ESTABLISHED"
    assert sorted(unbound["unestablished"]) == [
        "MARKET_CONVENTION_OVERTIME_NOT_ESTABLISHED",
        "MARKET_CONVENTION_SETTLEMENT_NOT_ESTABLISHED"]
    assert bound["source"] == "BOUND_CONVENTION_REGISTRY"
    assert bound["unestablished"] == []
    assert bound["sportsbookKey"] == "sgo:bravo"


def test_a_bound_convention_carries_its_citation_into_the_record():
    block = capture.conventions_for("NFL", "sgo:fanduel", "SPORTSGAMEODDS")

    assert block["unestablished"] == []
    assert block["overtimeRule"] == "INCLUDED"
    assert block["document"]["sha256"] == capture.BOUND_CONVENTIONS[
        ("NFL", "sgo:fanduel")]["document"]["sha256"]
    assert "voided" in block["verbatim"]["settlementRule"]


def test_a_tick_before_the_checkpoint_writes_nothing(world: World):
    """The universe reaches 36 hours ahead; a future game is not yet observable."""

    entry = ready_game(world, "A")
    summary = run(world, now=CHECKPOINT - timedelta(hours=8))

    assert records_on_disk(world, str(entry["game"].id)) == []
    assert summary["records"] == 0


def test_the_settling_margin_is_honoured_exactly(world: World):
    entry = ready_game(world, "A")

    run(world, now=CHECKPOINT)  # the checkpoint itself: evidence may still be committing
    assert records_on_disk(world, str(entry["game"].id)) == []
    run(world, now=CHECKPOINT + capture.CAPTURE_SETTLING - timedelta(seconds=1))
    assert records_on_disk(world, str(entry["game"].id)) == []
    run(world, now=CHECKPOINT + capture.CAPTURE_SETTLING)
    assert len(records_on_disk(world, str(entry["game"].id))) == 1


def test_an_early_tick_cannot_seal_a_game_before_its_decision_exists(world: World):
    """The failure this guard exists for, reproduced end to end.

    Without it, a morning tick evaluates the evening's game, finds no decision,
    and seals it EXCLUDED; write-once then refuses the real decision as "later
    evidence". The canonical record must be the one built AFTER the checkpoint,
    carrying the decision that was actually made.
    """

    # The morning: the game, its forecast and its prices exist -- but no
    # decision yet, because its checkpoint is hours away.
    entry = world.add_game("A")
    entry["forecast"] = world.forecast(entry)
    run(world, now=CHECKPOINT - timedelta(hours=8))

    # The evening: the checkpoint makes its decision, then the next tick runs.
    entry["decision"] = world.decide(entry, forecast=entry["forecast"])
    run(world)  # after the checkpoint and its margin

    record = only_record(world, entry)
    assert record["decision"] is not None
    assert "NO_PROBABILITY_BEARING_DECISION_CHANNEL" not in record["admission"]["reasonCodes"]
    ledger_text = (world.dir / capture.LEDGER_NAME).read_text(encoding="utf-8")         if (world.dir / capture.LEDGER_NAME).exists() else ""
    assert "OBSERVATION_ALREADY_ADMITTED_LATER_EVIDENCE_NOT_WRITTEN" not in ledger_text


def test_an_unestablished_convention_makes_the_record_unpriced_not_a_guess(world: World):
    entry = ready_game(world, "A")
    run(world)
    record = only_record(world, entry)
    assert record["admission"]["state"] == capture.ADMISSION_UNPRICED
    assert set(record["admission"]["reasonCodes"]) == {
        "MARKET_CONVENTION_SETTLEMENT_NOT_ESTABLISHED",
        "MARKET_CONVENTION_OVERTIME_NOT_ESTABLISHED"}
    conventions = record["market"]["conventions"]
    assert conventions["settlementRule"] == "NOT_ESTABLISHED"
    assert conventions["source"] == "NOT_ESTABLISHED"


def test_the_real_fanduel_citation_survives_the_contract_validator(world: World, monkeypatch):
    """The production binding, not a test stub, inside a priced record.

    Its verbatim rule sentences and document block ride in the record, so they
    must pass the same validator and guards as everything else -- no forbidden
    sentinel, no money field, the frozen key sets.
    """

    real = capture.BOUND_CONVENTIONS[("NFL", "sgo:fanduel")]
    monkeypatch.setattr(capture, "BOUND_CONVENTIONS", {("NFL", "sgo:alpha"): real})
    entry = ready_game(world, "A")
    run(world)
    record = only_record(world, entry)

    assert record["admission"]["state"] == capture.ADMISSION_PRICED
    assert capture.validate_record(record) == []
    capture.assert_no_not_applicable_sentinel(record)
    capture.assert_no_money(record)
    conventions = record["market"]["conventions"]
    assert conventions["document"]["sha256"] == real["document"]["sha256"]
    assert conventions["postponementRule"].startswith("VOID_UNLESS_RESUMED")


def test_a_bound_convention_lets_the_record_be_priced_eligible(world: World, bound_conventions):
    entry = ready_game(world, "A")
    run(world)
    record = only_record(world, entry)
    assert record["admission"]["state"] == capture.ADMISSION_PRICED
    assert record["admission"]["reasonCodes"] == []
    assert record["market"]["conventions"]["overtimeRule"] == "INCLUDED"
    assert record["market"]["conventions"]["tieTreatment"] == \
        "STAKE_REFUNDED_PUSH_OUTSIDE_BINARY_DENOMINATOR"


def test_mlb_requires_the_extra_innings_and_pitcher_action_conventions(mlb: World):
    entry = mlb.add_game("M")
    forecast = mlb.forecast(entry, p_home=0.55)
    mlb.decide(entry, forecast=forecast, supplement=None)
    run(mlb)
    record = only_record(mlb, entry)
    assert set(record["admission"]["reasonCodes"]) == {
        "MARKET_CONVENTION_SETTLEMENT_NOT_ESTABLISHED",
        "MARKET_CONVENTION_EXTRA_INNINGS_NOT_ESTABLISHED",
        "MARKET_CONVENTION_PITCHER_ACTION_NOT_ESTABLISHED"}


# ==========================================================================
# The model clocks
# ==========================================================================

@pytest.mark.parametrize("kwargs,expected", [
    ({"exposed_at": CHECKPOINT + timedelta(minutes=1)}, "FORECAST_EXPOSED_AFTER_CHECKPOINT"),
    ({"snapshot_type": "CURRENT"}, "FORECAST_SNAPSHOT_TYPE_IS_NOT_FINAL_PREGAME"),
])
def test_a_late_or_wrong_forecast_is_excluded(world: World, kwargs, expected):
    entry = world.add_game("A")
    forecast = world.forecast(entry, **kwargs)
    world.decide(entry, forecast=forecast)
    run(world)
    record = only_record(world, entry)
    assert record["admission"]["state"] == capture.ADMISSION_EXCLUDED
    assert expected in record["admission"]["reasonCodes"]


def test_an_import_after_the_checkpoint_is_excluded(world: World):
    entry = world.add_game("A")
    forecast = world.forecast(entry)
    world.decide(entry, forecast=forecast, imported_at=CHECKPOINT + timedelta(seconds=1))
    run(world)
    record = only_record(world, entry)
    assert "FORECAST_IMPORTED_AFTER_CHECKPOINT" in record["admission"]["reasonCodes"]


def test_a_missing_import_clock_is_excluded_not_assumed(world: World):
    entry = world.add_game("A")
    forecast = world.forecast(entry)
    world.decide(entry, forecast=forecast, no_import=True)
    run(world)
    assert "FORECAST_IMPORT_CLOCK_MISSING" in only_record(world, entry)["admission"]["reasonCodes"]


def test_nfl_without_its_eligibility_supplement_is_excluded(world: World):
    entry = world.add_game("A")
    forecast = world.forecast(entry)
    world.decide(entry, forecast=forecast, supplement=None)
    run(world)
    record = only_record(world, entry)
    assert "NFL_ELIGIBILITY_SUPPLEMENT_MISSING" in record["admission"]["reasonCodes"]


def test_pre_freeze_evidence_is_excluded_and_never_enrolled(world: World):
    early = datetime(2026, 9, 20, 23, 0, tzinfo=UTC)
    entry = world.add_game("OLD", starts_at=early)
    forecast = world.forecast(entry, exposed_at=early - timedelta(hours=1))
    world.decide(entry, forecast=forecast, at=early - timedelta(minutes=20),
                 imported_at=early - timedelta(minutes=40))
    capture.run_prospective_capture(
        world.session, world.settings, now=CHECKPOINT,
        window_start=early - timedelta(hours=6), window_end=WINDOW[1])
    record = only_record(world, entry)
    assert record["admission"]["state"] == capture.ADMISSION_EXCLUDED
    assert "PRE_FREEZE_EVIDENCE_NEVER_ENROLLED" in record["admission"]["reasonCodes"]
    assert record["evidenceWindow"]["checkpointIsAfterFreeze"] is False
    assert record["evidenceWindow"]["freezeInstantUtc"] == "2026-09-21T18:32:03.7468106Z"


# ==========================================================================
# Outcomes (disposition 4) and the append-only revision lane
# ==========================================================================

def test_a_null_completion_time_keeps_the_one_accepted_basis(world: World):
    entry = ready_game(world, "A")
    world.nfl_final(entry)
    run(world)
    outcome = only_record(world, entry)["outcome"]
    assert outcome["genuineCompletionAtUtc"] is None
    assert outcome["genuineCompletionBasis"] == \
        "SOURCE_CONFIRMED_TERMINAL_COMPLETION_TIME_UNAVAILABLE"
    # The retrieval clock exists and was NOT promoted into the completion field.
    assert outcome["sourceCaptureAtUtc"] == "2026-09-24T03:00:00Z"
    assert outcome["sourceLastUpdatedRaw"] == "2026-09-23 21:44:03 US/Eastern"


def test_nfl_outcomes_carry_overtime_week_and_game_type(world: World):
    entry = ready_game(world, "A")
    world.nfl_final(entry, overtime=True)
    run(world)
    outcome = only_record(world, entry)["outcome"]
    assert outcome["overtime"] is True
    assert outcome["week"] == 3
    assert outcome["gameType"] == "REG"
    assert outcome["season"] == 2026
    assert "REFUND" in outcome["tieDisposition"]


def test_an_official_nfl_tie_is_flagged_for_refund_treatment(world: World):
    entry = ready_game(world, "A")
    world.nfl_final(entry, home_score=20, away_score=20)
    run(world)
    outcome = only_record(world, entry)["outcome"]
    assert outcome["isTie"] is True
    assert "outside the binary proper-score denominator" in outcome["tieDisposition"]


def test_a_source_supplied_completion_time_is_carried_as_one(mlb: World):
    entry = mlb.add_game("M")
    forecast = mlb.forecast(entry, p_home=0.55)
    mlb.decide(entry, forecast=forecast, supplement=None)
    mlb.state_final(entry, final_at=KICKOFF + timedelta(hours=2, minutes=52))
    run(mlb)
    outcome = only_record(mlb, entry)["outcome"]
    assert outcome["genuineCompletionAtUtc"] == "2026-09-24T01:52:00Z"
    assert outcome["genuineCompletionBasis"] == "SOURCE_SUPPLIED_COMPLETION_TIME"


def test_a_game_with_no_supported_terminal_revision_has_a_null_outcome(world: World):
    entry = ready_game(world, "A")
    run(world)
    assert only_record(world, entry)["outcome"] is None


def test_a_later_correction_appends_a_revision_and_never_rewrites_the_observation(world: World):
    entry = ready_game(world, "A")
    world.nfl_final(entry, home_score=17, away_score=24)
    run(world)
    observation_before = records_on_disk(world, str(entry["game"].id))[0].read_bytes()
    revisions_before = sorted(
        (world.dir / capture.OUTCOMES_DIR / str(entry["game"].id)).glob("*.json"))
    assert len(revisions_before) == 1

    world.nfl_final(entry, home_score=17, away_score=27,
                    retrieved_at=KICKOFF + timedelta(hours=6))
    summary = run(world)
    # The canonical pregame observation is byte-identical and still the only one:
    # the correction did not rewrite it and did not create a competitor.
    assert summary["written"][0]["skipped"] is True
    paths = records_on_disk(world, str(entry["game"].id))
    assert len(paths) == 1
    assert paths[0].read_bytes() == observation_before
    revisions_after = sorted(
        (world.dir / capture.OUTCOMES_DIR / str(entry["game"].id)).glob("*.json"))
    assert len(revisions_after) == 2
    ordinals = [json.loads(path.read_bytes())["revisionOrdinal"] for path in revisions_after]
    assert sorted(ordinals) == [1, 2]
    for path in revisions_after:
        body = json.loads(path.read_bytes())
        assert body["observationId"] == json.loads(observation_before)["observationId"]


def test_a_non_terminal_observation_is_never_read_as_a_final(world: World):
    entry = ready_game(world, "A")
    world.nfl_final(entry, status="IN_PROGRESS")
    run(world)
    assert only_record(world, entry)["outcome"] is None


# ==========================================================================
# THE READ-ONLY GUARANTEE -- all five properties
# ==========================================================================

class _FakeDialect:
    name = "postgresql"


class _FakeBind:
    dialect = _FakeDialect()


class _FakePgSession:
    """Just enough Session to prove the read-only statement is issued and checked."""

    def __init__(self, answer: str = "on") -> None:
        self.statements: list[str] = []
        self.answer = answer
        self.rolled_back = 0
        self.new = self.dirty = self.deleted = ()
        self.info: dict = {}

    def get_bind(self):
        return _FakeBind()

    def no_autoflush(self):  # pragma: no cover - replaced below
        raise NotImplementedError

    def execute(self, statement):
        self.statements.append(str(statement))

        class _Result:
            def __init__(self, value): self.value = value
            def scalar(self): return self.value

        return _Result(self.answer if "SHOW" in str(statement) else None)

    def rollback(self):
        self.rolled_back += 1


def test_property_a_postgres_gets_an_explicit_and_confirmed_read_only_transaction():
    import contextlib

    from app.research_exports import v18_market_pilot_outcomes as out

    fake = _FakePgSession()
    fake.no_autoflush = contextlib.nullcontext()
    with out.read_only(fake):
        pass
    assert "SET TRANSACTION READ ONLY" in fake.statements
    assert any("SHOW transaction_read_only" in row for row in fake.statements)
    assert fake.rolled_back == 1


def test_property_a_a_server_that_does_not_confirm_read_only_is_refused():
    import contextlib

    from app.research_exports import v18_market_pilot_outcomes as out

    fake = _FakePgSession(answer="off")
    fake.no_autoflush = contextlib.nullcontext()
    with pytest.raises(out.OutcomeExportRefused):
        with out.read_only(fake):
            pass
    assert fake.rolled_back == 1


def test_property_b_the_module_contains_no_mutation_path():
    """Proved by AST walk, and the module refuses to import if it ever fails."""

    proof = capture.assert_module_has_no_mutation_path()
    assert proof["checked"] is True
    tree = ast.parse(capture.module_source())
    called = {node.func.attr for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert not called & capture.FORBIDDEN_CALL_ATTRIBUTES


@pytest.mark.parametrize("snippet,expected", [
    ("def f(s):\n    s.add(1)\n", "call .add()"),
    ("def f(s):\n    s.flush()\n", "call .flush()"),
    ("def f(s):\n    s.commit()\n", "call .commit()"),
    ("from sqlalchemy import update\n", "import sqlalchemy.update"),
    ("def f(s):\n    return s.execute(x('delete from games'))\n", "SQL token"),
])
def test_property_b_the_ast_guard_actually_catches_a_mutation_path(snippet, expected):
    with pytest.raises(capture.CaptureRefused) as error:
        capture.assert_module_has_no_mutation_path(snippet)
    assert error.value.code == "EXPORTER_CONTAINS_A_MUTATION_PATH"
    assert expected in error.value.detail


def test_property_c_a_file_write_inside_the_read_transaction_is_refused(world: World):
    ready_game(world, "A")
    ledger = capture.AttemptLedger(world.dir, name=capture.LEDGER_NAME)
    with capture.read_only_session(world.session):
        built = capture.build_all(world.session, window_start=WINDOW[0], window_end=WINDOW[1])
        assert world.session.info[capture.READ_TX_MARKER] is True
        with pytest.raises(capture.CaptureRefused) as error:
            capture.write_observation(world.dir, built[0][1], ledger=ledger, now=CHECKPOINT,
                                      session=world.session)
        assert error.value.code == "FILE_WRITE_ATTEMPTED_INSIDE_READ_TRANSACTION"
    # Outside it, the same write is fine -- which is where the real run does it.
    assert capture.READ_TX_MARKER not in world.session.info
    capture.write_observation(world.dir, built[0][1], ledger=ledger, now=CHECKPOINT,
                              session=world.session)


def test_property_d_a_mutation_through_the_exporters_session_fails(world: World):
    """On SQLite too, where SET TRANSACTION READ ONLY is a no-op."""

    ready_game(world, "A")
    with pytest.raises(capture.CaptureRefused) as error:
        with capture.read_only_session(world.session) as reader:
            reader.add(Team(league_id=world.league.id, name="X", abbreviation="XX"))
            reader.flush()
    assert "MUTATION" in error.value.code
    world.session.rollback()
    # And the listeners are gone afterwards, so the real decision path still works.
    world.session.add(Team(league_id=world.league.id, name="Y", abbreviation="YY"))
    world.session.flush()


def test_property_d_an_orm_delete_through_the_session_fails(world: World):
    from sqlalchemy import delete as sa_delete

    ready_game(world, "A")
    with pytest.raises(capture.CaptureRefused):
        with capture.read_only_session(world.session) as reader:
            reader.execute(sa_delete(OddsSnapshot))
    world.session.rollback()


def test_property_e_the_guarantee_is_not_a_row_count_diff(world: World):
    """A legitimate concurrent production write must not be blamed on the exporter."""

    source = capture.module_source()
    run_body = source.split("def run_prospective_capture")[1].split("\ndef ")[0]
    assert "sqlite_row_count_probe" not in run_body
    assert "func.count" not in run_body

    entry = ready_game(world, "A")
    before = capture.sqlite_row_count_probe(world.session)
    summary = run(world)
    # A legitimate production write lands between ticks. The export is unaffected
    # and makes no claim about it.
    world.decide(entry, forecast=entry["forecast"],
                 at=CHECKPOINT - timedelta(minutes=1),
                 experiment=world._experiment("CONCURRENT", version=9))
    world.session.commit()
    after = capture.sqlite_row_count_probe(world.session)
    assert after != before                       # counts DID change
    assert summary["enabled"] is True            # and nothing accused the exporter
    assert "rowCount" not in json.dumps(summary)
    assert summary["readOnlyProof"]["E_notARowCountDiff"].startswith(
        "No production path compares row counts")


def test_the_row_count_probe_is_documented_as_a_test_aid_only():
    doc = capture.sqlite_row_count_probe.__doc__ or ""
    assert "TEST AID ONLY" in doc
    assert "NOT the production write barrier" in doc


def test_the_exporter_writes_nothing_to_decisions_or_positions(world: World):
    """Belt and braces on top of properties A-D, for this fixture only."""

    ready_game(world, "A")
    world.session.commit()
    world.session.expire_all()

    def snapshot_values() -> list[tuple]:
        return [(row.id, row.american_odds, row.decimal_odds, str(row.observed_at),
                 str(row.available_as_of), row.payload_checksum)
                for row in world.session.scalars(select(OddsSnapshot).order_by(OddsSnapshot.id))]

    decisions = world.session.scalar(select(func.count(ShadowExperimentDecision.id)))
    positions = world.session.scalar(select(func.count(OfficialAspPosition.id)))
    before = snapshot_values()
    run(world)
    world.session.expire_all()
    assert world.session.scalar(select(func.count(ShadowExperimentDecision.id))) == decisions
    assert world.session.scalar(select(func.count(OfficialAspPosition.id))) == positions
    # VALUES, not just counts: an UPDATE would be invisible to a count.
    assert snapshot_values() == before


# ==========================================================================
# The contract validator
# ==========================================================================

def test_every_built_record_passes_the_contract_validator(world: World, bound_conventions):
    ready_game(world, "A")
    ready_game(world, "B", execution="NOT_EXECUTED")
    world.add_game("C")  # excluded: no decision
    run(world)
    checked = 0
    for game_dir in (world.dir / capture.RECORDS_DIR).iterdir():
        for path in game_dir.glob("*.json"):
            failures = capture.validate_record(json.loads(path.read_bytes()))
            assert failures == [], (path.name, failures)
            checked += 1
    assert checked == 3


def test_the_validator_rejects_a_record_that_breaks_the_contract(world: World):
    entry = ready_game(world, "A")
    run(world)
    record = only_record(world, entry)
    assert capture.validate_record({**record, "contractSha256": "0" * 64})
    assert capture.validate_record({**record, "contract": "SOMETHING_ELSE"})
    broken = json.loads(json.dumps(record))
    broken["decision"]["positionId"] = "NOT_APPLICABLE"
    assert any("NOT_APPLICABLE" in row for row in capture.validate_record(broken))
    shifted = json.loads(json.dumps(record))
    shifted["checkpointAtUtc"] = "2026-09-23T22:30:00Z"
    assert any("minus 15 minutes" in row for row in capture.validate_record(shifted))


def test_the_validator_refuses_priced_eligible_with_an_unestablished_convention(world: World):
    entry = ready_game(world, "A")
    run(world)
    record = json.loads(json.dumps(only_record(world, entry)))
    record["admission"]["state"] = capture.ADMISSION_PRICED
    record["admission"]["reasonCodes"] = []
    assert any("must yield UNPRICED" in row for row in capture.validate_record(record))


def test_the_validator_refuses_a_candidate_list_that_is_out_of_order(world: World):
    entry = world.add_game("A")
    world.price(entry, "sgo:bravo", home=-150, away=130,
                observed_at=CHECKPOINT - timedelta(minutes=2))
    forecast = world.forecast(entry)
    world.decide(entry, forecast=forecast)
    run(world)
    record = json.loads(json.dumps(only_record(world, entry)))
    record["market"]["eligibleBookCandidates"].reverse()
    assert any("ascending order" in row for row in capture.validate_record(record))


def test_the_reason_codes_are_an_immutable_named_set():
    assert capture.REASON_CODES == (
        capture.EXCLUSION_REASON_CODES | capture.UNPRICED_REASON_CODES
        | capture.BOOK_REASON_CODES)
    assert "PRE_FREEZE_EVIDENCE_NEVER_ENROLLED" in capture.EXCLUSION_REASON_CODES
    assert "NO_COMPLETE_SAME_BOOK_PAIR_AT_CHECKPOINT" in capture.UNPRICED_REASON_CODES
    with pytest.raises(capture.CaptureRefused):
        capture.Observation(game=None, checkpoint=CHECKPOINT).refuse("INVENTED_CODE")


def test_no_blend_score_or_threshold_is_ever_computed(world: World):
    ready_game(world, "A")
    run(world)
    for game_dir in (world.dir / capture.RECORDS_DIR).iterdir():
        for path in game_dir.glob("*.json"):
            capture.assert_no_derived_comparison(json.loads(path.read_bytes()))
            capture.assert_no_money(json.loads(path.read_bytes()))


def test_the_capture_lane_may_not_share_a_directory_with_a_pilot_lane(world: World):
    world.settings.v18_market_pilot_packet_dir = str(world.dir / "packets")
    summary = run(world, directory=str(world.dir))
    assert summary["refused"] == "CAPTURE_DIR_OVERLAPS_AN_EXISTING_EXPORT_LANE"


def test_the_synthetic_contract_example_passes_the_validator():
    """Task 4's artefact, checked from the test suite as well as its own script."""

    path = Path(r"C:\Users\james\aisp-analysis\wagering-review-20260921\contract"
                r"\synthetic-model-market-observation-v1.json")
    if not path.exists():  # pragma: no cover - the artefact lives outside the repo
        pytest.skip("synthetic example not present in this checkout")
    record = json.loads(path.read_bytes())
    assert record["status"] == capture.STATUS_SYNTHETIC
    assert capture.validate_record(record) == []

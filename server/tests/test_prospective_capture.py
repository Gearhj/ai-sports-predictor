"""Prospective research capture: the six hard rules, pinned.

The exporter supplies Model Studio the inputs that were knowable at a
checkpoint and the authentic outcomes, for EVERY eligible priced game. These
tests pin the properties that make that claim true rather than merely intended:

* append-only -- a second run never mutates an existing record;
* the pregame view is FROZEN -- a later forecast or a later quote does not
  change an already-written record;
* DECLINED and UNFUNDED games are captured, not dropped;
* a game without qualifying evidence produces an EXCLUSION record with a
  reason, never a silent omission;
* qualification, funding and position are THREE DISTINCT fields;
* a null completion time is preserved with its basis;
* the exporter writes nothing to shadow_experiment_decisions or
  official_asp_positions;
* it is OFF by default, and it registers no scheduled job.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    ExperimentDecisionPhase, Game, GameStateObservation, GameStatus, GameTeam, League, Market,
    MarketOutcome, MarketType, NflFinalObservation, OddsSnapshot, OfficialAspPosition,
    PositionResult, PositionStatus, Season, ShadowExperiment, ShadowExperimentDecision,
    ShadowForecast, ShadowForecastBundle, Sport, Sportsbook, Team,
)
from app.research_exports import prospective_capture as capture

KICKOFF = datetime(2026, 9, 20, 17, 0, tzinfo=UTC)
CHECKPOINT = KICKOFF - timedelta(minutes=15)
QUOTE_AT = CHECKPOINT - timedelta(minutes=5)
WINDOW = (KICKOFF - timedelta(hours=6), KICKOFF + timedelta(hours=6))


def _experiment(session: Session, name: str, decision_policy: str, *,
                version: int) -> ShadowExperiment:
    row = ShadowExperiment(
        name=name, version=version, started_at=KICKOFF - timedelta(days=7),
        start_date=date(2026, 9, 13), starting_bankroll=1000, sport="NFL",
        recommendation_policy_version=decision_policy,
        staking_policy_version="AISP_QUARTER_KELLY_V1", decision_policy=decision_policy,
        controlling_engine="NFL_M1_XGBOOST", supported_markets=["MONEYLINE"], kelly_fraction=0.25,
        unit_percentage=0.01, max_position_percentage=0.005, max_same_game_percentage=0.01,
        max_daily_percentage=0.02, code_commit="a1b1253", code_identity="test", is_active=True)
    session.add(row)
    session.flush()
    return row


# ---------------------------------------------------------------------------
# A small retained world
# ---------------------------------------------------------------------------
class World:
    """One NFL slate: a funded game, an unfunded-but-qualified game, a declined
    game, and a game with no decision at all."""

    def __init__(self, session: Session, root: Path) -> None:
        self.session = session
        self.dir = root / "capture"
        sport = Sport(code="FOOTBALL", name="Football")
        session.add(sport)
        session.flush()
        self.league = League(sport_id=sport.id, code="NFL", name="NFL")
        session.add(self.league)
        session.flush()
        self.season = Season(league_id=self.league.id, name="2026",
                             starts_at=KICKOFF - timedelta(days=30),
                             ends_at=KICKOFF + timedelta(days=120))
        session.add(self.season)
        self.book = Sportsbook(key="sgo:book", name="Book (research)", region="us", active=True)
        session.add(self.book)
        session.flush()
        self.experiment = _experiment(session, "NFL PAPER", "AISP_NFL_PAPER_POLICY_V1", version=1)
        self.bundle = ShadowForecastBundle(
            bundle_sha256=uuid.uuid4().hex, bundle_id=f"b-{uuid.uuid4().hex[:8]}",
            logical_uri="test://bundle", source_fixture_id="fix-1", slate_date=date(2026, 9, 20),
            league="NFL", sport="FOOTBALL", status="IMPORTED", research_shadow_only=True,
            generated_at=KICKOFF, forecast_count=1, quote_count=0, candidate_count=1,
            imported_at=KICKOFF, contract_version="v1", evidence={})
        session.add(self.bundle)
        session.flush()
        self.games: dict[str, dict] = {}

    # -- building blocks ---------------------------------------------------
    def add_game(self, tag: str, *, priced: bool = True, pair: bool = True,
                 home_observed_at: datetime | None = None) -> dict:
        home = Team(league_id=self.league.id, name=f"{tag} home", abbreviation=f"{tag}H")
        away = Team(league_id=self.league.id, name=f"{tag} away", abbreviation=f"{tag}A")
        self.session.add_all([home, away])
        self.session.flush()
        game = Game(league_id=self.league.id, season_id=self.season.id, starts_at=KICKOFF,
                    status=GameStatus.SCHEDULED)
        self.session.add(game)
        self.session.flush()
        self.session.add_all([
            GameTeam(game_id=game.id, team_id=home.id, is_home=True),
            GameTeam(game_id=game.id, team_id=away.id, is_home=False)])
        entry: dict = {"game": game, "home": home, "away": away}
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
            entry |= {"market": market, "home_outcome": home_outcome, "away_outcome": away_outcome,
                      "away_snapshot": self.quote(away_outcome, 124, QUOTE_AT)}
            if pair:
                entry["home_snapshot"] = self.quote(
                    home_outcome, -142, QUOTE_AT, observed_at=home_observed_at)
        self.session.flush()
        self.games[tag] = entry
        return entry

    def quote(self, outcome: MarketOutcome, american: int, at: datetime, *,
              observed_at: datetime | None = None) -> OddsSnapshot:
        snapshot = OddsSnapshot(
            market_outcome_id=outcome.id, sportsbook_id=self.book.id,
            observed_at=observed_at or at, available_as_of=at, american_odds=american,
            decimal_odds=1 + (american / 100 if american > 0 else 100 / abs(american)),
            source="SPORTSGAMEODDS", source_event_id=f"evt-{uuid.uuid4().hex[:8]}",
            source_mode="LIVE")
        self.session.add(snapshot)
        self.session.flush()
        return snapshot

    def forecast(self, entry: dict, *, available_as_of: datetime, p_home: float = 0.42
                 ) -> ShadowForecast:
        row = ShadowForecast(
            bundle_id=self.bundle.id,
            forecast_id=f"asp-sports-game-forecast:sha256:{uuid.uuid4().hex}",
            engine_id="NFL_M1_XGBOOST", engine_version="1.0",
            model_artifact_sha256=uuid.uuid4().hex, simulator_id="sim", simulator_version="1",
            simulator_seed=1, event_id=f"e-{uuid.uuid4().hex[:8]}",
            source_event_id=f"e-{uuid.uuid4().hex[:8]}", event_date=date(2026, 9, 20),
            season=2026, home_team_id="H", away_team_id="A", is_ready=True, readiness_score=99.0,
            readiness_threshold=50.0, status="READY", full_home_win_probability=p_home,
            full_away_win_probability=1 - p_home, full_expected_home_score=21.0,
            full_expected_away_score=23.0, distributions={}, uncertainty={}, readiness_detail={},
            forecast_schema_version="1", snapshot_type="FINAL_PREGAME", snapshot_version=1,
            identity_evidence={}, lineage_detail={}, canonical_game_id=entry["game"].id,
            available_as_of_utc=available_as_of)
        self.session.add(row)
        self.session.flush()
        return row

    def decide(self, entry: dict, *, qualification: str | None, qualification_reason: str,
               execution: str | None, execution_reason: str, recommendation: str,
               forecast: ShadowForecast | None = None, at: datetime = CHECKPOINT,
               model_probability: float | None = 0.58, evidence: dict | None = None,
               experiment: ShadowExperiment | None = None,
               names_snapshot: bool = True) -> ShadowExperimentDecision:
        row = ShadowExperimentDecision(
            experiment_id=(experiment or self.experiment).id, game_id=entry["game"].id,
            slate_date=date(2026, 9, 20), phase=ExperimentDecisionPhase.FINAL_PREGAME,
            sport="NFL", market="MONEYLINE", period="full_game", selection="AWAY",
            sportsbook="sgo:book", american_odds=124, decimal_odds=2.24,
            market_outcome_id=entry["away_outcome"].id,
            odds_snapshot_id=entry["away_snapshot"].id if names_snapshot else None,
            quote_observed_at=QUOTE_AT,
            shadow_forecast_id=forecast.id if forecast is not None else None,
            recommended_at=at, model_probability=model_probability, expected_value=0.05,
            recommendation=recommendation, recommendation_reason=execution_reason,
            qualification_reasons={}, qualification_outcome=qualification,
            qualification_reason=qualification_reason, execution_outcome=execution,
            execution_reason=execution_reason,
            controlling_forecast_identity=forecast.forecast_id if forecast is not None else None,
            forecast_snapshot_phase="FINAL_PREGAME",
            forecast_published_at=at - timedelta(minutes=40),
            forecast_imported_at=at - timedelta(minutes=30),
            source_mode="LIVE", evidence=evidence or {})
        self.session.add(row)
        self.session.flush()
        return row

    def fund(self, entry: dict, decision: ShadowExperimentDecision) -> OfficialAspPosition:
        position = OfficialAspPosition(
            shadow_experiment_id=self.experiment.id, shadow_decision_id=decision.id,
            shadow_forecast_id=decision.shadow_forecast_id,
            market_outcome_id=entry["away_outcome"].id,
            odds_snapshot_id=entry["away_snapshot"].id, placed_at=CHECKPOINT, stake=5.0,
            decimal_odds=2.24, result=PositionResult.PENDING, status=PositionStatus.OPEN,
            recommendation_policy_version="AISP_NFL_PAPER_POLICY_V1",
            staking_policy_version="AISP_QUARTER_KELLY_V1")
        self.session.add(position)
        self.session.flush()
        return position

    def nfl_final(self, entry: dict, *, home_score: int = 17, away_score: int = 24,
                  status: str = "FINAL") -> NflFinalObservation:
        row = NflFinalObservation(
            game_id=entry["game"].id, source_event_id="2026_02_AA_HH", source="NFLVERSE",
            source_mode="RESEARCH_SHADOW", result_tier=1, status=status, home_score=home_score,
            away_score=away_score, overtime=False,
            source_last_updated="2026-09-20 21:44:03 US/Eastern",
            retrieved_at=KICKOFF + timedelta(hours=4), payload_checksum=uuid.uuid4().hex,
            feed_checksum=uuid.uuid4().hex, raw_row={})
        self.session.add(row)
        self.session.flush()
        return row


@pytest.fixture
def world(session: Session, settings, artifact_root: Path) -> World:
    built = World(session, artifact_root)
    settings.prospective_capture_enabled = True
    settings.prospective_capture_dir = str(built.dir)
    built.settings = settings
    return built


def run(world: World, **kwargs) -> dict:
    return capture.run_prospective_capture(
        world.session, world.settings, now=CHECKPOINT,
        window_start=WINDOW[0], window_end=WINDOW[1], **kwargs)


def records_on_disk(world: World, game_id: str) -> list[Path]:
    directory = world.dir / capture.RECORDS_DIR / game_id
    return sorted(directory.glob("*.json")) if directory.exists() else []


def load(path: Path) -> dict:
    return json.loads(path.read_bytes())


def only_record(world: World, entry: dict) -> dict:
    paths = records_on_disk(world, str(entry["game"].id))
    assert len(paths) == 1, [p.name for p in paths]
    return load(paths[0])


# ==========================================================================
# It is OFF by default, and there is no scheduled job
# ==========================================================================

def test_it_is_disabled_by_default_and_inert_when_off(session: Session, settings, artifact_root):
    """Rule 5. The contract is not frozen; the exporter must do nothing."""
    assert settings.prospective_capture_enabled is False
    directory = artifact_root / "capture"
    summary = capture.run_prospective_capture(
        session, settings, now=CHECKPOINT, directory=directory)
    assert summary == {
        "enabled": False, "records": 0, "contractStatus": capture.CONTRACT_STATUS,
        "note": summary["note"]}
    assert not directory.exists()


def test_it_registers_no_scheduled_job(settings):
    """Rule 5: no new scheduler, and nothing that competes with production."""
    from app.jobs.scheduler import build_scheduler

    # Turning the flag ON must register exactly nothing: there is no job.
    scheduler = build_scheduler(settings.model_copy(update={
        "scheduler_core_jobs_enabled": True, "prospective_capture_enabled": True}))
    ids = {str(job.id) for job in scheduler.get_jobs()}
    names = {getattr(job.func, "__name__", "") for job in scheduler.get_jobs()}
    assert not any("prospective" in name for name in names)
    assert not any("prospective" in job_id for job_id in ids)

    import app.jobs.scheduler as scheduler_module

    source = Path(scheduler_module.__file__).read_text(encoding="utf-8")
    assert "prospective_capture" not in source

    module = Path(capture.__file__).read_text(encoding="utf-8")
    assert "SCHEDULER_ID" not in module
    assert "add_job" not in module


# ==========================================================================
# Append-only
# ==========================================================================

def test_a_second_run_never_mutates_an_existing_record(world: World):
    """Rule 1. Identical inputs -> the same content hash -> the same file, untouched."""
    entry = world.add_game("FUND")
    forecast = world.forecast(entry, available_as_of=CHECKPOINT - timedelta(minutes=30))
    decision = world.decide(entry, qualification="QUALIFIED",
                            qualification_reason="ALL_GATES_PASSED", execution="EXECUTED",
                            execution_reason="POSITION_TAKEN", recommendation="QUALIFIED_BET",
                            forecast=forecast)
    world.fund(entry, decision)
    world.session.commit()

    first = run(world)
    assert first["created"] == 1
    paths = records_on_disk(world, str(entry["game"].id))
    assert len(paths) == 1
    before = paths[0].read_bytes()
    stat_before = paths[0].stat().st_mtime_ns

    second = run(world)
    assert second["created"] == 0
    assert second["written"][0]["recordSha256"] == first["written"][0]["recordSha256"]
    after_paths = records_on_disk(world, str(entry["game"].id))
    assert [p.name for p in after_paths] == [p.name for p in paths]
    assert after_paths[0].read_bytes() == before
    assert after_paths[0].stat().st_mtime_ns == stat_before


def test_a_changed_evaluation_writes_a_new_record_beside_the_old_one(world: World):
    """Rule 1. A later evaluation is an ADDITION, never an edit."""
    entry = world.add_game("LATER")
    forecast = world.forecast(entry, available_as_of=CHECKPOINT - timedelta(minutes=30))
    world.decide(entry, qualification="QUALIFIED", qualification_reason="ALL_GATES_PASSED",
                 execution="EXECUTED", execution_reason="POSITION_TAKEN",
                 recommendation="QUALIFIED_BET", forecast=forecast)
    world.session.commit()

    run(world)
    first = records_on_disk(world, str(entry["game"].id))
    assert len(first) == 1
    original = first[0].read_bytes()

    # The outcome arrives. Same checkpoint, genuinely different content.
    world.nfl_final(entry)
    world.session.commit()
    run(world)

    after = records_on_disk(world, str(entry["game"].id))
    assert len(after) == 2
    assert first[0] in after
    assert first[0].read_bytes() == original           # the earlier record is untouched
    resolved = [load(p) for p in after if p != first[0]][0]
    assert load(first[0])["outcome"]["resolved"] is False
    assert resolved["outcome"]["resolved"] is True


def test_a_record_file_is_never_overwritten_with_different_bytes(world: World):
    """Rule 1, at the write boundary itself."""
    entry = world.add_game("ONCE")
    forecast = world.forecast(entry, available_as_of=CHECKPOINT - timedelta(minutes=30))
    world.decide(entry, qualification="QUALIFIED", qualification_reason="ALL_GATES_PASSED",
                 execution="EXECUTED", execution_reason="POSITION_TAKEN",
                 recommendation="QUALIFIED_BET", forecast=forecast)
    world.session.commit()
    run(world)

    path = records_on_disk(world, str(entry["game"].id))[0]
    with pytest.raises(capture.CaptureRefused) as error:
        capture._write_once_same(path, b'{"tampered":true}')
    assert error.value.code == "RECORD_EXISTS_WITH_DIFFERENT_BYTES"
    assert b"tampered" not in path.read_bytes()


def test_canonical_bytes_carry_no_export_clock(world: World):
    """Rule 6. Same inputs -> same bytes, forever, on any machine."""
    entry = world.add_game("CANON")
    forecast = world.forecast(entry, available_as_of=CHECKPOINT - timedelta(minutes=30))
    world.decide(entry, qualification="QUALIFIED", qualification_reason="ALL_GATES_PASSED",
                 execution="EXECUTED", execution_reason="POSITION_TAKEN",
                 recommendation="QUALIFIED_BET", forecast=forecast)
    world.session.commit()
    run(world)

    data = records_on_disk(world, str(entry["game"].id))[0].read_bytes()
    from app.research_exports import v18_market_pilot as pilot
    assert data == pilot.canonical_bytes(json.loads(data))       # sorted, no whitespace
    text = data.decode("utf-8")
    for forbidden in ("exportedAt", "generatedAt", "writtenAt", "recordedAtUtc", "nowUtc"):
        assert forbidden not in text


# ==========================================================================
# The pregame view is frozen
# ==========================================================================

def test_a_later_forecast_or_quote_does_not_change_an_already_written_record(world: World):
    """Rule 2. The record says what was knowable at the checkpoint. Full stop."""
    entry = world.add_game("FROZEN")
    forecast = world.forecast(entry, available_as_of=CHECKPOINT - timedelta(minutes=30),
                              p_home=0.42)
    world.decide(entry, qualification="QUALIFIED", qualification_reason="ALL_GATES_PASSED",
                 execution="EXECUTED", execution_reason="POSITION_TAKEN",
                 recommendation="QUALIFIED_BET", forecast=forecast)
    world.session.commit()

    run(world)
    path = records_on_disk(world, str(entry["game"].id))[0]
    original = path.read_bytes()
    record = load(path)
    assert record["market"]["selectedLeg"]["americanOdds"] == 124
    assert record["market"]["opposingLeg"]["americanOdds"] == -142

    # A newer forecast AND a newer, much better quote pair land after the lock.
    world.forecast(entry, available_as_of=CHECKPOINT + timedelta(minutes=5), p_home=0.99)
    world.quote(entry["away_outcome"], 410, CHECKPOINT + timedelta(minutes=6))
    world.quote(entry["home_outcome"], -900, CHECKPOINT + timedelta(minutes=6))
    world.session.commit()

    second = run(world)
    assert second["created"] == 0
    after = records_on_disk(world, str(entry["game"].id))
    assert len(after) == 1
    assert after[0].read_bytes() == original


def test_the_opposing_leg_is_the_one_visible_at_the_checkpoint_not_the_latest(world: World):
    """Rule 2, on the one leg the decision does not name."""
    entry = world.add_game("OPP")
    forecast = world.forecast(entry, available_as_of=CHECKPOINT - timedelta(minutes=30))
    world.decide(entry, qualification="QUALIFIED", qualification_reason="ALL_GATES_PASSED",
                 execution="EXECUTED", execution_reason="POSITION_TAKEN",
                 recommendation="QUALIFIED_BET", forecast=forecast)
    # A later home leg exists BEFORE the first export runs.
    world.quote(entry["home_outcome"], -900, CHECKPOINT + timedelta(minutes=1))
    world.session.commit()

    run(world)
    record = only_record(world, entry)
    assert record["market"]["opposingLeg"]["americanOdds"] == -142
    assert record["market"]["opposingLeg"]["oddsSnapshotId"] == str(entry["home_snapshot"].id)


def test_the_de_vig_arithmetic_is_shown_and_uses_both_captured_legs(world: World):
    entry = world.add_game("VIG")
    forecast = world.forecast(entry, available_as_of=CHECKPOINT - timedelta(minutes=30))
    world.decide(entry, qualification="QUALIFIED", qualification_reason="ALL_GATES_PASSED",
                 execution="EXECUTED", execution_reason="POSITION_TAKEN",
                 recommendation="QUALIFIED_BET", forecast=forecast)
    world.session.commit()
    run(world)

    vig = only_record(world, entry)["market"]["deVig"]
    r_sel = 1 / (1 + 124 / 100)
    r_opp = 1 / (1 + 100 / 142)
    assert vig["selectedImpliedProbability"] == pytest.approx(r_sel)
    assert vig["opposingImpliedProbability"] == pytest.approx(r_opp)
    assert vig["overround"] == pytest.approx(r_sel + r_opp - 1)
    assert vig["selectedNoVigProbability"] == pytest.approx(r_sel / (r_sel + r_opp))
    assert vig["selectedNoVigProbability"] + vig["opposingNoVigProbability"] == pytest.approx(1.0)


def test_each_leg_carries_its_own_observed_at_and_capture_clock(world: World):
    # The two legs were seen by the BOOK at different instants, and captured together.
    entry = world.add_game("CLOCKS", home_observed_at=QUOTE_AT - timedelta(minutes=3))
    forecast = world.forecast(entry, available_as_of=CHECKPOINT - timedelta(minutes=30))
    world.decide(entry, qualification="QUALIFIED", qualification_reason="ALL_GATES_PASSED",
                 execution="EXECUTED", execution_reason="POSITION_TAKEN",
                 recommendation="QUALIFIED_BET", forecast=forecast)
    world.session.commit()
    run(world)

    market = only_record(world, entry)["market"]
    assert market["selectedLeg"]["observedAtUtc"] == "2026-09-20T16:40:00Z"
    assert market["opposingLeg"]["observedAtUtc"] == "2026-09-20T16:37:00Z"
    assert market["selectedLeg"]["captureClockUtc"] == "2026-09-20T16:40:00Z"
    assert market["sameBook"] is True
    assert market["sportsbookKey"] == "sgo:book"
    assert "last_update" in market["selectedLeg"]["observedAtBasis"]


# ==========================================================================
# Declined and unfunded games ARE captured
# ==========================================================================

def test_declined_and_unfunded_games_are_captured_not_dropped(world: World):
    """A comparison that only sees funded games compares AISP's selection."""
    funded = world.add_game("A")
    unfunded = world.add_game("B")
    declined = world.add_game("C")
    for entry, quals in (
            (funded, ("QUALIFIED", "ALL_GATES_PASSED", "EXECUTED", "POSITION_TAKEN",
                      "QUALIFIED_BET")),
            (unfunded, ("QUALIFIED", "ALL_GATES_PASSED", "NOT_EXECUTED",
                        "STAKING_OR_EXPOSURE_CAP", "DECLINED")),
            (declined, ("DECLINED", "NO_QUALIFYING_VALUE", "NOT_EXECUTED",
                        "NO_QUALIFYING_VALUE", "DECLINED"))):
        forecast = world.forecast(entry, available_as_of=CHECKPOINT - timedelta(minutes=30))
        decision = world.decide(entry, qualification=quals[0], qualification_reason=quals[1],
                                execution=quals[2], execution_reason=quals[3],
                                recommendation=quals[4], forecast=forecast)
        if quals[2] == "EXECUTED":
            world.fund(entry, decision)
    world.session.commit()

    summary = run(world)
    assert summary["captures"] == 3 and summary["exclusions"] == 0
    for entry in (funded, unfunded, declined):
        record = only_record(world, entry)
        assert record["recordType"] == capture.RECORD_TYPE
        # Every one of them carries the full model/market input set.
        assert record["probabilities"]["rawModelProbability"]["value"] is not None
        assert record["probabilities"]["marketNoVigProbability"]["value"] is not None
        assert record["market"]["selectedLeg"]["americanOdds"] == 124


def test_qualification_funding_and_position_are_three_distinct_fields(world: World):
    """The 2026-09-20 defect: a value-qualified game with no position read as executed."""
    entry = world.add_game("UNFUNDED")
    forecast = world.forecast(entry, available_as_of=CHECKPOINT - timedelta(minutes=30))
    world.decide(entry, qualification="QUALIFIED", qualification_reason="ALL_GATES_PASSED",
                 execution="NOT_EXECUTED", execution_reason="STAKING_OR_EXPOSURE_CAP",
                 recommendation="DECLINED", forecast=forecast)
    world.session.commit()
    run(world)

    status = only_record(world, entry)["status"]
    assert status["qualificationStatus"] == "QUALIFIED"
    assert status["qualificationReason"] == "ALL_GATES_PASSED"
    assert status["fundingStatus"] == "NOT_EXECUTED"
    assert status["fundingReason"] == "STAKING_OR_EXPOSURE_CAP"
    assert status["positionStatus"] == "NO_POSITION"
    assert status["positionResult"] is None
    # Three keys, three answers, none collapsed into another.
    assert len({status["qualificationStatus"], status["fundingStatus"],
                status["positionStatus"]}) == 3


def test_a_funded_game_reads_as_funded_in_all_three_fields(world: World):
    entry = world.add_game("FUNDED")
    forecast = world.forecast(entry, available_as_of=CHECKPOINT - timedelta(minutes=30))
    decision = world.decide(entry, qualification="QUALIFIED",
                            qualification_reason="ALL_GATES_PASSED", execution="EXECUTED",
                            execution_reason="POSITION_TAKEN", recommendation="QUALIFIED_BET",
                            forecast=forecast)
    position = world.fund(entry, decision)
    world.session.commit()
    run(world)

    record = only_record(world, entry)
    assert record["status"]["qualificationStatus"] == "QUALIFIED"
    assert record["status"]["fundingStatus"] == "EXECUTED"
    assert record["status"]["positionStatus"] == str(PositionStatus.OPEN)
    assert record["identity"]["positionId"] == str(position.id)
    assert record["ledgerSettlement"]["present"] is True
    assert record["ledgerSettlement"]["stake"] == 5.0


def test_a_game_without_a_position_says_NOT_APPLICABLE_not_null(world: World):
    entry = world.add_game("NOPOS")
    forecast = world.forecast(entry, available_as_of=CHECKPOINT - timedelta(minutes=30))
    world.decide(entry, qualification="DECLINED", qualification_reason="NO_QUALIFYING_VALUE",
                 execution="NOT_EXECUTED", execution_reason="NO_QUALIFYING_VALUE",
                 recommendation="DECLINED", forecast=forecast)
    world.session.commit()
    run(world)

    record = only_record(world, entry)
    assert record["identity"]["positionId"] == capture.NOT_APPLICABLE == "NOT_APPLICABLE"
    assert record["ledgerSettlement"]["present"] is False


# ==========================================================================
# Exclusions: never a silent omission
# ==========================================================================

def test_a_game_with_no_qualifying_evidence_gets_an_exclusion_record_with_a_reason(world: World):
    world.add_game("NODECISION")
    world.session.commit()

    summary = run(world)
    assert summary["universe"] == 1
    assert summary["captures"] == 0 and summary["exclusions"] == 1
    record = only_record(world, world.games["NODECISION"])
    assert record["recordType"] == capture.EXCLUSION_TYPE
    assert record["exclusion"]["excluded"] is True
    assert record["exclusion"]["reasonCode"] == "NO_FINAL_PREGAME_DECISION_AT_CHECKPOINT"
    assert record["identity"]["gameId"] == str(world.games["NODECISION"]["game"].id)


@pytest.mark.parametrize("scenario,expected", [
    ("unpriced", "DECISION_NAMES_NO_ODDS_SNAPSHOT"),
    ("no_opposing_leg", "NO_SAME_BOOK_OPPOSING_LEG_VISIBLE_AT_CHECKPOINT"),
    ("decision_after_checkpoint", "ALL_FINAL_PREGAME_DECISIONS_AFTER_CHECKPOINT"),
    ("no_probability_channel", "NO_PROBABILITY_BEARING_DECISION_CHANNEL"),
    ("outcomes_not_distinct", "DECISION_LACKS_DISTINCT_QUALIFICATION_AND_EXECUTION_OUTCOMES"),
])
def test_every_excluded_shape_names_its_own_reason(world: World, scenario: str, expected: str):
    entry = world.add_game("X", pair=(scenario != "no_opposing_leg"))
    forecast = world.forecast(entry, available_as_of=CHECKPOINT - timedelta(minutes=30))
    kwargs = dict(qualification="QUALIFIED", qualification_reason="ALL_GATES_PASSED",
                  execution="EXECUTED", execution_reason="POSITION_TAKEN",
                  recommendation="QUALIFIED_BET", forecast=forecast)
    if scenario == "unpriced":
        world.decide(entry, names_snapshot=False, **kwargs)
    elif scenario == "no_opposing_leg":
        world.decide(entry, **kwargs)
    elif scenario == "decision_after_checkpoint":
        world.decide(entry, at=CHECKPOINT + timedelta(minutes=1), **kwargs)
    elif scenario == "no_probability_channel":
        world.decide(entry, **{**kwargs, "model_probability": None})
    elif scenario == "outcomes_not_distinct":
        world.decide(entry, **{**kwargs, "execution": None})
    world.session.commit()

    summary = run(world)
    assert summary["exclusions"] == 1, summary
    record = only_record(world, entry)
    assert record["recordType"] == capture.EXCLUSION_TYPE
    assert record["exclusion"]["reasonCode"] == expected


def test_the_probability_bearing_channel_is_chosen_not_the_latest_row(world: World):
    """Two channels write FINAL_PREGAME for one game; only one carries probabilities."""
    entry = world.add_game("TWOCHAN")
    forecast = world.forecast(entry, available_as_of=CHECKPOINT - timedelta(minutes=30))
    bearing = world.decide(
        entry, qualification="QUALIFIED", qualification_reason="ALL_GATES_PASSED",
        execution="EXECUTED", execution_reason="POSITION_TAKEN",
        recommendation="QUALIFIED_BET", forecast=forecast, at=CHECKPOINT - timedelta(minutes=2),
        model_probability=None,
        evidence={"raw_model_probability": 0.58, "adjusted_probability": 0.55,
                  "market_probability": 0.451, "policy_version": "V2_1", "staking_version": "S2"})
    # A V1-strict owner writes a LATER NO_BET row with no probabilities at all.
    strict = _experiment(world.session, "V1 STRICT", "AISP_OPPORTUNITY_POLICY_V1", version=2)
    world.decide(entry, qualification="DECLINED", qualification_reason="NO_GOVERNED_QUOTE",
                 execution="NOT_EXECUTED", execution_reason="NO_GOVERNED_QUOTE",
                 recommendation="NO_BET", at=CHECKPOINT, model_probability=None,
                 evidence={}, experiment=strict)
    world.session.commit()

    run(world)
    record = only_record(world, entry)
    assert record["recordType"] == capture.RECORD_TYPE
    assert record["identity"]["decisionId"] == str(bearing.id)
    assert record["identity"]["decisionChannelExperimentId"] == str(world.experiment.id)
    assert record["status"]["qualificationStatus"] == "QUALIFIED"
    assert record["probabilities"]["rawModelProbability"]["value"] == 0.58
    assert record["probabilities"]["policyAdjustedProbability"]["value"] == 0.55
    assert record["probabilities"]["retainedMarketProbability"]["value"] == 0.451


# ==========================================================================
# Outcomes
# ==========================================================================

def test_a_null_completion_time_is_preserved_with_its_basis(world: World):
    """NFL's source of record has no completion column. Nothing is invented."""
    entry = world.add_game("NFLFINAL")
    forecast = world.forecast(entry, available_as_of=CHECKPOINT - timedelta(minutes=30))
    world.decide(entry, qualification="QUALIFIED", qualification_reason="ALL_GATES_PASSED",
                 execution="EXECUTED", execution_reason="POSITION_TAKEN",
                 recommendation="QUALIFIED_BET", forecast=forecast)
    observation = world.nfl_final(entry, home_score=17, away_score=24)
    world.session.commit()
    run(world)

    outcome = only_record(world, entry)["outcome"]
    assert outcome["resolved"] is True
    assert outcome["sourceConfirmedTerminalStatus"] == "FINAL"
    assert outcome["homeScore"] == 17 and outcome["awayScore"] == 24
    assert outcome["outcomeRevisionIdentity"] == observation.payload_checksum
    # The two facts are kept apart, and the null keeps its stated basis.
    assert outcome["completionTimeUtc"] is None
    assert outcome["completionTimeBasis"] == (
        "SOURCE_CONFIRMED_TERMINAL_COMPLETION_TIME_UNAVAILABLE")
    # The same accepted basis string the V2 outcome lane already uses.
    from app.research_exports import v18_market_pilot_outcomes_v2 as v2
    assert capture.COMPLETION_TIME_UNAVAILABLE == v2.NULL_BASIS
    # The feed's own string is carried verbatim and never promoted to a clock.
    assert outcome["sourceLastUpdatedRaw"] == "2026-09-20 21:44:03 US/Eastern"
    assert outcome["retrievedAtUtc"] != outcome["completionTimeUtc"]


def test_a_source_supplied_completion_time_is_carried_as_one(world: World):
    """The sport-neutral lane does have one; it is used, and labelled as used."""
    entry = world.add_game("MLBLIKE")
    forecast = world.forecast(entry, available_as_of=CHECKPOINT - timedelta(minutes=30))
    world.decide(entry, qualification="QUALIFIED", qualification_reason="ALL_GATES_PASSED",
                 execution="EXECUTED", execution_reason="POSITION_TAKEN",
                 recommendation="QUALIFIED_BET", forecast=forecast)
    world.league.code = "MLB"       # route to game_state_observations
    final_at = KICKOFF + timedelta(hours=3, minutes=12)
    world.session.add(GameStateObservation(
        game_id=entry["game"].id, source="MLB_STATS_API", source_mode="LIVE",
        observed_at=final_at, available_as_of=final_at, payload_checksum=uuid.uuid4().hex,
        scheduled_at=KICKOFF, status="FINAL", home_score=3, away_score=5, final_at=final_at))
    world.session.commit()
    run(world, leagues=["MLB"])

    outcome = only_record(world, entry)["outcome"]
    assert outcome["source"] == "game_state_observations"
    assert outcome["completionTimeUtc"] == "2026-09-20T20:12:00Z"
    assert outcome["completionTimeBasis"] == "SOURCE_SUPPLIED_COMPLETION_TIME"


def test_an_unresolved_outcome_is_explicit_not_missing(world: World):
    entry = world.add_game("PENDING")
    forecast = world.forecast(entry, available_as_of=CHECKPOINT - timedelta(minutes=30))
    world.decide(entry, qualification="QUALIFIED", qualification_reason="ALL_GATES_PASSED",
                 execution="EXECUTED", execution_reason="POSITION_TAKEN",
                 recommendation="QUALIFIED_BET", forecast=forecast)
    world.session.commit()
    run(world)

    outcome = only_record(world, entry)["outcome"]
    assert outcome["resolved"] is False
    assert outcome["sourceConfirmedTerminalStatus"] is None
    assert outcome["terminalStatusBasis"] == "NO_SOURCE_CONFIRMED_TERMINAL_OBSERVATION"
    assert outcome["completionTimeBasis"] == "NO_SOURCE_CONFIRMED_TERMINAL_OBSERVATION"


def test_a_non_terminal_nfl_observation_is_never_read_as_a_final(world: World):
    entry = world.add_game("LIVE")
    forecast = world.forecast(entry, available_as_of=CHECKPOINT - timedelta(minutes=30))
    world.decide(entry, qualification="QUALIFIED", qualification_reason="ALL_GATES_PASSED",
                 execution="EXECUTED", execution_reason="POSITION_TAKEN",
                 recommendation="QUALIFIED_BET", forecast=forecast)
    world.nfl_final(entry, status="IN_PROGRESS", home_score=7, away_score=0)
    world.session.commit()
    run(world)

    outcome = only_record(world, entry)["outcome"]
    assert outcome["resolved"] is False
    assert outcome["homeScore"] is None and outcome["awayScore"] is None


# ==========================================================================
# No betting effect, and money stays in its own block
# ==========================================================================

def test_the_exporter_writes_nothing_to_decisions_or_positions(world: World):
    """Rule 3. Counted either side, on rows and on identities."""
    for tag, funded in (("P1", True), ("P2", False)):
        entry = world.add_game(tag)
        forecast = world.forecast(entry, available_as_of=CHECKPOINT - timedelta(minutes=30))
        decision = world.decide(
            entry, qualification="QUALIFIED", qualification_reason="ALL_GATES_PASSED",
            execution="EXECUTED" if funded else "NOT_EXECUTED",
            execution_reason="POSITION_TAKEN" if funded else "STAKING_OR_EXPOSURE_CAP",
            recommendation="QUALIFIED_BET" if funded else "DECLINED", forecast=forecast)
        if funded:
            world.fund(entry, decision)
    world.add_game("P3")                  # an exclusion, which also must not write
    world.session.commit()

    def snapshot() -> tuple:
        decisions = world.session.execute(
            select(ShadowExperimentDecision.id, ShadowExperimentDecision.recommendation,
                   ShadowExperimentDecision.qualification_outcome,
                   ShadowExperimentDecision.execution_outcome)
            .order_by(ShadowExperimentDecision.id)).all()
        positions = world.session.execute(
            select(OfficialAspPosition.id, OfficialAspPosition.stake,
                   OfficialAspPosition.status, OfficialAspPosition.result)
            .order_by(OfficialAspPosition.id)).all()
        return (tuple(decisions), tuple(positions))

    before = snapshot()
    summary = run(world)
    assert summary["records"] == 3
    after = snapshot()
    assert after == before
    assert world.session.scalar(select(func.count(ShadowExperimentDecision.id))) == 2
    assert world.session.scalar(select(func.count(OfficialAspPosition.id))) == 1


def test_a_write_during_the_read_is_refused(world: World):
    """Rule 3 is enforced, not merely asserted: SQLite ignores READ ONLY, so
    the guard counts rows itself."""
    entry = world.add_game("GUARD")
    world.session.commit()

    with pytest.raises(capture.CaptureRefused) as error:
        with capture._no_write_guard(world.session):
            world.session.add(OfficialAspPosition(
                shadow_experiment_id=world.experiment.id,
                market_outcome_id=entry["away_outcome"].id, placed_at=CHECKPOINT, stake=1.0,
                decimal_odds=2.0, result=PositionResult.PENDING, status=PositionStatus.OPEN))
            world.session.flush()
    assert error.value.code == "EXPORTER_CHANGED_DECISION_OR_POSITION_ROWS"
    world.session.rollback()


def test_money_appears_only_under_the_ledger_block(world: World):
    """Rule 4. Predictive scoring inputs must not be entangled with returns."""
    entry = world.add_game("MONEY")
    forecast = world.forecast(entry, available_as_of=CHECKPOINT - timedelta(minutes=30))
    decision = world.decide(entry, qualification="QUALIFIED",
                            qualification_reason="ALL_GATES_PASSED", execution="EXECUTED",
                            execution_reason="POSITION_TAKEN", recommendation="QUALIFIED_BET",
                            forecast=forecast)
    world.fund(entry, decision)
    world.session.commit()
    run(world)

    record = only_record(world, entry)
    capture.assert_money_is_quarantined(record)           # the real guard
    for block in ("probabilities", "outcome", "market", "status", "checkpoint"):
        text = json.dumps(record[block])
        for key in ("stake", "payout", "bankroll", "profit"):
            assert f'"{key}' not in text, (block, key)
    assert record["ledgerSettlement"]["stake"] == 5.0

    with pytest.raises(capture.CaptureRefused) as error:
        capture.assert_money_is_quarantined({"probabilities": {"stake": 5.0}})
    assert error.value.code == "MONEY_FIELD_OUTSIDE_LEDGER_BLOCK"


def test_no_blend_score_or_threshold_is_ever_computed(world: World):
    """Rule 4. Codex owns the comparison; we supply inputs and outcomes only."""
    entry = world.add_game("NOBLEND")
    forecast = world.forecast(entry, available_as_of=CHECKPOINT - timedelta(minutes=30))
    world.decide(entry, qualification="QUALIFIED", qualification_reason="ALL_GATES_PASSED",
                 execution="EXECUTED", execution_reason="POSITION_TAKEN",
                 recommendation="QUALIFIED_BET", forecast=forecast)
    world.nfl_final(entry)
    world.session.commit()
    run(world)

    record = only_record(world, entry)
    capture.assert_no_derived_comparison(record)
    with pytest.raises(capture.CaptureRefused) as error:
        capture.assert_no_derived_comparison({"probabilities": {"blend": 0.5}})
    assert error.value.code == "DERIVED_COMPARISON_FIELD_PRESENT"


# ==========================================================================
# Identity, orientation, definitions
# ==========================================================================

def test_the_record_states_orientation_identity_and_every_definition(world: World):
    entry = world.add_game("SHAPE")
    forecast = world.forecast(entry, available_as_of=CHECKPOINT - timedelta(minutes=30),
                              p_home=0.42)
    decision = world.decide(entry, qualification="QUALIFIED",
                            qualification_reason="ALL_GATES_PASSED", execution="EXECUTED",
                            execution_reason="POSITION_TAKEN", recommendation="QUALIFIED_BET",
                            forecast=forecast)
    world.fund(entry, decision)
    world.session.commit()
    run(world)

    record = only_record(world, entry)
    # Canonical bytes sort keys, so the on-disk order is alphabetical; the
    # ordered RECORD_FIELDS tuple is asserted inside build_record itself.
    assert set(record) == set(capture.RECORD_FIELDS)
    assert record["contractStatus"] == capture.CONTRACT_STATUS

    identity = record["identity"]
    assert identity["gameId"] == str(entry["game"].id)
    assert identity["decisionId"] == str(decision.id)
    assert identity["forecastRowId"] == str(forecast.id)
    assert identity["forecastRowTable"] == "shadow_forecasts"
    assert identity["forecastIdentity"] == forecast.forecast_id
    assert identity["forecastIdentityAgreesWithRow"] is True

    orientation = record["orientation"]
    assert orientation["orientationBasis"] == "game_teams.is_home"
    assert orientation["homeAbbreviation"] == "SHAPEH"
    assert orientation["awayAbbreviation"] == "SHAPEA"
    assert orientation["selectedSide"] == "AWAY"
    assert orientation["selectedTeamId"] == str(entry["away"].id)

    context = record["context"]
    assert context["engineId"] == "NFL_M1_XGBOOST"
    assert context["engineVersion"] == "1.0"
    assert context["policyVersion"] == "AISP_NFL_PAPER_POLICY_V1"
    assert context["stakingVersion"] == "AISP_QUARTER_KELLY_V1"

    for block in record["probabilities"].values():
        if isinstance(block, dict):
            assert block["definition"] and block["basis"]

    # The raw model probability is oriented to the SELECTED side, not to home.
    assert record["probabilities"]["forSide"] == "AWAY"
    assert record["probabilities"]["rawModelProbability"]["value"] == pytest.approx(0.58)


def test_the_checkpoint_is_the_decision_clock_and_created_at_is_never_used(world: World):
    entry = world.add_game("CLOCK")
    forecast = world.forecast(entry, available_as_of=CHECKPOINT - timedelta(minutes=50))
    world.decide(entry, qualification="QUALIFIED", qualification_reason="ALL_GATES_PASSED",
                 execution="EXECUTED", execution_reason="POSITION_TAKEN",
                 recommendation="QUALIFIED_BET", forecast=forecast, at=CHECKPOINT)
    world.session.commit()
    run(world)

    checkpoint = only_record(world, entry)["checkpoint"]
    # Four DIFFERENT clocks, none of them created_at, none of them each other.
    assert checkpoint["checkpointAtUtc"] == "2026-09-20T16:45:00Z"
    assert checkpoint["checkpointBasis"] == "DECISION_RECOMMENDED_AT"
    assert checkpoint["forecastAvailableAsOfUtc"] == "2026-09-20T15:55:00Z"
    assert checkpoint["forecastImportedAtUtc"] == "2026-09-20T16:15:00Z"
    assert checkpoint["forecastPublishedAtUtc"] == "2026-09-20T16:05:00Z"
    assert "transaction-START" in checkpoint["createdAtNotUsed"]
    # Every clock in the record is a UTC "Z" stamp, converted before formatting.
    text = json.dumps(only_record(world, entry))
    assert "-04:00" not in text and "-05:00" not in text


def test_a_forecast_published_after_the_checkpoint_is_an_exclusion(world: World):
    """Rule 2: a later forecast is never substituted, not even the one the
    decision names."""
    entry = world.add_game("LATEFC")
    forecast = world.forecast(entry, available_as_of=CHECKPOINT + timedelta(minutes=30))
    world.decide(entry, qualification="QUALIFIED", qualification_reason="ALL_GATES_PASSED",
                 execution="EXECUTED", execution_reason="POSITION_TAKEN",
                 recommendation="QUALIFIED_BET", forecast=forecast)
    world.session.commit()
    run(world)

    record = only_record(world, entry)
    assert record["recordType"] == capture.EXCLUSION_TYPE
    assert record["exclusion"]["reasonCode"] == "CONTROLLING_FORECAST_NOT_AVAILABLE_AT_CHECKPOINT"


def test_the_ledger_is_append_only_and_hash_chained(world: World):
    entry = world.add_game("LEDGER")
    forecast = world.forecast(entry, available_as_of=CHECKPOINT - timedelta(minutes=30))
    world.decide(entry, qualification="QUALIFIED", qualification_reason="ALL_GATES_PASSED",
                 execution="EXECUTED", execution_reason="POSITION_TAKEN",
                 recommendation="QUALIFIED_BET", forecast=forecast)
    world.session.commit()
    run(world)
    run(world)

    from app.research_exports.v18_market_pilot_seal import AttemptLedger
    entries = AttemptLedger(world.dir, name=capture.LEDGER_NAME).entries()
    assert [e["kind"] for e in entries] == ["RECORD_WRITTEN", "RECORD_ALREADY_PRESENT"]
    assert entries[0]["recordSha256"] == entries[1]["recordSha256"]
    assert entries[0]["seq"] == 1 and entries[1]["seq"] == 2
    assert entries[0]["prevLineSha256"] is None and entries[1]["prevLineSha256"]


def test_the_capture_lane_may_not_share_a_directory_with_a_pilot_lane(world: World):
    world.add_game("OVERLAP")
    world.session.commit()
    world.settings.v18_market_pilot_packet_dir = str(world.dir)
    summary = run(world)
    assert summary == {"enabled": True, "records": 0,
                       "refused": "CAPTURE_DIR_OVERLAPS_AN_EXISTING_EXPORT_LANE"}

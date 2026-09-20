"""V18 vs fresh-market pilot: consumer pregame packet export (protocol f75aeccd...).

Pins the rules the frozen protocol states and the owner restated: the
0..15-minute leg age (inclusive, never negative), completed receipts (a fetch
clock is not one), the lexicographic book rule, selection as of the lock, the
anchor tie-break, sealing only by T-5 and never twice, no outcome field
anywhere, and the research-export lane.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.jobs import lanes
from app.jobs.scheduler import build_scheduler
from app.models import (
    ExperimentDecisionPhase, ExternalIdMapping, Game, GameStateObservation, GameStatus, GameTeam, JobRun,
    League, Market, MarketOutcome, MarketType, OddsSnapshot, OddsStatus, OfficialAspPosition, PositionResult,
    PositionStatus, ProviderRequestLedger, RunStatus, Season, ShadowExperiment, ShadowExperimentDecision,
    ShadowForecast, ShadowForecastBundle, Sport, Sportsbook, Team,
)
from app.research_exports import v18_market_pilot as pilot
from app.research_exports import v18_market_pilot_seal as seal

START = datetime(2026, 9, 20, 20, 5, tzinfo=UTC)
LOCK = START - timedelta(minutes=15)            # 19:50
DEADLINE = START - timedelta(minutes=5)         # 20:00
GAME_PK = "824999"


# ---------------------------------------------------------------------------
# A small retained world: one game, one V18 forecast, decisions, quotes
# ---------------------------------------------------------------------------
class World:
    def __init__(self, session: Session, root: Path, *, object_overrides: dict | None = None) -> None:
        self.object_overrides = object_overrides
        self.session = session
        self.root = root
        self.raw = root / "raw"
        self.packets = root / "packets"
        self.ledger_path = root / "quarantine.json"
        self.ledger_path.write_text(json.dumps({
            "games": [{"mlbamGamePk": 823343}], "originalFinalOot": {"startDate": "2026-08-14",
                                                                     "endDate": "2026-08-27"}}),
            encoding="utf-8")
        sport = Sport(code="BASEBALL", name="Baseball")
        session.add(sport)
        session.flush()
        self.league = League(sport_id=sport.id, code="MLB", name="MLB")
        session.add(self.league)
        session.flush()
        season = Season(league_id=self.league.id, name="2026", starts_at=datetime(2026, 3, 1, tzinfo=UTC),
                        ends_at=datetime(2026, 11, 30, tzinfo=UTC))
        session.add(season)
        self.home = Team(league_id=self.league.id, name="Baltimore Orioles", abbreviation="BAL")
        self.away = Team(league_id=self.league.id, name="Milwaukee Brewers", abbreviation="MIL")
        session.add_all([self.home, self.away])
        session.flush()
        self.game = Game(league_id=self.league.id, season_id=season.id, starts_at=START,
                         status=GameStatus.SCHEDULED, source_mode="LIVE", scheduled_innings=9,
                         home_score=31337, away_score=27182)  # must never surface anywhere
        session.add(self.game)
        session.flush()
        session.add_all([
            GameTeam(game_id=self.game.id, team_id=self.home.id, is_home=True, score=31337),
            GameTeam(game_id=self.game.id, team_id=self.away.id, is_home=False, score=27182),
            ExternalIdMapping(entity_type="game", entity_id=self.game.id, source="MLB_STATS_API",
                              external_id=GAME_PK),
            ExternalIdMapping(entity_type="team", entity_id=self.home.id, source="MLB_STATS_API",
                              external_id="110"),
            ExternalIdMapping(entity_type="team", entity_id=self.away.id, source="MLB_STATS_API",
                              external_id="158"),
            GameStateObservation(game_id=self.game.id, observed_at=START - timedelta(days=2),
                                 available_as_of=START - timedelta(days=2), source="MLB_STATS_API",
                                 source_mode="LIVE", source_record_id=GAME_PK, payload_checksum="c" * 64,
                                 home_team_id=self.home.id, away_team_id=self.away.id, scheduled_at=START,
                                 status="scheduled", home_score=31337, away_score=27182),
        ])
        self.market = Market(game_id=self.game.id, market_type=MarketType.MONEYLINE, period="full_game")
        session.add(self.market)
        session.flush()
        self.home_outcome = MarketOutcome(market_id=self.market.id, outcome_key="baltimore orioles:None",
                                          label=self.home.name, participant_team_id=self.home.id)
        self.away_outcome = MarketOutcome(market_id=self.market.id, outcome_key="milwaukee brewers:None",
                                          label=self.away.name, participant_team_id=self.away.id)
        session.add_all([self.home_outcome, self.away_outcome])
        self.experiment = ShadowExperiment(
            id=uuid.UUID(pilot.V18_EXPERIMENT_ID), name="ASP MLB V18 CONDITIONAL VIRTUAL - 2026 V1",
            version=6, started_at=datetime(2026, 9, 12, 16, tzinfo=UTC), start_date=date(2026, 9, 12),
            starting_bankroll=1000, sport="MLB", recommendation_policy_version="AISP_PRACTICAL_PREGAME_POLICY_V2_1",
            staking_policy_version="S", decision_policy="AISP_PRACTICAL_PREGAME_POLICY_V2_1",
            controlling_engine=pilot.ENGINE_ID, supported_markets=["MONEYLINE"], kelly_fraction=0.25,
            unit_percentage=0.01, max_position_percentage=0.02, max_same_game_percentage=0.03,
            max_daily_percentage=0.1, code_commit="t", code_identity="t", is_active=True,
            model_era_id="ASP_MLB_V18_CONDITIONAL_VIRTUAL_20260912_V1_R1")
        session.add(self.experiment)
        session.flush()
        self.forecast = self._forecast()
        self._raw_schedule("R")
        self.books: dict[str, Sportsbook] = {}

    # -- producer object (synthetic, content-addressed like the real exchange) --
    P_HOME = 0.5431
    SEED = 20260907

    def producer_object(self, *, forecast_overrides: dict | None = None) -> tuple[str, Path, str, Path]:
        """Write a synthetic producer object + handoff exactly as the importer verifies them."""

        from app.shadow_import.mlb_v18_daily import _canonical_bytes, _sha_json

        exchange = self.root / "exchange"
        forecast_doc = {
            "schemaVersion": "MLB_V18_DAILY_GAME_FORECAST_V1", "gameId": str(self.game.id),
            "parentAuthority": {"bundleId": "mlb-v18-whole-game-a6e7e991",
                                "bundleSha256": pilot.FROZEN_MODEL["parentBundleSha256"],
                                "paModelSha256": pilot.FROZEN_MODEL["paSha256"],
                                "transitionSha256": pilot.FROZEN_MODEL["transitionSha256"]},
            "simulation": {"seed": self.SEED, "count": 5000},
            "probabilities": {"homeWin": self.P_HOME, "awayWin": 1 - self.P_HOME},
            "monteCarloUncertainty": {"homeWinProbabilityStandardError": (self.P_HOME * (1 - self.P_HOME) / 5000) ** 0.5},
            **(forecast_overrides or {}),
        }
        files = {
            "forecast.json": _canonical_bytes(forecast_doc),
            "execution-inputs.json": _canonical_bytes({"population": {"simulationCount": 5000,
                                                                      "homeRuns": [4, 2], "awayRuns": [3, 5]}}),
            "pregame-evidence.json": _canonical_bytes({"contentSha256": "e" * 64, "providerCalls": 0}),
        }
        body = {"schemaVersion": "MLB_V18_DAILY_GAME_OBJECT_MANIFEST_V1", "gameId": str(self.game.id),
                "artifacts": [{"relativePath": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
                              for name, data in sorted(files.items())]}
        object_sha = _sha_json(body)
        root = exchange / "objects" / "sha256" / object_sha
        root.mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            (root / name).write_bytes(data)
        (root / "manifest.json").write_bytes(_canonical_bytes({**body, "objectId": "obj",
                                                               "objectSha256": object_sha}))
        handoff_body = {"schemaVersion": "AISP_MLB_V18_DAILY_SLATE_HANDOFF_V1",
                        "forecasts": [{"objectSha256": object_sha, "manifestFile": "manifest.json",
                                       "forecastFile": "forecast.json", "gameId": str(self.game.id)}]}
        handoff_sha = _sha_json(handoff_body)
        handoff = exchange / "handoffs" / "sha256" / handoff_sha / "aisp-handoff.json"
        handoff.parent.mkdir(parents=True, exist_ok=True)
        handoff.write_bytes(_canonical_bytes({**handoff_body, "handoffId": "h", "handoffSha256": handoff_sha}))
        return object_sha, root, handoff_sha, handoff

    # -- forecast -------------------------------------------------------------
    def _forecast(self) -> ShadowForecast:
        created = LOCK - timedelta(minutes=70)
        object_sha, object_root, handoff_sha, handoff = self.producer_object(
            forecast_overrides=self.object_overrides)
        self.object_root, self.handoff_sha = object_root, handoff_sha
        bundle = ShadowForecastBundle(
            bundle_sha256=handoff_sha, contract_version="AISP_MLB_V18_DAILY_SLATE_HANDOFF_V1",
            bundle_id="mlb-v18-daily-handoff-test", logical_uri=str(handoff),
            source_fixture_id="b" * 64, slate_date=START.date(), league="MLB", sport="MLB",
            status="RESEARCH_SHADOW", generated_at=created + timedelta(minutes=1), forecast_count=1,
            quote_count=0, candidate_count=0, evidence={"handoffSha256": handoff_sha},
            imported_at=created + timedelta(minutes=4), created_at=created + timedelta(minutes=2))
        self.session.add(bundle)
        self.session.flush()
        p_home = self.P_HOME
        forecast = ShadowForecast(
            bundle_id=bundle.id, canonical_game_id=self.game.id, forecast_id="mlb-v18-daily-" + object_sha,
            forecast_schema_version="MLB_V18_DAILY_GAME_FORECAST_V1", snapshot_type="PROSPECTIVE",
            engine_id=pilot.ENGINE_ID, engine_version="mlb-v18-whole-game-a6e7e991",
            model_artifact_sha256=pilot.FROZEN_MODEL["paSha256"], model_manifest_sha256=object_sha,
            feature_vector_sha256="e" * 64, simulator_id="V18_WHOLE_GAME_MONTE_CARLO",
            simulator_version="MLB_V18_DAILY_GAME_FORECAST_V1", simulator_seed=self.SEED,
            event_id=str(self.game.id), source_event_id=GAME_PK, event_date=START.date(), season=2026,
            season_type="REGULAR", home_team_id=str(self.home.id), away_team_id=str(self.away.id),
            is_ready=True, readiness_score=1.0, readiness_threshold=1.0, status="PROSPECTIVE_RESEARCH_SHADOW",
            scheduled_start_utc=START, schedule_as_of_utc=created - timedelta(minutes=5),
            source_cutoff_utc=created - timedelta(minutes=5), forecast_cutoff_utc=created - timedelta(minutes=5),
            available_as_of_utc=created, full_home_win_probability=p_home, full_away_win_probability=1 - p_home,
            full_expected_home_score=4.4, full_expected_away_score=4.1, distributions={},
            uncertainty={"simulationCount": 5000,
                         "monteCarloStandardError": (p_home * (1 - p_home) / 5000) ** 0.5,
                         "illegalStateCount": 0, "nonterminationCount": 0, "deterministicReplayVerified": True},
            readiness_detail={"sides": {"HOME": {"lineupObservedAt": (created - timedelta(minutes=9)).isoformat(),
                                                 "lineupStatus": "CONFIRMED"}}},
            identity_evidence={"gameId": str(self.game.id)},
            lineage_detail={"objectSha256": object_sha, "parentBundleSha256": pilot.FROZEN_MODEL["parentBundleSha256"],
                            "sourceArtifact": str(object_root),
                            "inputReceipts": {"pregameSnapshotCapturedAt": (created - timedelta(minutes=5)).isoformat()},
                            "populationGeometry": {"supplementSha256": "f" * 64, "populationDigest": "9" * 64}},
            created_at=created + timedelta(minutes=2))
        self.session.add(forecast)
        self.session.add(JobRun(job_name=pilot.V18_IMPORT_JOB, status=RunStatus.SUCCEEDED,
                                started_at=created + timedelta(minutes=1, seconds=30),
                                completed_at=created + timedelta(minutes=5),
                                result={"v18": {"imported": [{"handoff_sha256": handoff_sha}]}}))
        self.session.flush()
        return forecast

    def _raw_schedule(self, game_type: str, *, archived: datetime | None = None,
                      game_date: str = "2026-09-20T20:05:00Z", home_id: int = 110, away_id: int = 158) -> None:
        archived = archived or LOCK - timedelta(minutes=40)
        folder = self.raw / "mlb_stats_api" / f"{archived:%Y}" / f"{archived:%m}" / f"{archived:%d}"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{archived:%H%M%S}_{archived:%f}_abc123.json"
        path.write_text(json.dumps({
            "metadata": {"source": "MLB_STATS_API", "ingestion_timestamp": archived.isoformat(),
                         "checksum": "0" * 64},
            "payload": {"schedule": {"dates": [{"date": "2026-09-20", "games": [{
                "gamePk": int(GAME_PK), "gameType": game_type, "gameDate": game_date,
                "officialDate": "2026-09-20", "doubleHeader": "N", "gameNumber": 1,
                "teams": {"home": {"team": {"id": home_id}}, "away": {"team": {"id": away_id}}},
                "linescore": {"teams": {"home": {"runs": 31337}}}}]}]}},
        }), encoding="utf-8")
        stamp = archived.timestamp()
        os.utime(path, (stamp, stamp))

    # -- decisions / positions ----------------------------------------------
    def decision(self, at: datetime, recommendation: str = "NO_BET", *, decision_id: str | None = None,
                 forecast: ShadowForecast | None = None, names_forecast: bool = True) -> ShadowExperimentDecision:
        row = ShadowExperimentDecision(
            id=uuid.UUID(decision_id) if decision_id else uuid.uuid4(), experiment_id=self.experiment.id,
            game_id=self.game.id, slate_date=START.date(), phase=ExperimentDecisionPhase.FINAL_PREGAME,
            market="MONEYLINE", period="Full game", selection="BAL", recommended_at=at,
            recommendation=recommendation, recommendation_reason="test", qualification_reasons=[],
            shadow_forecast_id=(forecast or self.forecast).id if names_forecast else None, created_at=at)
        self.session.add(row)
        self.session.flush()
        return row

    def position(self, decision: ShadowExperimentDecision) -> OfficialAspPosition:
        row = OfficialAspPosition(market_outcome_id=self.home_outcome.id, placed_at=decision.recommended_at,
                                  stake=10, decimal_odds=1.9, result=PositionResult.PENDING,
                                  status=PositionStatus.OPEN, strategy="S", shadow_experiment_id=self.experiment.id,
                                  shadow_decision_id=decision.id, shadow_forecast_id=self.forecast.id)
        self.session.add(row)
        self.session.flush()
        return row

    # -- quotes ---------------------------------------------------------------
    def quote(self, book: str, *, fetched: datetime, observed: datetime, home: int = -120, away: int = 110,
              receipt: bool = True, receipt_delay: timedelta = timedelta(seconds=3),
              job_delay: timedelta = timedelta(seconds=4), home_leg: bool = True,
              skew: dict | None = None, job: bool = True, job_status: RunStatus = RunStatus.SUCCEEDED) -> None:
        sportsbook = self.books.get(book)
        if sportsbook is None:
            sportsbook = self.books[book] = Sportsbook(key=f"sgo:{book}", name=book)
            self.session.add(sportsbook)
            self.session.flush()
        legs = [(self.away_outcome, away)] + ([(self.home_outcome, home)] if home_leg else [])
        for outcome, american in legs:
            decimal = float(pilot.american_to_decimal_exact(american))
            self.session.add(OddsSnapshot(
                market_outcome_id=outcome.id, sportsbook_id=sportsbook.id, observed_at=observed,
                available_as_of=fetched, american_odds=american, decimal_odds=decimal, status=OddsStatus.OPEN,
                source=pilot.SGO, source_event_id="EVT1", source_mode="LIVE",
                payload_checksum=uuid.uuid4().hex))
        if receipt and not self.session.query(ProviderRequestLedger).filter_by(
                requested_at=fetched, status="SUCCEEDED").count():
            filters = {"leagueID": "MLB", "limit": "1", "startsAfter": f"{fetched:%Y-%m-%dT%H:%M:%SZ}"}
            run = JobRun(job_name="collect-odds-checkpoint", status=job_status, started_at=fetched,
                         completed_at=fetched + job_delay,
                         result={"fallback": {"skew": skew, "request_filters": filters}} if skew else None)
            self.session.add(run)
            self.session.flush()
            job_id = run.id if job else None
            fingerprint = uuid.uuid4().hex
            reserved = ProviderRequestLedger(provider=pilot.SGO, endpoint_key="sgo_mlb_events", requested_at=fetched,
                                             status="RESERVED", request_fingerprint=fingerprint, job_run_id=job_id)
            self.session.add(reserved)
            self.session.flush()
            self.session.add(ProviderRequestLedger(
                provider=pilot.SGO, endpoint_key="sgo_mlb_events", requested_at=fetched,
                completed_at=fetched + receipt_delay, status="SUCCEEDED", request_fingerprint=fingerprint,
                records_received=1, job_run_id=job_id, reservation_id=reserved.id,
                response_metadata={"request_shape": {"filters": filters}, "objects_returned": 1}))
        self.session.flush()

    def sgo_response(self, *, archived: datetime, books: dict[str, tuple[str, str, str, str]],
                     modified: datetime | None = None, wrapped: bool = True, event_id: str = "EVT1") -> Path:
        """A retained SGO /events response, shaped like the real retained files.

        ``books`` maps a bookmaker to (home odds, home lastUpdatedAt, away odds,
        away lastUpdatedAt) as the provider sends them (strings). The body
        carries the fields the real response has -- including outcome-bearing
        ones (``score``, ``results``, closing odds) that must never be copied.
        """

        def side(odd_id, opposing, entity, which):
            return {
                "oddID": odd_id, "opposingOddID": opposing, "marketName": "Moneyline", "statID": "points",
                "statEntityID": entity, "periodID": "game", "betTypeID": "ml", "sideID": entity,
                "started": False, "ended": False, "cancelled": False, "bookOddsAvailable": True,
                "fairOddsAvailable": True, "fairOdds": "+101", "bookOdds": "-105", "scoringSupported": True,
                "openBookOdds": "-110", "openFairOdds": "+100", "closeBookOdds": "-125", "score": 31337,
                "byBookmaker": {name: {"odds": v[0 if which == "home" else 2],
                                       "lastUpdatedAt": v[1 if which == "home" else 3], "available": True,
                                       "deeplink": f"https://example.invalid/{name}"}
                                for name, v in books.items()},
            }

        def regulation(odd_id, entity):
            return {"oddID": odd_id, "marketName": "Regulation 3-Way Moneyline", "statID": "points",
                    "statEntityID": entity, "periodID": "reg", "betTypeID": "ml3way", "sideID": entity,
                    "byBookmaker": {}}

        body = {
            "success": True,
            "notice": "Response is missing 10445 bookmaker odds. Upgrade your API key to access all data.",
            "nextCursor": "CMLwBRoJKQD6U4igAQAAGhY6FEJDR1lr",
            "data": [{
                "eventID": event_id, "sportID": "BASEBALL", "leagueID": "MLB", "type": "match",
                "teams": {"home": {"teamID": "BALTIMORE_ORIOLES_MLB", "statEntityID": "home", "score": 31337,
                                   "names": {"long": "Baltimore Orioles", "short": "BAL"}},
                          "away": {"teamID": "MILWAUKEE_BREWERS_MLB", "statEntityID": "away", "score": 27182,
                                   "names": {"long": "Milwaukee Brewers", "short": "MIL"}}},
                "status": {"started": False, "completed": False, "cancelled": False, "ended": False,
                           "live": False, "startsAt": "2026-09-20T20:05:00.000Z", "hardStart": True,
                           "previousStartsAt": [], "oddsPresent": True, "oddsAvailable": True, "finalized": False},
                "info": {"venue": {"name": "Oriole Park at Camden Yards"}},
                "links": {"bookmakers": {name: f"https://example.invalid/{name}" for name in books}},
                "players": {"SHANE_BAZ_1_MLB": {"playerID": "SHANE_BAZ_1_MLB", "teamID": "BALTIMORE_ORIOLES_MLB",
                                                "name": "Shane Baz"}},
                "results": {"game": {"home": {"points": 31337}, "away": {"points": 27182}}},
                "odds": {
                    "points-home-game-ml-home": side("points-home-game-ml-home", "points-away-game-ml-away",
                                                     "home", "home"),
                    "points-away-game-ml-away": side("points-away-game-ml-away", "points-home-game-ml-home",
                                                     "away", "away"),
                    "points-home-reg-ml3way-home": regulation("points-home-reg-ml3way-home", "home"),
                    "points-all-reg-ml3way-draw": regulation("points-all-reg-ml3way-draw", "all"),
                },
            }],
        }
        folder = self.raw / pilot.SGO_RAW_DIRECTORY / f"{archived:%Y}" / f"{archived:%m}" / f"{archived:%d}"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{archived:%H%M%S}_{archived:%f}_{uuid.uuid4().hex[:10]}.json"
        document = ({"metadata": {"source": pilot.SGO, "ingestion_timestamp": archived.isoformat(),
                                  "checksum": "5" * 64, "status": "success", "request_fingerprint": None},
                     "payload": body} if wrapped else body)
        path.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
        stamp = (modified or archived).timestamp()
        os.utime(path, (stamp, stamp))
        return path

    # -- build ----------------------------------------------------------------
    def inputs(self, mode: str = pilot.MODE_PROSPECTIVE) -> pilot.BuildInputs:
        return pilot.BuildInputs(protocol=pilot.load_frozen_protocol(None), quarantine_ledger_path=self.ledger_path,
                                 raw_data_root=self.raw, mode=mode)

    def build(self, built_at: datetime | None = None) -> pilot.PacketBuild:
        game = pilot.read_game(self.session, self.game.id)
        ref = {"path": "attempt-ledger.jsonl", "sha256": "0" * 64, "bytes": 1, "locator": "seq=1"}
        return pilot.build_packet(self.session, game, inputs=self.inputs(), built_at=built_at or LOCK
                                  + timedelta(minutes=1), attempt_ref=ref)


@pytest.fixture
def world(session: Session, tmp_path: Path) -> World:
    return World(session, tmp_path)


@pytest.fixture
def evidence_gate_open(monkeypatch):
    """ONLY for tests of downstream price construction (book choice, no-vig, edges).

    Opens the market evidence stage (per-leg clocks, clock semantics, retained
    response, conventions), which no real SGO pair passes today, so the timing,
    receipt and book rules below it can still be exercised. Tests of the gate
    itself never use this fixture.
    """

    monkeypatch.setattr(pilot, "market_evidence_failures", lambda snapshot: [])


# ---------------------------------------------------------------------------
# 15-minute edges
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("age, expected", [
    (timedelta(0), None),
    (timedelta(minutes=15), None),
    (timedelta(minutes=15, seconds=1), "LEG_QUOTE_AGE_OVER_15_MINUTES"),
    (timedelta(minutes=16), "LEG_QUOTE_AGE_OVER_15_MINUTES"),
    (timedelta(seconds=-1), "LEG_QUOTE_AGE_NEGATIVE"),
    (timedelta(minutes=-3), "LEG_QUOTE_AGE_NEGATIVE"),
])
def test_quote_age_is_zero_to_fifteen_minutes_inclusive(age, expected):
    anchor = LOCK
    assert pilot.quote_age_failure(anchor - age, anchor) == expected


def test_missing_update_clock_is_not_evaluable():
    assert pilot.quote_age_failure(None, LOCK) == "LEG_QUOTE_UPDATE_CLOCK_MISSING"


@pytest.mark.parametrize("minutes, fresh", [(0, True), (15, True), (15.0 + 1 / 60, False), (-1, False)])
def test_packet_edges_at_the_research_anchor(world, evidence_gate_open, minutes, fresh):
    # No decision: the anchor is the labelled T-15 research cutoff.
    observed = LOCK - timedelta(minutes=minutes)
    fetched = min(observed, LOCK) - timedelta(minutes=1) if minutes >= 0 else LOCK - timedelta(minutes=2)
    world.quote("draftkings", fetched=fetched, observed=observed)
    build = world.build()
    assert build.packet["anchor"]["kind"] == "RESEARCH_T_MINUS_15"
    assert build.packet["anchor"]["atUtc"] == pilot.iso(LOCK)
    assert build.packet["market"]["status"] == ("FRESH_PAIRED" if fresh else "UNPRICED")
    if not fresh:
        expected = "MARKET_QUOTE_AGE_NEGATIVE" if minutes < 0 else "MARKET_QUOTE_OLDER_THAN_15_MINUTES"
        assert build.packet["eligibility"]["firstFailure"] == expected


# ---------------------------------------------------------------------------
# Receipts: a fetch clock is not one
# ---------------------------------------------------------------------------
def test_a_quote_without_a_completed_receipt_is_unpriced(world):
    world.quote("draftkings", fetched=LOCK - timedelta(minutes=5), observed=LOCK - timedelta(minutes=6),
                receipt=False)
    build = world.build()
    market = build.packet["market"]
    assert market["status"] == "UNPRICED"
    assert "MARKET_NO_RECEIPT_BY_ANCHOR" in market["reasonCodes"]
    leg = market["candidates"][0]["home"]
    assert leg["availableByIsReceipt"] is False
    assert "NOT proof of receipt" in leg["clockMeaning"]


def test_a_receipt_completed_after_the_anchor_is_unpriced(world):
    world.quote("draftkings", fetched=LOCK - timedelta(seconds=20), observed=LOCK - timedelta(minutes=2),
                receipt_delay=timedelta(seconds=10), job_delay=timedelta(seconds=40))
    build = world.build()
    assert build.packet["market"]["status"] == "UNPRICED"
    assert build.packet["eligibility"]["firstFailure"] == "MARKET_NO_RECEIPT_BY_ANCHOR"
    assert any("RECEIPT_AFTER_ANCHOR" in code for code in build.packet["market"]["candidates"][0]["reasonCodes"])


def test_a_one_sided_response_is_not_a_pair(world):
    world.quote("draftkings", fetched=LOCK - timedelta(minutes=3), observed=LOCK - timedelta(minutes=4),
                home_leg=False)
    build = world.build()
    assert build.packet["eligibility"]["firstFailure"] == "MARKET_NO_TWO_SIDED_SAME_RESPONSE_PAIR"


# ---------------------------------------------------------------------------
# Book choice
# ---------------------------------------------------------------------------
def test_primary_book_is_lexicographic_among_eligible_not_best_price(world, evidence_gate_open):
    world.quote("fanduel", fetched=LOCK - timedelta(minutes=4), observed=LOCK - timedelta(minutes=5),
                home=150, away=-170)                         # best home price
    world.quote("draftkings", fetched=LOCK - timedelta(minutes=4), observed=LOCK - timedelta(minutes=5),
                home=-200, away=170)
    world.quote("betmgm", fetched=LOCK - timedelta(minutes=40), observed=LOCK - timedelta(minutes=41))  # stale
    build = world.build()
    market = build.packet["market"]
    assert market["status"] == "FRESH_PAIRED"
    assert (market["selectedBookKey"], market["selectedProviderKey"]) == ("draftkings", pilot.SGO)
    assert {c["bookKey"]: c["eligible"] for c in market["candidates"]} == {
        "betmgm": False, "draftkings": True, "fanduel": True}
    r_home, r_away = 1 / (1 + 100 / 200), 1 / (1 + 170 / 100)
    assert market["qHome"] == pytest.approx(r_home / (r_home + r_away), abs=1e-12)


def test_a_books_latest_eligible_snapshot_is_used(world, evidence_gate_open):
    world.quote("draftkings", fetched=LOCK - timedelta(minutes=12), observed=LOCK - timedelta(minutes=13),
                home=-150, away=130)
    world.quote("draftkings", fetched=LOCK - timedelta(minutes=3), observed=LOCK - timedelta(minutes=4),
                home=-110, away=100)
    pair = world.build().packet["market"]["pair"]
    assert pair["home"]["american"] == -110
    assert pair["olderLegObservedAtUtc"] == pilot.iso(LOCK - timedelta(minutes=4))


def test_choose_primary_book_ignores_ineligible_snapshots():
    def snap(book, provider, eligible):
        s = pilot.Snapshot(snapshot_id=f"{provider}|{book}", book_key=book, provider_key=provider,
                           sportsbook_key=book, market_id="m", source_event_id="e", fetch_clock=LOCK)
        s.failures = [] if eligible else ["X"]
        return s
    reps = [snap("aaa", "Z", False), snap("bbb", "Y", True), snap("bbb", "X", True), snap("ccc", "A", True)]
    chosen = pilot.choose_primary_book(reps)
    assert (chosen.book_key, chosen.provider_key) == ("bbb", "X")
    assert pilot.choose_primary_book([snap("a", "b", False)]) is None


# ---------------------------------------------------------------------------
# Retained raw SGO responses: per-leg clocks, response identity, conventions
# ---------------------------------------------------------------------------
def _sgo_clock(moment: datetime) -> str:
    """A clock written the way SportsGameOdds writes lastUpdatedAt (milliseconds, Z)."""

    return f"{moment:%Y-%m-%dT%H:%M:%S}.{moment.microsecond // 1000:03d}Z"


FETCHED = LOCK - timedelta(minutes=4)
ARCHIVED = FETCHED + timedelta(seconds=2)       # inside [requested_at, receipt]: receipt +3s, job +4s
HOME_STAMP = LOCK - timedelta(minutes=6)        # the newer side
AWAY_STAMP = LOCK - timedelta(minutes=9)        # the older side: what AISP stored on BOTH legs


def _retained_pair(world, *, home_stamp=HOME_STAMP, away_stamp=AWAY_STAMP, home="-120", away="+110",
                   **response) -> Path:
    world.quote("draftkings", fetched=FETCHED, observed=min(home_stamp, away_stamp))
    return world.sgo_response(archived=response.pop("archived", ARCHIVED), books={
        "draftkings": (home, _sgo_clock(home_stamp), away, _sgo_clock(away_stamp))}, **response)


def _book(build, key: str = "draftkings") -> dict:
    """One book's candidate pair (the packet's `pair` is null whenever nothing is evaluable)."""

    return next(c for c in build.packet["market"]["candidates"] if c["bookKey"] == key)


EVIDENCE_REASONS_WITH_A_LINKED_RESPONSE = ["PAIR_QUOTE_CLOCK_SEMANTICS_UNPROVEN", "PAIR_CONVENTIONS_UNPROVED"]
EVIDENCE_REASONS_WITHOUT_ONE = ["LEG_HOME_QUOTE_CLOCK_NOT_PER_LEG", "LEG_AWAY_QUOTE_CLOCK_NOT_PER_LEG",
                                "PAIR_QUOTE_CLOCK_SEMANTICS_UNPROVEN", "PAIR_SOURCE_RESPONSE_NOT_RETAINED",
                                "PAIR_CONVENTIONS_UNPROVED"]


def _failures(candidate: dict) -> list[str]:
    return [code for code in candidate["reasonCodes"] if not code.startswith("LIMITATION_")]


def test_per_leg_clocks_are_recovered_from_the_retained_response(world):
    _retained_pair(world)
    build = world.build()
    assert build.schema_errors == []
    market = build.packet["market"]
    pair = _book(build)
    assert pair["perLegClocksRecovered"] is True
    home, away = pair["home"], pair["away"]
    # Each leg carries ITS OWN provider stamp, not the older one stored on both.
    assert home["quoteUpdatedAtUtc"] == pilot.iso(HOME_STAMP) and home["quoteUpdatedAtIsPerLeg"] is True
    assert away["quoteUpdatedAtUtc"] == pilot.iso(AWAY_STAMP) and away["quoteUpdatedAtIsPerLeg"] is True
    assert home["perLegClock"]["status"] == "RECOVERED_FROM_RETAINED_RESPONSE"
    assert home["perLegClock"]["providerField"] == (
        "data[eventID=EVT1].odds[points-home-game-ml-home].byBookmaker[draftkings].lastUpdatedAt")
    assert home["perLegClock"]["storedObservedAtUtc"] == pilot.iso(AWAY_STAMP)
    assert home["ageMinutesAtAnchor"] == 6.0 and away["ageMinutesAtAnchor"] == 9.0
    assert pair["legSkewSeconds"] == 180.0
    assert "LIMITATION_SGO_PER_LEG_PROVIDER_STAMPS_NOT_RETAINED_OLDER_SIDE_APPLIED_TO_BOTH" not in pair["reasonCodes"]
    assert "LIMITATION_RAW_PROVIDER_RESPONSE_NOT_ARCHIVED" not in market["reasonCodes"]
    # Per-leg clocks and a retained response are necessary, not sufficient: the
    # clock's semantics and the conventions are still unproven, so it stays UNPRICED.
    assert market["status"] == "UNPRICED" and pair["eligible"] is False
    assert _failures(pair) == EVIDENCE_REASONS_WITH_A_LINKED_RESPONSE
    assert build.packet["eligibility"]["firstFailure"] == "MARKET_EVIDENCE_UNPROVED"
    # The fetch clock is labelled as such and is never an update clock.
    assert pair["sourceResponse"]["fetchClockUtc"] == pilot.iso(FETCHED)
    assert pilot.iso(FETCHED) not in (home["quoteUpdatedAtUtc"], away["quoteUpdatedAtUtc"])
    assert build.diagnostics["sgoLegsPerLegClockRecovered"] == 2


def test_the_age_rule_reads_each_recovered_leg_clock(world):
    # Stored rows alone (older side on both legs, 9 minutes) pass TIMING ...
    world.quote("draftkings", fetched=FETCHED, observed=AWAY_STAMP)
    first = world.build().packet
    assert first["market"]["timingOnlyDiagnostics"]["timingFreshPrimaryBookKey"] == "draftkings"
    assert first["eligibility"]["firstFailure"] == "MARKET_EVIDENCE_UNPROVED"
    # ... but the retained response shows the home side stamped AFTER the anchor.
    after = LOCK + timedelta(minutes=1)
    world.sgo_response(archived=ARCHIVED, books={
        "draftkings": ("-120", _sgo_clock(after), "+110", _sgo_clock(AWAY_STAMP))})
    build = world.build()
    candidate = _book(build)
    assert candidate["home"]["quoteUpdatedAtUtc"] == pilot.iso(after)
    assert "LEG_QUOTE_AGE_NEGATIVE_HOME" in candidate["reasonCodes"]
    assert build.packet["market"]["timingOnlyDiagnostics"]["timingFreshBooks"] == []
    assert build.packet["eligibility"]["firstFailure"] == "MARKET_QUOTE_AGE_NEGATIVE"


def test_timing_fresh_pairs_are_unpriced_with_the_exact_evidence_reasons(world):
    # Fresh on its recorded clock (the older side copied to both legs), receipted,
    # two-sided, same book: timing passes; evidence does not.
    world.quote("draftkings", fetched=FETCHED, observed=AWAY_STAMP)
    world.quote("fanduel", fetched=FETCHED, observed=AWAY_STAMP)
    build = world.build()
    market = build.packet["market"]
    assert market["status"] == "UNPRICED" and market["pair"] is None and market["qHome"] is None
    assert build.packet["eligibility"]["priceEvaluable"] is False
    assert build.packet["eligibility"]["firstFailure"] == "MARKET_EVIDENCE_UNPROVED"
    assert market["reasonCodes"][:2] == ["MARKET_EVIDENCE_UNPROVED", "MARKET_NO_ELIGIBLE_BOOK"]
    for book in ("draftkings", "fanduel"):
        candidate = _book(build, book)
        assert candidate["eligible"] is False
        assert _failures(candidate) == EVIDENCE_REASONS_WITHOUT_ONE
        # The copied older clock is shown for what it is -- never relabelled per-leg or fresh.
        assert candidate["home"]["quoteUpdatedAtUtc"] == pilot.iso(AWAY_STAMP)
        assert candidate["home"]["quoteUpdatedAtIsPerLeg"] is False
    timing = market["timingOnlyDiagnostics"]
    assert [b["bookKey"] for b in timing["timingFreshBooks"]] == ["draftkings", "fanduel"]
    assert timing["timingFreshPrimaryEvidenceFailures"] == EVIDENCE_REASONS_WITHOUT_ONE
    assert "NOT EVALUABLE" in timing["meaning"]
    assert build.diagnostics["timingFreshBooks"] == 2 and build.diagnostics["eligibleBooks"] == 0
    assert "NOT_ENFORCED" not in json.dumps(build.packet) and "NOT_ENFORCED" not in json.dumps(build.evidence)


def test_the_ledger_only_sensitivity_applies_the_same_evidence_gate(world, monkeypatch):
    world.quote("draftkings", fetched=FETCHED, observed=AWAY_STAMP)
    assert world.build().diagnostics["eligibleBooksLedgerOnlyBound"] == 0
    monkeypatch.setattr(pilot, "market_evidence_failures", lambda snapshot: [])
    assert world.build().diagnostics["eligibleBooksLedgerOnlyBound"] == 1


def test_clock_semantics_are_not_established_for_sgo_by_any_default():
    semantics = pilot.QUOTE_CLOCK_SEMANTICS[pilot.SGO]
    assert semantics["established"] is False and semantics["meaning"] is None
    assert pilot.FIRST_FAILURE_ORDER[-1] == "MARKET_EVIDENCE_UNPROVED"


def test_the_pairs_response_identity_is_the_retained_file(world):
    path = _retained_pair(world)
    data = path.read_bytes()
    build = world.build()
    pair = _book(build)
    ref = pair["sourceResponseRef"]
    assert (ref["path"], ref["sha256"], ref["bytes"]) == (str(path), hashlib.sha256(data).hexdigest(), len(data))
    assert ref["locator"] == ("data[eventID=EVT1].odds[points-home-game-ml-home|points-away-game-ml-away]"
                              ".byBookmaker[draftkings]")
    identity = pair["sourceResponse"]
    assert identity["status"] == "LINKED" and identity["retained"] is True
    assert identity["sha256"] == ref["sha256"] and identity["bytes"] == len(data)
    assert identity["archivedAtUtc"] == pilot.iso(ARCHIVED)
    assert identity["archiveClockSource"] == "RAW_ARCHIVE_METADATA_INGESTION_TIMESTAMP"
    assert identity["rawArchivePayloadChecksum"] == "5" * 64
    assert identity["ledgerRowId"] and identity["requestFingerprint"]
    assert identity["providerEventId"] == "EVT1" and identity["providerBookmakerKey"] == "draftkings"
    # Both legs were read from that one response.
    assert pair["home"]["perLegClock"]["retainedResponseSha256"] == ref["sha256"]
    assert pair["away"]["perLegClock"]["retainedResponseSha256"] == ref["sha256"]
    # Nothing outcome-bearing in the raw body (score, results, closing odds) is copied.
    for document in (build.packet, build.evidence):
        for key, value in _walk(document):
            assert key not in pilot.FORBIDDEN_OUTCOME_KEYS, key
            assert key not in ("closeBookOdds", "fairOdds", "bookOdds"), key
            assert value not in (31337, 27182), (key, value)


def test_conventions_are_carried_only_as_the_provider_states_them(world):
    _retained_pair(world)
    conventions = _book(world.build())["conventions"]
    # Stated by the provider, cited by field.
    home = conventions["providerStated"]["home"]
    assert home["source"] == "data[eventID=EVT1].odds[points-home-game-ml-home]"
    assert home["fields"]["periodID"] == "game" and home["fields"]["betTypeID"] == "ml"
    assert home["fields"]["marketName"] == "Moneyline" and home["fields"]["sideID"] == "home"
    assert home["fields"]["opposingOddID"] == "points-away-game-ml-away"
    assert {"periodID": "reg", "betTypeID": "ml3way", "marketName": "Regulation 3-Way Moneyline"} in (
        conventions["otherMoneylineMarketsListed"])
    assert conventions["providerStatedSource"]["sha256"]
    assert conventions["providerPeriodId"] == "game"
    # Not stated by the provider: a status at the usual key, a null value, a reason; never asserted.
    for name in ("extraInnings", "settlementRule", "pitcherActionRule"):
        assert conventions[name] == "NOT_STATED_BY_PROVIDER"
        assert conventions["conventionDetails"][name]["value"] is None
    assert conventions["proved"] is False
    assert set(conventions["unprovedFields"]) == {"extraInnings", "settlementRule", "pitcherActionRule"}
    assert "LIMITATION_PITCHER_ACTION_CONVENTION_NOT_STATED_BY_PROVIDER" in conventions["reasonCodes"]
    assert "LIMITATION_EXTRA_INNINGS_INCLUSION_NOT_STATED_BY_PROVIDER" in conventions["reasonCodes"]
    assert conventions["eligibilityEffect"].startswith("ENFORCED")
    assert "INCLUDED" not in json.dumps(conventions) and "TWO_WAY_NO_TIE" not in json.dumps(conventions)


def test_a_missing_raw_response_is_kept_explicit(world):
    world.quote("draftkings", fetched=FETCHED, observed=AWAY_STAMP)
    build = world.build()
    market = build.packet["market"]
    assert market["status"] == "UNPRICED"
    pair = _book(build)
    assert "PAIR_SOURCE_RESPONSE_NOT_RETAINED" in pair["reasonCodes"]
    identity = pair["sourceResponse"]
    assert identity["status"] == "NOT_RETAINED" and identity["retained"] is False
    assert (identity["path"], identity["sha256"], identity["bytes"], identity["archivedAtUtc"]) == (
        None, None, None, None)
    assert identity["exactResponseBody"] is None
    assert identity["reasonCodes"] == ["RAW_RESPONSE_NOT_RETAINED_FOR_THIS_FETCH"]
    assert identity["fetchClockUtc"] == pilot.iso(FETCHED)
    assert pair["sourceResponseRef"]["locator"].startswith("/notes/response:")
    for leg in (pair["home"], pair["away"]):
        assert leg["quoteUpdatedAtUtc"] == pilot.iso(AWAY_STAMP)
        assert leg["quoteUpdatedAtIsPerLeg"] is False
        assert leg["perLegClock"]["providerLastUpdatedAtUtc"] is None
        assert leg["perLegClock"]["reasonCode"] == "PER_LEG_CLOCK_NOT_RECOVERED_NOT_RETAINED"
    assert pair["perLegClocksRecovered"] is False and pair["legSkewSeconds"] is None
    conventions = pair["conventions"]
    assert conventions["providerStated"] is None and conventions["providerStatedSource"] is None
    assert conventions["pitcherActionRule"] == "UNKNOWN_NO_MATCHING_RETAINED_RESPONSE"
    assert conventions["conventionDetails"]["extraInnings"]["value"] is None
    for code in ("LIMITATION_RAW_PROVIDER_RESPONSE_NOT_ARCHIVED",
                 "LIMITATION_SGO_PER_LEG_PROVIDER_STAMPS_NOT_RETAINED_OLDER_SIDE_APPLIED_TO_BOTH",
                 "LIMITATION_PITCHER_ACTION_CONVENTION_NOT_RETAINED"):
        assert code in market["reasonCodes"]


@pytest.mark.parametrize("where", ["after_the_receipt", "modified_after_the_anchor", "other_event"])
def test_a_response_not_bound_to_this_fetch_supplies_nothing(world, where):
    options = {"after_the_receipt": {"archived": FETCHED + timedelta(seconds=30)},
               "modified_after_the_anchor": {"modified": LOCK + timedelta(seconds=1)},
               "other_event": {"event_id": "EVT2"}}[where]
    _retained_pair(world, **options)
    pair = _book(world.build())
    expected = "RETAINED_RESPONSE_DOES_NOT_CONTAIN_EVENT" if where == "other_event" else "NOT_RETAINED"
    assert pair["sourceResponse"]["status"] == expected
    assert pair["perLegClocksRecovered"] is False
    assert pair["home"]["quoteUpdatedAtUtc"] == pilot.iso(AWAY_STAMP)
    assert "PAIR_SOURCE_RESPONSE_NOT_RETAINED" in pair["reasonCodes"]


def test_a_retained_response_that_disagrees_with_the_stored_legs_supplies_no_clock(world):
    _retained_pair(world, home="-135")                       # stored home price is -120
    pair = _book(world.build())
    identity = pair["sourceResponse"]
    assert identity["status"] == "RETAINED_RESPONSE_DISAGREES_WITH_STORED_LEGS"
    assert identity["reasonCodes"] == ["RETAINED_PRICE_DIFFERS_FROM_STORED"]
    assert identity["sha256"] is None and pair["perLegClocksRecovered"] is False
    assert "LIMITATION_RAW_PROVIDER_RESPONSE_RETAINED_RESPONSE_DISAGREES_WITH_STORED_LEGS" in pair["reasonCodes"]


def test_a_bare_provider_body_is_dated_by_its_archive_name(world):
    path = _retained_pair(world, wrapped=False)
    identity = _book(world.build())["sourceResponse"]
    assert identity["status"] == "LINKED"
    assert identity["archiveClockSource"] == "RAW_ARCHIVE_FILE_NAME_STAMP"
    assert identity["rawArchivePayloadChecksum"] is None
    assert identity["exactResponseBody"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_collectors_skew_receipt_is_evidence_never_a_clock(world):
    world.quote("draftkings", fetched=FETCHED, observed=AWAY_STAMP,
                skew={"pairs": 12, "max_skew_seconds": 0.0, "pairs_with_skew": 0, "simultaneity_claimed": False})
    build = world.build()
    pair = _book(build)
    receipt = pair["ingestParseSkewReceipt"]
    assert receipt["zeroSkewForEveryPair"] is True and receipt["pairs"] == 12
    assert receipt["field"] == "job_runs.result.fallback.skew"
    # A recorded zero skew never stands in for per-leg clocks.
    assert pair["home"]["quoteUpdatedAtIsPerLeg"] is False and pair["legSkewSeconds"] is None
    assert "LEG_HOME_QUOTE_CLOCK_NOT_PER_LEG" in pair["reasonCodes"]
    job_rows = build.evidence["rows"]["job_runs"]
    assert any(row.get("ingest_parse_skew_receipt") == receipt for row in job_rows.values())


def test_link_requires_the_older_stamp_to_equal_the_stored_observation():
    response = pilot.RetainedResponse(
        path="r.json", sha256="1" * 64, bytes=1, archived_at=ARCHIVED, archive_clock_source="X",
        file_modified_at=ARCHIVED, payload_checksum=None, request_fingerprint=None,
        payload={"data": [{"eventID": "E", "odds": {
            pilot.SGO_HOME_ODD_ID: {"oddID": pilot.SGO_HOME_ODD_ID, "opposingOddID": pilot.SGO_AWAY_ODD_ID,
                                    "periodID": "game", "betTypeID": "ml", "byBookmaker": {"fanduel": {
                                        "odds": "-120", "lastUpdatedAt": _sgo_clock(HOME_STAMP), "available": True}}},
            pilot.SGO_AWAY_ODD_ID: {"oddID": pilot.SGO_AWAY_ODD_ID, "opposingOddID": pilot.SGO_HOME_ODD_ID,
                                    "periodID": "game", "betTypeID": "ml", "byBookmaker": {"fanduel": {
                                        "odds": "+110", "lastUpdatedAt": _sgo_clock(AWAY_STAMP), "available": True}}},
        }}]})
    common = dict(event_id="E", book_key="fanduel", home_american=-120, away_american=110)
    assert pilot.link_sgo_pair([response], stored_observed=AWAY_STAMP, **common).linked
    moved = pilot.link_sgo_pair([response], stored_observed=AWAY_STAMP - timedelta(seconds=1), **common)
    assert moved.status == pilot.RAW_DISAGREES
    assert moved.reason_codes == ["RETAINED_OLDER_SIDE_STAMP_DIFFERS_FROM_STORED_OBSERVED_AT"]
    assert pilot.link_sgo_pair([], stored_observed=AWAY_STAMP, **common).status == pilot.RAW_NOT_RETAINED
    twin = pilot.RetainedResponse(**{**response.__dict__, "path": "s.json", "sha256": "2" * 64})
    assert pilot.link_sgo_pair([response, twin], stored_observed=AWAY_STAMP, **common).status == pilot.RAW_AMBIGUOUS


# ---------------------------------------------------------------------------
# Disposition 2: both completion receipts, with IDs, row hashes and linkage
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("kwargs, reason", [
    ({"job": False}, "RECEIPT_LEDGER_ROW_NAMES_NO_INSERTING_JOB_RUN"),
    ({"job_status": RunStatus.FAILED}, "RECEIPT_JOB_RUN_FAILED"),
])
def test_both_completion_receipts_are_required(world, evidence_gate_open, kwargs, reason):
    world.quote("draftkings", fetched=FETCHED, observed=AWAY_STAMP, **kwargs)
    build = world.build()
    assert build.packet["market"]["status"] == "UNPRICED"
    assert build.packet["eligibility"]["firstFailure"] == "MARKET_NO_RECEIPT_BY_ANCHOR"
    candidate = _book(build)
    assert f"LEG_HOME_{reason}" in candidate["reasonCodes"]
    receipts = candidate["home"]["completionReceipts"]
    assert receipts["established"] is False and receipts["reasonCode"] == reason
    assert candidate["home"]["availableByIsReceipt"] is False


def test_completion_receipts_carry_ids_row_hashes_and_linkage(world, evidence_gate_open):
    world.quote("draftkings", fetched=FETCHED, observed=AWAY_STAMP)
    build = world.build()
    home = build.packet["market"]["pair"]["home"]
    receipts = home["completionReceipts"]
    assert receipts["established"] is True and receipts["reasonCode"] is None
    rows = build.evidence["rows"]
    ledger_row = rows["provider_request_ledger"][receipts["ledger"]["id"]]
    job_row = rows["job_runs"][receipts["job"]["id"]]
    assert receipts["ledger"]["evidenceRowSha256"] == hashlib.sha256(pilot.canonical_bytes(ledger_row)).hexdigest()
    assert receipts["job"]["evidenceRowSha256"] == hashlib.sha256(pilot.canonical_bytes(job_row)).hexdigest()
    assert ledger_row["job_run_id"] == receipts["job"]["id"]
    assert receipts["linkage"] == {"oddsRowId": receipts["linkage"]["oddsRowId"],
                                   "rowAvailableAsOfEqualsLedgerRequestedAt": True, "ledgerNamesThisJobRun": True}
    # max(ledger +3s, job +4s): the later completion, never the request start.
    assert home["availableByUtc"] == pilot.iso(FETCHED + timedelta(seconds=4))
    assert "MoneylinePair.received_at (a pre-request clock)" in receipts["neverUsed"]


# ---------------------------------------------------------------------------
# Disposition 1: gameType=R bound to game, orientation and schedule revision
# ---------------------------------------------------------------------------
def test_phase_r_is_bound_to_game_orientation_and_schedule_revision(world):
    build = world.build()
    game = build.packet["game"]
    binding = game["phaseBinding"]
    assert game["phase"] == "R" and binding["bound"] is True
    assert {k for k, v in binding["checks"].items() if v} >= {"gamePk", "orientation", "scheduleRevision",
                                                              "archivedByLock"}
    assert binding["identityProjectionSha256"] == hashlib.sha256(
        pilot.canonical_bytes(binding["identityProjection"])).hexdigest()
    assert "linescore" not in json.dumps(binding) and binding["importerSeasonTypeConstantUsed"] is False
    assert f"identityProjectionSha256={binding['identityProjectionSha256']}" in game["phaseEvidenceRef"]["locator"]
    assert pilot.aware(build.evidence["files"]["phaseEvidence"]["archivedAtUtc"]) <= LOCK


@pytest.mark.parametrize("change, code", [
    ({"home_id": 158, "away_id": 110}, "PHASE_EVIDENCE_NOT_BOUND_TEAM_ORIENTATION"),
    ({"game_date": "2026-09-20T23:05:00Z"}, "PHASE_EVIDENCE_NOT_BOUND_SCHEDULE_REVISION"),
])
def test_a_capture_bound_to_another_orientation_or_revision_never_certifies_r(world, change, code):
    world._raw_schedule("R", archived=LOCK - timedelta(minutes=20), **change)   # the latest capture by the lock
    build = world.build()
    game = build.packet["game"]
    assert game["phase"] == "UNKNOWN" and code in game["reasonCodes"]
    assert game["phaseBinding"]["capturedGameType"] == "R"          # visible, never certified
    assert "PHASE_NOT_CERTIFIED_R" in build.packet["eligibility"]["reasonCodes"]
    assert build.packet["eligibility"]["primaryPopulationPhaseR"] is False


def test_unmapped_team_ids_leave_the_orientation_unproved(world):
    world.session.query(ExternalIdMapping).filter_by(entity_type="team").delete()
    world.session.flush()
    game = world.build().packet["game"]
    assert game["phase"] == "UNKNOWN"
    assert "PHASE_EVIDENCE_ORIENTATION_UNPROVED_TEAM_SOURCE_IDS_UNMAPPED" in game["reasonCodes"]


# ---------------------------------------------------------------------------
# Dispositions 4 and 6: the exact producer object, physically, and its numbers
# ---------------------------------------------------------------------------
def _digest(path: Path) -> tuple[str, str, int]:
    data = path.read_bytes()
    return str(path), hashlib.sha256(data).hexdigest(), len(data)


def test_the_forecast_binds_the_exact_producer_object_physically(world):
    build = world.build()
    forecast = build.packet["forecast"]
    object_ref, population_ref = forecast["objectRef"], forecast["scorePopulationRef"]
    assert (object_ref["path"], object_ref["sha256"], object_ref["bytes"]) == _digest(
        world.object_root / "manifest.json")
    assert (population_ref["path"], population_ref["sha256"], population_ref["bytes"]) == _digest(
        world.object_root / "execution-inputs.json")
    assert "execution-inputs.json#/population" in population_ref["locator"]
    assert forecast["objectRefIsPhysical"] is True and forecast["scorePopulationRefIsPhysical"] is True
    assert forecast["inputSnapshotRef"]["path"] == str(world.object_root / "pregame-evidence.json")
    verification = forecast["operationalEvidence"]["producerObjectVerification"]
    assert verification["verified"] is True and verification["reasonCodes"] == []
    assert {name for name, check in verification["identityChecks"].items() if check["agree"]} >= {
        "paSha256", "parentBundleSha256", "seed", "simulationCount", "pHome", "pAway", "gameId"}
    assert build.packet["eligibility"]["forecastEligible"] is True
    # Consumer nulls stay nulls; what the object holds is recovery provenance only.
    assert forecast["modelIdentity"]["transitionSha256"] is None
    assert build.evidence["notes"]["producerObjectVerification"]["producerObjectHashes"]["transitionSha256"] == (
        pilot.FROZEN_MODEL["transitionSha256"])
    assert "LIMITATION_NO_PER_FORECAST_DEPLOYMENT_ATTESTATION_RETAINED" in forecast["reasonCodes"]


@pytest.mark.parametrize("damage, code", [
    ("unreadable", "FORECAST_PRODUCER_OBJECT_NOT_PHYSICALLY_VERIFIED_UNREADABLE_BY_EXPORTER"),
    ("population", "FORECAST_SCORE_POPULATION_NOT_PHYSICALLY_VERIFIED_DIGEST_DIFFERS_FROM_MANIFEST"),
    ("seed", "FORECAST_AISP_SEED_DISAGREES_WITH_PRODUCER_OBJECT"),
    ("probability", "FORECAST_AISP_P_HOME_DISAGREES_WITH_PRODUCER_OBJECT"),
])
def test_an_unverifiable_or_disagreeing_producer_object_excludes_the_forecast(session, tmp_path, damage, code):
    # AISP rows are append-only, so a disagreement is built into the producer object itself.
    overrides = {"seed": {"simulation": {"seed": World.SEED + 1, "count": 5000}},
                 "probability": {"probabilities": {"homeWin": World.P_HOME + 0.01,
                                                   "awayWin": 1 - World.P_HOME - 0.01}}}.get(damage)
    world = World(session, tmp_path, object_overrides=overrides)
    if damage == "unreadable":
        (world.object_root / "manifest.json").rename(world.object_root / "manifest.moved")
    elif damage == "population":
        (world.object_root / "execution-inputs.json").write_bytes(b'{"population":{}}')
    build = world.build()
    assert code in build.packet["eligibility"]["reasonCodes"]
    assert build.packet["eligibility"]["forecastEligible"] is False
    assert build.packet["eligibility"]["firstFailure"] == "FORECAST_INTEGRITY_FAILED"
    assert build.schema_errors == []
    if damage == "unreadable":
        assert build.packet["forecast"]["objectRefIsPhysical"] is False
        assert build.packet["forecast"]["scorePopulationRefIsPhysical"] is False


# ---------------------------------------------------------------------------
# Disposition 5: the anchor
# ---------------------------------------------------------------------------
def test_a_selected_decision_before_the_window_still_has_priority():
    early = _candidate(20, "QUALIFIED_BET")                 # T-35: before [T-30, T-15]
    latest = _candidate(2)
    choice = pilot.choose_anchor([early, latest], [pilot.PositionLink("p", early.id, "f")], LOCK)
    assert choice.kind == "ACTUAL_DECISION" and choice.decision.id == early.id
    assert "ANCHOR_POSITION_DECISION_OUTSIDE_T30_T15_LEAD_TIME_STRATUM" in choice.reason_codes


def test_a_position_decision_after_the_lock_was_not_already_selected():
    after = _candidate(-2, "QUALIFIED_BET")
    latest = _candidate(3)
    choice = pilot.choose_anchor([after, latest], [pilot.PositionLink("p", after.id, "f")], LOCK)
    assert choice.decision.id == latest.id
    assert "POSITION_DECISION_AFTER_LOCK_NOT_ALREADY_SELECTED_IGNORED" in choice.reason_codes


def test_a_position_whose_forecast_differs_from_its_decisions_is_an_ambiguity_exclusion():
    backed = _candidate(5, "QUALIFIED_BET")                 # names forecast "f"
    choice = pilot.choose_anchor([backed], [pilot.PositionLink("p", backed.id, "g")], LOCK)
    assert choice.kind == "NONE_EXCLUDED" and choice.at is None
    assert "ANCHOR_AMBIGUOUS_POSITION_FORECAST_BINDING_CONFLICTS_WITH_DECISION" in choice.reason_codes


def test_a_selected_decision_without_a_forecast_is_not_replaced_by_the_research_cutoff():
    backed = pilot.DecisionCandidate(id=str(uuid.uuid4()), recommended_at=LOCK - timedelta(minutes=5),
                                     recommendation="QUALIFIED_BET", shadow_forecast_id=None)
    choice = pilot.choose_anchor([backed], [pilot.PositionLink("p", backed.id)], LOCK)
    assert choice.kind == "ACTUAL_DECISION" and choice.decision.id == backed.id
    assert "ANCHOR_POSITION_DECISION_NAMES_NO_V18_FORECAST_NO_RESEARCH_FALLBACK" in choice.reason_codes


def test_a_decision_naming_a_forecast_with_missing_evidence_is_excluded_without_fallback(world):
    decision = world.decision(LOCK - timedelta(minutes=6), "NO_BET")
    (world.object_root / "manifest.json").rename(world.object_root / "manifest.moved")
    build = world.build()
    anchor = build.packet["anchor"]
    assert anchor["kind"] == "ACTUAL_DECISION" and anchor["decisionId"] == str(decision.id)
    assert build.packet["eligibility"]["forecastEligible"] is False
    assert build.packet["eligibility"]["firstFailure"] == "FORECAST_INTEGRITY_FAILED"
    assert build.packet["forecast"]["forecastIdentity"] == world.forecast.forecast_id   # never substituted
    assert "ANCHOR_RESEARCH_FORECAST_IS_LATEST_HANDOFF_BY_LOCK_TIE_FORECAST_ID_ASC" not in (
        build.packet["eligibility"]["reasonCodes"])


def test_the_research_cutoff_keeps_the_real_decision_rows_and_the_selection_state(world, evidence_gate_open):
    # A checkpoint that bound no V18 forecast.
    decision = world.decision(LOCK - timedelta(minutes=5), "NO_BET", names_forecast=False)
    world.quote("draftkings", fetched=LOCK - timedelta(minutes=2), observed=LOCK - timedelta(minutes=3))
    build = world.build()
    anchor = build.packet["anchor"]
    assert anchor["kind"] == "RESEARCH_T_MINUS_15" and anchor["decisionId"] is None
    assert anchor["atUtc"] == pilot.iso(LOCK) and anchor["selectionCheckpointDecisionId"] == str(decision.id)
    assert str(decision.id) in build.evidence["rows"]["shadow_experiment_decisions"]
    assert build.packet["selection"]["statusAtResearchLock"] == "EVALUATED_NOT_SELECTED"
    assert build.packet["forecast"]["forecastIdentity"] == world.forecast.forecast_id
    # Prices are matched to T-15, the research cutoff.
    assert build.packet["market"]["anchorUtc"] == pilot.iso(LOCK)
    assert build.packet["market"]["pair"]["home"]["ageMinutesAtAnchor"] == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# Seal clocks and the running consumer's identity
# ---------------------------------------------------------------------------
def test_the_evidence_names_the_running_consumer_and_both_seal_clocks(world):
    build = world.build()
    identity = build.evidence["consumerIdentity"]
    assert identity["exportRevision"] == pilot.EXPORT_REVISION == build.evidence["exportRevision"]
    assert build.packet["selection"]["consumerExportRevision"] == pilot.EXPORT_REVISION
    assert identity["gitCommit"] is None or len(identity["gitCommit"]) == 40
    assert identity["gitCommit"] or identity["reason"]
    assert identity["builderModuleSha256"] == hashlib.sha256(Path(pilot.__file__).read_bytes()).hexdigest()
    clocks = build.evidence["clockConventions"]
    assert "BUILD START" in clocks["packet_sealedAtUtc"]
    assert "COMPLETION" in clocks["attemptLedger_SEALED_writtenAtUtc"]
    assert "BEFORE the request" in clocks["sgo_MoneylinePair_received_at"]


def test_git_head_is_read_from_plain_and_linked_checkouts(tmp_path):
    sha = "1" * 40
    plain = tmp_path / "plain"
    (plain / ".git").mkdir(parents=True)
    (plain / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (plain / ".git" / "packed-refs").write_text(f"# pack-refs\n{sha} refs/heads/main\n", encoding="utf-8")
    assert pilot._git_head(plain)["gitCommit"] == sha
    common = tmp_path / "common"
    (common / "refs" / "heads").mkdir(parents=True)
    (common / "refs" / "heads" / "wt").write_text("2" * 40 + "\n", encoding="utf-8")
    gitdir = common / "worktrees" / "wt"
    gitdir.mkdir(parents=True)
    (gitdir / "HEAD").write_text("ref: refs/heads/wt\n", encoding="utf-8")
    (gitdir / "commondir").write_text("../..\n", encoding="utf-8")
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    assert pilot._git_head(linked)["gitCommit"] == "2" * 40
    assert pilot._git_head(tmp_path / "nothing")["gitCommit"] is None


# ---------------------------------------------------------------------------
# Anchor and selection
# ---------------------------------------------------------------------------
def _candidate(minutes_before_lock: float, recommendation="NO_BET", ident=None):
    return pilot.DecisionCandidate(id=ident or str(uuid.uuid4()),
                                   recommended_at=LOCK - timedelta(minutes=minutes_before_lock),
                                   recommendation=recommendation, shadow_forecast_id="f")


def test_anchor_is_the_latest_decision_in_the_window_ties_by_decision_id():
    a = _candidate(2, ident="00000000-0000-0000-0000-00000000000b")
    b = _candidate(2, ident="00000000-0000-0000-0000-00000000000a")
    earlier = _candidate(10)
    outside_early = _candidate(15.5)          # before T-30
    after_lock = _candidate(-1)               # after T-15
    choice = pilot.choose_anchor([a, b, earlier, outside_early, after_lock], [], LOCK)
    assert choice.kind == "ACTUAL_DECISION"
    assert choice.decision.id == b.id
    assert "ANCHOR_TIE_BROKEN_BY_DECISION_ID_ASCENDING" in choice.reason_codes
    assert {c.id for c in choice.candidates} == {a.id, b.id, earlier.id}


def test_anchor_window_edges_are_inclusive():
    at_t30 = _candidate(15)
    at_t15 = _candidate(0)
    assert pilot.choose_anchor([at_t30], [], LOCK).decision.id == at_t30.id
    assert pilot.choose_anchor([at_t30, at_t15], [], LOCK).decision.id == at_t15.id


def test_a_position_backed_decision_wins_and_conflicts_are_excluded():
    latest = _candidate(1)
    backed = _candidate(8, "QUALIFIED_BET")
    choice = pilot.choose_anchor([latest, backed], [pilot.PositionLink("p1", backed.id, "f")], LOCK)
    assert choice.decision.id == backed.id and "ANCHOR_IS_DECISION_BEHIND_POSITION" in choice.reason_codes
    other = pilot.DecisionCandidate(id=str(uuid.uuid4()), recommended_at=LOCK - timedelta(minutes=3),
                                    recommendation="QUALIFIED_BET", shadow_forecast_id="g")
    ambiguous = pilot.choose_anchor([backed, other], [pilot.PositionLink("p1", backed.id),
                                                      pilot.PositionLink("p2", other.id)], LOCK)
    assert ambiguous.kind == "NONE_EXCLUDED" and ambiguous.at is None


def test_no_decision_falls_back_to_the_labelled_research_cutoff():
    choice = pilot.choose_anchor([_candidate(20)], [], LOCK)
    assert choice.kind == "RESEARCH_T_MINUS_15" and choice.at == LOCK and choice.decision is None
    assert pilot.selection_status(choice, experiment_present=True, export_complete=True)[0] == "NO_FINAL_DECISION"


def test_a_checkpoint_that_bound_no_forecast_is_not_a_usable_anchor_but_still_states_selection():
    usable_earlier = _candidate(9)
    latest_without_forecast = pilot.DecisionCandidate(
        id=str(uuid.uuid4()), recommended_at=LOCK - timedelta(minutes=2), recommendation="NO_BET",
        shadow_forecast_id=None)
    choice = pilot.choose_anchor([usable_earlier, latest_without_forecast], [], LOCK)
    assert choice.kind == "RESEARCH_T_MINUS_15" and choice.at == LOCK and choice.decision is None
    assert choice.selection_decision.id == latest_without_forecast.id   # no earlier substitution
    status, reasons = pilot.selection_status(choice, experiment_present=True, export_complete=True)
    assert status == "EVALUATED_NOT_SELECTED" and "SELECTION_CHECKPOINT_BOUND_NO_V18_FORECAST" in reasons


@pytest.mark.parametrize("recommendation, expected", [
    ("QUALIFIED_BET", "SELECTED"), ("NO_BET", "EVALUATED_NOT_SELECTED"), ("SOMETHING_NEW", "UNRESOLVED"),
])
def test_selection_status_from_the_anchor_decision(recommendation, expected):
    choice = pilot.choose_anchor([_candidate(3, recommendation)], [], LOCK)
    assert pilot.selection_status(choice, experiment_present=True, export_complete=True)[0] == expected


def test_selection_without_a_decision_or_with_an_incomplete_export():
    none = pilot.choose_anchor([], [], LOCK)
    assert pilot.selection_status(none, experiment_present=True, export_complete=True)[0] == "NO_FINAL_DECISION"
    decided = pilot.choose_anchor([_candidate(3, "QUALIFIED_BET")], [], LOCK)
    assert pilot.selection_status(decided, experiment_present=True, export_complete=False)[0] == "UNRESOLVED"
    assert pilot.selection_status(decided, experiment_present=False, export_complete=True)[0] == "UNRESOLVED"


def test_selected_packet_anchors_on_the_decision_and_matches_quotes_to_it(world, evidence_gate_open):
    decision = world.decision(LOCK - timedelta(minutes=6), "QUALIFIED_BET")
    world.position(decision)
    world.quote("draftkings", fetched=LOCK - timedelta(minutes=9), observed=LOCK - timedelta(minutes=10))
    build = world.build()
    packet = build.packet
    assert packet["anchor"]["kind"] == "ACTUAL_DECISION"
    assert packet["anchor"]["decisionId"] == str(decision.id)
    assert packet["selection"]["statusAtResearchLock"] == "SELECTED"
    assert packet["selection"]["completeAsOfExport"] is True
    # 10 minutes before the lock is 4 minutes before the decision anchor.
    assert packet["market"]["pair"]["home"]["ageMinutesAtAnchor"] == pytest.approx(4.0)
    assert packet["forecast"]["forecastIdentity"] == world.forecast.forecast_id
    assert packet["eligibility"]["forecastEligible"] is True
    assert packet["eligibility"]["priceEvaluable"] is True


def test_a_freeze_run_in_flight_at_the_lock_leaves_selection_unresolved(world):
    world.decision(LOCK - timedelta(minutes=6), "NO_BET")
    world.session.add(JobRun(job_name=pilot.FREEZE_JOB, status=RunStatus.RUNNING,
                             started_at=LOCK - timedelta(minutes=1)))
    world.session.flush()
    packet = world.build().packet
    assert packet["selection"]["statusAtResearchLock"] == "UNRESOLVED"
    assert packet["selection"]["completeAsOfExport"] is False


# ---------------------------------------------------------------------------
# Identity, phase and schema
# ---------------------------------------------------------------------------
def test_phase_comes_only_from_a_retained_schedule_capture(world, tmp_path):
    build = world.build()
    game = build.packet["game"]
    assert game["phase"] == "R" and game["phaseEvidenceRef"]["sha256"]
    assert game["quarantineIdentityGate"]["eligible"] is True
    empty = pilot.BuildInputs(protocol=pilot.load_frozen_protocol(None), quarantine_ledger_path=world.ledger_path,
                              raw_data_root=tmp_path / "nothing")
    unknown = pilot.build_packet(world.session, pilot.read_game(world.session, world.game.id), inputs=empty,
                                 built_at=LOCK + timedelta(minutes=1), attempt_ref=build.packet["attemptLedgerRef"])
    assert unknown.packet["game"]["phase"] == "UNKNOWN"
    assert unknown.packet["game"]["phaseEvidenceRef"] is None
    assert unknown.packet["eligibility"]["firstFailure"] == "PHASE_NOT_CERTIFIED_R"
    assert pilot.phase_from_game_type("F") == "POSTSEASON" and pilot.phase_from_game_type("S") == "OTHER"


def test_every_packet_validates_against_the_frozen_schema(world):
    world.quote("draftkings", fetched=LOCK - timedelta(minutes=4), observed=LOCK - timedelta(minutes=5))
    build = world.build()
    assert build.schema_errors == []
    validator, errors = pilot.validate_packet({**build.packet, "outcome": 1}, pilot.load_frozen_protocol().schema)
    assert errors, validator
    broken = json.loads(json.dumps(build.packet))
    broken["market"]["status"] = "PRICED_SOMEHOW"
    assert pilot.validate_packet(broken, pilot.load_frozen_protocol().schema)[1]


def test_the_vendored_protocol_is_the_frozen_one():
    protocol = pilot.load_frozen_protocol(None)
    assert protocol.sha256 == pilot.PROTOCOL_SHA256
    assert protocol.schema["$id"] == "MLB_V18_FRESH_MARKET_PREGAME_PACKET_V1"


# ---------------------------------------------------------------------------
# No outcome fields, anywhere
# ---------------------------------------------------------------------------
def _walk(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield None, child
            yield from _walk(child)


def test_no_outcome_field_or_value_reaches_the_packet_or_its_evidence(world):
    decision = world.decision(LOCK - timedelta(minutes=6), "QUALIFIED_BET")
    world.position(decision)
    world.quote("draftkings", fetched=LOCK - timedelta(minutes=4), observed=LOCK - timedelta(minutes=5))
    build = world.build()
    for document in (build.packet, build.evidence):
        for key, value in _walk(document):
            assert key not in pilot.FORBIDDEN_OUTCOME_KEYS, key
            assert value not in (31337, 27182), (key, value)
    with pytest.raises(pilot.OutcomeFieldError):
        pilot.assert_no_outcome_fields({"market": {"candidates": [{"home_score": 3}]}})


# ---------------------------------------------------------------------------
# Sealing: once, by T-5, never overwritten
# ---------------------------------------------------------------------------
def test_seal_writes_packet_evidence_and_sums_once(world):
    world.quote("draftkings", fetched=LOCK - timedelta(minutes=4), observed=LOCK - timedelta(minutes=5))
    game = pilot.read_game(world.session, world.game.id)
    outcome = seal.seal_game(world.session, game, inputs=world.inputs(), directory=world.packets,
                             clock=lambda: LOCK + timedelta(minutes=1))
    assert outcome.sealed and outcome.code == "SEALED"
    packet_file = world.packets / f"{outcome.episode_key}.json"
    sums = (world.packets / f"{outcome.episode_key}.sha256").read_text().split()
    import hashlib
    assert sums[0] == hashlib.sha256(packet_file.read_bytes()).hexdigest() == outcome.packet_sha256
    packet = json.loads(packet_file.read_bytes())
    assert packet["attemptLedgerRef"]["locator"].endswith("#seq=1")
    ledger = seal.AttemptLedger(world.packets).entries()
    assert [e["kind"] for e in ledger] == ["SEAL_ATTEMPT", "SEALED"]
    assert ledger[1]["prevLineSha256"] == packet["attemptLedgerRef"]["sha256"]
    evidence = world.packets / packet["sourceRefs"][0]["path"]
    assert hashlib.sha256(evidence.read_bytes()).hexdigest() == packet["sourceRefs"][0]["sha256"]


def test_seal_never_overwrites(world):
    game = pilot.read_game(world.session, world.game.id)
    first = seal.seal_game(world.session, game, inputs=world.inputs(), directory=world.packets,
                           clock=lambda: LOCK + timedelta(minutes=1))
    before = (world.packets / f"{first.episode_key}.json").read_bytes()
    second = seal.seal_game(world.session, game, inputs=world.inputs(), directory=world.packets,
                            clock=lambda: LOCK + timedelta(minutes=3))
    assert not second.sealed and second.code == "ALREADY_SEALED_NO_OVERWRITE"
    assert (world.packets / f"{first.episode_key}.json").read_bytes() == before
    with pytest.raises(FileExistsError):
        seal.write_once(world.packets / f"{first.episode_key}.json", b"{}")


@pytest.mark.parametrize("now, code", [
    (DEADLINE + timedelta(seconds=1), "AFTER_SEAL_DEADLINE"),
    (START + timedelta(minutes=1), "AFTER_SEAL_DEADLINE"),
])
def test_seal_is_refused_after_t_minus_5(world, now, code):
    game = pilot.read_game(world.session, world.game.id)
    outcome = seal.seal_game(world.session, game, inputs=world.inputs(), directory=world.packets,
                             clock=lambda: now)
    assert not outcome.sealed and outcome.code == code
    assert not list(world.packets.glob("*.json"))
    assert seal.AttemptLedger(world.packets).entries()[-1]["kind"] == "SEAL_REFUSED"


def test_seal_at_exactly_t_minus_5_is_allowed_and_before_the_lock_is_not_due(world):
    game = pilot.read_game(world.session, world.game.id)
    early = seal.seal_game(world.session, game, inputs=world.inputs(), directory=world.packets,
                           clock=lambda: LOCK - timedelta(seconds=1))
    assert early.code == "NOT_YET_LOCKED" and not world.packets.exists()
    on_time = seal.seal_game(world.session, game, inputs=world.inputs(), directory=world.packets,
                             clock=lambda: DEADLINE)
    assert on_time.sealed


def test_a_deadline_passing_during_the_build_is_refused(world):
    game = pilot.read_game(world.session, world.game.id)
    ticks = iter([DEADLINE - timedelta(seconds=1), DEADLINE + timedelta(seconds=1)])
    last = [DEADLINE + timedelta(seconds=1)]

    def clock():
        try:
            return next(ticks)
        except StopIteration:
            return last[0]
    outcome = seal.seal_game(world.session, game, inputs=world.inputs(), directory=world.packets, clock=clock)
    assert outcome.code == "SEAL_DEADLINE_PASSED_DURING_BUILD"
    assert not list(world.packets.glob("MLB-*.json"))


# ---------------------------------------------------------------------------
# The job: inert when off, seals once when on, on its own lane
# ---------------------------------------------------------------------------
def test_the_tick_is_inert_when_disabled(world, settings):
    off = settings.model_copy(update={"v18_market_pilot_export_enabled": False,
                                      "v18_market_pilot_packet_dir": str(world.packets)})
    result = seal.run_seal_tick(world.session, off, now=LOCK + timedelta(minutes=1))
    assert result["enabled"] is False and result["records"] == 0
    assert not world.packets.exists()
    assert seal.work_due(world.session, off, now=LOCK + timedelta(minutes=1)) is False


def test_the_tick_records_the_universe_then_seals_once(world, settings):
    on = settings.model_copy(update={
        "v18_market_pilot_export_enabled": True, "v18_market_pilot_packet_dir": str(world.packets),
        "v18_market_pilot_protocol_dir": "", "v18_market_pilot_quarantine_ledger_path": str(world.ledger_path),
        "raw_data_root": world.raw})
    early = LOCK - timedelta(hours=3)
    assert seal.work_due(world.session, on, now=early) is True
    first = seal.run_seal_tick(world.session, on, now=early, clock=lambda: early)
    assert len(first["universeRecorded"]) == 1 and first["sealed"] == [] and first["pending"] == 1
    assert seal.work_due(world.session, on, now=early) is False
    at = LOCK + timedelta(minutes=1)
    second = seal.run_seal_tick(world.session, on, now=at, clock=lambda: at)
    assert len(second["sealed"]) == 1 and second["records"] == 1
    third = seal.run_seal_tick(world.session, on, now=at + timedelta(minutes=2),
                               clock=lambda: at + timedelta(minutes=2))
    assert third["sealed"] == [] and third["universeRecorded"] == []
    kinds = [e["kind"] for e in seal.AttemptLedger(world.packets).entries()]
    assert kinds == ["UNIVERSE_FIRST_SEEN", "SEAL_ATTEMPT", "SEALED"]
    assert not world.session.new and not world.session.dirty


def test_a_game_missed_past_its_deadline_is_recorded_never_sealed(world, settings):
    on = settings.model_copy(update={
        "v18_market_pilot_export_enabled": True, "v18_market_pilot_packet_dir": str(world.packets),
        "v18_market_pilot_protocol_dir": "", "v18_market_pilot_quarantine_ledger_path": str(world.ledger_path),
        "raw_data_root": world.raw})
    late = DEADLINE + timedelta(minutes=2)
    result = seal.run_seal_tick(world.session, on, now=late, clock=lambda: late)
    assert result["missed"] and not result["sealed"]
    assert not list(world.packets.glob("MLB-*.json"))


def test_research_export_jobs_run_on_their_own_lane(settings):
    assert lanes.lane_for("asp-rx-v18-market-pilot-seal") == lanes.RESEARCH_EXPORT_LANE
    assert lanes.lane_for("asp-pm-discover") == lanes.PREDICTION_MARKET_LANE
    # The MLB freeze is a decision checkpoint (2026-09-20), so it is no longer
    # on `core` -- but it must still be off the research-export lane.
    assert lanes.lane_for("asp-dc-shadow-experiment-freeze") == lanes.DECISION_CHECKPOINT_LANE
    off = build_scheduler(settings.model_copy(update={"scheduler_core_jobs_enabled": True,
                                                      "v18_market_pilot_export_enabled": False}))
    assert off.get_job(seal.SCHEDULER_ID) is None
    on = build_scheduler(settings.model_copy(update={"scheduler_core_jobs_enabled": True,
                                                     "v18_market_pilot_export_enabled": True}))
    job = on.get_job(seal.SCHEDULER_ID)
    assert job is not None
    assert inspect.iscoroutinefunction(job.func)
    assert getattr(job.func, "__aisp_job_lane__") == lanes.RESEARCH_EXPORT_LANE
    assert getattr(job.func, "__aisp_off_event_loop__") is True
    assert job.trigger.interval == timedelta(minutes=2)
    assert job.max_instances == 1
    core_only = build_scheduler(settings.model_copy(update={"scheduler_core_jobs_enabled": False,
                                                            "v18_market_pilot_export_enabled": True}))
    assert core_only.get_job(seal.SCHEDULER_ID) is None


def test_the_job_is_a_registered_orchestrator_handler():
    from app.jobs.orchestrator import AVAILABLE_JOBS, JobOrchestrator

    assert seal.JOB_NAME in AVAILABLE_JOBS
    assert inspect.iscoroutinefunction(JobOrchestrator.seal_v18_market_pilot_packets)

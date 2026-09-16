"""AISP consumer of the Model Studio market-probability contract v1.

* The vendored module is byte-identical to the producer's and reproduces the
  published golden vectors exactly.
* The adapter binds one exact forecast revision per game, re-verifies the
  retained paired population (hashes, draw count, stored winner mass) and fails
  closed on any disagreement.
* Point-only forecasts never answer spread, total or parlay questions.
* Same-game parlays are per-draw intersections; cross-game requests are refused
  with CROSS_GAME_CALIBRATION_NOT_VALIDATED on every route.
* No parlay edge exists without a recorded sportsbook parlay price, and nothing
  here writes a decision, position or ledger row.

No test makes a network request or reads the Model Studio checkout.
"""

from __future__ import annotations

import hashlib
import json
import struct
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from app.models import (
    ExternalIdMapping, Game, GameStatus, GameTeam, League, NbaJointComparisonArm,
    NbaResearchForecast, OfficialAspPosition, ParlayTicket, PositionSettlement, Season,
    ShadowBoardDecision, ShadowExperimentDecision, ShadowForecast, ShadowForecastBundle, Sport, Team,
    VirtualPosition,
)
from app.probability import adapter, identity
from app.probability.assessments import _quote_assessment, daily_assessments
from app.probability.evaluate import parlay_assessment, run
from app.probability.market_probabilities import (
    JointScorePopulation, MarketProbabilityError, evaluate_cross_game, evaluate_market,
    evaluate_same_game_joint,
)

FIXTURES = Path(__file__).parent / "fixtures" / "market_probability_contract_v1"
MODULE = Path(adapter.__file__).with_name("market_probabilities.py")
KICKOFF = datetime(2026, 10, 4, 17, 0, tzinfo=UTC)

# The producer's synthetic golden population (with two tied cells).
GOLDEN = [(0, 0), (1, 0), (0, 1), (2, 1), (1, 2), (3, 0), (0, 3), (2, 2)]
# A tie-free population for sports whose admitted finals carry no tie mass.
TIE_FREE = [(1, 0), (0, 1), (2, 1), (1, 2), (3, 0), (0, 3), (3, 2), (2, 4)]


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    # Only the in-process TestClient ("testserver") may send a request.
    for cls in (httpx.Client, httpx.AsyncClient):
        original = cls.send

        def guarded(self, request, *args, _original=original, **kwargs):
            if request.url.host != "testserver":
                raise AssertionError("a network request was attempted")
            return _original(self, request, *args, **kwargs)
        monkeypatch.setattr(cls, "send", guarded)
    adapter.clear_cache()
    yield
    adapter.clear_cache()


@pytest.fixture
def exchange(tmp_path) -> Path:
    root = tmp_path / "exchange"
    root.mkdir()
    return root


@pytest.fixture
def settings(settings, exchange):
    return settings.model_copy(update={"probability_exchange_root": str(exchange)})


# ----------------------------------------------------------------- world --

def _game(session, code: str, *, home="Home", away="Away", starts_at=KICKOFF, mode="RESEARCH_SHADOW") -> Game:
    league = session.scalar(select(League).where(League.code == code))
    if league is None:
        sport = Sport(code=f"S_{code}", name=code)
        session.add(sport)
        session.flush()
        league = League(sport_id=sport.id, code=code, name=code)
        session.add(league)
        session.flush()
    season = Season(league_id=league.id, name=f"{code}-{starts_at.isoformat()}",
                    starts_at=starts_at - timedelta(days=60), ends_at=starts_at + timedelta(days=60))
    session.add(season)
    session.flush()
    game = Game(league_id=league.id, season_id=season.id, starts_at=starts_at,
                status=GameStatus.SCHEDULED, source_mode=mode)
    session.add(game)
    session.flush()
    for name, is_home in ((home, True), (away, False)):
        team = Team(league_id=league.id, name=f"{name} {game.id.hex[:4]}", abbreviation=name[:3].upper())
        session.add(team)
        session.flush()
        session.add(GameTeam(game_id=game.id, team_id=team.id, is_home=is_home))
    session.flush()
    return game


def _mass(cells, n):
    home = sum(1 for a, h in cells if h > a) / len(cells)
    away = sum(1 for a, h in cells if a > h) / len(cells)
    return home, away, 1 - home - away


def _bundle(session, sha="5" * 64) -> ShadowForecastBundle:
    bundle = ShadowForecastBundle(
        bundle_sha256=sha, bundle_id=f"b-{sha[:6]}", logical_uri="test://bundle",
        source_fixture_id="fixture", slate_date=KICKOFF.date(), league="NFL", sport="FOOTBALL",
        status="RESEARCH_SHADOW", generated_at=KICKOFF - timedelta(days=2),
        forecast_count=1, quote_count=0, candidate_count=0,
    )
    session.add(bundle)
    session.flush()
    return bundle


def _shadow(session, game, bundle, *, engine, engine_version, artifact, forecast_id, home, away,
            distributions, lineage, uncertainty, created=None) -> ShadowForecast:
    row = ShadowForecast(
        bundle_id=bundle.id, forecast_id=forecast_id, engine_id=engine, engine_version=engine_version,
        model_artifact_sha256=artifact, simulator_id="sim", simulator_version="1", simulator_seed=1,
        event_id=forecast_id[:60], source_event_id="evt", event_date=KICKOFF.date(), season=2026,
        canonical_game_id=game.id, home_team_id="HOM", away_team_id="AWA", snapshot_type="CURRENT",
        snapshot_version=1, is_ready=True, readiness_score=1.0, readiness_threshold=0.5,
        uncertainty=uncertainty, readiness_detail={}, status="RESEARCH_SHADOW",
        scheduled_start_utc=game.starts_at, source_cutoff_utc=KICKOFF - timedelta(days=1),
        full_home_win_probability=home, full_away_win_probability=away,
        full_expected_home_score=21.0, full_expected_away_score=20.0,
        distributions=distributions, lineage_detail=lineage,
    )
    session.add(row)
    session.flush()
    if created is not None:
        row.created_at = created
        session.flush()
    return row


def nfl_game(session, *, cells=GOLDEN, draws=10000, stored=None, tie=None, artifact=None, version=None):
    game = _game(session, "NFL", home="KC", away="IND")
    per = draws // len(cells)
    home, away, tie_mass = stored or _mass(cells, draws)
    joint = [{"home_score": h, "away_score": a, "probability": per / draws} for a, h in cells]
    row = _shadow(
        session, game, _bundle(session), engine="NFL_M1_XGBOOST",
        engine_version=version or adapter.ADMITTED["NFL"]["configuration_id"],
        artifact=artifact or adapter.ADMITTED["NFL"]["model_artifact_sha256"],
        forecast_id=f"asp-sports-game-forecast:sha256:{'6' * 64}", home=home, away=away,
        distributions={"tie_probability": tie_mass if tie is None else tie, "joint_score_distribution": joint},
        lineage={"simulation_count": draws, "completed_simulations": draws}, uncertainty={},
    )
    return game, row


def mlb_game(session, exchange: Path, *, cells=TIE_FREE, tamper=False, declared=5000):
    from app.shadow_import.mlb_v18_daily import _canonical_bytes

    game = _game(session, "MLB", home="NYY", away="BOS", mode="LIVE")
    per = 5000 // len(cells)
    pairs = [pair for pair in cells for _ in range(per)]
    execution = json.dumps({"population": {
        "awayRuns": [a for a, _ in pairs], "homeRuns": [h for _, h in pairs], "simulationCount": declared,
    }}).encode()
    entry = {"relativePath": "execution-inputs.json", "bytes": len(execution), "sha256": _sha(execution)}
    body = {"schemaVersion": "MLB_V18_DAILY_GAME_OBJECT_MANIFEST_V1", "gameId": str(game.id),
            "artifacts": [entry]}
    object_sha = _sha(_canonical_bytes(body))
    root = exchange / "mlb-v18-current" / "sha256" / ("h" * 64) / "objects" / "sha256" / object_sha
    root.mkdir(parents=True)
    (root / "manifest.json").write_bytes(json.dumps({**body, "objectSha256": object_sha}).encode())
    (root / "execution-inputs.json").write_bytes(execution + (b" " if tamper else b""))
    home, away, _ = _mass(cells, 5000)
    admitted = adapter.ADMITTED["MLB"]
    row = _shadow(
        session, game, _bundle(session, sha="7" * 64), engine=admitted["engine_id"],
        engine_version="mlb-v18-whole-game-test", artifact=admitted["model_artifact_sha256"],
        forecast_id=f"mlb-v18-daily-{object_sha}", home=home, away=away,
        distributions={}, uncertainty={"simulationCount": 5000},
        lineage={"objectSha256": object_sha, "parentBundleSha256": admitted["parent_bundle_sha256"],
                 "sourceArtifact": str(root)},
    )
    return game, row


def nba_game(session, exchange: Path, *, cells=TIE_FREE, logical=None, with_point=True):
    import polars as pl

    game = _game(session, "NBA", home="DET", away="BOS")
    package = "a" * 64
    root = exchange / "nba-gen1-research-v1" / "joint-comparison-v1" / "packages" / "sha256" / package
    (root / "populations").mkdir(parents=True)
    per = 10000 // len(cells)
    pairs = [pair for pair in cells for _ in range(per)]
    frame = pl.DataFrame({"drawIndex": list(range(len(pairs))),
                          "homeDET": [h for _, h in pairs], "awayBOS": [a for a, _ in pairs]})
    path = root / "populations" / "baseline-joint-draws.parquet"
    frame.write_parquet(path)
    raw = path.read_bytes()
    interleaved = [v for a, h in pairs for v in (h, a)]
    digest = _sha(struct.pack(f"<{len(interleaved)}q", *interleaved))
    home, away, _ = _mass(cells, 10000)
    session.add(NbaJointComparisonArm(
        package_sha256=package, forecast_id="nba-joint:test", forecast_sha256="b" * 64, arm="baseline",
        label="Baseline", configuration_identity=adapter.ADMITTED["NBA"]["configuration_id"],
        schema_version="TEST", canonical_game_id=game.id, supplements_forecast_id="nba-point:test",
        supplements_forecast_sha256="c" * 64, draw_count=10000, home_win_probability=home,
        away_win_probability=away, tie_probability=0.0, expected_home=110.0, expected_away=108.0,
        joint_simulation={}, authority={"researchOnly": True},
        population={"artifact": {"relativePath": "populations/baseline-joint-draws.parquet",
                                 "bytes": len(raw), "sha256": _sha(raw)},
                    "drawCount": 10000, "logicalScorePairsInt64LeSha256": logical or digest},
        imported_at=KICKOFF - timedelta(days=20),
    ))
    if with_point:
        session.add(NbaResearchForecast(
            forecast_id="nba-point:test", forecast_sha256="d" * 64, forecast_bytes=1,
            forecast_schema="TEST", package_index_sha256="e" * 64, package_index_bytes=1,
            envelope_schema="TEST", engine_id="NBA_GEN1_POINT_ELO", canonical_game_id=game.id,
            source_game_id="401909088", captured_tipoff_utc=game.starts_at,
            captured_game_date=game.starts_at.date(), elo_win_probability_home=0.58,
            elo_win_probability_away=0.42, elo_tie_probability=0.0, draw_count=0,
            imported_at=KICKOFF - timedelta(days=20),
        ))
    session.flush()
    return game


def nba_point_only_game(session):
    game = _game(session, "NBA", home="DET", away="BOS")
    session.add(NbaResearchForecast(
        forecast_id="nba-point:only", forecast_sha256="d" * 64, forecast_bytes=1,
        forecast_schema="TEST", package_index_sha256="e" * 64, package_index_bytes=1,
        envelope_schema="TEST", engine_id="NBA_GEN1_POINT_ELO", canonical_game_id=game.id,
        source_game_id="401909089", captured_tipoff_utc=game.starts_at,
        captured_game_date=game.starts_at.date(), elo_win_probability_home=0.58,
        elo_win_probability_away=0.42, draw_count=0, imported_at=KICKOFF,
    ))
    session.flush()
    return game


def cfb_game(session, exchange: Path, *, cells=TIE_FREE):
    import polars as pl

    from app.cfb.game_binding import admit_game
    from tests.test_cfb_game_binding import _forecast

    index = b'{"schema":"TEST"}'
    package = _sha(index)
    root = exchange / "cfb-gen1-research-v1" / "current-v1" / "packages" / "sha256" / package
    root.mkdir(parents=True)
    (root / "index.json").write_bytes(index)
    per = 10000 // len(cells)
    pairs = [pair for pair in cells for _ in range(per)]
    pl.DataFrame({"draw": list(range(len(pairs))), "home_score": [h for _, h in pairs],
                  "away_score": [a for a, _ in pairs]}).write_parquet(root / "baseline-joint-draws.parquet")
    home, away, _ = _mass(cells, 10000)
    row = _forecast(session, kickoff=KICKOFF, joint=home)
    row.joint_away_win_probability = away
    row.package_index_sha256 = package
    row.joint_draws_sha256 = _sha((root / "baseline-joint-draws.parquet").read_bytes())
    session.flush()
    result = admit_game(session, row)
    return session.get(Game, result["game_id"] if not isinstance(result["game_id"], str)
                       else __import__("uuid").UUID(result["game_id"]))


def _golden_population(**overrides) -> JointScorePopulation:
    fixture = json.loads((FIXTURES / "golden-vectors.json").read_bytes())
    return JointScorePopulation.from_cells(**{**fixture["population"], **overrides})


# ------------------------------------------------------ contract identity --

def test_vendored_contract_is_byte_identical_to_the_producer_commit():
    assert _sha(MODULE.read_bytes()) == identity.PRODUCER_MODULE_SHA256
    for name, expected in (("golden-vectors.json", identity.GOLDEN_VECTORS_SHA256),
                           ("request.schema.json", identity.REQUEST_SCHEMA_SHA256),
                           ("response.schema.json", identity.RESPONSE_SCHEMA_SHA256),
                           ("capability-matrix.json", identity.CAPABILITY_MATRIX_SHA256)):
        assert _sha((FIXTURES / name).read_bytes()) == expected, name


def test_golden_vectors_reproduce_exactly():
    fixture = json.loads((FIXTURES / "golden-vectors.json").read_bytes())
    population = _golden_population()
    assert population.population_sha256 == fixture["expectedPopulationSha256"]
    for vector in fixture["vectors"]:
        result = (evaluate_market(population, vector["leg"]) if vector["kind"] == "single"
                  else evaluate_same_game_joint(population, vector["legs"]))
        assert result["counts"] == vector["expectedCounts"], vector["name"]
        assert sum(result["probabilities"].values()) == pytest.approx(1.0, abs=1e-15)


def test_admitted_identities_match_the_capability_matrix():
    matrix = {(row["sport"], row["modelId"]): row
              for row in json.loads((FIXTURES / "capability-matrix.json").read_bytes())["sports"]}
    for sport in ("MLB", "NFL", "NBA", "CFB"):
        admitted = adapter.ADMITTED[sport]
        row = matrix[(sport, admitted["model_id"])]
        assert row["configurationId"] == admitted["configuration_id"]
        assert row["drawCount"] == admitted["draw_count"]
    assert matrix[("NFL", "NFL_M1_XGBOOST")]["modelArtifactSha256"] == adapter.ADMITTED["NFL"]["model_artifact_sha256"]
    assert matrix[("NBA", "NBA_GEN1_REDUCED_POINT_WITH_SEPARATE_ELO")]["drawCount"] == 0


def test_request_validation_mirrors_the_published_schema():
    from app.probability.evaluate import MoneylineLeg, PopulationBinding, SpreadLeg, TotalLeg

    schema = json.loads((FIXTURES / "request.schema.json").read_bytes())
    binding = schema["properties"]["populationBinding"]
    assert set(binding["required"]) == set(PopulationBinding.model_fields)
    legs = {item["properties"]["market"]["const"]: item for item in schema["properties"]["legs"]["items"]["oneOf"]}
    assert set(legs["MONEYLINE"]["properties"]) == set(MoneylineLeg.model_fields)
    assert set(legs["SPREAD"]["properties"]) == set(SpreadLeg.model_fields)
    assert set(legs["TOTAL"]["properties"]) == set(TotalLeg.model_fields)
    assert schema["properties"]["crossGameMode"]["enum"] == ["REJECT", "INDEPENDENCE_ASSUMPTION"]


# ---------------------------------------------------------- settlement --

def test_integer_total_pushes_and_half_points_do_not():
    population = _golden_population()
    assert evaluate_market(population, {"market": "TOTAL", "selection": "OVER", "line": 3})["counts"]["push"] == 4
    assert evaluate_market(population, {"market": "TOTAL", "selection": "UNDER", "line": 2.5})["counts"]["push"] == 0
    spread = evaluate_market(population, {"market": "SPREAD", "selection": "HOME", "line": -1})
    assert spread["counts"] == {"win": 1, "loss": 5, "push": 2, "void": 0}


def test_nfl_tie_needs_an_explicit_rule_and_the_rule_moves_the_mass():
    population = _golden_population()
    with pytest.raises(MarketProbabilityError, match="TIE_SETTLEMENT_REQUIRED"):
        evaluate_market(population, {"market": "MONEYLINE", "selection": "HOME"})
    push = evaluate_market(population, {"market": "MONEYLINE", "selection": "HOME", "tieSettlement": "PUSH"})
    loss = evaluate_market(population, {"market": "MONEYLINE", "selection": "HOME", "tieSettlement": "LOSS"})
    void = evaluate_market(population, {"market": "MONEYLINE", "selection": "HOME", "tieSettlement": "VOID"})
    assert push["counts"] == {"win": 3, "loss": 3, "push": 2, "void": 0}
    assert loss["counts"] == {"win": 3, "loss": 5, "push": 0, "void": 0}
    assert void["counts"] == {"win": 3, "loss": 3, "push": 0, "void": 2}
    assert adapter.MONEYLINE_TIE_SETTLEMENT == "PUSH"


def test_same_game_intersection_is_not_the_product_of_marginals():
    population = _golden_population()
    legs = [{"market": "MONEYLINE", "selection": "HOME", "tieSettlement": "PUSH"},
            {"market": "TOTAL", "selection": "OVER", "line": 2.5}]
    joint = evaluate_same_game_joint(population, legs)
    marginals = [evaluate_market(population, leg)["probabilities"]["win"] for leg in legs]
    assert joint["probabilities"]["win"] == 2 / 8
    assert marginals[0] * marginals[1] == 15 / 64
    assert joint["probabilities"]["win"] != marginals[0] * marginals[1]
    assert joint["method"] == "PER_DRAW_SAME_GAME_INTERSECTION"


def test_point_only_populations_are_refused():
    with pytest.raises(MarketProbabilityError, match="PAIRED_POPULATION_UNAVAILABLE"):
        _golden_population(draw_count=0, cells=[])


# --------------------------------------------------------------- adapter --

def test_nfl_binding_verifies_and_reconciles(session, settings):
    game, row = nfl_game(session)
    channel = adapter.bind(session, settings, game.id)
    binding = channel.binding
    assert binding["drawCount"] == 10000 and binding["forecastId"] == row.forecast_id
    assert binding["packageSha256"] == "5" * 64
    assert binding["modelId"] == "NFL_M1_XGBOOST"
    assert channel.authority == adapter.PAPER_RESEARCH
    tie = evaluate_market(channel.population, {"market": "MONEYLINE", "selection": "TIE"})
    assert tie["probabilities"]["win"] == pytest.approx(0.25)
    assert not session.new and not session.dirty


@pytest.mark.parametrize("change, code", [
    ({"stored": (0.40, 0.35, 0.25)}, "POPULATION_RECONCILIATION_FAILED"),
    ({"artifact": "9" * 64}, "IDENTITY_MISMATCH"),
    ({"version": "NFL_M1_OTHER"}, "IDENTITY_MISMATCH"),
    ({"draws": 8000}, "DRAW_COUNT_MISMATCH"),
])
def test_nfl_identity_count_or_mass_disagreement_fails_closed(session, settings, change, code):
    game, _ = nfl_game(session, **change)
    with pytest.raises(adapter.ProbabilityUnavailable) as caught:
        adapter.bind(session, settings, game.id)
    assert caught.value.code == code


def test_mlb_reads_and_verifies_the_retained_arrays(session, settings, exchange):
    game, row = mlb_game(session, exchange)
    channel = adapter.bind(session, settings, game.id)
    assert channel.binding["drawCount"] == 5000
    assert channel.binding["packageSha256"] == row.forecast_id.removeprefix("mlb-v18-daily-")
    assert channel.authority == adapter.CONTROLLING
    run_line = evaluate_market(channel.population, {"market": "SPREAD", "selection": "HOME", "line": -1.5})
    assert run_line["counts"]["push"] == 0


def test_mlb_tampered_or_miscounted_population_fails_closed(session, settings, exchange):
    game, _ = mlb_game(session, exchange, tamper=True)
    with pytest.raises(adapter.ProbabilityUnavailable) as caught:
        adapter.bind(session, settings, game.id)
    assert caught.value.code == "PACKAGE_HASH_MISMATCH"


def test_mlb_unreadable_source_is_unavailable_not_approximated(session, settings, exchange):
    game, row = mlb_game(session, exchange)
    for child in Path(row.lineage_detail["sourceArtifact"]).iterdir():
        child.unlink()
    with pytest.raises(adapter.ProbabilityUnavailable) as caught:
        adapter.bind(session, settings, game.id)
    assert caught.value.code == "POPULATION_SOURCE_UNREADABLE"


def test_nba_joint_population_is_research_only_and_elo_is_never_substituted(session, settings, exchange):
    game = nba_game(session, exchange)
    described = adapter.describe(session, settings, game.id)
    channels = {c["channel"]: c for c in described["channels"]}
    assert channels["PAIRED_POPULATION"]["status"] == "SUPPORTED"
    assert channels["PAIRED_POPULATION"]["authority"] == adapter.RESEARCH_ONLY
    assert channels["PAIRED_POPULATION"]["binding"]["forecastId"] == "nba-joint:test"
    point = channels["POINT_ELO"]
    assert point["status"] == "WINNER_ONLY" and point["draw_count"] == 0
    assert point["capabilities"] == {"moneyline": True, "spread": False, "total": False, "same_game": False}
    # The joint winner channel is the population's, not Elo's 58%.
    assert channels["PAIRED_POPULATION"]["winner"]["home"] == pytest.approx(0.5)


def test_nba_logical_digest_mismatch_fails_closed(session, settings, exchange):
    game = nba_game(session, exchange, logical="0" * 64)
    with pytest.raises(adapter.ProbabilityUnavailable) as caught:
        adapter.bind(session, settings, game.id)
    assert caught.value.code == "POPULATION_HASH_MISMATCH"


def test_nba_point_only_game_supports_no_line_or_parlay(session, settings, client):
    game = nba_point_only_game(session)
    described = client.get(f"/api/v1/probability/games/{game.id}").json()
    paired = next(c for c in described["channels"] if c["channel"] == "PAIRED_POPULATION")
    assert paired["status"] == "UNSUPPORTED"
    assert paired["error"]["code"] == "PAIRED_POPULATION_UNAVAILABLE"
    assert paired["capabilities"] == {"moneyline": False, "spread": False, "total": False, "same_game": False}
    assert "binding" not in paired


def test_cfb_population_binds_research_only(session, settings, exchange):
    game = cfb_game(session, exchange)
    channel = adapter.bind(session, settings, game.id)
    assert channel.authority == adapter.RESEARCH_ONLY
    assert channel.binding["modelId"] == "CFB_GEN1_RIDGE_A100"
    assert channel.binding["sport"] == "CFB"


# ------------------------------------------------------------------- API --

def _request(binding, legs, **extra):
    return {"schemaVersion": "ASP_MARKET_PROBABILITY_REQUEST_V1", "populationBinding": binding, "legs": legs, **extra}


LEGS = [{"market": "MONEYLINE", "selection": "HOME", "tieSettlement": "PUSH"},
        {"market": "TOTAL", "selection": "OVER", "line": 2.5}]


def _counts(session):
    return tuple(session.scalar(select(func.count()).select_from(model))
                 for model in (VirtualPosition, OfficialAspPosition, PositionSettlement, ParlayTicket,
                               ShadowBoardDecision, ShadowExperimentDecision))


def test_same_game_api_returns_the_intersection_and_writes_nothing(session, client):
    game, _ = nfl_game(session)
    binding = client.get(f"/api/v1/probability/games/{game.id}").json()["channels"][0]["binding"]
    before = _counts(session)
    response = client.post("/api/v1/probability/evaluate", json=_request(binding, LEGS))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["contract"]["counts"] == {"win": 2500, "loss": 6250, "push": 1250, "void": 0}
    assert body["display_method"] == identity.SAME_GAME_DISPLAY_METHOD
    assert body["contract"]["effectiveDrawCount"] == 10000
    assert body["contract"]["winProbabilityMonteCarloStandardError"] > 0
    assert body["parlay"]["quote_status"] == "SPORTSBOOK_PARLAY_PRICE_UNAVAILABLE"
    assert body["parlay"]["message"] == identity.PARLAY_PRICE_UNAVAILABLE
    assert body["parlay"]["edge"] is None and body["parlay"]["expected_return"] is None
    assert body["creates_decision"] is False and body["creates_position"] is False
    assert _counts(session) == before


def test_a_stale_or_foreign_binding_is_refused(session, client):
    game, _ = nfl_game(session)
    binding = client.get(f"/api/v1/probability/games/{game.id}").json()["channels"][0]["binding"]
    for key, value in (("populationSha256", "0" * 64), ("forecastId", "older-revision"),
                       ("packageSha256", "1" * 64), ("drawCount", 9999)):
        response = client.post("/api/v1/probability/evaluate", json=_request({**binding, key: value}, LEGS))
        assert response.status_code == 409, key
        assert response.json()["detail"]["code"] == "BINDING_MISMATCH"


def test_cross_game_requests_fail_closed_on_every_route(session, client):
    first, _ = nfl_game(session)
    binding = client.get(f"/api/v1/probability/games/{first.id}").json()["channels"][0]["binding"]
    response = client.post("/api/v1/probability/cross-game", json={"games": [str(first.id), "other"]})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == identity.CROSS_GAME_CALIBRATION_NOT_VALIDATED
    response = client.post("/api/v1/probability/evaluate",
                           json=_request(binding, LEGS, crossGameMode="INDEPENDENCE_ASSUMPTION"))
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == identity.CROSS_GAME_CALIBRATION_NOT_VALIDATED
    # A leg cannot name another game: unknown leg fields are refused.
    smuggled = [LEGS[0], {**LEGS[1], "gameId": "another-game"}]
    response = client.post("/api/v1/probability/evaluate", json=_request(binding, smuggled))
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "REQUEST_INVALID"
    described = client.get(f"/api/v1/probability/games/{first.id}").json()
    assert described["cross_game"]["code"] == identity.CROSS_GAME_CALIBRATION_NOT_VALIDATED
    assert "probabilities" not in described["cross_game"]


def test_no_app_module_reaches_the_independence_convolution():
    app_root = Path(adapter.__file__).parents[1]
    offenders = []
    for path in app_root.rglob("*.py"):
        if path == MODULE:
            continue
        text = path.read_text(encoding="utf-8")
        if "evaluate_cross_game" in text or "allow_independence_assumption" in text:
            offenders.append(str(path))
    assert offenders == []
    # The producer function itself still fails closed by default.
    single = evaluate_market(_golden_population(), LEGS[1])
    other = {**single, "population": {**single["population"], "gameId": "g2"}}
    with pytest.raises(MarketProbabilityError, match="CROSS_GAME_JOINT_UNSUPPORTED"):
        evaluate_cross_game([single, other])


def test_parlay_edge_requires_a_recorded_combined_quote(session, settings):
    game, _ = nfl_game(session)
    result = run(adapter.bind(session, settings, game.id), LEGS)["contract"]
    synthesized = {"provenance": "ESTIMATED_FROM_SAME_BOOK_LEG_ODDS", "decimal": 3.5}
    for quote in (None, synthesized, {"provenance": identity.RECORDED_COMBINED_QUOTE, "decimal": None}):
        assessed = parlay_assessment(result, quote)
        assert assessed["quote_status"] == "SPORTSBOOK_PARLAY_PRICE_UNAVAILABLE"
        assert assessed["implied_probability"] is None and assessed["edge"] is None
        assert assessed["expected_return"] is None
    recorded = parlay_assessment(result, {"provenance": identity.RECORDED_COMBINED_QUOTE, "decimal": 5.0,
                                          "sportsbook": "Book"})
    assert recorded["implied_probability"] == pytest.approx(0.2)
    assert recorded["expected_return"] == pytest.approx(0.25 * 4 - 0.625)


# ------------------------------------------------------------ assessments --

def _quote(market, sides, *, line=None, fresh=True, classification="GOVERNED"):
    return {"market": market, "line": line, "sportsbook": "DraftKings", "sportsbook_key": "draftkings",
            "provider": "The Odds API", "classification": classification,
            "observed_at": "2026-10-04T12:00:00+00:00", "age_minutes": 30.0, "fresh": fresh, "sides": sides}


def test_stale_quotes_are_labelled_and_carry_no_edge(session, settings):
    game, _ = nfl_game(session)
    channel = adapter.bind(session, settings, game.id)
    sides = {"HOME": {"american": -110, "decimal": 1.909}, "AWAY": {"american": -110, "decimal": 1.909}}
    stale = _quote_assessment(channel, _quote("MONEYLINE", sides, fresh=False))
    assert stale["state"] == "STORED_REFERENCE_ONLY"
    assert all(s["edge"] is None and s["edge_reason"] == "QUOTE_NOT_CURRENT" for s in stale["sides"].values())
    assert stale["sides"]["HOME"]["model"]["push"] == pytest.approx(0.25)
    # A research-classified book from a provider the policy permits is assessed
    # (the governed decisions price there too); an unpermitted provider is not.
    research = _quote_assessment(channel, {**_quote("MONEYLINE", sides, classification="RESEARCH"),
                                           "provider": "SportsGameOdds"})
    assert research["sides"]["HOME"]["edge"] is not None
    foreign = _quote_assessment(channel, {**_quote("MONEYLINE", sides), "provider": "Unlisted Feed"})
    assert foreign["sides"]["HOME"]["edge"] is None
    assert foreign["sides"]["HOME"]["edge_reason"] == "PROVIDER_NOT_PERMITTED"
    current = _quote_assessment(channel, _quote("MONEYLINE", sides))
    assert current["state"] == "CURRENT"
    assert current["sides"]["HOME"]["edge"] == pytest.approx(0.5 - 0.5)
    assert current["sides"]["HOME"]["expected_return"] == pytest.approx(0.375 * 0.909 - 0.375)


def test_missing_sides_and_lines_are_never_inferred(session, settings):
    game, _ = nfl_game(session)
    channel = adapter.bind(session, settings, game.id)
    one_side = _quote_assessment(channel, _quote("TOTAL", {"OVER": {"american": -105, "decimal": 1.952}}, line=2.5))
    assert list(one_side["sides"]) == ["OVER"]
    assert one_side["sides"]["OVER"]["no_vig"] is None
    assert one_side["sides"]["OVER"]["edge_reason"] == "NO_VIG_UNAVAILABLE"
    no_line = _quote_assessment(channel, _quote("SPREAD", {"HOME": {"american": -110, "decimal": 1.909},
                                                          "AWAY": {"american": -110, "decimal": 1.909}}))
    assert all(s["model"] is None and s["edge_reason"] == "LINE_MISSING" for s in no_line["sides"].values())
    # A spread is evaluated with the line attached to each side.
    spread = _quote_assessment(channel, _quote("SPREAD", {"HOME": {"american": -110, "decimal": 1.909},
                                                         "AWAY": {"american": -110, "decimal": 1.909}}, line=-1))
    assert spread["sides"]["HOME"]["model"]["win"] == pytest.approx(1 / 8)
    assert spread["sides"]["AWAY"]["model"]["win"] == pytest.approx(5 / 8)


@pytest.fixture
def no_priced_slate(monkeypatch):
    """market_coverage is PostgreSQL SQL; these SQLite tests use an empty priced slate."""
    monkeypatch.setattr("app.services.command_center.market_coverage",
                        lambda session, day, now=None: {"games": []})


def test_daily_assessments_list_research_games_without_prices(session, settings, exchange, no_priced_slate):
    nba_game(session, exchange)
    cfb = cfb_game(session, exchange)
    payload = daily_assessments(session, settings, (KICKOFF - timedelta(days=1)).date(),
                                now=KICKOFF - timedelta(days=1))
    research = {row["sport"]: row for row in payload["research"]}
    assert set(research) == {"NBA", "CFB"}
    assert research["CFB"]["game_id"] == str(cfb.id)
    assert all(row["price_state"] == "NO_GOVERNED_PRICE" for row in research.values())
    assert payload["cross_game"]["code"] == identity.CROSS_GAME_CALIBRATION_NOT_VALIDATED
    assert payload["parlay_policy"]["sportsbook_parlay_quotes_stored"] == 0
    assert payload["provider_calls_made"] == 0


def test_protected_nba_and_cfb_rows_are_not_touched(session, settings, exchange, no_priced_slate):
    nba = nba_game(session, exchange)
    cfb = cfb_game(session, exchange)
    session.commit()
    snapshot = lambda: (  # noqa: E731
        [(g.id, g.source_mode, g.starts_at, g.status) for g in session.scalars(select(Game).order_by(Game.id))],
        session.scalar(select(func.count()).select_from(ExternalIdMapping)),
        [(a.forecast_id, a.home_win_probability) for a in session.scalars(select(NbaJointComparisonArm))],
    )
    before = snapshot()
    for game in (nba, cfb):
        adapter.describe(session, settings, game.id)
    daily_assessments(session, settings, KICKOFF.date(), now=KICKOFF - timedelta(days=1))
    assert not session.new and not session.dirty and not session.deleted
    assert snapshot() == before


def test_parlay_lab_examples_use_only_quoted_lines(session, settings, monkeypatch):
    game, _ = nfl_game(session)
    ml = _quote("MONEYLINE", {"HOME": {"american": -110, "decimal": 1.909}, "AWAY": {"american": -110, "decimal": 1.909}})
    total = _quote("TOTAL", {"OVER": {"american": -110, "decimal": 1.909}, "UNDER": {"american": -110, "decimal": 1.909}},
                   line=2.5, fresh=False)
    coverage = {"games": [{"game_id": str(game.id), "league": "NFL", "quotes": [ml, total]}]}
    monkeypatch.setattr("app.services.command_center.market_coverage", lambda s, d, now=None: coverage)
    payload = daily_assessments(session, settings, KICKOFF.date(), now=KICKOFF - timedelta(hours=5))
    [row] = payload["games"]
    assert row["binding"]["status"] == "SUPPORTED"
    # No spread was quoted, so only the moneyline + total example exists.
    [example] = row["parlay_lab"]
    assert [leg["market"] for leg in example["legs"]] == ["MONEYLINE", "TOTAL"]
    assert example["legs"][1]["line"] == 2.5
    assert example["display_method"] == "PAIRED_DRAW_INTERSECTION"
    assert example["effective_draw_count"] == 10000
    assert example["parlay"]["edge"] is None and example["parlay"]["implied_probability"] is None
    assert {q["state"] for q in row["quotes"]} == {"CURRENT", "STORED_REFERENCE_ONLY"}

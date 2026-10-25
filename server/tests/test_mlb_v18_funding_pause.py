"""MLB_V18_FUNDING_PAUSE_V1.

The accepted design's test plan (1-26) with Model Studio's bound-authority
review applied: a V18 identity with ANY mismatch creates no position and raises
an alert (it no longer falls back to funding), and only an explicitly non-V18
experiment passes through to the ordinary funded path.

Everything here runs on the isolated in-memory SQLite fixture. No provider, no
production database, no job invocation.
"""
from __future__ import annotations

import ast
import inspect
import json
import textwrap
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import event, func, inspect as sa_inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.immutability import ImmutableRecordError
from app.core.config import Settings
from app.models import (
    AuditEvent,
    ExperimentDecisionPhase,
    Forecast,
    Game,
    GameStatus,
    GameTeam,
    League,
    Market,
    MarketOutcome,
    MarketType,
    MlbV18FundingPause,
    OddsSnapshot,
    OfficialAspPosition,
    PositionSettlement,
    PositionStatus,
    Season,
    ShadowExperiment,
    ShadowExperimentDecision,
    ShadowForecast,
    ShadowForecastBundle,
    Sport,
    Sportsbook,
    Team,
)
from app.services import mlb_v18_funding_pause as fp
from app.services.practical_policy import frozen_policy_config
from app.services.shadow_experiment_service import (
    FINAL_PREGAME_CUTOFF_MINUTES,
    ShadowExperimentService,
)

START = datetime(2026, 9, 26, 17, 5, tzinfo=UTC)
CUTOFF = START - timedelta(minutes=FINAL_PREGAME_CUTOFF_MINUTES)
DECIDED_AT = CUTOFF - timedelta(minutes=10)
POSITION_NOW = CUTOFF + timedelta(minutes=5)
SLATE = date(2026, 9, 26)
V18_ID = uuid.UUID(fp.AUTHORITY["experimentId"])
AUTHORITY_FILE = Path.home() / "asp-model-studio" / "outputs" / "production-followups-20260924-v1" / \
    "MLB_V18_FUNDING_PAUSE_V1_AUTHORITY.json"


# -- world ---------------------------------------------------------------------

def _league(session: Session, code: str) -> League:
    league = session.scalar(select(League).where(League.code == code))
    if league is None:
        sport = Sport(code=f"S_{code}", name=code)
        session.add(sport)
        session.flush()
        league = League(sport_id=sport.id, code=code, name=code)
        session.add(league)
        session.flush()
    return league


def _game(session: Session, code: str = "MLB", *, starts_at: datetime = START) -> Game:
    league = _league(session, code)
    season = Season(league_id=league.id, name=f"{code}-{uuid.uuid4().hex[:8]}",
                    starts_at=starts_at - timedelta(days=90), ends_at=starts_at + timedelta(days=30))
    session.add(season)
    session.flush()
    game = Game(league_id=league.id, season_id=season.id, starts_at=starts_at,
                status=GameStatus.SCHEDULED, source_mode="LIVE")
    session.add(game)
    session.flush()
    for name, is_home in (("HOM", True), ("AWA", False)):
        team = Team(league_id=league.id, name=f"{name} {game.id.hex[:6]}",
                    abbreviation=f"{name[0]}{game.id.hex[:5]}")
        session.add(team)
        session.flush()
        session.add(GameTeam(game_id=game.id, team_id=team.id, is_home=is_home))
    session.flush()
    return game


def _quote(session: Session, game: Game, *, american: int = 120) -> tuple[MarketOutcome, OddsSnapshot, Sportsbook]:
    home = session.scalar(select(GameTeam).where(GameTeam.game_id == game.id, GameTeam.is_home.is_(True)))
    market = Market(game_id=game.id, market_type=MarketType.MONEYLINE, period="full_game")
    session.add(market)
    session.flush()
    outcome = MarketOutcome(market_id=market.id, outcome_key="HOME", label="HOM",
                            participant_team_id=home.team_id)
    session.add(outcome)
    session.flush()
    book = session.scalar(select(Sportsbook).where(Sportsbook.key == "testbook"))
    if book is None:
        book = Sportsbook(key="testbook", name="Test Book")
        session.add(book)
        session.flush()
    observed = DECIDED_AT - timedelta(minutes=5)
    snapshot = OddsSnapshot(market_outcome_id=outcome.id, sportsbook_id=book.id,
                            observed_at=observed, available_as_of=observed,
                            american_odds=american, decimal_odds=1 + american / 100,
                            source="SPORTSGAMEODDS", source_mode="LIVE")
    session.add(snapshot)
    session.flush()
    return outcome, snapshot, book


def _v18_forecast(session: Session, game: Game, *, engine: str = "MLB_V18_WHOLE_GAME",
                  artifact: str | None = None, parent: str | None = None) -> ShadowForecast:
    package = uuid.uuid4().hex + uuid.uuid4().hex
    bundle = ShadowForecastBundle(
        bundle_sha256=uuid.uuid4().hex + uuid.uuid4().hex, bundle_id=f"b-{package[:8]}",
        logical_uri="test://bundle", source_fixture_id="fixture", slate_date=SLATE,
        league="MLB", sport="BASEBALL", status="RESEARCH_SHADOW",
        generated_at=DECIDED_AT - timedelta(hours=3), forecast_count=1, quote_count=0,
        candidate_count=0,
    )
    session.add(bundle)
    session.flush()
    row = ShadowForecast(
        bundle_id=bundle.id, forecast_id=f"mlb-v18-daily-{package}", engine_id=engine,
        engine_version="mlb-v18-whole-game-a6e7e99122f1b60701a4728b",
        model_artifact_sha256=artifact or fp.AUTHORITY["modelArtifactSha256"],
        simulator_id="V18_WHOLE_GAME_MONTE_CARLO", simulator_version="MLB_V18_DAILY_GAME_FORECAST_V1",
        simulator_seed=20260907, event_id=str(game.id), source_event_id="823496",
        event_date=SLATE, season=2026, canonical_game_id=game.id, home_team_id="HOM",
        away_team_id="AWA", snapshot_type="PROSPECTIVE", snapshot_version=1, is_ready=True,
        readiness_score=1.0, readiness_threshold=0.5, uncertainty={"simulationCount": 5000},
        readiness_detail={}, status="PROSPECTIVE_RESEARCH_SHADOW", scheduled_start_utc=START,
        available_as_of_utc=DECIDED_AT - timedelta(hours=2),
        source_cutoff_utc=DECIDED_AT - timedelta(hours=2, minutes=7),
        full_home_win_probability=0.6, full_away_win_probability=0.4,
        full_expected_home_score=4.6, full_expected_away_score=3.9, distributions={},
        lineage_detail={"objectSha256": package,
                        "parentBundleSha256": parent or fp.AUTHORITY["parentLogicalBundleSha256"]},
    )
    session.add(row)
    session.flush()
    return row


def _gen1_forecast(session: Session, game: Game) -> Forecast:
    # The position path reads only the forecast's clocks; the registry rows
    # exist so the foreign keys hold on any dialect.
    from app.models import FeatureVersion, ModelVersion

    model = ModelVersion(name="gen1-fixture", version=uuid.uuid4().hex[:12])
    feature = FeatureVersion(name="gen1-fixture", version=uuid.uuid4().hex[:12], schema_hash="0" * 64)
    session.add_all([model, feature])
    session.flush()
    row = Forecast(game_id=game.id, model_version_id=model.id, feature_version_id=feature.id,
                   version=1, generated_at=DECIDED_AT - timedelta(hours=2),
                   available_as_of=DECIDED_AT - timedelta(hours=2),
                   home_win_probability=0.6, away_win_probability=0.4, source_mode="LIVE")
    session.add(row)
    session.flush()
    return row


_VERSION = iter(range(100, 10_000))


def _experiment(session: Session, settings: Settings, *, kind: str = "V18", **overrides) -> ShadowExperiment:
    base = dict(
        name=f"fixture {kind}", version=next(_VERSION), started_at=START - timedelta(days=20),
        start_date=(START - timedelta(days=20)).date(), starting_bankroll=1000.0, sport="MLB",
        supported_markets=["MONEYLINE"], kelly_fraction=0.25, unit_percentage=0.005,
        max_position_percentage=0.01, max_same_game_percentage=0.02, max_daily_percentage=0.05,
        code_commit="fixture", code_identity="git:fixture", is_active=True,
    )
    if kind == "V18":
        base.update(id=V18_ID, name="ASP MLB V18 CONDITIONAL VIRTUAL - 2026 V1",
                    recommendation_policy_version="AISP_PRACTICAL_PREGAME_POLICY_V2_1",
                    staking_policy_version="AISP_QUARTER_KELLY_EVIDENCE_V2",
                    decision_policy="AISP_PRACTICAL_PREGAME_POLICY_V2_1",
                    controlling_engine="MLB_V18_WHOLE_GAME",
                    policy_config=frozen_policy_config(settings),
                    model_era_id=fp.AUTHORITY["modelEraId"])
    elif kind == "BASELINE":
        base.update(recommendation_policy_version="AISP_OPPORTUNITY_POLICY_V1",
                    staking_policy_version="AISP_QUARTER_KELLY_1_2_5_V1",
                    decision_policy="AISP_OPPORTUNITY_POLICY_V1", controlling_engine="AISP_BASELINE",
                    policy_config=None)
    elif kind == "PRACTICAL":
        base.update(recommendation_policy_version="AISP_PRACTICAL_PREGAME_POLICY_V2_1",
                    staking_policy_version="AISP_QUARTER_KELLY_EVIDENCE_V2",
                    decision_policy="AISP_PRACTICAL_PREGAME_POLICY_V2_1",
                    controlling_engine="AISP_BASELINE", policy_config=frozen_policy_config(settings))
    else:  # any other, non-V18 MLB experiment
        base.update(recommendation_policy_version="SOME_OTHER_MLB_POLICY_V1",
                    staking_policy_version="SOME_OTHER_STAKING_V1",
                    decision_policy="SOME_OTHER_MLB_POLICY_V1", controlling_engine="MLB_GEN2_CANDIDATE",
                    policy_config={"min_adjusted_edge": 0.03})
    base.update(overrides)
    row = ShadowExperiment(**base)
    session.add(row)
    session.flush()
    return row


def _decision(session: Session, experiment: ShadowExperiment, game: Game, forecast, quote, *,
              recommendation: str = "QUALIFIED_BET", **overrides) -> ShadowExperimentDecision:
    outcome, snapshot, book = quote
    shadow = isinstance(forecast, ShadowForecast)
    base = dict(
        experiment_id=experiment.id, game_id=game.id, slate_date=SLATE,
        phase=ExperimentDecisionPhase.FINAL_PREGAME, sport="BASEBALL_MLB", market="MONEYLINE",
        period="Full game", selection="HOM", sportsbook=book.name,
        american_odds=snapshot.american_odds, decimal_odds=float(snapshot.decimal_odds),
        market_outcome_id=outcome.id, odds_snapshot_id=snapshot.id,
        quote_observed_at=snapshot.observed_at,
        forecast_id=None if shadow else forecast.id,
        shadow_forecast_id=forecast.id if shadow else None,
        forecast_generated_at=(forecast.available_as_of_utc if shadow else forecast.generated_at),
        forecast_cutoff=(forecast.source_cutoff_utc if shadow else forecast.available_as_of),
        recommended_at=DECIDED_AT, model_probability=0.6, no_vig_probability=0.43,
        edge=0.6 - 1 / float(snapshot.decimal_odds), expected_value=0.32,
        recommendation=recommendation, recommendation_reason="All qualification gates passed.",
        qualification_reasons=[], evidence_fingerprint=uuid.uuid4().hex + uuid.uuid4().hex,
        source_mode="LIVE",
        evidence={"adjusted_probability": 0.58, "adjusted_edge": 0.0321,
                  "position_cap_fraction": 0.01, "evidence_multiplier": 1.0},
    )
    base.update(overrides)
    row = ShadowExperimentDecision(**base)
    session.add(row)
    session.flush()
    return row


def _v18_world(session: Session, settings: Settings, **forecast_kwargs):
    experiment = session.get(ShadowExperiment, V18_ID) or _experiment(session, settings)
    game = _game(session)
    quote = _quote(session, game)
    forecast = _v18_forecast(session, game, **forecast_kwargs)
    decision = _decision(session, experiment, game, forecast, quote)
    return experiment, game, decision


def _on(settings: Settings) -> Settings:
    return settings.model_copy(update={"mlb_v18_funding_pause_enabled": True})


def _positions(session: Session) -> list[OfficialAspPosition]:
    return list(session.scalars(select(OfficialAspPosition)))


def _pauses(session: Session) -> list[MlbV18FundingPause]:
    return list(session.scalars(select(MlbV18FundingPause)))


def _alerts(session: Session, action: str = fp.MISMATCH_ACTION) -> list[AuditEvent]:
    return list(session.scalars(select(AuditEvent).where(AuditEvent.action == action)))


def _create(session, settings, experiment, game, receipt=None) -> bool:
    return ShadowExperimentService(session, settings)._create_canonical_position(
        experiment, game, SLATE, POSITION_NOW, funding_pause=receipt)


def _dispose(session, settings, experiment, game, receipt=None) -> str:
    return ShadowExperimentService(session, settings)._canonical_position_disposition(
        experiment, game, SLATE, POSITION_NOW, funding_pause=receipt)


# == the authority, verbatim ==================================================

def test_authority_mapping_is_the_bound_model_studio_tuple():
    assert dict(fp.AUTHORITY) == {
        "sport": "MLB",
        "experimentId": "b7532da1-27b2-4d7a-b373-bbfb3166bd3a",
        "modelEraId": "ASP_MLB_V18_CONDITIONAL_VIRTUAL_20260912_V1_R1",
        "controllingEngine": "MLB_V18_WHOLE_GAME",
        "modelId": "MLB_V18_HGB_PA",
        "modelConfigurationIdentity": "UNRESTRICTED_V18_FOLD5_PA_TRANSITION_FOLD4",
        "modelArtifactSha256": "44ec4f457741f1aaeff42b81af1be5fd2712e494709ff1a0a8f0742b88d71c8b",
        "parentLogicalBundleSha256": "a6e7e99122f1b60701a4728b97b32fcc11e03405bfd456ed6cd54f69b69adb27",
        "recommendationPolicyVersion": "AISP_PRACTICAL_PREGAME_POLICY_V2_1",
        "decisionPolicyVersion": "AISP_PRACTICAL_PREGAME_POLICY_V2_1",
        "stakingPolicyVersion": "AISP_QUARTER_KELLY_EVIDENCE_V2",
        "policyConfigurationSha256": "7e439d4bed6ec67e261dfc272e3b1ced93f01f0d59a8813b867dc1b39620a8d2",
        "qualifiedEdgeThreshold": 0.02,
        "requiredPhase": "FINAL_PREGAME",
        "requiredRecommendation": "QUALIFIED_BET",
        "requiredSourceMode": "LIVE",
    }
    with pytest.raises(TypeError):
        fp.AUTHORITY["experimentId"] = "x"  # type: ignore[index]
    if AUTHORITY_FILE.exists():  # the retained Model Studio mapping, when present on this machine
        retained = json.loads(AUTHORITY_FILE.read_bytes())["authority"]
        assert retained == dict(fp.AUTHORITY)


def test_admitted_probability_binding_matches_the_pinned_model_identity():
    from app.probability.adapter import ADMITTED

    mlb = ADMITTED["MLB"]
    assert (mlb["engine_id"], mlb["model_id"], mlb["configuration_id"], mlb["model_artifact_sha256"],
            mlb["parent_bundle_sha256"]) == (
        fp.AUTHORITY["controllingEngine"], fp.AUTHORITY["modelId"],
        fp.AUTHORITY["modelConfigurationIdentity"], fp.AUTHORITY["modelArtifactSha256"],
        fp.AUTHORITY["parentLogicalBundleSha256"])


# == 1-8: authority and negative controls ======================================

def test_1_exact_v18_authority_pauses_and_creates_no_position(session, settings):
    experiment, game, decision = _v18_world(session, settings)
    receipt = fp.FundingPauseReceipt(experiment_id=str(experiment.id))

    assert _dispose(session, _on(settings), experiment, game, receipt) == fp.PAUSE_NEW
    assert _positions(session) == []
    [row] = _pauses(session)
    assert row.decision_id == decision.id
    assert receipt.paused[0]["disposition"] == fp.PAUSE_NEW
    assert _alerts(session) == []


@pytest.mark.parametrize("kind", ["BASELINE", "PRACTICAL", "OTHER"])
def test_2_3_4_non_v18_mlb_experiments_fund_normally_with_the_pause_on(session, settings, kind):
    experiment = _experiment(session, settings, kind=kind)
    game = _game(session)
    _decision(session, experiment, game, _gen1_forecast(session, game), _quote(session, game),
              evidence=None if kind == "BASELINE" else
              {"adjusted_probability": 0.58, "adjusted_edge": 0.0321,
               "position_cap_fraction": 0.01, "evidence_multiplier": 1.0})
    receipt = fp.FundingPauseReceipt(experiment_id=str(experiment.id))

    assert _create(session, _on(settings), experiment, game, receipt) is True
    [position] = _positions(session)
    assert position.shadow_experiment_id == experiment.id and float(position.stake) > 0
    assert _pauses(session) == [] and _alerts(session) == []
    assert receipt.counts["pass_through_to_funding_path"] == 1
    assert receipt.counts["positions_created"] == 1


def test_2b_a_v18_pause_does_not_defund_the_baseline_or_practical_experiment_beside_it(session, settings):
    """Enabled with every active MLB experiment present: only V18 stops."""
    v18, v18_game, _ = _v18_world(session, settings)
    baseline = _experiment(session, settings, kind="BASELINE")
    practical = _experiment(session, settings, kind="PRACTICAL")
    game, other = _game(session), _game(session)
    _decision(session, baseline, game, _gen1_forecast(session, game), _quote(session, game), evidence=None)
    _decision(session, practical, other, _gen1_forecast(session, other), _quote(session, other))
    on = _on(settings)

    assert _create(session, on, v18, v18_game) is False
    assert _create(session, on, baseline, game) is True
    assert _create(session, on, practical, other) is True
    assert sorted(str(p.shadow_experiment_id) for p in _positions(session)) == sorted(
        [str(baseline.id), str(practical.id)])
    assert len(_pauses(session)) == 1


def test_5_right_experiment_wrong_engine_is_a_mismatch_with_no_position(session, settings):
    experiment = _experiment(session, settings, controlling_engine="AISP_BASELINE")
    game = _game(session)
    decision = _decision(session, experiment, game, _v18_forecast(session, game), _quote(session, game))

    assert _dispose(session, _on(settings), experiment, game) == fp.AUTHORITY_MISMATCH
    assert _positions(session) == [] and _pauses(session) == []
    [alert] = _alerts(session)
    assert alert.entity_id == decision.id
    assert "controllingEngine" in alert.metadata_json["mismatched_dimensions"]


@pytest.mark.parametrize("override", [
    {"id": uuid.uuid4()},
    {"model_era_id": "ASP_MLB_V18_CONDITIONAL_VIRTUAL_20260912_V1_R2"},
    {"id": uuid.uuid4(), "model_era_id": None},
])
def test_6_right_engine_wrong_experiment_or_era_is_a_mismatch(session, settings, override):
    experiment = _experiment(session, settings, **override)
    game = _game(session)
    _decision(session, experiment, game, _v18_forecast(session, game), _quote(session, game))

    assert _dispose(session, _on(settings), experiment, game) == fp.AUTHORITY_MISMATCH
    assert _positions(session) == [] and _pauses(session) == []
    assert len(_alerts(session)) == 1


def _tampered_config(settings):
    config = frozen_policy_config(settings)
    config["min_adjusted_edge"] = 0.03  # checksum left as recorded: fails integrity
    return config


@pytest.mark.parametrize("override,dimension", [
    ({"recommendation_policy_version": "AISP_OPPORTUNITY_POLICY_V1"}, "recommendationPolicyVersion"),
    ({"decision_policy": "AISP_OPPORTUNITY_POLICY_V1"}, "decisionPolicyVersion"),
    ({"staking_policy_version": "AISP_QUARTER_KELLY_V1"}, "stakingPolicyVersion"),
    ({"policy_config": {"checksum": "0" * 64}}, "policyConfigurationSha256"),
    ("TAMPERED", "policyConfigurationIntegrity"),
])
def test_7_wrong_policy_identity_is_a_mismatch(session, settings, override, dimension):
    override = {"policy_config": _tampered_config(settings)} if override == "TAMPERED" else override
    experiment = _experiment(session, settings, **override)
    game = _game(session)
    _decision(session, experiment, game, _v18_forecast(session, game), _quote(session, game))

    assert _dispose(session, _on(settings), experiment, game) == fp.AUTHORITY_MISMATCH
    assert _positions(session) == []
    [alert] = _alerts(session)
    assert dimension in alert.metadata_json["mismatched_dimensions"]


@pytest.mark.parametrize("forecast_kwargs,dimension", [
    ({"artifact": "f" * 64}, "modelArtifactSha256"),
    ({"parent": "e" * 64}, "parentLogicalBundleSha256"),
    ({"engine": "MLB_V18_OTHER_ENGINE"}, "forecastEngine"),
])
def test_7b_wrong_model_identity_is_a_mismatch(session, settings, forecast_kwargs, dimension):
    experiment, game, _ = _v18_world(session, settings, **forecast_kwargs)

    assert _dispose(session, _on(settings), experiment, game) == fp.AUTHORITY_MISMATCH
    assert _positions(session) == []
    assert dimension in _alerts(session)[0].metadata_json["mismatched_dimensions"]


@pytest.mark.parametrize("dimension", list(fp.EXPECTED))
def test_7c_a_single_altered_field_never_returns_v18_to_funding(dimension):
    observed = dict(fp.EXPECTED)
    assert fp.classify(observed).kind == fp.PAUSE_FUNDING
    observed[dimension] = None if dimension != "qualifiedEdgeThreshold" else 0.021
    result = fp.classify(observed)
    assert result.kind == fp.AUTHORITY_MISMATCH_CLASS
    assert dimension in result.mismatched


@pytest.mark.parametrize("code", ["NFL", "CFB", "NBA"])
def test_8_other_sports_never_reach_the_guard(session, settings, monkeypatch, code):
    calls: list[str] = []
    monkeypatch.setattr(fp, "funding_pause_disposition", lambda *a, **k: calls.append("called"))
    experiment = session.get(ShadowExperiment, V18_ID) or _experiment(session, settings)
    game = _game(session, code)
    _decision(session, experiment, game, _v18_forecast(session, game), _quote(session, game))

    assert _dispose(session, _on(settings), experiment, game) == "INELIGIBLE"
    assert calls == [] and _positions(session) == [] and _pauses(session) == []
    nfl = _experiment(session, settings, kind="OTHER", sport=code)
    payload = ShadowExperimentService(session, _on(settings)).freeze(
        SLATE, phase=ExperimentDecisionPhase.FINAL_PREGAME, now=POSITION_NOW, experiment=nfl)
    assert payload["primary"] == 0 and "funding_pause" not in payload and calls == []


def test_8b_the_guard_sits_after_the_league_check_and_every_integrity_guard():
    source = textwrap.dedent(inspect.getsource(ShadowExperimentService._canonical_position_disposition))
    order = _call_order(source)
    guard = order.index("funding_pause_disposition")
    assert order.index("_is_mlb_game") < guard
    assert order.index("_decision_forecast") < guard
    assert order.index("same_quoted_price") < guard


# == 9-11: decision preservation ===============================================

def _decision_bytes(decision) -> str:
    def plain(value):
        if isinstance(value, datetime):  # SQLite drops the zone on a round trip
            return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC).isoformat()
        return str(value)
    columns = sa_inspect(ShadowExperimentDecision).columns.keys()
    return json.dumps({c: plain(getattr(decision, c)) for c in columns}, sort_keys=True)


@pytest.mark.parametrize("enabled", [True, False])
def test_9_the_decision_row_is_identical_with_the_pause_on_or_off(session, settings, enabled):
    experiment, game, decision = _v18_world(session, settings)
    before = _decision_bytes(decision)
    config = _on(settings) if enabled else settings
    _create(session, config, experiment, game)
    session.commit()
    session.expire_all()
    assert _decision_bytes(session.get(ShadowExperimentDecision, decision.id)) == before


def test_10_the_threshold_is_unchanged():
    from app.forecast_exchange.pricing import ShadowPricingConfig
    from app.services.practical_policy import MIN_ADJUSTED_EDGE

    assert ShadowPricingConfig().qualified_edge == 0.02
    assert MIN_ADJUSTED_EDGE == 0.02
    assert fp.AUTHORITY["qualifiedEdgeThreshold"] == 0.02


def test_10b_the_frozen_policy_configuration_still_hashes_to_the_pinned_checksum(settings):
    assert frozen_policy_config(settings)["checksum"] == fp.AUTHORITY["policyConfigurationSha256"]


def test_11_prospective_exports_do_not_depend_on_the_pause(session, settings):
    from app.research_exports import prospective_capture, v18_market_pilot

    for module in (prospective_capture, v18_market_pilot):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "funding_pause" not in source and "MlbV18FundingPause" not in source

    experiment, game, decision = _v18_world(session, settings)

    def block():
        observed = prospective_capture.Observation(
            game=None, checkpoint=POSITION_NOW, decision=decision,
            position=prospective_capture.position_for(session, decision))
        return json.dumps(prospective_capture._decision_block(observed), sort_keys=True)

    unpaused = block()
    assert _create(session, _on(settings), experiment, game) is False
    assert len(_pauses(session)) == 1
    assert block() == unpaused  # same population row, same bytes; it is simply unfunded


# == 12-13: zero position creation, zero staking ===============================

def _freeze_world(session, settings, monkeypatch):
    from app.services.read_service import ReadService

    experiment, game, decision = _v18_world(session, settings)
    monkeypatch.setattr(ReadService, "_live_games_for_date", lambda self, d, league: [game])
    monkeypatch.setattr(ReadService, "game_card", lambda self, g: object())
    monkeypatch.setattr(ReadService, "_daily_opportunities", lambda self, g, c, f: ([], None))
    monkeypatch.setattr(ShadowExperimentService, "_controlling_forecasts",
                        lambda self, e, games, now: ([None for _ in games], []))
    # Past the cutoff the freeze only binds the persisted decision; the
    # practical-policy evaluation it performs first is not under test here.
    from app.services.practical_policy import PracticalPolicyEvaluator

    monkeypatch.setattr(PracticalPolicyEvaluator, "evaluate", lambda self, *a, **k: None)
    return experiment, game, decision


def test_12_a_paused_qualifier_creates_no_position_and_no_primary_count(session, settings, monkeypatch):
    experiment, _, decision = _freeze_world(session, settings, monkeypatch)
    service = ShadowExperimentService(session, _on(settings))
    payload = service.freeze(SLATE, phase=ExperimentDecisionPhase.FINAL_PREGAME,
                             now=POSITION_NOW, experiment=experiment)

    assert payload["primary"] == 0
    assert _positions(session) == []
    block = payload["funding_pause"]
    assert block["counts"]["newly_paused"] == 1
    [entry] = block["paused"]
    assert entry["decision_id"] == str(decision.id)
    assert entry["content_sha256"] == _pauses(session)[0].content_sha256


def test_12b_with_the_pause_off_the_same_freeze_funds_exactly_as_before(session, settings, monkeypatch):
    experiment, _, _ = _freeze_world(session, settings, monkeypatch)
    payload = ShadowExperimentService(session, settings).freeze(
        SLATE, phase=ExperimentDecisionPhase.FINAL_PREGAME, now=POSITION_NOW, experiment=experiment)

    assert payload["primary"] == 1
    assert "funding_pause" not in payload
    assert len(_positions(session)) == 1 and _pauses(session) == []


def test_13_no_stake_or_bankroll_is_ever_computed_for_a_paused_decision(session, settings, monkeypatch):
    experiment, game, _ = _v18_world(session, settings)

    def forbidden(*args, **kwargs):
        raise AssertionError("staking or bankroll arithmetic ran on a paused decision")

    for name in ("compute_position_stake", "_stake_used", "_available_bankroll",
                 "current_bankroll", "_account_staking_basis", "_reserved_open_stake"):
        monkeypatch.setattr(ShadowExperimentService, name, forbidden)
    assert _dispose(session, _on(settings), experiment, game) == fp.PAUSE_NEW
    # And on reuse.
    assert _dispose(session, _on(settings), experiment, game) == fp.PAUSE_REUSED
    assert _positions(session) == []


def _call_order(source: str) -> list[str]:
    """Called names in source order (line, column), for ordering assertions."""
    calls = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            target = node.func
            name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", None)
            if name:
                calls.append((node.lineno, node.col_offset, name))
    return [name for _, _, name in sorted(calls)]


def test_13b_the_guard_precedes_every_staking_call_in_source_order():
    source = textwrap.dedent(inspect.getsource(ShadowExperimentService._canonical_position_disposition))
    order = _call_order(source)
    guard = order.index("funding_pause_disposition")
    for name in ("_account_staking_basis", "current_bankroll", "_available_bankroll",
                 "compute_position_stake", "_stake_used", "OfficialAspPosition"):
        assert name in order and order.index(name) > guard, name


# == 14-17: record integrity ====================================================

def test_14_every_bound_field_is_populated(session, settings):
    experiment, game, decision = _v18_world(session, settings)
    _create(session, _on(settings), experiment, game)
    [row] = _pauses(session)
    for name in (*fp.HASHED_FIELDS, "id", "content_sha256", "created_at"):
        assert getattr(row, name) is not None, name
    assert row.model_era_id == fp.AUTHORITY["modelEraId"]
    assert row.controlling_engine == fp.AUTHORITY["controllingEngine"]
    assert row.model_id == fp.AUTHORITY["modelId"]
    assert row.model_configuration_identity == fp.AUTHORITY["modelConfigurationIdentity"]
    assert row.policy_configuration_sha256 == fp.AUTHORITY["policyConfigurationSha256"]
    assert row.authority_mapping_sha256 == fp.AUTHORITY_MAPPING_SHA256
    assert row.qualified_edge_threshold == 0.02
    assert row.policy_adjusted_edge == pytest.approx(0.0321)
    assert row.edge == pytest.approx(decision.edge)
    assert row.odds_snapshot_id == decision.odds_snapshot_id
    assert row.market_outcome_id == decision.market_outcome_id
    assert row.shadow_forecast_id == decision.shadow_forecast_id
    assert row.forecast_identity == f"mlb-v18-daily-{row.forecast_package_sha256}"


def test_15_content_hash_recomputes_and_a_tampered_row_fails_closed(session, settings):
    experiment, game, decision = _v18_world(session, settings)
    _create(session, _on(settings), experiment, game)
    [row] = _pauses(session)
    assert fp.verify_record(row)
    assert fp.record_content_sha256(row) == row.content_sha256
    session.commit()

    # Corrupt the stored row beneath the ORM (the append-only guard forbids it
    # through the application), then tick again.
    session.execute(text("update mlb_v18_funding_pauses set edge = 0.9 where id = :i"),
                    {"i": row.id.hex})
    session.commit()
    session.expire_all()
    assert _dispose(session, _on(settings), experiment, game) == fp.PAUSE_INTEGRITY_FAILURE
    assert _positions(session) == []
    [alert] = _alerts(session, fp.INTEGRITY_ACTION)
    assert alert.metadata_json["problem"] == "STORED_CONTENT_SHA256_MISMATCH"
    assert alert.entity_id == decision.id


FORBIDDEN_TOKENS = ("stake", "payout", "profit", "bankroll", "exposure", "settle",
                    "counterfactual", "kelly", "_cap", "result", "pnl", "won", "loss")


def _money_free(names):
    bad = [n for n in names for t in FORBIDDEN_TOKENS if t in n.lower()]
    bad += [n for n in names if "outcome" in n.lower() and n != "market_outcome_id"]
    return bad


def test_16_no_money_or_outcome_column_exists(session, settings):
    columns = [c.name for c in MlbV18FundingPause.__table__.columns]
    assert _money_free(columns) == []
    experiment, game, _ = _v18_world(session, settings)
    receipt = fp.FundingPauseReceipt(experiment_id=str(experiment.id))
    _create(session, _on(settings), experiment, game, receipt)
    assert _money_free(list(receipt.paused[0])) == []
    assert _money_free(list(fp.funding_pause_view(_pauses(session)[0]))) == []


def test_17_reason_and_status(session, settings):
    experiment, game, _ = _v18_world(session, settings)
    _create(session, _on(settings), experiment, game)
    [row] = _pauses(session)
    assert row.reason_code == "FUNDING_EDGE_NOT_ESTABLISHED"
    assert row.validation_status == "PENDING_PROSPECTIVE_VALIDATION"
    assert (row.decision_phase, row.decision_recommendation, row.decision_source_mode) == (
        "FINAL_PREGAME", "QUALIFIED_BET", "LIVE")


# == 18-20: idempotency =======================================================

def test_18_a_second_tick_reuses_the_record_and_writes_nothing(session, settings):
    experiment, game, _ = _v18_world(session, settings)
    assert _dispose(session, _on(settings), experiment, game) == fp.PAUSE_NEW
    session.commit()
    written: list[object] = []
    listener = lambda s, ctx, inst: written.extend([*s.new, *s.dirty, *s.deleted])  # noqa: E731
    event.listen(Session, "before_flush", listener)
    try:
        receipt = fp.FundingPauseReceipt(experiment_id=str(experiment.id))
        assert _dispose(session, _on(settings), experiment, game, receipt) == fp.PAUSE_REUSED
    finally:
        event.remove(Session, "before_flush", listener)
    assert written == []
    assert len(_pauses(session)) == 1
    assert receipt.paused[0]["disposition"] == fp.PAUSE_REUSED
    assert receipt.paused[0]["content_sha256"] == _pauses(session)[0].content_sha256


def test_18b_in_batch_duplicate_under_autoflush_false(session, settings):
    """Production's session does not autoflush; the second call in the SAME
    uncommitted transaction must still see the first record."""
    session.autoflush = False
    experiment, game, _ = _v18_world(session, settings)
    assert _dispose(session, _on(settings), experiment, game) == fp.PAUSE_NEW
    assert _dispose(session, _on(settings), experiment, game) == fp.PAUSE_REUSED
    session.flush()
    assert session.scalar(select(func.count(MlbV18FundingPause.id))) == 1


def test_18c_a_pending_unflushed_record_is_seen_before_a_second_insert(session, settings):
    """A record added earlier in the batch but not yet flushed is found, not
    duplicated: the guard flushes pending state before it looks."""
    from app.services.v18_forecast_view import V18ForecastView

    session.autoflush = False
    experiment, game, decision = _v18_world(session, settings)
    forecast = V18ForecastView(session.get(ShadowForecast, decision.shadow_forecast_id))
    observed = fp.observe(experiment, decision, forecast)
    fields, missing = fp.build_record_fields(
        session, experiment=experiment, game=game, decision=decision, forecast=forecast,
        snapshot=None, observed=observed, now=POSITION_NOW, paused_at=POSITION_NOW)
    assert missing == []
    session.add(MlbV18FundingPause(**fields, content_sha256=fp.record_content_sha256(fields)))
    assert _dispose(session, _on(settings), experiment, game) == fp.PAUSE_REUSED
    session.flush()
    assert session.scalar(select(func.count(MlbV18FundingPause.id))) == 1


def test_18e_the_database_rejects_a_second_record_for_one_decision(session, settings):
    experiment, game, _ = _v18_world(session, settings)
    _dispose(session, _on(settings), experiment, game)
    [row] = _pauses(session)
    fields = {name: getattr(row, name) for name in fp.HASHED_FIELDS}
    session.add(MlbV18FundingPause(**fields, content_sha256=row.content_sha256))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_18d_alerts_are_idempotent_too(session, settings):
    session.autoflush = False
    experiment = _experiment(session, settings, controlling_engine="AISP_BASELINE")
    game = _game(session)
    _decision(session, experiment, game, _v18_forecast(session, game), _quote(session, game))
    for _ in range(3):
        assert _dispose(session, _on(settings), experiment, game) == fp.AUTHORITY_MISMATCH
    session.flush()
    [alert] = _alerts(session)
    body = {k: v for k, v in alert.metadata_json.items() if k != "content_sha256"}
    assert alert.metadata_json["content_sha256"] == fp._sha256(body)
    for key in ("decision_id", "game_id", "shadow_forecast_id", "odds_snapshot_id", "market_outcome_id",
                "observed", "expected", "authority_mapping_sha256", "mismatched_dimensions",
                "detected_at"):
        assert alert.metadata_json.get(key) is not None, key
    assert alert.metadata_json["position_created"] is False


def test_19_two_qualifiers_two_records(session, settings):
    experiment, game_a, _ = _v18_world(session, settings)
    _, game_b, _ = _v18_world(session, settings)
    assert _dispose(session, _on(settings), experiment, game_a) == fp.PAUSE_NEW
    assert _dispose(session, _on(settings), experiment, game_b) == fp.PAUSE_NEW
    assert len({row.decision_id for row in _pauses(session)}) == 2


def test_20_uniqueness_is_on_decision_id_alone(session):
    uniques = [c for c in MlbV18FundingPause.__table__.constraints
               if c.__class__.__name__ == "UniqueConstraint"]
    assert [[col.name for col in c.columns] for c in uniques] == [["decision_id"]]
    migration = (Path(__file__).parents[1] / "alembic" / "versions" /
                 "20260925_0053_mlb_v18_funding_pause.py").read_text(encoding="utf-8")
    assert 'sa.UniqueConstraint("decision_id", name="uq_mlb_v18_funding_pause_decision")' in migration
    assert migration.count("UniqueConstraint(") == 1


# == 21-23: preservation and flag parity ======================================

def test_21_existing_positions_and_settlements_are_untouched_and_open_ones_still_settle(session, settings):
    on = _on(settings)
    experiment, old_game, _ = _v18_world(session, settings)
    # Pre-existing history: one settled, one open V18 position (flag off, as today).
    assert _create(session, settings, experiment, old_game) is True
    [open_position] = _positions(session)
    settled_game = _game(session)
    _decision(session, experiment, settled_game, _v18_forecast(session, settled_game), _quote(session, settled_game))
    assert _create(session, settings, experiment, settled_game) is True
    settled_game.status, settled_game.home_score, settled_game.away_score = GameStatus.FINAL, 5, 2
    session.commit()
    first = ShadowExperimentService(session, settings).settle()
    assert first["settled"] == 1
    before_positions = {str(p.id): (str(p.status), float(p.stake)) for p in _positions(session)}
    before_settlements = {str(s.id): float(s.profit_loss) for s in session.scalars(select(PositionSettlement))}

    # Enable, pause a new qualifier.
    _, new_game, _ = _v18_world(session, settings)
    assert _create(session, on, experiment, new_game) is False
    session.commit()
    assert {str(p.id): (str(p.status), float(p.stake)) for p in _positions(session)} == before_positions
    assert {str(s.id): float(s.profit_loss) for s in session.scalars(select(PositionSettlement))} == before_settlements

    # The open position still settles through the ordinary owner.
    old_game.status, old_game.home_score, old_game.away_score = GameStatus.FINAL, 1, 3
    session.commit()
    assert ShadowExperimentService(session, on).settle()["settled"] == 1
    assert session.get(OfficialAspPosition, open_position.id).status == PositionStatus.SETTLED
    assert len(_pauses(session)) == 1


def test_22_the_guard_only_inserts_and_the_record_is_append_only(session, settings):
    experiment, game, _ = _v18_world(session, settings)
    session.commit()
    touched: list[tuple[str, object]] = []

    def listener(s, ctx, inst):
        touched.extend(("dirty", o) for o in s.dirty if s.is_modified(o))
        touched.extend(("deleted", o) for o in s.deleted)

    event.listen(Session, "before_flush", listener)
    try:
        _dispose(session, _on(settings), experiment, game)
    finally:
        event.remove(Session, "before_flush", listener)
    assert touched == []
    session.commit()
    [row] = _pauses(session)
    row.edge = 0.5
    with pytest.raises(ImmutableRecordError):
        session.flush()
    session.rollback()
    [row] = _pauses(session)
    session.delete(row)
    with pytest.raises(ImmutableRecordError):
        session.flush()
    session.rollback()


def test_23_flag_defaults_off_and_off_is_todays_path(session, settings, monkeypatch):
    assert Settings.model_fields["mlb_v18_funding_pause_enabled"].default is False
    assert settings.mlb_v18_funding_pause_enabled is False
    monkeypatch.setattr(fp, "funding_pause_disposition",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("guard ran with the flag off")))
    experiment, game, decision = _v18_world(session, settings)
    assert _create(session, settings, experiment, game) is True
    [position] = _positions(session)
    assert position.shadow_decision_id == decision.id
    assert _pauses(session) == [] and _alerts(session) == []


# == receipts and the read surface ================================================

def test_job_receipt_lists_records_directly_with_separate_counts(session, settings):
    experiment, game, _ = _v18_world(session, settings)
    first = fp.FundingPauseReceipt(experiment_id=str(experiment.id))
    _create(session, _on(settings), experiment, game, first)
    second = fp.FundingPauseReceipt(experiment_id=str(experiment.id))
    _create(session, _on(settings), experiment, game, second)
    ineligible_game = _game(session)
    _decision(session, experiment, ineligible_game, _v18_forecast(session, ineligible_game),
              _quote(session, ineligible_game), recommendation="NO_BET")
    _create(session, _on(settings), experiment, ineligible_game, second)

    job = fp.aggregate_receipts([first.as_dict(), second.as_dict()])
    assert job["counts"]["newly_paused"] == 1
    assert job["counts"]["previously_paused_reused"] == 1
    assert job["counts"]["ineligible_not_paused"] == 1
    assert job["counts"]["mismatch_alerts"] == 0
    [new] = job["newly_paused"]
    [reused] = job["previously_paused_reused"]
    assert new["record_id"] == reused["record_id"] and new["content_sha256"] == reused["content_sha256"]
    for key in ("decision_id", "experiment_id", "model_era_id", "controlling_engine",
                "shadow_forecast_id", "forecast_package_sha256", "odds_snapshot_id", "market_outcome_id",
                "sportsbook", "selection", "american_odds", "decimal_odds", "model_probability",
                "market_probability", "edge", "policy_adjusted_edge", "qualified_edge_threshold",
                "quote_observed_at", "forecast_cutoff_at", "forecast_available_at",
                "recommendation_policy_version", "staking_policy_version", "policy_configuration_sha256",
                "authority_mapping_sha256"):
        assert new[key] is not None, key
    assert _money_free(list(new)) == []


def test_read_surface_exposes_the_pause_by_decision(session, settings):
    experiment, game, decision = _v18_world(session, settings)
    _create(session, _on(settings), experiment, game)
    views = fp.funding_pauses_for_decisions(session, [str(decision.id), str(uuid.uuid4())])
    assert list(views) == [str(decision.id)]
    view = views[str(decision.id)]
    assert view["reason"] == "FUNDING_EDGE_NOT_ESTABLISHED"
    assert view["status"] == "PENDING_PROSPECTIVE_VALIDATION"
    assert view["verified"] is True and view["position_created"] is False
    assert view["record_id"] and view["content_sha256"] and view["paused_at"]
    source = Path(inspect.getsourcefile(__import__("app.services.command_center",
                                                   fromlist=["x"]))).read_text(encoding="utf-8")
    assert '"funding_pause": pauses.get(str(did))' in source

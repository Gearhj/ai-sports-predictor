"""Practical pregame policy V2 — focused tests.

Covers the starter/lineup evidence state machines, the empirical penalty
calibration, adjusted-edge and EV requirements, evidence-tier staking, the
separate immutable V2 experiment (V1 untouched), prospective-only activation,
idempotent decisions, and the zero-provider-call public-read boundary.

Fixtures are built directly (no demo-slate seeding) so every test is
independent of the wall-clock time of day.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import (
    AvailabilityEvent,
    Game,
    GameStatus,
    GameTeam,
    League,
    LineupObservation,
    ShadowExperiment,
    Sport,
    StarterObservation,
    Team,
)
from app.services.evidence_states import (
    LINEUP_CONFIRMED_COMPLETE,
    LINEUP_MISSING_BLOCKING,
    LINEUP_PENDING_ALLOWED,
    LineupContext,
    STARTER_CONFIRMED,
    STARTER_CONFLICT,
    STARTER_MISSING,
    STARTER_PROBABLE_STABLE,
    STARTER_PROBABLE_UNSTABLE,
    lineup_evidence,
    starter_evidence,
)
from app.services.practical_policy import (
    PENALTY_CALIBRATION,
    POLICY_VERSION,
    PracticalPolicyEvaluator,
    TIER_C,
    TIER_D,
    frozen_policy_config,
)
from app.services.shadow_experiment_service import (
    EXPERIMENT_NAME,
    ShadowExperimentService,
    V2_DECISION_POLICY,
    V2_EXPERIMENT_NAME,
)

NOW = datetime(2026, 8, 31, 20, 0, tzinfo=UTC)  # fixed instant; wall clock never used
STARTS = datetime(2026, 8, 31, 23, 5, tzinfo=UTC)
CUTOFF = STARTS - timedelta(minutes=15)
import uuid as _uuid

PITCHER_A = _uuid.UUID("11111111-1111-1111-1111-111111111111")
PITCHER_B = _uuid.UUID("22222222-2222-2222-2222-222222222222")


def _seed_game(session):
    sport = session.scalar(select(Sport).where(Sport.code == "BASEBALL"))
    if sport is None:
        sport = Sport(code="BASEBALL", name="Baseball", active=True)
        session.add(sport)
        session.flush()
    league = session.scalar(select(League).where(League.code == "MLB"))
    if league is None:
        league = League(sport_id=sport.id, code="MLB", name="MLB", active=True)
        session.add(league)
        session.flush()

    def team(name, abbr):
        row = Team(league_id=league.id, name=name, abbreviation=abbr, city=name.split()[0], active=True)
        session.add(row)
        session.flush()
        return row

    home = team("New York Yankees", "NYY")
    away = team("Boston Red Sox", "BOS")
    game = Game(league_id=league.id, starts_at=STARTS, status=GameStatus.SCHEDULED, source_mode="LIVE")
    session.add(game)
    session.flush()
    session.add_all([
        GameTeam(game_id=game.id, team_id=home.id, is_home=True),
        GameTeam(game_id=game.id, team_id=away.id, is_home=False),
    ])
    session.commit()
    return game, home, away


def _observe(session, game, team, *, pitcher=PITCHER_A, state="PROBABLE", at=None, source="MLB_STATS_API"):
    at = at or (CUTOFF - timedelta(hours=2))
    session.add(StarterObservation(
        game_id=game.id, team_id=team.id,
        probable_pitcher_id=pitcher, confirmed_pitcher_id=pitcher if state == "CONFIRMED" else None,
        confirmation_state=state, observed_at=at, available_as_of=at,
        source=source, source_mode="LIVE",
        source_record_id=f"{game.id}:{team.id}:{at.isoformat()}",
        payload_checksum=f"cs-{team.id}-{at.timestamp()}",
    ))
    session.commit()


def _stable_pair(session, game, team, *, pitcher=PITCHER_A, latest=None):
    latest = latest or (CUTOFF - timedelta(minutes=30))
    _observe(session, game, team, pitcher=pitcher, at=latest - timedelta(minutes=30))
    _observe(session, game, team, pitcher=pitcher, at=latest)


FORECAST_AT = CUTOFF - timedelta(hours=3)


# -- starter evidence state machine ------------------------------------------

def test_confirmed_starter_state(session, settings):
    game, home, _ = _seed_game(session)
    _observe(session, game, home, state="CONFIRMED", at=CUTOFF - timedelta(minutes=20))
    result = starter_evidence(session, game, home.id, cutoff=CUTOFF, forecast_generated_at=FORECAST_AT)
    assert result.state == STARTER_CONFIRMED


def test_probable_stable_qualification(session, settings):
    game, home, _ = _seed_game(session)
    _stable_pair(session, game, home)
    result = starter_evidence(
        session, game, home.id, cutoff=CUTOFF,
        forecast_generated_at=CUTOFF - timedelta(minutes=20),
    )
    assert result.state == STARTER_PROBABLE_STABLE
    assert result.stable_streak_count >= 2
    assert result.eligible


def test_single_observation_is_unstable(session, settings):
    game, home, _ = _seed_game(session)
    _observe(session, game, home, at=CUTOFF - timedelta(minutes=30))
    result = starter_evidence(session, game, home.id, cutoff=CUTOFF, forecast_generated_at=FORECAST_AT)
    assert result.state == STARTER_PROBABLE_UNSTABLE
    assert any("consecutive observation" in r for r in result.reasons)


def test_observations_too_close_together_are_unstable(session, settings):
    game, home, _ = _seed_game(session)
    base = CUTOFF - timedelta(minutes=30)
    _observe(session, game, home, at=base)
    _observe(session, game, home, at=base + timedelta(minutes=5))
    result = starter_evidence(session, game, home.id, cutoff=CUTOFF, forecast_generated_at=FORECAST_AT)
    assert result.state == STARTER_PROBABLE_UNSTABLE
    assert any("span under" in r for r in result.reasons)


def test_starter_identity_change_restarts_the_streak(session, settings):
    game, home, _ = _seed_game(session)
    _observe(session, game, home, pitcher=PITCHER_A, at=CUTOFF - timedelta(hours=3))
    _observe(session, game, home, pitcher=PITCHER_B, at=CUTOFF - timedelta(minutes=50))
    result = starter_evidence(session, game, home.id, cutoff=CUTOFF, forecast_generated_at=FORECAST_AT)
    # Only one observation of the NEW identity: not yet stable.
    assert result.state == STARTER_PROBABLE_UNSTABLE
    assert result.pitcher_id == str(PITCHER_B)


def test_simultaneous_provider_conflict(session, settings):
    game, home, _ = _seed_game(session)
    at = CUTOFF - timedelta(minutes=30)
    _observe(session, game, home, pitcher=PITCHER_A, at=at, source="SOURCE_ONE")
    _observe(session, game, home, pitcher=PITCHER_B, at=at + timedelta(seconds=30), source="SOURCE_TWO")
    result = starter_evidence(session, game, home.id, cutoff=CUTOFF, forecast_generated_at=FORECAST_AT)
    assert result.state == STARTER_CONFLICT


def test_stale_starter_observation_is_unstable(session, settings):
    game, home, _ = _seed_game(session)
    _stable_pair(session, game, home, latest=CUTOFF - timedelta(hours=5))
    result = starter_evidence(session, game, home.id, cutoff=CUTOFF, forecast_generated_at=FORECAST_AT)
    assert result.state == STARTER_PROBABLE_UNSTABLE
    assert any("not current" in r for r in result.reasons)


def test_unresolved_availability_event_blocks_stability(session, settings):
    game, home, _ = _seed_game(session)
    _stable_pair(session, game, home)
    session.add(AvailabilityEvent(
        schema_version="1", event_type="PROBABLE_PITCHER_CHANGED", source="MLB_DERIVED",
        source_event_key=f"evt-{game.id}", sport="BASEBALL", league="MLB", game_id=game.id,
        published_at=CUTOFF - timedelta(minutes=10), received_at=CUTOFF - timedelta(minutes=10),
        description="probable changed", payload={},
    ))
    session.commit()
    # Forecast predates the event -> the event is unresolved.
    result = starter_evidence(
        session, game, home.id, cutoff=CUTOFF, forecast_generated_at=CUTOFF - timedelta(hours=1),
    )
    assert result.state == STARTER_PROBABLE_UNSTABLE
    assert any("Unresolved availability" in r for r in result.reasons)


def test_forecast_predating_stable_identity_is_unstable(session, settings):
    game, home, _ = _seed_game(session)
    _stable_pair(session, game, home, pitcher=PITCHER_B)
    streak_start = CUTOFF - timedelta(minutes=60)
    result = starter_evidence(
        session, game, home.id, cutoff=CUTOFF,
        forecast_generated_at=streak_start - timedelta(hours=2),
    )
    assert result.state == STARTER_PROBABLE_UNSTABLE
    assert any("predates" in r for r in result.reasons)


def test_missing_starter_state(session, settings):
    game, home, _ = _seed_game(session)
    result = starter_evidence(session, game, home.id, cutoff=CUTOFF, forecast_generated_at=FORECAST_AT)
    assert result.state == STARTER_MISSING


# -- lineup evidence ----------------------------------------------------------

def _lineup(session, game, team, *, status="CONFIRMED", complete=True, at=None):
    at = at or (CUTOFF - timedelta(minutes=40))
    session.add(LineupObservation(
        game_id=game.id, team_id=team.id, lineup_status=status, is_complete=complete,
        observed_at=at, available_as_of=at, source="MLB_STATS_API", source_mode="LIVE",
        source_record_id=f"{game.id}:{team.id}", payload_checksum=f"lc-{team.id}-{at.timestamp()}",
    ))
    session.commit()


def _ctx(**overrides):
    # Deliberately decoupled from starter/quote/cutoff gates (they block
    # independently; coupling them here was duplicate derived gating).
    base = dict(market_is_moneyline=True, dependency_allows_pending=True, pregame=True)
    base.update(overrides)
    return LineupContext(**base)


def test_lineup_confirmed_complete(session, settings):
    game, home, away = _seed_game(session)
    _lineup(session, game, home)
    _lineup(session, game, away)
    result = lineup_evidence(session, game, home.id, away.id, cutoff=CUTOFF,
                             forecast_generated_at=FORECAST_AT, context=_ctx())
    assert result.state == LINEUP_CONFIRMED_COMPLETE


def test_lineup_pending_allowed_for_moneyline(session, settings):
    game, home, away = _seed_game(session)
    result = lineup_evidence(session, game, home.id, away.id, cutoff=CUTOFF,
                             forecast_generated_at=FORECAST_AT, context=_ctx())
    assert result.state == LINEUP_PENDING_ALLOWED
    assert result.eligible_for_moneyline and result.penalized


def test_lineup_pending_blocked_for_other_markets(session, settings):
    game, home, away = _seed_game(session)
    result = lineup_evidence(session, game, home.id, away.id, cutoff=CUTOFF,
                             forecast_generated_at=FORECAST_AT,
                             context=_ctx(market_is_moneyline=False))
    assert result.state == LINEUP_MISSING_BLOCKING
    assert any("moneyline only" in r for r in result.reasons)


def test_lineup_pending_blocked_when_model_dependency_forbids(session, settings):
    game, home, away = _seed_game(session)
    result = lineup_evidence(session, game, home.id, away.id, cutoff=CUTOFF,
                             forecast_generated_at=FORECAST_AT,
                             context=_ctx(dependency_allows_pending=False))
    assert result.state == LINEUP_MISSING_BLOCKING


# -- penalties + adjusted-edge evaluation ------------------------------------

def _selection(**overrides):
    base = dict(
        market_type="MONEYLINE", selection="NYY ML", is_stale=False,
        model_probability_fraction=0.60, no_vig_probability_fraction=0.52,
        decimal_odds=2.00, american_odds=100, sportsbook="BookX",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _forecast(uncertainty=0.01):
    # Generated after the stable-streak start (streak begins at CUTOFF - 60m in
    # the _stable_pair fixture) so the forecast reflects the pitcher identity.
    return SimpleNamespace(
        generated_at=CUTOFF - timedelta(minutes=20), uncertainty=uncertainty, readiness_components={},
    )


def test_penalty_calibration_is_conservative_and_versioned(settings):
    assert PENALTY_CALIBRATION["starter"]["penalty_points"] == 2.0
    assert PENALTY_CALIBRATION["starter"]["basis"] == "sparse_history_conservative_default"
    assert PENALTY_CALIBRATION["starter"]["sample_size"] < 30
    assert PENALTY_CALIBRATION["lineup"]["penalty_points"] == 0.5
    assert PENALTY_CALIBRATION["lineup"]["sample_size"] >= 30
    config = frozen_policy_config(settings)
    assert config["checksum"] == frozen_policy_config(settings)["checksum"]
    assert len(config["checksum"]) == 64


def test_v2_qualifies_with_probable_stable_and_pending_lineup(session, settings):
    game, home, away = _seed_game(session)
    _stable_pair(session, game, home)
    _stable_pair(session, game, away, pitcher=PITCHER_B)
    evaluator = PracticalPolicyEvaluator(session, settings)
    result = evaluator.evaluate(
        game, _forecast(), SimpleNamespace(last_availability_event=None),
        _selection(), now=NOW, cutoff=CUTOFF,
    )
    assert result.qualified
    assert result.tier == TIER_C  # lineup pending dominates the tier
    assert result.starter_penalty == pytest.approx(0.02)
    assert result.lineup_penalty == pytest.approx(0.005)
    # 0.60 - 0.01 uncertainty - 0.02 starter - 0.005 lineup = 0.565
    assert result.adjusted_probability == pytest.approx(0.565)
    assert result.adjusted_edge == pytest.approx(0.045)
    assert result.adjusted_expected_value == pytest.approx(0.13)
    assert result.evidence_multiplier == 0.5
    assert result.position_cap_fraction == pytest.approx(0.005)


def test_v2_blocks_below_adjusted_edge_threshold(session, settings):
    game, home, away = _seed_game(session)
    _stable_pair(session, game, home)
    _stable_pair(session, game, away, pitcher=PITCHER_B)
    result = PracticalPolicyEvaluator(session, settings).evaluate(
        game, _forecast(), SimpleNamespace(last_availability_event=None),
        _selection(no_vig_probability_fraction=0.555), now=NOW, cutoff=CUTOFF,
    )
    assert not result.qualified
    assert any("Adjusted edge" in r for r in result.reasons)


def test_v2_requires_positive_adjusted_ev(session, settings):
    game, home, away = _seed_game(session)
    _stable_pair(session, game, home)
    _stable_pair(session, game, away, pitcher=PITCHER_B)
    # Short price: decimal 1.30 makes adjusted EV negative even with edge.
    result = PracticalPolicyEvaluator(session, settings).evaluate(
        game, _forecast(uncertainty=0.0),
        SimpleNamespace(last_availability_event=None),
        _selection(model_probability_fraction=0.62, no_vig_probability_fraction=0.58, decimal_odds=1.30),
        now=NOW, cutoff=CUTOFF,
    )
    assert not result.qualified
    assert any("expected value" in r.lower() for r in result.reasons)


def test_v2_blocks_unstable_starters(session, settings):
    game, home, away = _seed_game(session)
    _observe(session, game, home)  # single observation only
    _stable_pair(session, game, away, pitcher=PITCHER_B)
    result = PracticalPolicyEvaluator(session, settings).evaluate(
        game, _forecast(), SimpleNamespace(last_availability_event=None),
        _selection(), now=NOW, cutoff=CUTOFF,
    )
    assert result.tier == TIER_D
    assert not result.qualified


def test_v2_blocks_stale_quote_and_missing_forecast(session, settings):
    game, home, away = _seed_game(session)
    evaluator = PracticalPolicyEvaluator(session, settings)
    stale = evaluator.evaluate(game, _forecast(), None, _selection(is_stale=True), now=NOW, cutoff=CUTOFF)
    assert stale.tier == TIER_D and "stale" in stale.reason.lower()
    # Pre-cutoff a stale quote is pending evidence (refreshes arrive in the
    # pregame window), never a final ineligibility.
    assert stale.lifecycle == "PENDING_FINAL_EVIDENCE"
    missing = evaluator.evaluate(game, None, None, _selection(), now=NOW, cutoff=CUTOFF)
    assert missing.tier == TIER_D and "No valid current forecast" in missing.reason
    final_missing = evaluator.evaluate(game, None, None, _selection(), now=NOW, cutoff=CUTOFF, final=True)
    assert final_missing.lifecycle == "FINAL_NO_BET"


# -- V2 staking ---------------------------------------------------------------

def test_v2_reduced_stake_uses_adjusted_probability_and_cap(settings):
    experiment = SimpleNamespace(kelly_fraction=0.25, max_position_percentage=0.01)
    decision = SimpleNamespace(
        decimal_odds=2.0, model_probability=0.60,
        evidence={"adjusted_probability": 0.565, "position_cap_fraction": 0.005,
                  "evidence_multiplier": 0.5},
    )
    stake = ShadowExperimentService.compute_position_stake(experiment, decision, 1000.0)
    # Kelly(0.565 @ 2.0) = 0.13; quarter = 0.0325 -> capped at 0.5% = $5.00; x0.5 = $2.50.
    assert stake == pytest.approx(2.50)
    v1_decision = SimpleNamespace(decimal_odds=2.0, model_probability=0.60, evidence=None)
    v1_stake = ShadowExperimentService.compute_position_stake(experiment, v1_decision, 1000.0)
    assert v1_stake == pytest.approx(10.0)  # quarter-Kelly capped at the 1% V1 cap


def test_zero_or_negative_adjusted_kelly_is_no_position(settings):
    experiment = SimpleNamespace(kelly_fraction=0.25, max_position_percentage=0.01)
    decision = SimpleNamespace(
        decimal_odds=1.80, model_probability=0.60,
        evidence={"adjusted_probability": 0.40, "position_cap_fraction": 0.005,
                  "evidence_multiplier": 0.5},
    )
    assert ShadowExperimentService.compute_position_stake(experiment, decision, 1000.0) is None


# -- separate immutable V2 experiment -----------------------------------------

def _start_v1(session, settings):
    service = ShadowExperimentService(session, settings)
    today = datetime.now(UTC).astimezone().date()
    from zoneinfo import ZoneInfo

    from app.core.time import local_today

    v1 = service.ensure_started(start_date=local_today(settings.display_timezone), code_commit="deadbeef")
    session.commit()
    return service, v1


def test_v2_registration_is_prospective_separate_and_idempotent(session, settings):
    from app.core.time import local_today

    service, v1 = _start_v1(session, settings)
    today = local_today(settings.display_timezone)
    with pytest.raises(ValueError):
        service.ensure_v2_started(effective_date=today, code_commit="deadbeef")
    v2 = service.ensure_v2_started(effective_date=today + timedelta(days=1), code_commit="deadbeef")
    session.commit()
    assert v2.id != v1.id
    assert v2.name == V2_EXPERIMENT_NAME
    assert v2.decision_policy == V2_DECISION_POLICY
    assert v2.policy_config and v2.policy_config.get("checksum")
    assert float(v2.starting_bankroll) == 1000.0
    # Idempotent re-registration returns the same row.
    again = service.ensure_v2_started(effective_date=today + timedelta(days=2), code_commit="deadbeef")
    assert again.id == v2.id
    # V1 selection is untouched: the default read still returns the strict control.
    assert service.active_experiment().id == v1.id
    assert service.active_experiment().name == EXPERIMENT_NAME
    keys = [e.decision_policy for e in service.active_experiments()]
    assert set(keys) == {v1.decision_policy, V2_DECISION_POLICY}


def test_v2_freeze_refuses_pre_effective_slates(session, settings):
    from app.core.time import local_today
    from app.models import ExperimentDecisionPhase, ShadowExperimentDecision

    service, _ = _start_v1(session, settings)
    today = local_today(settings.display_timezone)
    v2 = service.ensure_v2_started(effective_date=today + timedelta(days=1), code_commit="deadbeef")
    session.commit()
    result = service.freeze(today, phase=ExperimentDecisionPhase.FINAL_PREGAME, experiment=v2)
    assert result.get("note") == "no historical backfill"
    count = session.scalar(select(ShadowExperimentDecision.id).where(
        ShadowExperimentDecision.experiment_id == v2.id
    ))
    assert count is None  # no retroactive decision exists


def test_public_v2_reads_make_zero_provider_calls(session, settings, monkeypatch):
    import httpx

    def _no_network(*args, **kwargs):
        raise AssertionError("public V2 read attempted a provider network call")

    monkeypatch.setattr(httpx.AsyncClient, "get", _no_network)
    monkeypatch.setattr(httpx.Client, "get", _no_network, raising=False)
    game, home, away = _seed_game(session)
    _stable_pair(session, game, home)
    result = PracticalPolicyEvaluator(session, settings).evaluate(
        game, _forecast(), None, _selection(), now=NOW, cutoff=CUTOFF,
    )
    assert result.recommendation in {"QUALIFIED_BET", "NO_BET"}

    from app.services.read_service import ReadService

    comparison = ReadService(session, settings).experiment_comparison()
    assert "experiments" in comparison


# -- V2.1 semantics repair: decomposition, lifecycle, stability -------------

def test_feature_completeness_decomposition_classifies_every_component():
    from app.services.practical_policy import (
        CLASS_A, CLASS_B, CLASS_D, CLASS_E, DECOMPOSITION_VERSION,
        decompose_feature_completeness,
    )

    forecast = SimpleNamespace(readiness_components={
        "feature_completeness": {"status": "stale", "critical": True, "detail": "36% coverage"},
        "lineups": {"status": "missing", "critical": False},
        "pitcher_confirmation": {"status": "missing", "critical": False},
        "team_stats": {"status": "ready", "critical": True},
        "weather": {"status": "stale", "critical": False},
        "_gate2": {
            "missing_features": [
                "home_lineup_xwoba", "away_starter_era", "home_statcast_xwoba",
                "baserunning", "defense", "opponent_adjustment",
            ],
            "stale_features": ["roster"],
            "provisional_features": ["park_run_factor:neutral_prior"],
        },
    })
    result = decompose_feature_completeness(forecast)
    assert result["version"] == DECOMPOSITION_VERSION
    by_name = {c["component"]: c for c in result["components"]}
    # Lineup/starter-derived features are DUPLICATE gates, never second blocks.
    assert by_name["home_lineup_xwoba"]["class"] == CLASS_E
    assert by_name["away_starter_era"]["class"] == CLASS_E
    assert by_name["lineups"]["class"] == CLASS_E
    assert by_name["pitcher_confirmation"]["class"] == CLASS_E
    # Statcast disabled by configuration -> governed fallback.
    assert by_name["home_statcast_xwoba"]["class"] == CLASS_B
    # Structural placeholders are optional context.
    assert by_name["baserunning"]["class"] == CLASS_D
    assert by_name["defense"]["class"] == CLASS_D
    # The composite itself never lands on the hard-block list here.
    assert result["model_required_missing"] == []
    assert not any(c["hard_block_v2"] for c in result["components"])


def test_optional_and_fallback_absence_does_not_block_v2(session, settings):
    game, home, away = _seed_game(session)
    _stable_pair(session, game, home)
    _stable_pair(session, game, away, pitcher=PITCHER_B)
    forecast = SimpleNamespace(
        generated_at=CUTOFF - timedelta(minutes=20), uncertainty=0.01,
        readiness_components={
            "feature_completeness": {"status": "stale", "critical": True},
            "weather": {"status": "missing", "critical": False},
            "_gate2": {"missing_features": [
                "home_lineup_xwoba", "away_lineup_xwoba", "home_statcast_xwoba",
                "baserunning", "defense", "opponent_adjustment", "roof_orientation_adjustment",
            ], "stale_features": ["roster"], "provisional_features": []},
        },
    )
    result = PracticalPolicyEvaluator(session, settings).evaluate(
        game, forecast, SimpleNamespace(last_availability_event=None),
        _selection(), now=NOW, cutoff=CUTOFF,
    )
    assert result.qualified
    assert result.critical_evidence_complete
    assert "model_required_inputs_valid_or_governed_fallback" in result.gates_passed


def test_model_required_input_with_no_fallback_blocks_v2(session, settings):
    game, home, away = _seed_game(session)
    _stable_pair(session, game, home)
    _stable_pair(session, game, away, pitcher=PITCHER_B)
    forecast = SimpleNamespace(
        generated_at=CUTOFF - timedelta(minutes=20), uncertainty=0.01,
        readiness_components={
            "_gate2": {"missing_features": ["home_rating"], "stale_features": [], "provisional_features": []},
        },
    )
    result = PracticalPolicyEvaluator(session, settings).evaluate(
        game, forecast, SimpleNamespace(last_availability_event=None),
        _selection(), now=NOW, cutoff=CUTOFF,
    )
    assert not result.qualified
    assert result.tier == TIER_D
    assert any("no valid governed fallback" in r for r in result.reasons)
    assert "home_rating" in (result.decomposition or {}).get("model_required_missing", [])


def test_v1_readiness_score_never_independently_blocks_v2(session, settings):
    game, home, away = _seed_game(session)
    _stable_pair(session, game, home)
    _stable_pair(session, game, away, pitcher=PITCHER_B)
    # Readiness far below 70 with only duplicate/fallback/optional gaps.
    forecast = SimpleNamespace(
        generated_at=CUTOFF - timedelta(minutes=20), uncertainty=0.01,
        readiness_score=31.0,
        readiness_components={
            "feature_completeness": {"status": "stale", "critical": True},
            "lineups": {"status": "missing", "critical": True},
            "pitcher_confirmation": {"status": "missing", "critical": True},
            "_gate2": {"missing_features": ["home_lineup_xwoba"], "stale_features": [], "provisional_features": []},
        },
    )
    result = PracticalPolicyEvaluator(session, settings).evaluate(
        game, forecast, SimpleNamespace(last_availability_event=None),
        _selection(), now=NOW, cutoff=CUTOFF,
    )
    assert result.qualified  # V1 readiness/duplicate gates removed


def test_identical_observations_establish_and_never_reset_stable_since(session, settings):
    game, home, _ = _seed_game(session)
    base = CUTOFF - timedelta(hours=4)
    for minutes in (0, 30, 60, 90, 120):  # repeated identical polls
        _observe(session, game, home, at=base + timedelta(minutes=minutes))
    result = starter_evidence(
        session, game, home.id, cutoff=CUTOFF,
        forecast_generated_at=base + timedelta(minutes=10),
    )
    assert result.state == STARTER_PROBABLE_STABLE
    assert result.streak_started_at == base.isoformat()
    # stable_since = the first observation >= 10 minutes after the streak began
    assert result.stable_since == (base + timedelta(minutes=30)).isoformat()
    # A forecast generated after the identity was first known stays current
    # regardless of later identical polls.
    assert result.forecast_evidence_match is True


def test_forecast_with_same_identity_survives_later_identical_polls(session, settings):
    game, home, _ = _seed_game(session)
    base = CUTOFF - timedelta(hours=6)
    _observe(session, game, home, at=base)
    _observe(session, game, home, at=base + timedelta(minutes=30))
    forecast_at = base + timedelta(hours=1)
    # Many identical polls AFTER the forecast.
    for hours in (2, 3, 4, 5):
        _observe(session, game, home, at=base + timedelta(hours=hours))
    result = starter_evidence(session, game, home.id, cutoff=CUTOFF, forecast_generated_at=forecast_at)
    assert result.state == STARTER_PROBABLE_STABLE


def test_lineup_event_does_not_destabilize_starter_state(session, settings):
    game, home, _ = _seed_game(session)
    _stable_pair(session, game, home)
    session.add(AvailabilityEvent(
        schema_version="1", event_type="LINEUP_CONFIRMED", source="MLB_DERIVED",
        source_event_key=f"lineup-{game.id}", sport="BASEBALL", league="MLB", game_id=game.id,
        published_at=CUTOFF - timedelta(minutes=5), received_at=CUTOFF - timedelta(minutes=5),
        description="lineup posted", payload={},
    ))
    session.commit()
    result = starter_evidence(
        session, game, home.id, cutoff=CUTOFF,
        forecast_generated_at=CUTOFF - timedelta(hours=1),
    )
    # A lineup event is not material to the STARTER machine.
    assert result.state == STARTER_PROBABLE_STABLE


def test_material_lineup_change_after_forecast_blocks_lineup_until_refresh(session, settings):
    game, home, away = _seed_game(session)
    session.add(AvailabilityEvent(
        schema_version="1", event_type="LINEUP_CONFIRMED", source="MLB_DERIVED",
        source_event_key=f"lineup2-{game.id}", sport="BASEBALL", league="MLB", game_id=game.id,
        published_at=CUTOFF - timedelta(minutes=5), received_at=CUTOFF - timedelta(minutes=5),
        description="lineup posted", payload={},
    ))
    session.commit()
    stale_forecast_at = CUTOFF - timedelta(hours=2)
    blocked = lineup_evidence(session, game, home.id, away.id, cutoff=CUTOFF,
                              forecast_generated_at=stale_forecast_at, context=_ctx())
    assert blocked.state == LINEUP_MISSING_BLOCKING
    assert any("Material lineup change" in r for r in blocked.reasons)
    # After the one governed refresh the forecast postdates the event.
    refreshed = lineup_evidence(session, game, home.id, away.id, cutoff=CUTOFF,
                                forecast_generated_at=CUTOFF - timedelta(minutes=2), context=_ctx())
    assert refreshed.state == LINEUP_PENDING_ALLOWED


def test_unresolved_scratch_still_blocks_lineup(session, settings):
    game, home, away = _seed_game(session)
    session.add(AvailabilityEvent(
        schema_version="1", event_type="LATE_SCRATCH", source="MLB_DERIVED",
        source_event_key=f"scratch-{game.id}", sport="BASEBALL", league="MLB", game_id=game.id,
        published_at=CUTOFF - timedelta(minutes=5), received_at=CUTOFF - timedelta(minutes=5),
        description="late scratch", payload={},
    ))
    session.commit()
    result = lineup_evidence(session, game, home.id, away.id, cutoff=CUTOFF,
                             forecast_generated_at=CUTOFF - timedelta(hours=1), context=_ctx())
    assert result.state == "LATE_SCRATCH_UNRESOLVED"


def test_precutoff_pending_and_final_lifecycle_states(session, settings):
    game, home, away = _seed_game(session)
    _observe(session, game, home)  # single obs: not yet stable — pending evidence
    _stable_pair(session, game, away, pitcher=PITCHER_B)
    evaluator = PracticalPolicyEvaluator(session, settings)
    pre = evaluator.evaluate(
        game, _forecast(), SimpleNamespace(last_availability_event=None),
        _selection(), now=NOW, cutoff=CUTOFF,
    )
    assert pre.lifecycle == "PENDING_FINAL_EVIDENCE"
    assert "PENDING_FINAL_EVIDENCE" in pre.reason  # not branded final-ineligible
    final = evaluator.evaluate(
        game, _forecast(), SimpleNamespace(last_availability_event=None),
        _selection(), now=NOW, cutoff=CUTOFF, final=True,
    )
    assert final.lifecycle == "FINAL_NO_BET"


def test_provisionally_eligible_becomes_final_qualified(session, settings):
    game, home, away = _seed_game(session)
    _stable_pair(session, game, home)
    _stable_pair(session, game, away, pitcher=PITCHER_B)
    evaluator = PracticalPolicyEvaluator(session, settings)
    pre = evaluator.evaluate(
        game, _forecast(), SimpleNamespace(last_availability_event=None),
        _selection(), now=NOW, cutoff=CUTOFF,
    )
    assert pre.qualified and pre.lifecycle == "PROVISIONALLY_ELIGIBLE"
    final = evaluator.evaluate(
        game, _forecast(), SimpleNamespace(last_availability_event=None),
        _selection(), now=NOW, cutoff=CUTOFF, final=True,
    )
    assert final.qualified and final.lifecycle == "FINAL_QUALIFIED"


def test_v2_supersession_registry_and_v21_separation(session, settings):
    from datetime import timedelta as _td

    from app.core.time import local_today
    from app.models import AuditEvent, ShadowExperiment
    from app.services.practical_policy import SUPERSEDED_POLICY_VERSION

    service, v1 = _start_v1(session, settings)
    today = local_today(settings.display_timezone)
    # Simulate the preserved never-activated V2 registration.
    old = ShadowExperiment(
        name="ASP PRACTICAL PREGAME BANKROLL — 2026 V2", version=99,
        started_at=datetime.now(UTC), start_date=today + _td(days=1),
        starting_bankroll=1000.0, sport="MLB",
        recommendation_policy_version=SUPERSEDED_POLICY_VERSION,
        staking_policy_version="AISP_QUARTER_KELLY_EVIDENCE_V2",
        decision_policy=SUPERSEDED_POLICY_VERSION,
        controlling_engine="AISP_BASELINE", supported_markets=["MONEYLINE"],
        kelly_fraction=0.25, unit_percentage=0.005, max_position_percentage=0.01,
        max_same_game_percentage=0.02, max_daily_percentage=0.05,
        code_commit="cafe", code_identity="git:cafe", is_active=True,
    )
    session.add(old)
    session.commit()
    v21 = service.ensure_v2_started(effective_date=today + _td(days=1), code_commit="deadbeef")
    session.commit()
    assert v21.decision_policy == V2_DECISION_POLICY
    assert v21.decision_policy.endswith("V2_1")
    # The superseded registration is excluded from every operational surface…
    active_policies = {e.decision_policy for e in service.active_experiments()}
    assert SUPERSEDED_POLICY_VERSION not in active_policies
    assert V2_DECISION_POLICY in active_policies
    # …but its row is preserved untouched, and the supersession is audited.
    preserved = session.get(ShadowExperiment, old.id)
    assert preserved.is_active is True and preserved.decision_policy == SUPERSEDED_POLICY_VERSION
    audit = session.scalar(select(AuditEvent).where(
        AuditEvent.action == "experiment.superseded_before_activation",
        AuditEvent.entity_id == old.id,
    ))
    assert audit is not None
    assert audit.metadata_json["status"] == "SUPERSEDED_BEFORE_ACTIVATION"
    # V1 untouched throughout.
    assert service.active_experiment().id == v1.id

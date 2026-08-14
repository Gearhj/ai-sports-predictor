from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import sqrt

import pytest

from app.gen2.event_taxonomy import PlateAppearanceOutcome, ordered_outcomes
from app.gen2.simulator import (
    BaseOutTransitionModel,
    BaseOutState,
    GameSimulationInput,
    RelieverOption,
    SimulationError,
    TeamSimulationInput,
    TransitionObservation,
    TransitionProbability,
    TransitionResult,
    fit_base_out_transition_model,
    sample_outcome_frequencies,
    simulate_game,
    simulate_once,
    validate_outcome_probabilities,
)


EVIDENCE_TIME = datetime(2025, 6, 1, tzinfo=UTC)
FIT_CUTOFF = datetime(2025, 7, 1, tzinfo=UTC)
PROVIDER_IDENTITY = "f" * 64


def _observation(
    *,
    observation_id: str,
    plate_appearance_id: str,
    outcome: PlateAppearanceOutcome,
    before: BaseOutState,
    after: BaseOutState,
    runs_scored: int,
    evidence_checksum: str,
    game_id: str = "g",
    event_at: datetime = EVIDENCE_TIME,
    eligible_at: datetime = EVIDENCE_TIME,
    evidence_tier: str = "A",
) -> TransitionObservation:
    return TransitionObservation(
        observation_id=observation_id,
        game_id=game_id,
        plate_appearance_id=plate_appearance_id,
        outcome=outcome,
        before=before,
        after=after,
        runs_scored=runs_scored,
        evidence_checksum=evidence_checksum,
        event_at=event_at,
        evidence_end_at=event_at,
        eligible_at=eligible_at,
        source_evidence_ids=(f"source-{observation_id}",),
        evidence_tier=evidence_tier,
    )


def _vector(**overrides: float) -> dict[PlateAppearanceOutcome, float]:
    values = {outcome: 0.0 for outcome in ordered_outcomes()}
    for name, value in overrides.items():
        values[PlateAppearanceOutcome[name]] = value
    return values


def _transition_model():
    rows: list[TransitionObservation] = []
    for before in BaseOutState.ordinary_states():
        strikeout_after = (
            BaseOutState.inning_end_state()
            if before.outs == 2
            else BaseOutState(before.outs + 1, before.base_mask)
        )
        rows.append(
            _observation(
                observation_id=f"k-{before.outs}-{before.base_mask}",
                plate_appearance_id=f"k-{before.outs}-{before.base_mask}",
                outcome=PlateAppearanceOutcome.STRIKEOUT,
                before=before,
                after=strikeout_after,
                runs_scored=0,
                evidence_checksum="1" * 64,
            )
        )
        rows.append(
            _observation(
                observation_id=f"hr-{before.outs}-{before.base_mask}",
                plate_appearance_id=f"hr-{before.outs}-{before.base_mask}",
                outcome=PlateAppearanceOutcome.HOME_RUN,
                before=before,
                after=BaseOutState(before.outs, 0),
                runs_scored=before.runners + 1,
                evidence_checksum="2" * 64,
            )
        )
    return fit_base_out_transition_model(
        rows,
        cutoff=FIT_CUTOFF,
        current_game_id="target-game",
    )


def _team(prefix: str, *, exit_immediately: bool = False) -> TeamSimulationInput:
    return TeamSimulationInput(
        team_id=prefix,
        batting_order=tuple(f"{prefix}-b{i}" for i in range(9)),
        starter_id=f"{prefix}-starter",
        starter_exit_hazards=(1.0 if exit_immediately else 0.0,),
        relievers=(RelieverOption(f"{prefix}-reliever", 1.0, max_batters=100),),
    )


def _game(*, exit_immediately: bool = False) -> GameSimulationInput:
    return GameSimulationInput(
        game_id="game-1",
        season=2025,
        away=_team("away"),
        home=_team("home", exit_immediately=exit_immediately),
        game_type="R",
        automatic_runner_start_inning=10,
    )


def test_all_24_states_and_inning_end_are_distinct() -> None:
    states = BaseOutState.ordinary_states()
    assert len(states) == 24
    assert len(set(states)) == 24
    assert all(not state.inning_end for state in states)
    assert BaseOutState.inning_end_state().inning_end
    with pytest.raises(SimulationError, match="clear the bases"):
        BaseOutState(3, 1)


def test_transition_conservation_and_posterior_probability_contract() -> None:
    model = _transition_model()
    assert model.observation_count == 48
    for state in BaseOutState.ordinary_states():
        for outcome in (PlateAppearanceOutcome.STRIKEOUT, PlateAppearanceOutcome.HOME_RUN):
            values = model.probabilities(outcome, state)
            assert sum(item.probability for item in values) == pytest.approx(1.0, abs=1e-12)
    with pytest.raises(SimulationError, match="does not conserve"):
        _observation(
            observation_id="bad",
            plate_appearance_id="bad",
            outcome=PlateAppearanceOutcome.SINGLE,
            before=BaseOutState(0, 0),
            after=BaseOutState(0, 0),
            runs_scored=0,
            evidence_checksum="3" * 64,
        )
    with pytest.raises(SimulationError, match="home-run transition"):
        _observation(
            observation_id="bad-hr",
            plate_appearance_id="bad-hr",
            outcome=PlateAppearanceOutcome.HOME_RUN,
            before=BaseOutState(0, 1),
            after=BaseOutState(0, 2),
            runs_scored=1,
            evidence_checksum="4" * 64,
        )
    # A scored strikeout may leave the batter safe on a dropped third strike.
    dropped_third = _observation(
        observation_id="dropped-third",
        plate_appearance_id="dropped-third",
        outcome=PlateAppearanceOutcome.STRIKEOUT,
        before=BaseOutState(2, 0),
        after=BaseOutState(2, 1),
        runs_scored=0,
        evidence_checksum="5" * 64,
    )
    assert dropped_third.after == BaseOutState(2, 1)


def test_event_vector_is_exact_and_fails_closed() -> None:
    valid = _vector(STRIKEOUT=0.7, HOME_RUN=0.3)
    assert sum(validate_outcome_probabilities(valid)) == pytest.approx(1.0)
    missing = dict(valid)
    missing.pop(PlateAppearanceOutcome.TRIPLE)
    with pytest.raises(SimulationError, match="missing or unexpected"):
        validate_outcome_probabilities(missing)
    invalid = dict(valid)
    invalid[PlateAppearanceOutcome.STRIKEOUT] = 0.6
    with pytest.raises(SimulationError, match="sum to one"):
        validate_outcome_probabilities(invalid)


def test_simulator_cycles_order_changes_pitcher_and_walks_off_in_extras() -> None:
    model = _transition_model()

    def provider(context):
        if context.inning < 10:
            return _vector(STRIKEOUT=1.0)
        if context.top:
            return _vector(STRIKEOUT=1.0)
        return _vector(HOME_RUN=1.0)

    result = simulate_once(
        _game(exit_immediately=True),
        model,
        provider,
        seed="fixed",
        capture_trace=True,
    )
    assert (result.away_runs, result.home_runs, result.innings) == (0, 2, 10)
    assert result.trace[0].pitcher_id == "home-starter"
    assert result.trace[1].pitcher_id == "home-reliever"
    away_batters = [row.batter_id for row in result.trace if row.top]
    assert away_batters[:10] == [f"away-b{i}" for i in range(9)] + ["away-b0"]
    final = result.trace[-1]
    assert not final.top and final.inning == 10
    assert final.before.base_mask == 2
    assert final.outcome is PlateAppearanceOutcome.HOME_RUN


def test_game_summary_is_seed_stable_and_has_no_ties() -> None:
    model = _transition_model()

    def provider(context):
        if context.inning < 9:
            return _vector(STRIKEOUT=1.0)
        if context.top:
            return _vector(STRIKEOUT=1.0)
        return _vector(HOME_RUN=1.0)

    first = simulate_game(
        _game(),
        model,
        provider,
        simulations=25,
        seed="stable",
        probability_provider_identity_checksum=PROVIDER_IDENTITY,
    )
    second = simulate_game(
        _game(),
        model,
        provider,
        simulations=25,
        seed="stable",
        probability_provider_identity_checksum=PROVIDER_IDENTITY,
    )
    assert first == second
    assert first.checksum == second.checksum
    assert first.home_win_probability == 1.0
    assert first.away_win_probability == 0.0
    assert sum(row.probability for row in first.score_distribution) == pytest.approx(1.0)
    assert all(row.home_runs != row.away_runs for row in first.score_distribution)


def test_exhausted_bullpen_fails_instead_of_reentering_a_pitcher() -> None:
    model = _transition_model()
    home = TeamSimulationInput(
        team_id="home",
        batting_order=tuple(f"home-b{i}" for i in range(9)),
        starter_id="home-starter",
        starter_exit_hazards=(1.0,),
        relievers=(RelieverOption("home-only-reliever", 1.0, max_batters=1),),
    )
    game = GameSimulationInput(
        game_id="exhausted",
        season=2025,
        away=_team("away"),
        home=home,
        game_type="R",
        automatic_runner_start_inning=10,
    )

    def provider(context):
        return _vector(HOME_RUN=1.0) if context.top else _vector(STRIKEOUT=1.0)

    with pytest.raises(SimulationError, match="re-entry is forbidden"):
        simulate_once(game, model, provider, seed="no-reentry")


def test_non_home_run_walkoff_credits_only_runs_through_the_winner() -> None:
    base = _transition_model()
    distributions = dict(base.distributions)
    distributions[(PlateAppearanceOutcome.WALK_HBP_CI, BaseOutState(0, 0))] = (
        TransitionProbability(TransitionResult(BaseOutState(0, 1), 0), 1.0),
    )
    distributions[(PlateAppearanceOutcome.WALK_HBP_CI, BaseOutState(0, 1))] = (
        TransitionProbability(TransitionResult(BaseOutState(0, 3), 0), 1.0),
    )
    distributions[(PlateAppearanceOutcome.WALK_HBP_CI, BaseOutState(0, 3))] = (
        TransitionProbability(TransitionResult(BaseOutState(0, 7), 0), 1.0),
    )
    distributions[(PlateAppearanceOutcome.SINGLE, BaseOutState(0, 7))] = (
        TransitionProbability(TransitionResult(BaseOutState(0, 1), 3), 1.0),
    )
    model = BaseOutTransitionModel(
        version=base.version,
        alpha=base.alpha,
        distributions=distributions,
        evidence_checksum=base.evidence_checksum,
        observation_count=base.observation_count + 4,
        fit_cutoff=base.fit_cutoff,
        source_evidence_ids=base.source_evidence_ids,
    )

    def provider(context):
        if context.inning < 9:
            return _vector(STRIKEOUT=1.0)
        if context.top:
            return (
                _vector(HOME_RUN=1.0)
                if context.state == BaseOutState(0, 0) and context.batting_score == 0
                else _vector(STRIKEOUT=1.0)
            )
        if context.state.base_mask in {0, 1, 3}:
            return _vector(WALK_HBP_CI=1.0)
        return _vector(SINGLE=1.0)

    result = simulate_once(_game(), model, provider, seed="walkoff", capture_trace=True)
    assert (result.away_runs, result.home_runs) == (1, 2)
    assert result.trace[-1].outcome is PlateAppearanceOutcome.SINGLE
    assert result.trace[-1].runs_scored == 2
    assert result.trace[-1].after is None
    assert result.trace[-1].terminal_state_unobserved is True


def test_one_million_draw_event_frequency_gate() -> None:
    expected = _vector(STRIKEOUT=0.7, HOME_RUN=0.3)
    actual = sample_outcome_frequencies(expected, draws=1_000_000, seed="frequency")
    for outcome, probability in expected.items():
        sigma = sqrt(probability * (1 - probability) / 1_000_000)
        tolerance = max(0.002, 5 * sigma)
        assert abs(actual[outcome] - probability) <= tolerance


def test_transition_fit_excludes_target_future_and_weaker_tier_evidence() -> None:
    before = BaseOutState(0, 0)
    rows = (
        _observation(
            observation_id="eligible",
            plate_appearance_id="eligible",
            outcome=PlateAppearanceOutcome.STRIKEOUT,
            before=before,
            after=BaseOutState(1, 0),
            runs_scored=0,
            evidence_checksum="1" * 64,
        ),
        _observation(
            observation_id="target",
            plate_appearance_id="target",
            outcome=PlateAppearanceOutcome.STRIKEOUT,
            before=before,
            after=BaseOutState(1, 0),
            runs_scored=0,
            evidence_checksum="2" * 64,
            game_id="target-game",
        ),
        _observation(
            observation_id="future",
            plate_appearance_id="future",
            outcome=PlateAppearanceOutcome.STRIKEOUT,
            before=before,
            after=BaseOutState(1, 0),
            runs_scored=0,
            evidence_checksum="3" * 64,
            event_at=FIT_CUTOFF + timedelta(seconds=1),
            eligible_at=FIT_CUTOFF + timedelta(seconds=1),
        ),
        _observation(
            observation_id="tier-b",
            plate_appearance_id="tier-b",
            outcome=PlateAppearanceOutcome.STRIKEOUT,
            before=before,
            after=BaseOutState(1, 0),
            runs_scored=0,
            evidence_checksum="4" * 64,
            evidence_tier="B",
        ),
    )
    model = fit_base_out_transition_model(
        rows,
        cutoff=FIT_CUTOFF,
        current_game_id="target-game",
    )
    assert model.observation_count == 1
    assert model.source_evidence_ids == ("source-eligible",)
    assert model.support_coverage == pytest.approx(1 / 192)


def test_summary_checksum_binds_lines_and_probability_provider_identity() -> None:
    model = _transition_model()

    def provider(context):
        if context.inning < 9 or context.top:
            return _vector(STRIKEOUT=1.0)
        return _vector(HOME_RUN=1.0)

    first = simulate_game(
        _game(),
        model,
        provider,
        simulations=5,
        seed="checksum",
        probability_provider_identity_checksum="a" * 64,
    )
    changed_line_game = GameSimulationInput(
        **{**_game().__dict__, "total_line": 7.5}
    )
    changed_line = simulate_game(
        changed_line_game,
        model,
        provider,
        simulations=5,
        seed="checksum",
        probability_provider_identity_checksum="a" * 64,
    )
    changed_provider = simulate_game(
        _game(),
        model,
        provider,
        simulations=5,
        seed="checksum",
        probability_provider_identity_checksum="b" * 64,
    )
    assert first.checksum != changed_line.checksum
    assert first.checksum != changed_provider.checksum

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.gen2.dynamic_state import (
    HALF_LIFE_CANDIDATES_DAYS,
    DynamicMetricObservation,
    DynamicStateVariant,
    EvidenceKind,
    build_dynamic_state_candidates,
    estimate_dynamic_state,
)
from app.gen2.event_taxonomy import PlateAppearanceOutcome
from app.gen2.platoon import (
    PlatoonObservation,
    PlatoonRatePrior,
    combine_batter_pitcher_platoon,
    estimate_player_platoon,
)
from app.gen2.talent import (
    PA_EVENT_CATEGORIES,
    BetaPrior,
    BinaryMetricObservation,
    ContinuousMetricObservation,
    NormalPrior,
    PlateAppearanceEvent,
    PriorRateProfile,
    SeasonEventSummary,
    build_hierarchical_pa_prior,
    build_multiseason_prior_candidates,
    build_weighted_multiseason_prior,
    estimate_pa_event_posterior,
    update_beta_posterior,
    update_normal_posterior,
)


CUTOFF = datetime(2025, 7, 1, 23, 0, tzinfo=UTC)
RATES = (0.21, 0.10, 0.035, 0.14, 0.05, 0.005, 0.45, 0.01)
STATIC_LINEAGE = {
    "static_evidence_cutoff": datetime(2025, 6, 1, tzinfo=UTC),
    "static_evidence_checksum": "7" * 64,
    "static_source_evidence_ids": ("static-player-posterior",),
}


def _platoon_prior(probability: float, suffix: str) -> PlatoonRatePrior:
    return PlatoonRatePrior(
        probability=probability,
        evidence_cutoff=datetime(2025, 6, 1, tzinfo=UTC),
        evidence_checksum=suffix * 64,
        source_evidence_ids=(f"platoon-prior-{suffix}",),
    )


def _prior(*, rookie: bool = False):
    prior_cutoff = datetime(2025, 2, 28, tzinfo=UTC)
    def profile(name: str, rates, sample: float, checksum_digit: str):
        return PriorRateProfile(
            name,
            rates,
            sample,
            prior_cutoff,
            checksum_digit * 64,
            (f"{name}-evidence",),
        )
    return build_hierarchical_pa_prior(
        league=profile("league", RATES, 140, "1"),
        role=profile("starter" if not rookie else "hitter", RATES, 40, "2"),
        handedness=profile("throws_r", RATES, 20, "3"),
        rookie=profile(
            "rookie",
            (0.25, 0.10, 0.025, 0.14, 0.045, 0.01, 0.42, 0.01),
            60,
            "4",
        ),
        is_rookie=rookie,
        evidence_cutoff=datetime(2025, 3, 1, tzinfo=UTC),
    )


def _pa(
    index: int,
    *,
    outcome: str = PlateAppearanceOutcome.HOME_RUN.value,
    player_id: str = "player-1",
    player_role: str = "hitter",
    team_id: str = "team-a",
    game_id: str | None = None,
    pa_id: str | None = None,
    event_at: datetime | None = None,
    eligible_at: datetime | None = None,
) -> PlateAppearanceEvent:
    occurred = event_at or CUTOFF - timedelta(hours=index + 1)
    return PlateAppearanceEvent(
        event_id=f"event-{index}",
        plate_appearance_id=pa_id or f"pa-{index}",
        game_id=game_id or f"game-{index}",
        player_id=player_id,
        player_role=player_role,
        team_id=team_id,
        season=2025,
        event_at=occurred,
        eligible_at=eligible_at or occurred,
        outcome=outcome,
    )


def _dynamic(
    index: int,
    *,
    value: float | None,
    kind: EvidenceKind = EvidenceKind.UNDERLYING_SKILL,
    game_id: str | None = None,
    pa_id: str | None = None,
    event_at: datetime | None = None,
    eligible_at: datetime | None = None,
    team_id: str = "team-a",
) -> DynamicMetricObservation:
    occurred = event_at or CUTOFF - timedelta(days=index + 1)
    return DynamicMetricObservation(
        observation_id=f"dynamic-{index}",
        player_id="player-1",
        team_id=team_id,
        game_id=game_id or f"game-{index}",
        plate_appearance_id=pa_id or f"pa-{index}",
        pitch_id=f"pitch-{index}",
        event_at=occurred,
        eligible_at=eligible_at or occurred,
        metric_name="exit_velocity",
        evidence_kind=kind,
        value=value,
    )


def _platoon(
    index: int,
    *,
    success: bool | None,
    batter_id: str = "batter-1",
    pitcher_id: str = "pitcher-1",
    batter_team_id: str = "team-a",
    game_id: str | None = None,
    pa_id: str | None = None,
) -> PlatoonObservation:
    occurred = CUTOFF - timedelta(days=index + 1)
    return PlatoonObservation(
        observation_id=f"split-{index}",
        plate_appearance_id=pa_id or f"split-pa-{index}",
        game_id=game_id or f"split-game-{index}",
        event_at=occurred,
        eligible_at=occurred,
        batter_id=batter_id,
        pitcher_id=pitcher_id,
        batter_team_id=batter_team_id,
        pitcher_team_id="pitch-team",
        batter_hand="L",
        pitcher_hand="R",
        metric_name="home_run",
        success=success,
    )


def test_dirichlet_posterior_shrinks_small_samples_and_preserves_uncertainty() -> None:
    prior = _prior()
    small = estimate_pa_event_posterior(
        (_pa(index) for index in range(4)),
        player_id="player-1",
        player_role="hitter",
        cutoff=CUTOFF,
        prior=prior,
        current_game_id="target-game",
    )
    large = estimate_pa_event_posterior(
        (
            _pa(
                index,
                outcome=(
                    PlateAppearanceOutcome.HOME_RUN.value
                    if index < 30
                    else PlateAppearanceOutcome.BIP_OUT.value
                ),
            )
            for index in range(300)
        ),
        player_id="player-1",
        player_role="hitter",
        cutoff=CUTOFF,
        prior=prior,
        current_game_id="target-game",
    )
    home_run = PlateAppearanceOutcome.HOME_RUN.value
    prior_hr = prior.probabilities[home_run]
    assert prior_hr < small.probabilities[home_run] < large.probabilities[home_run] < 1
    assert large.posterior_variances[home_run] < small.posterior_variances[home_run]
    assert small.reliability < large.reliability
    assert sum(small.probabilities.values()) == pytest.approx(1.0)


def test_rookie_and_unseen_player_remain_explicit_population_priors() -> None:
    rookie_prior = _prior(rookie=True)
    unseen = estimate_pa_event_posterior(
        (),
        player_id="rookie-1",
        player_role="hitter",
        cutoff=CUTOFF,
        prior=rookie_prior,
        current_game_id="target-game",
    )
    assert rookie_prior.component_names == ("league", "hitter", "throws_r", "rookie")
    assert unseen.probabilities == pytest.approx(rookie_prior.probabilities)
    assert unseen.observed_sample_size == 0
    assert unseen.reliability == 0
    assert all(value > 0 for value in unseen.variances)


def test_player_identity_spans_trade_teams_and_checksum_is_order_independent() -> None:
    events = (
        _pa(1, team_id="old-team"),
        _pa(2, team_id="new-team", outcome=PlateAppearanceOutcome.SINGLE.value),
        _pa(3, player_id="different-player", team_id="new-team"),
        _pa(4, player_role="pitcher", team_id="new-team"),
    )
    first = estimate_pa_event_posterior(
        events,
        player_id="player-1",
        player_role="hitter",
        cutoff=CUTOFF,
        prior=_prior(),
        current_game_id="target-game",
    )
    reversed_result = estimate_pa_event_posterior(
        reversed(events),
        player_id="player-1",
        player_role="hitter",
        cutoff=CUTOFF,
        prior=_prior(),
        current_game_id="target-game",
    )
    assert first.observed_sample_size == 2
    assert first.team_ids == ("new-team", "old-team")
    assert first.identity_scope == "player_id_across_teams"
    assert first.checksum == reversed_result.checksum
    assert first.evidence_checksum == reversed_result.evidence_checksum


def test_current_game_current_pa_and_cutoff_boundary_are_excluded() -> None:
    events = (
        _pa(1, outcome=PlateAppearanceOutcome.SINGLE.value),
        _pa(2, game_id="target-game"),
        _pa(3, pa_id="target-pa"),
        _pa(4, event_at=CUTOFF),
        _pa(
            5,
            event_at=CUTOFF - timedelta(minutes=2),
            eligible_at=CUTOFF + timedelta(seconds=1),
        ),
    )
    result = estimate_pa_event_posterior(
        events,
        player_id="player-1",
        player_role="hitter",
        cutoff=CUTOFF,
        prior=_prior(),
        current_game_id="target-game",
        current_plate_appearance_id="target-pa",
    )
    assert result.observed_sample_size == 1
    single_index = PA_EVENT_CATEGORIES.index(PlateAppearanceOutcome.SINGLE.value)
    assert result.observed_counts[single_index] == 1


def test_multiseason_prior_is_chronological_and_rejects_future_seasons() -> None:
    summaries = tuple(
        SeasonEventSummary(
            season=season,
            counts=(10 + season - 2022, 4, 2, 8, 3, 1, 20, 1),
            evidence_cutoff=datetime(season, 11, 1, tzinfo=UTC),
            evidence_id=f"season-{season}",
        )
        for season in (2022, 2023, 2024)
    )
    candidates = build_multiseason_prior_candidates(
        base_prior=_prior(),
        summaries=summaries,
        target_season=2025,
        evidence_cutoff=datetime(2025, 3, 1, tzinfo=UTC),
    )
    assert "season_2024@1" in candidates[1].component_names
    assert all("2023" not in item for item in candidates[1].component_names)
    assert any("season_2023@0.65" == item for item in candidates[2].component_names)
    assert candidates[1].effective_sample_size < candidates[3].effective_sample_size

    future = SeasonEventSummary(
        season=2025,
        counts=(1, 1, 1, 1, 1, 1, 1, 1),
        evidence_cutoff=datetime(2025, 6, 1, tzinfo=UTC),
        evidence_id="future",
    )
    with pytest.raises(ValueError, match="current- or future-season"):
        build_weighted_multiseason_prior(
            base_prior=_prior(),
            summaries=(*summaries, future),
            target_season=2025,
            seasons=3,
            evidence_cutoff=CUTOFF,
        )


def test_beta_and_normal_helpers_track_missingness_reliability_and_variance() -> None:
    binary = tuple(
        BinaryMetricObservation(
            observation_id=f"binary-{index}",
            player_id="player-1",
            team_id="team-a",
            game_id=f"game-{index}",
            plate_appearance_id=f"pa-{index}",
            event_at=CUTOFF - timedelta(days=index + 1),
            eligible_at=CUTOFF - timedelta(days=index + 1),
            value=value,
        )
        for index, value in enumerate((True, False, True, None))
    )
    beta = update_beta_posterior(
        binary,
        player_id="player-1",
        cutoff=CUTOFF,
        prior=BetaPrior(
            3,
            7,
            datetime(2025, 3, 1, tzinfo=UTC),
            "5" * 64,
            ("league-whiff-evidence",),
            "league_whiff",
        ),
        current_game_id="target-game",
    )
    assert beta.observed_sample_size == 3
    assert beta.missing_observation_count == 1
    assert beta.mean == pytest.approx(5 / 13)
    assert 0 < beta.variance < 0.25
    assert beta.reliability == pytest.approx(3 / 13)

    continuous = tuple(
        ContinuousMetricObservation(
            observation_id=f"continuous-{index}",
            player_id="player-1",
            team_id="team-a",
            game_id=f"game-{index}",
            plate_appearance_id=f"pa-{index}",
            event_at=CUTOFF - timedelta(days=index + 1),
            eligible_at=CUTOFF - timedelta(days=index + 1),
            value=value,
        )
        for index, value in enumerate((92.0, 94.0, None))
    )
    normal = update_normal_posterior(
        continuous,
        player_id="player-1",
        cutoff=CUTOFF,
        prior=NormalPrior(
            90.0,
            4.0,
            20,
            datetime(2025, 3, 1, tzinfo=UTC),
            "6" * 64,
            ("league-velocity-evidence",),
            "league_velocity",
        ),
        observation_variance=4.0,
        current_game_id="target-game",
    )
    assert 90 < normal.mean < 93
    assert normal.variance < 4
    assert normal.observed_sample_size == 2
    assert normal.missing_observation_count == 1
    assert normal.reliability == pytest.approx(2 / 22)


def test_dynamic_variants_are_static_outcome_and_underlying_with_declared_half_lives() -> None:
    candidates = build_dynamic_state_candidates(
        (
            _dynamic(1, value=95.0),
            _dynamic(2, value=1.0, kind=EvidenceKind.OUTCOME),
        ),
        player_id="player-1",
        metric_name="exit_velocity",
        cutoff=CUTOFF,
        current_game_id="target-game",
        static_value=90.0,
        static_variance=4.0,
        **STATIC_LINEAGE,
    )
    assert len(candidates) == 7
    assert candidates[0].variant is DynamicStateVariant.STATIC_TRUE_TALENT
    assert {
        item.half_life_days for item in candidates if item.half_life_days is not None
    } == set(HALF_LIFE_CANDIDATES_DAYS)
    assert {item.variant for item in candidates} == {
        DynamicStateVariant.STATIC_TRUE_TALENT,
        DynamicStateVariant.OUTCOME_EWMA,
        DynamicStateVariant.UNDERLYING_SKILL_EWMA,
    }
    assert all("streak" not in item.method and "momentum" not in item.method for item in candidates)


def test_dynamic_state_excludes_current_pa_later_pitch_and_outcome_evidence() -> None:
    rows = (
        _dynamic(1, value=96.0, team_id="old-team"),
        _dynamic(2, value=97.0, pa_id="target-pa"),
        _dynamic(3, value=99.0, event_at=CUTOFF + timedelta(microseconds=1)),
        _dynamic(4, value=1.0, kind=EvidenceKind.OUTCOME),
        _dynamic(5, value=None, team_id="new-team"),
        _dynamic(6, value=98.0, game_id="target-game"),
    )
    result = estimate_dynamic_state(
        rows,
        player_id="player-1",
        metric_name="exit_velocity",
        cutoff=CUTOFF,
        current_game_id="target-game",
        current_plate_appearance_id="target-pa",
        static_value=90.0,
        static_variance=4.0,
        **STATIC_LINEAGE,
        variant=DynamicStateVariant.UNDERLYING_SKILL_EWMA,
        half_life_days=14,
    )
    assert result.observed_sample_size == 1
    assert result.missing_observation_count == 1
    assert 90 < result.value < 96
    assert result.team_ids == ("old-team",)
    assert 0 < result.posterior_variance < 4


def test_dynamic_checksum_is_deterministic_and_missing_underlying_is_not_invented() -> None:
    rows = (_dynamic(1, value=None), _dynamic(2, value=None))
    first = estimate_dynamic_state(
        rows,
        player_id="player-1",
        metric_name="exit_velocity",
        cutoff=CUTOFF,
        current_game_id="target-game",
        static_value=90.0,
        static_variance=4.0,
        **STATIC_LINEAGE,
        variant=DynamicStateVariant.UNDERLYING_SKILL_EWMA,
        half_life_days=30,
    )
    second = estimate_dynamic_state(
        reversed(rows),
        player_id="player-1",
        metric_name="exit_velocity",
        cutoff=CUTOFF,
        current_game_id="target-game",
        static_value=90.0,
        static_variance=4.0,
        **STATIC_LINEAGE,
        variant=DynamicStateVariant.UNDERLYING_SKILL_EWMA,
        half_life_days=30,
    )
    assert first.value == 90.0
    assert first.observed_sample_size == 0
    assert first.missing_observation_count == 2
    assert first.reliability == 0
    assert first.checksum == second.checksum


def test_platoon_sparse_samples_pool_aggressively_and_trade_identity_is_stable() -> None:
    sparse_rows = (
        _platoon(1, success=True, batter_team_id="old-team"),
        _platoon(2, success=True, batter_team_id="new-team"),
    )
    large_rows = tuple(
        _platoon(index + 10, success=True, batter_team_id="new-team")
        for index in range(300)
    )
    sparse = estimate_player_platoon(
        sparse_rows,
        player_id="batter-1",
        player_role="batter",
        opponent_hand="R",
        metric_name="home_run",
        cutoff=CUTOFF,
        current_game_id="target-game",
        overall_prior=_platoon_prior(0.30, "a"),
        league_split_prior=_platoon_prior(0.32, "b"),
    )
    sparse_reversed = estimate_player_platoon(
        reversed(sparse_rows),
        player_id="batter-1",
        player_role="batter",
        opponent_hand="R",
        metric_name="home_run",
        cutoff=CUTOFF,
        current_game_id="target-game",
        overall_prior=_platoon_prior(0.30, "a"),
        league_split_prior=_platoon_prior(0.32, "b"),
    )
    established = estimate_player_platoon(
        large_rows,
        player_id="batter-1",
        player_role="batter",
        opponent_hand="R",
        metric_name="home_run",
        cutoff=CUTOFF,
        current_game_id="target-game",
        overall_prior=_platoon_prior(0.30, "a"),
        league_split_prior=_platoon_prior(0.32, "b"),
    )
    assert abs(sparse.probability - sparse.prior_probability) < 0.01
    assert established.probability > sparse.probability
    assert established.variance < sparse.variance
    assert sparse.team_ids == ("new-team", "old-team")
    assert sparse.checksum == sparse_reversed.checksum


def test_pitcher_platoon_and_matchup_combination_retain_uncertainty() -> None:
    rows = tuple(_platoon(index, success=index % 3 == 0) for index in range(20))
    batter = estimate_player_platoon(
        rows,
        player_id="batter-1",
        player_role="batter",
        opponent_hand="R",
        metric_name="home_run",
        cutoff=CUTOFF,
        current_game_id="target-game",
        overall_prior=_platoon_prior(0.30, "a"),
        league_split_prior=_platoon_prior(0.31, "b"),
    )
    pitcher = estimate_player_platoon(
        rows,
        player_id="pitcher-1",
        player_role="pitcher",
        opponent_hand="L",
        metric_name="home_run",
        cutoff=CUTOFF,
        current_game_id="target-game",
        overall_prior=_platoon_prior(0.29, "c"),
        league_split_prior=_platoon_prior(0.31, "d"),
    )
    matchup = combine_batter_pitcher_platoon(
        batter_split=batter,
        pitcher_split=pitcher,
        batter_hand="L",
        pitcher_hand="R",
    )
    assert 0 < matchup.probability < 1
    assert matchup.variance > 0
    assert matchup.variance != pytest.approx(batter.variance + pitcher.variance)
    assert len(matchup.checksum) == 64


def test_player_metric_values_and_platoon_priors_fail_closed() -> None:
    with pytest.raises(ValueError, match="binary metric values"):
        update_beta_posterior(
            (
                BinaryMetricObservation(
                    "bad-binary",
                    "player-1",
                    "team-a",
                    "game-1",
                    "pa-1",
                    CUTOFF - timedelta(days=1),
                    CUTOFF - timedelta(days=1),
                    "false",  # type: ignore[arg-type]
                ),
            ),
            player_id="player-1",
            cutoff=CUTOFF,
            prior=BetaPrior(
                2,
                3,
                CUTOFF - timedelta(days=2),
                "a" * 64,
                ("beta-prior",),
            ),
            current_game_id="target",
        )
    with pytest.raises(ValueError, match="dynamic metric values"):
        estimate_dynamic_state(
            (_dynamic(1, value=True),),  # type: ignore[arg-type]
            player_id="player-1",
            metric_name="exit_velocity",
            cutoff=CUTOFF,
            current_game_id="target",
            static_value=90,
            static_variance=4,
            variant=DynamicStateVariant.UNDERLYING_SKILL_EWMA,
            half_life_days=30,
            **STATIC_LINEAGE,
        )
    with pytest.raises(ValueError, match="platoon success"):
        estimate_player_platoon(
            (replace(_platoon(1, success=True), success="yes"),),  # type: ignore[arg-type]
            player_id="batter-1",
            player_role="batter",
            opponent_hand="R",
            metric_name="home_run",
            cutoff=CUTOFF,
            current_game_id="target-game",
            overall_prior=_platoon_prior(0.30, "a"),
            league_split_prior=_platoon_prior(0.31, "b"),
        )


def test_bounded_streaming_guards_reject_oversized_player_evidence() -> None:
    with pytest.raises(ValueError, match="bounded maximum"):
        estimate_pa_event_posterior(
            (_pa(index) for index in range(3)),
            player_id="player-1",
            player_role="hitter",
            cutoff=CUTOFF,
            prior=_prior(),
            current_game_id="target-game",
            max_observations=2,
        )

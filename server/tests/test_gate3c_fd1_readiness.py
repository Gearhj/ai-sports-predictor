from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from app.gen2.fd1_readiness import (
    Asp2025CanonicalSummary,
    ConservationConflict,
    DatasetPopulation,
    FD1ReadinessInput,
    GameConflictKind,
    GameReconciliationConflict,
    IdentityConflict,
    OutcomeCount,
    PopulationSeparationSummary,
    ReadinessDecision,
    SeasonImportSummary,
    SourceFileChecksumIssue,
    SourceSummary,
    TemporalAuditSummary,
    TemporalFoldEvidence,
    TemporalLeakage,
    TemporalLeakageKind,
    TierLeakage,
    TrainingEligibilitySummary,
    audit_fd1_readiness,
)
from app.gen2.event_taxonomy import ordered_outcomes


def _outcomes(each: int) -> tuple[OutcomeCount, ...]:
    return tuple(OutcomeCount(outcome, each) for outcome in ordered_outcomes())


def _season(season: int) -> SeasonImportSummary:
    primary = season >= 2023
    games = 2_430 if primary else 100
    return SeasonImportSummary(
        season=season,
        population=(DatasetPopulation.PRIMARY if primary else DatasetPopulation.PRIOR_ONLY),
        expected_regular_season_games=games,
        imported_regular_season_games=games,
        valid_final_games=games,
        home_win_games=games // 2,
        away_win_games=games - games // 2,
        plate_appearance_games=games,
        plate_appearances=800,
        transition_games=games,
        transitions=10_000,
        identity_unique_player_count=100,
        resolved_identity_unique_player_count=100,
        identity_reference_count=1_000,
        resolved_identity_reference_count=1_000,
        complete_lineup_games=games,
        lineup_timing_risk_games=games,
        starter_games=games,
        starter_historical_reconstruction_games=games,
        starter_duration_rows=games * 2,
        bullpen_usage_games=games,
        bullpen_usage_rows=games * 6,
        fielding_games=games,
        fielding_rows=games * 18,
        weather_games=games,
        pitch_sequence_games=games,
        taxonomy_terminal_events=800,
        taxonomy_mapped_terminal_events=800,
        taxonomy_outcome_counts=_outcomes(100),
        player_prior_rows=500,
        park_factor_rows=30,
        tier_candidate_rows=120 if primary else 100,
        tier_eligible_rows=120 if primary else 100,
        strict_deployable_rows=100 if primary else 0,
        enhanced_research_rows=20 if primary else 0,
        prior_only_rows=0 if primary else 100,
    )


def _evidence() -> FD1ReadinessInput:
    seasons = tuple(_season(season) for season in range(2021, 2026))
    return FD1ReadinessInput(
        sources=(
            SourceSummary("RETROSHEET", "2025", 7, 7, attribution_required=True),
            SourceSummary("CHADWICK", "abc123", 20, 20, attribution_required=True),
            SourceSummary("LAHMAN", "2025", 19, 19, attribution_required=True),
        ),
        seasons=seasons,
        asp_2025=Asp2025CanonicalSummary(2_430, 2_430, 2_430, 2_430),
        population_separation=PopulationSeparationSummary(
            tier_candidate_row_count=560,
            tier_eligible_row_count=560,
            strict_row_count=300,
            enhanced_row_count=60,
            prior_only_row_count=200,
            historical_actual_lineup_rows=300,
            isolated_enhanced_lineup_rows=300,
            historical_actual_starter_rows=300,
            isolated_enhanced_starter_rows=300,
        ),
        temporal=TemporalAuditSummary(
            chronology_checked_rows=50_000,
            folds=(
                TemporalFoldEvidence(
                    "train-2023-validate-2024",
                    date(2023, 12, 31),
                    date(2024, 3, 20),
                    date(2024, 9, 30),
                    10_000,
                    5_000,
                ),
                TemporalFoldEvidence(
                    "train-through-2024-validate-2025",
                    date(2024, 12, 31),
                    date(2025, 3, 20),
                    date(2025, 9, 30),
                    20_000,
                    5_000,
                ),
            ),
        ),
        training=TrainingEligibilitySummary(
            event_training_rows=800,
            event_outcome_counts=_outcomes(100),
            transition_training_rows=10_000,
            transition_state_outcome_cells=150,
            simulator_validation_games=500,
            starter_duration_rows=7_290,
            bullpen_usage_rows=20_000,
            chronological_player_prior_rows=2_000,
            prior_period_park_factor_rows=90,
        ),
    )


def _replace_season(
    evidence: FD1ReadinessInput,
    season: int,
    **changes,
) -> FD1ReadinessInput:
    return replace(
        evidence,
        seasons=tuple(
            replace(row, **changes) if row.season == season else row
            for row in evidence.seasons
        ),
    )


def test_complete_evidence_is_ready_and_preserves_exact_reconciliation() -> None:
    result = audit_fd1_readiness(_evidence())

    assert result.decision is ReadinessDecision.READY_FOR_GEN2_FREE_DATA_MODELING
    assert result.modeling_can_proceed
    assert [row.season for row in result.season_reconciliation] == [
        2021,
        2022,
        2023,
        2024,
        2025,
    ]
    assert result.season(2025).games_complete
    assert result.season(2025).lineups.ratio == 1.0
    assert result.answer("event_model_trainable").passed
    assert result.answer("simulator_trainable_and_validatable").passed
    assert {"pitch_velocity", "xwoba", "tracking_based_defense"} <= set(
        result.statcast_gaps
    )
    payload = result.as_dict()
    assert payload["decision"] == "READY_FOR_GEN2_FREE_DATA_MODELING"
    assert payload["season_reconciliation"][4]["season"] == 2025


def test_sufficient_but_partial_free_data_is_ready_with_exact_limitations() -> None:
    evidence = _replace_season(
        _evidence(),
        2023,
        weather_games=2_200,
        pitch_sequence_games=1_000,
        resolved_identity_reference_count=999,
        unresolved_identity_ids=("retro-unresolved-1",),
    )
    result = audit_fd1_readiness(evidence)

    assert result.decision is ReadinessDecision.READY_WITH_DOCUMENTED_FREE_DATA_LIMITATIONS
    assert result.modeling_can_proceed
    assert result.answer("player_ids_resolved").passed
    assert result.answer("weather_coverage_sufficient").passed
    joined = "\n".join(result.documented_limitations)
    assert "2023: weather coverage is 2200/2430" in joined
    assert "2023: player identity coverage is 999/1000" in joined
    assert "Retrosheet pitch sequences" in joined


def test_primary_unique_and_reference_identity_rates_are_both_gated() -> None:
    evidence = _replace_season(
        _evidence(),
        2024,
        identity_unique_player_count=1_000,
        resolved_identity_unique_player_count=994,
    )

    result = audit_fd1_readiness(evidence)

    assert result.decision is ReadinessDecision.NOT_READY_DATA_INTEGRITY_FAILURE
    assert not result.answer("player_ids_resolved").passed
    assert "unique_players=" in result.answer("player_ids_resolved").evidence
    assert any(
        "unique player identity coverage is 994/1000" in row
        for row in result.documented_limitations
    )


def test_each_season_must_project_nonempty_rows_into_its_allowed_tiers() -> None:
    evidence = _replace_season(
        _evidence(),
        2024,
        tier_candidate_rows=0,
        tier_eligible_rows=0,
        strict_deployable_rows=0,
        enhanced_research_rows=0,
    )
    evidence = replace(
        evidence,
        population_separation=replace(
            evidence.population_separation,
            tier_candidate_row_count=440,
            tier_eligible_row_count=440,
            strict_row_count=200,
            enhanced_row_count=40,
        ),
    )

    result = audit_fd1_readiness(evidence)

    assert result.decision is ReadinessDecision.NOT_READY_DATA_INTEGRITY_FAILURE
    assert not result.answer("strict_enhanced_prior_only_separated").passed
    assert any(
        "2024: primary tier projection must contain both strict and enhanced rows"
        in row
        for row in result.integrity_failures
    )


def test_incomplete_primary_games_fail_closed() -> None:
    evidence = _replace_season(
        _evidence(),
        2024,
        imported_regular_season_games=2_429,
        valid_final_games=2_429,
        home_win_games=1_214,
        plate_appearance_games=2_429,
        transition_games=2_429,
        complete_lineup_games=2_429,
        lineup_timing_risk_games=2_429,
        starter_games=2_429,
        starter_historical_reconstruction_games=2_429,
        bullpen_usage_games=2_429,
        fielding_games=2_429,
        weather_games=2_429,
        pitch_sequence_games=2_429,
    )
    result = audit_fd1_readiness(evidence)

    assert result.decision is ReadinessDecision.NOT_READY_DATA_INTEGRITY_FAILURE
    assert not result.modeling_can_proceed
    assert any(
        "2024: incomplete modern game population" in row
        for row in result.integrity_failures
    )
    assert not result.answer("primary_games_complete").passed


def test_checksum_identity_conservation_and_tier_leakage_are_exact_hard_stops() -> None:
    issue = SourceFileChecksumIssue(
        "plays.csv",
        "a" * 64,
        "b" * 64,
        "payload changed after publication",
    )
    identity = IdentityConflict(
        "identity-1",
        2025,
        "RETROSHEET",
        "smith001",
        ("chadwick-a", "chadwick-b"),
        ("game-1",),
    )
    conservation = ConservationConflict(
        "conservation-1",
        2024,
        "2024-07",
        "game-2",
        "play-9",
        "RUNNER_CONSERVATION",
        "one runner was created",
    )
    tier = TierLeakage(
        "tier-1",
        "lineup-row-1",
        "TIER_B_ENHANCED",
        "STRICT_DEPLOYABLE",
        "historical actual lineup entered strict data",
    )
    base = _evidence()
    evidence = replace(
        base,
        sources=(
            replace(
                base.sources[0],
                checksum_verified_artifact_count=6,
                checksum_issues=(issue,),
            ),
        )
        + base.sources[1:],
        identity_conflicts=(identity,),
        conservation_conflicts=(conservation,),
        population_separation=replace(base.population_separation, leakage=(tier,)),
    )
    result = audit_fd1_readiness(evidence)

    assert result.decision is ReadinessDecision.NOT_READY_DATA_INTEGRITY_FAILURE
    assert result.checksum_issues == (issue,)
    assert result.identity_conflicts == (identity,)
    assert result.conservation_conflicts == (conservation,)
    assert result.tier_leakage == (tier,)
    joined = "\n".join(result.integrity_failures)
    assert "checksums verified 6/7" in joined
    assert "identity-1" in joined
    assert "conservation-1" in joined
    assert "tier-1" in joined


def test_temporal_leakage_and_missing_event_class_block_training() -> None:
    leak = TemporalLeakage(
        "time-1",
        "feature-row-1",
        TemporalLeakageKind.CURRENT_GAME,
        date(2025, 7, 1),
        date(2025, 7, 1),
        "current-game final entered its own feature row",
    )
    base = _evidence()
    missing_triple = tuple(
        row for row in _outcomes(100) if row.outcome != "TRIPLE"
    )
    evidence = replace(
        base,
        temporal=replace(base.temporal, leakage=(leak,)),
        training=replace(
            base.training,
            event_training_rows=700,
            event_outcome_counts=missing_triple,
        ),
    )
    result = audit_fd1_readiness(evidence)

    assert result.decision is ReadinessDecision.NOT_READY_DATA_INTEGRITY_FAILURE
    assert result.temporal_leakage == (leak,)
    assert not result.answer("rolling_temporal_folds_formable").passed
    assert not result.answer("event_model_trainable").passed
    assert "outcomes_present=7/8" in result.answer("event_model_trainable").evidence


def test_2025_documented_difference_is_limitation_but_score_conflict_is_hard() -> None:
    starter = GameReconciliationConflict(
        "starter-diff",
        "NYA202504010",
        "asp-1",
        GameConflictKind.STARTER,
        "pitcher-a",
        "pitcher-b",
        "sources disagree on starter",
    )
    base = _evidence()
    result = audit_fd1_readiness(
        replace(
            base,
            asp_2025=replace(base.asp_2025, exact_match_games=2_429, conflicts=(starter,)),
        )
    )
    assert result.decision is ReadinessDecision.READY_WITH_DOCUMENTED_FREE_DATA_LIMITATIONS
    assert result.game_conflicts == (starter,)

    score = replace(
        starter,
        conflict_id="score-diff",
        kind=GameConflictKind.FINAL_SCORE,
        retrosheet_value="4-3",
        asp_value="3-4",
    )
    failed = audit_fd1_readiness(
        replace(
            base,
            asp_2025=replace(base.asp_2025, exact_match_games=2_429, conflicts=(score,)),
        )
    )
    assert failed.decision is ReadinessDecision.NOT_READY_DATA_INTEGRITY_FAILURE
    assert any("score-diff" in row for row in failed.integrity_failures)


def test_structural_count_contract_rejects_impossible_summary() -> None:
    with pytest.raises(ValueError, match="resolved identity references"):
        replace(_season(2025), resolved_identity_reference_count=1_001)
    with pytest.raises(ValueError, match="weather_games exceeds expected games"):
        replace(_season(2025), weather_games=2_431)
    with pytest.raises(ValueError, match="classified exactly once"):
        replace(_season(2025), tier_eligible_rows=121)


def test_timing_risk_labels_and_training_count_reconciliation_fail_closed() -> None:
    base = _replace_season(
        _evidence(),
        2025,
        lineup_timing_risk_games=2_429,
        starter_historical_reconstruction_games=2_429,
    )
    evidence = replace(
        base,
        training=replace(base.training, transition_training_rows=100_000),
    )

    result = audit_fd1_readiness(evidence)

    assert result.decision is ReadinessDecision.NOT_READY_DATA_INTEGRITY_FAILURE
    joined = "\n".join(result.integrity_failures)
    assert "HISTORICAL_ACTUAL_LINEUP_TIMING_RISK (2429/2430)" in joined
    assert "ACTUAL_STARTER_HISTORICAL_RECONSTRUCTION (2429/2430)" in joined
    assert "eligible=100000, imported=30000" in joined

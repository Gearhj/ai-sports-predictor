from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
import json

import joblib
import pytest

from app.modeling import ModelingConfig, UnlabeledGame, run_temporal_model_development


@dataclass(frozen=True)
class TrainingRowFixture:
    game_id: str
    scheduled_first_pitch: str
    data_as_of: str
    lifecycle: str
    feature_snapshot_id: str
    feature_snapshot_checksum: str
    feature_version_id: str
    feature_version: str
    feature_schema_version: str
    source_mode: str
    source_evidence: tuple[dict, ...]
    home_team_id: str
    away_team_id: str
    features: dict[str, float]
    missing_features: tuple[str, ...]
    stale_features: tuple[str, ...]
    provisional_features: tuple[str, ...]
    feature_completeness: float
    home_win: int
    home_runs: int
    away_runs: int
    run_differential: int
    total_runs: int
    market_home_probability: float
    market_timestamp: str
    market_books: int
    closing_home_probability: float | None = None
    closing_timestamp: str | None = None


START = datetime(2024, 4, 1, 19, tzinfo=UTC)


def _training_rows(days: int = 50) -> list[TrainingRowFixture]:
    rows: list[TrainingRowFixture] = []
    for day in range(days):
        for game in range(2):
            starts = START + timedelta(days=day, minutes=game * 20)
            as_of = starts - timedelta(hours=12)
            home_win = game
            home_runs, away_runs = ((6, 3) if home_win else (3, 6))
            strength = 1.0 if home_win else -1.0
            features = {
                "home_rating": 1500 + strength * 90,
                "away_rating": 1500,
                "home_runs_per_game": 4.5 + strength * 0.6,
                "away_runs_per_game": 4.5 - strength * 0.4,
                "home_runs_allowed_per_game": 4.4 - strength * 0.4,
                "away_runs_allowed_per_game": 4.4 + strength * 0.5,
                "home_ops": 0.720 + strength * 0.025,
                "away_ops": 0.720 - strength * 0.020,
                "home_recent_runs": 4.5 + strength * 0.7,
                "away_recent_runs": 4.5 - strength * 0.5,
                "home_starter_era": 4.2 - strength * 0.4,
                "away_starter_era": 4.2 + strength * 0.4,
                "park_run_factor": 1.0,
            }
            rows.append(TrainingRowFixture(
                game_id=f"game-{day}-{game}",
                scheduled_first_pitch=starts.isoformat(),
                data_as_of=as_of.isoformat(),
                lifecycle="FINAL" if day % 2 else "MORNING",
                feature_snapshot_id=f"snapshot-{day}-{game}",
                feature_snapshot_checksum=f"checksum-{day}-{game}",
                feature_version_id="fv-id",
                feature_version="2.0.0",
                feature_schema_version="mlb_point_in_time_v2",
                source_mode="LIVE",
                source_evidence=(),
                home_team_id=f"home-{game}",
                away_team_id=f"away-{game}",
                features=features,
                missing_features=(),
                stale_features=(),
                provisional_features=(),
                feature_completeness=0.85,
                home_win=home_win,
                home_runs=home_runs,
                away_runs=away_runs,
                run_differential=home_runs - away_runs,
                total_runs=home_runs + away_runs,
                market_home_probability=0.52,
                market_timestamp=as_of.isoformat(),
                market_books=5,
            ))
    return rows


def _config(*, ablations: bool = True) -> ModelingConfig:
    return ModelingConfig(
        holdout_fraction=0.20,
        min_development_dates=8,
        min_train_dates=8,
        validation_dates=4,
        step_dates=4,
        include_xgboost=False,
        minimum_isotonic_samples=1_000,
        bootstrap_resamples=50,
        run_ablations=ablations,
        random_seed=19,
    )


def test_integrated_temporal_protocol_returns_auditable_serializable_bundle(artifact_root) -> None:
    rows = _training_rows()
    result = run_temporal_model_development(rows, _config())

    assert len(result.feature_names) > 30
    assert len(result.feature_schema_hash) == 64
    assert sum(result.ensemble_weights.values()) == pytest.approx(1.0)
    assert set(result.ensemble_weights) >= {
        "home_prior", "rating", "run_strength", "logistic", "poisson",
        "negative_binomial", "team_eb",
    }
    assert result.hyperparameters["xgboost_eligible"] is False
    assert result.hyperparameters["poisson_eligible"] is True
    assert result.hyperparameters["negative_binomial_eligible"] is True
    assert result.hyperparameters["team_eb_eligible"] is True
    assert result.temporal_windows["holdout_games"] == 20
    assert result.holdout_metrics.classification.observations == 20
    assert result.holdout_metrics.comparison_run_metrics is not None
    assert set(result.holdout_metrics.comparison_run_metrics) == {"simple_run_strength"}
    assert (
        result.holdout_metrics.comparison_run_metrics["simple_run_strength"].observations
        == result.holdout_metrics.classification.observations
    )
    assert "simple_run_strength" not in result.holdout_metrics.comparison_classification
    assert len(result.holdout_predictions.game_ids) == 20
    assert set(result.ablation_metrics) == {
        "full", "without_recent", "without_lifecycle",
        "without_home_lineup_xwoba", "without_away_lineup_xwoba",
    }
    assert set(result.subgroup_metrics) == {"FINAL", "MORNING"}
    assert result.betting_simulations and all(item.secondary_analysis for item in result.betting_simulations)
    assert result.calibration.method in {"identity", "platt"}
    assert result.ood_diagnostic.observations == 20

    for audit in result.fold_audits:
        assert audit.fitted_row_ids == audit.train_game_ids
        assert set(audit.train_game_ids).isdisjoint(audit.validation_game_ids)
    assert all(row.trained_through < row.event_at for row in result.oof_predictions)
    serialized = result.to_dict()
    json.dumps(serialized)
    assert set(serialized["holdout_metrics"]["comparison_run_metrics"]) == {
        "simple_run_strength",
    }

    sample = rows[-1]
    observation = UnlabeledGame(
        game_id=sample.game_id,
        event_at=datetime.fromisoformat(sample.scheduled_first_pitch),
        features=sample.features,
        lifecycle=sample.lifecycle,
        source_mode=sample.source_mode,
        home_team_id=sample.home_team_id,
        away_team_id=sample.away_team_id,
    )
    expected = result.fitted_bundle.predict([observation])
    artifact = artifact_root / "gate3-bundle.joblib"
    joblib.dump(result.fitted_bundle, artifact)
    loaded = joblib.load(artifact)
    assert loaded.predict([observation]) == expected


def test_holdout_label_perturbation_cannot_change_fit_predictions_or_weights() -> None:
    original = _training_rows()
    first = run_temporal_model_development(original, _config(ablations=False))
    holdout_ids = set(first.holdout_predictions.game_ids)
    changed = []
    for row in original:
        if row.game_id in holdout_ids:
            changed.append(replace(
                row,
                home_win=1 - row.home_win,
                home_runs=row.away_runs,
                away_runs=row.home_runs,
                run_differential=-row.run_differential,
            ))
        else:
            changed.append(row)
    second = run_temporal_model_development(changed, _config(ablations=False))

    assert first.ensemble_weights == second.ensemble_weights
    assert first.calibration_selection_scores == second.calibration_selection_scores
    assert first.holdout_predictions == second.holdout_predictions
    assert first.ood_diagnostic == second.ood_diagnostic
    assert first.holdout_metrics.classification.log_loss != second.holdout_metrics.classification.log_loss


def test_stack_validation_predictions_are_temporally_cross_fitted() -> None:
    original = _training_rows()
    first = run_temporal_model_development(original, _config(ablations=False))

    assert first.stack_fold_audits
    audit_by_index = {audit.fold_index: audit for audit in first.stack_fold_audits}
    for audit in first.stack_fold_audits:
        assert set(audit.train_game_ids).isdisjoint(audit.validation_game_ids)
        assert audit.trained_through < audit.validation_started_at
        assert sum(audit.weights.values()) == pytest.approx(1.0)
    for prediction in first.oof_predictions:
        audit = audit_by_index[prediction.stack_fold_index]
        assert prediction.game_id in audit.validation_game_ids
        assert prediction.game_id not in audit.train_game_ids
        assert prediction.trained_through == audit.trained_through

    # A validation row's own outcome cannot affect the raw stack prediction
    # made for it.  Perturb the entire latest meta-validation fold; its base
    # predictions and its earlier-only stack fit must therefore stay fixed.
    latest_validation_ids = set(first.stack_fold_audits[-1].validation_game_ids)
    changed = [
        replace(
            row,
            home_win=1 - row.home_win,
            home_runs=row.away_runs,
            away_runs=row.home_runs,
            run_differential=-row.run_differential,
        )
        if row.game_id in latest_validation_ids
        else row
        for row in original
    ]
    second = run_temporal_model_development(changed, _config(ablations=False))
    first_probability = {
        row.game_id: row.raw_stacked_probability
        for row in first.oof_predictions
        if row.game_id in latest_validation_ids
    }
    second_probability = {
        row.game_id: row.raw_stacked_probability
        for row in second.oof_predictions
        if row.game_id in latest_validation_ids
    }
    assert first_probability
    assert second_probability == pytest.approx(first_probability)


def test_temporal_windows_report_exact_cross_fitted_oof_bounds() -> None:
    result = run_temporal_model_development(_training_rows(), _config(ablations=False))
    first = min(row.event_at for row in result.oof_predictions)
    last = max(row.event_at for row in result.oof_predictions)

    assert result.temporal_windows["oof_validation_start"] == first.isoformat()
    assert result.temporal_windows["oof_validation_end"] == last.isoformat()
    assert result.temporal_windows["oof_games"] == len(result.oof_predictions)
    assert result.temporal_windows["base_oof_games"] > result.temporal_windows["oof_games"]
    assert result.temporal_windows["stack_rolling_folds"] == len(result.stack_fold_audits)
    assert first == START + timedelta(days=12)
    assert last == START + timedelta(days=39, minutes=20)


def test_training_row_adapter_fails_closed_on_postgame_or_mixed_feature_versions() -> None:
    rows = _training_rows()
    postgame = replace(rows[0], data_as_of=rows[0].scheduled_first_pitch)
    with pytest.raises(ValueError, match="strictly pregame"):
        run_temporal_model_development([postgame, *rows[1:]], _config(ablations=False))

    mixed = replace(rows[-1], feature_version="3.0.0")
    with pytest.raises(ValueError, match="mix feature_version"):
        run_temporal_model_development([*rows[:-1], mixed], _config(ablations=False))

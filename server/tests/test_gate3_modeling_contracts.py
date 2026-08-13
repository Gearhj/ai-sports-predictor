from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.modeling import (
    AblationSpec,
    FeatureSpec,
    FoldPreprocessor,
    FrozenFeatureSchema,
    LabeledGame,
    apply_ablation,
    date_group_chronological_holdout,
    feature_family_ablations,
    lifecycle_ablations,
    lifecycle_cohorts,
    recent_window_ablations,
    rolling_origin_folds,
    simulate_betting_thresholds,
)


START = datetime(2025, 4, 1, 19, tzinfo=UTC)


def _schema() -> FrozenFeatureSchema:
    return FrozenFeatureSchema(
        "test-v1",
        (
            FeatureSpec("strength", "team", 0.0),
            FeatureSpec("recent_7d", "recent", 0.0, recent_window_days=7),
            FeatureSpec("recent_30d", "recent", 0.0, recent_window_days=30),
            FeatureSpec("minutes", "lifecycle", 720.0),
        ),
    )


def _rows(days: int = 8) -> list[LabeledGame]:
    rows: list[LabeledGame] = []
    for day in range(days):
        for game in range(2):
            rows.append(LabeledGame(
                game_id=f"g-{day}-{game}",
                event_at=START + timedelta(days=day, minutes=game * 15),
                features={"strength": 1.0 if game else -1.0},
                home_win=game,
                lifecycle="FINAL" if day % 2 else "MORNING",
                home_runs=5.0 if game else 2.0,
                away_runs=2.0 if game else 5.0,
            ))
    return rows


def test_frozen_schema_has_named_missing_indicators_and_stable_hash() -> None:
    schema = _schema()
    extracted = schema.extract({"strength": 2.5, "recent_7d": float("nan"), "minutes": None})

    assert extracted.names == schema.output_names
    assert extracted.values[schema.output_names.index("strength")] == 2.5
    assert extracted.values[schema.output_names.index("strength__missing")] == 0.0
    assert extracted.values[schema.output_names.index("recent_7d__missing")] == 1.0
    assert set(extracted.missing) == {"recent_7d", "recent_30d", "minutes"}
    assert len(schema.schema_hash) == 64
    assert schema.schema_hash == _schema().schema_hash
    assert apply_ablation(schema, AblationSpec("no_recent", excluded_families=("recent",))).schema_hash != schema.schema_hash


def test_fold_preprocessor_fits_once_on_training_rows_only() -> None:
    processor = FoldPreprocessor()
    train = np.asarray([[1.0, 0.0], [3.0, 1.0]])
    transformed = processor.fit_transform(train, ("x", "x__missing"), row_ids=("train-a", "train-b"))
    holdout = processor.transform(np.asarray([[101.0, 0.0]]), ("x", "x__missing"))

    assert np.allclose(transformed[:, 0], [-1.0, 1.0])
    assert transformed[:, 1].tolist() == [0.0, 1.0]
    assert holdout[0, 0] == pytest.approx(99.0)
    assert processor.audit.fit_row_ids == ("train-a", "train-b")
    with pytest.raises(RuntimeError, match="cannot be refitted"):
        processor.fit(train, ("x", "x__missing"))
    with pytest.raises(ValueError, match="order"):
        processor.transform(train, ("x__missing", "x"))


def test_date_group_holdout_is_locked_and_rolling_folds_never_split_dates() -> None:
    split = date_group_chronological_holdout(_rows(), holdout_fraction=0.25, min_development_dates=3)
    development_dates = {row.event_at.date() for row in split.development}
    holdout_dates = set(split.holdout.event_dates)

    assert development_dates.isdisjoint(holdout_dates)
    assert max(development_dates) < min(holdout_dates)
    assert not hasattr(split.holdout.observations[0], "home_win")
    report = split.holdout.final_evaluate(
        [0.25, 0.75, 0.25, 0.75],
        predicted_home_runs=[2.0, 5.0, 2.0, 5.0],
        predicted_away_runs=[5.0, 2.0, 5.0, 2.0],
        comparison_run_predictions={
            "simple_run_strength": ([4.0] * 4, [4.0] * 4),
        },
    )
    assert report.classification.observations == 4
    assert report.home_runs_rmse == pytest.approx(0.0)
    assert report.total_runs_mae == pytest.approx(0.0)
    assert report.home_runs_poisson_deviance == pytest.approx(0.0)
    assert report.comparison_run_metrics is not None
    assert set(report.comparison_run_metrics) == {"simple_run_strength"}
    baseline = report.comparison_run_metrics["simple_run_strength"]
    assert baseline.observations == 4
    assert baseline.home_runs_rmse > 0
    assert baseline.total_runs_poisson_deviance > 0
    with pytest.raises(RuntimeError, match="already been evaluated"):
        split.holdout.final_evaluate([0.25, 0.75, 0.25, 0.75])

    folds = rolling_origin_folds(split.development, min_train_dates=2, validation_dates=1)
    for fold in folds:
        train_dates = {row.event_at.date() for row in fold.train}
        validation_dates = {row.event_at.date() for row in fold.validation}
        assert train_dates.isdisjoint(validation_dates)
        assert max(train_dates) < min(validation_dates)
        assert len(validation_dates) == 1


def test_temporal_contract_rejects_duplicate_games_and_demo_evidence() -> None:
    rows = _rows(5)
    with pytest.raises(ValueError, match="only once"):
        date_group_chronological_holdout([*rows, rows[0]], min_development_dates=2)
    demo = LabeledGame("demo", START + timedelta(days=10), {}, 1, source_mode="DEMO")
    with pytest.raises(ValueError, match="LIVE-only"):
        date_group_chronological_holdout([*rows, demo], min_development_dates=2)


def test_ablation_helpers_are_predeclared_and_lifecycle_cohorts_are_stable() -> None:
    schema = _schema()
    family_names = {item.name for item in feature_family_ablations(schema)}
    recent = recent_window_ablations(schema)

    assert "without_recent" in family_names
    assert {item.maximum_recent_window_days for item in recent} >= {7, 30}
    assert lifecycle_ablations()[0].excluded_families == ("lifecycle",)
    cohorts = lifecycle_cohorts(reversed(_rows(3)))
    assert set(cohorts) == {"FINAL", "MORNING"}
    assert all(tuple(group) == tuple(sorted(group, key=lambda row: (row.event_at, row.game_id))) for group in cohorts.values())


def test_betting_threshold_simulation_is_explicitly_secondary() -> None:
    results = simulate_betting_thresholds(
        [0.70, 0.51, 0.65, 0.40],
        [2.0, 2.0, None, 2.0],
        [1, 0, 1, 0],
        thresholds=(0.0, 0.10, 0.30),
    )

    assert all(item.secondary_analysis for item in results)
    assert results[0].bets == 2
    assert results[0].profit == pytest.approx(0.0)
    assert results[-1].bets == 0
    assert results[-1].roi is None

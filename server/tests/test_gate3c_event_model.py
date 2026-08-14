from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from app.gen2.event_model import (
    EVENT_FEATURE_NAMES,
    EVENT_FEATURE_SCHEMA_HASH,
    EVENT_FEATURE_SCHEMA_VERSION,
    EventModelError,
    OOFMulticlassPrediction,
    PlateAppearanceTrainingRow,
    fit_hierarchical_event_model,
    fit_temperature_calibrator,
    fit_xgboost_event_model,
    multiclass_metrics,
    oof_model_training_checksum,
)
from app.gen2.event_taxonomy import PlateAppearanceOutcome, ordered_outcomes


FIT_CUTOFF = datetime(2024, 5, 1, tzinfo=UTC)
FOLD_IDENTITY = "f" * 64


def _fit_hierarchical(rows):
    return fit_hierarchical_event_model(
        rows,
        fit_cutoff=FIT_CUTOFF,
        excluded_game_ids=("validation-game",),
        fold_identity_checksum=FOLD_IDENTITY,
    )


def _fit_xgboost(rows, **kwargs):
    return fit_xgboost_event_model(
        rows,
        fit_cutoff=FIT_CUTOFF,
        excluded_game_ids=("validation-game",),
        fold_identity_checksum=FOLD_IDENTITY,
        **kwargs,
    )


def _features(class_index: int, row_index: int) -> dict[str, float | None]:
    values: dict[str, float | None] = {
        name: ((row_index % 11) - 5) / 10 for name in EVENT_FEATURE_NAMES
    }
    outcome = ordered_outcomes()[class_index]
    suffix = outcome.value.lower()
    for prefix in (
        "batter_posterior_logit_",
        "pitcher_allowed_posterior_logit_",
        "league_environment_logit_",
    ):
        name = prefix + suffix
        if name in values:
            values[name] = 2.0 + row_index / 100
    values["batter_effective_sample"] = float(30 + row_index)
    values["pitcher_effective_sample"] = float(40 + row_index)
    values["batter_posterior_variance"] = 0.02 + class_index / 100
    values["pitcher_posterior_variance"] = 0.03 + class_index / 100
    values["same_hand_indicator"] = float(row_index % 2)
    values["batter_left_indicator"] = float((row_index + 1) % 2)
    values["pitcher_left_indicator"] = float(row_index % 2)
    values["prior_period_park_run_factor"] = None if row_index == 0 else 1.0
    return values


def _rows(repeats: int = 8) -> tuple[PlateAppearanceTrainingRow, ...]:
    rows: list[PlateAppearanceTrainingRow] = []
    origin = datetime(2024, 4, 1, 18, tzinfo=UTC)
    for repeat in range(repeats):
        for class_index, outcome in enumerate(ordered_outcomes()):
            index = repeat * len(ordered_outcomes()) + class_index
            event_at = origin + timedelta(days=repeat, minutes=class_index)
            rows.append(
                PlateAppearanceTrainingRow(
                    row_id=f"row-{index}",
                    game_id=f"game-{repeat}",
                    plate_appearance_id=f"pa-{index}",
                    event_date=event_at.date(),
                    event_at=event_at,
                    evidence_end_at=event_at - timedelta(seconds=1),
                    eligible_at=event_at - timedelta(seconds=1),
                    outcome=outcome,
                    features=_features(class_index, index),
                    source_game_ids=(f"prior-{repeat}",),
                    evidence_checksum=f"{index:064x}"[-64:],
                )
            )
    return tuple(rows)


def test_frozen_schema_and_point_in_time_guards() -> None:
    assert EVENT_FEATURE_SCHEMA_VERSION == "mlb_gen2_pa_event_features_v1"
    assert len(EVENT_FEATURE_NAMES) == len(set(EVENT_FEATURE_NAMES)) == 29
    assert len(EVENT_FEATURE_SCHEMA_HASH) == 64
    base = _rows(1)[0]
    # Official baseball date is governed independently from the UTC instant.
    assert replace(base, event_date=date(2024, 3, 31)).event_date == date(2024, 3, 31)
    with pytest.raises(EventModelError, match="current-game"):
        replace(base, source_game_ids=(base.game_id,))
    with pytest.raises(EventModelError, match="end before"):
        replace(base, evidence_end_at=base.event_at)
    with pytest.raises(EventModelError, match="Tier A"):
        replace(base, evidence_tier="B")
    with pytest.raises(EventModelError, match="source game identities"):
        replace(base, source_game_ids=())


def test_hierarchical_multinomial_persists_coefficients_and_predicts_exact_vector() -> None:
    rows = _rows()
    model = _fit_hierarchical(rows)
    evidence = model.evidence
    assert evidence.training_rows == 64
    assert evidence.training_dates == 8
    assert evidence.feature_schema_hash == EVENT_FEATURE_SCHEMA_HASH
    assert len(evidence.coefficient_rows) == 8
    assert len(evidence.parameter_checksum) == 64
    prediction = model.predict(rows[0].features)
    assert tuple(prediction.probabilities) == ordered_outcomes()
    assert sum(prediction.probabilities.values()) == pytest.approx(1.0, abs=1e-12)
    assert all(value >= 0 for value in prediction.probabilities.values())
    changed = (replace(rows[0], evidence_end_at=rows[0].evidence_end_at - timedelta(seconds=1)),) + rows[1:]
    assert (
        _fit_hierarchical(changed).evidence.training_evidence_checksum
        != evidence.training_evidence_checksum
    )


def test_missing_taxonomy_class_and_feature_contract_fail_closed() -> None:
    rows = tuple(row for row in _rows() if row.outcome is not PlateAppearanceOutcome.TRIPLE)
    with pytest.raises(EventModelError, match="TRIPLE"):
        _fit_hierarchical(rows)
    bad_features = dict(_rows()[0].features)
    bad_features.pop(EVENT_FEATURE_NAMES[0])
    with pytest.raises(EventModelError, match="frozen schema"):
        replace(_rows()[0], features=bad_features)


def test_restrained_xgboost_challenger_predicts_frozen_taxonomy() -> None:
    rows = _rows(10)
    model = _fit_xgboost(rows, estimators=12, max_depth=2)
    assert model.evidence.training_rows == 80
    assert model.evidence.coefficient_rows == ()
    prediction = model.predict(rows[-1].features)
    assert tuple(prediction.probabilities) == ordered_outcomes()
    assert sum(prediction.probabilities.values()) == pytest.approx(1.0, abs=1e-12)
    with pytest.raises(EventModelError, match="restrained"):
        _fit_xgboost(rows, estimators=401)


def test_temperature_and_multiclass_metrics_use_only_aligned_oof_rows() -> None:
    targets = tuple(ordered_outcomes()) * 4
    predictions = []
    for target in targets:
        probabilities = {outcome: 0.2 / 7 for outcome in ordered_outcomes()}
        probabilities[target] = 0.8
        predictions.append(probabilities)
    training_ids = tuple(f"training-{index}" for index in range(12))
    training_cutoff = datetime(2024, 3, 1, tzinfo=UTC)

    def record(sample_id, target, probabilities, fold_digit):
        fold_identity = fold_digit * 64
        return OOFMulticlassPrediction(
            sample_id=sample_id,
            target=target,
            probabilities=probabilities,
            event_at=datetime(2024, 4, 1, tzinfo=UTC),
            fold_identity_checksum=fold_identity,
            training_cutoff=training_cutoff,
            training_sample_ids=training_ids,
            model_training_checksum=oof_model_training_checksum(
                fold_identity_checksum=fold_identity,
                training_cutoff=training_cutoff,
                training_sample_ids=training_ids,
            ),
        )

    fit_records = tuple(
        record(f"fit-{index}", targets[index], predictions[index], "a")
        for index in range(16)
    )
    selection_records = tuple(
        record(
            f"selection-{index}",
            targets[16 + index],
            predictions[16 + index],
            "b",
        )
        for index in range(16)
    )
    calibrator = fit_temperature_calibrator(
        fit_records,
        selection_records=selection_records,
    )
    assert calibrator.fit_rows == 16
    assert calibrator.selection_rows == 16
    assert 0.5 <= calibrator.temperature <= 2.0
    assert len(calibrator.parameter_checksum) == 64
    assert calibrator.calibrated_selection_metrics.rows == 16
    calibrated = [calibrator.calibrate(row).probabilities for row in predictions]
    metrics = multiclass_metrics(targets, calibrated)
    assert metrics.rows == 32
    assert metrics.log_loss >= 0
    assert metrics.multiclass_brier >= 0
    assert set(metrics.classwise_brier) == {outcome.value for outcome in ordered_outcomes()}
    with pytest.raises(EventModelError, match="aligned"):
        multiclass_metrics(targets[:-1], calibrated)
    with pytest.raises(EventModelError, match="disjoint"):
        fit_temperature_calibrator(
            fit_records,
            selection_records=(
                replace(selection_records[0], sample_id=fit_records[0].sample_id),
                *selection_records[1:],
            ),
        )
    with pytest.raises(EventModelError, match="appears in its model training"):
        replace(
            fit_records[0],
            sample_id=training_ids[0],
        )

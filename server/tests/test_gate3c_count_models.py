from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pytest

from app.gen2.count_models import (
    COUNT_FEATURE_NAMES,
    COUNT_FEATURE_SCHEMA_HASH,
    CountModelError,
    CountTrainingRow,
    fit_count_models,
    nb2_pmf,
    poisson_pmf,
    shared_environment_joint_distribution,
)


FIT_CUTOFF = datetime(2024, 8, 1, tzinfo=UTC)


def _fit(rows):
    return fit_count_models(
        rows,
        fit_cutoff=FIT_CUTOFF,
        current_game_id="target-game",
    )


def _features(index: int) -> dict[str, float | None]:
    home = 3.2 + (index % 7) * 0.15
    away = 3.0 + (index % 5) * 0.12
    return {
        "simulator_mean_home_runs": home,
        "simulator_mean_away_runs": away,
        "simulator_variance_home_runs": home + 0.8,
        "simulator_variance_away_runs": away + 0.6,
        "event_strength_home": (index % 9) / 10,
        "event_strength_away": (index % 8) / 10,
        "starter_duration_home": 21 + index % 6,
        "starter_duration_away": 20 + index % 7,
        "bullpen_strength_home": 0.2 + (index % 4) / 10,
        "bullpen_strength_away": 0.25 + (index % 3) / 10,
        "prior_period_park_run_factor": None if index == 0 else 1.0 + (index % 3) / 20,
        "weather_run_environment": 0.0,
    }


def _rows(count: int = 80) -> tuple[CountTrainingRow, ...]:
    origin = datetime(2024, 4, 1, 23, tzinfo=UTC)
    rows = []
    for index in range(count):
        first_pitch = origin + timedelta(days=index // 2, minutes=index % 2)
        shared_shock = 2 if index % 9 == 0 else 0
        home_runs = index % 6 + shared_shock
        away_runs = (index * 3) % 6 + shared_shock
        rows.append(
            CountTrainingRow(
                row_id=f"row-{index}",
                game_id=f"game-{index}",
                official_date=date(2024, 4, 1) + timedelta(days=index // 2),
                first_pitch_at=first_pitch,
                computed_at=first_pitch - timedelta(hours=6),
                evidence_end_at=first_pitch - timedelta(hours=7),
                eligible_at=first_pitch - timedelta(hours=7),
                label_available_at=first_pitch + timedelta(hours=4),
                home_runs=home_runs,
                away_runs=away_runs,
                features=_features(index),
                source_game_ids=(f"prior-{index}",),
                evidence_checksum=f"{index + 1000:064x}"[-64:],
            )
        )
    return tuple(rows)


def test_count_contract_rejects_target_and_post_start_evidence() -> None:
    assert len(COUNT_FEATURE_NAMES) == len(set(COUNT_FEATURE_NAMES)) == 12
    assert len(COUNT_FEATURE_SCHEMA_HASH) == 64
    row = _rows()[0]
    with pytest.raises(CountModelError, match="target-game"):
        replace(row, source_game_ids=(row.game_id,))
    with pytest.raises(CountModelError, match="before first pitch"):
        replace(row, evidence_end_at=row.first_pitch_at)
    with pytest.raises(CountModelError, match="Tier A"):
        replace(row, evidence_tier="B")
    with pytest.raises(CountModelError, match="source game identities"):
        replace(row, source_game_ids=())
    with pytest.raises(CountModelError, match="eligible before it ends"):
        replace(row, eligible_at=row.evidence_end_at - timedelta(seconds=1))
    with pytest.raises(CountModelError, match="after features were computed"):
        replace(
            row,
            evidence_end_at=row.computed_at + timedelta(seconds=1),
            eligible_at=row.computed_at + timedelta(seconds=1),
        )


def test_poisson_nb_and_shared_environment_are_fitted_from_train_rows() -> None:
    model = _fit(_rows())
    evidence = model.evidence
    assert evidence.rows == 80
    assert evidence.dates == 40
    assert evidence.feature_schema_hash == COUNT_FEATURE_SCHEMA_HASH
    assert evidence.home_overdispersion_alpha >= 0
    assert evidence.away_overdispersion_alpha >= 0
    assert evidence.shared_environment_fraction >= 0
    assert all(len(value) == 64 for value in (
        evidence.evidence_checksum,
        evidence.preprocessing_checksum,
        evidence.parameter_checksum,
        evidence.training_population_checksum,
    ))
    prediction = model.predict(_features(101))
    assert prediction.mean_home_runs > 0
    assert prediction.mean_away_runs > 0
    assert prediction.shared_environment_lambda <= min(
        prediction.mean_home_runs, prediction.mean_away_runs
    )
    rows = _rows()
    changed = (replace(rows[0], computed_at=rows[0].computed_at - timedelta(seconds=1)),) + rows[1:]
    assert _fit(changed).evidence.evidence_checksum != evidence.evidence_checksum


def test_joint_score_distribution_is_normalized_and_dependent() -> None:
    prediction = _fit(_rows()).predict(_features(3))
    joint = shared_environment_joint_distribution(prediction, max_runs=50)
    assert joint.shape == (51, 51)
    assert float(joint.sum()) == pytest.approx(1.0, abs=1e-12)
    home_values = np.arange(joint.shape[0])[:, None]
    away_values = np.arange(joint.shape[1])[None, :]
    home_mean = float((joint * home_values).sum())
    away_mean = float((joint * away_values).sum())
    covariance = float((joint * (home_values - home_mean) * (away_values - away_mean)).sum())
    assert covariance >= -1e-12
    with pytest.raises(CountModelError, match="omits probability mass"):
        shared_environment_joint_distribution(prediction, max_runs=5)


def test_probability_mass_functions_are_valid_and_fail_closed() -> None:
    assert sum(poisson_pmf(value, 4.0) for value in range(40)) == pytest.approx(1.0)
    assert sum(nb2_pmf(value, 4.0, 0.3) for value in range(100)) == pytest.approx(1.0)
    with pytest.raises(CountModelError, match="invalid Poisson"):
        poisson_pmf(-1, 4.0)
    with pytest.raises(CountModelError, match="invalid NB2"):
        nb2_pmf(1, 4.0, -0.1)


def test_duplicate_and_small_training_sets_fail_closed() -> None:
    with pytest.raises(CountModelError, match="at least 20"):
        _fit(_rows(19))
    rows = list(_rows(20))
    rows.append(replace(rows[0], row_id="conflict", home_runs=99))
    with pytest.raises(CountModelError, match="conflicting duplicate"):
        _fit(rows)

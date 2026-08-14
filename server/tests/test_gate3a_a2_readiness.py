from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from app.training.dataset import TrainingDatasetManifestData, TrainingRow
from app.training.readiness import (
    NOT_READY_STATUS,
    READY_STATUS,
    DatasetReadinessPolicy,
    REQUIRED_FEATURE_FAMILIES,
    audit_historical_dataset_readiness,
)


def _row(index: int, *, feature_version_id: str = "feature-v5") -> TrainingRow:
    event = datetime(2025, 3, 1, 18, tzinfo=UTC) + timedelta(days=index // 15)
    return TrainingRow(
        game_id=f"game-{index}",
        scheduled_first_pitch=event.isoformat().replace("+00:00", "Z"),
        data_as_of=(event - timedelta(hours=6)).isoformat().replace("+00:00", "Z"),
        lifecycle="HISTORICAL_T_MINUS_6H",
        feature_snapshot_id=f"snapshot-{index}",
        feature_snapshot_checksum="a" * 64,
        feature_version_id=feature_version_id,
        feature_version="mlb/a2-v5",
        feature_schema_version="mlb_pregame_historical_2025_v5",
        source_mode="LIVE",
        source_evidence=[{
            "lineage_version": "gate3a_a2_compact_lineage_v1",
            "attestation_identity_checksum": "d" * 64,
            "canonical_set_checksum": "e" * 64,
            "source_bundle_checksum": "f" * 64,
            "target_excluded": True,
            "temporal_valid": True,
        }],
        home_team_id="home",
        away_team_id="away",
        features={"home_rating": 1500.0},
        missing_features=[],
        stale_features=[],
        provisional_features=[],
        feature_completeness=0.6,
        home_win=index % 2,
        home_runs=4 if index % 2 else 2,
        away_runs=2 if index % 2 else 4,
        run_differential=2 if index % 2 else -2,
        total_runs=6,
        market_home_probability=None,
        market_timestamp=None,
        market_books=0,
        closing_home_probability=None,
        closing_timestamp=None,
        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
        historical_quality_tier="A",
        cutoff_policy_version="mlb_t_minus_6h_v1",
    )


def _manifest(rows: list[TrainingRow]) -> TrainingDatasetManifestData:
    return TrainingDatasetManifestData(
        dataset_id="dataset-a2",
        created_at="2026-08-13T00:00:00Z",
        date_start="2025-03-01",
        date_end="2025-05-31",
        seasons=[2025],
        feature_schema_version="mlb_pregame_historical_2025_v5",
        feature_version="mlb/a2-v5",
        feature_version_id="feature-v5",
        feature_names=["home_rating"],
        row_count=len(rows),
        excluded_row_count=0,
        exclusion_reasons={},
        source_modes=["LIVE"],
        rows_checksum="b" * 64,
        checksum="c" * 64,
        target_definitions={"home_win": "final target"},
        training_cutoff="2026-08-13T00:00:00Z",
        leakage_checks={"one_row_per_game": True, "pregame_cutoff": True},
        feature_family_coverage={family: 1.0 for family in REQUIRED_FEATURE_FAMILIES},
        population="HISTORICAL_RECONSTRUCTION_A2",
        temporal_semantics="RETROSPECTIVE_RECONSTRUCTION",
        cutoff_policy_version="mlb_t_minus_6h_v1",
        quality_tiers=["A"],
    )


def test_readiness_audit_passes_without_using_holdout_labels_or_models() -> None:
    rows = [_row(index) for index in range(1200)]
    result = audit_historical_dataset_readiness(_manifest(rows), rows)

    assert result.status == READY_STATUS
    assert result.row_count == 1200
    assert result.distinct_event_dates == 80
    assert result.observed_outcome_classes == (0, 1)
    assert result.holdout_rows == 240
    assert result.model_training_invoked is False
    assert result.model_fitting_invoked is False
    assert result.holdout_metrics_inspected is False
    assert result.holdout_outcomes_inspected is False


def test_readiness_audit_fails_closed_on_small_or_inconsistent_population() -> None:
    rows = [_row(index) for index in range(60)]
    manifest = replace(
        _manifest(rows),
        row_count=61,
        leakage_checks={"one_row_per_game": True, "pregame_cutoff": False},
    )
    result = audit_historical_dataset_readiness(
        manifest,
        rows,
        policy=DatasetReadinessPolicy(minimum_rows=50, minimum_holdout_rows=10),
    )

    assert result.status == NOT_READY_STATUS
    assert result.checks["manifest_row_count"]["passed"] is False
    assert result.checks["manifest_leakage_gates"]["passed"] is False
    assert result.checks["distinct_event_dates"]["passed"] is False
    assert result.holdout_outcomes_inspected is False


def test_readiness_audit_rejects_mixed_feature_identity() -> None:
    rows = [_row(index) for index in range(1200)]
    rows[-1] = replace(rows[-1], feature_version_id="other-feature")

    result = audit_historical_dataset_readiness(_manifest(rows), rows)

    assert result.status == NOT_READY_STATUS
    assert result.checks["schema_resolves"]["passed"] is False


def test_readiness_schema_accepts_canonical_json_key_order() -> None:
    rows = [
        replace(
            _row(index),
            features=dict(
                sorted(
                    {
                        "home_rating": 1500.0 + index,
                        "away_rating": 1490.0 - index,
                    }.items()
                )
            ),
        )
        for index in range(1200)
    ]
    manifest = replace(
        _manifest(rows),
        feature_names=["home_rating", "away_rating"],
    )
    assert list(rows[0].features) == ["away_rating", "home_rating"]

    result = audit_historical_dataset_readiness(manifest, rows)
    assert result.status == READY_STATUS
    assert result.checks["schema_resolves"]["passed"] is True

    missing = list(rows)
    missing[0] = replace(missing[0], features={"home_rating": 1500.0})
    blocked = audit_historical_dataset_readiness(manifest, missing)
    assert blocked.status == NOT_READY_STATUS
    assert blocked.checks["schema_resolves"]["passed"] is False


def test_readiness_never_inspects_holdout_labels_and_rejects_row_leakage() -> None:
    rows = [_row(index) for index in range(1200)]
    baseline = audit_historical_dataset_readiness(_manifest(rows), rows)

    # Mutating every held-out outcome cannot change development-only class
    # evidence or readiness; the audit uses those rows only for date/row counts.
    holdout_dates = {
        datetime.fromisoformat(row.scheduled_first_pitch.replace("Z", "+00:00")).date()
        for row in rows[-240:]
    }
    mutated = [
        replace(row, home_win=1)
        if datetime.fromisoformat(row.scheduled_first_pitch.replace("Z", "+00:00")).date()
        in holdout_dates
        else row
        for row in rows
    ]
    changed = audit_historical_dataset_readiness(_manifest(mutated), mutated)
    assert changed.observed_outcome_classes == baseline.observed_outcome_classes
    assert changed.status == baseline.status == READY_STATUS
    assert changed.holdout_outcomes_inspected is False

    leaked = list(rows)
    leaked[0] = replace(leaked[0], market_home_probability=0.55)
    blocked = audit_historical_dataset_readiness(_manifest(leaked), leaked)
    assert blocked.status == NOT_READY_STATUS
    assert blocked.checks["row_point_in_time_contract"]["passed"] is False

    bad_lineage = list(rows)
    bad_lineage[0] = replace(
        bad_lineage[0],
        source_evidence=[{
            **bad_lineage[0].source_evidence[0],
            "target_excluded": False,
            "canonical_set_checksum": "not-a-checksum",
        }],
    )
    lineage_blocked = audit_historical_dataset_readiness(
        _manifest(bad_lineage), bad_lineage
    )
    assert lineage_blocked.status == NOT_READY_STATUS
    assert lineage_blocked.checks["row_point_in_time_contract"]["passed"] is False

    sparse_manifest = replace(
        _manifest(rows),
        feature_family_coverage={
            family: (0.69 if family == "statcast" else 1.0)
            for family in REQUIRED_FEATURE_FAMILIES
        },
    )
    sparse = audit_historical_dataset_readiness(sparse_manifest, rows)
    assert sparse.status == NOT_READY_STATUS
    assert sparse.checks["required_feature_family_coverage"]["passed"] is False

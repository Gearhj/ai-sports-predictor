from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.audit import ImmutableRecordError
from app.models import (
    FeatureVersion,
    ModelEvaluation,
    ModelStatus,
    ModelVersion,
    TrainingDatasetManifest,
)


GATE3_MODEL_COLUMNS = {
    "target",
    "training_manifest_id",
    "validation_window",
    "holdout_window",
    "calibrator_uri",
    "calibrator_checksum",
    "ensemble_weights",
    "code_revision",
    "identity_checksum",
    "feature_names",
    "seed_policy",
    "target_definition",
    "promotion_decision",
    "promotion_reason",
}


def _config(database_url: str) -> Config:
    server_root = Path(__file__).resolve().parents[1]
    config = Config(str(server_root / "alembic.ini"))
    config.set_main_option("script_location", str(server_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _provider_disabled_environment(monkeypatch: pytest.MonkeyPatch, database_url: str) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("THE_ODDS_API_KEY", "")
    monkeypatch.setenv("ODDS_POLLING_ENABLED", "false")
    monkeypatch.setenv("OPEN_METEO_ENABLED", "false")
    monkeypatch.setenv("STATCAST_ENABLED", "false")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")


def test_gate3_migration_preserves_gate2_models_and_round_trips(
    artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{(artifact_root / 'gate3.db').as_posix()}"
    _provider_disabled_environment(monkeypatch, database_url)
    config = _config(database_url)

    command.upgrade(config, "20260812_0003")
    engine = create_engine(database_url)
    before = inspect(engine)
    assert {"training_dataset_manifests", "model_evaluations"}.isdisjoint(before.get_table_names())
    assert GATE3_MODEL_COLUMNS.isdisjoint(
        column["name"] for column in before.get_columns("model_versions")
    )
    legacy_id = uuid4().hex
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO model_versions (name, version, algorithm, status, id) "
                "VALUES (:name, :version, :algorithm, :status, :id)"
            ),
            {
                "name": "legacy-gate2",
                "version": "1",
                "algorithm": "baseline",
                "status": "DEVELOPMENT",
                "id": legacy_id,
            },
        )
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    schema = inspect(engine)
    assert {"training_dataset_manifests", "model_evaluations"} <= set(schema.get_table_names())

    model_columns = {column["name"]: column for column in schema.get_columns("model_versions")}
    assert GATE3_MODEL_COLUMNS <= set(model_columns)
    assert all(model_columns[name]["nullable"] is True for name in GATE3_MODEL_COLUMNS)
    assert any(
        foreign_key["referred_table"] == "training_dataset_manifests"
        and foreign_key["constrained_columns"] == ["training_manifest_id"]
        for foreign_key in schema.get_foreign_keys("model_versions")
    )
    assert {
        constraint["name"] for constraint in schema.get_unique_constraints("model_versions")
    } >= {"uq_model_name_version", "uq_model_version_identity_checksum"}
    status_constraint = next(
        constraint
        for constraint in schema.get_check_constraints("model_versions")
        if constraint["name"] == "modelstatus"
    )
    assert "CANDIDATE" in status_constraint["sqltext"]

    manifest_columns = {
        column["name"]: column
        for column in schema.get_columns("training_dataset_manifests")
    }
    assert manifest_columns["identity_checksum"]["nullable"] is False
    assert manifest_columns["feature_version_id"]["nullable"] is False
    assert manifest_columns["artifact_checksum"]["nullable"] is False
    assert {
        constraint["name"]
        for constraint in schema.get_unique_constraints("training_dataset_manifests")
    } >= {"uq_training_dataset_name_version", "uq_training_dataset_identity_checksum"}

    evaluation_columns = {
        column["name"]: column for column in schema.get_columns("model_evaluations")
    }
    assert evaluation_columns["model_version_id"]["nullable"] is False
    assert evaluation_columns["training_manifest_id"]["nullable"] is False
    assert evaluation_columns["identity_checksum"]["nullable"] is False
    evaluation_foreign_keys = schema.get_foreign_keys("model_evaluations")
    assert sum(
        foreign_key["referred_table"] == "model_versions"
        for foreign_key in evaluation_foreign_keys
    ) == 2
    assert any(
        foreign_key["referred_table"] == "training_dataset_manifests"
        for foreign_key in evaluation_foreign_keys
    )

    with engine.begin() as connection:
        legacy = connection.execute(
            text(
                "SELECT target, training_manifest_id, identity_checksum "
                "FROM model_versions WHERE name = 'legacy-gate2'"
            )
        ).one()
        assert tuple(legacy) == (None, None, None)
        connection.execute(
            text(
                "INSERT INTO model_versions (name, version, algorithm, status, id) "
                "VALUES (:name, :version, :algorithm, :status, :id)"
            ),
            {
                "name": "candidate-gate3",
                "version": "1",
                "algorithm": "test",
                "status": "CANDIDATE",
                "id": uuid4().hex,
            },
        )
    engine.dispose()

    command.check(config)
    command.downgrade(config, "20260812_0003")
    engine = create_engine(database_url)
    downgraded = inspect(engine)
    assert {"training_dataset_manifests", "model_evaluations"}.isdisjoint(
        downgraded.get_table_names()
    )
    assert GATE3_MODEL_COLUMNS.isdisjoint(
        column["name"] for column in downgraded.get_columns("model_versions")
    )
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT status FROM model_versions WHERE name = 'candidate-gate3'")
        ).scalar_one() == "CHALLENGER"
    engine.dispose()

    command.upgrade(config, "head")
    command.check(config)


def _manifest(feature_version_id, *, checksum: str = "a" * 64) -> TrainingDatasetManifest:
    return TrainingDatasetManifest(
        name="mlb-pregame",
        version="2024-v1",
        feature_version_id=feature_version_id,
        source_mode="LIVE",
        schema_version="gate3-v1",
        target="home_win",
        target_definition={"kind": "binary", "positive": "home_score > away_score"},
        cutoff_policy={"kind": "fixed_offset", "minutes_before_first_pitch": 60},
        split_policy={"kind": "season_boundaries", "train": [2022, 2023]},
        feature_names=["home_rating", "away_rating"],
        seasons=[2022, 2023, 2024],
        event_start_at=datetime(2022, 4, 7, tzinfo=UTC),
        event_end_at=datetime(2024, 9, 29, tzinfo=UTC),
        row_count=7200,
        game_count=7200,
        excluded_row_count=42,
        artifact_uri="data/features/mlb-pregame/2024-v1.parquet",
        artifact_checksum="b" * 64,
        source_evidence=[{"source": "MLB_STATS_API", "mode": "LIVE"}],
        quality_summary={"leakage_checks_passed": True},
        code_revision="0123456789abcdef",
        identity_checksum=checksum,
        built_at=datetime(2025, 1, 2, tzinfo=UTC),
    )


def test_gate3_governance_records_persist_candidate_identity_and_are_append_only(session) -> None:
    feature_version = FeatureVersion(
        name="mlb_pregame",
        version="3.0.0",
        schema_hash="f" * 64,
        definition={"ordered_features": ["home_rating", "away_rating"]},
    )
    session.add(feature_version)
    session.flush()
    manifest = _manifest(feature_version.id)
    session.add(manifest)
    session.flush()
    model = ModelVersion(
        name="asp_mlb_candidate",
        version="3.0.0-test",
        feature_version_id=feature_version.id,
        algorithm="xgboost_calibrated_ensemble",
        status=ModelStatus.CANDIDATE,
        target="home_win",
        training_manifest_id=manifest.id,
        validation_window={"start": "2024-04-01", "end": "2024-06-30"},
        holdout_window={"start": "2024-07-01", "end": "2024-09-29"},
        calibrator_uri="artifacts/models/3.0.0/calibrator.joblib",
        calibrator_checksum="c" * 64,
        ensemble_weights={"xgboost": 0.8, "baseline": 0.2},
        code_revision="0123456789abcdef",
        identity_checksum="d" * 64,
        feature_names=["home_rating", "away_rating"],
        seed_policy={"training_seed": 20260813, "deterministic": True},
        target_definition={"kind": "binary", "positive": "home_score > away_score"},
        promotion_decision="PENDING",
        promotion_reason="Awaiting immutable holdout evaluation.",
    )
    session.add(model)
    session.flush()
    evaluation = ModelEvaluation(
        model_version_id=model.id,
        training_manifest_id=manifest.id,
        evaluation_type="HOLDOUT",
        status="COMPLETED",
        validation_window=model.validation_window,
        holdout_window=model.holdout_window,
        metrics={"brier_score": 0.218, "log_loss": 0.631},
        baseline_metrics={"brier_score": 0.224, "log_loss": 0.644},
        slice_metrics={"season": {"2024": {"brier_score": 0.218}}},
        calibration={"ece": 0.021, "bin_count": 10},
        prediction_artifact_uri="artifacts/evaluations/3.0.0/predictions.parquet",
        prediction_artifact_checksum="e" * 64,
        evaluated_at=datetime(2025, 1, 3, tzinfo=UTC),
        source_mode="LIVE",
        code_revision="0123456789abcdef",
        identity_checksum="9" * 64,
        superiority_claim=False,
        promotion_recommendation=True,
        notes="Development evaluation only.",
    )
    session.add(evaluation)
    session.commit()

    assert model.status == ModelStatus.CANDIDATE
    assert model.training_manifest_id == manifest.id
    assert model.feature_names == ["home_rating", "away_rating"]
    assert evaluation.training_manifest_id == manifest.id
    assert evaluation.metrics == {"brier_score": 0.218, "log_loss": 0.631}

    manifest.row_count = 1
    with pytest.raises(
        ImmutableRecordError,
        match="TrainingDatasetManifest records are append-only",
    ):
        session.commit()
    session.rollback()

    evaluation = session.get(ModelEvaluation, evaluation.id)
    assert evaluation is not None
    evaluation.notes = "rewritten"
    with pytest.raises(
        ImmutableRecordError,
        match="ModelEvaluation records are append-only",
    ):
        session.commit()
    session.rollback()

    model = session.get(ModelVersion, model.id)
    assert model is not None
    model.artifact_uri = "artifacts/models/3.0.0/rewritten.joblib"
    with pytest.raises(
        ImmutableRecordError,
        match="ModelVersion governance fields are immutable after creation: artifact_uri",
    ):
        session.commit()
    session.rollback()

    model = session.get(ModelVersion, model.id)
    assert model is not None
    model.promotion_decision = "PROMOTED"
    with pytest.raises(
        ImmutableRecordError,
        match="ModelVersion governance fields are immutable after creation: promotion_decision",
    ):
        session.commit()
    session.rollback()

    # Gate 1's forward-performance recalculation remains an intentional legacy
    # carveout; governed offline evidence lives in append-only evaluations.
    model = session.get(ModelVersion, model.id)
    assert model is not None
    model.metrics = {"forward_evaluation": {"evaluated_games": 12}}
    session.commit()
    assert session.get(ModelVersion, model.id).metrics == {
        "forward_evaluation": {"evaluated_games": 12},
    }

    model = session.get(ModelVersion, model.id)
    assert model is not None
    session.delete(model)
    with pytest.raises(
        ImmutableRecordError,
        match="ModelVersion registry records cannot be deleted",
    ):
        session.commit()
    session.rollback()

    evaluation = session.get(ModelEvaluation, evaluation.id)
    assert evaluation is not None
    session.delete(evaluation)
    with pytest.raises(
        ImmutableRecordError,
        match="ModelEvaluation records are append-only",
    ):
        session.commit()

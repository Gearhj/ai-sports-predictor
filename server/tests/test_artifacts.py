from __future__ import annotations

from datetime import UTC, datetime

import joblib
import polars as pl
import pytest

from app.analytics import ParquetSnapshotWriter
from app.forecasting.registry import ModelArtifactStore


def test_immutable_parquet_snapshot(artifact_root) -> None:
    writer = ParquetSnapshotWriter(artifact_root)
    snapshot = writer.write("features", [{"game": "g1", "xwoba": 0.321}], event_date=datetime(2026, 8, 12, tzinfo=UTC))
    frame = pl.read_parquet(snapshot.path)
    assert snapshot.row_count == 1
    assert len(snapshot.checksum) == 64
    assert frame["game"].to_list() == ["g1"]


def test_versioned_model_serialization_and_checksum(artifact_root) -> None:
    store = ModelArtifactStore(artifact_root)
    model = {"weights": [1, 2, 3]}
    store.save(
        model, name="test", version="1.0.0", algorithm="fixture", feature_names=["x"],
        training_window={"start": "2025-01-01", "end": "2025-12-31"}, hyperparameters={}, evaluation_metrics={},
    )
    loaded, manifest = store.load("test", "1.0.0")
    assert loaded == model
    assert manifest.version == "1.0.0"
    with pytest.raises(FileExistsError):
        store.save(model, name="test", version="1.0.0", algorithm="fixture", feature_names=["x"], training_window={}, hyperparameters={}, evaluation_metrics={})

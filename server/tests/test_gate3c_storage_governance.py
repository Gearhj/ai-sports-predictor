from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.audit import ImmutableRecordError
from app.audit.immutability import _APPEND_ONLY_TYPES
from app.models import (
    ExistingEvidenceDerivedUseDecision,
    HistoricalAcquisitionStage,
    HistoricalEventPartition,
    HistoricalRequestAttempt,
    ProviderAccessAuthorization,
    SourceOwnerApproval,
)


TABLES = {
    "source_owner_approvals",
    "provider_access_authorizations",
    "existing_evidence_derived_use_decisions",
    "historical_acquisition_stages",
    "historical_request_attempts",
    "historical_event_partitions",
}


def _config(database_url: str) -> Config:
    server_root = Path(__file__).resolve().parents[1]
    config = Config(str(server_root / "alembic.ini"))
    config.set_main_option("script_location", str(server_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _provider_disabled_environment(
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("THE_ODDS_API_KEY", "")
    monkeypatch.setenv("ODDS_POLLING_ENABLED", "false")
    monkeypatch.setenv("OPEN_METEO_ENABLED", "false")
    monkeypatch.setenv("STATCAST_ENABLED", "false")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")


def test_linear_0010_migration_round_trip_and_database_default_denial(
    artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{(artifact_root / 'gate3c-storage.db').as_posix()}"
    _provider_disabled_environment(monkeypatch, database_url)
    config = _config(database_url)

    command.upgrade(config, "20260814_0009")
    engine = create_engine(database_url)
    assert TABLES.isdisjoint(inspect(engine).get_table_names())
    engine.dispose()

    command.upgrade(config, "20260814_0010")
    engine = create_engine(database_url)
    schema = inspect(engine)
    assert TABLES <= set(schema.get_table_names())
    assert {
        "ix_historical_event_partition_source",
        "ix_historical_event_partition_checkpoint",
        "ix_historical_event_partition_events",
    } <= {
        item["name"] for item in schema.get_indexes("historical_event_partitions")
    }

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO source_owner_approvals "
                "(source_key, decided_by, decided_at, identity_checksum, id) "
                "VALUES (:source_key, :decided_by, :decided_at, :identity, :id)"
            ),
            {
                "source_key": "statcast",
                "decided_by": "project-owner",
                "decided_at": "2026-08-14 12:00:00+00:00",
                "identity": "1" * 64,
                "id": "a" * 32,
            },
        )
        connection.execute(
            text(
                "INSERT INTO provider_access_authorizations "
                "(owner_approval_id, source_key, provider_name, access_method, "
                "decided_by, decided_at, decision_basis, identity_checksum, id) "
                "VALUES (:owner_id, :source_key, :provider, :method, :decided_by, "
                ":decided_at, :basis, :identity, :id)"
            ),
            {
                "owner_id": "a" * 32,
                "source_key": "statcast",
                "provider": "Baseball Savant / Statcast",
                "method": "UNRESOLVED",
                "decided_by": "project-owner",
                "decided_at": "2026-08-14 12:00:00+00:00",
                "basis": "No provider access authorization has been granted.",
                "identity": "2" * 64,
                "id": "b" * 32,
            },
        )
        result = connection.execute(
            text(
                "SELECT decision, authorized FROM provider_access_authorizations "
                "WHERE id = :id"
            ),
            {"id": "b" * 32},
        ).one()
        assert result.decision == "DENIED"
        assert not bool(result.authorized)
    engine.dispose()

    command.downgrade(config, "20260814_0009")
    engine = create_engine(database_url)
    assert TABLES.isdisjoint(inspect(engine).get_table_names())
    engine.dispose()


def test_storage_governance_models_are_exported_append_only_and_default_denied(
    session,
) -> None:
    expected = {
        SourceOwnerApproval,
        ProviderAccessAuthorization,
        ExistingEvidenceDerivedUseDecision,
        HistoricalAcquisitionStage,
        HistoricalRequestAttempt,
        HistoricalEventPartition,
    }
    assert expected <= set(_APPEND_ONLY_TYPES)

    owner = SourceOwnerApproval(
        source_key="statcast",
        decided_by="project-owner",
        decided_at=datetime(2026, 8, 14, tzinfo=UTC),
        identity_checksum="1" * 64,
    )
    session.add(owner)
    session.commit()
    assert owner.decision == "DENIED"
    assert owner.approved is False

    provider = ProviderAccessAuthorization(
        owner_approval_id=owner.id,
        source_key="statcast",
        provider_name="Baseball Savant / Statcast",
        access_method="UNRESOLVED",
        decided_by="project-owner",
        decided_at=datetime(2026, 8, 14, tzinfo=UTC),
        decision_basis="No provider access authorization has been granted.",
        identity_checksum="2" * 64,
    )
    derived = ExistingEvidenceDerivedUseDecision(
        owner_approval_id=owner.id,
        source_key="statcast",
        evidence_manifest_checksum="3" * 64,
        decided_by="project-owner",
        decided_at=datetime(2026, 8, 14, tzinfo=UTC),
        decision_basis="Existing evidence has not been approved for derivation.",
        identity_checksum="4" * 64,
    )
    session.add_all([provider, derived])
    session.commit()
    assert provider.decision == "DENIED"
    assert provider.authorized is False
    assert derived.decision == "DENIED"
    assert derived.allowed is False

    recorded_at = datetime(2026, 8, 14, 13, tzinfo=UTC)
    stage = HistoricalAcquisitionStage(
        source_version_id=uuid4(),
        owner_approval_id=owner.id,
        provider_authorization_id=provider.id,
        stage_key="D_2024_STATCAST_MONTH_PILOT",
        stage_revision=1,
        target_family="STATCAST",
        season=2024,
        month=4,
        status="FAILED",
        recorded_at=recorded_at,
        started_at=recorded_at - timedelta(minutes=2),
        completed_at=recorded_at - timedelta(minutes=1),
        physical_attempt_count=1,
        failure_count=1,
        identity_checksum="5" * 64,
    )
    session.add(stage)
    session.commit()
    attempt = HistoricalRequestAttempt(
        acquisition_stage_id=stage.id,
        endpoint_key="statcast-search",
        attempt_sequence=1,
        request_fingerprint="6" * 64,
        status="FAILED",
        requested_at=recorded_at - timedelta(minutes=2),
        completed_at=recorded_at - timedelta(minutes=1),
        records_received=0,
        bytes_received=0,
        elapsed_ms=60_000,
        error_type="CONTROLLED_TEST_FAILURE",
        identity_checksum="7" * 64,
    )
    session.add(attempt)
    session.commit()
    assert stage.physical_attempt_count == 1
    assert attempt.request_fingerprint == "6" * 64

    owner.approved = True
    owner.decision = "APPROVED"
    with pytest.raises(
        ImmutableRecordError,
        match="SourceOwnerApproval records are append-only",
    ):
        session.commit()
    session.rollback()

    persisted = session.get(ProviderAccessAuthorization, provider.id)
    assert persisted is not None
    session.delete(persisted)
    with pytest.raises(
        ImmutableRecordError,
        match="ProviderAccessAuthorization records are append-only",
    ):
        session.commit()
    session.rollback()


def test_partition_model_has_exact_family_counts_lineage_and_integrity_contract() -> None:
    table = HistoricalEventPartition.__table__
    columns = set(table.columns.keys())
    assert {
        "source_version_id",
        "checkpoint_id",
        "event_family",
        "season",
        "month",
        "logical_uri",
        "row_count",
        "game_count",
        "plate_appearance_count",
        "player_count",
        "min_event_date",
        "max_event_date",
        "min_event_at",
        "max_event_at",
        "schema_version",
        "schema_hash",
        "file_sha256",
        "raw_contributor_manifest",
        "raw_contributor_checksum",
        "code_revision",
        "evidence_tier",
        "timing_classification",
        "identity_checksum",
    } <= columns
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if hasattr(constraint, "sqltext")
    }
    assert "pitch" in checks["ck_historical_event_partition_family"]
    assert "plate_appearance" in checks["ck_historical_event_partition_family"]
    assert "row_count > 0" in checks["ck_historical_event_partition_counts"]
    assert "PARQUET" in checks["ck_historical_event_partition_storage"]
    assert "ZSTD" in checks["ck_historical_event_partition_storage"]
    assert "length(file_sha256) = 64" in checks[
        "ck_historical_event_partition_checksums"
    ]

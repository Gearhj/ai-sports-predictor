from __future__ import annotations

from datetime import UTC, datetime
from types import MappingProxyType, SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

import app.gen2.fd1_recovery_publication as publication
from app.gen2.fd1_recovery_models import (
    FD1RecoveryPublicationStage,
    FD1RecoveryRun,
)
from app.gen2.fd1_recovery_publication import (
    FD1_RECOVERY_PUBLICATION_SCHEMA_VERSION,
    FD1RecoveryPublicationConflict,
    FD1RecoveryPublicationError,
    PublicationPlan,
    PublicationTarget,
    _ingestion_run_target,
    _retrosheet_ledger_units,
    promote_publication_plan,
    promote_recovery_publication,
    stage_publication_plan,
)
from app.models import IngestionRun, RunStatus


NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


def _run_values(run_id: UUID) -> dict[str, object]:
    return {
        "id": run_id,
        "manifest_checksum": "a" * 64,
        "manifest_schema_version": "gate3c_fd1_recovery_manifest_v1",
        "manifest_logical_uri": (
            ".runtime/gate3c-fd1/recovery/fd1-recovery-manifest.json"
        ),
        "manifest_header": {"file_count": 329},
        "hash_cache_schema_version": "gate3c_fd1_recovery_hash_cache_v1",
        "hash_cache_checksum": "b" * 64,
        "generation_code_revision": "c" * 64,
        "finalizer_code_revision": "d" * 64,
        "identity_spool_logical_uri": (
            ".runtime/gate3c-fd1/recovery/identity-spool.csv"
        ),
        "identity_spool_sha256": "e" * 64,
        "identity_spool_row_count": 737_259,
        "status": "PLANNED",
        "progress": {},
        "performance_metrics": {},
    }


def _target() -> PublicationTarget:
    return _ingestion_run_target(
        code_revision="f" * 64,
        execution_at=NOW,
        unit_kind="identity_mapping",
        unit_identity_checksum="1" * 64,
        records_processed=10_000,
        result={
            "identity_mapping_batch_checksum": "1" * 64,
            "batch_number": 1,
            "source_keys": ["CHADWICK_REGISTER_ARCHIVES"],
        },
    )


def test_publication_target_and_plan_are_content_addressed() -> None:
    target = _target()
    run_id = UUID("11111111-1111-1111-1111-111111111111")
    first = PublicationPlan.create(
        recovery_id=run_id,
        scope="FULL",
        targets=[target, target],
    )
    second = PublicationPlan.create(
        recovery_id=run_id,
        scope="FULL",
        targets=[target],
    )

    assert first == second
    assert first.schema_version == FD1_RECOVERY_PUBLICATION_SCHEMA_VERSION
    assert len(first.targets) == 1
    assert first.plan_checksum == second.plan_checksum

    with pytest.raises(FD1RecoveryPublicationError, match="payload checksum"):
        PublicationTarget(
            object_kind=target.object_kind,
            target_id=target.target_id,
            natural_key_checksum=target.natural_key_checksum,
            semantic_identity_checksum=target.semantic_identity_checksum,
            target_payload=target.target_payload,
            payload_checksum="0" * 64,
        )


def test_identity_batches_allow_same_number_with_distinct_checksums(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = (
        {
            "batch_number": 1,
            "batch_checksum": "1" * 64,
            "source_key": "CHADWICK_REGISTER_ARCHIVES",
            "evidence_logical_uri": "raw/person.csv",
            "staging_ordinal": 1,
        },
        {
            "batch_number": 1,
            "batch_checksum": "2" * 64,
            "source_key": "CHADWICK_REGISTER_ARCHIVES",
            "evidence_logical_uri": "raw/org.csv",
            "staging_ordinal": 2,
        },
    )

    class _Rows:
        def mappings(self) -> _Rows:
            return self

        def __iter__(self):
            return iter(rows)

    class _Connection:
        def scalar(self, _statement):
            return None

        def execute(self, _statement):
            return _Rows()

    class _Registry:
        def context(self, _source_key: str) -> object:
            return object()

    decision = PublicationTarget.create(
        object_kind="DERIVED_USE_DECISION",
        target_id=uuid4(),
        natural_key={"identity_checksum": "3" * 64},
        semantic_identity_checksum="3" * 64,
        target_payload={"identity_checksum": "3" * 64},
    )
    monkeypatch.setattr(
        publication,
        "_identity_contributors",
        lambda *_args, **_kwargs: ({"retrieved_at": NOW.isoformat()},),
    )
    monkeypatch.setattr(
        publication,
        "_derived_use_target",
        lambda *_args, **_kwargs: decision,
    )

    targets, batch_count = publication._identity_targets(  # noqa: SLF001
        _Connection(),
        recovery_id=uuid4(),
        scope="FULL",
        registry=_Registry(),
        extractions={},
        code_revision="f" * 64,
        execution_at=NOW,
    )

    runs = [target for target in targets if target.object_kind == "INGESTION_RUN"]
    assert batch_count == 2
    assert len(runs) == 2
    assert {
        (
            target.target_payload["source_metadata"]["result"]["batch_number"],
            target.target_payload["source_metadata"]["result"][
                "identity_mapping_batch_checksum"
            ],
        )
        for target in runs
    } == {(1, "1" * 64), (1, "2" * 64)}


def test_publication_timeout_callback_is_recomputed_at_each_boundary() -> None:
    class _Connection:
        def __init__(self) -> None:
            self.parameters: list[dict[str, str]] = []

        def execute(self, _statement, parameters):
            self.parameters.append(parameters)

    connection = _Connection()
    remaining = iter((1_800_000, 23))

    publication._apply_publication_timeout(  # noqa: SLF001
        connection, remaining.__next__
    )
    publication._apply_publication_timeout(  # noqa: SLF001
        connection, remaining.__next__
    )

    assert connection.parameters == [
        {"timeout": "1800000ms"},
        {"timeout": "23ms"},
    ]


def test_target_classification_refreshes_deadline_before_each_query() -> None:
    class _Rows:
        def mappings(self) -> list[dict[str, object]]:
            return []

    class _Connection:
        def __init__(self) -> None:
            self.timeouts: list[str] = []

        def execute(self, statement, parameters=None):
            if "set_config('statement_timeout'" in str(statement):
                self.timeouts.append(parameters["timeout"])
                return object()
            return _Rows()

    targets = (
        _target(),
        _ingestion_run_target(
            code_revision="f" * 64,
            execution_at=NOW,
            unit_kind="identity_mapping",
            unit_identity_checksum="2" * 64,
            records_processed=5,
            result={"batch_number": 1},
        ),
    )
    remaining = iter((500, 400))
    connection = _Connection()

    classifications = publication._classify_targets(  # noqa: SLF001
        connection,
        targets,
        statement_timeout_ms=remaining.__next__,
    )

    assert classifications == (("INSERT", None, None), ("INSERT", None, None))
    assert connection.timeouts == ["500ms", "400ms"]


@pytest.mark.parametrize("timeout", [True, 0, 1_800_001])
def test_publication_timeout_is_positive_and_capped(timeout: object) -> None:
    with pytest.raises(ValueError, match="publication statement_timeout_ms"):
        publication._publication_timeout(timeout)  # noqa: SLF001


def test_publication_stage_is_bounded_invisible_and_atomic_promotion_is_idempotent(
    session,
) -> None:
    run_id = uuid4()
    session.add(FD1RecoveryRun(**_run_values(run_id)))
    session.commit()
    engine = session.get_bind()
    target = _target()
    plan = PublicationPlan.create(
        recovery_id=run_id,
        scope="FULL",
        targets=[target],
    )

    staged = stage_publication_plan(
        engine,
        run_id=run_id,
        scope="FULL",
        plan=plan,
        batch_size=1,
        validated_at=NOW,
    )
    assert staged.targets == 1
    assert staged.inserted_stage_rows == 1
    assert staged.proposed_inserts == 1
    assert staged.bounded_commits == 1
    with engine.connect() as connection:
        assert connection.get_transaction() is None
        assert connection.scalar(
            select(IngestionRun.id).where(IngestionRun.id == UUID(target.target_id))
        ) is None

    with engine.begin() as connection:
        promoted = promote_publication_plan(
            connection,
            run_id=run_id,
            scope="FULL",
            promoted_at=NOW,
            batch_size=1,
        )
        assert connection.in_transaction()
        assert promoted.canonical_rows_inserted == 1
        assert promoted.transaction_committed_by_function is False

    with engine.begin() as connection:
        repeated = promote_recovery_publication(
            connection,
            run_id,
            "FULL",
            promoted_at=NOW,
            batch_size=1,
        )
        assert repeated.canonical_rows_inserted == 0
        assert repeated.canonical_rows_reused == 1
        assert repeated.partitions == 0
        assert repeated.as_dict()["partitions"] == 0

    session.expire_all()
    canonical = session.get(IngestionRun, UUID(target.target_id))
    assert canonical is not None
    assert canonical.status is RunStatus.SUCCEEDED
    assert canonical.records_processed == 10_000
    stage_row = session.scalar(
        select(FD1RecoveryPublicationStage).where(
            FD1RecoveryPublicationStage.run_id == run_id
        )
    )
    assert stage_row is not None
    assert stage_row.action == "INSERT"
    assert stage_row.promoted_at is not None


def test_stage_persists_conflict_evidence_without_canonical_write(session) -> None:
    run_id = uuid4()
    session.add(FD1RecoveryRun(**_run_values(run_id)))
    target = _target()
    payload = dict(target.target_payload)
    metadata = dict(payload["source_metadata"])
    metadata["unit_identity_checksum"] = "2" * 64
    session.add(
        IngestionRun(
            id=UUID(target.target_id),
            source=payload["source"],
            job_name=payload["job_name"],
            status=RunStatus.SUCCEEDED,
            started_at=NOW,
            completed_at=NOW,
            records_processed=10_000,
            warnings=[],
            raw_reference=None,
            schema_version=payload["schema_version"],
            source_metadata=metadata,
        )
    )
    session.commit()
    engine = session.get_bind()
    plan = PublicationPlan.create(
        recovery_id=run_id,
        scope="FULL",
        targets=[target],
    )

    with pytest.raises(FD1RecoveryPublicationConflict, match="1 canonical conflict"):
        stage_publication_plan(
            engine,
            run_id=run_id,
            scope="FULL",
            plan=plan,
            batch_size=1,
            validated_at=NOW,
        )

    session.expire_all()
    row = session.scalar(
        select(FD1RecoveryPublicationStage).where(
            FD1RecoveryPublicationStage.run_id == run_id
        )
    )
    assert row is not None
    assert row.action == "CONFLICT"
    assert row.reason_code == "TARGET_ID_PAYLOAD_DIFFERS"
    assert row.promoted_at is None


def test_rehearsal_promotion_is_refused_before_any_write(session) -> None:
    connection = session.connection()
    with pytest.raises(FD1RecoveryPublicationError, match="REHEARSAL"):
        promote_publication_plan(
            connection,
            run_id=uuid4(),
            scope="REHEARSAL",
        )
    with pytest.raises(FD1RecoveryPublicationError, match="invalid for FULL"):
        promote_publication_plan(
            connection,
            run_id=uuid4(),
            scope="FULL",
            allow_rehearsal_rollback=True,
        )


def test_explicit_rehearsal_promotion_exercises_writes_then_caller_rolls_back(
    session,
) -> None:
    run_id = uuid4()
    session.add(FD1RecoveryRun(**_run_values(run_id)))
    session.commit()
    engine = session.get_bind()
    target = _target()
    plan = PublicationPlan.create(
        recovery_id=run_id,
        scope="REHEARSAL",
        targets=[target],
    )
    stage_publication_plan(
        engine,
        run_id=run_id,
        scope="REHEARSAL",
        plan=plan,
        batch_size=1,
        validated_at=NOW,
    )

    with engine.connect() as connection:
        transaction = connection.begin()
        rehearsed = promote_recovery_publication(
            connection,
            run_id,
            "REHEARSAL",
            allow_rehearsal_rollback=True,
            promoted_at=NOW,
            batch_size=1,
        )
        assert rehearsed.canonical_rows_inserted == 1
        assert rehearsed.transaction_committed_by_function is False
        assert connection.scalar(
            select(IngestionRun.id).where(IngestionRun.id == UUID(target.target_id))
        ) == UUID(target.target_id)
        transaction.rollback()

    with engine.connect() as connection:
        assert connection.scalar(
            select(IngestionRun.id).where(IngestionRun.id == UUID(target.target_id))
        ) is None
        stage_row = connection.execute(
            select(FD1RecoveryPublicationStage.__table__).where(
                FD1RecoveryPublicationStage.run_id == run_id
            )
        ).mappings().one()
        assert stage_row["action"] == "INSERT"
        assert stage_row["promoted_at"] is None


def test_v5_monthly_ledger_index_is_exact_and_conflict_detecting() -> None:
    row = {
        "season": 2025,
        "month": 4,
        "partition_checksums": {"weather": "a" * 64},
        "partition_quality_reports": {"weather": {"partial_month": False}},
        "family_rows": {"weather": 420},
        "selected_bundle_checksum": "b" * 64,
    }
    ledger = {
        "retrosheet_materialization": {
            "monthly": [row],
        }
    }

    units = _retrosheet_ledger_units(MappingProxyType(ledger))
    unit = units[(2025, 4, "weather")]
    assert unit.partition_checksum == "a" * 64
    assert unit.row_count == 420

    duplicated = {
        "retrosheet_materialization": {
            "monthly": [row, row],
        }
    }
    with pytest.raises(FD1RecoveryPublicationConflict, match="duplicated"):
        _retrosheet_ledger_units(duplicated)


def test_trusted_ledger_allows_only_governed_event_archives_outside_ledger() -> None:
    retro_key = publication._RETROSHEET_EVENT_SOURCE_KEY  # noqa: SLF001
    ledger_source_keys = sorted(set(publication.FD1_SOURCE_DEFINITIONS) - {retro_key})
    releases = []
    ledger_sources = {}
    for index, source_key in enumerate(ledger_source_keys, start=1):
        release = SimpleNamespace(
            source_key=source_key,
            source_version_id=str(UUID(int=index)),
            dataset_version=f"release-{index}",
            identity_checksum=f"{index:064x}",
            source_mode="LIVE",
        )
        releases.append(release)
        ledger_sources[source_key] = {
            "id": release.source_version_id,
            "dataset_version": release.dataset_version,
            "identity_checksum": release.identity_checksum,
            "source_mode": release.source_mode,
        }
    releases.append(
        SimpleNamespace(
            source_key=retro_key,
            source_version_id=str(UUID(int=99)),
            dataset_version="events-through-2025",
            identity_checksum="f" * 64,
            source_mode="LIVE",
        )
    )

    manifest_downloads = []
    ledger_downloads = []
    for index in range(12):
        source_key = ledger_source_keys[index % len(ledger_source_keys)]
        row = SimpleNamespace(
            source_key=source_key,
            object_key=f"ledger-object-{index}",
            download_identity_checksum=f"{100 + index:064x}",
            archive_sha256=f"{200 + index:064x}",
            content_length=1_000 + index,
            payload_sha256=f"{300 + index:064x}",
            raw_logical_uri=f"raw/ledger-{index}",
            source_release=f"release-{ledger_source_keys.index(source_key) + 1}",
        )
        manifest_downloads.append(row)
        ledger_downloads.append(
            {
                "source_key": row.source_key,
                "object_key": row.object_key,
                "download_identity_checksum": row.download_identity_checksum,
                "archive_sha256": row.archive_sha256,
                "content_length": row.content_length,
                "payload_sha256": row.payload_sha256,
                "raw_logical_uri": row.raw_logical_uri,
                "source_release": row.source_release,
            }
        )
    for index in range(10):
        manifest_downloads.append(
            SimpleNamespace(
                source_key=retro_key,
                object_key=f"retrosheet-events-{2016 + index}",
                download_identity_checksum=f"{400 + index:064x}",
                archive_sha256=f"{500 + index:064x}",
                content_length=2_000 + index,
                payload_sha256=f"{600 + index:064x}",
                raw_logical_uri=f"raw/events-{2016 + index}.zip",
                source_release="events-through-2025",
            )
        )

    lineage = SimpleNamespace(
        trusted_dry_run_ledger=SimpleNamespace(
            schema_version=publication.TRUSTED_LEDGER_SCHEMA_VERSION
        ),
        code_revision="a" * 64,
        source_releases=tuple(releases),
        source_archives=tuple(manifest_downloads),
    )
    manifest = SimpleNamespace(
        generation_lineage=lineage,
        aggregate=SimpleNamespace(row_count=123),
        identity_spool=SimpleNamespace(row_count=456),
    )
    ledger = {
        "schema_version": publication.TRUSTED_LEDGER_SCHEMA_VERSION,
        "mode": "dry-run",
        "success": True,
        "publication": {
            "code_revision": "a" * 64,
            "batches_seen": publication.FULL_PARTITION_COUNT,
            "parquet_rows": 123,
            "identity_mappings_seen": 456,
        },
        "prohibited_activity": {
            "network_requests": 0,
            "provider_transport_constructed": False,
        },
        "retrosheet_materialization": {"single_pass_source_scan": True},
        "source_versions": ledger_sources,
        "registered_downloads_used": ledger_downloads,
    }

    publication._validate_trusted_ledger(manifest, ledger)  # noqa: SLF001

    extra = SimpleNamespace(**vars(manifest_downloads[0]))
    extra.object_key = "unbound-extra"
    extra.download_identity_checksum = "e" * 64
    bad_lineage = SimpleNamespace(**vars(lineage))
    bad_lineage.source_archives = (*lineage.source_archives, extra)
    bad_manifest = SimpleNamespace(
        generation_lineage=bad_lineage,
        aggregate=manifest.aggregate,
        identity_spool=manifest.identity_spool,
    )
    with pytest.raises(FD1RecoveryPublicationError, match="downloads differ"):
        publication._validate_trusted_ledger(bad_manifest, ledger)  # noqa: SLF001

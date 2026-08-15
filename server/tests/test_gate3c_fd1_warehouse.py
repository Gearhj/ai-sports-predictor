from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from types import MappingProxyType

import polars as pl
import pytest
from sqlalchemy import event, func, select, update

from app.gen2.fd1_identity import (
    FD1_IDENTITY_SCHEMA_VERSION,
    GEN2_PRIMARY_POPULATION,
    LAHMAN_SOURCE_KEY,
    PlayerPriorPartitionBatch,
    RegistryIdentityMappingBatch,
    _identity_mapping_spec_payload,
)
from app.gen2.fd1_materializer import (
    RETROSHEET_SOURCE_KEY,
    FamilyStagingPayload,
)
from app.gen2.fd1_registry_sync import FD1_SOURCE_DEFINITIONS
from app.gen2.fd1_warehouse import (
    FD1_CODE_REVISION_INPUTS,
    FD1_PUBLICATION_ENVELOPE_SCHEMA_VERSION,
    ContributorDownloadBinding,
    FD1WarehouseError,
    build_fd1_code_revision,
    main as warehouse_main,
    publish_fd1_warehouse,
)
from app.gen2.open_data_acquisition import (
    DOWNLOAD_MANIFEST_SCHEMA_VERSION,
    EXTRACTION_MANIFEST_SCHEMA_VERSION,
    ArchiveFormat,
    AttemptAccounting,
    DownloadArtifact,
    DownloadDisposition,
    ExtractedMember,
    ExtractionArtifact,
)
from app.gen2.open_data_registry import (
    OpenDataDownloadSpec,
    OpenDataIdentityMappingSpec,
    OpenDataRegistry,
)
from app.gen2.parquet_store import HistoricalEventParquetStore
from app.models import (
    ExistingEvidenceDerivedUseDecision,
    HistoricalEventPartition,
    HistoryBackfillCheckpoint,
    IngestionRun,
    OpenDataIdentityMapping,
)


NOW = datetime(2026, 8, 14, 13, tzinfo=UTC)
REVISION = "a" * 64


def test_code_revision_attests_all_fd1_semantic_dependencies() -> None:
    required = {
        "app/gen2/context.py",
        "app/gen2/event_taxonomy.py",
        "app/gen2/fd1_identity.py",
        "app/gen2/fd1_materializer.py",
        "app/gen2/fd1_park_factors.py",
        "app/gen2/fd1_reconciliation.py",
        "app/gen2/fd1_warehouse.py",
        "app/gen2/retrosheet.py",
        "app/gen2/parquet_store.py",
        "app/models/entities.py",
    }
    assert required <= set(FD1_CODE_REVISION_INPUTS)
    assert len(FD1_CODE_REVISION_INPUTS) == len(set(FD1_CODE_REVISION_INPUTS))
    server_root = Path(__file__).resolve().parents[1]
    assert all((server_root / name).is_file() for name in FD1_CODE_REVISION_INPUTS)
    assert len(build_fd1_code_revision()) == 64


def _sha(value: object) -> str:
    if isinstance(value, bytes):
        encoded = value
    else:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _register_download(
    session,
    *,
    source_key: str,
    object_key: str,
    payload_sha256: str,
    archive_format: ArchiveFormat,
):
    definition = FD1_SOURCE_DEFINITIONS[source_key]
    registry = OpenDataRegistry(session)
    source = registry.ensure_source_release(definition.release_spec())
    owner, provider = registry.ensure_source_decisions(
        definition.owner_spec(), definition.provider_spec()
    )
    attempt = AttemptAccounting(
        attempt_number=1,
        started_at="2026-08-14T12:00:01+00:00",
        completed_at="2026-08-14T12:00:02+00:00",
        requested_range_start=0,
        status_code=200,
        response_content_length=12,
        received_bytes=12,
        outcome="SUCCEEDED",
    )
    artifact = DownloadArtifact(
        schema_version=DOWNLOAD_MANIFEST_SCHEMA_VERSION,
        source_key=source_key,
        object_key=object_key,
        sanitized_url="https://example.org/official/source.bin",
        policy_identity_checksum="b" * 64,
        authorization_identity_checksum=provider.identity_checksum,
        disposition=DownloadDisposition.CREATED_REVISION,
        revision=1,
        content_logical_uri=f"open-data/{source_key.lower()}/{object_key}/r1.blob",
        manifest_logical_uri=(
            f"open-data/{source_key.lower()}/{object_key}/acquisition.json"
        ),
        sha256=payload_sha256,
        content_length=12,
        mime_type=(
            "application/zip"
            if archive_format is ArchiveFormat.ZIP
            else "text/csv"
        ),
        archive_format=archive_format,
        http_status=200,
        started_at=attempt.started_at,
        completed_at=attempt.completed_at,
        physical_attempt_count=1,
        transport_bytes_received=12,
        attempts=(attempt,),
    )
    download = registry.record_download(
        source,
        provider,
        artifact,
        OpenDataDownloadSpec(
            source_key=source_key,
            source_release=definition.dataset_version,
            file_name=("source.zip" if archive_format is ArchiveFormat.ZIP else "source.csv"),
        ),
    )
    return source, download


def _extraction(download, member_sha: str) -> ExtractionArtifact:
    member = ExtractedMember(
        relative_path="gameinfo.csv", size_bytes=7, sha256=member_sha
    )
    semantic = {
        "schema_version": EXTRACTION_MANIFEST_SCHEMA_VERSION,
        "source_key": download.source_key,
        "object_key": download.object_key,
        "source_revision": download.upstream_revision,
        "archive_sha256": download.payload_sha256,
        "archive_format": ArchiveFormat.ZIP.value,
        "extraction_logical_uri": "open-data/retrosheet/extracted/r1",
        "manifest_logical_uri": (
            "open-data/retrosheet/extracted/r1/extraction-manifest.json"
        ),
        "extracted_at": "2026-08-14T12:00:03+00:00",
        "member_count": 1,
        "expanded_bytes": member.size_bytes,
        "members": [asdict(member)],
    }
    return ExtractionArtifact(
        **{
            key: value
            for key, value in semantic.items()
            if key not in {"archive_format", "members"}
        },
        archive_format=ArchiveFormat.ZIP,
        members=(member,),
        identity_checksum=_sha(semantic),
    )


def _retrosheet_batch(source_id, member_sha: str, *, scope="COMPLETE_MONTH_PARTITION"):
    row = {
        "source_key": RETROSHEET_SOURCE_KEY,
        "source_version_id": str(source_id),
        "game_id": "retrosheet:game:AAA202406010",
        "game_date": "2024-06-01",
        "event_date": "2024-06-01",
        "season": 2024,
        "month": 6,
        "player_id": "retrosheet:player:player1",
        "actual_start_flag": 1,
        "source_filename": "gameinfo.csv",
        "source_row_number": 2,
        "source_row_checksum": "c" * 64,
        "source_file_sha256": member_sha,
    }
    row["row_checksum"] = _sha(row)
    schema = "retrosheet_fd1_starter_usage_v1"
    bundle = "d" * 64
    partition = _sha(
        {
            "family": "starter_usage",
            "season": 2024,
            "month": 6,
            "schema_version": schema,
            "source_bundle_checksum": bundle,
            "rows": [row],
        }
    )
    return FamilyStagingPayload(
        family="starter_usage",
        season=2024,
        month=6,
        schema_version=schema,
        publication_scope=scope,
        rows=(MappingProxyType(row),),
        row_count=1,
        game_count=1,
        plate_appearance_count=0,
        player_count=1,
        min_event_date=date(2024, 6, 1),
        max_event_date=date(2024, 6, 1),
        source_file_checksums=MappingProxyType({"gameinfo.csv": member_sha}),
        source_bundle_checksum=bundle,
        partition_checksum=partition,
        leakage_policy="TEST_RETROSPECTIVE_ONLY",
        quality_report=MappingProxyType({"partial_month": False}),
    )


def _prior_batch(download) -> PlayerPriorPartitionBatch:
    row = MappingProxyType(
        {
            "event_family": "player_prior",
            "canonical_game_id": None,
            "canonical_player_id": "person-1",
            "player_id": None,
            "season": 2024,
            "official_date": "2024-12-31",
            "population": GEN2_PRIMARY_POPULATION,
            "source_key": LAHMAN_SOURCE_KEY,
        }
    )
    contributor = MappingProxyType(
        {
            "logical_uri": download.raw_logical_uri,
            "file_sha256": download.payload_sha256,
            "record_count": 1,
        }
    )
    return PlayerPriorPartitionBatch(
        event_family="player_prior",
        season=2024,
        month=12,
        population=GEN2_PRIMARY_POPULATION,
        batch_number=1,
        rows=(row,),
        row_count=1,
        player_count=1,
        min_official_date=date(2024, 12, 31),
        max_official_date=date(2024, 12, 31),
        raw_contributor_manifest=(contributor,),
        raw_contributor_checksum=_sha([dict(contributor)]),
        payload_checksum=_sha([dict(row)]),
    )


def _mapping_batch(source_key: str, evidence_uri: str) -> RegistryIdentityMappingBatch:
    spec = OpenDataIdentityMappingSpec(
        source_key=source_key,
        entity_type="person",
        source_namespace="retrosheet",
        external_id="player1",
        canonical_namespace="chadwick_uuid",
        canonical_id="00000000-0000-0000-0000-000000000001",
        source_row_number=2,
        source_row_checksum="e" * 64,
        confidence="EXACT",
        evidence_logical_uri=evidence_uri,
    )
    return RegistryIdentityMappingBatch(
        batch_number=1,
        specs=(spec,),
        batch_checksum=_sha([_identity_mapping_spec_payload(spec)]),
    )


def _mapping_batch_many(
    *specs: OpenDataIdentityMappingSpec,
) -> RegistryIdentityMappingBatch:
    return RegistryIdentityMappingBatch(
        batch_number=1,
        specs=tuple(specs),
        batch_checksum=_sha(
            [_identity_mapping_spec_payload(spec) for spec in specs]
        ),
    )


def test_publication_is_provider_free_transactional_and_idempotent(session, artifact_root):
    archive_sha = "1" * 64
    member_sha = "2" * 64
    retro_source, retro_download = _register_download(
        session,
        source_key=RETROSHEET_SOURCE_KEY,
        object_key="retrosheet-csv-through-2025",
        payload_sha256=archive_sha,
        archive_format=ArchiveFormat.ZIP,
    )
    _, lahman_download = _register_download(
        session,
        source_key=LAHMAN_SOURCE_KEY,
        object_key="lahman-2025-batting-csv",
        payload_sha256="3" * 64,
        archive_format=ArchiveFormat.NONE,
    )
    _, chadwick_download = _register_download(
        session,
        source_key="CHADWICK_REGISTER_ARCHIVES",
        object_key="chadwick-register-archive",
        payload_sha256="4" * 64,
        archive_format=ArchiveFormat.ZIP,
    )
    extraction = _extraction(retro_download, member_sha)
    binding = ContributorDownloadBinding(
        file_sha256=member_sha,
        download_identity_checksum=retro_download.identity_checksum,
        extraction_identity_checksum=extraction.identity_checksum,
        member_relative_path="gameinfo.csv",
    )
    retro_batch = _retrosheet_batch(retro_source.id, member_sha)
    prior_batch = _prior_batch(lahman_download)
    mapping_batch = _mapping_batch(
        "CHADWICK_REGISTER_ARCHIVES", chadwick_download.raw_logical_uri
    )
    store = HistoricalEventParquetStore(
        artifact_root / "curated",
        maximum_store_bytes=1024**3,
        free_space=lambda _: 200 * 1024**3,
    )

    before = {
        model: session.scalar(select(func.count()).select_from(model)) or 0
        for model in (
            IngestionRun,
            HistoryBackfillCheckpoint,
            HistoricalEventPartition,
            ExistingEvidenceDerivedUseDecision,
            OpenDataIdentityMapping,
        )
    }
    dry = publish_fd1_warehouse(
        session,
        store,
        retrosheet_batches=(retro_batch,),
        player_prior_batches=(prior_batch,),
        identity_mapping_batches=(mapping_batch,),
        contributor_bindings=(binding,),
        extraction_artifacts=(extraction,),
        code_revision=REVISION,
        dry_run=True,
        execution_at=NOW,
        allowed_store_roots=(artifact_root,),
    )
    assert dry.mode == "dry-run"
    assert dry.network_requests == 0
    assert not list((artifact_root / "curated").rglob("*.parquet"))
    assert before == {
        model: session.scalar(select(func.count()).select_from(model)) or 0
        for model in before
    }

    first = publish_fd1_warehouse(
        session,
        store,
        retrosheet_batches=(retro_batch,),
        player_prior_batches=(prior_batch,),
        identity_mapping_batches=(mapping_batch,),
        contributor_bindings=(binding,),
        extraction_artifacts=(extraction,),
        code_revision=REVISION,
        execution_at=NOW,
        allowed_store_roots=(artifact_root,),
    )
    assert first.partition_rows_created == 2
    assert first.ingestion_run_rows_created == 3
    assert first.checkpoint_rows_created == 2
    assert first.identity_mapping_rows_created == 1
    assert first.derived_use_rows_created == 3
    assert first.network_requests == 0
    partitions = tuple(session.scalars(select(HistoricalEventPartition)))
    assert {item.event_family for item in partitions} == {
        "starter_usage",
        "player_prior",
    }
    assert {item.compression for item in partitions} == {"ZSTD"}
    assert {item.source_version_id for item in partitions} == {
        retro_source.id,
        lahman_download.source_version_id,
    }
    checkpoints = tuple(session.scalars(select(HistoryBackfillCheckpoint)))
    assert len(checkpoints) == 2
    assert {item.source_version_id for item in checkpoints} == {
        retro_source.id,
        lahman_download.source_version_id,
    }
    assert {item.status for item in checkpoints} == {"SUCCEEDED"}
    for partition in partitions:
        frame = pl.read_parquet(store.resolve(partition.logical_uri))
        assert frame.height == 1

    # Simulate an operator checkpoint commit and a fresh ORM identity map.  A
    # restart must reuse both SQL rows and already-published immutable bytes.
    session.commit()
    session.expire_all()
    second = publish_fd1_warehouse(
        session,
        store,
        retrosheet_batches=(retro_batch,),
        player_prior_batches=(prior_batch,),
        identity_mapping_batches=(mapping_batch,),
        contributor_bindings=(binding,),
        extraction_artifacts=(extraction,),
        code_revision=REVISION,
        execution_at=NOW,
        allowed_store_roots=(artifact_root,),
    )
    assert second.partition_rows_reused == 2
    assert second.ingestion_run_rows_reused == 3
    assert second.checkpoint_rows_reused == 2
    assert second.identity_mapping_rows_reused == 1
    assert second.derived_use_rows_reused == 3
    assert session.scalar(select(func.count()).select_from(IngestionRun)) == 3
    assert session.scalar(select(func.count()).select_from(HistoricalEventPartition)) == 2

    retro_checkpoint = next(
        item for item in session.scalars(select(HistoryBackfillCheckpoint))
        if item.stream_key == "fd1:starter_usage"
    )
    session.execute(
        update(HistoryBackfillCheckpoint)
        .where(HistoryBackfillCheckpoint.id == retro_checkpoint.id)
        .values(records_received=2)
    )
    session.expire_all()
    with pytest.raises(FD1WarehouseError, match="records_received"):
        publish_fd1_warehouse(
            session,
            store,
            retrosheet_batches=(retro_batch,),
            contributor_bindings=(binding,),
            extraction_artifacts=(extraction,),
            code_revision=REVISION,
            execution_at=NOW,
            allowed_store_roots=(artifact_root,),
        )


def test_publication_rejects_partial_month_and_unproved_archive_member(
    session, artifact_root
):
    retro_source, retro_download = _register_download(
        session,
        source_key=RETROSHEET_SOURCE_KEY,
        object_key="retrosheet-csv-through-2025",
        payload_sha256="5" * 64,
        archive_format=ArchiveFormat.ZIP,
    )
    member_sha = "6" * 64
    batch = _retrosheet_batch(retro_source.id, member_sha, scope="PILOT_OR_PARTIAL_MONTH_STAGING")
    store = HistoricalEventParquetStore(
        artifact_root / "curated",
        free_space=lambda _: 200 * 1024**3,
    )
    with pytest.raises(FD1WarehouseError, match="partial-month"):
        publish_fd1_warehouse(
            session,
            store,
            retrosheet_batches=(batch,),
            code_revision=REVISION,
            dry_run=True,
            execution_at=NOW,
            allowed_store_roots=(artifact_root,),
        )

    complete = _retrosheet_batch(retro_source.id, member_sha)
    unproved = ContributorDownloadBinding(
        file_sha256=member_sha,
        download_identity_checksum=retro_download.identity_checksum,
    )
    with pytest.raises(FD1WarehouseError, match="requires exact extraction lineage"):
        publish_fd1_warehouse(
            session,
            store,
            retrosheet_batches=(complete,),
            contributor_bindings=(unproved,),
            code_revision=REVISION,
            dry_run=True,
            execution_at=NOW,
            allowed_store_roots=(artifact_root,),
        )


def test_publication_rejects_a_store_below_the_100_gib_reserve(
    session, artifact_root
):
    store = HistoricalEventParquetStore(
        artifact_root / "curated",
        minimum_free_bytes_after_write=0,
        free_space=lambda _: 200 * 1024**3,
    )
    with pytest.raises(FD1WarehouseError, match="100-GiB"):
        publish_fd1_warehouse(
            session,
            store,
            dry_run=True,
            code_revision=REVISION,
            execution_at=NOW,
            allowed_store_roots=(artifact_root,),
        )


def test_mixed_source_identity_batch_is_source_bound_and_duplicate_safe(
    session, artifact_root
):
    _, chadwick_download = _register_download(
        session,
        source_key="CHADWICK_REGISTER_ARCHIVES",
        object_key="chadwick-register-archive",
        payload_sha256="7" * 64,
        archive_format=ArchiveFormat.ZIP,
    )
    _, lahman_download = _register_download(
        session,
        source_key=LAHMAN_SOURCE_KEY,
        object_key="lahman-2025-people-csv",
        payload_sha256="8" * 64,
        archive_format=ArchiveFormat.NONE,
    )
    chadwick = OpenDataIdentityMappingSpec(
        source_key="CHADWICK_REGISTER_ARCHIVES",
        entity_type="person",
        source_namespace="retrosheet",
        external_id="player1",
        canonical_namespace="chadwick_uuid",
        canonical_id="00000000-0000-0000-0000-000000000001",
        source_row_number=2,
        source_row_checksum="9" * 64,
        confidence="EXACT",
        evidence_logical_uri=chadwick_download.raw_logical_uri,
    )
    lahman = OpenDataIdentityMappingSpec(
        source_key=LAHMAN_SOURCE_KEY,
        entity_type="person",
        source_namespace="lahman",
        external_id="player01",
        canonical_namespace="chadwick_uuid",
        canonical_id="00000000-0000-0000-0000-000000000001",
        source_row_number=2,
        source_row_checksum="a" * 64,
        confidence="EXACT",
        evidence_logical_uri=lahman_download.raw_logical_uri,
    )
    store = HistoricalEventParquetStore(
        artifact_root / "curated",
        free_space=lambda _: 200 * 1024**3,
    )
    summary = publish_fd1_warehouse(
        session,
        store,
        identity_mapping_batches=(_mapping_batch_many(chadwick, lahman),),
        code_revision=REVISION,
        execution_at=NOW,
        allowed_store_roots=(artifact_root,),
    )
    assert summary.identity_mapping_rows_created == 2
    assert summary.derived_use_rows_created == 2
    assert summary.ingestion_run_rows_created == 1
    rows = tuple(session.scalars(select(OpenDataIdentityMapping)))
    assert {row.source_version_id for row in rows} == {
        chadwick_download.source_version_id,
        lahman_download.source_version_id,
    }

    duplicate = _mapping_batch_many(chadwick, chadwick)
    with pytest.raises(FD1WarehouseError, match="duplicate mapping"):
        publish_fd1_warehouse(
            session,
            store,
            identity_mapping_batches=(duplicate,),
            code_revision=REVISION,
            dry_run=True,
            execution_at=NOW,
            allowed_store_roots=(artifact_root,),
        )


def test_large_identity_batch_has_bounded_queries_and_is_idempotent(
    session, artifact_root
):
    _, chadwick_download = _register_download(
        session,
        source_key="CHADWICK_REGISTER_ARCHIVES",
        object_key="chadwick-register-archive",
        payload_sha256="d" * 64,
        archive_format=ArchiveFormat.ZIP,
    )
    specs = tuple(
        OpenDataIdentityMappingSpec(
            source_key="CHADWICK_REGISTER_ARCHIVES",
            entity_type="person",
            source_namespace="retrosheet",
            external_id=f"player{index:04d}",
            canonical_namespace="chadwick_uuid",
            canonical_id=f"00000000-0000-0000-0000-{index:012d}",
            source_row_number=index + 2,
            source_row_checksum=_sha({"row": index}),
            confidence="EXACT",
            evidence_logical_uri=chadwick_download.raw_logical_uri,
        )
        for index in range(1_200)
    )
    batch = _mapping_batch_many(*specs)
    store = HistoricalEventParquetStore(
        artifact_root / "curated",
        free_space=lambda _: 200 * 1024**3,
    )
    engine = session.get_bind()

    def run_and_count(*, dry_run: bool):
        identity_selects = 0

        def count_identity_selects(
            _connection, _cursor, statement, _parameters, _context, _executemany
        ):
            nonlocal identity_selects
            normalized = statement.lstrip().upper()
            if (
                normalized.startswith("SELECT")
                and "OPEN_DATA_IDENTITY_MAPPINGS" in normalized
            ):
                identity_selects += 1

        event.listen(engine, "before_cursor_execute", count_identity_selects)
        try:
            summary = publish_fd1_warehouse(
                session,
                store,
                identity_mapping_batches=(batch,),
                code_revision=REVISION,
                dry_run=dry_run,
                execution_at=NOW,
                allowed_store_roots=(artifact_root,),
            )
        finally:
            event.remove(engine, "before_cursor_execute", count_identity_selects)
        return summary, identity_selects

    dry, dry_selects = run_and_count(dry_run=True)
    assert dry.identity_mappings_seen == 1_200
    assert dry_selects == 3
    assert (
        session.scalar(select(func.count()).select_from(OpenDataIdentityMapping))
        == 0
    )

    first, first_selects = run_and_count(dry_run=False)
    assert first.identity_mapping_rows_created == 1_200
    assert first.identity_mapping_rows_reused == 0
    assert first_selects == 3
    session.commit()
    session.expire_all()

    second, second_selects = run_and_count(dry_run=False)
    assert second.identity_mapping_rows_created == 0
    assert second.identity_mapping_rows_reused == 1_200
    assert second_selects == 3
    assert (
        session.scalar(select(func.count()).select_from(OpenDataIdentityMapping))
        == 1_200
    )

    first_spec = specs[0]
    conflict = OpenDataIdentityMappingSpec(
        source_key=first_spec.source_key,
        entity_type=first_spec.entity_type,
        source_namespace=first_spec.source_namespace,
        external_id=first_spec.external_id,
        canonical_namespace=first_spec.canonical_namespace,
        canonical_id="00000000-0000-0000-0000-999999999999",
        source_row_number=first_spec.source_row_number,
        source_row_checksum=first_spec.source_row_checksum,
        confidence=first_spec.confidence,
        evidence_logical_uri=first_spec.evidence_logical_uri,
    )
    with pytest.raises(FD1WarehouseError, match="identity mapping"):
        publish_fd1_warehouse(
            session,
            store,
            identity_mapping_batches=(_mapping_batch_many(conflict),),
            code_revision=REVISION,
            dry_run=True,
            execution_at=NOW,
            allowed_store_roots=(artifact_root,),
        )


def test_cli_execute_rejects_unverified_archive_metadata(
    session, artifact_root
):
    _, download = _register_download(
        session,
        source_key=RETROSHEET_SOURCE_KEY,
        object_key="retrosheet-csv-through-2025",
        payload_sha256="b" * 64,
        archive_format=ArchiveFormat.ZIP,
    )
    extraction = _extraction(download, "c" * 64)
    extraction_document = asdict(extraction)
    extraction_document["archive_format"] = extraction.archive_format.value
    envelope = {
        "schema_version": FD1_PUBLICATION_ENVELOPE_SCHEMA_VERSION,
        "retrosheet_batches": [],
        "player_prior_batches": [],
        "identity_mapping_batches": [],
        "contributor_bindings": [],
        "extraction_artifacts": [extraction_document],
    }
    path = artifact_root / "unverified-envelope.json"
    path.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(FD1WarehouseError, match="JSON cannot prove member bytes"):
        warehouse_main(
            (
                "execute",
                "--envelope",
                str(path),
                "--store-root",
                str(artifact_root / "curated"),
            )
        )

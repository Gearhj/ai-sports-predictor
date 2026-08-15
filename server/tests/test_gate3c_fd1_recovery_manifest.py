from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import polars as pl
import pytest

import app.gen2.fd1_recovery_manifest as recovery
from app.gen2.fd1_recovery_manifest import (
    GIB,
    FD1RecoveryIntegrityError,
    FD1RecoveryStorageError,
    FinalizerLineage,
    GenerationLineage,
    IdentitySpoolBinding,
    InterruptedExecutionContext,
    RecoveryHashCache,
    RecoveryManifest,
    SourceArchiveBinding,
    SourceReleaseBinding,
    TrustedDryRunLedger,
    build_recovery_manifest,
    load_recovery_hash_cache,
    load_recovery_manifest,
    manifest_paths,
    write_recovery_hash_cache,
    write_recovery_manifest,
)


SOURCE_VERSION = "64db602a-090a-4e37-b8f2-2a48d36dd7f2"
LAHMAN_VERSION = "3af682f0-ecdb-47d8-8e14-660ec5f7455c"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _partition(root: Path, month: int) -> str:
    temporary = root / f"weather-{month}.parquet"
    pl.DataFrame(
        [
            {
                "source_key": "RETROSHEET_CSV_DOWNLOADS",
                "source_version_id": SOURCE_VERSION,
                "season": 2025,
                "month": month,
                "game_date": f"2025-{month:02d}-01",
                "game_id": f"retrosheet:game:2025-{month}",
                # Deliberately exercise the real all-null physical-schema variant.
                "doubleheader_id": None if month == 4 else "2",
            },
            {
                "source_key": "RETROSHEET_CSV_DOWNLOADS",
                "source_version_id": SOURCE_VERSION,
                "season": 2025,
                "month": month,
                "game_date": f"2025-{month:02d}-02",
                "game_id": f"retrosheet:game:2025-{month}-b",
                "doubleheader_id": None if month == 4 else "2",
            },
        ]
    ).write_parquet(temporary, compression="zstd", statistics=True)
    digest = _sha256(temporary)
    checkpoint = f"00000000-0000-5000-8000-{month:012d}"
    governance = f"{month:064x}"
    directory = (
        root
        / "historical-events"
        / "source=retrosheet_csv_downloads"
        / "family=weather"
        / "season=2025"
        / f"month={month:02d}"
        / "schema=retrosheet_fd1_weather_v8"
        / f"source-version={SOURCE_VERSION}"
        / f"checkpoint={checkpoint}"
        / f"governance={governance}"
    )
    directory.mkdir(parents=True)
    final = directory / f"part-{digest}.parquet"
    temporary.replace(final)
    return final.relative_to(root).as_posix()


def _prior_partition(root: Path) -> str:
    temporary = root / "prior.parquet"
    pl.DataFrame(
        [
            {
                "source_key": "SABR_LAHMAN_BOX_DOWNLOADS",
                "season": 2025,
                "official_date": "2025-12-31",
                "canonical_player_id": "player-1",
            }
        ]
    ).write_parquet(temporary, compression="zstd", statistics=True)
    digest = _sha256(temporary)
    directory = (
        root
        / "historical-events"
        / "source=sabr_lahman_box_downloads"
        / "family=player_prior"
        / "season=2025"
        / "month=12"
        / "schema=gate3c_fd1_identity_prior_v1"
        / f"source-version={LAHMAN_VERSION}"
        / "checkpoint=00000000-0000-5000-8000-000000000012"
        / f"governance={'1' * 64}"
    )
    directory.mkdir(parents=True)
    final = directory / f"part-{digest}.parquet"
    temporary.replace(final)
    return final.relative_to(root).as_posix()


def _lineage(*, files: int, byte_count: int) -> GenerationLineage:
    return GenerationLineage(
        code_revision="8c8da2e335f6af77fd204cadc20180e6077ffeccab881ca60eed1ce4d73452b7",
        trusted_dry_run_ledger=TrustedDryRunLedger(
            logical_uri=(
                ".runtime/gate3c-fd1/artifacts/"
                "publish-actual-dry-run-pretransaction-closeout.json"
            ),
            sha256="a" * 64,
            schema_version="gate3c_fd1_actual_publication_operator_v5",
        ),
        source_releases=(
            SourceReleaseBinding(
                source_key="RETROSHEET_CSV_DOWNLOADS",
                source_version_id=SOURCE_VERSION,
                dataset_version="2025-current",
                identity_checksum="b" * 64,
                source_mode="LIVE",
            ),
        ),
        source_archives=(
            SourceArchiveBinding(
                source_key="RETROSHEET_CSV_DOWNLOADS",
                object_key="retrosheet-csv",
                source_release="2025-current",
                raw_logical_uri="raw/open-data/retrosheet/retrosheet-csv.zip",
                archive_sha256="c" * 64,
                payload_sha256="d" * 64,
                content_length=1234,
                download_identity_checksum="e" * 64,
            ),
        ),
        interrupted_execution=InterruptedExecutionContext(
            attempted_command="python publish_actual.py --execute",
            started_at_utc="2026-08-15T12:39:06Z",
            stopped_at_utc="2026-08-15T14:29:54Z",
            completed_files=files,
            completed_bytes=byte_count,
            sql_rolled_back=True,
            final_execute_evidence_present=False,
            suspected_slow_stage="IDENTITY_MAPPING_ORM_FLUSH",
        ),
    )


def _spool() -> IdentitySpoolBinding:
    return IdentitySpoolBinding(
        logical_uri=".runtime/gate3c-fd1/recovery/identity-spool.csv",
        schema_version="gate3c_fd1_identity_spool_v1",
        sha256="f" * 64,
        byte_count=500,
        row_count=20,
    )


def _build(
    root: Path,
    uris: list[str],
    *,
    cache: RecoveryHashCache | None = None,
    progress=None,
):
    byte_count = sum((root / Path(uri)).stat().st_size for uri in uris)
    return build_recovery_manifest(
        store_root=root,
        logical_uris=uris,
        generation_lineage=_lineage(files=len(uris), byte_count=byte_count),
        finalizer_lineage=FinalizerLineage(
            code_revision="9" * 40,
            command_schema_version="finalize-fd1-from-existing-parquet-v1",
        ),
        identity_spool=_spool(),
        hash_cache=cache,
        expected_file_count=len(uris),
        heartbeat_seconds=0,
        progress_callback=progress,
        free_space=lambda _: 200 * GIB,
    )


def test_explicit_build_freezes_lineages_spool_schema_and_content_addressed_outputs(
    artifact_root: Path,
) -> None:
    store = artifact_root / "store"
    store.mkdir()
    uris = [_partition(store, 4), _partition(store, 5)]
    # This valid but unmanifested file proves that the builder never discovers extras.
    _partition(store, 6)
    progress = []

    result = _build(store, uris, progress=progress.append)

    assert len(result.manifest.files) == 2
    assert result.manifest.aggregate.row_count == 4
    assert result.manifest.aggregate.byte_count == sum(
        (store / Path(uri)).stat().st_size for uri in uris
    )
    assert result.files_hashed == 2
    assert result.cache_hits == 0
    assert result.bytes_hashed == result.manifest.aggregate.byte_count
    assert progress[0].stage == "FILES_VALIDATING"
    assert progress[-1].stage == "FILES_VALIDATED"
    assert progress[-1].files_validated == 2
    assert UUID(result.manifest.recovery_id).version == 5
    assert result.manifest.generation_lineage.code_revision.startswith("8c8da2")
    assert result.manifest.finalizer_lineage.code_revision == "9" * 40
    assert result.manifest.identity_spool == _spool()
    assert "run_id" not in result.manifest.identity_spool.to_dict()
    assert (
        result.manifest.files[0].physical_schema_fingerprint
        != result.manifest.files[1].physical_schema_fingerprint
    )
    assert [path.name for path in manifest_paths(result.manifest, store_root=store)] == [
        Path(uri).name for uri in sorted(uris)
    ]

    output = artifact_root / "recovery-artifacts"
    manifest_artifact = write_recovery_manifest(
        result.manifest,
        output_root=output,
        free_space=lambda _: 200 * GIB,
    )
    cache_artifact = write_recovery_hash_cache(
        result.hash_cache,
        output_root=output,
        free_space=lambda _: 200 * GIB,
    )
    assert manifest_artifact.path.name == (
        f"fd1-recovery-manifest-sha256-{manifest_artifact.sha256}.json"
    )
    assert cache_artifact.path.name == (
        f"fd1-recovery-hash-cache-sha256-{cache_artifact.sha256}.json"
    )
    assert load_recovery_manifest(manifest_artifact.path, expected_file_count=2) == (
        result.manifest
    )
    assert load_recovery_hash_cache(cache_artifact.path) == result.hash_cache
    assert write_recovery_manifest(
        result.manifest,
        output_root=output,
        free_space=lambda _: 200 * GIB,
    ).reused


def test_hash_cache_hit_skips_hash_and_stat_change_hashes_only_changed_file(
    artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = artifact_root / "store"
    store.mkdir()
    uris = [_partition(store, 4), _partition(store, 5)]
    calls: list[str] = []
    original = recovery._sha256_file

    def spy(path: Path, *, on_chunk=None) -> str:
        calls.append(path.name)
        return original(path, on_chunk=on_chunk)

    monkeypatch.setattr(recovery, "_sha256_file", spy)
    first = _build(store, uris)
    assert len(calls) == 2

    calls.clear()
    second = _build(store, uris, cache=first.hash_cache)
    assert calls == []
    assert second.cache_hits == 2
    assert second.files_hashed == 0
    assert second.bytes_hashed == 0
    assert second.manifest == first.manifest

    changed = store / Path(uris[1])
    before = changed.stat()
    os.utime(changed, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000))
    calls.clear()
    third = _build(store, uris, cache=second.hash_cache)
    assert calls == [changed.name]
    assert third.cache_hits == 1
    assert third.files_hashed == 1
    assert third.bytes_hashed == changed.stat().st_size
    assert third.manifest.recovery_id != second.manifest.recovery_id


def test_player_prior_partition_uses_official_date_without_internal_month(
    artifact_root: Path,
) -> None:
    store = artifact_root / "store"
    store.mkdir()
    uri = _prior_partition(store)
    size = (store / Path(uri)).stat().st_size
    lineage = GenerationLineage(
        code_revision="8" * 40,
        trusted_dry_run_ledger=TrustedDryRunLedger(
            logical_uri=".runtime/gate3c-fd1/dry-run-ledger.json",
            sha256="a" * 64,
            schema_version="operator-v5",
        ),
        source_releases=(
            SourceReleaseBinding(
                source_key="SABR_LAHMAN_BOX_DOWNLOADS",
                source_version_id=LAHMAN_VERSION,
                dataset_version="2025",
                identity_checksum="b" * 64,
                source_mode="LIVE",
            ),
        ),
        source_archives=(
            SourceArchiveBinding(
                source_key="SABR_LAHMAN_BOX_DOWNLOADS",
                object_key="lahman",
                source_release="2025",
                raw_logical_uri="raw/open-data/lahman.zip",
                archive_sha256="c" * 64,
                payload_sha256="d" * 64,
                content_length=10,
                download_identity_checksum="e" * 64,
            ),
        ),
        interrupted_execution=InterruptedExecutionContext(
            attempted_command="publish --execute",
            started_at_utc=None,
            stopped_at_utc=None,
            completed_files=1,
            completed_bytes=size,
            sql_rolled_back=True,
            final_execute_evidence_present=False,
            suspected_slow_stage="IDENTITY",
        ),
    )
    result = build_recovery_manifest(
        store_root=store,
        logical_uris=[uri],
        generation_lineage=lineage,
        finalizer_lineage=FinalizerLineage("9" * 40, "v1"),
        identity_spool=_spool(),
        expected_file_count=1,
        free_space=lambda _: 200 * GIB,
    )
    entry = result.manifest.files[0]
    assert (entry.family, entry.month, entry.min_date, entry.max_date) == (
        "player_prior",
        12,
        "2025-12-31",
        "2025-12-31",
    )


def test_strict_checksums_recovery_uuid_spool_schema_count_and_reserve_fail_closed(
    artifact_root: Path,
) -> None:
    store = artifact_root / "store"
    store.mkdir()
    uri = _partition(store, 4)
    result = _build(store, [uri])

    cache_payload = result.hash_cache.to_dict()
    cache_payload["entries"][0]["row_count"] += 1
    with pytest.raises(FD1RecoveryIntegrityError, match="cache checksum"):
        RecoveryHashCache.from_dict(cache_payload)

    manifest_payload = result.manifest.to_dict()
    manifest_payload["identity_spool"]["sha256"] = "0" * 64
    with pytest.raises(FD1RecoveryIntegrityError, match="Recovery UUID"):
        RecoveryManifest.from_dict(manifest_payload)

    spool_payload = _spool().to_dict()
    spool_payload["run_id"] = "must-not-appear"
    with pytest.raises(FD1RecoveryIntegrityError, match="fields are not exact"):
        IdentitySpoolBinding.from_dict(spool_payload)
    with pytest.raises(FD1RecoveryIntegrityError, match="ignored FD1 recovery"):
        IdentitySpoolBinding(
            logical_uri="data/identity-spool.csv",
            schema_version="v1",
            sha256="f" * 64,
            byte_count=1,
            row_count=1,
        )

    with pytest.raises(FD1RecoveryIntegrityError, match="exactly 329"):
        build_recovery_manifest(
            store_root=store,
            logical_uris=[uri],
            generation_lineage=_lineage(
                files=1,
                byte_count=(store / Path(uri)).stat().st_size,
            ),
            finalizer_lineage=result.manifest.finalizer_lineage,
            identity_spool=_spool(),
            free_space=lambda _: 200 * GIB,
        )

    with pytest.raises(FD1RecoveryStorageError, match="100-GiB reserve"):
        build_recovery_manifest(
            store_root=store,
            logical_uris=[uri],
            generation_lineage=_lineage(
                files=1,
                byte_count=(store / Path(uri)).stat().st_size,
            ),
            finalizer_lineage=result.manifest.finalizer_lineage,
            identity_spool=_spool(),
            expected_file_count=1,
            free_space=lambda _: 100 * GIB - 1,
        )

    with pytest.raises(ValueError, match="cannot weaken"):
        build_recovery_manifest(
            store_root=store,
            logical_uris=[uri],
            generation_lineage=_lineage(
                files=1,
                byte_count=(store / Path(uri)).stat().st_size,
            ),
            finalizer_lineage=result.manifest.finalizer_lineage,
            identity_spool=_spool(),
            expected_file_count=1,
            minimum_free_bytes=0,
            free_space=lambda _: 200 * GIB,
        )

    with pytest.raises(ValueError, match="between 0 and 30"):
        build_recovery_manifest(
            store_root=store,
            logical_uris=[uri],
            generation_lineage=_lineage(
                files=1,
                byte_count=(store / Path(uri)).stat().st_size,
            ),
            finalizer_lineage=result.manifest.finalizer_lineage,
            identity_spool=_spool(),
            expected_file_count=1,
            heartbeat_seconds=31,
            free_space=lambda _: 200 * GIB,
        )

    with pytest.raises(FD1RecoveryStorageError, match="100-GiB reserve"):
        write_recovery_manifest(
            result.manifest,
            output_root=artifact_root / "reserve-output",
            free_space=lambda _: 100 * GIB,
        )


def test_lineage_order_release_binding_and_logical_uris_are_canonical() -> None:
    base = _lineage(files=1, byte_count=1)
    with pytest.raises(FD1RecoveryIntegrityError, match="matching source release"):
        GenerationLineage(
            code_revision=base.code_revision,
            trusted_dry_run_ledger=base.trusted_dry_run_ledger,
            source_releases=base.source_releases,
            source_archives=(
                replace(base.source_archives[0], source_release="wrong-release"),
            ),
            interrupted_execution=base.interrupted_execution,
        )

    lahman_release = SourceReleaseBinding(
        source_key="SABR_LAHMAN_BOX_DOWNLOADS",
        source_version_id=LAHMAN_VERSION,
        dataset_version="2025",
        identity_checksum="1" * 64,
        source_mode="LIVE",
    )
    lahman_archive = SourceArchiveBinding(
        source_key="SABR_LAHMAN_BOX_DOWNLOADS",
        object_key="lahman",
        source_release="2025",
        raw_logical_uri="raw/open-data/lahman.zip",
        archive_sha256="2" * 64,
        payload_sha256="3" * 64,
        content_length=1,
        download_identity_checksum="4" * 64,
    )
    with pytest.raises(FD1RecoveryIntegrityError, match="sorted and unique"):
        GenerationLineage(
            code_revision=base.code_revision,
            trusted_dry_run_ledger=base.trusted_dry_run_ledger,
            source_releases=(lahman_release, *base.source_releases),
            source_archives=(lahman_archive, *base.source_archives),
            interrupted_execution=base.interrupted_execution,
        )

    with pytest.raises(ValueError, match="relative logical URI"):
        IdentitySpoolBinding(
            logical_uri=".runtime//gate3c-fd1/recovery/identity-spool.csv",
            schema_version="v1",
            sha256="f" * 64,
            byte_count=1,
            row_count=1,
        )


def test_symlinked_manifest_file_is_rejected_when_supported(artifact_root: Path) -> None:
    store = artifact_root / "store"
    store.mkdir()
    uri = _partition(store, 4)
    original = store / Path(uri)
    target = artifact_root / "outside.parquet"
    original.replace(target)
    try:
        original.symlink_to(target)
    except OSError:
        pytest.skip("This Windows environment does not grant symlink creation")
    with pytest.raises(FD1RecoveryIntegrityError, match="links or reparse"):
        _build(store, [uri])


def test_content_addressed_loader_rejects_noncanonical_or_tampered_json(
    artifact_root: Path,
) -> None:
    bad = artifact_root / "bad-cache.json"
    bad.write_text(json.dumps({"schema_version": "wrong"}), encoding="utf-8")
    with pytest.raises(FD1RecoveryIntegrityError, match="content-addressed"):
        load_recovery_hash_cache(bad)

    noncanonical = b'{ "schema_version" : "wrong" }\n'
    noncanonical_digest = hashlib.sha256(noncanonical).hexdigest()
    noncanonical_path = artifact_root / (
        f"fd1-recovery-hash-cache-sha256-{noncanonical_digest}.json"
    )
    noncanonical_path.write_bytes(noncanonical)
    with pytest.raises(FD1RecoveryIntegrityError, match="not canonical JSON"):
        load_recovery_hash_cache(noncanonical_path)

    duplicate = b'{"schema_version":"one","schema_version":"two"}\n'
    duplicate_digest = hashlib.sha256(duplicate).hexdigest()
    duplicate_path = artifact_root / (
        f"fd1-recovery-hash-cache-sha256-{duplicate_digest}.json"
    )
    duplicate_path.write_bytes(duplicate)
    with pytest.raises(FD1RecoveryIntegrityError, match="duplicate key"):
        load_recovery_hash_cache(duplicate_path)

"""Provider-free, resumable publication of Gate 3C-FD1 warehouse artifacts.

The materializer and identity importer deliberately stop before durable writes.
This module is their narrow publication boundary.  It accepts only their frozen
batch contracts, proves every file contributor against the live open-data
registry (and, for archive members, an exact extraction artifact), obtains a
manifest-specific local-processing grant, and then publishes immutable Zstd
Parquet plus append-only ORM metadata.

No transport, provider adapter, model fitting, promotion, odds, or position code
is imported or constructed here.  The caller owns the SQL transaction.  A
failed transaction can leave only content-addressed Parquet bytes; a retry uses
the deterministic checkpoint UUID and safely reuses those validated bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import and_, or_, select, tuple_
from sqlalchemy.orm import Session

from app.gen2.fd1_identity import (
    FD1_IDENTITY_SCHEMA_VERSION,
    LAHMAN_SOURCE_KEY,
    PLAYER_PRIOR_TIMING_CLASSIFICATION,
    PlayerPriorPartitionBatch,
    RegistryIdentityMappingBatch,
)
from app.gen2.fd1_materializer import (
    RETROSHEET_SOURCE_KEY,
    FamilyStagingPayload,
    verify_payload_checksum,
)
from app.gen2.fd1_registry_sync import FD1_SOURCE_DEFINITIONS
from app.gen2.open_data_acquisition import (
    EXTRACTION_MANIFEST_SCHEMA_VERSION,
    ArchiveFormat,
    ExtractedMember,
    ExtractionArtifact,
)
from app.gen2.open_data_registry import (
    OpenDataRegistry,
    build_identity_mapping_identity,
    build_owner_decision_identity,
    build_provider_decision_identity,
    build_source_release_identity,
    canonical_sha256,
)
from app.gen2.parquet_store import (
    DEFAULT_MAXIMUM_PARTITION_BYTES,
    DEFAULT_MAXIMUM_STORE_BYTES,
    DEFAULT_MINIMUM_FREE_BYTES_AFTER_WRITE,
    HistoricalEventParquetStore,
    HistoricalEventPartitionArtifact,
)
from app.gen2.source_authorization import (
    ExistingEvidenceDerivedUseDecision as LocalDerivedUseDecision,
    OwnerApproval as LocalOwnerApproval,
    require_local_processing,
)
from app.historical.checkpoints import (
    HistoricalCheckpoint,
    SqlAlchemyCheckpointRepository,
)
from app.models import (
    ExistingEvidenceDerivedUseDecision,
    HistoricalEventPartition,
    HistoricalSourceVersion,
    HistoryBackfillCheckpoint,
    IngestionRun,
    OpenDataDownloadManifest,
    OpenDataIdentityMapping,
    ProviderAccessAuthorization,
    RunStatus,
    SourceOwnerApproval,
)


FD1_WAREHOUSE_SCHEMA_VERSION = "gate3c_fd1_warehouse_v1"
FD1_PUBLICATION_ENVELOPE_SCHEMA_VERSION = "gate3c_fd1_publication_envelope_v1"
FD1_INGESTION_SOURCE = "GATE3C_FD1_OPEN_DATA"
FD1_INGESTION_JOB = "gate3c-fd1-warehouse-publication"
RETROSHEET_CSV_OBJECT_KEY = "retrosheet-csv-through-2025"
DEFAULT_MAXIMUM_ENVELOPE_BYTES = 2 * 1024**3
MAXIMUM_IDENTITY_MAPPING_BATCH_SIZE = 10_000
IDENTITY_MAPPING_QUERY_CHUNK_SIZE = 500
FD1_CODE_REVISION_INPUTS = (
    "alembic/versions/20260814_0013_gate3c_fd1_partition_families.py",
    "alembic/versions/20260814_0014_fd1_identity_mapping_natural_key.py",
    "app/gen2/context.py",
    "app/gen2/event_taxonomy.py",
    "app/gen2/fd1_identity.py",
    "app/gen2/fd1_materializer.py",
    "app/gen2/fd1_park_factors.py",
    "app/gen2/fd1_reconciliation.py",
    "app/gen2/fd1_registry_sync.py",
    "app/gen2/fd1_warehouse.py",
    "app/gen2/open_data_acquisition.py",
    "app/gen2/open_data_registry.py",
    "app/gen2/parquet_store.py",
    "app/gen2/retrosheet.py",
    "app/gen2/source_authorization.py",
    "app/historical/checkpoints.py",
    "app/models/entities.py",
)
DEFAULT_ALLOWED_STORE_ROOTS = (
    Path(__file__).resolve().parents[3] / "data" / "curated",
    Path(__file__).resolve().parents[3] / "data" / "features",
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CODE_REVISION = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_PUBLICATION_NAMESPACE = UUID("418ebff6-c8f3-4a91-970d-7ec71c39c944")
_REPARSE_POINT = 0x400
_TIMING_BY_RETROSHEET_FAMILY: Mapping[str, str] = MappingProxyType(
    {
        "plate_appearance": "RETROSPECTIVE_EVENT_TIMING_RISK",
        "play_transition": "RETROSPECTIVE_EVENT_TIMING_RISK",
        "pitch_sequence": "RETROSPECTIVE_EVENT_TIMING_RISK",
        "player_game_batting": "HISTORICAL_ACTUAL_LINEUP_TIMING_RISK",
        "player_game_pitching": "RETROSPECTIVE_RECONSTRUCTION",
        "player_game_fielding": "RETROSPECTIVE_RECONSTRUCTION",
        "starter_usage": "HISTORICAL_ACTUAL_STARTER_TIMING_RISK",
        "bullpen_usage": "HISTORICAL_ACTUAL_RELIEVER_TIMING_RISK",
        "weather": "RETROSPECTIVE_GAME_WEATHER_TIMING_RISK",
    }
)


class FD1WarehouseError(RuntimeError):
    """The publication request failed a registry, lineage, or identity proof."""


@dataclass(frozen=True, slots=True)
class ContributorDownloadBinding:
    """Bind one materializer file checksum to one registered physical download.

    Direct downloads need only the first two fields.  If the contributor is an
    extracted archive member, both extraction fields are mandatory and the
    supplied :class:`ExtractionArtifact` is revalidated in full.
    """

    file_sha256: str
    download_identity_checksum: str
    extraction_identity_checksum: str | None = None
    member_relative_path: str | None = None

    def __post_init__(self) -> None:
        _require_sha256(self.file_sha256, "binding file_sha256")
        _require_sha256(
            self.download_identity_checksum, "binding download identity"
        )
        if (self.extraction_identity_checksum is None) != (
            self.member_relative_path is None
        ):
            raise FD1WarehouseError(
                "Archive-member bindings require both extraction identity and member path"
            )
        if self.extraction_identity_checksum is not None:
            _require_sha256(
                self.extraction_identity_checksum, "binding extraction identity"
            )
            _logical_uri(self.member_relative_path or "", "member_relative_path")


@dataclass(frozen=True, slots=True)
class FD1WarehouseSummary:
    mode: str
    code_revision: str
    batches_seen: int
    parquet_rows: int
    ingestion_run_rows_created: int
    ingestion_run_rows_reused: int
    partition_rows_created: int
    partition_rows_reused: int
    checkpoint_rows_created: int
    checkpoint_rows_reused: int
    identity_mappings_seen: int
    identity_mapping_rows_created: int
    identity_mapping_rows_reused: int
    derived_use_rows_created: int
    derived_use_rows_reused: int
    bytes_published: int
    provider_transport_constructed: bool = False
    network_requests: int = 0
    transaction_committed_by_function: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class _Counters:
    batches_seen: int = 0
    parquet_rows: int = 0
    ingestion_run_rows_created: int = 0
    ingestion_run_rows_reused: int = 0
    partition_rows_created: int = 0
    partition_rows_reused: int = 0
    checkpoint_rows_created: int = 0
    checkpoint_rows_reused: int = 0
    identity_mappings_seen: int = 0
    identity_mapping_rows_created: int = 0
    identity_mapping_rows_reused: int = 0
    derived_use_rows_created: int = 0
    derived_use_rows_reused: int = 0
    bytes_published: int = 0


@dataclass(frozen=True, slots=True)
class _RegistryContext:
    source: HistoricalSourceVersion
    owner: SourceOwnerApproval
    provider: ProviderAccessAuthorization
    downloads_by_identity: Mapping[str, OpenDataDownloadManifest]
    downloads_by_payload: Mapping[str, tuple[OpenDataDownloadManifest, ...]]


@dataclass(frozen=True, slots=True)
class _ResolvedContributor:
    manifest: Mapping[str, Any]
    download: OpenDataDownloadManifest


def build_fd1_code_revision() -> str:
    """Hash every local semantic input to FD1 publication and closeout evidence."""

    root = Path(__file__).resolve().parents[2]
    digest = hashlib.sha256()
    for name in sorted(FD1_CODE_REVISION_INPUTS):
        path = root / name
        payload = path.read_bytes()
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def load_extraction_artifact_manifest(
    manifest_path: Path,
    *,
    raw_root: Path,
    verify_member_bytes: bool = True,
) -> ExtractionArtifact:
    """Load and revalidate one ignored extraction manifest without network I/O."""

    root = Path(raw_root).resolve(strict=True)
    path = Path(manifest_path).resolve(strict=True)
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError as exc:
        raise FD1WarehouseError("Extraction manifest is outside the raw root") from exc
    if not path.is_file() or _linklike(path):
        raise FD1WarehouseError("Extraction manifest is not a safe regular file")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FD1WarehouseError("Extraction manifest JSON is invalid") from exc
    if not isinstance(payload, Mapping) or set(payload) != {
        "schema_version",
        "source_key",
        "object_key",
        "source_revision",
        "archive_sha256",
        "archive_format",
        "extraction_logical_uri",
        "manifest_logical_uri",
        "extracted_at",
        "member_count",
        "expanded_bytes",
        "members",
        "identity_checksum",
        "reused",
    }:
        raise FD1WarehouseError("Extraction manifest fields are not exact")
    artifact = _extraction_from_json(payload)
    _validate_extraction_identity(artifact)
    if artifact.manifest_logical_uri != relative:
        raise FD1WarehouseError(
            "Extraction manifest path differs from its logical identity"
        )
    extraction_root = root.joinpath(
        *PurePosixPath(artifact.extraction_logical_uri).parts
    ).resolve(strict=True)
    if not extraction_root.is_dir() or _linklike(extraction_root):
        raise FD1WarehouseError("Extraction root is missing or unsafe")
    if path.parent != extraction_root:
        raise FD1WarehouseError("Extraction manifest is outside its extraction root")
    if verify_member_bytes:
        expected = {PurePosixPath(artifact.manifest_logical_uri).name.casefold()}
        for member in artifact.members:
            member_path = extraction_root.joinpath(
                *PurePosixPath(member.relative_path).parts
            ).resolve(strict=True)
            try:
                relative_member = member_path.relative_to(extraction_root).as_posix()
            except ValueError as exc:
                raise FD1WarehouseError("Extraction member escapes its root") from exc
            if not member_path.is_file() or _linklike(member_path):
                raise FD1WarehouseError("Extraction member is missing or unsafe")
            if member_path.stat().st_size != member.size_bytes:
                raise FD1WarehouseError("Extraction member byte count changed")
            if _file_sha256(member_path) != member.sha256:
                raise FD1WarehouseError("Extraction member checksum changed")
            expected.add(relative_member.casefold())
        actual = {
            item.relative_to(extraction_root).as_posix().casefold()
            for item in extraction_root.rglob("*")
            if item.is_file()
        }
        if actual != expected:
            raise FD1WarehouseError("Extraction file population changed")
    return artifact


def build_archive_member_bindings(
    extraction: ExtractionArtifact,
    *,
    download_identity_checksum: str,
    required_file_checksums: Mapping[str, str],
) -> tuple[ContributorDownloadBinding, ...]:
    """Build exact bindings for a materializer's filename-to-checksum map."""

    _validate_extraction_identity(extraction)
    _require_sha256(download_identity_checksum, "download identity")
    result: list[ContributorDownloadBinding] = []
    for filename, checksum in sorted(required_file_checksums.items()):
        _logical_uri(filename, "required archive filename")
        _require_sha256(checksum, "required archive member checksum")
        matches = [
            member
            for member in extraction.members
            if member.relative_path == filename
            or PurePosixPath(member.relative_path).name == filename
        ]
        if len(matches) != 1 or matches[0].sha256 != checksum:
            raise FD1WarehouseError(
                "Required materializer file does not match one extraction member"
            )
        result.append(
            ContributorDownloadBinding(
                file_sha256=checksum,
                download_identity_checksum=download_identity_checksum,
                extraction_identity_checksum=extraction.identity_checksum,
                member_relative_path=matches[0].relative_path,
            )
        )
    return tuple(result)


def publish_fd1_warehouse(
    session: Session,
    store: HistoricalEventParquetStore,
    *,
    retrosheet_batches: Iterable[FamilyStagingPayload] = (),
    player_prior_batches: Iterable[PlayerPriorPartitionBatch] = (),
    identity_mapping_batches: Iterable[RegistryIdentityMappingBatch] = (),
    contributor_bindings: Sequence[ContributorDownloadBinding] = (),
    extraction_artifacts: Sequence[ExtractionArtifact] = (),
    code_revision: str | None = None,
    dry_run: bool = False,
    execution_at: datetime | None = None,
    allowed_store_roots: Sequence[Path] = DEFAULT_ALLOWED_STORE_ROOTS,
) -> FD1WarehouseSummary:
    """Validate or publish an FD1 warehouse stream without committing SQL.

    Iterables are consumed once and may therefore be backed directly by the
    single-pass Retrosheet monthly dispatcher.  In execute mode, every
    partition/checkpoint is flushed in the caller's transaction.  The caller
    must commit on success or roll back on any exception.  Every archive
    extraction supplied for execute must first come from
    :func:`load_extraction_artifact_manifest` with member-byte verification;
    JSON-envelope archive execution is deliberately disabled.
    """

    revision = code_revision or build_fd1_code_revision()
    if not _CODE_REVISION.fullmatch(revision):
        raise FD1WarehouseError(
            "code_revision must be a 40-character Git SHA-1 or 64-character content SHA-256"
        )
    now = _aware(execution_at or datetime.now(UTC), "execution_at")
    _validate_store_root(store.root, allowed_store_roots)
    _validate_store_configuration(store)
    bindings = _binding_index(contributor_bindings)
    extractions = _extraction_index(extraction_artifacts)
    contexts: dict[str, _RegistryContext] = {}

    def context(source_key: str) -> _RegistryContext:
        if source_key not in FD1_SOURCE_DEFINITIONS:
            raise FD1WarehouseError(
                "Identity mapping references a source outside frozen FD1 governance"
            )
        if source_key not in contexts:
            contexts[source_key] = _load_registry_context(session, source_key)
        return contexts[source_key]
    counters = _Counters()
    observed_work = False

    for batch in retrosheet_batches:
        observed_work = True
        retrosheet = context(RETROSHEET_SOURCE_KEY)
        _validate_retrosheet_batch(batch, retrosheet.source)
        normalized = _resolve_contributors(
            batch.raw_contributor_manifest,
            context=retrosheet,
            bindings=bindings,
            extractions=extractions,
        )
        _validate_local_grant(
            session,
            context=retrosheet,
            contributors=normalized,
            execution_at=now,
            persist=not dry_run,
            counters=counters,
        )
        counters.batches_seen += 1
        counters.parquet_rows += batch.row_count
        if dry_run:
            continue
        run, run_created = _ensure_ingestion_run(
            session,
            code_revision=revision,
            execution_at=now,
            unit_kind=f"partition:{batch.family}",
            unit_identity_checksum=batch.partition_checksum,
        )
        _count_run(counters, run_created)
        checkpoint, created = _ensure_checkpoint(
            session,
            ingestion_run=run,
            context=retrosheet,
            family=batch.family,
            season=batch.season,
            month=batch.month,
            batch_number=1,
            batch_checksum=batch.partition_checksum,
            contributors=normalized,
            row_count=batch.row_count,
            min_date=batch.min_event_date,
            max_date=batch.max_event_date,
            code_revision=revision,
            execution_at=now,
            quality_summary={
                "publication_scope": batch.publication_scope,
                "source_bundle_checksum": batch.source_bundle_checksum,
                "leakage_policy": batch.leakage_policy,
                "quality_report": dict(batch.quality_report),
                "provider_requests": 0,
            },
        )
        _count_checkpoint(counters, created)
        grant = _local_grant(
            context=retrosheet,
            contributors=normalized,
            execution_at=now,
            persisted_decision=_derived_use_row(
                session,
                source_version_id=retrosheet.source.id,
                manifest_checksum=_manifest_checksum(normalized),
            ),
        )
        kwargs = batch.parquet_store_metadata()
        kwargs["raw_contributor_manifest"] = normalized
        artifact = store.write_partition(
            **kwargs,
            source_version_id=retrosheet.source.id,
            checkpoint_id=checkpoint.id,
            code_revision=revision,
            evidence_tier="A",
            timing_classification=_TIMING_BY_RETROSHEET_FAMILY[batch.family],
            authorization_grant=grant,
            raw_contributor_checksum=_manifest_checksum(normalized),
        )
        _persist_partition(session, artifact, counters)
        _finalize_ingestion_run(
            session,
            run,
            records_processed=batch.row_count,
            execution_at=now,
            result={
                "partition_checksum": batch.partition_checksum,
                "event_family": batch.family,
                "season": batch.season,
                "month": batch.month,
            },
        )

    for batch in player_prior_batches:
        observed_work = True
        lahman = context(LAHMAN_SOURCE_KEY)
        _validate_player_prior_batch(batch)
        normalized = _resolve_contributors(
            batch.raw_contributor_manifest,
            context=lahman,
            bindings=bindings,
            extractions=extractions,
        )
        _validate_local_grant(
            session,
            context=lahman,
            contributors=normalized,
            execution_at=now,
            persist=not dry_run,
            counters=counters,
        )
        counters.batches_seen += 1
        counters.parquet_rows += batch.row_count
        if dry_run:
            continue
        run, run_created = _ensure_ingestion_run(
            session,
            code_revision=revision,
            execution_at=now,
            unit_kind="partition:player_prior",
            unit_identity_checksum=batch.payload_checksum,
        )
        _count_run(counters, run_created)
        checkpoint, created = _ensure_checkpoint(
            session,
            ingestion_run=run,
            context=lahman,
            family=batch.event_family,
            season=batch.season,
            month=batch.month,
            batch_number=batch.batch_number,
            batch_checksum=batch.payload_checksum,
            contributors=normalized,
            row_count=batch.row_count,
            min_date=batch.min_official_date,
            max_date=batch.max_official_date,
            code_revision=revision,
            execution_at=now,
            quality_summary={
                "population": batch.population,
                "annual_period_end_partition": True,
                "same_season_predictor_eligible": False,
                "provider_requests": 0,
            },
        )
        _count_checkpoint(counters, created)
        grant = _local_grant(
            context=lahman,
            contributors=normalized,
            execution_at=now,
            persisted_decision=_derived_use_row(
                session,
                source_version_id=lahman.source.id,
                manifest_checksum=_manifest_checksum(normalized),
            ),
        )
        kwargs = batch.parquet_write_kwargs()
        kwargs["raw_contributor_manifest"] = normalized
        kwargs["raw_contributor_checksum"] = _manifest_checksum(normalized)
        artifact = store.write_partition(
            **kwargs,
            source_key=LAHMAN_SOURCE_KEY,
            source_version_id=lahman.source.id,
            checkpoint_id=checkpoint.id,
            schema_version=FD1_IDENTITY_SCHEMA_VERSION,
            code_revision=revision,
            evidence_tier="A",
            timing_classification=PLAYER_PRIOR_TIMING_CLASSIFICATION,
            authorization_grant=grant,
        )
        _persist_partition(session, artifact, counters)
        _finalize_ingestion_run(
            session,
            run,
            records_processed=batch.row_count,
            execution_at=now,
            result={
                "payload_checksum": batch.payload_checksum,
                "event_family": batch.event_family,
                "season": batch.season,
                "month": batch.month,
                "batch_number": batch.batch_number,
            },
        )

    for batch in identity_mapping_batches:
        observed_work = True
        mapping_contexts = {
            source_key: context(source_key)
            for source_key in sorted({spec.source_key for spec in batch.specs})
        }
        _validate_identity_batch(batch, mapping_contexts)
        for source_key, selected_specs in _specs_by_source(batch).items():
            mapping_contributors = _identity_contributors(
                selected_specs,
                context=mapping_contexts[source_key],
                extractions=extractions,
            )
            _validate_local_grant(
                session,
                context=mapping_contexts[source_key],
                contributors=mapping_contributors,
                execution_at=now,
                persist=not dry_run,
                counters=counters,
            )
        counters.identity_mappings_seen += len(batch.specs)
        if dry_run:
            _record_identity_mappings_set_based(
                session,
                mapping_contexts,
                batch,
                persist=False,
            )
            continue
        run, run_created = _ensure_ingestion_run(
            session,
            code_revision=revision,
            execution_at=now,
            unit_kind="identity_mapping",
            unit_identity_checksum=batch.batch_checksum,
        )
        _count_run(counters, run_created)
        created, reused = _record_identity_mappings_set_based(
            session,
            mapping_contexts,
            batch,
            persist=True,
        )
        counters.identity_mapping_rows_created += created
        counters.identity_mapping_rows_reused += reused
        _finalize_ingestion_run(
            session,
            run,
            records_processed=len(batch.specs),
            execution_at=now,
            result={
                "identity_mapping_batch_checksum": batch.batch_checksum,
                "batch_number": batch.batch_number,
                "source_keys": sorted(mapping_contexts),
            },
        )

    if not observed_work:
        raise FD1WarehouseError("Publication contains no partition or identity work")
    return FD1WarehouseSummary(
        mode="dry-run" if dry_run else "execute",
        code_revision=revision,
        **asdict(counters),
    )


def _load_registry_context(session: Session, source_key: str) -> _RegistryContext:
    definition = FD1_SOURCE_DEFINITIONS[source_key]
    source_identity = build_source_release_identity(definition.release_spec())
    sources = list(
        session.scalars(
            select(HistoricalSourceVersion).where(
                HistoricalSourceVersion.source_key == source_key,
                HistoricalSourceVersion.dataset_version == definition.dataset_version,
                HistoricalSourceVersion.source_mode == "LIVE",
            )
        )
    )
    if len(sources) != 1 or sources[0].identity_checksum != source_identity:
        raise FD1WarehouseError(
            f"{source_key} does not have exactly the frozen live source release"
        )
    source = sources[0]
    owner_identity = build_owner_decision_identity(definition.owner_spec())
    owners = list(
        session.scalars(
            select(SourceOwnerApproval).where(
                SourceOwnerApproval.source_key == source_key,
                SourceOwnerApproval.identity_checksum == owner_identity,
            )
        )
    )
    if len(owners) != 1 or not owners[0].approved:
        raise FD1WarehouseError(f"{source_key} lacks its exact approved owner decision")
    owner = owners[0]
    provider_identity = build_provider_decision_identity(
        owner.identity_checksum, definition.provider_spec()
    )
    providers = list(
        session.scalars(
            select(ProviderAccessAuthorization).where(
                ProviderAccessAuthorization.owner_approval_id == owner.id,
                ProviderAccessAuthorization.identity_checksum == provider_identity,
            )
        )
    )
    if len(providers) != 1 or not providers[0].authorized:
        raise FD1WarehouseError(
            f"{source_key} lacks its exact authorized official-download decision"
        )
    downloads = tuple(
        session.scalars(
            select(OpenDataDownloadManifest).where(
                OpenDataDownloadManifest.source_version_id == source.id,
                OpenDataDownloadManifest.source_key == source_key,
            )
        )
    )
    if not downloads:
        raise FD1WarehouseError(f"{source_key} has no registered download manifests")
    by_identity = {item.identity_checksum: item for item in downloads}
    if len(by_identity) != len(downloads):
        raise FD1WarehouseError("Download-manifest identities are not unique")
    by_payload: dict[str, list[OpenDataDownloadManifest]] = {}
    for item in downloads:
        by_payload.setdefault(item.payload_sha256, []).append(item)
    return _RegistryContext(
        source=source,
        owner=owner,
        provider=providers[0],
        downloads_by_identity=MappingProxyType(by_identity),
        downloads_by_payload=MappingProxyType(
            {key: tuple(value) for key, value in by_payload.items()}
        ),
    )


def _binding_index(
    bindings: Sequence[ContributorDownloadBinding],
) -> Mapping[str, ContributorDownloadBinding]:
    result: dict[str, ContributorDownloadBinding] = {}
    for binding in bindings:
        previous = result.setdefault(binding.file_sha256, binding)
        if previous != binding:
            raise FD1WarehouseError(
                "One contributor checksum has conflicting download bindings"
            )
    return MappingProxyType(result)


def _validate_store_root(root: Path, allowed_roots: Sequence[Path]) -> None:
    selected = Path(root).resolve()
    if not allowed_roots:
        raise FD1WarehouseError("At least one governed Parquet root is required")
    for allowed in allowed_roots:
        parent = Path(allowed).resolve()
        if selected == parent or parent in selected.parents:
            return
    raise FD1WarehouseError(
        "FD1 Parquet root must remain under a governed curated or feature-data root"
    )


def _validate_store_configuration(store: HistoricalEventParquetStore) -> None:
    """Refuse a live-capable store whose configured limits weaken FD1 policy."""

    if (
        store.minimum_free_bytes_after_write
        < DEFAULT_MINIMUM_FREE_BYTES_AFTER_WRITE
    ):
        raise FD1WarehouseError(
            "FD1 publication requires at least the 100-GiB free-space reserve"
        )
    if store.maximum_partition_bytes > DEFAULT_MAXIMUM_PARTITION_BYTES:
        raise FD1WarehouseError(
            "FD1 publication cannot raise the governed partition byte cap"
        )
    if store.maximum_store_bytes > DEFAULT_MAXIMUM_STORE_BYTES:
        raise FD1WarehouseError(
            "FD1 publication cannot raise the governed Parquet-store byte cap"
        )


def _extraction_index(
    artifacts: Sequence[ExtractionArtifact],
) -> Mapping[str, ExtractionArtifact]:
    result: dict[str, ExtractionArtifact] = {}
    for artifact in artifacts:
        _validate_extraction_identity(artifact)
        previous = result.setdefault(artifact.identity_checksum, artifact)
        if previous != artifact:
            raise FD1WarehouseError("Extraction identity collision")
    return MappingProxyType(result)


def _resolve_contributors(
    contributors: Sequence[Mapping[str, Any]],
    *,
    context: _RegistryContext,
    bindings: Mapping[str, ContributorDownloadBinding],
    extractions: Mapping[str, ExtractionArtifact],
) -> tuple[Mapping[str, Any], ...]:
    if not contributors:
        raise FD1WarehouseError("A publication batch has no raw contributors")
    resolved: list[_ResolvedContributor] = []
    for raw in contributors:
        file_sha = str(raw.get("file_sha256", ""))
        _require_sha256(file_sha, "contributor file_sha256")
        binding = bindings.get(file_sha)
        direct = context.downloads_by_payload.get(file_sha, ())
        if binding is None:
            if len(direct) != 1:
                raise FD1WarehouseError(
                    "Contributor is not an unambiguous direct download and lacks "
                    "an explicit binding"
                )
            download = direct[0]
            extraction = None
        else:
            download = context.downloads_by_identity.get(
                binding.download_identity_checksum
            )
            if download is None:
                raise FD1WarehouseError(
                    "Contributor binding references a download outside its exact source release"
                )
            extraction = None
            if binding.extraction_identity_checksum is not None:
                extraction = extractions.get(binding.extraction_identity_checksum)
                if extraction is None:
                    raise FD1WarehouseError(
                        "Contributor binding references an absent extraction artifact"
                    )
                _validate_extraction_against_download(extraction, download)
                matches = [
                    item
                    for item in extraction.members
                    if item.relative_path == binding.member_relative_path
                ]
                if len(matches) != 1 or matches[0].sha256 != file_sha:
                    raise FD1WarehouseError(
                        "Archive-member binding does not identify the contributor bytes"
                    )
            elif download.payload_sha256 != file_sha:
                raise FD1WarehouseError(
                    "Non-direct contributor binding requires exact extraction lineage"
                )
        if context.source.source_key == RETROSHEET_SOURCE_KEY and (
            download.object_key != RETROSHEET_CSV_OBJECT_KEY
        ):
            raise FD1WarehouseError(
                "Retrosheet analytical contributors must descend from the Daily Logs CSV archive"
            )
        if context.source.source_key == LAHMAN_SOURCE_KEY and not (
            download.object_key.startswith("lahman-2025-")
        ):
            raise FD1WarehouseError("Player-prior contributor is not a Lahman 2025 object")
        normalized = _normalized_contributor(raw, download, binding, extraction)
        resolved.append(_ResolvedContributor(normalized, download))
    # Identical normalized evidence is collapsed; distinct evidence order is canonical.
    unique = {
        canonical_sha256(item.manifest): item.manifest for item in resolved
    }
    return tuple(
        MappingProxyType(dict(unique[key])) for key in sorted(unique)
    )


def _normalized_contributor(
    raw: Mapping[str, Any],
    download: OpenDataDownloadManifest,
    binding: ContributorDownloadBinding | None,
    extraction: ExtractionArtifact | None,
) -> dict[str, Any]:
    allowed = {
        "logical_uri",
        "file_sha256",
        "payload_checksum",
        "record_count",
        "source_record_id",
    }
    if set(raw) - allowed:
        raise FD1WarehouseError("Input contributor contains fields outside its staging contract")
    file_sha = str(raw["file_sha256"])
    record_count = raw.get("record_count")
    if record_count is not None and (
        isinstance(record_count, bool)
        or not isinstance(record_count, int)
        or record_count < 0
    ):
        raise FD1WarehouseError("Contributor record_count is invalid")
    raw_uri = str(raw.get("logical_uri", ""))
    _logical_uri(raw_uri, "contributor logical_uri")
    if extraction is None:
        if raw_uri != download.raw_logical_uri:
            raise FD1WarehouseError(
                "Direct contributor logical URI does not match its registered download"
            )
        logical_uri = download.raw_logical_uri
        source_record_id = f"open-data-download:{download.identity_checksum}"
    else:
        assert binding is not None and binding.member_relative_path is not None
        expected_name = PurePosixPath(binding.member_relative_path).name.casefold()
        staged_name = str(raw.get("source_record_id", PurePosixPath(raw_uri).name)).casefold()
        if staged_name != expected_name and PurePosixPath(raw_uri).name.casefold() != expected_name:
            raise FD1WarehouseError(
                "Staging contributor name differs from its exact extraction member"
            )
        logical_uri = (
            PurePosixPath(extraction.extraction_logical_uri)
            / PurePosixPath(binding.member_relative_path)
        ).as_posix()
        source_record_id = (
            f"open-data-download:{download.identity_checksum};"
            f"extraction:{extraction.identity_checksum};"
            f"member:{binding.member_relative_path}"
        )
    result: dict[str, Any] = {
        "logical_uri": logical_uri,
        "file_sha256": file_sha,
        "payload_checksum": file_sha,
        "archive_checksum": download.archive_sha256 or download.payload_sha256,
        "retrieved_at": _stored_utc(
            download.downloaded_at, "download.downloaded_at"
        ).isoformat(),
        "source_record_id": source_record_id,
        "source_version_id": str(download.source_version_id),
    }
    if record_count is not None:
        result["record_count"] = record_count
    return result


def _validate_extraction_identity(artifact: ExtractionArtifact) -> None:
    if artifact.schema_version != EXTRACTION_MANIFEST_SCHEMA_VERSION:
        raise FD1WarehouseError("Extraction artifact schema version is invalid")
    if artifact.member_count != len(artifact.members) or artifact.expanded_bytes != sum(
        item.size_bytes for item in artifact.members
    ):
        raise FD1WarehouseError("Extraction artifact counts do not reconcile")
    semantic = {
        "schema_version": artifact.schema_version,
        "source_key": artifact.source_key,
        "object_key": artifact.object_key,
        "source_revision": artifact.source_revision,
        "archive_sha256": artifact.archive_sha256,
        "archive_format": artifact.archive_format.value,
        "extraction_logical_uri": artifact.extraction_logical_uri,
        "manifest_logical_uri": artifact.manifest_logical_uri,
        "extracted_at": artifact.extracted_at,
        "member_count": artifact.member_count,
        "expanded_bytes": artifact.expanded_bytes,
        "members": [asdict(item) for item in artifact.members],
    }
    if canonical_sha256(semantic) != artifact.identity_checksum:
        raise FD1WarehouseError("Extraction artifact identity checksum is invalid")
    _logical_uri(artifact.extraction_logical_uri, "extraction logical URI")
    _logical_uri(artifact.manifest_logical_uri, "extraction manifest logical URI")
    seen: set[str] = set()
    for member in artifact.members:
        _logical_uri(member.relative_path, "extraction member")
        if member.relative_path.casefold() in seen:
            raise FD1WarehouseError("Extraction member paths are not unique")
        seen.add(member.relative_path.casefold())
        _require_sha256(member.sha256, "extraction member checksum")
        if member.size_bytes < 0:
            raise FD1WarehouseError("Extraction member byte count is invalid")


def _validate_extraction_against_download(
    extraction: ExtractionArtifact, download: OpenDataDownloadManifest
) -> None:
    if (
        extraction.source_key != download.source_key
        or extraction.object_key != download.object_key
        or extraction.source_revision != download.upstream_revision
        or extraction.archive_sha256 != download.payload_sha256
        or download.archive_sha256 != download.payload_sha256
        or extraction.archive_format is ArchiveFormat.NONE
    ):
        raise FD1WarehouseError(
            "Extraction artifact does not descend from the registered archive download"
        )


def _validate_retrosheet_batch(
    batch: FamilyStagingPayload, source: HistoricalSourceVersion
) -> None:
    if not isinstance(batch, FamilyStagingPayload):
        raise FD1WarehouseError("Retrosheet stream contains a foreign batch type")
    verify_payload_checksum(batch)
    if batch.family not in _TIMING_BY_RETROSHEET_FAMILY:
        raise FD1WarehouseError("Retrosheet batch family is outside the FD1 registry")
    if batch.publication_scope != "COMPLETE_MONTH_PARTITION":
        raise FD1WarehouseError("Pilot or partial-month staging cannot be published")
    for row in batch.rows:
        if row.get("source_key") != RETROSHEET_SOURCE_KEY:
            raise FD1WarehouseError("Retrosheet row source_key is inconsistent")
        if str(row.get("source_version_id")) != str(source.id):
            raise FD1WarehouseError(
                "Retrosheet rows are not bound to the exact live source-version UUID"
            )


def _validate_player_prior_batch(batch: PlayerPriorPartitionBatch) -> None:
    if not isinstance(batch, PlayerPriorPartitionBatch):
        raise FD1WarehouseError("Player-prior stream contains a foreign batch type")
    for row in batch.rows:
        if row.get("source_key") != LAHMAN_SOURCE_KEY:
            raise FD1WarehouseError("Player-prior row source_key is inconsistent")


def _validate_identity_batch(
    batch: RegistryIdentityMappingBatch,
    contexts: Mapping[str, _RegistryContext],
) -> None:
    if not isinstance(batch, RegistryIdentityMappingBatch):
        raise FD1WarehouseError("Identity stream contains a foreign batch type")
    if len(batch.specs) > MAXIMUM_IDENTITY_MAPPING_BATCH_SIZE:
        raise FD1WarehouseError(
            "Identity batch exceeds the governed maximum of "
            f"{MAXIMUM_IDENTITY_MAPPING_BATCH_SIZE} mappings"
        )
    seen_natural: dict[tuple[Any, ...], str] = {}
    for spec in batch.specs:
        source = contexts[spec.source_key].source
        identity = build_identity_mapping_identity(
            source_version_identity_checksum=source.identity_checksum,
            spec=spec,
        )
        natural = (
            source.id,
            spec.entity_type,
            spec.source_namespace,
            spec.external_id,
            spec.source_row_number,
        )
        previous = seen_natural.get(natural)
        if previous is not None:
            if previous != identity:
                raise FD1WarehouseError(
                    "Identity batch contains a conflicting natural key"
                )
            raise FD1WarehouseError("Identity batch contains a duplicate mapping")
        seen_natural[natural] = identity


def _specs_by_source(
    batch: RegistryIdentityMappingBatch,
) -> Mapping[str, tuple[Any, ...]]:
    grouped: dict[str, list[Any]] = {}
    for spec in batch.specs:
        grouped.setdefault(spec.source_key, []).append(spec)
    return MappingProxyType(
        {key: tuple(value) for key, value in sorted(grouped.items())}
    )


def _identity_contributors(
    specs: Sequence[Any],
    *,
    context: _RegistryContext,
    extractions: Mapping[str, ExtractionArtifact],
) -> tuple[Mapping[str, Any], ...]:
    """Resolve every row-bound mapping URI to immutable file-level evidence."""

    counts: dict[str, int] = {}
    for spec in specs:
        if spec.source_key != context.source.source_key:
            raise FD1WarehouseError("Identity mapping source group is inconsistent")
        counts[spec.evidence_logical_uri] = counts.get(spec.evidence_logical_uri, 0) + 1
    resolved: list[Mapping[str, Any]] = []
    for logical_uri, record_count in sorted(counts.items()):
        direct = [
            item
            for item in context.downloads_by_identity.values()
            if item.raw_logical_uri == logical_uri
        ]
        if len(direct) == 1:
            download = direct[0]
            resolved.append(
                MappingProxyType(
                    {
                        "logical_uri": download.raw_logical_uri,
                        "file_sha256": download.payload_sha256,
                        "payload_checksum": download.payload_sha256,
                        "archive_checksum": (
                            download.archive_sha256 or download.payload_sha256
                        ),
                        "record_count": record_count,
                        "retrieved_at": _stored_utc(
                            download.downloaded_at, "download.downloaded_at"
                        ).isoformat(),
                        "source_record_id": (
                            f"open-data-download:{download.identity_checksum}"
                        ),
                        "source_version_id": str(download.source_version_id),
                    }
                )
            )
            continue
        extraction_matches: list[
            tuple[ExtractionArtifact, ExtractedMember, OpenDataDownloadManifest]
        ] = []
        for extraction in extractions.values():
            if extraction.source_key != context.source.source_key:
                continue
            for member in extraction.members:
                candidate = (
                    PurePosixPath(extraction.extraction_logical_uri)
                    / PurePosixPath(member.relative_path)
                ).as_posix()
                if candidate != logical_uri:
                    continue
                parents = [
                    item
                    for item in context.downloads_by_identity.values()
                    if item.object_key == extraction.object_key
                    and item.upstream_revision == extraction.source_revision
                    and item.payload_sha256 == extraction.archive_sha256
                ]
                if len(parents) != 1:
                    raise FD1WarehouseError(
                        "Identity extraction does not have one exact registered parent"
                    )
                _validate_extraction_against_download(extraction, parents[0])
                extraction_matches.append((extraction, member, parents[0]))
        if len(extraction_matches) != 1:
            raise FD1WarehouseError(
                "Identity mapping evidence URI is not one exact registered file"
            )
        extraction, member, download = extraction_matches[0]
        resolved.append(
            MappingProxyType(
                {
                    "logical_uri": logical_uri,
                    "file_sha256": member.sha256,
                    "payload_checksum": member.sha256,
                    "archive_checksum": download.payload_sha256,
                    "record_count": record_count,
                    "byte_count": member.size_bytes,
                    "retrieved_at": _stored_utc(
                        download.downloaded_at, "download.downloaded_at"
                    ).isoformat(),
                    "source_record_id": (
                        f"open-data-download:{download.identity_checksum};"
                        f"extraction:{extraction.identity_checksum};"
                        f"member:{member.relative_path}"
                    ),
                    "source_version_id": str(download.source_version_id),
                }
            )
        )
    return tuple(resolved)


def _record_identity_mappings_set_based(
    session: Session,
    contexts: Mapping[str, _RegistryContext],
    batch: RegistryIdentityMappingBatch,
    *,
    persist: bool,
) -> tuple[int, int]:
    """Validate/reuse/append one bounded identity batch with set-based queries.

    Every chunk performs one collision query covering both immutable identity and
    declared natural keys.  New rows are flushed once per chunk, preserving the
    append-only ORM guards without issuing a SELECT/flush cycle per mapping.
    """

    created = 0
    reused = 0
    for source_key, specs in _specs_by_source(batch).items():
        context = contexts[source_key]
        OpenDataRegistry(session).require_identity_mapping_source(context.source)
        candidates = tuple(
            _identity_mapping_candidate(context, spec) for spec in specs
        )
        for offset in range(0, len(candidates), IDENTITY_MAPPING_QUERY_CHUNK_SIZE):
            chunk = candidates[
                offset : offset + IDENTITY_MAPPING_QUERY_CHUNK_SIZE
            ]
            identities = tuple(item[0] for item in chunk)
            natural_keys = tuple(item[1] for item in chunk)
            rows = tuple(
                session.scalars(
                    select(OpenDataIdentityMapping).where(
                        or_(
                            OpenDataIdentityMapping.identity_checksum.in_(
                                identities
                            ),
                            and_(
                                OpenDataIdentityMapping.source_version_id
                                == context.source.id,
                                tuple_(
                                    OpenDataIdentityMapping.entity_type,
                                    OpenDataIdentityMapping.source_namespace,
                                    OpenDataIdentityMapping.external_id,
                                    OpenDataIdentityMapping.source_row_number,
                                ).in_(natural_keys),
                            ),
                        )
                    )
                )
            )
            by_identity: dict[str, list[OpenDataIdentityMapping]] = {}
            by_natural: dict[
                tuple[str, str, str, int], list[OpenDataIdentityMapping]
            ] = {}
            for row in rows:
                by_identity.setdefault(row.identity_checksum, []).append(row)
                natural = (
                    row.entity_type,
                    row.source_namespace,
                    row.external_id,
                    row.source_row_number,
                )
                by_natural.setdefault(natural, []).append(row)

            pending: list[OpenDataIdentityMapping] = []
            for identity, natural, values in chunk:
                identity_rows = by_identity.get(identity, [])
                natural_rows = by_natural.get(natural, [])
                if len(identity_rows) > 1 or len(natural_rows) > 1:
                    raise FD1WarehouseError(
                        "Persisted identity mappings violate immutable uniqueness"
                    )
                identity_row = identity_rows[0] if identity_rows else None
                natural_row = natural_rows[0] if natural_rows else None
                if natural_row is not None:
                    _assert_orm_fields(
                        natural_row,
                        values,
                        "identity mapping",
                    )
                    if identity_row is not None and identity_row.id != natural_row.id:
                        raise FD1WarehouseError(
                            "Identity checksum is bound to another natural key"
                        )
                    reused += 1
                    continue
                if identity_row is not None:
                    raise FD1WarehouseError(
                        "Identity checksum is bound to another natural key"
                    )
                if persist:
                    pending.append(OpenDataIdentityMapping(**values))
                created += 1
            if persist and pending:
                session.add_all(pending)
                session.flush()
    return created, reused


def _identity_mapping_candidate(
    context: _RegistryContext, spec: Any
) -> tuple[str, tuple[str, str, str, int], dict[str, Any]]:
    identity = build_identity_mapping_identity(
        source_version_identity_checksum=context.source.identity_checksum,
        spec=spec,
    )
    natural = (
        spec.entity_type,
        spec.source_namespace,
        spec.external_id,
        spec.source_row_number,
    )
    values = {
        "source_version_id": context.source.id,
        "entity_type": spec.entity_type,
        "source_namespace": spec.source_namespace,
        "external_id": spec.external_id,
        "canonical_namespace": spec.canonical_namespace,
        "canonical_id": spec.canonical_id,
        "source_row_number": spec.source_row_number,
        "source_row_checksum": spec.source_row_checksum,
        "confidence": spec.confidence,
        "valid_from": spec.valid_from,
        "valid_through": spec.valid_through,
        "evidence_logical_uri": spec.evidence_logical_uri,
        "identity_checksum": identity,
    }
    return identity, natural, values


def _validate_local_grant(
    session: Session,
    *,
    context: _RegistryContext,
    contributors: Sequence[Mapping[str, Any]],
    execution_at: datetime,
    persist: bool,
    counters: _Counters,
) -> None:
    if persist:
        row, created = _ensure_derived_use_decision(
            session,
            context=context,
            contributors=contributors,
        )
        if created:
            counters.derived_use_rows_created += 1
        else:
            counters.derived_use_rows_reused += 1
    else:
        row = None
    _local_grant(
        context=context,
        contributors=contributors,
        execution_at=execution_at,
        persisted_decision=row,
    )


def _ensure_derived_use_decision(
    session: Session,
    *,
    context: _RegistryContext,
    contributors: Sequence[Mapping[str, Any]],
) -> tuple[ExistingEvidenceDerivedUseDecision, bool]:
    manifest_checksum = _manifest_checksum(contributors)
    fields = _derived_use_fields(context, manifest_checksum, contributors)
    identity = canonical_sha256(
        {
            "record_type": "FD1_EXISTING_EVIDENCE_DERIVED_USE",
            "schema_version": FD1_WAREHOUSE_SCHEMA_VERSION,
            "source_version_identity_checksum": context.source.identity_checksum,
            **_json_safe(fields),
        }
    )
    existing = session.scalar(
        select(ExistingEvidenceDerivedUseDecision).where(
            ExistingEvidenceDerivedUseDecision.identity_checksum == identity
        )
    )
    if existing is not None:
        _assert_orm_fields(existing, fields, "derived-use decision")
        return existing, False
    row = ExistingEvidenceDerivedUseDecision(
        id=uuid5(_PUBLICATION_NAMESPACE, f"derived-use:{identity}"),
        **fields,
        identity_checksum=identity,
    )
    session.add(row)
    session.flush()
    return row, True


def _derived_use_fields(
    context: _RegistryContext,
    manifest_checksum: str,
    contributors: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    selected_retrievals = tuple(
        _aware(
            datetime.fromisoformat(str(item["retrieved_at"])),
            "contributor.retrieved_at",
        )
        for item in contributors
    )
    if not selected_retrievals:
        raise FD1WarehouseError("Derived-use evidence manifest is empty")
    decided_at = max(
        _stored_utc(context.owner.decided_at, "owner.decided_at"),
        max(selected_retrievals),
    )
    return {
        "owner_approval_id": context.owner.id,
        "source_version_id": context.source.id,
        "source_key": context.source.source_key,
        "evidence_manifest_checksum": manifest_checksum,
        "use_scope": "LOCAL_PRIVATE_RESEARCH",
        "decision": "ALLOWED",
        "allowed": True,
        "decided_by": "gate3c-fd1-provider-free-warehouse-operator",
        "decided_at": decided_at,
        "expires_at": context.owner.expires_at,
        "decision_basis": (
            "Project-owner-approved local private research derivation from the exact "
            "registered contributor manifest; no provider request is constructed."
        ),
    }


def _derived_use_row(
    session: Session, *, source_version_id: UUID, manifest_checksum: str
) -> ExistingEvidenceDerivedUseDecision:
    rows = list(
        session.scalars(
            select(ExistingEvidenceDerivedUseDecision).where(
                ExistingEvidenceDerivedUseDecision.source_version_id
                == source_version_id,
                ExistingEvidenceDerivedUseDecision.evidence_manifest_checksum
                == manifest_checksum,
                ExistingEvidenceDerivedUseDecision.allowed.is_(True),
            )
        )
    )
    if len(rows) != 1:
        raise FD1WarehouseError("Exact persisted local-use decision is not unique")
    return rows[0]


def _local_grant(
    *,
    context: _RegistryContext,
    contributors: Sequence[Mapping[str, Any]],
    execution_at: datetime,
    persisted_decision: ExistingEvidenceDerivedUseDecision | None,
):
    owner = LocalOwnerApproval(
        source_key=context.owner.source_key,
        approved=context.owner.approved,
        approval_scope=context.owner.approval_scope,
        decided_at=_stored_utc(context.owner.decided_at, "owner.decided_at"),
        expires_at=(
            _stored_utc(context.owner.expires_at, "owner.expires_at")
            if context.owner.expires_at is not None
            else None
        ),
    )
    values = (
        _derived_use_fields(
            context,
            _manifest_checksum(contributors),
            contributors,
        )
        if persisted_decision is None
        else {
            "source_key": persisted_decision.source_key,
            "evidence_manifest_checksum": persisted_decision.evidence_manifest_checksum,
            "allowed": persisted_decision.allowed,
            "decided_at": _stored_utc(
                persisted_decision.decided_at, "derived_use.decided_at"
            ),
            "expires_at": (
                _stored_utc(persisted_decision.expires_at, "derived_use.expires_at")
                if persisted_decision.expires_at is not None
                else None
            ),
        }
    )
    decision = LocalDerivedUseDecision(
        source_key=str(values["source_key"]),
        evidence_manifest_checksum=str(values["evidence_manifest_checksum"]),
        allowed=bool(values["allowed"]),
        decided_at=values["decided_at"],
        expires_at=values["expires_at"],
        owner_approval_identity_checksum=owner.identity_checksum,
    )
    return require_local_processing(
        decision,
        owner_approval=owner,
        as_of=execution_at,
        expected_source_key=context.source.source_key,
        expected_manifest_checksum=_manifest_checksum(contributors),
    )


def _ensure_ingestion_run(
    session: Session,
    *,
    code_revision: str,
    execution_at: datetime,
    unit_kind: str,
    unit_identity_checksum: str,
) -> tuple[IngestionRun, bool]:
    _require_sha256(unit_identity_checksum, "publication unit identity")
    publication_identity = canonical_sha256(
        {
            "schema_version": FD1_WAREHOUSE_SCHEMA_VERSION,
            "job_name": FD1_INGESTION_JOB,
            "code_revision": code_revision,
            "unit_kind": unit_kind,
            "unit_identity_checksum": unit_identity_checksum,
            "sources": {
                key: build_source_release_identity(value.release_spec())
                for key, value in sorted(FD1_SOURCE_DEFINITIONS.items())
            },
        }
    )
    run_id = uuid5(_PUBLICATION_NAMESPACE, f"ingestion:{publication_identity}")
    existing = session.get(IngestionRun, run_id)
    metadata = {
        "publication_identity_checksum": publication_identity,
        "code_revision": code_revision,
        "source_version_identity_checksums": {
            key: build_source_release_identity(value.release_spec())
            for key, value in sorted(FD1_SOURCE_DEFINITIONS.items())
        },
        "provider_transport_constructed": False,
        "network_requests": 0,
        "transaction_committed_by_operator": False,
        "unit_kind": unit_kind,
        "unit_identity_checksum": unit_identity_checksum,
    }
    if existing is not None:
        if (
            existing.source != FD1_INGESTION_SOURCE
            or existing.job_name != FD1_INGESTION_JOB
            or existing.schema_version != FD1_WAREHOUSE_SCHEMA_VERSION
            or not isinstance(existing.source_metadata, Mapping)
            or existing.source_metadata.get("publication_identity_checksum")
            != publication_identity
            or existing.status not in {RunStatus.RUNNING, RunStatus.SUCCEEDED}
        ):
            raise FD1WarehouseError("Ingestion-run identity collision")
        if (
            existing.source_metadata.get("unit_kind") != unit_kind
            or existing.source_metadata.get("unit_identity_checksum")
            != unit_identity_checksum
        ):
            raise FD1WarehouseError("Ingestion-run publication unit differs")
        persisted_metadata = dict(existing.source_metadata)
        persisted_metadata.pop("result", None)
        if _json_safe(persisted_metadata) != _json_safe(metadata):
            raise FD1WarehouseError("Ingestion-run governance metadata differs")
        return existing, False
    row = IngestionRun(
        id=run_id,
        source=FD1_INGESTION_SOURCE,
        job_name=FD1_INGESTION_JOB,
        status=RunStatus.RUNNING,
        started_at=execution_at,
        records_processed=0,
        warnings=[],
        raw_reference=None,
        schema_version=FD1_WAREHOUSE_SCHEMA_VERSION,
        source_metadata=metadata,
    )
    session.add(row)
    session.flush()
    return row, True


def _ensure_checkpoint(
    session: Session,
    *,
    ingestion_run: IngestionRun,
    context: _RegistryContext,
    family: str,
    season: int,
    month: int,
    batch_number: int,
    batch_checksum: str,
    contributors: Sequence[Mapping[str, Any]],
    row_count: int,
    min_date: date,
    max_date: date,
    code_revision: str,
    execution_at: datetime,
    quality_summary: Mapping[str, Any],
) -> tuple[HistoryBackfillCheckpoint, bool]:
    manifest_checksum = _manifest_checksum(contributors)
    query = canonical_sha256(
        {
            "schema_version": FD1_WAREHOUSE_SCHEMA_VERSION,
            "source_version_identity_checksum": context.source.identity_checksum,
            "family": family,
            "season": season,
            "month": month,
            "batch_number": batch_number,
            "batch_checksum": batch_checksum,
            "raw_contributor_checksum": manifest_checksum,
            "code_revision": code_revision,
        }
    )
    checkpoint_id = uuid5(_PUBLICATION_NAMESPACE, f"checkpoint:{query}")
    event_start_at = datetime.combine(min_date, time.min, tzinfo=UTC)
    event_end_at = datetime.combine(max_date, time.max, tzinfo=UTC)
    raw_references = tuple(str(item["logical_uri"]) for item in contributors)
    raw_checksums = tuple(str(item["file_sha256"]) for item in contributors)
    governed_quality = {
        **dict(quality_summary),
        "raw_contributor_checksum": manifest_checksum,
        "batch_checksum": batch_checksum,
        "publication_operation": "LOCAL_DERIVED_PROCESSING",
        "provider_transport_constructed": False,
        "network_requests": 0,
        "attempt_semantics": "ONE_LOCAL_PUBLICATION_ATTEMPT",
    }
    repository = SqlAlchemyCheckpointRepository(session)
    completed = repository.completed(str(context.source.id), f"fd1:{family}", query)
    if completed is not None:
        row = session.get(HistoryBackfillCheckpoint, UUID(completed.checkpoint_id))
        if row is None or row.id != checkpoint_id:
            raise FD1WarehouseError("Completed checkpoint ID is not deterministic")
        _assert_orm_fields(
            row,
            {
                "ingestion_run_id": ingestion_run.id,
                "source_version_id": context.source.id,
                "stream_key": f"fd1:{family}",
                "sequence": season * 10_000 + month * 100 + batch_number,
                "season": season,
                "event_start_at": event_start_at,
                "event_end_at": event_end_at,
                "query_fingerprint": query,
                "status": "SUCCEEDED",
                "attempt_count": 1,
                "retry_count": 0,
                "records_received": row_count,
                "records_accepted": row_count,
                "records_rejected": 0,
                "duplicate_count": 0,
                "raw_references": list(raw_references),
                "raw_checksums": list(raw_checksums),
                "schema_version": FD1_WAREHOUSE_SCHEMA_VERSION,
                "code_revision": code_revision,
                "quality_summary": governed_quality,
                "source_mode": "LIVE",
                "temporal_semantics": "RETROSPECTIVE_RECONSTRUCTION",
            },
            "completed checkpoint",
        )
        return row, False
    checkpoint = HistoricalCheckpoint(
        checkpoint_id=str(checkpoint_id),
        ingestion_run_id=str(ingestion_run.id),
        source_version_id=str(context.source.id),
        stream_key=f"fd1:{family}",
        sequence=season * 10_000 + month * 100 + batch_number,
        season=season,
        event_start_at=event_start_at,
        event_end_at=event_end_at,
        query_fingerprint=query,
        status="SUCCEEDED",
        requested_at=execution_at,
        completed_at=execution_at,
        attempt_count=1,
        retry_count=0,
        records_received=row_count,
        records_accepted=row_count,
        records_rejected=0,
        duplicate_count=0,
        raw_references=raw_references,
        raw_checksums=raw_checksums,
        schema_version=FD1_WAREHOUSE_SCHEMA_VERSION,
        code_revision=code_revision,
        quality_summary=governed_quality,
    )
    repository.append(checkpoint)
    row = session.get(HistoryBackfillCheckpoint, checkpoint_id)
    if row is None:
        raise FD1WarehouseError("Checkpoint append did not persist its deterministic ID")
    return row, True


def _persist_partition(
    session: Session,
    artifact: HistoricalEventPartitionArtifact,
    counters: _Counters,
) -> HistoricalEventPartition:
    values = artifact.orm_values()
    existing = session.scalar(
        select(HistoricalEventPartition).where(
            HistoricalEventPartition.identity_checksum == artifact.identity_checksum
        )
    )
    if existing is not None:
        _assert_orm_fields(existing, values, "historical event partition")
        counters.partition_rows_reused += 1
        return existing
    checkpoint_collisions = tuple(
        session.scalars(
            select(HistoricalEventPartition).where(
                HistoricalEventPartition.checkpoint_id == UUID(artifact.checkpoint_id)
            )
        )
    )
    if checkpoint_collisions:
        raise FD1WarehouseError(
            "One deterministic checkpoint cannot publish different Parquet evidence"
        )
    uri_collision = session.scalar(
        select(HistoricalEventPartition).where(
            HistoricalEventPartition.logical_uri == artifact.logical_uri
        )
    )
    if uri_collision is not None:
        raise FD1WarehouseError("Parquet logical URI is already bound to other metadata")
    row = HistoricalEventPartition(**values)
    session.add(row)
    session.flush()
    counters.partition_rows_created += 1
    counters.bytes_published += artifact.file_size_bytes
    return row


def _finalize_ingestion_run(
    session: Session,
    run: IngestionRun,
    *,
    records_processed: int,
    execution_at: datetime,
    result: Mapping[str, Any],
) -> None:
    if run.status == RunStatus.SUCCEEDED:
        if run.records_processed != records_processed:
            raise FD1WarehouseError(
                "Existing succeeded ingestion run has different processed-row accounting"
            )
        if not isinstance(run.source_metadata, Mapping) or run.source_metadata.get(
            "result"
        ) != dict(result):
            raise FD1WarehouseError(
                "Existing succeeded ingestion run has different result evidence"
            )
        return
    run.status = RunStatus.SUCCEEDED
    run.completed_at = execution_at
    run.records_processed = records_processed
    metadata = dict(run.source_metadata or {})
    metadata["result"] = dict(result)
    run.source_metadata = metadata
    session.flush()


def _count_checkpoint(counters: _Counters, created: bool) -> None:
    if created:
        counters.checkpoint_rows_created += 1
    else:
        counters.checkpoint_rows_reused += 1


def _count_run(counters: _Counters, created: bool) -> None:
    if created:
        counters.ingestion_run_rows_created += 1
    else:
        counters.ingestion_run_rows_reused += 1


def _manifest_checksum(contributors: Sequence[Mapping[str, Any]]) -> str:
    plain = [dict(item) for item in contributors]
    return hashlib.sha256(
        json.dumps(plain, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _assert_orm_fields(row: object, fields: Mapping[str, Any], label: str) -> None:
    for key, expected in fields.items():
        actual = getattr(row, key)
        if isinstance(expected, datetime):
            if _stored_utc(actual, f"{label}.{key}") != _aware(expected, key):
                raise FD1WarehouseError(f"Existing {label} differs at {key}")
        elif _json_safe(actual) != _json_safe(expected):
            raise FD1WarehouseError(f"Existing {label} differs at {key}")


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime):
        return _aware(value, "timestamp").isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


def _require_sha256(value: str, label: str) -> str:
    if not _SHA256.fullmatch(str(value)):
        raise FD1WarehouseError(f"{label} must be a lowercase SHA-256 digest")
    return str(value)


def _logical_uri(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "://" in value:
        raise FD1WarehouseError(f"{label} must be a relative logical URI")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise FD1WarehouseError(f"{label} must be a relative logical URI")
    return value


def _aware(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise FD1WarehouseError(f"{label} must be timezone-aware")
    return value.astimezone(UTC)


def _stored_utc(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise FD1WarehouseError(f"{label} is not a timestamp")
    # SQLite drops timezone information; registry/checkpoint timestamps are UTC.
    return (
        value.replace(tzinfo=UTC)
        if value.tzinfo is None
        else value.astimezone(UTC)
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _linklike(path: Path) -> bool:
    try:
        details = path.lstat()
    except OSError:
        return True
    return path.is_symlink() or bool(
        getattr(details, "st_file_attributes", 0) & _REPARSE_POINT
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("dry-run", "execute"):
        child = subparsers.add_parser(command)
        child.add_argument("--envelope", required=True, type=Path)
        child.add_argument("--store-root", required=True, type=Path)
        child.add_argument("--code-revision")
        child.add_argument(
            "--maximum-envelope-bytes",
            type=int,
            default=DEFAULT_MAXIMUM_ENVELOPE_BYTES,
        )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run a bounded JSON-envelope publication; execute commits explicitly."""

    args = _parser().parse_args(argv)
    envelope = load_publication_envelope(
        args.envelope, maximum_bytes=args.maximum_envelope_bytes
    )
    if args.command == "execute" and envelope["extraction_artifacts"]:
        raise FD1WarehouseError(
            "CLI archive execution is disabled because JSON cannot prove member bytes; "
            "use the direct API with load_extraction_artifact_manifest(..., "
            "verify_member_bytes=True)"
        )
    from app.db.session import SessionLocal

    store = HistoricalEventParquetStore(args.store_root)
    with SessionLocal() as session:
        try:
            summary = publish_fd1_warehouse(
                session,
                store,
                **envelope,
                code_revision=args.code_revision,
                dry_run=args.command == "dry-run",
            )
            payload = summary.as_dict()
            if args.command == "execute":
                session.commit()
                payload["transaction_committed_by_cli"] = True
        except Exception:
            session.rollback()
            raise
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def load_publication_envelope(
    path: Path, *, maximum_bytes: int = DEFAULT_MAXIMUM_ENVELOPE_BYTES
) -> dict[str, Any]:
    """Load the CLI interchange format with strict fields and bounded bytes."""

    if maximum_bytes <= 0:
        raise FD1WarehouseError("maximum envelope bytes must be positive")
    candidate = Path(path).resolve(strict=True)
    if not candidate.is_file() or candidate.is_symlink():
        raise FD1WarehouseError("Publication envelope must be a regular file")
    size = candidate.stat().st_size
    if size <= 0 or size > maximum_bytes:
        raise FD1WarehouseError("Publication envelope size is outside the configured bound")
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FD1WarehouseError("Publication envelope JSON is invalid") from exc
    if not isinstance(payload, Mapping) or set(payload) != {
        "schema_version",
        "retrosheet_batches",
        "player_prior_batches",
        "identity_mapping_batches",
        "contributor_bindings",
        "extraction_artifacts",
    }:
        raise FD1WarehouseError("Publication envelope fields are not exact")
    if payload["schema_version"] != FD1_PUBLICATION_ENVELOPE_SCHEMA_VERSION:
        raise FD1WarehouseError("Publication envelope schema version is invalid")
    try:
        return {
            "retrosheet_batches": tuple(
                _retrosheet_batch_from_json(item)
                for item in _array(payload, "retrosheet_batches")
            ),
            "player_prior_batches": tuple(
                _prior_batch_from_json(item)
                for item in _array(payload, "player_prior_batches")
            ),
            "identity_mapping_batches": tuple(
                _identity_batch_from_json(item)
                for item in _array(payload, "identity_mapping_batches")
            ),
            "contributor_bindings": tuple(
                ContributorDownloadBinding(**item)
                for item in _mapping_array(payload, "contributor_bindings")
            ),
            "extraction_artifacts": tuple(
                _extraction_from_json(item)
                for item in _mapping_array(payload, "extraction_artifacts")
            ),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise FD1WarehouseError("Publication envelope payload is invalid") from exc


def _retrosheet_batch_from_json(value: object) -> FamilyStagingPayload:
    item = _mapping(value, "retrosheet batch")
    return FamilyStagingPayload(
        **{
            **item,
            "rows": tuple(MappingProxyType(dict(row)) for row in _mapping_values(item["rows"])),
            "min_event_date": date.fromisoformat(item["min_event_date"]),
            "max_event_date": date.fromisoformat(item["max_event_date"]),
            "source_file_checksums": MappingProxyType(dict(item["source_file_checksums"])),
            "quality_report": MappingProxyType(dict(item["quality_report"])),
        }
    )


def _prior_batch_from_json(value: object) -> PlayerPriorPartitionBatch:
    item = _mapping(value, "player prior batch")
    return PlayerPriorPartitionBatch(
        **{
            **item,
            "rows": tuple(MappingProxyType(dict(row)) for row in _mapping_values(item["rows"])),
            "min_official_date": date.fromisoformat(item["min_official_date"]),
            "max_official_date": date.fromisoformat(item["max_official_date"]),
            "raw_contributor_manifest": tuple(
                MappingProxyType(dict(row))
                for row in _mapping_values(item["raw_contributor_manifest"])
            ),
        }
    )


def _identity_batch_from_json(value: object) -> RegistryIdentityMappingBatch:
    from app.gen2.open_data_registry import OpenDataIdentityMappingSpec

    item = _mapping(value, "identity mapping batch")
    specs = []
    for raw in _mapping_values(item["specs"]):
        values = dict(raw)
        for key in ("valid_from", "valid_through"):
            if values.get(key) is not None:
                values[key] = date.fromisoformat(values[key])
        specs.append(OpenDataIdentityMappingSpec(**values))
    return RegistryIdentityMappingBatch(
        batch_number=item["batch_number"],
        specs=tuple(specs),
        batch_checksum=item["batch_checksum"],
    )


def _extraction_from_json(value: object) -> ExtractionArtifact:
    item = _mapping(value, "extraction artifact")
    return ExtractionArtifact(
        **{
            **item,
            "archive_format": ArchiveFormat(item["archive_format"]),
            "members": tuple(
                ExtractedMember(**member) for member in _mapping_values(item["members"])
            ),
        }
    )


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise FD1WarehouseError(f"{label} must be an object")
    return dict(value)


def _array(value: Mapping[str, Any], key: str) -> list[Any]:
    selected = value.get(key)
    if not isinstance(selected, list):
        raise FD1WarehouseError(f"{key} must be an array")
    return selected


def _mapping_array(value: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    return [_mapping(item, key) for item in _array(value, key)]


def _mapping_values(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise FD1WarehouseError("Envelope collection must be an array")
    return [_mapping(item, "envelope item") for item in value]


if __name__ == "__main__":  # pragma: no cover - operator entry point
    raise SystemExit(main())


__all__ = [
    "DEFAULT_MAXIMUM_ENVELOPE_BYTES",
    "DEFAULT_ALLOWED_STORE_ROOTS",
    "FD1_CODE_REVISION_INPUTS",
    "FD1_PUBLICATION_ENVELOPE_SCHEMA_VERSION",
    "FD1_WAREHOUSE_SCHEMA_VERSION",
    "ContributorDownloadBinding",
    "FD1WarehouseError",
    "FD1WarehouseSummary",
    "build_archive_member_bindings",
    "build_fd1_code_revision",
    "load_extraction_artifact_manifest",
    "load_publication_envelope",
    "main",
    "publish_fd1_warehouse",
]

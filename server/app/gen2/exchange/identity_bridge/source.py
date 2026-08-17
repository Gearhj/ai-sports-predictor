"""Authoritative, bounded, read-only identity extraction for Gate 3C-X2.

This module is deliberately a source adapter rather than a general identity
repository.  Every exported mapping is bound to the one finalized Gate 3C-FD1
FULL run, its historical source release, and its promoted canonical row.  The
adapter never scans rows into Python unless they are selected by caller-supplied
Retrosheet IDs or canonical player UUIDs.

Database access is fail closed: only PostgreSQL is accepted, the transaction is
explicitly marked READ ONLY, an engine event rejects SQL outside the small read
allowlist, and the transaction is rolled back on every exit path.
"""

from __future__ import annotations

import math
import re
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
from typing import Any
from uuid import UUID

from sqlalchemy import String, and_, bindparam, event, func, select, text
from sqlalchemy.engine import Connection, Engine

from app.db.session import get_engine
from app.gen2.exchange.evidence import RECOVERY_ID, RECOVERY_MANIFEST_CHECKSUM
from app.gen2.fd1_recovery_models import (
    FD1RecoveryIdentityQuarantine,
    FD1RecoveryIdentityStage,
    FD1RecoveryManifestFile,
    FD1RecoveryRun,
)
from app.models.entities import HistoricalSourceVersion, OpenDataIdentityMapping


EXPECTED_DATABASE_MIGRATION = "20260815_0015"
EXPECTED_RECOVERY_ID = UUID(RECOVERY_ID)
EXPECTED_MANIFEST_SCHEMA = "gate3c_fd1_recovery_manifest_v1"
EXPECTED_MANIFEST_FILE_COUNT = 329
EXPECTED_STAGE_ROWS = 737_259
EXPECTED_CANONICAL_MAPPING_ROWS = 736_056
EXPECTED_QUARANTINE_ROWS = 1_203
EXPECTED_UNRESOLVED_ROWS = 1_187
EXPECTED_SOURCE_DECLARED_CONFLICT_ROWS = 16
EXPECTED_BLOCKING_CONFLICT_ROWS = 0

CANONICAL_PLAYER_NAMESPACE = "chadwick_uuid"
SUPPORTED_PERSON_NAMESPACES = (
    "baseball_reference",
    "chadwick_person",
    "lahman",
    "mlbam",
    "retrosheet",
)
MAX_LOOKUP_IDS = 10_000
MAX_ALIAS_ROWS = 250_000

_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_MUTATING_SQL = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|MERGE|UPSERT|ALTER|CREATE|DROP|TRUNCATE|"
    r"COPY|CALL|DO|GRANT|REVOKE|VACUUM|ANALYZE|REFRESH|REINDEX|CLUSTER|"
    r"LOCK|LISTEN|UNLISTEN|NOTIFY|COMMENT|INTO)\b|"
    r"\bFOR\s+(?:UPDATE|NO\s+KEY\s+UPDATE|SHARE|KEY\s+SHARE)\b",
    re.IGNORECASE,
)


class IdentityBridgeSourceError(RuntimeError):
    """Raised when the database is not the exact governed identity authority."""


class IdentityBridgeSourceDeadline(IdentityBridgeSourceError):
    """The caller's absolute runtime boundary was reached during a DB read."""


class IdentityBridgeInputError(ValueError):
    """Raised when a lookup would be ambiguous or insufficiently bounded."""


@dataclass(frozen=True, slots=True)
class SourceVersionAuthority:
    """Exact historical source release authorized by the FD1 manifest."""

    source_key: str
    source_version_id: UUID
    dataset_version: str
    source_mode: str
    release_identity_checksum: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_key": self.source_key,
            "source_version_id": str(self.source_version_id),
            "dataset_version": self.dataset_version,
            "source_mode": self.source_mode,
            "release_identity_checksum": self.release_identity_checksum,
        }


EXPECTED_SOURCE_VERSIONS = (
    SourceVersionAuthority(
        source_key="CHADWICK_REGISTER_ARCHIVES",
        source_version_id=UUID("4a4956de-0769-4bb0-9dc4-cc30515ba16a"),
        dataset_version="2e8e73355f9c77b963115377bd98c784cfeec10f",
        source_mode="LIVE",
        release_identity_checksum=(
            "8aa66c044fb49ceb681af16fb4c20bef582f94e56cdcc790f2634d26f1bf36b0"
        ),
    ),
    SourceVersionAuthority(
        source_key="RETROSHEET_AUXILIARY_DOWNLOADS",
        source_version_id=UUID("fef044cc-35e1-499a-8971-a8b29cd9dd7c"),
        dataset_version="retrosheet_through_2025",
        source_mode="LIVE",
        release_identity_checksum=(
            "47eaa3c9322215f5445c7254f72ea7dac4fa29013ae56308b187a87d75a6881e"
        ),
    ),
    SourceVersionAuthority(
        source_key="RETROSHEET_CSV_DOWNLOADS",
        source_version_id=UUID("64db602a-090a-4e37-b8f2-2a48d36dd7f2"),
        dataset_version="retrosheet_through_2025",
        source_mode="LIVE",
        release_identity_checksum=(
            "d952cbf26b3e37be338b12a24da4099a24fbafc19f839edc03631fb1ded42a52"
        ),
    ),
    SourceVersionAuthority(
        source_key="RETROSHEET_EVENT_ARCHIVES",
        source_version_id=UUID("9a08b5fd-e5ed-4043-a984-04021584f15e"),
        dataset_version="retrosheet_through_2025",
        source_mode="LIVE",
        release_identity_checksum=(
            "2ee67ae07abfc2abba53011cd22239eb7ed4e8185e10e6338b829413379c85e8"
        ),
    ),
    SourceVersionAuthority(
        source_key="SABR_LAHMAN_BOX_DOWNLOADS",
        source_version_id=UUID("3af682f0-ecdb-47d8-8e14-660ec5f7455c"),
        dataset_version="lahman_1871-2025_csv",
        source_mode="LIVE",
        release_identity_checksum=(
            "61d462de4f4030263ce46a01ee213d211adbd26ff869365ec18b3477063a3cf5"
        ),
    ),
)
_EXPECTED_SOURCE_BY_ID = {
    authority.source_version_id: authority for authority in EXPECTED_SOURCE_VERSIONS
}
_IDENTITY_STAGE_SOURCE_KEYS = frozenset(
    {
        "CHADWICK_REGISTER_ARCHIVES",
        "RETROSHEET_AUXILIARY_DOWNLOADS",
        "SABR_LAHMAN_BOX_DOWNLOADS",
    }
)


@dataclass(frozen=True, slots=True)
class ReadOnlyDatabaseBoundary:
    backend: str
    transaction_read_only: bool
    transaction_rolled_back: bool
    read_statement_count: int
    control_statement_count: int
    rejected_statement_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "transaction_read_only": self.transaction_read_only,
            "transaction_rolled_back": self.transaction_rolled_back,
            "read_statement_count": self.read_statement_count,
            "control_statement_count": self.control_statement_count,
            "rejected_statement_count": self.rejected_statement_count,
        }


@dataclass(slots=True)
class _BoundaryLedger:
    backend: str
    transaction_read_only: bool = False
    transaction_rolled_back: bool = False
    read_statement_count: int = 0
    control_statement_count: int = 0
    rejected_statement_count: int = 0

    def freeze(self) -> ReadOnlyDatabaseBoundary:
        return ReadOnlyDatabaseBoundary(
            backend=self.backend,
            transaction_read_only=self.transaction_read_only,
            transaction_rolled_back=self.transaction_rolled_back,
            read_statement_count=self.read_statement_count,
            control_statement_count=self.control_statement_count,
            rejected_statement_count=self.rejected_statement_count,
        )


@dataclass(frozen=True, slots=True)
class GovernedIdentityEvidence:
    database_migration: str
    recovery_id: UUID
    recovery_manifest_checksum: str
    manifest_file_count: int
    stage_rows: int
    canonical_mapping_rows: int
    quarantine_rows: int
    unresolved_rows: int
    source_declared_conflict_rows: int
    blocking_conflict_rows: int
    source_versions: tuple[SourceVersionAuthority, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "database_migration": self.database_migration,
            "recovery_id": str(self.recovery_id),
            "recovery_manifest_checksum": self.recovery_manifest_checksum,
            "manifest_file_count": self.manifest_file_count,
            "stage_rows": self.stage_rows,
            "canonical_mapping_rows": self.canonical_mapping_rows,
            "quarantine_rows": self.quarantine_rows,
            "unresolved_rows": self.unresolved_rows,
            "source_declared_conflict_rows": self.source_declared_conflict_rows,
            "blocking_conflict_rows": self.blocking_conflict_rows,
            "source_versions": [version.as_dict() for version in self.source_versions],
        }


@dataclass(frozen=True, slots=True)
class IdentityAlias:
    """One promoted person alias with its complete FD1 row lineage."""

    recovery_id: UUID
    scope: str
    staging_ordinal: int
    mapping_id: UUID
    target_mapping_id: UUID
    source_key: str
    source_version_id: UUID
    dataset_version: str
    source_mode: str
    source_release_identity_checksum: str
    batch_number: int
    batch_checksum: str
    entity_type: str
    source_namespace: str
    external_id: str
    canonical_namespace: str
    canonical_player_id: UUID
    source_row_number: int
    source_row_checksum: str
    confidence: str
    valid_from: date | None
    valid_through: date | None
    evidence_logical_uri: str
    mapping_basis: str
    identity_checksum: str
    record_checksum: str
    outcome: str
    reconciled_at: datetime
    stage_created_at: datetime
    mapping_created_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "recovery_id": str(self.recovery_id),
            "scope": self.scope,
            "staging_ordinal": self.staging_ordinal,
            "mapping_id": str(self.mapping_id),
            "target_mapping_id": str(self.target_mapping_id),
            "source_key": self.source_key,
            "source_version_id": str(self.source_version_id),
            "dataset_version": self.dataset_version,
            "source_mode": self.source_mode,
            "source_release_identity_checksum": (
                self.source_release_identity_checksum
            ),
            "batch_number": self.batch_number,
            "batch_checksum": self.batch_checksum,
            "entity_type": self.entity_type,
            "source_namespace": self.source_namespace,
            "external_id": self.external_id,
            "canonical_namespace": self.canonical_namespace,
            "canonical_player_id": str(self.canonical_player_id),
            "source_row_number": self.source_row_number,
            "source_row_checksum": self.source_row_checksum,
            "confidence": self.confidence,
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "valid_through": (
                self.valid_through.isoformat() if self.valid_through else None
            ),
            "evidence_logical_uri": self.evidence_logical_uri,
            "mapping_basis": self.mapping_basis,
            "identity_checksum": self.identity_checksum,
            "record_checksum": self.record_checksum,
            "outcome": self.outcome,
            "reconciled_at": self.reconciled_at.isoformat(),
            "stage_created_at": self.stage_created_at.isoformat(),
            "mapping_created_at": self.mapping_created_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class RetrosheetPlayerResolution:
    raw_retrosheet_player_id: str
    canonical_player_id: UUID
    mapping_id: UUID
    source_version_id: UUID
    source_key: str
    dataset_version: str
    source_release_identity_checksum: str
    staging_ordinal: int
    source_row_number: int
    source_row_checksum: str
    mapping_basis: str
    identity_checksum: str
    record_checksum: str

    @classmethod
    def from_alias(cls, alias: IdentityAlias) -> RetrosheetPlayerResolution:
        return cls(
            raw_retrosheet_player_id=alias.external_id,
            canonical_player_id=alias.canonical_player_id,
            mapping_id=alias.mapping_id,
            source_version_id=alias.source_version_id,
            source_key=alias.source_key,
            dataset_version=alias.dataset_version,
            source_release_identity_checksum=(
                alias.source_release_identity_checksum
            ),
            staging_ordinal=alias.staging_ordinal,
            source_row_number=alias.source_row_number,
            source_row_checksum=alias.source_row_checksum,
            mapping_basis=alias.mapping_basis,
            identity_checksum=alias.identity_checksum,
            record_checksum=alias.record_checksum,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "raw_retrosheet_player_id": self.raw_retrosheet_player_id,
            "canonical_player_id": str(self.canonical_player_id),
            "mapping_id": str(self.mapping_id),
            "source_version_id": str(self.source_version_id),
            "source_key": self.source_key,
            "dataset_version": self.dataset_version,
            "source_release_identity_checksum": (
                self.source_release_identity_checksum
            ),
            "staging_ordinal": self.staging_ordinal,
            "source_row_number": self.source_row_number,
            "source_row_checksum": self.source_row_checksum,
            "mapping_basis": self.mapping_basis,
            "identity_checksum": self.identity_checksum,
            "record_checksum": self.record_checksum,
        }


@dataclass(frozen=True, slots=True)
class IdentityBridgeSourceResult:
    raw_retrosheet_player_ids: tuple[str, ...]
    prior_canonical_player_ids: tuple[UUID, ...]
    canonical_player_ids: tuple[UUID, ...]
    retrosheet_resolutions: tuple[RetrosheetPlayerResolution, ...]
    aliases: tuple[IdentityAlias, ...]
    governed_evidence: GovernedIdentityEvidence
    database_boundary: ReadOnlyDatabaseBoundary

    def as_dict(self) -> dict[str, Any]:
        return {
            "raw_retrosheet_player_ids": list(self.raw_retrosheet_player_ids),
            "prior_canonical_player_ids": [
                str(value) for value in self.prior_canonical_player_ids
            ],
            "canonical_player_ids": [str(value) for value in self.canonical_player_ids],
            "retrosheet_resolutions": [
                resolution.as_dict() for resolution in self.retrosheet_resolutions
            ],
            "aliases": [alias.as_dict() for alias in self.aliases],
            "governed_evidence": self.governed_evidence.as_dict(),
            "database_boundary": self.database_boundary.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class OperationalRelationSnapshot:
    relation: str
    row_count: int
    digest: str
    digest_schema: str = "sha256-over-postgresql-row-json-md5-v1"

    def as_dict(self) -> dict[str, Any]:
        return {
            "relation": self.relation,
            "row_count": self.row_count,
            "digest": self.digest,
            "digest_schema": self.digest_schema,
        }


@dataclass(frozen=True, slots=True)
class OperationalSnapshot:
    captured_at: datetime
    relations: tuple[OperationalRelationSnapshot, ...]
    database_boundary: ReadOnlyDatabaseBoundary
    schema_version: str = "gate3c_x2_identity_source_operational_snapshot_v1"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "captured_at": self.captured_at.isoformat(),
            "relations": [relation.as_dict() for relation in self.relations],
            "database_boundary": self.database_boundary.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class OperationalSnapshotComparison:
    unchanged: bool
    changed_relations: tuple[str, ...]
    before_captured_at: datetime
    after_captured_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "unchanged": self.unchanged,
            "changed_relations": list(self.changed_relations),
            "before_captured_at": self.before_captured_at.isoformat(),
            "after_captured_at": self.after_captured_at.isoformat(),
        }


def _statement_kind(statement: str) -> str:
    normalized = " ".join(statement.strip().split())
    upper = normalized.upper()
    if upper == "SET TRANSACTION READ ONLY":
        return "CONTROL"
    first_token = upper.partition(" ")[0]
    if (
        first_token in {"SELECT", "WITH", "SHOW"}
        and ";" not in upper
        and _MUTATING_SQL.search(upper) is None
    ):
        return "READ"
    return "REJECT"


@contextmanager
def _read_only_connection(
    database_url: str | None,
    *,
    deadline_monotonic: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Iterator[tuple[Connection, _BoundaryLedger]]:
    if deadline_monotonic is not None and (
        isinstance(deadline_monotonic, bool)
        or not isinstance(deadline_monotonic, (int, float))
        or not math.isfinite(deadline_monotonic)
    ):
        raise IdentityBridgeInputError(
            "deadline_monotonic must be a finite absolute clock value"
        )
    engine: Engine = get_engine(database_url)
    ledger = _BoundaryLedger(backend=engine.dialect.name)
    if engine.dialect.name != "postgresql":
        engine.dispose()
        raise IdentityBridgeSourceError(
            "identity bridge source extraction requires PostgreSQL"
        )

    def enforce_read_only_sql(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        kind = _statement_kind(statement)
        if deadline_monotonic is not None:
            remaining = deadline_monotonic - clock()
            if remaining <= 0:
                raise IdentityBridgeSourceDeadline(
                    "identity source database deadline reached"
                )
            # ``SET TRANSACTION`` must remain the first transaction command.
            # Each later statement receives a timeout bounded by the caller's
            # remaining absolute budget.  The fixed integer command goes
            # directly through DBAPI to avoid recursively entering this guard.
            if kind != "CONTROL":
                timeout_ms = max(
                    1,
                    min(int(remaining * 1_000), 2_147_483_647),
                )
                try:
                    _cursor.execute(f"SET LOCAL statement_timeout = {timeout_ms}")
                except Exception as exc:
                    raise IdentityBridgeSourceError(
                        "unable to enforce the identity source statement deadline"
                    ) from exc
                ledger.control_statement_count += 1
        if kind == "READ":
            ledger.read_statement_count += 1
            return
        if kind == "CONTROL":
            ledger.control_statement_count += 1
            return
        ledger.rejected_statement_count += 1
        raise IdentityBridgeSourceError(
            "non-read SQL is prohibited during identity source extraction"
        )

    event.listen(engine, "before_cursor_execute", enforce_read_only_sql)
    connection: Connection | None = None
    transaction: Any = None
    try:
        connection = engine.connect()
        transaction = connection.begin()
        connection.execute(text("SET TRANSACTION READ ONLY"))
        read_only_setting = str(
            connection.execute(text("SHOW transaction_read_only")).scalar_one()
        ).lower()
        if read_only_setting not in {"on", "true", "1"}:
            raise IdentityBridgeSourceError(
                "PostgreSQL did not confirm a read-only transaction"
            )
        ledger.transaction_read_only = True
        yield connection, ledger
    finally:
        try:
            if transaction is not None:
                transaction.rollback()
                ledger.transaction_rolled_back = True
        finally:
            if connection is not None:
                connection.close()
            event.remove(engine, "before_cursor_execute", enforce_read_only_sql)
            engine.dispose()


def _validated_raw_ids(values: Iterable[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise IdentityBridgeInputError(
            "raw_retrosheet_player_ids must be an iterable of individual IDs"
        )
    materialized = tuple(values)
    if not materialized:
        raise IdentityBridgeInputError(
            "at least one raw Retrosheet player ID is required"
        )
    if len(materialized) > MAX_LOOKUP_IDS:
        raise IdentityBridgeInputError(
            f"raw Retrosheet lookup exceeds the {MAX_LOOKUP_IDS}-ID safety bound"
        )
    for value in materialized:
        if not isinstance(value, str):
            raise IdentityBridgeInputError("every raw Retrosheet ID must be text")
        if (
            not value
            or len(value) > 255
            or value != value.strip()
            or not value.isprintable()
        ):
            raise IdentityBridgeInputError(
                "raw Retrosheet IDs must be nonblank, exact printable values"
            )
    if len(set(materialized)) != len(materialized):
        raise IdentityBridgeInputError("raw Retrosheet player IDs must be unique")
    return materialized


def _validated_canonical_ids(values: Iterable[UUID | str]) -> tuple[UUID, ...]:
    if isinstance(values, (str, bytes, UUID)):
        raise IdentityBridgeInputError(
            "prior_canonical_player_ids must be an iterable of UUID values"
        )
    result: list[UUID] = []
    for value in values:
        if isinstance(value, UUID):
            parsed = value
        elif isinstance(value, str):
            try:
                parsed = UUID(value)
            except ValueError as exc:
                raise IdentityBridgeInputError(
                    "prior canonical player IDs must be valid UUIDs"
                ) from exc
        else:
            raise IdentityBridgeInputError(
                "prior canonical player IDs must be UUIDs or UUID strings"
            )
        result.append(parsed)
    if len(result) > MAX_LOOKUP_IDS:
        raise IdentityBridgeInputError(
            f"prior canonical lookup exceeds the {MAX_LOOKUP_IDS}-ID safety bound"
        )
    if len(set(result)) != len(result):
        raise IdentityBridgeInputError("prior canonical player IDs must be unique")
    return tuple(result)


def _finalized_mapping_join() -> Any:
    stage = FD1RecoveryIdentityStage.__table__
    mapping = OpenDataIdentityMapping.__table__
    version = HistoricalSourceVersion.__table__
    run = FD1RecoveryRun.__table__
    return (
        stage.join(
            mapping,
            and_(
                mapping.c.id == stage.c.target_mapping_id,
                mapping.c.identity_checksum == stage.c.identity_checksum,
                mapping.c.source_version_id == stage.c.source_version_id,
                mapping.c.entity_type == stage.c.entity_type,
                mapping.c.source_namespace == stage.c.source_namespace,
                mapping.c.external_id == stage.c.external_id,
                mapping.c.canonical_namespace == stage.c.canonical_namespace,
                mapping.c.canonical_id.is_not_distinct_from(stage.c.canonical_id),
                mapping.c.source_row_number == stage.c.source_row_number,
                mapping.c.source_row_checksum == stage.c.source_row_checksum,
                mapping.c.confidence == stage.c.confidence,
                mapping.c.valid_from.is_not_distinct_from(stage.c.valid_from),
                mapping.c.valid_through.is_not_distinct_from(stage.c.valid_through),
                mapping.c.evidence_logical_uri == stage.c.evidence_logical_uri,
            ),
        )
        .join(
            version,
            and_(
                version.c.id == stage.c.source_version_id,
                version.c.source_key == stage.c.source_key,
                version.c.identity_checksum
                == stage.c.source_release_identity_checksum,
            ),
        )
        .join(run, run.c.id == stage.c.run_id)
    )


def _finalized_mapping_predicates() -> tuple[Any, ...]:
    stage = FD1RecoveryIdentityStage.__table__
    version = HistoricalSourceVersion.__table__
    run = FD1RecoveryRun.__table__
    return (
        stage.c.run_id == bindparam("recovery_id"),
        stage.c.scope == bindparam("scope"),
        stage.c.outcome.in_(("INSERT", "REUSE")),
        run.c.manifest_checksum == bindparam("manifest_checksum"),
        run.c.status == "FINALIZED",
        run.c.last_completed_stage == "FINALIZED",
        run.c.active_stage.is_(None),
        version.c.source_mode == "LIVE",
    )


def _alias_columns() -> tuple[Any, ...]:
    stage = FD1RecoveryIdentityStage.__table__
    mapping = OpenDataIdentityMapping.__table__
    version = HistoricalSourceVersion.__table__
    return (
        stage.c.run_id.label("recovery_id"),
        stage.c.scope,
        stage.c.staging_ordinal,
        mapping.c.id.label("mapping_id"),
        stage.c.target_mapping_id,
        stage.c.source_key,
        stage.c.source_version_id,
        version.c.dataset_version,
        version.c.source_mode,
        stage.c.source_release_identity_checksum,
        version.c.identity_checksum.label("version_identity_checksum"),
        stage.c.batch_number,
        stage.c.batch_checksum,
        stage.c.entity_type,
        stage.c.source_namespace,
        stage.c.external_id,
        stage.c.canonical_namespace,
        stage.c.canonical_id,
        stage.c.source_row_number,
        stage.c.source_row_checksum,
        stage.c.confidence,
        stage.c.valid_from,
        stage.c.valid_through,
        stage.c.evidence_logical_uri,
        stage.c.mapping_basis,
        stage.c.identity_checksum,
        mapping.c.identity_checksum.label("mapping_identity_checksum"),
        stage.c.record_checksum,
        stage.c.outcome,
        stage.c.reconciled_at,
        stage.c.created_at.label("stage_created_at"),
        mapping.c.created_at.label("mapping_created_at"),
    )


_FINALIZED_PARAMETERS = {
    "recovery_id": EXPECTED_RECOVERY_ID,
    "scope": "FULL",
    "manifest_checksum": RECOVERY_MANIFEST_CHECKSUM,
}


def _validated_checksum(value: object, label: str) -> str:
    rendered = str(value)
    if _HEX_64.fullmatch(rendered) is None:
        raise IdentityBridgeSourceError(f"{label} is not a lowercase SHA-256 value")
    return rendered


def _alias_from_row(row: Mapping[str, Any]) -> IdentityAlias:
    try:
        canonical_player_id = UUID(str(row["canonical_id"]))
        source_version_id = UUID(str(row["source_version_id"]))
        mapping_id = UUID(str(row["mapping_id"]))
        target_mapping_id = UUID(str(row["target_mapping_id"]))
    except (TypeError, ValueError) as exc:
        raise IdentityBridgeSourceError(
            "a finalized person mapping contains a non-UUID authority ID"
        ) from exc

    authority = _EXPECTED_SOURCE_BY_ID.get(source_version_id)
    if authority is None:
        raise IdentityBridgeSourceError(
            "a finalized alias references an unauthorized source version"
        )
    if (
        str(row["source_key"]) != authority.source_key
        or str(row["dataset_version"]) != authority.dataset_version
        or str(row["source_mode"]) != authority.source_mode
        or str(row["source_release_identity_checksum"])
        != authority.release_identity_checksum
        or str(row["version_identity_checksum"])
        != authority.release_identity_checksum
    ):
        raise IdentityBridgeSourceError("a finalized alias changed source release")
    if mapping_id != target_mapping_id:
        raise IdentityBridgeSourceError("canonical mapping target lineage changed")
    if str(row["mapping_identity_checksum"]) != str(row["identity_checksum"]):
        raise IdentityBridgeSourceError("canonical/stage mapping checksum changed")
    if (
        UUID(str(row["recovery_id"])) != EXPECTED_RECOVERY_ID
        or str(row["scope"]) != "FULL"
        or str(row["entity_type"]) != "person"
        or str(row["source_namespace"]) not in SUPPORTED_PERSON_NAMESPACES
        or str(row["canonical_namespace"]) != CANONICAL_PLAYER_NAMESPACE
        or str(row["confidence"]) not in {"EXACT", "HIGH"}
        or str(row["outcome"]) not in {"INSERT", "REUSE"}
    ):
        raise IdentityBridgeSourceError("finalized alias authority constraints changed")
    reconciled_at = row["reconciled_at"]
    stage_created_at = row["stage_created_at"]
    mapping_created_at = row["mapping_created_at"]
    if not all(
        isinstance(value, datetime)
        for value in (reconciled_at, stage_created_at, mapping_created_at)
    ):
        raise IdentityBridgeSourceError("finalized alias timing lineage is absent")

    return IdentityAlias(
        recovery_id=EXPECTED_RECOVERY_ID,
        scope="FULL",
        staging_ordinal=int(row["staging_ordinal"]),
        mapping_id=mapping_id,
        target_mapping_id=target_mapping_id,
        source_key=authority.source_key,
        source_version_id=source_version_id,
        dataset_version=authority.dataset_version,
        source_mode=authority.source_mode,
        source_release_identity_checksum=authority.release_identity_checksum,
        batch_number=int(row["batch_number"]),
        batch_checksum=_validated_checksum(row["batch_checksum"], "batch checksum"),
        entity_type="person",
        source_namespace=str(row["source_namespace"]),
        external_id=str(row["external_id"]),
        canonical_namespace=CANONICAL_PLAYER_NAMESPACE,
        canonical_player_id=canonical_player_id,
        source_row_number=int(row["source_row_number"]),
        source_row_checksum=_validated_checksum(
            row["source_row_checksum"], "source-row checksum"
        ),
        confidence=str(row["confidence"]),
        valid_from=row["valid_from"],
        valid_through=row["valid_through"],
        evidence_logical_uri=str(row["evidence_logical_uri"]),
        mapping_basis=str(row["mapping_basis"]),
        identity_checksum=_validated_checksum(
            row["identity_checksum"], "mapping identity checksum"
        ),
        record_checksum=_validated_checksum(
            row["record_checksum"], "stage record checksum"
        ),
        outcome=str(row["outcome"]),
        reconciled_at=reconciled_at,
        stage_created_at=stage_created_at,
        mapping_created_at=mapping_created_at,
    )


def _load_and_validate_governed_evidence(
    connection: Connection,
) -> GovernedIdentityEvidence:
    migrations = tuple(
        str(value)
        for value in connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalars()
    )
    if migrations != (EXPECTED_DATABASE_MIGRATION,):
        raise IdentityBridgeSourceError(
            "database migration is not the exact Gate 3C-FD1 head"
        )

    run_table = FD1RecoveryRun.__table__
    run = connection.execute(
        select(
            run_table.c.id,
            run_table.c.manifest_checksum,
            run_table.c.manifest_schema_version,
            run_table.c.identity_spool_row_count,
            run_table.c.status,
            run_table.c.active_stage,
            run_table.c.last_completed_stage,
            run_table.c.rehearsal_succeeded_at,
            run_table.c.lease_owner,
            run_table.c.lease_expires_at,
            run_table.c.provider_calls,
            run_table.c.finalized_at,
        ).where(run_table.c.id == bindparam("run_id")),
        {"run_id": EXPECTED_RECOVERY_ID},
    ).mappings().one_or_none()
    if run is None:
        raise IdentityBridgeSourceError("the exact finalized FD1 recovery run is absent")
    expected_run_values = {
        "manifest_checksum": RECOVERY_MANIFEST_CHECKSUM,
        "manifest_schema_version": EXPECTED_MANIFEST_SCHEMA,
        "identity_spool_row_count": EXPECTED_STAGE_ROWS,
        "status": "FINALIZED",
        "active_stage": None,
        "last_completed_stage": "FINALIZED",
        "lease_owner": None,
        "lease_expires_at": None,
        "provider_calls": 0,
    }
    if any(run[key] != value for key, value in expected_run_values.items()):
        raise IdentityBridgeSourceError("the FD1 recovery run authority changed")
    if run["rehearsal_succeeded_at"] is None or run["finalized_at"] is None:
        raise IdentityBridgeSourceError("the FD1 finalization/rehearsal evidence is absent")

    manifest_table = FD1RecoveryManifestFile.__table__
    manifest_file_count = int(
        connection.execute(
            select(func.count())
            .select_from(manifest_table)
            .where(manifest_table.c.run_id == bindparam("manifest_run_id")),
            {"manifest_run_id": EXPECTED_RECOVERY_ID},
        ).scalar_one()
    )
    if manifest_file_count != EXPECTED_MANIFEST_FILE_COUNT:
        raise IdentityBridgeSourceError("the persisted FD1 manifest population changed")

    version_table = HistoricalSourceVersion.__table__
    expected_ids = tuple(_EXPECTED_SOURCE_BY_ID)
    version_rows = list(
        connection.execute(
            select(
                version_table.c.id,
                version_table.c.source_key,
                version_table.c.dataset_version,
                version_table.c.source_mode,
                version_table.c.identity_checksum,
            ).where(
                version_table.c.id.in_(
                    bindparam(
                        "source_version_ids",
                        expanding=True,
                    )
                )
            ),
            {"source_version_ids": expected_ids},
        ).mappings()
    )
    if len(version_rows) != len(EXPECTED_SOURCE_VERSIONS):
        raise IdentityBridgeSourceError("the FD1 historical source registry is incomplete")
    for row in version_rows:
        authority = _EXPECTED_SOURCE_BY_ID.get(UUID(str(row["id"])))
        if authority is None or (
            str(row["source_key"]),
            str(row["dataset_version"]),
            str(row["source_mode"]),
            str(row["identity_checksum"]),
        ) != (
            authority.source_key,
            authority.dataset_version,
            authority.source_mode,
            authority.release_identity_checksum,
        ):
            raise IdentityBridgeSourceError("an FD1 historical source version changed")

    stage = FD1RecoveryIdentityStage.__table__
    stage_versions = {
        (
            UUID(str(row["source_version_id"])),
            str(row["source_key"]),
            str(row["source_release_identity_checksum"]),
        )
        for row in connection.execute(
            select(
                stage.c.source_version_id,
                stage.c.source_key,
                stage.c.source_release_identity_checksum,
            )
            .where(
                stage.c.run_id == bindparam("stage_version_run_id"),
                stage.c.scope == bindparam("stage_version_scope"),
            )
            .distinct(),
            {
                "stage_version_run_id": EXPECTED_RECOVERY_ID,
                "stage_version_scope": "FULL",
            },
        ).mappings()
    }
    expected_stage_versions = {
        (
            authority.source_version_id,
            authority.source_key,
            authority.release_identity_checksum,
        )
        for authority in EXPECTED_SOURCE_VERSIONS
        if authority.source_key in _IDENTITY_STAGE_SOURCE_KEYS
    }
    if stage_versions != expected_stage_versions:
        raise IdentityBridgeSourceError("the FULL stage source-release set changed")

    outcome_counts = {
        str(row["outcome"]): int(row["row_count"])
        for row in connection.execute(
            select(stage.c.outcome, func.count().label("row_count"))
            .where(
                stage.c.run_id == bindparam("outcome_run_id"),
                stage.c.scope == bindparam("outcome_scope"),
            )
            .group_by(stage.c.outcome),
            {"outcome_run_id": EXPECTED_RECOVERY_ID, "outcome_scope": "FULL"},
        ).mappings()
    }
    expected_outcomes = {
        "INSERT": EXPECTED_CANONICAL_MAPPING_ROWS,
        "UNRESOLVED": EXPECTED_QUARANTINE_ROWS,
    }
    if outcome_counts != expected_outcomes:
        raise IdentityBridgeSourceError("the governed FD1 stage outcome counts changed")

    unresolved_reasons = {
        str(row["reason_code"]): int(row["row_count"])
        for row in connection.execute(
            select(stage.c.reason_code, func.count().label("row_count"))
            .where(
                stage.c.run_id == bindparam("reason_run_id"),
                stage.c.scope == bindparam("reason_scope"),
                stage.c.outcome == "UNRESOLVED",
            )
            .group_by(stage.c.reason_code),
            {"reason_run_id": EXPECTED_RECOVERY_ID, "reason_scope": "FULL"},
        ).mappings()
    }
    source_conflicts = unresolved_reasons.get(
        "SOURCE_DECLARED_CONFLICT_QUARANTINED", 0
    )
    unresolved_rows = EXPECTED_QUARANTINE_ROWS - source_conflicts
    if (
        source_conflicts != EXPECTED_SOURCE_DECLARED_CONFLICT_ROWS
        or unresolved_rows != EXPECTED_UNRESOLVED_ROWS
    ):
        raise IdentityBridgeSourceError("the governed FD1 quarantine split changed")

    quarantine = FD1RecoveryIdentityQuarantine.__table__
    quarantine_rows = list(
        connection.execute(
            select(
                quarantine.c.outcome,
                quarantine.c.reason_code,
                func.count().label("row_count"),
            )
            .where(
                quarantine.c.run_id == bindparam("quarantine_run_id"),
                quarantine.c.scope == bindparam("quarantine_scope"),
            )
            .group_by(quarantine.c.outcome, quarantine.c.reason_code),
            {
                "quarantine_run_id": EXPECTED_RECOVERY_ID,
                "quarantine_scope": "FULL",
            },
        ).mappings()
    )
    if (
        sum(int(row["row_count"]) for row in quarantine_rows)
        != EXPECTED_QUARANTINE_ROWS
        or {str(row["outcome"]) for row in quarantine_rows} != {"UNRESOLVED"}
        or sum(
            int(row["row_count"])
            for row in quarantine_rows
            if row["reason_code"] == "SOURCE_DECLARED_CONFLICT_QUARANTINED"
        )
        != EXPECTED_SOURCE_DECLARED_CONFLICT_ROWS
    ):
        raise IdentityBridgeSourceError("the persisted FD1 quarantine changed")

    resolved_mapping_rows = int(
        connection.execute(
            select(func.count())
            .select_from(_finalized_mapping_join())
            .where(*_finalized_mapping_predicates()),
            _FINALIZED_PARAMETERS,
        ).scalar_one()
    )
    if resolved_mapping_rows != EXPECTED_CANONICAL_MAPPING_ROWS:
        raise IdentityBridgeSourceError(
            "the finalized canonical identity mapping population changed"
        )

    return GovernedIdentityEvidence(
        database_migration=EXPECTED_DATABASE_MIGRATION,
        recovery_id=EXPECTED_RECOVERY_ID,
        recovery_manifest_checksum=RECOVERY_MANIFEST_CHECKSUM,
        manifest_file_count=manifest_file_count,
        stage_rows=sum(outcome_counts.values()),
        canonical_mapping_rows=resolved_mapping_rows,
        quarantine_rows=EXPECTED_QUARANTINE_ROWS,
        unresolved_rows=unresolved_rows,
        source_declared_conflict_rows=source_conflicts,
        blocking_conflict_rows=outcome_counts.get("CONFLICT", 0),
        source_versions=EXPECTED_SOURCE_VERSIONS,
    )


def _phase_one_retrosheet_aliases(
    connection: Connection,
    raw_ids: Sequence[str],
) -> tuple[IdentityAlias, ...]:
    stage = FD1RecoveryIdentityStage.__table__
    statement = (
        select(*_alias_columns())
        .select_from(_finalized_mapping_join())
        .where(
            *_finalized_mapping_predicates(),
            stage.c.entity_type == "person",
            stage.c.source_namespace == "retrosheet",
            stage.c.canonical_namespace == CANONICAL_PLAYER_NAMESPACE,
            stage.c.confidence == "EXACT",
            stage.c.external_id.in_(
                bindparam("raw_retrosheet_ids", expanding=True, type_=String())
            ),
        )
        .order_by(stage.c.external_id, stage.c.staging_ordinal)
    )
    rows = connection.execute(
        statement,
        {**_FINALIZED_PARAMETERS, "raw_retrosheet_ids": tuple(raw_ids)},
    ).mappings().all()
    aliases = tuple(_alias_from_row(row) for row in rows)
    by_raw_id: dict[str, list[IdentityAlias]] = defaultdict(list)
    for alias in aliases:
        by_raw_id[alias.external_id].append(alias)
    missing = tuple(raw_id for raw_id in raw_ids if raw_id not in by_raw_id)
    ambiguous = tuple(
        raw_id for raw_id in raw_ids if len(by_raw_id.get(raw_id, ())) != 1
    )
    if missing:
        raise IdentityBridgeSourceError(
            f"{len(missing)} supplied Retrosheet player IDs lack exact mappings"
        )
    if ambiguous:
        raise IdentityBridgeSourceError(
            f"{len(ambiguous)} supplied Retrosheet player IDs are not uniquely mapped"
        )
    return tuple(by_raw_id[raw_id][0] for raw_id in raw_ids)


def _phase_two_person_aliases(
    connection: Connection,
    canonical_player_ids: Sequence[UUID],
) -> tuple[IdentityAlias, ...]:
    stage = FD1RecoveryIdentityStage.__table__
    statement = (
        select(*_alias_columns())
        .select_from(_finalized_mapping_join())
        .where(
            *_finalized_mapping_predicates(),
            stage.c.entity_type == "person",
            stage.c.source_namespace.in_(SUPPORTED_PERSON_NAMESPACES),
            stage.c.canonical_namespace == CANONICAL_PLAYER_NAMESPACE,
            stage.c.confidence.in_(("EXACT", "HIGH")),
            stage.c.canonical_id.in_(
                bindparam("canonical_player_ids", expanding=True, type_=String())
            ),
        )
        .order_by(
            stage.c.canonical_id,
            stage.c.source_namespace,
            stage.c.external_id,
            stage.c.source_row_number,
        )
    )
    rows = connection.execute(
        statement,
        {
            **_FINALIZED_PARAMETERS,
            "canonical_player_ids": tuple(str(value) for value in canonical_player_ids),
        },
    ).mappings().all()
    if len(rows) > MAX_ALIAS_ROWS:
        raise IdentityBridgeSourceError(
            f"bounded alias query exceeded the {MAX_ALIAS_ROWS}-row safety limit"
        )
    aliases = tuple(_alias_from_row(row) for row in rows)
    expected_ids = set(canonical_player_ids)
    observed_ids = {alias.canonical_player_id for alias in aliases}
    if observed_ids != expected_ids:
        missing = expected_ids - observed_ids
        unexpected = observed_ids - expected_ids
        raise IdentityBridgeSourceError(
            "supported aliases do not exactly cover the requested canonical players "
            f"(missing={len(missing)}, unexpected={len(unexpected)})"
        )
    mapping_ids = [alias.mapping_id for alias in aliases]
    if len(mapping_ids) != len(set(mapping_ids)):
        raise IdentityBridgeSourceError("the finalized alias query returned duplicates")
    return aliases


def load_identity_bridge_source(
    raw_retrosheet_player_ids: Iterable[str],
    prior_canonical_player_ids: Iterable[UUID | str] = (),
    *,
    database_url: str | None = None,
    deadline_monotonic: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> IdentityBridgeSourceResult:
    """Load the exact bridge population from the finalized FD1 database authority.

    Phase 1 resolves every supplied raw Retrosheet ID to exactly one canonical
    Chadwick UUID.  Phase 2 unions those UUIDs with the supplied ``player_prior``
    UUIDs and returns only supported person aliases.  All queries are bounded by
    those inputs; the full staging population is used only in aggregate checks.
    """

    raw_ids = _validated_raw_ids(raw_retrosheet_player_ids)
    prior_ids = _validated_canonical_ids(prior_canonical_player_ids)
    ledger: _BoundaryLedger | None = None
    with _read_only_connection(
        database_url,
        deadline_monotonic=deadline_monotonic,
        clock=clock,
    ) as (connection, active_ledger):
        ledger = active_ledger
        governed_evidence = _load_and_validate_governed_evidence(connection)
        retrosheet_aliases = _phase_one_retrosheet_aliases(connection, raw_ids)
        canonical_ids = tuple(
            dict.fromkeys(
                (
                    *(alias.canonical_player_id for alias in retrosheet_aliases),
                    *prior_ids,
                )
            )
        )
        if len(canonical_ids) > MAX_LOOKUP_IDS:
            raise IdentityBridgeInputError(
                f"combined canonical lookup exceeds the {MAX_LOOKUP_IDS}-ID safety bound"
            )
        aliases = _phase_two_person_aliases(connection, canonical_ids)

    if ledger is None or not ledger.transaction_rolled_back:
        raise IdentityBridgeSourceError("read-only extraction rollback was not observed")
    resolutions = tuple(
        RetrosheetPlayerResolution.from_alias(alias) for alias in retrosheet_aliases
    )
    return IdentityBridgeSourceResult(
        raw_retrosheet_player_ids=raw_ids,
        prior_canonical_player_ids=prior_ids,
        canonical_player_ids=canonical_ids,
        retrosheet_resolutions=resolutions,
        aliases=aliases,
        governed_evidence=governed_evidence,
        database_boundary=ledger.freeze(),
    )


_SNAPSHOT_QUERIES = (
    (
        "forecasts",
        """
        WITH snapshot_rows AS (
            SELECT id::text AS sort_key, md5(row_to_json(t)::text) AS row_digest
            FROM forecasts AS t
        )
        SELECT COUNT(*)::bigint AS row_count,
               md5(COALESCE(string_agg(row_digest, '' ORDER BY sort_key), ''))
                   AS database_digest
        FROM snapshot_rows
        """,
    ),
    (
        "provider_request_ledger",
        """
        WITH snapshot_rows AS (
            SELECT id::text AS sort_key, md5(row_to_json(t)::text) AS row_digest
            FROM provider_request_ledger AS t
        )
        SELECT COUNT(*)::bigint AS row_count,
               md5(COALESCE(string_agg(row_digest, '' ORDER BY sort_key), ''))
                   AS database_digest
        FROM snapshot_rows
        """,
    ),
    (
        "fd1_recovery_runs",
        """
        WITH snapshot_rows AS (
            SELECT id::text AS sort_key, md5(row_to_json(t)::text) AS row_digest
            FROM fd1_recovery_runs AS t
        )
        SELECT COUNT(*)::bigint AS row_count,
               md5(COALESCE(string_agg(row_digest, '' ORDER BY sort_key), ''))
                   AS database_digest
        FROM snapshot_rows
        """,
    ),
    (
        "fd1_recovery_stage_checkpoints",
        """
        WITH snapshot_rows AS (
            SELECT id::text AS sort_key, md5(row_to_json(t)::text) AS row_digest
            FROM fd1_recovery_stage_checkpoints AS t
        )
        SELECT COUNT(*)::bigint AS row_count,
               md5(COALESCE(string_agg(row_digest, '' ORDER BY sort_key), ''))
                   AS database_digest
        FROM snapshot_rows
        """,
    ),
    (
        "fd1_recovery_manifest_files",
        """
        WITH snapshot_rows AS (
            SELECT id::text AS sort_key, md5(row_to_json(t)::text) AS row_digest
            FROM fd1_recovery_manifest_files AS t
        )
        SELECT COUNT(*)::bigint AS row_count,
               md5(COALESCE(string_agg(row_digest, '' ORDER BY sort_key), ''))
                   AS database_digest
        FROM snapshot_rows
        """,
    ),
    (
        "fd1_recovery_identity_stage",
        """
        WITH snapshot_rows AS (
            SELECT concat_ws(':', run_id::text, scope, staging_ordinal::text)
                       AS sort_key,
                   md5(row_to_json(t)::text) AS row_digest
            FROM fd1_recovery_identity_stage AS t
        )
        SELECT COUNT(*)::bigint AS row_count,
               md5(COALESCE(string_agg(row_digest, '' ORDER BY sort_key), ''))
                   AS database_digest
        FROM snapshot_rows
        """,
    ),
    (
        "fd1_recovery_identity_quarantine",
        """
        WITH snapshot_rows AS (
            SELECT id::text AS sort_key, md5(row_to_json(t)::text) AS row_digest
            FROM fd1_recovery_identity_quarantine AS t
        )
        SELECT COUNT(*)::bigint AS row_count,
               md5(COALESCE(string_agg(row_digest, '' ORDER BY sort_key), ''))
                   AS database_digest
        FROM snapshot_rows
        """,
    ),
    (
        "fd1_recovery_publication_stage",
        """
        WITH snapshot_rows AS (
            SELECT id::text AS sort_key, md5(row_to_json(t)::text) AS row_digest
            FROM fd1_recovery_publication_stage AS t
        )
        SELECT COUNT(*)::bigint AS row_count,
               md5(COALESCE(string_agg(row_digest, '' ORDER BY sort_key), ''))
                   AS database_digest
        FROM snapshot_rows
        """,
    ),
    (
        "open_data_identity_mappings",
        """
        WITH snapshot_rows AS (
            SELECT id::text AS sort_key, md5(row_to_json(t)::text) AS row_digest
            FROM open_data_identity_mappings AS t
        )
        SELECT COUNT(*)::bigint AS row_count,
               md5(COALESCE(string_agg(row_digest, '' ORDER BY sort_key), ''))
                   AS database_digest
        FROM snapshot_rows
        """,
    ),
    (
        "historical_event_partitions",
        """
        WITH snapshot_rows AS (
            SELECT id::text AS sort_key, md5(row_to_json(t)::text) AS row_digest
            FROM historical_event_partitions AS t
        )
        SELECT COUNT(*)::bigint AS row_count,
               md5(COALESCE(string_agg(row_digest, '' ORDER BY sort_key), ''))
                   AS database_digest
        FROM snapshot_rows
        """,
    ),
)


def capture_operational_snapshot(
    *,
    database_url: str | None = None,
    deadline_monotonic: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> OperationalSnapshot:
    """Return secret-free counts/digests suitable for pre/post publication proof.

    Rows are hashed inside PostgreSQL.  Only a row count and a domain-separated
    aggregate digest cross the database boundary; database URLs, payloads,
    provider metadata, and credentials are never returned.
    """

    relation_snapshots: list[OperationalRelationSnapshot] = []
    ledger: _BoundaryLedger | None = None
    captured_at = datetime.now(timezone.utc)
    with _read_only_connection(
        database_url,
        deadline_monotonic=deadline_monotonic,
        clock=clock,
    ) as (connection, active_ledger):
        ledger = active_ledger
        for relation, query in _SNAPSHOT_QUERIES:
            row = connection.execute(text(query)).mappings().one()
            row_count = int(row["row_count"])
            database_digest = str(row["database_digest"])
            if re.fullmatch(r"[0-9a-f]{32}", database_digest) is None:
                raise IdentityBridgeSourceError(
                    f"operational snapshot digest failed for {relation}"
                )
            digest = sha256(
                (
                    "gate3c-x2-operational-snapshot-v1\0"
                    f"{relation}\0{row_count}\0{database_digest}"
                ).encode("utf-8")
            ).hexdigest()
            relation_snapshots.append(
                OperationalRelationSnapshot(
                    relation=relation,
                    row_count=row_count,
                    digest=digest,
                )
            )

    if ledger is None or not ledger.transaction_rolled_back:
        raise IdentityBridgeSourceError("operational snapshot rollback was not observed")
    return OperationalSnapshot(
        captured_at=captured_at,
        relations=tuple(relation_snapshots),
        database_boundary=ledger.freeze(),
    )


def compare_operational_snapshots(
    before: OperationalSnapshot,
    after: OperationalSnapshot,
) -> OperationalSnapshotComparison:
    """Compare two snapshots without exposing any underlying database values."""

    if before.schema_version != after.schema_version:
        raise IdentityBridgeInputError("operational snapshot schemas differ")
    before_relations = {row.relation: row for row in before.relations}
    after_relations = {row.relation: row for row in after.relations}
    if set(before_relations) != set(after_relations):
        raise IdentityBridgeInputError("operational snapshot relation sets differ")
    changed = tuple(
        name
        for name in sorted(before_relations)
        if (
            before_relations[name].row_count,
            before_relations[name].digest,
            before_relations[name].digest_schema,
        )
        != (
            after_relations[name].row_count,
            after_relations[name].digest,
            after_relations[name].digest_schema,
        )
    )
    return OperationalSnapshotComparison(
        unchanged=not changed,
        changed_relations=changed,
        before_captured_at=before.captured_at,
        after_captured_at=after.captured_at,
    )


def _import_self_check() -> None:
    if EXPECTED_RECOVERY_ID != UUID(RECOVERY_ID):
        raise RuntimeError("identity source recovery constant is invalid")
    if _HEX_64.fullmatch(RECOVERY_MANIFEST_CHECKSUM) is None:
        raise RuntimeError("identity source manifest checksum is invalid")
    if (
        EXPECTED_CANONICAL_MAPPING_ROWS + EXPECTED_QUARANTINE_ROWS
        != EXPECTED_STAGE_ROWS
        or EXPECTED_UNRESOLVED_ROWS
        + EXPECTED_SOURCE_DECLARED_CONFLICT_ROWS
        != EXPECTED_QUARANTINE_ROWS
        or EXPECTED_BLOCKING_CONFLICT_ROWS != 0
    ):
        raise RuntimeError("identity source governed counts are inconsistent")
    if tuple(sorted(SUPPORTED_PERSON_NAMESPACES)) != SUPPORTED_PERSON_NAMESPACES:
        raise RuntimeError("identity source namespace allowlist is not canonical")
    if len(_EXPECTED_SOURCE_BY_ID) != len(EXPECTED_SOURCE_VERSIONS):
        raise RuntimeError("identity source version IDs are not unique")
    if len({row.source_key for row in EXPECTED_SOURCE_VERSIONS}) != len(
        EXPECTED_SOURCE_VERSIONS
    ):
        raise RuntimeError("identity source keys are not unique")
    if not _IDENTITY_STAGE_SOURCE_KEYS < {
        row.source_key for row in EXPECTED_SOURCE_VERSIONS
    }:
        raise RuntimeError("identity-stage source authority is invalid")
    if any(
        row.source_mode != "LIVE"
        or _HEX_64.fullmatch(row.release_identity_checksum) is None
        for row in EXPECTED_SOURCE_VERSIONS
    ):
        raise RuntimeError("identity source release authority is invalid")
    snapshot_relations = tuple(name for name, _query in _SNAPSHOT_QUERIES)
    if len(snapshot_relations) != len(set(snapshot_relations)):
        raise RuntimeError("operational snapshot relation names are not unique")
    if any(_statement_kind(query) != "READ" for _name, query in _SNAPSHOT_QUERIES):
        raise RuntimeError("operational snapshot query left the read allowlist")


_import_self_check()


__all__ = [
    "CANONICAL_PLAYER_NAMESPACE",
    "EXPECTED_DATABASE_MIGRATION",
    "EXPECTED_SOURCE_VERSIONS",
    "GovernedIdentityEvidence",
    "IdentityAlias",
    "IdentityBridgeInputError",
    "IdentityBridgeSourceError",
    "IdentityBridgeSourceDeadline",
    "IdentityBridgeSourceResult",
    "OperationalRelationSnapshot",
    "OperationalSnapshot",
    "OperationalSnapshotComparison",
    "ReadOnlyDatabaseBoundary",
    "RetrosheetPlayerResolution",
    "SUPPORTED_PERSON_NAMESPACES",
    "SourceVersionAuthority",
    "capture_operational_snapshot",
    "compare_operational_snapshots",
    "load_identity_bridge_source",
]

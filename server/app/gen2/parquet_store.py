"""Governed, immutable Parquet storage for Gen-2 historical MLB events."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any
from uuid import UUID, uuid4

import polars as pl

from app.gen2.source_authorization import AuthorizationGrant, LOCAL_PRIVATE_RESEARCH


GIB = 1024**3
DEFAULT_MINIMUM_FREE_BYTES_AFTER_WRITE = 100 * GIB
DEFAULT_MAXIMUM_PARTITION_BYTES = 100 * GIB
DEFAULT_MAXIMUM_STORE_BYTES = 100 * GIB
EVENT_FAMILIES = frozenset(
    {
        # Gate 3C retained-event families.
        "pitch",
        "plate_appearance",
        # Gate 3C-FD1 open-data analytical families.
        "play_transition",
        "pitch_sequence",
        "player_game_batting",
        "player_game_pitching",
        "player_game_fielding",
        "starter_usage",
        "bullpen_usage",
        "weather",
        "player_prior",
    }
)
# Every count is always re-derived from the Parquet rows.  A family may have a
# legitimate zero only for dimensions that are not part of its semantic grain.
_FAMILY_REQUIRED_COUNTS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "pitch": frozenset({"game_count", "plate_appearance_count", "player_count"}),
        "plate_appearance": frozenset(
            {"game_count", "plate_appearance_count", "player_count"}
        ),
        "play_transition": frozenset(
            {"game_count", "plate_appearance_count", "player_count"}
        ),
        "pitch_sequence": frozenset(
            {"game_count", "plate_appearance_count", "player_count"}
        ),
        "player_game_batting": frozenset({"game_count", "player_count"}),
        "player_game_pitching": frozenset({"game_count", "player_count"}),
        "player_game_fielding": frozenset({"game_count", "player_count"}),
        "starter_usage": frozenset({"game_count", "player_count"}),
        "bullpen_usage": frozenset({"game_count", "player_count"}),
        "weather": frozenset({"game_count"}),
        "player_prior": frozenset({"player_count"}),
    }
)
EVIDENCE_TIERS = frozenset({"A", "B", "C", "EXCLUDED"})
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SAFE_SEGMENT = re.compile(r"[a-z0-9][a-z0-9._-]{0,119}")
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_CONTRIBUTOR_FIELDS = frozenset(
    {
        "logical_uri",
        "file_sha256",
        "payload_checksum",
        "archive_checksum",
        "record_count",
        "byte_count",
        "retrieved_at",
        "source_record_id",
        "source_version_id",
        "checkpoint_id",
    }
)
_SECRET_FIELD_MARKERS = (
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "authorization",
    "cookie",
    "credential",
    "signed_url",
    "headers",
)


class HistoricalParquetStoreError(RuntimeError):
    """Base error for governed event-partition storage."""


class HistoricalParquetCollisionError(HistoricalParquetStoreError):
    """A content-addressed path exists but does not contain the expected bytes."""


class HistoricalParquetIntegrityError(HistoricalParquetStoreError):
    """A staged or existing Parquet file failed read-back validation."""


class HistoricalParquetStorageLimitError(HistoricalParquetStoreError):
    """The configured size cap or free-space reserve would be violated."""


@dataclass(frozen=True, slots=True)
class HistoricalEventPartitionArtifact:
    """Validated metadata suitable for a ``HistoricalEventPartition`` row."""

    source_key: str
    source_version_id: str
    checkpoint_id: str
    event_family: str
    season: int
    month: int
    logical_uri: str
    row_count: int
    game_count: int
    plate_appearance_count: int
    player_count: int
    min_event_date: str | None
    max_event_date: str | None
    min_event_at: str | None
    max_event_at: str | None
    storage_format: str
    compression: str
    schema_version: str
    schema_hash: str
    file_sha256: str
    file_size_bytes: int
    raw_contributor_manifest: tuple[dict[str, Any], ...]
    raw_contributor_checksum: str
    code_revision: str
    evidence_tier: str
    timing_classification: str
    identity_checksum: str
    reused: bool = False

    def orm_values(self) -> dict[str, Any]:
        """Return ORM-ready values, excluding the operational reuse observation."""

        values = asdict(self)
        values.pop("reused")
        values.pop("source_key")
        for field in ("min_event_date", "max_event_date"):
            values[field] = date.fromisoformat(values[field]) if values[field] else None
        for field in ("min_event_at", "max_event_at"):
            values[field] = datetime.fromisoformat(values[field]) if values[field] else None
        values["source_version_id"] = UUID(values["source_version_id"])
        values["checkpoint_id"] = UUID(values["checkpoint_id"])
        values["raw_contributor_manifest"] = list(values["raw_contributor_manifest"])
        return values


class HistoricalEventParquetStore:
    """Atomic, content-addressed, root-confined Zstd Parquet partition store.

    Callers should pass bounded partition-sized frames. Iterables are
    materialized once because the writer must derive and validate identity,
    counts, and schema before it can publish an immutable file.
    """

    def __init__(
        self,
        root: Path,
        *,
        minimum_free_bytes_after_write: int = DEFAULT_MINIMUM_FREE_BYTES_AFTER_WRITE,
        maximum_partition_bytes: int = DEFAULT_MAXIMUM_PARTITION_BYTES,
        maximum_store_bytes: int = DEFAULT_MAXIMUM_STORE_BYTES,
        free_space: Callable[[Path], int] | None = None,
    ) -> None:
        if minimum_free_bytes_after_write < 0:
            raise ValueError("minimum_free_bytes_after_write cannot be negative")
        if maximum_partition_bytes <= 0 or maximum_store_bytes <= 0:
            raise ValueError("Parquet byte caps must be positive")
        lexical_root = Path(root).absolute()
        _assert_no_link_ancestors(lexical_root)
        self.root = lexical_root.resolve()
        self.minimum_free_bytes_after_write = int(minimum_free_bytes_after_write)
        self.maximum_partition_bytes = int(maximum_partition_bytes)
        self.maximum_store_bytes = int(maximum_store_bytes)
        self._free_space = free_space or _free_bytes

    def write_partition(
        self,
        rows: pl.DataFrame | Iterable[Mapping[str, Any]],
        *,
        source_key: str,
        source_version_id: str | UUID,
        checkpoint_id: str | UUID,
        event_family: str,
        season: int,
        month: int,
        schema_version: str,
        raw_contributor_manifest: Sequence[Mapping[str, Any]],
        code_revision: str,
        evidence_tier: str,
        timing_classification: str,
        authorization_grant: AuthorizationGrant,
        raw_contributor_checksum: str | None = None,
        game_count: int | None = None,
        plate_appearance_count: int | None = None,
        player_count: int | None = None,
        min_event_date: date | None = None,
        max_event_date: date | None = None,
        min_event_at: datetime | None = None,
        max_event_at: datetime | None = None,
    ) -> HistoricalEventPartitionArtifact:
        """Validate, atomically publish, read back, and identify one partition."""

        source_segment = _segment(source_key, "source_key")
        source_version = _uuid(source_version_id, "source_version_id")
        checkpoint = _uuid(checkpoint_id, "checkpoint_id")
        if event_family not in EVENT_FAMILIES:
            raise ValueError("event_family is outside the frozen Gen-2 family registry")
        if season < 1876:
            raise ValueError("season is outside governed MLB history")
        if month < 1 or month > 12:
            raise ValueError("month must be between 1 and 12")
        schema_segment = _segment(schema_version, "schema_version")
        if not code_revision or not code_revision.strip():
            raise ValueError("code_revision is required")
        if evidence_tier not in EVIDENCE_TIERS:
            raise ValueError("evidence_tier is invalid")
        if not timing_classification or not timing_classification.strip():
            raise ValueError("timing_classification is required")

        contributors = tuple(_plain_mapping(item) for item in raw_contributor_manifest)
        if not contributors:
            raise ValueError("At least one raw contributor is required")
        contributor_checksum = _canonical_checksum(contributors)
        if raw_contributor_checksum is not None:
            _require_sha256(raw_contributor_checksum, "raw_contributor_checksum")
            if contributor_checksum != raw_contributor_checksum:
                raise HistoricalParquetIntegrityError(
                    "Raw contributor manifest checksum does not match its canonical content"
                )
        for contributor in contributors:
            _validate_contributor(contributor)
        if not isinstance(authorization_grant, AuthorizationGrant):
            raise HistoricalParquetIntegrityError(
                "Parquet publication requires a validated local-processing grant"
            )
        try:
            authorization_grant.verify()
        except (ValueError, PermissionError) as exc:
            raise HistoricalParquetIntegrityError(
                "Parquet authorization grant proof is invalid"
            ) from exc
        if (
            authorization_grant.operation != "LOCAL_DERIVED_PROCESSING"
            or authorization_grant.scope != LOCAL_PRIVATE_RESEARCH
            or authorization_grant.source_key != source_key
            or authorization_grant.evidence_manifest_checksum != contributor_checksum
        ):
            raise HistoricalParquetIntegrityError(
                "Parquet authorization grant does not match the exact source manifest"
            )

        frame = (
            rows.clone()
            if isinstance(rows, pl.DataFrame)
            else pl.from_dicts(list(rows), infer_schema_length=None)
        )
        if frame.height == 0:
            raise ValueError("A historical event partition must contain at least one row")
        if not frame.columns:
            raise ValueError("A historical event partition must have a schema")
        schema_document = _schema_document(frame)
        schema_hash = _canonical_checksum(schema_document)
        inferred = _inferred_metadata(frame)
        required_counts = _FAMILY_REQUIRED_COUNTS[event_family]
        resolved_game_count = _resolve_exact_count(
            "game_count",
            game_count,
            inferred["game_count"],
            required="game_count" in required_counts,
        )
        resolved_pa_count = _resolve_exact_count(
            "plate_appearance_count",
            plate_appearance_count,
            inferred["plate_appearance_count"],
            required="plate_appearance_count" in required_counts,
        )
        resolved_player_count = _resolve_exact_count(
            "player_count",
            player_count,
            inferred["player_count"],
            required="player_count" in required_counts,
        )
        bounds = _resolve_bounds(
            inferred,
            min_event_date=min_event_date,
            max_event_date=max_event_date,
            min_event_at=min_event_at,
            max_event_at=max_event_at,
        )
        _validate_partition_month(bounds, season=season, month=month)

        governance = {
            "source_key": source_key,
            "source_version_id": str(source_version),
            "checkpoint_id": str(checkpoint),
            "event_family": event_family,
            "season": season,
            "month": month,
            "schema_version": schema_version,
            "schema_hash": schema_hash,
            "raw_contributor_checksum": contributor_checksum,
            "code_revision": code_revision,
            "evidence_tier": evidence_tier,
            "timing_classification": timing_classification,
        }
        governance_hash = _canonical_checksum(governance)
        directory = self._contained(
            self.root
            / "historical-events"
            / f"source={source_segment}"
            / f"family={event_family}"
            / f"season={season:04d}"
            / f"month={month:02d}"
            / f"schema={schema_segment}"
            / f"source-version={source_version}"
            / f"checkpoint={checkpoint}"
            / f"governance={governance_hash}"
        )
        estimated = max(int(frame.estimated_size()), 1)
        self._guard_storage(estimated, permanent_additional_bytes=0)
        self.root.mkdir(parents=True, exist_ok=True)
        _assert_no_links(self.root)
        directory.mkdir(parents=True, exist_ok=True)
        _assert_no_links_between(self.root, directory)
        temporary = directory / f".parquet-write-{uuid4().hex}.tmp"
        try:
            frame.write_parquet(temporary, compression="zstd", statistics=True)
            _sync_file(temporary)
            actual_size = temporary.stat().st_size
            file_sha256 = _file_sha256(temporary)
            self._validate_file(
                temporary,
                expected_sha256=file_sha256,
                expected_schema=schema_document,
                expected_rows=frame.height,
            )
            final = self._contained(directory / f"part-{file_sha256}.parquet")
            target_exists = final.exists() or final.is_symlink()
            self._guard_storage(
                actual_size,
                staged_bytes=actual_size,
                permanent_additional_bytes=0 if target_exists else actual_size,
            )
            reused = False
            if target_exists:
                self._validate_existing(
                    final,
                    expected_sha256=file_sha256,
                    expected_schema=schema_document,
                    expected_rows=frame.height,
                )
                reused = True
            else:
                try:
                    # A hard-link publishes complete staged bytes atomically and
                    # fails if another writer already won this exact identity.
                    os.link(temporary, final)
                except FileExistsError:
                    self._validate_existing(
                        final,
                        expected_sha256=file_sha256,
                        expected_schema=schema_document,
                        expected_rows=frame.height,
                    )
                    reused = True
                self._validate_existing(
                    final,
                    expected_sha256=file_sha256,
                    expected_schema=schema_document,
                    expected_rows=frame.height,
                )
            logical_uri = final.relative_to(self.root).as_posix()
            semantic = {
                **governance,
                "logical_uri": logical_uri,
                "row_count": frame.height,
                "game_count": resolved_game_count,
                "plate_appearance_count": resolved_pa_count,
                "player_count": resolved_player_count,
                **bounds,
                "storage_format": "PARQUET",
                "compression": "ZSTD",
                "file_sha256": file_sha256,
                "file_size_bytes": final.stat().st_size,
                "raw_contributor_manifest": contributors,
            }
            identity_checksum = _canonical_checksum(semantic)
            return HistoricalEventPartitionArtifact(
                identity_checksum=identity_checksum,
                reused=reused,
                **semantic,
            )
        finally:
            if temporary.exists() and not temporary.is_symlink():
                temporary.unlink()

    # ``write`` is a concise compatibility spelling; semantics remain identical.
    write = write_partition

    def resolve(self, logical_uri: str) -> Path:
        """Resolve a logical URI only when it remains inside the governed root."""

        if not isinstance(logical_uri, str) or "\\" in logical_uri:
            raise HistoricalParquetIntegrityError("Parquet logical URI is invalid")
        pure = PurePosixPath(logical_uri)
        if (
            pure.is_absolute()
            or not pure.parts
            or pure.parts[0] != "historical-events"
            or any(part in {"", ".", ".."} for part in pure.parts)
            or pure.suffix != ".parquet"
        ):
            raise HistoricalParquetIntegrityError("Parquet logical URI is not root-confined")
        path = self._contained(self.root.joinpath(*pure.parts))
        _assert_no_links_between(self.root, path)
        return path

    def validate(self, artifact: HistoricalEventPartitionArtifact) -> Path:
        """Revalidate a previously returned artifact before a local read."""

        path = self.resolve(artifact.logical_uri)
        if path.name != f"part-{artifact.file_sha256}.parquet":
            raise HistoricalParquetIntegrityError(
                "Parquet logical URI is not addressed by its file checksum"
            )
        expected_parts = {
            f"source={_segment(artifact.source_key, 'source_key')}",
            f"family={artifact.event_family}",
            f"season={artifact.season:04d}",
            f"month={artifact.month:02d}",
            f"schema={_segment(artifact.schema_version, 'schema_version')}",
            f"source-version={_uuid(artifact.source_version_id, 'source_version_id')}",
            f"checkpoint={_uuid(artifact.checkpoint_id, 'checkpoint_id')}",
        }
        if not expected_parts <= set(PurePosixPath(artifact.logical_uri).parts):
            raise HistoricalParquetIntegrityError(
                "Parquet logical URI does not match partition metadata"
            )
        if (
            _canonical_checksum(artifact.raw_contributor_manifest)
            != artifact.raw_contributor_checksum
        ):
            raise HistoricalParquetIntegrityError(
                "Parquet raw contributor manifest checksum mismatch"
            )
        frame = pl.read_parquet(path)
        actual_schema = _schema_document(frame)
        if _canonical_checksum(actual_schema) != artifact.schema_hash:
            raise HistoricalParquetIntegrityError("Parquet schema hash changed")
        self._validate_file(
            path,
            expected_sha256=artifact.file_sha256,
            expected_schema=actual_schema,
            expected_rows=artifact.row_count,
        )
        if path.stat().st_size != artifact.file_size_bytes:
            raise HistoricalParquetIntegrityError("Parquet file size changed")
        inferred = _inferred_metadata(frame)
        count_fields = {
            "game_count": artifact.game_count,
            "plate_appearance_count": artifact.plate_appearance_count,
            "player_count": artifact.player_count,
        }
        for name, expected in count_fields.items():
            if inferred[name] != expected:
                raise HistoricalParquetIntegrityError(
                    f"Parquet read-back {name} does not match partition metadata"
                )
        for name in ("min_event_date", "max_event_date", "min_event_at", "max_event_at"):
            if inferred[name] != getattr(artifact, name):
                raise HistoricalParquetIntegrityError(
                    f"Parquet read-back {name} does not match partition metadata"
                )
        _validate_partition_month(inferred, season=artifact.season, month=artifact.month)
        semantic = asdict(artifact)
        semantic.pop("identity_checksum")
        semantic.pop("reused")
        if _canonical_checksum(semantic) != artifact.identity_checksum:
            raise HistoricalParquetIntegrityError("Parquet partition identity mismatch")
        return path

    def _validate_existing(
        self,
        path: Path,
        *,
        expected_sha256: str,
        expected_schema: tuple[dict[str, str], ...],
        expected_rows: int,
    ) -> None:
        if _is_link_or_reparse(path):
            raise HistoricalParquetCollisionError(
                "Content-addressed Parquet target is a link or reparse point"
            )
        try:
            self._validate_file(
                path,
                expected_sha256=expected_sha256,
                expected_schema=expected_schema,
                expected_rows=expected_rows,
            )
        except HistoricalParquetIntegrityError as exc:
            raise HistoricalParquetCollisionError(
                "Content-addressed Parquet path exists with conflicting evidence"
            ) from exc

    @staticmethod
    def _validate_file(
        path: Path,
        *,
        expected_sha256: str,
        expected_schema: tuple[dict[str, str], ...],
        expected_rows: int,
    ) -> None:
        try:
            actual_sha256 = _file_sha256(path)
            read_back = pl.read_parquet(path)
        except (OSError, pl.exceptions.PolarsError) as exc:
            raise HistoricalParquetIntegrityError("Parquet read-back failed") from exc
        if actual_sha256 != expected_sha256:
            raise HistoricalParquetIntegrityError("Parquet file checksum mismatch")
        if read_back.height != expected_rows:
            raise HistoricalParquetIntegrityError("Parquet read-back row count mismatch")
        if _schema_document(read_back) != expected_schema:
            raise HistoricalParquetIntegrityError("Parquet read-back schema mismatch")

    def _contained(self, path: Path) -> Path:
        lexical = path.absolute()
        _assert_no_links_between(self.root, lexical)
        resolved = lexical.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise HistoricalParquetIntegrityError("Parquet path escapes the governed root")
        return resolved

    def _guard_storage(
        self,
        projected_bytes: int,
        *,
        staged_bytes: int = 0,
        permanent_additional_bytes: int | None = None,
    ) -> None:
        if projected_bytes <= 0:
            raise HistoricalParquetStorageLimitError("Projected Parquet bytes must be positive")
        if projected_bytes > self.maximum_partition_bytes:
            raise HistoricalParquetStorageLimitError(
                "Parquet partition exceeds the configured 100-GiB default cap"
            )
        current_bytes = _tree_file_bytes(self.root)
        permanent_current = max(current_bytes - staged_bytes, 0)
        permanent_additional = (
            projected_bytes
            if permanent_additional_bytes is None
            else permanent_additional_bytes
        )
        if permanent_additional < 0:
            raise HistoricalParquetStorageLimitError(
                "Permanent Parquet byte projection cannot be negative"
            )
        if permanent_current + permanent_additional > self.maximum_store_bytes:
            raise HistoricalParquetStorageLimitError(
                "Parquet store exceeds the configured 100-GiB default cap"
            )
        probe = self.root
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        try:
            available = int(self._free_space(probe))
        except OSError as exc:
            raise HistoricalParquetStorageLimitError(
                "Unable to verify free storage for the Parquet write"
            ) from exc
        additional_free_space = 0 if staged_bytes else projected_bytes
        if available < additional_free_space + self.minimum_free_bytes_after_write:
            raise HistoricalParquetStorageLimitError(
                "Parquet write would violate the configured 100-GiB free-space reserve"
            )


def _inferred_metadata(frame: pl.DataFrame) -> dict[str, Any]:
    games = _unique_strings(
        frame,
        ("canonical_game_id", "game_id", "mlb_game_id", "game_pk"),
    )
    plate_appearances = _unique_strings(
        frame,
        (
            "canonical_plate_appearance_id",
            "plate_appearance_id",
            "pa_id",
            "at_bat_id",
        ),
    )
    players: set[str] = set()
    for field in (
        "canonical_player_id",
        "canonical_batter_id",
        "canonical_pitcher_id",
        "player_id",
        "batter_id",
        "pitcher_id",
    ):
        if field in frame.columns:
            players.update(_column_strings(frame, field))
    timestamps: list[datetime] = []
    dates: list[date] = []
    for field in (
        "event_timestamp",
        "event_at",
        "pitch_timestamp",
        "game_date",
        "official_date",
        "event_date",
    ):
        if field not in frame.columns:
            continue
        is_timestamp_field = field in {"event_timestamp", "event_at", "pitch_timestamp"}
        for value in frame.get_column(field).to_list():
            parsed_date, parsed_at = _parse_temporal(
                value,
                require_timestamp=is_timestamp_field,
                field=field,
            )
            if not is_timestamp_field and parsed_date is not None:
                dates.append(parsed_date)
            if is_timestamp_field and parsed_at is not None:
                timestamps.append(parsed_at)
    if not dates and timestamps:
        # UTC event dates are an explicit fallback only when no governed
        # official/local event-date column exists in the partition schema.
        dates.extend(value.date() for value in timestamps)
    return {
        "game_count": len(games),
        "plate_appearance_count": len(plate_appearances),
        "player_count": len(players),
        "min_event_date": min(dates).isoformat() if dates else None,
        "max_event_date": max(dates).isoformat() if dates else None,
        "min_event_at": min(timestamps).isoformat() if timestamps else None,
        "max_event_at": max(timestamps).isoformat() if timestamps else None,
    }


def _resolve_bounds(
    inferred: Mapping[str, Any],
    *,
    min_event_date: date | None,
    max_event_date: date | None,
    min_event_at: datetime | None,
    max_event_at: datetime | None,
) -> dict[str, str | None]:
    supplied_dates = (min_event_date, max_event_date)
    supplied_times = (min_event_at, max_event_at)
    if (supplied_dates[0] is None) != (supplied_dates[1] is None):
        raise ValueError("Event date bounds must both be present or both be null")
    if (supplied_times[0] is None) != (supplied_times[1] is None):
        raise ValueError("Event timestamp bounds must both be present or both be null")
    if min_event_date is not None and inferred["min_event_date"] is not None:
        if (
            min_event_date.isoformat() != inferred["min_event_date"]
            or max_event_date.isoformat() != inferred["max_event_date"]
        ):
            raise HistoricalParquetIntegrityError(
                "Supplied event date bounds do not match partition rows"
            )
    if min_event_at is not None and inferred["min_event_at"] is not None:
        if (
            _iso_utc(min_event_at) != inferred["min_event_at"]
            or _iso_utc(max_event_at) != inferred["max_event_at"]
        ):
            raise HistoricalParquetIntegrityError(
                "Supplied event timestamp bounds do not match partition rows"
            )
    date_min = min_event_date.isoformat() if min_event_date else inferred["min_event_date"]
    date_max = max_event_date.isoformat() if max_event_date else inferred["max_event_date"]
    time_min = _iso_utc(min_event_at) if min_event_at else inferred["min_event_at"]
    time_max = _iso_utc(max_event_at) if max_event_at else inferred["max_event_at"]
    if date_min and date_max and date_max < date_min:
        raise ValueError("Event date bounds are reversed")
    if time_min and time_max and datetime.fromisoformat(time_max) < datetime.fromisoformat(time_min):
        raise ValueError("Event timestamp bounds are reversed")
    return {
        "min_event_date": date_min,
        "max_event_date": date_max,
        "min_event_at": time_min,
        "max_event_at": time_max,
    }


def _validate_partition_month(bounds: Mapping[str, str | None], *, season: int, month: int) -> None:
    if bounds["min_event_date"] is not None:
        for key in ("min_event_date", "max_event_date"):
            value = date.fromisoformat(str(bounds[key]))
            if value.year != season or value.month != month:
                raise ValueError("Event date lies outside the declared season/month partition")
        return
    for key in ("min_event_at", "max_event_at"):
        if bounds[key]:
            value = datetime.fromisoformat(str(bounds[key]))
            if value.year != season or value.month != month:
                raise ValueError("Event timestamp lies outside the declared season/month partition")


def _resolve_exact_count(
    name: str,
    supplied: int | None,
    inferred: int,
    *,
    required: bool,
) -> int:
    if isinstance(supplied, bool):
        raise ValueError(f"{name} must be an integer")
    if inferred < 0:
        raise HistoricalParquetIntegrityError(f"{name} cannot be negative")
    if required and inferred <= 0:
        raise HistoricalParquetIntegrityError(
            f"{name} cannot be derived from the governed partition schema"
        )
    value = inferred if supplied is None else int(supplied)
    if value < 0 or (required and value <= 0):
        qualifier = "positive" if required else "nonnegative"
        raise ValueError(f"{name} must be {qualifier}")
    if supplied is not None and value != inferred:
        raise HistoricalParquetIntegrityError(f"Supplied {name} does not match partition rows")
    return value


def _schema_document(frame: pl.DataFrame) -> tuple[dict[str, str], ...]:
    return tuple(
        {"name": name, "dtype": str(dtype)}
        for name, dtype in frame.schema.items()
    )


def _unique_strings(frame: pl.DataFrame, candidates: Sequence[str]) -> set[str]:
    result: set[str] = set()
    for field in candidates:
        if field in frame.columns:
            result.update(_column_strings(frame, field))
            break
    return result


def _column_strings(frame: pl.DataFrame, field: str) -> set[str]:
    return {str(value) for value in frame.get_column(field).to_list() if value is not None}


def _parse_temporal(
    value: object,
    *,
    require_timestamp: bool,
    field: str,
) -> tuple[date | None, datetime | None]:
    if value is None:
        return None, None
    if isinstance(value, datetime):
        if require_timestamp and (value.tzinfo is None or value.utcoffset() is None):
            raise HistoricalParquetIntegrityError(
                f"{field} contains a timezone-naive event timestamp"
            )
        aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return aware.date(), aware
    if isinstance(value, date):
        if require_timestamp:
            raise HistoricalParquetIntegrityError(
                f"{field} requires an explicit timezone-aware event timestamp"
            )
        return value, None
    if isinstance(value, str):
        normalized = value.strip().replace("Z", "+00:00")
        try:
            parsed_at = datetime.fromisoformat(normalized)
        except ValueError:
            try:
                return date.fromisoformat(normalized), None
            except ValueError:
                raise HistoricalParquetIntegrityError(
                    f"{field} contains an invalid temporal value"
                )
        if require_timestamp and (parsed_at.tzinfo is None or parsed_at.utcoffset() is None):
            raise HistoricalParquetIntegrityError(
                f"{field} contains a timezone-naive event timestamp"
            )
        aware = (
            parsed_at.replace(tzinfo=UTC)
            if parsed_at.tzinfo is None
            else parsed_at.astimezone(UTC)
        )
        return aware.date(), aware
    raise HistoricalParquetIntegrityError(f"{field} contains an unsupported temporal value")


def _plain_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("Raw contributor entries must be mappings")
    return json.loads(
        json.dumps(dict(value), sort_keys=True, separators=(",", ":"), default=_json_default)
    )


def _validate_contributor(value: Mapping[str, Any]) -> None:
    keys = set(value)
    if any(
        marker in str(key).lower()
        for key in keys
        for marker in _SECRET_FIELD_MARKERS
    ):
        raise HistoricalParquetIntegrityError(
            "Raw contributor metadata contains a secret-bearing field"
        )
    unexpected = keys - _CONTRIBUTOR_FIELDS
    if unexpected:
        raise HistoricalParquetIntegrityError(
            "Raw contributor metadata contains fields outside the frozen schema"
        )
    if "logical_uri" not in value or "file_sha256" not in value:
        raise HistoricalParquetIntegrityError(
            "Raw contributor metadata requires logical_uri and file_sha256"
        )
    logical_uri = value["logical_uri"]
    if not isinstance(logical_uri, str) or "\\" in logical_uri:
        raise HistoricalParquetIntegrityError("Raw contributor logical_uri is invalid")
    pure = PurePosixPath(logical_uri)
    if (
        pure.is_absolute()
        or not pure.parts
        or ":" in pure.parts[0]
        or any(part in {"", ".", ".."} for part in pure.parts)
        or "://" in logical_uri
    ):
        raise HistoricalParquetIntegrityError(
            "Raw contributor logical_uri must be root-relative and non-secret"
        )
    _require_sha256(str(value["file_sha256"]), "raw contributor file_sha256")
    for key in ("payload_checksum", "archive_checksum"):
        if value.get(key) is not None:
            _require_sha256(str(value[key]), f"raw contributor {key}")
    for key in ("record_count", "byte_count"):
        if value.get(key) is not None and (
            isinstance(value[key], bool)
            or not isinstance(value[key], int)
            or value[key] < 0
        ):
            raise HistoricalParquetIntegrityError(
                f"Raw contributor {key} must be a nonnegative integer"
            )
    if value.get("retrieved_at") is not None:
        try:
            retrieved = datetime.fromisoformat(str(value["retrieved_at"]).replace("Z", "+00:00"))
        except ValueError as exc:
            raise HistoricalParquetIntegrityError(
                "Raw contributor retrieved_at is invalid"
            ) from exc
        if retrieved.tzinfo is None or retrieved.utcoffset() is None:
            raise HistoricalParquetIntegrityError(
                "Raw contributor retrieved_at must be timezone-aware"
            )


def _canonical_checksum(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _segment(value: str, label: str) -> str:
    normalized = str(value).strip().lower().replace(" ", "-")
    if not _SAFE_SEGMENT.fullmatch(normalized) or ".." in normalized:
        raise ValueError(f"{label} is not a safe deterministic path segment")
    return normalized


def _uuid(value: str | UUID, label: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a UUID") from exc


def _require_sha256(value: str, label: str) -> str:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("Event timestamp bounds must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return _iso_utc(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return str(value)


def _sync_file(path: Path) -> None:
    with path.open("r+b") as stream:
        stream.flush()
        os.fsync(stream.fileno())


def _free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def _tree_file_bytes(root: Path) -> int:
    if not root.exists():
        return 0
    _assert_no_links(root)
    total = 0
    for directory, names, files in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        _assert_no_links(directory_path)
        for name in names:
            _assert_no_links(directory_path / name)
        for name in files:
            path = directory_path / name
            _assert_no_links(path)
            total += path.stat().st_size
    return total


def _assert_no_links_between(root: Path, target: Path) -> None:
    root_absolute = root.absolute()
    target_absolute = target.absolute()
    try:
        relative = target_absolute.relative_to(root_absolute)
    except ValueError as exc:
        raise HistoricalParquetIntegrityError("Parquet path is outside the governed root") from exc
    _assert_no_links(root_absolute)
    current = root_absolute
    for part in relative.parts:
        current = current / part
        if current.exists() or current.is_symlink():
            _assert_no_links(current)


def _assert_no_links(path: Path) -> None:
    if (path.exists() or path.is_symlink()) and _is_link_or_reparse(path):
        raise HistoricalParquetIntegrityError(
            f"Governed Parquet paths cannot use links or reparse points: {path.name}"
        )


def _assert_no_link_ancestors(path: Path) -> None:
    for candidate in (*reversed(path.parents), path):
        if candidate.exists() or candidate.is_symlink():
            _assert_no_links(candidate)


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = int(getattr(path.lstat(), "st_file_attributes", 0))
    except OSError:
        return False
    return bool(attributes & _REPARSE_POINT)


# Explicit aliases make the store easy to discover without duplicating behavior.
Gen2ParquetStore = HistoricalEventParquetStore
HistoricalParquetStore = HistoricalEventParquetStore
ParquetPartitionArtifact = HistoricalEventPartitionArtifact


__all__ = [
    "DEFAULT_MAXIMUM_PARTITION_BYTES",
    "DEFAULT_MAXIMUM_STORE_BYTES",
    "DEFAULT_MINIMUM_FREE_BYTES_AFTER_WRITE",
    "EVENT_FAMILIES",
    "Gen2ParquetStore",
    "HistoricalEventParquetStore",
    "HistoricalEventPartitionArtifact",
    "HistoricalParquetCollisionError",
    "HistoricalParquetIntegrityError",
    "HistoricalParquetStorageLimitError",
    "HistoricalParquetStore",
    "HistoricalParquetStoreError",
    "ParquetPartitionArtifact",
]

"""Exact, provider-free identity coverage for the active Retrosheet era.

The audit consumes already-verified :mod:`fd1_identity` crosswalk output and
the five Retrosheet tables that carry player identifiers.  It performs no
network, database, or filesystem writes.  Every source file must be pinned by
both byte length and SHA-256, and the file is hashed while it is streamed.

One populated player-ID cell is one reference.  Distinct Retrosheet IDs are
the unique-player denominator.  Game-table rows are limited to regular-season
games; ``allplayers.csv`` is retained as season-roster evidence because it has
no game-type column.  Names are never read and never participate in a link.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
import csv
from dataclasses import asdict, dataclass, field
from enum import Enum
from hashlib import sha256
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any

from app.gen2.fd1_identity import (
    GEN2_PRIMARY_POPULATION,
    GEN2_PRIOR_ONLY_POPULATION,
    PRIMARY_SEASONS,
    PRIOR_ONLY_SEASONS,
    PersonCrosswalkReport,
    ResolutionStatus,
    SourceFileEvidence,
    SourceFileSpec,
)
from app.gen2.fd1_materializer import RETROSHEET_SOURCE_KEY
from app.gen2.retrosheet import RETROSHEET_CSV_HEADERS


FD1_IDENTITY_COVERAGE_SCHEMA_VERSION = "gate3c_fd1_active_identity_coverage_v1"
ACTIVE_SEASONS = tuple(sorted(PRIOR_ONLY_SEASONS | PRIMARY_SEASONS))
PRIMARY_IDENTITY_THRESHOLD = 0.995

_REQUIRED_TABLES = (
    "allplayers.csv",
    "batting.csv",
    "pitching.csv",
    "fielding.csv",
    "plays.csv",
)
_REGULAR_SEASON_TOKENS = frozenset({"regular", "r", "reg"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

_PLAYER_COLUMNS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "allplayers.csv": ("id",),
        "batting.csv": ("id",),
        "pitching.csv": ("id",),
        "fielding.csv": ("id",),
        "plays.csv": (
            "batter",
            "pitcher",
            *(f"br{base}_{when}" for when in ("pre", "post") for base in range(1, 4)),
            *(f"lob_id{base}" for base in range(1, 4)),
            *(f"pr{base}_{when}" for when in ("pre", "post") for base in range(1, 4)),
            *(f"l{slot}" for slot in range(1, 10)),
            *(f"f{position}" for position in range(2, 10)),
        ),
    }
)


class FD1IdentityCoverageError(ValueError):
    """The active-era identity audit could not prove its result exactly."""


class IdentityNamespace(str, Enum):
    RETROSHEET = "RETROSHEET"
    CHADWICK_PERSON = "CHADWICK_PERSON"
    MLBAM = "MLBAM"
    LAHMAN = "LAHMAN"


class IdentityRelation(str, Enum):
    RETROSHEET_TO_CHADWICK = "RETROSHEET_TO_CHADWICK"
    RETROSHEET_TO_MLBAM = "RETROSHEET_TO_MLBAM"
    LAHMAN_TO_CHADWICK = "LAHMAN_TO_CHADWICK"


@dataclass(frozen=True, slots=True)
class IdentityCoverageLimits:
    """Hard ceilings keep a malformed local source from exhausting memory."""

    max_file_bytes: int = 16 * 1024**3
    max_csv_record_bytes: int = 4 * 1024**2
    max_rows_per_file: int = 50_000_000
    max_active_references: int = 250_000_000
    max_player_season_identities: int = 100_000
    max_player_role_game_links: int = 5_000_000

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise FD1IdentityCoverageError(f"{name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class RetrosheetIdentitySourceEvidence:
    logical_table: str
    source_key: str
    source_release: str
    content_sha256: str
    content_length: int
    rows_scanned: int
    active_references: int

    def __post_init__(self) -> None:
        if self.logical_table not in _REQUIRED_TABLES:
            raise FD1IdentityCoverageError("unexpected identity source table")
        if self.source_key != RETROSHEET_SOURCE_KEY:
            raise FD1IdentityCoverageError("identity source key is not Retrosheet CSV")
        if not _SHA256.fullmatch(self.content_sha256):
            raise FD1IdentityCoverageError("identity source SHA-256 is invalid")
        if self.content_length <= 0 or self.rows_scanned < 0 or self.active_references < 0:
            raise FD1IdentityCoverageError("identity source counts are invalid")


@dataclass(frozen=True, slots=True)
class NamespaceCoverage:
    namespace: IdentityNamespace
    unique_player_count: int
    exact_unique_player_count: int
    conflict_unique_player_count: int
    unresolved_unique_player_count: int
    reference_count: int
    exact_reference_count: int
    conflict_reference_count: int
    unresolved_reference_count: int

    def __post_init__(self) -> None:
        values = tuple(
            getattr(self, name)
            for name in self.__dataclass_fields__
            if name != "namespace"
        )
        if any(value < 0 for value in values):
            raise FD1IdentityCoverageError("namespace coverage cannot be negative")
        if self.unique_player_count != (
            self.exact_unique_player_count
            + self.conflict_unique_player_count
            + self.unresolved_unique_player_count
        ):
            raise FD1IdentityCoverageError("unique namespace statuses do not reconcile")
        if self.reference_count != (
            self.exact_reference_count
            + self.conflict_reference_count
            + self.unresolved_reference_count
        ):
            raise FD1IdentityCoverageError("reference namespace statuses do not reconcile")

    @property
    def exact_unique_rate(self) -> float:
        return (
            self.exact_unique_player_count / self.unique_player_count
            if self.unique_player_count
            else 0.0
        )

    @property
    def exact_reference_rate(self) -> float:
        return (
            self.exact_reference_count / self.reference_count
            if self.reference_count
            else 0.0
        )


@dataclass(frozen=True, slots=True)
class ExactLinkRate:
    relation: IdentityRelation
    source_unique_id_count: int
    exact_unique_link_count: int
    conflict_unique_link_count: int
    unresolved_unique_link_count: int
    source_reference_count: int
    exact_reference_count: int
    conflict_reference_count: int
    unresolved_reference_count: int

    def __post_init__(self) -> None:
        values = tuple(
            getattr(self, name)
            for name in self.__dataclass_fields__
            if name != "relation"
        )
        if any(value < 0 for value in values):
            raise FD1IdentityCoverageError("link-rate counts cannot be negative")
        if self.source_unique_id_count != (
            self.exact_unique_link_count
            + self.conflict_unique_link_count
            + self.unresolved_unique_link_count
        ):
            raise FD1IdentityCoverageError("unique link statuses do not reconcile")
        if self.source_reference_count != (
            self.exact_reference_count
            + self.conflict_reference_count
            + self.unresolved_reference_count
        ):
            raise FD1IdentityCoverageError("reference link statuses do not reconcile")

    @property
    def exact_unique_rate(self) -> float:
        return (
            self.exact_unique_link_count / self.source_unique_id_count
            if self.source_unique_id_count
            else 0.0
        )

    @property
    def exact_reference_rate(self) -> float:
        return (
            self.exact_reference_count / self.source_reference_count
            if self.source_reference_count
            else 0.0
        )


@dataclass(frozen=True, slots=True)
class IdentityCoverageSlice:
    label: str
    population: str
    seasons: tuple[int, ...]
    unique_retrosheet_players: int
    reference_count: int
    namespace_coverage: tuple[NamespaceCoverage, ...]
    exact_link_rates: tuple[ExactLinkRate, ...]

    def __post_init__(self) -> None:
        if not self.label or self.label != self.label.strip():
            raise FD1IdentityCoverageError("coverage-slice label is invalid")
        if not self.seasons or tuple(sorted(set(self.seasons))) != self.seasons:
            raise FD1IdentityCoverageError("coverage-slice seasons are invalid")
        if not set(self.seasons).issubset(ACTIVE_SEASONS):
            raise FD1IdentityCoverageError("coverage slice is outside the active era")
        if self.unique_retrosheet_players < 0 or self.reference_count < 0:
            raise FD1IdentityCoverageError("coverage-slice counts cannot be negative")
        expected_namespaces = tuple(IdentityNamespace)
        if tuple(row.namespace for row in self.namespace_coverage) != expected_namespaces:
            raise FD1IdentityCoverageError("coverage-slice namespaces are incomplete")
        expected_relations = tuple(IdentityRelation)
        if tuple(row.relation for row in self.exact_link_rates) != expected_relations:
            raise FD1IdentityCoverageError("coverage-slice link rates are incomplete")
        if any(
            row.unique_player_count != self.unique_retrosheet_players
            or row.reference_count != self.reference_count
            for row in self.namespace_coverage
        ):
            raise FD1IdentityCoverageError("namespace denominators cross a coverage slice")

    def namespace(self, namespace: IdentityNamespace) -> NamespaceCoverage:
        return next(row for row in self.namespace_coverage if row.namespace is namespace)

    def relation(self, relation: IdentityRelation) -> ExactLinkRate:
        return next(row for row in self.exact_link_rates if row.relation is relation)


@dataclass(frozen=True, slots=True)
class AffectedRole:
    role: str
    reference_count: int
    affected_game_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.role or self.role != self.role.strip():
            raise FD1IdentityCoverageError("affected role is invalid")
        if self.reference_count <= 0:
            raise FD1IdentityCoverageError("affected role requires references")
        if tuple(sorted(set(self.affected_game_ids))) != self.affected_game_ids:
            raise FD1IdentityCoverageError("affected game IDs must be deterministic")


@dataclass(frozen=True, slots=True)
class ActiveIdentityIssue:
    retrosheet_id: str
    seasons: tuple[int, ...]
    reference_count: int
    retrosheet_to_chadwick_status: ResolutionStatus
    chadwick_person_status: ResolutionStatus
    mlbam_status: ResolutionStatus
    lahman_status: ResolutionStatus
    canonical_chadwick_uuid: str | None
    chadwick_person_id: str | None
    mlbam_id: str | None
    lahman_player_id: str | None
    roles: tuple[AffectedRole, ...]

    def __post_init__(self) -> None:
        _identifier(self.retrosheet_id, "active retrosheet_id")
        if tuple(sorted(set(self.seasons))) != self.seasons or not self.seasons:
            raise FD1IdentityCoverageError("active issue seasons are invalid")
        if self.reference_count <= 0 or not self.roles:
            raise FD1IdentityCoverageError("active issue lacks source references")
        if sum(role.reference_count for role in self.roles) != self.reference_count:
            raise FD1IdentityCoverageError("active issue role counts do not reconcile")
        if all(
            status is ResolutionStatus.EXACT
            for status in (
                self.retrosheet_to_chadwick_status,
                self.chadwick_person_status,
                self.mlbam_status,
                self.lahman_status,
            )
        ):
            raise FD1IdentityCoverageError("an active issue must contain a non-exact link")


@dataclass(frozen=True, slots=True)
class ActiveIdentityCoverageReport:
    schema_version: str
    source_release: str
    source_files: tuple[RetrosheetIdentitySourceEvidence, ...]
    crosswalk_source_files: tuple[SourceFileEvidence, ...]
    season_coverage: tuple[IdentityCoverageSlice, ...]
    population_coverage: tuple[IdentityCoverageSlice, ...]
    overall_coverage: IdentityCoverageSlice
    affected_players: tuple[ActiveIdentityIssue, ...]
    primary_threshold: float
    primary_unique_meets_threshold: bool
    primary_reference_meets_threshold: bool
    primary_meets_threshold: bool
    name_based_match_count: int
    derivation_checksum: str

    def __post_init__(self) -> None:
        if self.schema_version != FD1_IDENTITY_COVERAGE_SCHEMA_VERSION:
            raise FD1IdentityCoverageError("identity coverage schema version is invalid")
        if tuple(row.logical_table for row in self.source_files) != _REQUIRED_TABLES:
            raise FD1IdentityCoverageError("identity source evidence is incomplete")
        if tuple(row.seasons for row in self.season_coverage) != tuple(
            (season,) for season in ACTIVE_SEASONS
        ):
            raise FD1IdentityCoverageError("season identity coverage is incomplete")
        if tuple(row.population for row in self.population_coverage) != (
            GEN2_PRIOR_ONLY_POPULATION,
            GEN2_PRIMARY_POPULATION,
        ):
            raise FD1IdentityCoverageError("population identity coverage is incomplete")
        if self.overall_coverage.seasons != ACTIVE_SEASONS:
            raise FD1IdentityCoverageError("overall coverage is outside the active era")
        if not 0.0 < self.primary_threshold <= 1.0:
            raise FD1IdentityCoverageError("primary identity threshold is invalid")
        if self.primary_meets_threshold != (
            self.primary_unique_meets_threshold
            and self.primary_reference_meets_threshold
        ):
            raise FD1IdentityCoverageError("primary threshold decisions do not reconcile")
        if self.name_based_match_count != 0:
            raise FD1IdentityCoverageError("name-based identity matching is prohibited")
        if tuple(sorted(self.affected_players, key=lambda row: row.retrosheet_id)) != (
            self.affected_players
        ):
            raise FD1IdentityCoverageError("active identity issues are not deterministic")
        if not _SHA256.fullmatch(self.derivation_checksum):
            raise FD1IdentityCoverageError("identity coverage checksum is invalid")

    @property
    def primary(self) -> IdentityCoverageSlice:
        return next(
            row
            for row in self.population_coverage
            if row.population == GEN2_PRIMARY_POPULATION
        )

    @property
    def exact_retrosheet_to_chadwick_rate(self) -> float:
        return self.overall_coverage.relation(
            IdentityRelation.RETROSHEET_TO_CHADWICK
        ).exact_reference_rate

    @property
    def exact_retrosheet_to_mlbam_rate(self) -> float:
        return self.overall_coverage.relation(
            IdentityRelation.RETROSHEET_TO_MLBAM
        ).exact_reference_rate

    @property
    def exact_lahman_to_chadwick_rate(self) -> float:
        return self.overall_coverage.relation(
            IdentityRelation.LAHMAN_TO_CHADWICK
        ).exact_reference_rate

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-ready report with explicit computed rate fields."""

        payload = json.loads(json.dumps(asdict(self)))

        def add_rates(raw_slice: dict[str, Any], source: IdentityCoverageSlice) -> None:
            for raw, row in zip(
                raw_slice["namespace_coverage"],
                source.namespace_coverage,
                strict=True,
            ):
                raw["exact_unique_rate"] = row.exact_unique_rate
                raw["exact_reference_rate"] = row.exact_reference_rate
            for raw, row in zip(
                raw_slice["exact_link_rates"],
                source.exact_link_rates,
                strict=True,
            ):
                raw["exact_unique_rate"] = row.exact_unique_rate
                raw["exact_reference_rate"] = row.exact_reference_rate

        for raw, row in zip(
            payload["season_coverage"], self.season_coverage, strict=True
        ):
            add_rates(raw, row)
        for raw, row in zip(
            payload["population_coverage"], self.population_coverage, strict=True
        ):
            add_rates(raw, row)
        add_rates(payload["overall_coverage"], self.overall_coverage)
        payload["exact_reference_rates"] = {
            IdentityRelation.RETROSHEET_TO_CHADWICK.value: (
                self.exact_retrosheet_to_chadwick_rate
            ),
            IdentityRelation.RETROSHEET_TO_MLBAM.value: (
                self.exact_retrosheet_to_mlbam_rate
            ),
            IdentityRelation.LAHMAN_TO_CHADWICK.value: (
                self.exact_lahman_to_chadwick_rate
            ),
        }
        return payload


@dataclass(slots=True)
class _ReferenceAccumulator:
    reference_count: int = 0
    roles: Counter[str] = field(default_factory=Counter)
    games_by_role: dict[str, set[str]] = field(
        default_factory=lambda: defaultdict(set)
    )


@dataclass(frozen=True, slots=True)
class _PlayerResolution:
    retrosheet_to_chadwick: ResolutionStatus
    canonical_uuid: str | None
    chadwick_person: ResolutionStatus
    chadwick_person_id: str | None
    mlbam: ResolutionStatus
    mlbam_id: str | None
    lahman: ResolutionStatus
    lahman_player_id: str | None
    lahman_links: tuple[Any, ...]


def _identifier(value: str, field_name: str) -> str:
    if not value or value != value.strip() or len(value) > 200 or "\x00" in value:
        raise FD1IdentityCoverageError(f"{field_name} is not an exact identifier")
    return value


def _canonical_checksum(value: Any) -> str:
    def encode(item: Any) -> Any:
        if isinstance(item, Enum):
            return item.value
        raise TypeError(f"cannot encode {type(item)!r}")

    return sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=encode,
        ).encode("utf-8")
    ).hexdigest()


def _row_season(filename: str, fields: Sequence[str], index: Mapping[str, int]) -> int:
    if filename == "allplayers.csv":
        token = fields[index["season"]].strip()
        if len(token) != 4 or not token.isascii() or not token.isdigit():
            raise FD1IdentityCoverageError("allplayers.csv has an invalid season")
        return int(token)
    gid = _identifier(fields[index["gid"]], f"{filename}.gid")
    if len(gid) != 12 or not gid[3:11].isdigit() or not gid[11].isdigit():
        raise FD1IdentityCoverageError(f"{filename} has an invalid Retrosheet game ID")
    return int(gid[3:7])


def _quote_state(encoded: bytes, inside_quotes: bool) -> bool:
    """Track CSV quote state without a per-byte loop over multi-gigabyte files."""

    offset = 0
    while True:
        position = encoded.find(b'"', offset)
        if position < 0:
            return inside_quotes
        if inside_quotes and position + 1 < len(encoded) and encoded[position + 1] == 0x22:
            offset = position + 2
            continue
        inside_quotes = not inside_quotes
        offset = position + 1


def _scan_source(
    spec: SourceFileSpec,
    *,
    limits: IdentityCoverageLimits,
    on_row: Callable[[Sequence[str], Mapping[str, int]], int],
) -> RetrosheetIdentitySourceEvidence:
    if spec.source_key != RETROSHEET_SOURCE_KEY:
        raise FD1IdentityCoverageError(
            f"{spec.logical_table} must be bound to {RETROSHEET_SOURCE_KEY}"
        )
    if spec.expected_sha256 is None or spec.expected_content_length is None:
        raise FD1IdentityCoverageError(
            f"{spec.logical_table} requires expected SHA-256 and content length"
        )
    expected_header = RETROSHEET_CSV_HEADERS[spec.logical_table]
    try:
        before = spec.path.stat()
    except OSError as exc:
        raise FD1IdentityCoverageError(f"cannot stat {spec.logical_table}") from exc
    if not spec.path.is_file() or before.st_size <= 0:
        raise FD1IdentityCoverageError(f"{spec.logical_table} is not a non-empty file")
    if before.st_size > limits.max_file_bytes:
        raise FD1IdentityCoverageError(f"{spec.logical_table} exceeds max_file_bytes")
    if before.st_size != spec.expected_content_length:
        raise FD1IdentityCoverageError(f"{spec.logical_table} content length changed")

    digest = sha256()
    rows_scanned = 0
    active_references = 0
    quote_open = False
    logical_record_bytes = 0

    try:
        stream = spec.path.open("rb")
    except OSError as exc:
        raise FD1IdentityCoverageError(f"cannot open {spec.logical_table}") from exc

    def decoded_lines() -> Iterator[str]:
        nonlocal quote_open, logical_record_bytes
        first = True
        for encoded in stream:
            digest.update(encoded)
            logical_record_bytes += len(encoded)
            if logical_record_bytes > limits.max_csv_record_bytes:
                raise FD1IdentityCoverageError(
                    f"{spec.logical_table} CSV record exceeds configured bound"
                )
            quote_open = _quote_state(encoded, quote_open)
            try:
                yield encoded.decode("utf-8-sig" if first else "utf-8")
            except UnicodeDecodeError as exc:
                raise FD1IdentityCoverageError(
                    f"{spec.logical_table} is not valid UTF-8"
                ) from exc
            first = False
            if not quote_open:
                logical_record_bytes = 0

    with stream:
        try:
            reader = csv.reader(decoded_lines(), strict=True)
            try:
                actual_header = tuple(next(reader))
            except StopIteration as exc:
                raise FD1IdentityCoverageError(
                    f"{spec.logical_table} has no header"
                ) from exc
            if actual_header != expected_header:
                raise FD1IdentityCoverageError(
                    f"{spec.logical_table} header contract mismatch"
                )
            index = MappingProxyType(
                {column: position for position, column in enumerate(expected_header)}
            )
            for fields in reader:
                rows_scanned += 1
                if rows_scanned > limits.max_rows_per_file:
                    raise FD1IdentityCoverageError(
                        f"{spec.logical_table} exceeds max_rows_per_file"
                    )
                if len(fields) != len(expected_header) or not any(fields):
                    raise FD1IdentityCoverageError(
                        f"{spec.logical_table}:{rows_scanned + 1} is malformed"
                    )
                active_references += on_row(fields, index)
        except csv.Error as exc:
            raise FD1IdentityCoverageError(
                f"{spec.logical_table} contains malformed CSV"
            ) from exc
    if quote_open:
        raise FD1IdentityCoverageError(f"{spec.logical_table} ends inside a quoted field")

    try:
        after = spec.path.stat()
    except OSError as exc:
        raise FD1IdentityCoverageError(f"cannot restat {spec.logical_table}") from exc
    content_sha256 = digest.hexdigest()
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or content_sha256 != spec.expected_sha256
    ):
        raise FD1IdentityCoverageError(
            f"{spec.logical_table} changed or failed its SHA-256 pin"
        )
    return RetrosheetIdentitySourceEvidence(
        logical_table=spec.logical_table,
        source_key=spec.source_key,
        source_release=spec.source_release,
        content_sha256=content_sha256,
        content_length=after.st_size,
        rows_scanned=rows_scanned,
        active_references=active_references,
    )


def _status_for_target(
    core_status: ResolutionStatus,
    canonical_uuid: str | None,
    exact_ids_by_uuid: Mapping[str, set[str]],
    conflict_uuids: set[str],
) -> tuple[ResolutionStatus, str | None]:
    if core_status is ResolutionStatus.CONFLICT:
        return ResolutionStatus.CONFLICT, None
    if core_status is ResolutionStatus.UNRESOLVED or canonical_uuid is None:
        return ResolutionStatus.UNRESOLVED, None
    exact_ids = exact_ids_by_uuid.get(canonical_uuid, set())
    if canonical_uuid in conflict_uuids or len(exact_ids) > 1:
        return ResolutionStatus.CONFLICT, None
    if len(exact_ids) == 1:
        return ResolutionStatus.EXACT, next(iter(exact_ids))
    return ResolutionStatus.UNRESOLVED, None


def _build_resolutions(
    crosswalk: PersonCrosswalkReport,
    active_ids: set[str],
) -> Mapping[str, _PlayerResolution]:
    retro_assertions = {
        assertion.external_id: assertion
        for assertion in crosswalk.namespace_assertions
        if assertion.namespace == "RETROSHEET" and assertion.external_id in active_ids
    }
    if len(retro_assertions) > len(active_ids):
        raise FD1IdentityCoverageError("duplicate Retrosheet assertions are impossible")

    core_by_id: dict[str, tuple[ResolutionStatus, str | None]] = {}
    active_uuid_by_source_row: dict[tuple[str, int, str], set[str]] = defaultdict(set)
    active_uuids: set[str] = set()
    for retrosheet_id in active_ids:
        assertion = retro_assertions.get(retrosheet_id)
        if assertion is None:
            core_by_id[retrosheet_id] = (ResolutionStatus.UNRESOLVED, None)
            continue
        if assertion.resolution_status is ResolutionStatus.CONFLICT:
            core_by_id[retrosheet_id] = (ResolutionStatus.CONFLICT, None)
            continue
        canonical_uuid = assertion.canonical_chadwick_uuid
        if canonical_uuid is None:
            raise FD1IdentityCoverageError("exact Retrosheet assertion lacks a UUID")
        core_by_id[retrosheet_id] = (ResolutionStatus.EXACT, canonical_uuid)
        active_uuids.add(canonical_uuid)
        for row in assertion.source_rows:
            active_uuid_by_source_row[
                (row.file_sha256, row.row_number, row.row_sha256)
            ].add(canonical_uuid)

    exact_ids: dict[str, dict[str, set[str]]] = {
        "CHADWICK_PERSON": defaultdict(set),
        "MLBAM": defaultdict(set),
    }
    conflict_uuids: dict[str, set[str]] = {
        "CHADWICK_PERSON": set(),
        "MLBAM": set(),
    }
    for assertion in crosswalk.namespace_assertions:
        if assertion.namespace not in exact_ids:
            continue
        if (
            assertion.resolution_status is ResolutionStatus.EXACT
            and assertion.canonical_chadwick_uuid in active_uuids
        ):
            exact_ids[assertion.namespace][assertion.canonical_chadwick_uuid].add(
                assertion.external_id
            )
            continue
        if assertion.resolution_status is not ResolutionStatus.CONFLICT:
            continue
        for row in assertion.source_rows:
            conflict_uuids[assertion.namespace].update(
                active_uuid_by_source_row.get(
                    (row.file_sha256, row.row_number, row.row_sha256), set()
                )
            )

    lahman_by_retro: dict[str, dict[str, Any]] = defaultdict(dict)
    for link in crosswalk.links:
        associated_id = link.lahman_retrosheet_id
        if associated_id not in active_ids:
            associated_id = (
                link.retrosheet_id
                if link.resolution_status is ResolutionStatus.EXACT
                and link.retrosheet_id in active_ids
                else None
            )
        if associated_id is not None:
            lahman_by_retro[associated_id][link.lahman_player_id] = link

    result: dict[str, _PlayerResolution] = {}
    for retrosheet_id in sorted(active_ids):
        core_status, canonical_uuid = core_by_id[retrosheet_id]
        person_status, person_id = _status_for_target(
            core_status,
            canonical_uuid,
            exact_ids["CHADWICK_PERSON"],
            conflict_uuids["CHADWICK_PERSON"],
        )
        mlbam_status, mlbam_id = _status_for_target(
            core_status,
            canonical_uuid,
            exact_ids["MLBAM"],
            conflict_uuids["MLBAM"],
        )
        lahman_links = tuple(
            lahman_by_retro.get(retrosheet_id, {}).get(player_id)
            for player_id in sorted(lahman_by_retro.get(retrosheet_id, {}))
        )
        exact_lahman = tuple(
            link
            for link in lahman_links
            if link.resolution_status is ResolutionStatus.EXACT
            and link.retrosheet_id == retrosheet_id
            and link.canonical_chadwick_uuid == canonical_uuid
        )
        if core_status is ResolutionStatus.CONFLICT:
            lahman_status = ResolutionStatus.CONFLICT
            lahman_id = None
        elif core_status is ResolutionStatus.UNRESOLVED:
            lahman_status = ResolutionStatus.UNRESOLVED
            lahman_id = None
        elif len(lahman_links) > 1 or any(
            link.resolution_status is ResolutionStatus.CONFLICT for link in lahman_links
        ):
            lahman_status = ResolutionStatus.CONFLICT
            lahman_id = None
        elif len(lahman_links) == 1 and len(exact_lahman) == 1:
            lahman_status = ResolutionStatus.EXACT
            lahman_id = exact_lahman[0].lahman_player_id
        else:
            lahman_status = ResolutionStatus.UNRESOLVED
            lahman_id = None
        result[retrosheet_id] = _PlayerResolution(
            retrosheet_to_chadwick=core_status,
            canonical_uuid=canonical_uuid,
            chadwick_person=person_status,
            chadwick_person_id=person_id,
            mlbam=mlbam_status,
            mlbam_id=mlbam_id,
            lahman=lahman_status,
            lahman_player_id=lahman_id,
            lahman_links=lahman_links,
        )
    return MappingProxyType(result)


def _status_counts(
    status_by_id: Mapping[str, ResolutionStatus],
    references_by_id: Mapping[str, int],
) -> tuple[int, int, int, int, int, int]:
    unique = Counter(status_by_id.values())
    references: Counter[ResolutionStatus] = Counter()
    for player_id, count in references_by_id.items():
        references[status_by_id[player_id]] += count
    return (
        unique[ResolutionStatus.EXACT],
        unique[ResolutionStatus.CONFLICT],
        unique[ResolutionStatus.UNRESOLVED],
        references[ResolutionStatus.EXACT],
        references[ResolutionStatus.CONFLICT],
        references[ResolutionStatus.UNRESOLVED],
    )


def _coverage_slice(
    label: str,
    population: str,
    seasons: tuple[int, ...],
    references: Mapping[int, Mapping[str, _ReferenceAccumulator]],
    resolutions: Mapping[str, _PlayerResolution],
) -> IdentityCoverageSlice:
    references_by_id: Counter[str] = Counter()
    for season in seasons:
        references_by_id.update(
            {
                player_id: accumulator.reference_count
                for player_id, accumulator in references[season].items()
            }
        )

    status_by_namespace: dict[IdentityNamespace, dict[str, ResolutionStatus]] = {
        IdentityNamespace.RETROSHEET: {
            player_id: ResolutionStatus.EXACT for player_id in references_by_id
        },
        IdentityNamespace.CHADWICK_PERSON: {
            player_id: resolutions[player_id].chadwick_person
            for player_id in references_by_id
        },
        IdentityNamespace.MLBAM: {
            player_id: resolutions[player_id].mlbam for player_id in references_by_id
        },
        IdentityNamespace.LAHMAN: {
            player_id: resolutions[player_id].lahman for player_id in references_by_id
        },
    }
    namespace_rows: list[NamespaceCoverage] = []
    for namespace in IdentityNamespace:
        counts = _status_counts(status_by_namespace[namespace], references_by_id)
        namespace_rows.append(
            NamespaceCoverage(
                namespace=namespace,
                unique_player_count=len(references_by_id),
                exact_unique_player_count=counts[0],
                conflict_unique_player_count=counts[1],
                unresolved_unique_player_count=counts[2],
                reference_count=sum(references_by_id.values()),
                exact_reference_count=counts[3],
                conflict_reference_count=counts[4],
                unresolved_reference_count=counts[5],
            )
        )

    relation_rows: list[ExactLinkRate] = []
    for relation, statuses in (
        (
            IdentityRelation.RETROSHEET_TO_CHADWICK,
            {
                player_id: resolutions[player_id].retrosheet_to_chadwick
                for player_id in references_by_id
            },
        ),
        (
            IdentityRelation.RETROSHEET_TO_MLBAM,
            {
                player_id: resolutions[player_id].mlbam
                for player_id in references_by_id
            },
        ),
    ):
        counts = _status_counts(statuses, references_by_id)
        relation_rows.append(
            ExactLinkRate(
                relation=relation,
                source_unique_id_count=len(statuses),
                exact_unique_link_count=counts[0],
                conflict_unique_link_count=counts[1],
                unresolved_unique_link_count=counts[2],
                source_reference_count=sum(references_by_id.values()),
                exact_reference_count=counts[3],
                conflict_reference_count=counts[4],
                unresolved_reference_count=counts[5],
            )
        )

    lahman_statuses: dict[str, ResolutionStatus] = {}
    lahman_reference_counts: Counter[str] = Counter()
    for retrosheet_id, reference_count in references_by_id.items():
        resolution = resolutions[retrosheet_id]
        for link in resolution.lahman_links:
            status = link.resolution_status
            if (
                status is ResolutionStatus.EXACT
                and (
                    link.canonical_chadwick_uuid != resolution.canonical_uuid
                    or link.retrosheet_id != retrosheet_id
                )
            ):
                status = ResolutionStatus.CONFLICT
            previous = lahman_statuses.get(link.lahman_player_id)
            if previous is None:
                lahman_statuses[link.lahman_player_id] = status
            elif previous is not status:
                lahman_statuses[link.lahman_player_id] = ResolutionStatus.CONFLICT
            lahman_reference_counts[link.lahman_player_id] += reference_count
    lahman_counts = _status_counts(lahman_statuses, lahman_reference_counts)
    relation_rows.append(
        ExactLinkRate(
            relation=IdentityRelation.LAHMAN_TO_CHADWICK,
            source_unique_id_count=len(lahman_statuses),
            exact_unique_link_count=lahman_counts[0],
            conflict_unique_link_count=lahman_counts[1],
            unresolved_unique_link_count=lahman_counts[2],
            source_reference_count=sum(lahman_reference_counts.values()),
            exact_reference_count=lahman_counts[3],
            conflict_reference_count=lahman_counts[4],
            unresolved_reference_count=lahman_counts[5],
        )
    )
    return IdentityCoverageSlice(
        label=label,
        population=population,
        seasons=seasons,
        unique_retrosheet_players=len(references_by_id),
        reference_count=sum(references_by_id.values()),
        namespace_coverage=tuple(namespace_rows),
        exact_link_rates=tuple(relation_rows),
    )


def _affected_players(
    references: Mapping[int, Mapping[str, _ReferenceAccumulator]],
    resolutions: Mapping[str, _PlayerResolution],
) -> tuple[ActiveIdentityIssue, ...]:
    combined: dict[str, _ReferenceAccumulator] = {}
    seasons_by_id: dict[str, set[int]] = defaultdict(set)
    for season in ACTIVE_SEASONS:
        for player_id, source in references[season].items():
            seasons_by_id[player_id].add(season)
            target = combined.setdefault(player_id, _ReferenceAccumulator())
            target.reference_count += source.reference_count
            target.roles.update(source.roles)
            for role, games in source.games_by_role.items():
                target.games_by_role[role].update(games)

    issues: list[ActiveIdentityIssue] = []
    for player_id in sorted(combined):
        resolution = resolutions[player_id]
        statuses = (
            resolution.retrosheet_to_chadwick,
            resolution.chadwick_person,
            resolution.mlbam,
            resolution.lahman,
        )
        if all(status is ResolutionStatus.EXACT for status in statuses):
            continue
        source = combined[player_id]
        roles = tuple(
            AffectedRole(
                role=role,
                reference_count=source.roles[role],
                affected_game_ids=tuple(sorted(source.games_by_role.get(role, set()))),
            )
            for role in sorted(source.roles)
        )
        issues.append(
            ActiveIdentityIssue(
                retrosheet_id=player_id,
                seasons=tuple(sorted(seasons_by_id[player_id])),
                reference_count=source.reference_count,
                retrosheet_to_chadwick_status=resolution.retrosheet_to_chadwick,
                chadwick_person_status=resolution.chadwick_person,
                mlbam_status=resolution.mlbam,
                lahman_status=resolution.lahman,
                canonical_chadwick_uuid=resolution.canonical_uuid,
                chadwick_person_id=resolution.chadwick_person_id,
                mlbam_id=resolution.mlbam_id,
                lahman_player_id=resolution.lahman_player_id,
                roles=roles,
            )
        )
    return tuple(issues)


def build_active_identity_coverage(
    crosswalk: PersonCrosswalkReport,
    retrosheet_sources: Sequence[SourceFileSpec],
    *,
    limits: IdentityCoverageLimits | None = None,
    primary_threshold: float = PRIMARY_IDENTITY_THRESHOLD,
) -> ActiveIdentityCoverageReport:
    """Audit exact active-player identity coverage without external providers.

    The primary threshold passes only when *both* the unique-player and raw
    reference Retrosheet-to-Chadwick rates for 2023--2025 meet the threshold.
    Lahman-to-Chadwick reference counts project each Retrosheet source reference
    onto every active Lahman ``playerID`` associated by an official exact ID.
    """

    bounds = limits or IdentityCoverageLimits()
    if isinstance(primary_threshold, bool) or not 0.0 < primary_threshold <= 1.0:
        raise FD1IdentityCoverageError("primary_threshold must be in (0, 1]")
    if crosswalk.name_based_match_count != 0:
        raise FD1IdentityCoverageError("name-based identity evidence is prohibited")
    by_table: dict[str, SourceFileSpec] = {}
    for spec in retrosheet_sources:
        if spec.logical_table not in _REQUIRED_TABLES:
            raise FD1IdentityCoverageError(
                f"unexpected Retrosheet identity table {spec.logical_table}"
            )
        if spec.logical_table in by_table:
            raise FD1IdentityCoverageError(
                f"duplicate Retrosheet identity table {spec.logical_table}"
            )
        by_table[spec.logical_table] = spec
    if tuple(sorted(by_table)) != tuple(sorted(_REQUIRED_TABLES)):
        raise FD1IdentityCoverageError("exactly five identity-bearing tables are required")
    releases = {spec.source_release for spec in by_table.values()}
    if len(releases) != 1:
        raise FD1IdentityCoverageError("Retrosheet identity tables cross source releases")

    references: dict[int, dict[str, _ReferenceAccumulator]] = {
        season: {} for season in ACTIVE_SEASONS
    }
    active_reference_count = 0
    player_role_game_links = 0

    def observer(filename: str) -> Callable[[Sequence[str], Mapping[str, int]], int]:
        player_columns = _PLAYER_COLUMNS[filename]

        def observe(fields: Sequence[str], index: Mapping[str, int]) -> int:
            nonlocal active_reference_count, player_role_game_links
            season = _row_season(filename, fields, index)
            if season not in references:
                return 0
            game_id: str | None = None
            if filename != "allplayers.csv":
                if fields[index["gametype"]].strip().lower() not in _REGULAR_SEASON_TOKENS:
                    return 0
                game_id = fields[index["gid"]]
            selected = 0
            for column in player_columns:
                player_id = fields[index[column]]
                if player_id == "":
                    continue
                _identifier(player_id, f"{filename}.{column}")
                season_rows = references[season]
                if player_id not in season_rows:
                    if sum(len(rows) for rows in references.values()) >= (
                        bounds.max_player_season_identities
                    ):
                        raise FD1IdentityCoverageError(
                            "active player-season identities exceed configured bound"
                        )
                    season_rows[player_id] = _ReferenceAccumulator()
                accumulator = season_rows[player_id]
                role = f"{filename}.{column}"
                accumulator.reference_count += 1
                accumulator.roles[role] += 1
                if game_id is not None and game_id not in accumulator.games_by_role[role]:
                    player_role_game_links += 1
                    if player_role_game_links > bounds.max_player_role_game_links:
                        raise FD1IdentityCoverageError(
                            "player-role game links exceed configured bound"
                        )
                    accumulator.games_by_role[role].add(game_id)
                selected += 1
                active_reference_count += 1
                if active_reference_count > bounds.max_active_references:
                    raise FD1IdentityCoverageError(
                        "active identity references exceed configured bound"
                    )
            return selected

        return observe

    source_files = tuple(
        _scan_source(
            by_table[logical_table],
            limits=bounds,
            on_row=observer(logical_table),
        )
        for logical_table in _REQUIRED_TABLES
    )
    active_ids = {
        player_id for season_rows in references.values() for player_id in season_rows
    }
    resolutions = _build_resolutions(crosswalk, active_ids)

    season_coverage = tuple(
        _coverage_slice(
            label=f"SEASON_{season}",
            population=(
                GEN2_PRIOR_ONLY_POPULATION
                if season in PRIOR_ONLY_SEASONS
                else GEN2_PRIMARY_POPULATION
            ),
            seasons=(season,),
            references=references,
            resolutions=resolutions,
        )
        for season in ACTIVE_SEASONS
    )
    population_coverage = (
        _coverage_slice(
            label=GEN2_PRIOR_ONLY_POPULATION,
            population=GEN2_PRIOR_ONLY_POPULATION,
            seasons=tuple(sorted(PRIOR_ONLY_SEASONS)),
            references=references,
            resolutions=resolutions,
        ),
        _coverage_slice(
            label=GEN2_PRIMARY_POPULATION,
            population=GEN2_PRIMARY_POPULATION,
            seasons=tuple(sorted(PRIMARY_SEASONS)),
            references=references,
            resolutions=resolutions,
        ),
    )
    overall = _coverage_slice(
        label="GEN2_ACTIVE_2021_2025",
        population="GEN2_ACTIVE_2021_2025",
        seasons=ACTIVE_SEASONS,
        references=references,
        resolutions=resolutions,
    )
    affected = _affected_players(references, resolutions)
    primary = population_coverage[1].relation(
        IdentityRelation.RETROSHEET_TO_CHADWICK
    )
    primary_unique_meets = primary.exact_unique_rate >= primary_threshold
    primary_reference_meets = primary.exact_reference_rate >= primary_threshold
    payload = {
        "schema_version": FD1_IDENTITY_COVERAGE_SCHEMA_VERSION,
        "source_release": next(iter(releases)),
        "source_files": [asdict(row) for row in source_files],
        "crosswalk_source_files": [asdict(row) for row in crosswalk.source_files],
        "season_coverage": [asdict(row) for row in season_coverage],
        "population_coverage": [asdict(row) for row in population_coverage],
        "overall_coverage": asdict(overall),
        "affected_players": [asdict(row) for row in affected],
        "primary_threshold": primary_threshold,
        "primary_unique_meets_threshold": primary_unique_meets,
        "primary_reference_meets_threshold": primary_reference_meets,
        "name_based_match_count": 0,
    }
    return ActiveIdentityCoverageReport(
        schema_version=FD1_IDENTITY_COVERAGE_SCHEMA_VERSION,
        source_release=next(iter(releases)),
        source_files=source_files,
        crosswalk_source_files=tuple(crosswalk.source_files),
        season_coverage=season_coverage,
        population_coverage=population_coverage,
        overall_coverage=overall,
        affected_players=affected,
        primary_threshold=primary_threshold,
        primary_unique_meets_threshold=primary_unique_meets,
        primary_reference_meets_threshold=primary_reference_meets,
        primary_meets_threshold=primary_unique_meets and primary_reference_meets,
        name_based_match_count=0,
        derivation_checksum=_canonical_checksum(payload),
    )


__all__ = [
    "ACTIVE_SEASONS",
    "FD1_IDENTITY_COVERAGE_SCHEMA_VERSION",
    "PRIMARY_IDENTITY_THRESHOLD",
    "ActiveIdentityCoverageReport",
    "ActiveIdentityIssue",
    "AffectedRole",
    "ExactLinkRate",
    "FD1IdentityCoverageError",
    "IdentityCoverageLimits",
    "IdentityCoverageSlice",
    "IdentityNamespace",
    "IdentityRelation",
    "NamespaceCoverage",
    "RetrosheetIdentitySourceEvidence",
    "build_active_identity_coverage",
]

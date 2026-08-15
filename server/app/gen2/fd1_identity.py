"""Provider-free Chadwick/Lahman identity and chronological-prior import.

The importer is deliberately a filesystem-to-Python boundary.  It does not
download data, open a database connection, or write warehouse artifacts.  All
emitted records retain the checksum of the immutable source file and the exact
logical CSV record (including its original line terminator) from which they
were derived.

Names are descriptive attributes only.  Person, team, franchise, and park
links are made solely through identifiers published by Chadwick or Lahman.
Conflicting identifier evidence is returned as ``CONFLICT`` with no canonical
assignment; it is never repaired with a name-based guess.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
import csv
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from hashlib import sha256
import io
import json
from pathlib import Path
from pathlib import PurePosixPath
import re
from types import MappingProxyType
from typing import Any
from uuid import UUID

from app.gen2.open_data_registry import OpenDataIdentityMappingSpec


FD1_IDENTITY_SCHEMA_VERSION = "gate3c_fd1_identity_prior_v1"
CHADWICK_SOURCE_KEY = "CHADWICK_REGISTER_ARCHIVES"
LAHMAN_SOURCE_KEY = "SABR_LAHMAN_BOX_DOWNLOADS"
RETROSHEET_AUXILIARY_SOURCE_KEY = "RETROSHEET_AUXILIARY_DOWNLOADS"
CHADWICK_RELEASE = "2e8e73355f9c77b963115377bd98c784cfeec10f"
LAHMAN_RELEASE = "lahman_1871-2025_csv"

PRIOR_SEASONS = (2021, 2022, 2023, 2024, 2025)
PRIOR_ONLY_SEASONS = frozenset((2021, 2022))
PRIMARY_SEASONS = frozenset((2023, 2024, 2025))
GEN2_PRIOR_ONLY_POPULATION = "GEN2_PRIOR_ONLY_2021_2022"
GEN2_PRIMARY_POPULATION = "GEN2_PRIMARY_2023_2025"
PLAYER_PRIOR_EVENT_FAMILY = "player_prior"
PLAYER_PRIOR_PARTITION_MONTH = 12
PLAYER_PRIOR_TIMING_CLASSIFICATION = "RETROSPECTIVE_RECONSTRUCTION"

# These tables were expected by the feature design but are not published in
# the official SABR Lahman 2025 Box folder.  Their absence is a source-release
# limitation, not permission to synthesize equivalent rows.
EXPECTED_MISSING_LAHMAN_TABLES = (
    "Appearances.csv",
    "AllstarFull.csv",
    "AwardsPlayers.csv",
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_KEY = re.compile(r"^[A-Z][A-Z0-9_]{2,79}$")
_LOGICAL_URI_SECRET_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "password",
    "secret",
    "signature",
    "signed_url",
    "token",
)

_CHADWICK_REQUIRED_COLUMNS = (
    "key_person",
    "key_uuid",
    "key_mlbam",
    "key_retro",
    "key_bbref",
)
_LAHMAN_PEOPLE_REQUIRED_COLUMNS = (
    "playerID",
    "bbrefID",
    "retroID",
)

_CHADWICK_REGISTRY_NAMESPACE = MappingProxyType(
    {
        "BASEBALL_REFERENCE": "baseball_reference",
        "CHADWICK_PERSON": "chadwick_person",
        "MLBAM": "mlbam",
        "RETROSHEET": "retrosheet",
    }
)

_PRIOR_METRICS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "BATTING": (
            "G",
            "AB",
            "R",
            "H",
            "2B",
            "3B",
            "HR",
            "RBI",
            "SB",
            "CS",
            "BB",
            "SO",
            "IBB",
            "HBP",
            "SH",
            "SF",
            "GIDP",
        ),
        "PITCHING": (
            "W",
            "L",
            "G",
            "GS",
            "CG",
            "SHO",
            "SV",
            "IPouts",
            "H",
            "ER",
            "HR",
            "BB",
            "SO",
            "IBB",
            "WP",
            "HBP",
            "BK",
            "BFP",
            "GF",
            "R",
            "SH",
            "SF",
            "GIDP",
        ),
        "FIELDING": (
            "G",
            "GS",
            "InnOuts",
            "PO",
            "A",
            "E",
            "DP",
            "PB",
            "WP",
            "SB",
            "CS",
        ),
    }
)

_PRIOR_TABLE_TO_DOMAIN = MappingProxyType(
    {
        "Batting.csv": "BATTING",
        "Pitching.csv": "PITCHING",
        "Fielding.csv": "FIELDING",
    }
)


class FD1IdentityImportError(ValueError):
    """A source file or derivation failed the fail-closed import contract."""


class ResolutionStatus(str, Enum):
    EXACT = "EXACT"
    UNRESOLVED = "UNRESOLVED"
    CONFLICT = "CONFLICT"


class PriorCoverageRole(str, Enum):
    PRIOR_ONLY = "PRIOR_ONLY"
    PRIMARY = "PRIMARY"


@dataclass(frozen=True, slots=True)
class ImportLimits:
    """Explicit memory and parser ceilings for local open-data imports."""

    max_file_bytes: int = 512 * 1024**2
    max_row_bytes: int = 2 * 1024**2
    max_rows_per_file: int = 2_000_000
    max_chadwick_people: int = 1_000_000
    max_identifier_claims: int = 5_000_000
    max_candidates_per_identifier: int = 128
    max_prior_groups: int = 250_000
    max_evidence_rows_per_group: int = 512
    max_organization_records: int = 250_000

    def __post_init__(self) -> None:
        for name in (
            "max_file_bytes",
            "max_row_bytes",
            "max_rows_per_file",
            "max_chadwick_people",
            "max_identifier_claims",
            "max_candidates_per_identifier",
            "max_prior_groups",
            "max_evidence_rows_per_group",
            "max_organization_records",
        ):
            if getattr(self, name) <= 0:
                raise FD1IdentityImportError(f"{name} must be positive")


@dataclass(frozen=True, slots=True)
class SourceFileSpec:
    """One already-acquired immutable CSV object."""

    path: Path
    source_key: str
    source_release: str
    logical_table: str
    expected_sha256: str | None = None
    expected_content_length: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path))
        if not _SOURCE_KEY.fullmatch(self.source_key):
            raise FD1IdentityImportError("source_key is invalid")
        _required_text(self.source_release, "source_release", 160)
        _required_text(self.logical_table, "logical_table", 160)
        if Path(self.logical_table).name != self.logical_table:
            raise FD1IdentityImportError("logical_table must be a base file name")
        if self.expected_sha256 is not None and not _SHA256.fullmatch(
            self.expected_sha256
        ):
            raise FD1IdentityImportError("expected_sha256 must be lowercase SHA-256")
        if self.expected_content_length is not None and self.expected_content_length <= 0:
            raise FD1IdentityImportError("expected_content_length must be positive")


@dataclass(frozen=True, slots=True)
class SourceFileEvidence:
    source_key: str
    source_release: str
    logical_table: str
    content_sha256: str
    content_length: int

    def __post_init__(self) -> None:
        if not _SHA256.fullmatch(self.content_sha256):
            raise FD1IdentityImportError("content_sha256 is invalid")
        if self.content_length <= 0:
            raise FD1IdentityImportError("content_length must be positive")


@dataclass(frozen=True, slots=True)
class SourceRowEvidence:
    source_key: str
    source_release: str
    logical_table: str
    file_sha256: str
    row_number: int
    row_sha256: str

    def __post_init__(self) -> None:
        if not _SHA256.fullmatch(self.file_sha256):
            raise FD1IdentityImportError("file_sha256 is invalid")
        if not _SHA256.fullmatch(self.row_sha256):
            raise FD1IdentityImportError("row_sha256 is invalid")
        if self.row_number < 2:
            raise FD1IdentityImportError("data row_number must follow the header")


@dataclass(frozen=True, slots=True)
class ExternalIdentifier:
    namespace: str
    value: str
    source_field: str

    def __post_init__(self) -> None:
        _required_text(self.namespace, "namespace", 80)
        _identifier(self.value, "identifier value")
        _required_text(self.source_field, "source_field", 80)


@dataclass(frozen=True, slots=True)
class LahmanPersonCrosswalk:
    lahman_player_id: str
    resolution_status: ResolutionStatus
    confidence: str
    canonical_chadwick_uuid: str | None
    chadwick_person_key: str | None
    mlbam_id: str | None
    retrosheet_id: str | None
    baseball_reference_id: str | None
    lahman_retrosheet_id: str | None
    lahman_baseball_reference_id: str | None
    evidence_namespaces: tuple[str, ...]
    mismatch_namespaces: tuple[str, ...]
    candidate_chadwick_uuids: tuple[str, ...]
    source_rows: tuple[SourceRowEvidence, ...]
    derivation_checksum: str

    def __post_init__(self) -> None:
        _identifier(self.lahman_player_id, "lahman_player_id")
        if not self.source_rows:
            raise FD1IdentityImportError("person crosswalk requires source evidence")
        if not _SHA256.fullmatch(self.derivation_checksum):
            raise FD1IdentityImportError("derivation_checksum is invalid")
        if self.resolution_status is ResolutionStatus.EXACT:
            if self.canonical_chadwick_uuid is None:
                raise FD1IdentityImportError("EXACT person link requires a Chadwick UUID")
            if self.candidate_chadwick_uuids != (self.canonical_chadwick_uuid,):
                raise FD1IdentityImportError("EXACT person link must have one candidate")
            if self.mismatch_namespaces:
                raise FD1IdentityImportError("EXACT person link cannot retain mismatches")
            expected_confidence = (
                "EXACT_MULTI_NAMESPACE"
                if len(self.evidence_namespaces) > 1
                else "EXACT_SINGLE_NAMESPACE"
            )
            if self.confidence != expected_confidence:
                raise FD1IdentityImportError("EXACT person confidence is inconsistent")
        elif self.canonical_chadwick_uuid is not None:
            raise FD1IdentityImportError(
                "Unresolved or conflicting links cannot assign a canonical UUID"
            )
        elif self.confidence != self.resolution_status.value:
            raise FD1IdentityImportError("Non-EXACT person confidence is inconsistent")


@dataclass(frozen=True, slots=True)
class NamespaceCount:
    namespace: str
    count: int

    def __post_init__(self) -> None:
        if self.count < 0:
            raise FD1IdentityImportError("namespace count cannot be negative")


@dataclass(frozen=True, slots=True)
class PersonNamespaceAssertion:
    """One identifier claim carried directly by a Chadwick source row."""

    namespace: str
    external_id: str
    resolution_status: ResolutionStatus
    canonical_chadwick_uuid: str | None
    source_rows: tuple[SourceRowEvidence, ...]
    derivation_checksum: str

    def __post_init__(self) -> None:
        if self.namespace not in _CHADWICK_REGISTRY_NAMESPACE:
            raise FD1IdentityImportError("person assertion namespace is invalid")
        _identifier(self.external_id, "person assertion external_id")
        if self.resolution_status is ResolutionStatus.EXACT:
            if self.canonical_chadwick_uuid is None:
                raise FD1IdentityImportError(
                    "EXACT person namespace assertion requires a Chadwick UUID"
                )
            _validate_uuid(self.canonical_chadwick_uuid)
        elif self.canonical_chadwick_uuid is not None:
            raise FD1IdentityImportError(
                "Ambiguous person namespace assertion cannot assign a UUID"
            )
        if self.resolution_status is ResolutionStatus.UNRESOLVED:
            raise FD1IdentityImportError(
                "A populated Chadwick identifier claim cannot be UNRESOLVED"
            )
        if not self.source_rows:
            raise FD1IdentityImportError(
                "person namespace assertion requires source rows"
            )
        if not _SHA256.fullmatch(self.derivation_checksum):
            raise FD1IdentityImportError(
                "person assertion derivation_checksum is invalid"
            )


@dataclass(frozen=True, slots=True)
class PersonCrosswalkReport:
    chadwick_source_rows: int
    chadwick_unique_uuids: int
    lahman_source_rows: int
    lahman_people: int
    exact_count: int
    unresolved_count: int
    conflict_count: int
    duplicate_identifier_claims: int
    name_based_match_count: int
    identifier_counts: tuple[NamespaceCount, ...]
    source_files: tuple[SourceFileEvidence, ...]
    links: tuple[LahmanPersonCrosswalk, ...]
    namespace_assertions: tuple[PersonNamespaceAssertion, ...]

    def __post_init__(self) -> None:
        counts = (
            self.chadwick_source_rows,
            self.chadwick_unique_uuids,
            self.lahman_source_rows,
            self.lahman_people,
            self.exact_count,
            self.unresolved_count,
            self.conflict_count,
            self.duplicate_identifier_claims,
            self.name_based_match_count,
        )
        if any(value < 0 for value in counts):
            raise FD1IdentityImportError("person report counts cannot be negative")
        if self.lahman_people != len(self.links):
            raise FD1IdentityImportError("lahman_people must equal emitted links")
        if self.lahman_people != (
            self.exact_count + self.unresolved_count + self.conflict_count
        ):
            raise FD1IdentityImportError("person resolution counts do not reconcile")
        if self.name_based_match_count != 0:
            raise FD1IdentityImportError("name-based identity matching is prohibited")
        if self.chadwick_unique_uuids > self.chadwick_source_rows:
            raise FD1IdentityImportError("unique UUID count exceeds source rows")
        if len({link.lahman_player_id for link in self.links}) != len(self.links):
            raise FD1IdentityImportError("person report must emit one link per Lahman ID")
        ordered_assertions = tuple(
            sorted(
                self.namespace_assertions,
                key=lambda item: (
                    item.namespace,
                    item.external_id,
                    item.source_rows[0].logical_table,
                    item.source_rows[0].row_number,
                    item.source_rows[0].row_sha256,
                ),
            )
        )
        if ordered_assertions != self.namespace_assertions:
            raise FD1IdentityImportError(
                "person namespace assertions must be deterministic"
            )
        expected_assertions = sum(item.count for item in self.identifier_counts)
        if expected_assertions != len(self.namespace_assertions):
            raise FD1IdentityImportError(
                "person namespace assertion counts do not reconcile"
            )

    def exact_uuid_by_lahman_id(self) -> Mapping[str, str]:
        return MappingProxyType(
            {
                link.lahman_player_id: link.canonical_chadwick_uuid
                for link in self.links
                if link.resolution_status is ResolutionStatus.EXACT
                and link.canonical_chadwick_uuid is not None
            }
        )

    def status_by_lahman_id(self) -> Mapping[str, ResolutionStatus]:
        return MappingProxyType(
            {link.lahman_player_id: link.resolution_status for link in self.links}
        )

    @property
    def lahman_to_chadwick_match_rate(self) -> float:
        return self.exact_count / self.lahman_people if self.lahman_people else 0.0

    @property
    def retrosheet_identifier_count(self) -> int:
        return sum(link.lahman_retrosheet_id is not None for link in self.links)

    @property
    def retrosheet_to_chadwick_count(self) -> int:
        return sum(
            link.resolution_status is ResolutionStatus.EXACT
            and link.lahman_retrosheet_id is not None
            and link.lahman_retrosheet_id == link.retrosheet_id
            for link in self.links
        )

    @property
    def retrosheet_to_chadwick_match_rate(self) -> float:
        denominator = self.retrosheet_identifier_count
        return self.retrosheet_to_chadwick_count / denominator if denominator else 0.0

    @property
    def retrosheet_to_mlbam_count(self) -> int:
        return sum(
            link.resolution_status is ResolutionStatus.EXACT
            and link.lahman_retrosheet_id is not None
            and link.lahman_retrosheet_id == link.retrosheet_id
            and link.mlbam_id is not None
            for link in self.links
        )

    @property
    def retrosheet_to_mlbam_match_rate(self) -> float:
        denominator = self.retrosheet_identifier_count
        return self.retrosheet_to_mlbam_count / denominator if denominator else 0.0


@dataclass(frozen=True, slots=True)
class PlayerPriorRow:
    lahman_player_id: str
    canonical_chadwick_uuid: str | None
    identity_resolution_status: ResolutionStatus
    season: int
    coverage_role: PriorCoverageRole
    population_label: str
    domain: str
    position: str | None
    eligible_from_season: int
    same_season_predictor_eligible: bool
    team_ids: tuple[str, ...]
    league_ids: tuple[str, ...]
    metrics: tuple[tuple[str, int | None], ...]
    source_rows: tuple[SourceRowEvidence, ...]
    derivation_checksum: str

    def __post_init__(self) -> None:
        _identifier(self.lahman_player_id, "lahman_player_id")
        if self.season not in PRIOR_SEASONS:
            raise FD1IdentityImportError("player prior season is outside FD1 coverage")
        expected_role = (
            PriorCoverageRole.PRIOR_ONLY
            if self.season in PRIOR_ONLY_SEASONS
            else PriorCoverageRole.PRIMARY
        )
        if self.coverage_role is not expected_role:
            raise FD1IdentityImportError("player prior coverage role is inconsistent")
        expected_population = (
            GEN2_PRIOR_ONLY_POPULATION
            if self.season in PRIOR_ONLY_SEASONS
            else GEN2_PRIMARY_POPULATION
        )
        if self.population_label != expected_population:
            raise FD1IdentityImportError("player prior population label is inconsistent")
        if self.domain not in _PRIOR_METRICS:
            raise FD1IdentityImportError("player prior domain is invalid")
        if self.domain == "FIELDING" and not self.position:
            raise FD1IdentityImportError("fielding prior requires a source position")
        if self.domain != "FIELDING" and self.position is not None:
            raise FD1IdentityImportError("non-fielding prior cannot have a position")
        if self.eligible_from_season != self.season + 1:
            raise FD1IdentityImportError("season total is eligible only from the next season")
        if self.same_season_predictor_eligible:
            raise FD1IdentityImportError("full-season totals cannot predict that season")
        if self.identity_resolution_status is ResolutionStatus.EXACT:
            if self.canonical_chadwick_uuid is None:
                raise FD1IdentityImportError("EXACT prior identity requires a UUID")
        elif self.canonical_chadwick_uuid is not None:
            raise FD1IdentityImportError("non-EXACT prior cannot assign a UUID")
        if not self.source_rows:
            raise FD1IdentityImportError("player prior requires source rows")
        if tuple(name for name, _ in self.metrics) != _PRIOR_METRICS[self.domain]:
            raise FD1IdentityImportError("player prior metric contract is invalid")
        if not _SHA256.fullmatch(self.derivation_checksum):
            raise FD1IdentityImportError("player prior derivation checksum is invalid")


@dataclass(frozen=True, slots=True)
class CountByKey:
    key: str
    count: int

    def __post_init__(self) -> None:
        if self.count < 0:
            raise FD1IdentityImportError("count cannot be negative")


@dataclass(frozen=True, slots=True)
class PlayerPriorReport:
    source_rows_scanned: int
    selected_source_rows: int
    prior_row_count: int
    unresolved_identity_count: int
    conflict_identity_count: int
    counts_by_season: tuple[CountByKey, ...]
    counts_by_domain: tuple[CountByKey, ...]
    source_files: tuple[SourceFileEvidence, ...]
    rows: tuple[PlayerPriorRow, ...]

    def __post_init__(self) -> None:
        if min(
            self.source_rows_scanned,
            self.selected_source_rows,
            self.prior_row_count,
            self.unresolved_identity_count,
            self.conflict_identity_count,
        ) < 0:
            raise FD1IdentityImportError("prior report counts cannot be negative")
        if self.selected_source_rows > self.source_rows_scanned:
            raise FD1IdentityImportError("selected prior rows exceed scanned rows")
        if self.prior_row_count != len(self.rows):
            raise FD1IdentityImportError("prior_row_count must equal emitted rows")
        if sum(item.count for item in self.counts_by_season) != len(self.rows):
            raise FD1IdentityImportError("season prior counts do not reconcile")
        if sum(item.count for item in self.counts_by_domain) != len(self.rows):
            raise FD1IdentityImportError("domain prior counts do not reconcile")
        ordered = tuple(
            sorted(
                self.rows,
                key=lambda row: (
                    row.season,
                    row.lahman_player_id,
                    row.domain,
                    row.position or "",
                ),
            )
        )
        if ordered != self.rows:
            raise FD1IdentityImportError("player priors must be chronological")


@dataclass(frozen=True, slots=True)
class RegistryIdentityMappingBatch:
    """A bounded deterministic batch accepted by ``record_identity_mapping``."""

    batch_number: int
    specs: tuple[OpenDataIdentityMappingSpec, ...]
    batch_checksum: str

    def __post_init__(self) -> None:
        if self.batch_number < 1:
            raise FD1IdentityImportError("identity batch_number must be positive")
        if not self.specs:
            raise FD1IdentityImportError("identity mapping batch cannot be empty")
        if not _SHA256.fullmatch(self.batch_checksum):
            raise FD1IdentityImportError("identity batch checksum is invalid")
        expected = _canonical_checksum(
            [_identity_mapping_spec_payload(spec) for spec in self.specs]
        )
        if expected != self.batch_checksum:
            raise FD1IdentityImportError("identity batch checksum does not match specs")


@dataclass(frozen=True, slots=True)
class PlayerPriorPartitionBatch:
    """Annual Lahman priors in a bounded monthly Parquet-store partition batch.

    Lahman publishes annual regular-season totals, not monthly observations.
    They are therefore assigned only to the December monthly store partition,
    with ``official_date`` equal to the source season's period end.  This does
    not make the total eligible inside that season; every row retains its
    next-season eligibility field.
    """

    event_family: str
    season: int
    month: int
    population: str
    batch_number: int
    rows: tuple[Mapping[str, Any], ...]
    row_count: int
    player_count: int
    min_official_date: date
    max_official_date: date
    raw_contributor_manifest: tuple[Mapping[str, Any], ...]
    raw_contributor_checksum: str
    payload_checksum: str

    def __post_init__(self) -> None:
        if self.event_family != PLAYER_PRIOR_EVENT_FAMILY:
            raise FD1IdentityImportError("player-prior batch family is invalid")
        if self.season not in PRIOR_SEASONS:
            raise FD1IdentityImportError("player-prior batch season is invalid")
        if self.month != PLAYER_PRIOR_PARTITION_MONTH:
            raise FD1IdentityImportError(
                "Annual Lahman priors may only use the December partition"
            )
        expected_population = (
            GEN2_PRIOR_ONLY_POPULATION
            if self.season in PRIOR_ONLY_SEASONS
            else GEN2_PRIMARY_POPULATION
        )
        if self.population != expected_population:
            raise FD1IdentityImportError("player-prior batch population is invalid")
        if self.batch_number < 1:
            raise FD1IdentityImportError("player-prior batch_number must be positive")
        if not self.rows or self.row_count != len(self.rows):
            raise FD1IdentityImportError("player-prior batch row count is invalid")
        if self.player_count <= 0 or self.player_count > self.row_count:
            raise FD1IdentityImportError("player-prior batch player count is invalid")
        expected_date = date(self.season, PLAYER_PRIOR_PARTITION_MONTH, 31)
        if (
            self.min_official_date != expected_date
            or self.max_official_date != expected_date
        ):
            raise FD1IdentityImportError("player-prior batch date bounds are invalid")
        if not self.raw_contributor_manifest:
            raise FD1IdentityImportError("player-prior batch requires contributors")
        for contributor in self.raw_contributor_manifest:
            if set(contributor) != {"logical_uri", "file_sha256", "record_count"}:
                raise FD1IdentityImportError(
                    "player-prior contributor fields are outside the store contract"
                )
            _validate_relative_logical_uri(str(contributor["logical_uri"]))
            if not _SHA256.fullmatch(str(contributor["file_sha256"])):
                raise FD1IdentityImportError("contributor file checksum is invalid")
            count = contributor["record_count"]
            if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
                raise FD1IdentityImportError("contributor record_count must be positive")
        expected_contributor_checksum = _canonical_checksum(
            [dict(item) for item in self.raw_contributor_manifest]
        )
        if self.raw_contributor_checksum != expected_contributor_checksum:
            raise FD1IdentityImportError("raw contributor checksum does not reconcile")
        expected_payload_checksum = _canonical_checksum(
            [dict(item) for item in self.rows]
        )
        if self.payload_checksum != expected_payload_checksum:
            raise FD1IdentityImportError("player-prior payload checksum does not reconcile")
        derived_players: set[str] = set()
        for row in self.rows:
            if row.get("canonical_game_id") is not None:
                raise FD1IdentityImportError("player priors cannot assert a game identity")
            if row.get("season") != self.season:
                raise FD1IdentityImportError("player-prior row crosses batch season")
            if row.get("population") != self.population:
                raise FD1IdentityImportError("player-prior row crosses population")
            if row.get("official_date") != expected_date.isoformat():
                raise FD1IdentityImportError("player-prior row date is inconsistent")
            identifier = row.get("canonical_player_id") or row.get("player_id")
            if not isinstance(identifier, str) or not identifier:
                raise FD1IdentityImportError("player-prior row lacks a player identity")
            derived_players.add(identifier)
        if len(derived_players) != self.player_count:
            raise FD1IdentityImportError("player-prior player count does not reconcile")

    def parquet_write_kwargs(self) -> dict[str, Any]:
        """Return the family/partition/count arguments for ``write_partition``.

        The caller still supplies source/checkpoint authorization and code
        revision arguments controlled by the warehouse orchestration layer.
        """

        return {
            "rows": self.rows,
            "event_family": self.event_family,
            "season": self.season,
            "month": self.month,
            "raw_contributor_manifest": self.raw_contributor_manifest,
            "raw_contributor_checksum": self.raw_contributor_checksum,
            "game_count": 0,
            "plate_appearance_count": 0,
            "player_count": self.player_count,
            "min_event_date": self.min_official_date,
            "max_event_date": self.max_official_date,
        }


@dataclass(frozen=True, slots=True)
class TeamSeasonIdentity:
    canonical_team_season_id: str
    season: int
    lahman_team_id: str
    lahman_franchise_id: str
    retrosheet_team_id: str | None
    baseball_reference_team_id: str | None
    lahman_1945_team_id: str | None
    display_name: str | None
    resolution_status: ResolutionStatus
    conflict_namespaces: tuple[str, ...]
    source_rows: tuple[SourceRowEvidence, ...]
    derivation_checksum: str


@dataclass(frozen=True, slots=True)
class FranchiseIdentity:
    canonical_franchise_id: str
    lahman_franchise_id: str
    display_name: str | None
    active_flag: str | None
    national_association_id: str | None
    resolution_status: ResolutionStatus
    source_rows: tuple[SourceRowEvidence, ...]
    derivation_checksum: str


@dataclass(frozen=True, slots=True)
class ParkIdentity:
    canonical_park_id: str
    lahman_park_id: str
    display_name: str | None
    aliases: tuple[str, ...]
    city: str | None
    state: str | None
    country: str | None
    resolution_status: ResolutionStatus
    source_rows: tuple[SourceRowEvidence, ...]
    derivation_checksum: str


@dataclass(frozen=True, slots=True)
class TeamParkLink:
    season: int
    league_id: str
    lahman_team_id: str
    lahman_park_id: str
    canonical_team_season_id: str | None
    canonical_park_id: str | None
    span_first: date | None
    span_last: date | None
    games: int | None
    openings: int | None
    attendance: int | None
    resolution_status: ResolutionStatus
    source_rows: tuple[SourceRowEvidence, ...]
    derivation_checksum: str


@dataclass(frozen=True, slots=True)
class OrganizationIdentityReport:
    team_season_count: int
    franchise_count: int
    park_count: int
    team_park_link_count: int
    exact_team_park_link_count: int
    unresolved_team_park_link_count: int
    conflict_count: int
    name_based_match_count: int
    source_files: tuple[SourceFileEvidence, ...]
    team_seasons: tuple[TeamSeasonIdentity, ...]
    franchises: tuple[FranchiseIdentity, ...]
    parks: tuple[ParkIdentity, ...]
    team_park_links: tuple[TeamParkLink, ...]

    def __post_init__(self) -> None:
        if self.team_season_count != len(self.team_seasons):
            raise FD1IdentityImportError("team count does not reconcile")
        if self.franchise_count != len(self.franchises):
            raise FD1IdentityImportError("franchise count does not reconcile")
        if self.park_count != len(self.parks):
            raise FD1IdentityImportError("park count does not reconcile")
        if self.team_park_link_count != len(self.team_park_links):
            raise FD1IdentityImportError("team/park count does not reconcile")
        if self.team_park_link_count != (
            self.exact_team_park_link_count
            + self.unresolved_team_park_link_count
            + sum(
                link.resolution_status is ResolutionStatus.CONFLICT
                for link in self.team_park_links
            )
        ):
            raise FD1IdentityImportError("team/park resolution counts do not reconcile")
        if self.name_based_match_count != 0:
            raise FD1IdentityImportError("name-based organization matching is prohibited")


@dataclass(frozen=True, slots=True)
class RetrosheetTeamSeasonCrosswalk:
    season: int
    retrosheet_team_id: str
    canonical_team_season_id: str | None
    resolution_status: ResolutionStatus
    source_rows: tuple[SourceRowEvidence, ...]
    derivation_checksum: str

    def __post_init__(self) -> None:
        if self.season < 1800 or self.season > 2200:
            raise FD1IdentityImportError("Retrosheet team season is invalid")
        _identifier(self.retrosheet_team_id, "retrosheet_team_id")
        if self.resolution_status is ResolutionStatus.EXACT:
            if self.canonical_team_season_id is None:
                raise FD1IdentityImportError(
                    "EXACT Retrosheet team link requires a canonical team season"
                )
        elif self.canonical_team_season_id is not None:
            raise FD1IdentityImportError(
                "Non-EXACT Retrosheet team link cannot assign a canonical ID"
            )
        if not self.source_rows:
            raise FD1IdentityImportError("Retrosheet team link requires source rows")
        if not _SHA256.fullmatch(self.derivation_checksum):
            raise FD1IdentityImportError(
                "Retrosheet team derivation_checksum is invalid"
            )


@dataclass(frozen=True, slots=True)
class RetrosheetParkCrosswalk:
    retrosheet_park_id: str
    canonical_park_id: str | None
    resolution_status: ResolutionStatus
    source_rows: tuple[SourceRowEvidence, ...]
    derivation_checksum: str

    def __post_init__(self) -> None:
        _identifier(self.retrosheet_park_id, "retrosheet_park_id")
        if self.resolution_status is ResolutionStatus.EXACT:
            if self.canonical_park_id is None:
                raise FD1IdentityImportError(
                    "EXACT Retrosheet park link requires a canonical park"
                )
        elif self.canonical_park_id is not None:
            raise FD1IdentityImportError(
                "Non-EXACT Retrosheet park link cannot assign a canonical ID"
            )
        if not self.source_rows:
            raise FD1IdentityImportError("Retrosheet park link requires source rows")
        if not _SHA256.fullmatch(self.derivation_checksum):
            raise FD1IdentityImportError(
                "Retrosheet park derivation_checksum is invalid"
            )


@dataclass(frozen=True, slots=True)
class RetrosheetOrganizationCrosswalkReport:
    team_season_count: int
    exact_team_season_count: int
    unresolved_team_season_count: int
    conflict_team_season_count: int
    park_count: int
    exact_park_count: int
    unresolved_park_count: int
    conflict_park_count: int
    unlinked_retrosheet_team_id_count: int
    unlinked_lahman_park_count: int
    name_based_match_count: int
    source_files: tuple[SourceFileEvidence, ...]
    team_seasons: tuple[RetrosheetTeamSeasonCrosswalk, ...]
    parks: tuple[RetrosheetParkCrosswalk, ...]

    def __post_init__(self) -> None:
        counts = (
            self.team_season_count,
            self.exact_team_season_count,
            self.unresolved_team_season_count,
            self.conflict_team_season_count,
            self.park_count,
            self.exact_park_count,
            self.unresolved_park_count,
            self.conflict_park_count,
            self.unlinked_retrosheet_team_id_count,
            self.unlinked_lahman_park_count,
            self.name_based_match_count,
        )
        if any(value < 0 for value in counts):
            raise FD1IdentityImportError(
                "Retrosheet organization counts cannot be negative"
            )
        if self.team_season_count != len(self.team_seasons) or (
            self.team_season_count
            != self.exact_team_season_count
            + self.unresolved_team_season_count
            + self.conflict_team_season_count
        ):
            raise FD1IdentityImportError(
                "Retrosheet team-season counts do not reconcile"
            )
        if self.park_count != len(self.parks) or (
            self.park_count
            != self.exact_park_count
            + self.unresolved_park_count
            + self.conflict_park_count
        ):
            raise FD1IdentityImportError("Retrosheet park counts do not reconcile")
        if self.name_based_match_count != 0:
            raise FD1IdentityImportError(
                "name-based Retrosheet organization matching is prohibited"
            )


@dataclass(frozen=True, slots=True)
class SourceLimitation:
    table_name: str
    status: str
    reason: str
    substitution_permitted: bool
    fabricated_row_count: int

    def __post_init__(self) -> None:
        if self.status != "SOURCE_RELEASE_NOT_PUBLISHED":
            raise FD1IdentityImportError("source limitation status is invalid")
        if self.substitution_permitted or self.fabricated_row_count != 0:
            raise FD1IdentityImportError("missing official tables cannot be fabricated")


@dataclass(frozen=True, slots=True)
class LahmanReleaseLimitations:
    release: str
    published_tables: tuple[str, ...]
    missing_expected_tables: tuple[SourceLimitation, ...]

    def __post_init__(self) -> None:
        if len(set(self.published_tables)) != len(self.published_tables):
            raise FD1IdentityImportError("published Lahman table names must be unique")
        expected = tuple(
            name
            for name in EXPECTED_MISSING_LAHMAN_TABLES
            if name not in self.published_tables
        )
        if tuple(item.table_name for item in self.missing_expected_tables) != expected:
            raise FD1IdentityImportError("missing-table limitation list is inconsistent")


@dataclass(frozen=True, slots=True)
class FD1IdentityPriorSourceBundle:
    chadwick_people: tuple[SourceFileSpec, ...]
    lahman_people: SourceFileSpec
    lahman_batting: SourceFileSpec
    lahman_pitching: SourceFileSpec
    lahman_fielding: SourceFileSpec
    lahman_teams: SourceFileSpec
    lahman_franchises: SourceFileSpec
    lahman_parks: SourceFileSpec
    lahman_home_games: SourceFileSpec
    published_lahman_tables: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FD1IdentityPriorImport:
    schema_version: str
    people: PersonCrosswalkReport
    priors: PlayerPriorReport
    organizations: OrganizationIdentityReport
    limitations: LahmanReleaseLimitations

    def __post_init__(self) -> None:
        if self.schema_version != FD1_IDENTITY_SCHEMA_VERSION:
            raise FD1IdentityImportError("FD1 identity schema version is invalid")


@dataclass(frozen=True, slots=True)
class _CsvRow:
    values: Mapping[str, str]
    evidence: SourceRowEvidence


@dataclass(slots=True)
class _ChadwickPerson:
    uuid: str
    person_key: str
    mlbam: str | None
    retro: str | None
    bbref: str | None
    row: SourceRowEvidence


@dataclass(slots=True)
class _PriorAccumulator:
    player_id: str
    season: int
    domain: str
    position: str | None
    totals: dict[str, int]
    complete: dict[str, bool]
    teams: set[str] = field(default_factory=set)
    leagues: set[str] = field(default_factory=set)
    source_rows: list[SourceRowEvidence] = field(default_factory=list)


@dataclass(slots=True)
class _OrganizationAccumulator:
    values: dict[str, str]
    source_rows: list[SourceRowEvidence]


def _required_text(value: str, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise FD1IdentityImportError(f"{name} must be non-empty and <= {maximum} chars")
    if value != value.strip() or "\x00" in value:
        raise FD1IdentityImportError(f"{name} contains unsafe whitespace or NUL")
    return value


def _identifier(value: str, name: str) -> str:
    return _required_text(value, name, 200)


def _optional_identifier(value: str, name: str) -> str | None:
    if value == "":
        return None
    return _identifier(value, name)


def _canonical_checksum(value: Any) -> str:
    return sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _validate_relative_logical_uri(value: str) -> str:
    _required_text(value, "evidence logical URI", 1000)
    pure = PurePosixPath(value)
    lowered = value.lower()
    if (
        value.startswith("/")
        or "\\" in value
        or "://" in value
        or "?" in value
        or "#" in value
        or ".." in value
        or any(part in {"", ".", ".."} for part in pure.parts)
        or any(marker in lowered for marker in _LOGICAL_URI_SECRET_MARKERS)
    ):
        raise FD1IdentityImportError(
            "evidence logical URI must be secret-free and root-relative"
        )
    return value


def _logical_uri_for_row(
    row: SourceRowEvidence,
    evidence_logical_uri_by_file_checksum: Mapping[str, str],
) -> str:
    try:
        value = evidence_logical_uri_by_file_checksum[row.file_sha256]
    except KeyError as exc:
        raise FD1IdentityImportError(
            f"No logical URI supplied for source checksum {row.file_sha256}"
        ) from exc
    return _validate_relative_logical_uri(value)


def _identity_mapping_spec_payload(
    spec: OpenDataIdentityMappingSpec,
) -> dict[str, Any]:
    return {
        "source_key": spec.source_key,
        "entity_type": spec.entity_type,
        "source_namespace": spec.source_namespace,
        "external_id": spec.external_id,
        "canonical_namespace": spec.canonical_namespace,
        "canonical_id": spec.canonical_id,
        "source_row_number": spec.source_row_number,
        "source_row_checksum": spec.source_row_checksum,
        "confidence": spec.confidence,
        "evidence_logical_uri": spec.evidence_logical_uri,
        "valid_from": spec.valid_from.isoformat() if spec.valid_from else None,
        "valid_through": (
            spec.valid_through.isoformat() if spec.valid_through else None
        ),
    }


def _inspect_source_file(
    spec: SourceFileSpec, limits: ImportLimits
) -> SourceFileEvidence:
    try:
        stat = spec.path.stat()
    except OSError as exc:
        raise FD1IdentityImportError(f"Cannot stat source file {spec.path}") from exc
    if not spec.path.is_file():
        raise FD1IdentityImportError(f"Source path is not a file: {spec.path}")
    if stat.st_size <= 0:
        raise FD1IdentityImportError(f"Source file is empty: {spec.path}")
    if stat.st_size > limits.max_file_bytes:
        raise FD1IdentityImportError(
            f"Source file exceeds max_file_bytes: {spec.logical_table}"
        )
    if (
        spec.expected_content_length is not None
        and stat.st_size != spec.expected_content_length
    ):
        raise FD1IdentityImportError(
            f"Content length mismatch for {spec.logical_table}: "
            f"{stat.st_size} != {spec.expected_content_length}"
        )
    digest = sha256()
    with spec.path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    content_sha256 = digest.hexdigest()
    if spec.expected_sha256 is not None and content_sha256 != spec.expected_sha256:
        raise FD1IdentityImportError(
            f"SHA-256 mismatch for {spec.logical_table}: "
            f"{content_sha256} != {spec.expected_sha256}"
        )
    return SourceFileEvidence(
        source_key=spec.source_key,
        source_release=spec.source_release,
        logical_table=spec.logical_table,
        content_sha256=content_sha256,
        content_length=stat.st_size,
    )


def _raw_csv_records(handle: Any, max_row_bytes: int) -> Iterator[bytes]:
    """Yield exact RFC-4180-ish logical records without unbounded buffering."""

    record = bytearray()
    inside_quotes = False
    while chunk := handle.read(64 * 1024):
        for octet in chunk:
            record.append(octet)
            if len(record) > max_row_bytes:
                raise FD1IdentityImportError("CSV logical row exceeds max_row_bytes")
            if octet == 0x22:  # paired quotes toggle twice and preserve state
                inside_quotes = not inside_quotes
            if octet == 0x0A and not inside_quotes:
                yield bytes(record)
                record.clear()
    if inside_quotes:
        raise FD1IdentityImportError("CSV ends inside a quoted field")
    if record:
        yield bytes(record)


def _parse_csv_record(raw: bytes, *, first: bool) -> tuple[str, ...]:
    try:
        text = raw.decode("utf-8-sig" if first else "utf-8")
    except UnicodeDecodeError as exc:
        raise FD1IdentityImportError("CSV must be valid UTF-8") from exc
    try:
        parsed = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as exc:
        raise FD1IdentityImportError("CSV record is malformed") from exc
    if len(parsed) != 1:
        raise FD1IdentityImportError("CSV logical record did not parse exactly once")
    return tuple(parsed[0])


def _stream_csv_rows(
    spec: SourceFileSpec,
    *,
    required_columns: Sequence[str],
    limits: ImportLimits,
) -> tuple[SourceFileEvidence, Iterator[_CsvRow]]:
    evidence = _inspect_source_file(spec, limits)

    def iterator() -> Iterator[_CsvRow]:
        parsed_digest = sha256()
        record_number = 0
        header: tuple[str, ...] | None = None
        emitted = 0
        before = spec.path.stat()
        with spec.path.open("rb") as handle:
            for raw in _raw_csv_records(handle, limits.max_row_bytes):
                parsed_digest.update(raw)
                record_number += 1
                if header is None:
                    header = _parse_csv_record(raw, first=True)
                    if not header or any(not column for column in header):
                        raise FD1IdentityImportError("CSV header contains an empty column")
                    if len(set(header)) != len(header):
                        raise FD1IdentityImportError("CSV header columns must be unique")
                    missing = sorted(set(required_columns).difference(header))
                    if missing:
                        raise FD1IdentityImportError(
                            f"{spec.logical_table} is missing columns: {', '.join(missing)}"
                        )
                    continue
                if raw.rstrip(b"\r\n") == b"":
                    continue
                fields = _parse_csv_record(raw, first=False)
                if len(fields) != len(header):
                    raise FD1IdentityImportError(
                        f"{spec.logical_table} row {record_number} has "
                        f"{len(fields)} fields; expected {len(header)}"
                    )
                emitted += 1
                if emitted > limits.max_rows_per_file:
                    raise FD1IdentityImportError(
                        f"{spec.logical_table} exceeds max_rows_per_file"
                    )
                yield _CsvRow(
                    values=MappingProxyType(dict(zip(header, fields, strict=True))),
                    evidence=SourceRowEvidence(
                        source_key=spec.source_key,
                        source_release=spec.source_release,
                        logical_table=spec.logical_table,
                        file_sha256=evidence.content_sha256,
                        row_number=record_number,
                        row_sha256=sha256(raw).hexdigest(),
                    ),
                )
        if header is None:
            raise FD1IdentityImportError(f"{spec.logical_table} has no CSV header")
        after = spec.path.stat()
        if (
            before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or parsed_digest.hexdigest() != evidence.content_sha256
        ):
            raise FD1IdentityImportError(
                f"Source file changed during import: {spec.logical_table}"
            )

    return evidence, iterator()


def _require_source(
    spec: SourceFileSpec, source_key: str, logical_table: str | None = None
) -> None:
    if spec.source_key != source_key:
        raise FD1IdentityImportError(
            f"{spec.logical_table} must be bound to source {source_key}"
        )
    if logical_table is not None and spec.logical_table != logical_table:
        raise FD1IdentityImportError(
            f"Expected logical table {logical_table}, got {spec.logical_table}"
        )


def _validate_uuid(value: str) -> str:
    _identifier(value, "key_uuid")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise FD1IdentityImportError(f"Invalid Chadwick UUID: {value}") from exc
    if str(parsed) != value.lower():
        raise FD1IdentityImportError("Chadwick UUID must use canonical text form")
    return value.lower()


def build_person_crosswalk(
    chadwick_people: Sequence[SourceFileSpec],
    lahman_people: SourceFileSpec,
    *,
    limits: ImportLimits | None = None,
) -> PersonCrosswalkReport:
    """Build an exact-ID person crosswalk while retaining unresolved conflicts."""

    bounds = limits or ImportLimits()
    if not chadwick_people:
        raise FD1IdentityImportError("At least one Chadwick people shard is required")
    _require_source(lahman_people, LAHMAN_SOURCE_KEY, "People.csv")

    people_by_uuid: dict[str, list[_ChadwickPerson]] = defaultdict(list)
    claims: dict[tuple[str, str], set[str]] = defaultdict(set)
    claim_count = 0
    chadwick_rows = 0
    source_files: list[SourceFileEvidence] = []

    for spec in sorted(chadwick_people, key=lambda item: item.logical_table):
        _require_source(spec, CHADWICK_SOURCE_KEY)
        if not (
            spec.logical_table.startswith("people-")
            and spec.logical_table.endswith(".csv")
        ):
            raise FD1IdentityImportError("Chadwick shards must be logical people-*.csv")
        file_evidence, rows = _stream_csv_rows(
            spec,
            required_columns=_CHADWICK_REQUIRED_COLUMNS,
            limits=bounds,
        )
        source_files.append(file_evidence)
        for row in rows:
            chadwick_rows += 1
            if chadwick_rows > bounds.max_chadwick_people:
                raise FD1IdentityImportError("Chadwick people exceed configured bound")
            person = _ChadwickPerson(
                uuid=_validate_uuid(row.values["key_uuid"]),
                person_key=_identifier(row.values["key_person"], "key_person"),
                mlbam=_optional_identifier(row.values["key_mlbam"], "key_mlbam"),
                retro=_optional_identifier(row.values["key_retro"], "key_retro"),
                bbref=_optional_identifier(row.values["key_bbref"], "key_bbref"),
                row=row.evidence,
            )
            people_by_uuid[person.uuid].append(person)
            for namespace, value in (
                ("CHADWICK_PERSON", person.person_key),
                ("MLBAM", person.mlbam),
                ("RETROSHEET", person.retro),
                ("BASEBALL_REFERENCE", person.bbref),
            ):
                if value is None:
                    continue
                claims[(namespace, value)].add(person.uuid)
                claim_count += 1
                if claim_count > bounds.max_identifier_claims:
                    raise FD1IdentityImportError(
                        "Chadwick identifier claims exceed configured bound"
                    )
                if len(claims[(namespace, value)]) > bounds.max_candidates_per_identifier:
                    raise FD1IdentityImportError(
                        f"Identifier {namespace}:{value} exceeds candidate bound"
                    )

    duplicate_claims = sum(len(uuids) > 1 for uuids in claims.values()) + sum(
        len(rows) > 1 for rows in people_by_uuid.values()
    )

    lahman_file, lahman_rows_iter = _stream_csv_rows(
        lahman_people,
        required_columns=_LAHMAN_PEOPLE_REQUIRED_COLUMNS,
        limits=bounds,
    )
    source_files.append(lahman_file)
    lahman_by_id: dict[str, list[_CsvRow]] = defaultdict(list)
    lahman_source_rows = 0
    for row in lahman_rows_iter:
        lahman_source_rows += 1
        player_id = _identifier(row.values["playerID"], "playerID")
        lahman_by_id[player_id].append(row)
        if len(lahman_by_id) > bounds.max_chadwick_people:
            raise FD1IdentityImportError("Lahman people exceed configured bound")
        if len(lahman_by_id[player_id]) > bounds.max_candidates_per_identifier:
            raise FD1IdentityImportError("Duplicate Lahman player exceeds candidate bound")

    links: list[LahmanPersonCrosswalk] = []
    for player_id in sorted(lahman_by_id):
        rows = lahman_by_id[player_id]
        provided: list[tuple[str, str]] = []
        for row in rows:
            bbref = _optional_identifier(row.values["bbrefID"], "bbrefID")
            retro = _optional_identifier(row.values["retroID"], "retroID")
            if bbref is not None:
                provided.append(("BASEBALL_REFERENCE", bbref))
            if retro is not None:
                provided.append(("RETROSHEET", retro))

        candidate_uuids: set[str] = set()
        evidence_namespaces: set[str] = set()
        for namespace, value in provided:
            matched = claims.get((namespace, value), set())
            if matched:
                candidate_uuids.update(matched)
                evidence_namespaces.add(namespace)

        mismatches: set[str] = set()
        status = ResolutionStatus.UNRESOLVED
        selected: _ChadwickPerson | None = None
        if len(rows) > 1:
            status = ResolutionStatus.CONFLICT
            mismatches.add("LAHMAN_PLAYER")
        elif len(candidate_uuids) > 1:
            status = ResolutionStatus.CONFLICT
        elif len(candidate_uuids) == 1:
            candidate_uuid = next(iter(candidate_uuids))
            candidates = people_by_uuid[candidate_uuid]
            if len(candidates) != 1:
                status = ResolutionStatus.CONFLICT
                mismatches.add("CHADWICK_UUID")
            else:
                selected = candidates[0]
                lahman_bbref = _optional_identifier(
                    rows[0].values["bbrefID"], "bbrefID"
                )
                lahman_retro = _optional_identifier(
                    rows[0].values["retroID"], "retroID"
                )
                if (
                    lahman_bbref is not None
                    and selected.bbref is not None
                    and lahman_bbref != selected.bbref
                ):
                    mismatches.add("BASEBALL_REFERENCE")
                if (
                    lahman_retro is not None
                    and selected.retro is not None
                    and lahman_retro != selected.retro
                ):
                    mismatches.add("RETROSHEET")
                for namespace, value in (
                    ("CHADWICK_PERSON", selected.person_key),
                    ("MLBAM", selected.mlbam),
                    ("RETROSHEET", selected.retro),
                    ("BASEBALL_REFERENCE", selected.bbref),
                ):
                    if value is not None and len(claims[(namespace, value)]) != 1:
                        mismatches.add(namespace)
                status = (
                    ResolutionStatus.CONFLICT
                    if mismatches
                    else ResolutionStatus.EXACT
                )

        candidate_rows: list[SourceRowEvidence] = []
        for candidate_uuid in sorted(candidate_uuids):
            candidate_rows.extend(
                person.row for person in people_by_uuid.get(candidate_uuid, ())
            )
        source_rows = tuple(
            [row.evidence for row in rows]
            + sorted(
                candidate_rows,
                key=lambda item: (item.logical_table, item.row_number, item.row_sha256),
            )
        )
        exact = status is ResolutionStatus.EXACT and selected is not None
        lahman_bbref_value = _optional_identifier(rows[0].values["bbrefID"], "bbrefID")
        lahman_retro_value = _optional_identifier(rows[0].values["retroID"], "retroID")
        checksum_payload = {
            "schema": FD1_IDENTITY_SCHEMA_VERSION,
            "entity": "person_crosswalk",
            "lahman_player_id": player_id,
            "status": status.value,
            "candidate_uuids": sorted(candidate_uuids),
            "mismatches": sorted(mismatches),
            "source_rows": [item.row_sha256 for item in source_rows],
        }
        links.append(
            LahmanPersonCrosswalk(
                lahman_player_id=player_id,
                resolution_status=status,
                confidence=(
                    (
                        "EXACT_MULTI_NAMESPACE"
                        if len(evidence_namespaces) > 1
                        else "EXACT_SINGLE_NAMESPACE"
                    )
                    if status is ResolutionStatus.EXACT
                    else status.value
                ),
                canonical_chadwick_uuid=selected.uuid if exact else None,
                chadwick_person_key=selected.person_key if exact else None,
                mlbam_id=selected.mlbam if exact else None,
                retrosheet_id=selected.retro if exact else None,
                baseball_reference_id=selected.bbref if exact else None,
                lahman_retrosheet_id=lahman_retro_value,
                lahman_baseball_reference_id=lahman_bbref_value,
                evidence_namespaces=tuple(sorted(evidence_namespaces)),
                mismatch_namespaces=tuple(sorted(mismatches)),
                candidate_chadwick_uuids=tuple(sorted(candidate_uuids)),
                source_rows=source_rows,
                derivation_checksum=_canonical_checksum(checksum_payload),
            )
        )

    person_attribute_by_namespace = {
        "BASEBALL_REFERENCE": "bbref",
        "CHADWICK_PERSON": "person_key",
        "MLBAM": "mlbam",
        "RETROSHEET": "retro",
    }
    namespace_assertions: list[PersonNamespaceAssertion] = []
    for (namespace, external_id), candidate_uuids in sorted(claims.items()):
        attribute_name = person_attribute_by_namespace[namespace]
        evidence_rows = tuple(
            sorted(
                {
                    person.row
                    for candidate_uuid in candidate_uuids
                    for person in people_by_uuid[candidate_uuid]
                    if getattr(person, attribute_name) == external_id
                },
                key=lambda item: (
                    item.logical_table,
                    item.row_number,
                    item.row_sha256,
                ),
            )
        )
        exact = len(candidate_uuids) == 1 and all(
            len(people_by_uuid[candidate_uuid]) == 1
            for candidate_uuid in candidate_uuids
        )
        canonical_uuid = next(iter(candidate_uuids)) if exact else None
        assertion_payload = {
            "schema": FD1_IDENTITY_SCHEMA_VERSION,
            "entity": "person_namespace_assertion",
            "namespace": namespace,
            "external_id": external_id,
            "status": (
                ResolutionStatus.EXACT.value
                if exact
                else ResolutionStatus.CONFLICT.value
            ),
            "canonical_chadwick_uuid": canonical_uuid,
            "candidate_chadwick_uuids": sorted(candidate_uuids),
            "source_rows": [item.row_sha256 for item in evidence_rows],
        }
        namespace_assertions.append(
            PersonNamespaceAssertion(
                namespace=namespace,
                external_id=external_id,
                resolution_status=(
                    ResolutionStatus.EXACT
                    if exact
                    else ResolutionStatus.CONFLICT
                ),
                canonical_chadwick_uuid=canonical_uuid,
                source_rows=evidence_rows,
                derivation_checksum=_canonical_checksum(assertion_payload),
            )
        )

    identifier_counts = tuple(
        NamespaceCount(namespace, count)
        for namespace, count in sorted(
            (
                namespace,
                sum(1 for claim_namespace, _ in claims if claim_namespace == namespace),
            )
            for namespace in {namespace for namespace, _ in claims}
        )
    )
    return PersonCrosswalkReport(
        chadwick_source_rows=chadwick_rows,
        chadwick_unique_uuids=len(people_by_uuid),
        lahman_source_rows=lahman_source_rows,
        lahman_people=len(links),
        exact_count=sum(
            link.resolution_status is ResolutionStatus.EXACT for link in links
        ),
        unresolved_count=sum(
            link.resolution_status is ResolutionStatus.UNRESOLVED for link in links
        ),
        conflict_count=sum(
            link.resolution_status is ResolutionStatus.CONFLICT for link in links
        ),
        duplicate_identifier_claims=duplicate_claims,
        name_based_match_count=0,
        identifier_counts=identifier_counts,
        source_files=tuple(source_files),
        links=tuple(links),
        namespace_assertions=tuple(namespace_assertions),
    )


def iter_registry_identity_mapping_specs(
    report: PersonCrosswalkReport,
    *,
    evidence_logical_uri_by_file_checksum: Mapping[str, str],
    source_namespace: str = "lahman",
    canonical_namespace: str = "chadwick_uuid",
) -> Iterator[OpenDataIdentityMappingSpec]:
    """Yield deterministic registry-ready Lahman-to-Chadwick assertions.

    Multi-namespace exact links translate to registry confidence ``EXACT``;
    single-namespace exact links translate to ``HIGH``.  Ambiguous and absent
    links preserve ``CONFLICT``/``UNRESOLVED`` and never carry a canonical ID.
    """

    _required_text(source_namespace, "source_namespace", 80)
    _required_text(canonical_namespace, "canonical_namespace", 80)
    for link in sorted(report.links, key=lambda item: item.lahman_player_id):
        lahman_rows = sorted(
            (
                row
                for row in link.source_rows
                if row.source_key == LAHMAN_SOURCE_KEY
                and row.logical_table == "People.csv"
            ),
            key=lambda row: (row.row_number, row.row_sha256),
        )
        if not lahman_rows:
            raise FD1IdentityImportError(
                f"{link.lahman_player_id} lacks Lahman People.csv row evidence"
            )
        # The official release has one People row per playerID.  If a malformed
        # source repeats the ID, the earliest exact source row anchors the
        # registry assertion while confidence remains CONFLICT; the report
        # retains every conflicting row.
        row = lahman_rows[0]
        if link.resolution_status is ResolutionStatus.EXACT:
            confidence = (
                "EXACT"
                if link.confidence == "EXACT_MULTI_NAMESPACE"
                else "HIGH"
            )
            canonical_id = link.canonical_chadwick_uuid
        else:
            confidence = link.resolution_status.value
            canonical_id = None
        yield OpenDataIdentityMappingSpec(
            source_key=row.source_key,
            entity_type="person",
            source_namespace=source_namespace,
            external_id=link.lahman_player_id,
            canonical_namespace=canonical_namespace,
            canonical_id=canonical_id,
            source_row_number=row.row_number,
            source_row_checksum=row.row_sha256,
            confidence=confidence,
            evidence_logical_uri=_logical_uri_for_row(
                row, evidence_logical_uri_by_file_checksum
            ),
        )


def iter_registry_identity_mapping_batches(
    report: PersonCrosswalkReport,
    *,
    evidence_logical_uri_by_file_checksum: Mapping[str, str],
    batch_size: int = 10_000,
    source_namespace: str = "lahman",
    canonical_namespace: str = "chadwick_uuid",
) -> Iterator[RegistryIdentityMappingBatch]:
    """Yield bounded identity-spec batches without persistence side effects."""

    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size <= 0:
        raise FD1IdentityImportError("identity mapping batch_size must be positive")
    current: list[OpenDataIdentityMappingSpec] = []
    batch_number = 0
    for spec in iter_registry_identity_mapping_specs(
        report,
        evidence_logical_uri_by_file_checksum=evidence_logical_uri_by_file_checksum,
        source_namespace=source_namespace,
        canonical_namespace=canonical_namespace,
    ):
        current.append(spec)
        if len(current) == batch_size:
            batch_number += 1
            payload = [_identity_mapping_spec_payload(item) for item in current]
            yield RegistryIdentityMappingBatch(
                batch_number=batch_number,
                specs=tuple(current),
                batch_checksum=_canonical_checksum(payload),
            )
            current = []
    if current:
        batch_number += 1
        payload = [_identity_mapping_spec_payload(item) for item in current]
        yield RegistryIdentityMappingBatch(
            batch_number=batch_number,
            specs=tuple(current),
            batch_checksum=_canonical_checksum(payload),
        )


def iter_all_registry_person_identity_mapping_specs(
    report: PersonCrosswalkReport,
    *,
    evidence_logical_uri_by_file_checksum: Mapping[str, str],
    canonical_namespace: str = "chadwick_uuid",
    lahman_source_namespace: str = "lahman",
) -> Iterator[OpenDataIdentityMappingSpec]:
    """Yield every supported exact-ID person namespace for the registry.

    Chadwick is the row-level authority for its person key, MLBAM,
    Retrosheet, and Baseball-Reference claims.  Lahman assertions retain the
    cross-source confidence calculated by :func:`build_person_crosswalk`.
    Duplicate Chadwick claims are emitted once as ``CONFLICT`` and retain all
    conflicting source rows on :class:`PersonNamespaceAssertion`; the registry
    schema can anchor only one evidence row, so the earliest row is used there.
    """

    _required_text(canonical_namespace, "canonical_namespace", 80)
    _required_text(lahman_source_namespace, "lahman_source_namespace", 80)
    for assertion in report.namespace_assertions:
        row = assertion.source_rows[0]
        yield OpenDataIdentityMappingSpec(
            source_key=row.source_key,
            entity_type="person",
            source_namespace=_CHADWICK_REGISTRY_NAMESPACE[assertion.namespace],
            external_id=assertion.external_id,
            canonical_namespace=canonical_namespace,
            canonical_id=assertion.canonical_chadwick_uuid,
            source_row_number=row.row_number,
            source_row_checksum=row.row_sha256,
            confidence=assertion.resolution_status.value,
            evidence_logical_uri=_logical_uri_for_row(
                row, evidence_logical_uri_by_file_checksum
            ),
        )
    yield from iter_registry_identity_mapping_specs(
        report,
        evidence_logical_uri_by_file_checksum=evidence_logical_uri_by_file_checksum,
        source_namespace=lahman_source_namespace,
        canonical_namespace=canonical_namespace,
    )


def iter_all_registry_person_identity_mapping_batches(
    report: PersonCrosswalkReport,
    *,
    evidence_logical_uri_by_file_checksum: Mapping[str, str],
    batch_size: int = 10_000,
    canonical_namespace: str = "chadwick_uuid",
    lahman_source_namespace: str = "lahman",
) -> Iterator[RegistryIdentityMappingBatch]:
    """Yield bounded deterministic batches for every supported person ID."""

    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size <= 0:
        raise FD1IdentityImportError("identity mapping batch_size must be positive")
    current: list[OpenDataIdentityMappingSpec] = []
    batch_number = 0
    for spec in iter_all_registry_person_identity_mapping_specs(
        report,
        evidence_logical_uri_by_file_checksum=evidence_logical_uri_by_file_checksum,
        canonical_namespace=canonical_namespace,
        lahman_source_namespace=lahman_source_namespace,
    ):
        current.append(spec)
        if len(current) == batch_size:
            batch_number += 1
            yield RegistryIdentityMappingBatch(
                batch_number=batch_number,
                specs=tuple(current),
                batch_checksum=_canonical_checksum(
                    [_identity_mapping_spec_payload(item) for item in current]
                ),
            )
            current = []
    if current:
        batch_number += 1
        yield RegistryIdentityMappingBatch(
            batch_number=batch_number,
            specs=tuple(current),
            batch_checksum=_canonical_checksum(
                [_identity_mapping_spec_payload(item) for item in current]
            ),
        )


def _parse_year(value: str, field_name: str) -> int:
    try:
        result = int(_identifier(value, field_name))
    except ValueError as exc:
        raise FD1IdentityImportError(f"{field_name} must be an integer") from exc
    if result < 1800 or result > 2200:
        raise FD1IdentityImportError(f"{field_name} is outside the accepted range")
    return result


def _optional_nonnegative_int(value: str, field_name: str) -> int | None:
    if value == "":
        return None
    try:
        result = int(value)
    except ValueError as exc:
        raise FD1IdentityImportError(f"{field_name} must be an integer or blank") from exc
    if result < 0:
        raise FD1IdentityImportError(f"{field_name} cannot be negative")
    return result


def build_player_priors(
    prior_tables: Sequence[SourceFileSpec],
    *,
    crosswalk: PersonCrosswalkReport | None = None,
    limits: ImportLimits | None = None,
) -> PlayerPriorReport:
    """Aggregate 2021-2025 Lahman rows without same-season leakage."""

    bounds = limits or ImportLimits()
    by_table = {spec.logical_table: spec for spec in prior_tables}
    if set(by_table) != set(_PRIOR_TABLE_TO_DOMAIN):
        raise FD1IdentityImportError(
            "Prior import requires exactly Batting.csv, Pitching.csv, and Fielding.csv"
        )
    if len(by_table) != len(prior_tables):
        raise FD1IdentityImportError("Duplicate prior table specification")

    exact_ids = crosswalk.exact_uuid_by_lahman_id() if crosswalk else {}
    identity_status = crosswalk.status_by_lahman_id() if crosswalk else {}
    groups: dict[tuple[str, int, str, str | None], _PriorAccumulator] = {}
    natural_rows: set[tuple[str, int, str, str, str, str | None]] = set()
    source_files: list[SourceFileEvidence] = []
    scanned = 0
    selected = 0

    for table_name in ("Batting.csv", "Pitching.csv", "Fielding.csv"):
        spec = by_table[table_name]
        _require_source(spec, LAHMAN_SOURCE_KEY, table_name)
        domain = _PRIOR_TABLE_TO_DOMAIN[table_name]
        required = (
            "playerID",
            "yearID",
            "stint",
            "teamID",
            "lgID",
            *(("POS",) if domain == "FIELDING" else ()),
            *_PRIOR_METRICS[domain],
        )
        file_evidence, rows = _stream_csv_rows(
            spec, required_columns=required, limits=bounds
        )
        source_files.append(file_evidence)
        for row in rows:
            scanned += 1
            season = _parse_year(row.values["yearID"], "yearID")
            if season not in PRIOR_SEASONS:
                continue
            selected += 1
            player_id = _identifier(row.values["playerID"], "playerID")
            stint = _identifier(row.values["stint"], "stint")
            team = _identifier(row.values["teamID"], "teamID")
            league = _optional_identifier(row.values["lgID"], "lgID")
            position = (
                _identifier(row.values["POS"], "POS")
                if domain == "FIELDING"
                else None
            )
            natural = (player_id, season, domain, stint, team, position)
            if natural in natural_rows:
                raise FD1IdentityImportError(
                    f"Duplicate Lahman prior natural row: {natural}"
                )
            natural_rows.add(natural)
            key = (player_id, season, domain, position)
            accumulator = groups.get(key)
            if accumulator is None:
                if len(groups) >= bounds.max_prior_groups:
                    raise FD1IdentityImportError("Player-prior groups exceed bound")
                accumulator = _PriorAccumulator(
                    player_id=player_id,
                    season=season,
                    domain=domain,
                    position=position,
                    totals={metric: 0 for metric in _PRIOR_METRICS[domain]},
                    complete={metric: True for metric in _PRIOR_METRICS[domain]},
                )
                groups[key] = accumulator
            if len(accumulator.source_rows) >= bounds.max_evidence_rows_per_group:
                raise FD1IdentityImportError("Prior group exceeds evidence-row bound")
            accumulator.source_rows.append(row.evidence)
            accumulator.teams.add(team)
            if league is not None:
                accumulator.leagues.add(league)
            for metric in _PRIOR_METRICS[domain]:
                value = _optional_nonnegative_int(row.values[metric], metric)
                if value is None:
                    accumulator.complete[metric] = False
                else:
                    accumulator.totals[metric] += value

    output: list[PlayerPriorRow] = []
    for key in sorted(groups, key=lambda value: (value[1], value[0], value[2], value[3] or "")):
        item = groups[key]
        status = identity_status.get(item.player_id, ResolutionStatus.UNRESOLVED)
        canonical_uuid = exact_ids.get(item.player_id)
        metrics = tuple(
            (
                metric,
                item.totals[metric] if item.complete[metric] else None,
            )
            for metric in _PRIOR_METRICS[item.domain]
        )
        source_rows = tuple(
            sorted(
                item.source_rows,
                key=lambda evidence: (
                    evidence.logical_table,
                    evidence.row_number,
                    evidence.row_sha256,
                ),
            )
        )
        payload = {
            "schema": FD1_IDENTITY_SCHEMA_VERSION,
            "entity": "player_prior",
            "player": item.player_id,
            "canonical_uuid": canonical_uuid,
            "identity_status": status.value,
            "season": item.season,
            "domain": item.domain,
            "position": item.position,
            "metrics": metrics,
            "source_rows": [row.row_sha256 for row in source_rows],
            "eligible_from_season": item.season + 1,
        }
        output.append(
            PlayerPriorRow(
                lahman_player_id=item.player_id,
                canonical_chadwick_uuid=canonical_uuid,
                identity_resolution_status=status,
                season=item.season,
                coverage_role=(
                    PriorCoverageRole.PRIOR_ONLY
                    if item.season in PRIOR_ONLY_SEASONS
                    else PriorCoverageRole.PRIMARY
                ),
                population_label=(
                    GEN2_PRIOR_ONLY_POPULATION
                    if item.season in PRIOR_ONLY_SEASONS
                    else GEN2_PRIMARY_POPULATION
                ),
                domain=item.domain,
                position=item.position,
                eligible_from_season=item.season + 1,
                same_season_predictor_eligible=False,
                team_ids=tuple(sorted(item.teams)),
                league_ids=tuple(sorted(item.leagues)),
                metrics=metrics,
                source_rows=source_rows,
                derivation_checksum=_canonical_checksum(payload),
            )
        )

    by_season = tuple(
        CountByKey(str(season), sum(row.season == season for row in output))
        for season in PRIOR_SEASONS
    )
    by_domain = tuple(
        CountByKey(domain, sum(row.domain == domain for row in output))
        for domain in _PRIOR_METRICS
    )
    return PlayerPriorReport(
        source_rows_scanned=scanned,
        selected_source_rows=selected,
        prior_row_count=len(output),
        unresolved_identity_count=sum(
            row.identity_resolution_status is ResolutionStatus.UNRESOLVED
            for row in output
        ),
        conflict_identity_count=sum(
            row.identity_resolution_status is ResolutionStatus.CONFLICT
            for row in output
        ),
        counts_by_season=by_season,
        counts_by_domain=by_domain,
        source_files=tuple(source_files),
        rows=tuple(output),
    )


def _player_prior_payload(
    row: PlayerPriorRow,
    evidence_logical_uri_by_file_checksum: Mapping[str, str],
) -> Mapping[str, Any]:
    official_date = date(row.season, PLAYER_PRIOR_PARTITION_MONTH, 31).isoformat()
    source_rows = tuple(
        sorted(
            row.source_rows,
            key=lambda item: (
                item.logical_table,
                item.row_number,
                item.row_sha256,
            ),
        )
    )
    logical_uris = tuple(
        _logical_uri_for_row(item, evidence_logical_uri_by_file_checksum)
        for item in source_rows
    )
    file_checksums = tuple(sorted({item.file_sha256 for item in source_rows}))
    source_releases = tuple(sorted({item.source_release for item in source_rows}))
    source_tables = tuple(sorted({item.logical_table for item in source_rows}))
    canonical_player_id = (
        row.canonical_chadwick_uuid
        if row.identity_resolution_status is ResolutionStatus.EXACT
        else None
    )
    fallback_player_id = (
        None if canonical_player_id is not None else f"lahman:{row.lahman_player_id}"
    )
    payload = {
        "event_family": PLAYER_PRIOR_EVENT_FAMILY,
        "canonical_game_id": None,
        "canonical_player_id": canonical_player_id,
        "player_id": fallback_player_id,
        "lahman_player_id": row.lahman_player_id,
        "identity_resolution_status": row.identity_resolution_status.value,
        "season": row.season,
        "official_date": official_date,
        "official_date_semantics": "ANNUAL_REGULAR_SEASON_PERIOD_END",
        "population": row.population_label,
        "coverage_role": row.coverage_role.value,
        "domain": row.domain,
        "position": row.position,
        "eligible_from_season": row.eligible_from_season,
        "same_season_predictor_eligible": row.same_season_predictor_eligible,
        "team_ids": list(row.team_ids),
        "league_ids": list(row.league_ids),
        "metrics_json": json.dumps(
            dict(row.metrics),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ),
        "source_key": LAHMAN_SOURCE_KEY,
        "source_releases": list(source_releases),
        "source_logical_tables": list(source_tables),
        "source_logical_uris": list(logical_uris),
        "source_file_sha256": (
            file_checksums[0] if len(file_checksums) == 1 else None
        ),
        "source_file_sha256s": list(file_checksums),
        "source_row_numbers": [item.row_number for item in source_rows],
        "source_row_sha256s": [item.row_sha256 for item in source_rows],
        "source_derivation_checksum": row.derivation_checksum,
        "timing_classification": PLAYER_PRIOR_TIMING_CLASSIFICATION,
    }
    return MappingProxyType(payload)


def iter_player_prior_family_payloads(
    report: PlayerPriorReport,
    *,
    evidence_logical_uri_by_file_checksum: Mapping[str, str],
) -> Iterator[Mapping[str, Any]]:
    """Stream Parquet-row mappings in deterministic chronological order."""

    for row in report.rows:
        yield _player_prior_payload(row, evidence_logical_uri_by_file_checksum)


def _player_prior_partition_batch(
    *,
    season: int,
    batch_number: int,
    rows: Sequence[Mapping[str, Any]],
) -> PlayerPriorPartitionBatch:
    if not rows:
        raise FD1IdentityImportError("Cannot finalize an empty player-prior batch")
    population = str(rows[0]["population"])
    contributors: dict[tuple[str, str], set[tuple[int, str]]] = defaultdict(set)
    players: set[str] = set()
    for row in rows:
        identifier = row.get("canonical_player_id") or row.get("player_id")
        if not isinstance(identifier, str) or not identifier:
            raise FD1IdentityImportError("Player-prior payload lacks identity")
        players.add(identifier)
        uris = row.get("source_logical_uris")
        checksums = row.get("source_file_sha256s")
        row_numbers = row.get("source_row_numbers")
        row_checksums = row.get("source_row_sha256s")
        if not all(isinstance(value, list) for value in (uris, row_numbers, row_checksums)):
            raise FD1IdentityImportError("Player-prior source evidence is malformed")
        if not isinstance(checksums, list) or len(checksums) != 1:
            raise FD1IdentityImportError(
                "Each Lahman prior row must derive from one immutable source file"
            )
        if not (len(uris) == len(row_numbers) == len(row_checksums)):
            raise FD1IdentityImportError("Player-prior source evidence lengths differ")
        for uri, row_number, row_checksum in zip(
            uris, row_numbers, row_checksums, strict=True
        ):
            contributors[(str(uri), str(checksums[0]))].add(
                (int(row_number), str(row_checksum))
            )
    contributor_manifest = tuple(
        MappingProxyType(
            {
                "logical_uri": logical_uri,
                "file_sha256": file_checksum,
                "record_count": len(source_rows),
            }
        )
        for (logical_uri, file_checksum), source_rows in sorted(contributors.items())
    )
    frozen_rows = tuple(
        item if isinstance(item, MappingProxyType) else MappingProxyType(dict(item))
        for item in rows
    )
    contributor_checksum = _canonical_checksum(
        [dict(item) for item in contributor_manifest]
    )
    payload_checksum = _canonical_checksum([dict(item) for item in frozen_rows])
    period_end = date(season, PLAYER_PRIOR_PARTITION_MONTH, 31)
    return PlayerPriorPartitionBatch(
        event_family=PLAYER_PRIOR_EVENT_FAMILY,
        season=season,
        month=PLAYER_PRIOR_PARTITION_MONTH,
        population=population,
        batch_number=batch_number,
        rows=frozen_rows,
        row_count=len(frozen_rows),
        player_count=len(players),
        min_official_date=period_end,
        max_official_date=period_end,
        raw_contributor_manifest=contributor_manifest,
        raw_contributor_checksum=contributor_checksum,
        payload_checksum=payload_checksum,
    )


def iter_annual_player_prior_batches(
    report: PlayerPriorReport,
    *,
    evidence_logical_uri_by_file_checksum: Mapping[str, str],
    max_rows_per_batch: int = 25_000,
) -> Iterator[PlayerPriorPartitionBatch]:
    """Yield bounded annual prior batches on valid monthly store coordinates."""

    if (
        isinstance(max_rows_per_batch, bool)
        or not isinstance(max_rows_per_batch, int)
        or max_rows_per_batch <= 0
    ):
        raise FD1IdentityImportError("max_rows_per_batch must be positive")
    current_season: int | None = None
    current_rows: list[Mapping[str, Any]] = []
    batch_number = 0

    def flush() -> PlayerPriorPartitionBatch:
        assert current_season is not None
        return _player_prior_partition_batch(
            season=current_season,
            batch_number=batch_number,
            rows=current_rows,
        )

    for payload in iter_player_prior_family_payloads(
        report,
        evidence_logical_uri_by_file_checksum=evidence_logical_uri_by_file_checksum,
    ):
        season = int(payload["season"])
        if current_season is not None and season != current_season:
            if current_rows:
                batch_number += 1
                yield flush()
            current_rows = []
            batch_number = 0
        current_season = season
        current_rows.append(payload)
        if len(current_rows) == max_rows_per_batch:
            batch_number += 1
            yield flush()
            current_rows = []
    if current_rows:
        batch_number += 1
        yield flush()


def iter_monthly_player_prior_partition_batches(
    report: PlayerPriorReport,
    *,
    evidence_logical_uri_by_file_checksum: Mapping[str, str],
    max_rows_per_batch: int = 25_000,
) -> Iterator[PlayerPriorPartitionBatch]:
    """Yield December monthly partitions containing annual Lahman priors.

    No pseudo-monthly statistics are fabricated from annual source totals.
    """

    yield from iter_annual_player_prior_batches(
        report,
        evidence_logical_uri_by_file_checksum=evidence_logical_uri_by_file_checksum,
        max_rows_per_batch=max_rows_per_batch,
    )


def _group_source_rows(
    spec: SourceFileSpec,
    required_columns: Sequence[str],
    natural_key: Any,
    limits: ImportLimits,
) -> tuple[SourceFileEvidence, dict[Any, _OrganizationAccumulator]]:
    file_evidence, rows = _stream_csv_rows(
        spec, required_columns=required_columns, limits=limits
    )
    groups: dict[Any, _OrganizationAccumulator] = {}
    for row in rows:
        key = natural_key(row.values)
        if key not in groups:
            if len(groups) >= limits.max_organization_records:
                raise FD1IdentityImportError("Organization records exceed bound")
            groups[key] = _OrganizationAccumulator(dict(row.values), [])
        group = groups[key]
        if len(group.source_rows) >= limits.max_candidates_per_identifier:
            raise FD1IdentityImportError("Organization duplicate candidates exceed bound")
        group.source_rows.append(row.evidence)
    return file_evidence, groups


def _organization_checksum(entity: str, values: Any, rows: Sequence[SourceRowEvidence]) -> str:
    return _canonical_checksum(
        {
            "schema": FD1_IDENTITY_SCHEMA_VERSION,
            "entity": entity,
            "values": values,
            "source_rows": [row.row_sha256 for row in rows],
        }
    )


def _optional_date(value: str, field_name: str) -> date | None:
    if value == "":
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise FD1IdentityImportError(f"{field_name} must be ISO date or blank") from exc


def build_organization_crosswalk(
    *,
    teams: SourceFileSpec,
    franchises: SourceFileSpec,
    parks: SourceFileSpec,
    home_games: SourceFileSpec,
    limits: ImportLimits | None = None,
) -> OrganizationIdentityReport:
    """Import only identifier-backed Lahman team/franchise/park mappings."""

    bounds = limits or ImportLimits()
    for spec, table in (
        (teams, "Teams.csv"),
        (franchises, "TeamsFranchises.csv"),
        (parks, "Parks.csv"),
        (home_games, "HomeGames.csv"),
    ):
        _require_source(spec, LAHMAN_SOURCE_KEY, table)

    franchise_file, franchise_groups = _group_source_rows(
        franchises,
        ("franchID", "franchName", "active", "NAassoc"),
        lambda values: _identifier(values["franchID"], "franchID"),
        bounds,
    )
    park_file, park_groups = _group_source_rows(
        parks,
        ("parkkey", "parkalias", "parkname", "city", "state", "country"),
        lambda values: _identifier(values["parkkey"], "parkkey"),
        bounds,
    )
    team_file, team_groups = _group_source_rows(
        teams,
        (
            "yearID",
            "teamID",
            "franchID",
            "name",
            "teamIDBR",
            "teamIDlahman45",
            "teamIDretro",
        ),
        lambda values: (
            _parse_year(values["yearID"], "yearID"),
            _identifier(values["teamID"], "teamID"),
        ),
        bounds,
    )
    home_file, home_groups = _group_source_rows(
        home_games,
        (
            "yearkey",
            "leaguekey",
            "teamkey",
            "parkkey",
            "spanfirst",
            "spanlast",
            "games",
            "openings",
            "attendance",
        ),
        lambda values: (
            _parse_year(values["yearkey"], "yearkey"),
            _identifier(values["leaguekey"], "leaguekey"),
            _identifier(values["teamkey"], "teamkey"),
            _identifier(values["parkkey"], "parkkey"),
            values["spanfirst"],
            values["spanlast"],
        ),
        bounds,
    )

    franchise_records: list[FranchiseIdentity] = []
    for franchise_id in sorted(franchise_groups):
        item = franchise_groups[franchise_id]
        rows = tuple(item.source_rows)
        status = (
            ResolutionStatus.EXACT
            if len(rows) == 1
            else ResolutionStatus.CONFLICT
        )
        values = item.values
        franchise_records.append(
            FranchiseIdentity(
                canonical_franchise_id=f"LAHMAN_FRANCHISE:{franchise_id}",
                lahman_franchise_id=franchise_id,
                display_name=values["franchName"] or None,
                active_flag=values["active"] or None,
                national_association_id=values["NAassoc"] or None,
                resolution_status=status,
                source_rows=rows,
                derivation_checksum=_organization_checksum(
                    "franchise", values, rows
                ),
            )
        )
    franchises_by_id = {row.lahman_franchise_id: row for row in franchise_records}

    park_records: list[ParkIdentity] = []
    for park_id in sorted(park_groups):
        item = park_groups[park_id]
        values = item.values
        rows = tuple(item.source_rows)
        status = (
            ResolutionStatus.EXACT
            if len(rows) == 1
            else ResolutionStatus.CONFLICT
        )
        aliases = tuple(
            part.strip()
            for part in values["parkalias"].split(";")
            if part.strip()
        )
        park_records.append(
            ParkIdentity(
                canonical_park_id=f"LAHMAN_PARK:{park_id}",
                lahman_park_id=park_id,
                display_name=values["parkname"] or None,
                aliases=aliases,
                city=values["city"] or None,
                state=values["state"] or None,
                country=values["country"] or None,
                resolution_status=status,
                source_rows=rows,
                derivation_checksum=_organization_checksum("park", values, rows),
            )
        )
    parks_by_id = {row.lahman_park_id: row for row in park_records}

    team_claims: dict[tuple[int, str, str], set[tuple[int, str]]] = defaultdict(set)
    for (season, team_id), item in team_groups.items():
        for namespace, field_name in (
            ("LAHMAN_TEAM", "teamID"),
            ("BASEBALL_REFERENCE_TEAM", "teamIDBR"),
            ("LAHMAN_1945_TEAM", "teamIDlahman45"),
            ("RETROSHEET_TEAM", "teamIDretro"),
        ):
            value = item.values[field_name]
            if value:
                team_claims[(season, namespace, value)].add((season, team_id))

    team_records: list[TeamSeasonIdentity] = []
    for season, team_id in sorted(team_groups):
        item = team_groups[(season, team_id)]
        values = item.values
        rows = tuple(item.source_rows)
        franchise_id = _identifier(values["franchID"], "franchID")
        conflicts = tuple(
            sorted(
                namespace
                for namespace, field_name in (
                    ("LAHMAN_TEAM", "teamID"),
                    ("BASEBALL_REFERENCE_TEAM", "teamIDBR"),
                    ("LAHMAN_1945_TEAM", "teamIDlahman45"),
                    ("RETROSHEET_TEAM", "teamIDretro"),
                )
                if values[field_name]
                and len(team_claims[(season, namespace, values[field_name])]) > 1
            )
        )
        if len(rows) > 1 or conflicts:
            status = ResolutionStatus.CONFLICT
        elif franchise_id not in franchises_by_id or (
            franchises_by_id[franchise_id].resolution_status
            is not ResolutionStatus.EXACT
        ):
            status = ResolutionStatus.UNRESOLVED
        else:
            status = ResolutionStatus.EXACT
        canonical = f"LAHMAN_TEAM_SEASON:{season}:{team_id}"
        team_records.append(
            TeamSeasonIdentity(
                canonical_team_season_id=canonical,
                season=season,
                lahman_team_id=team_id,
                lahman_franchise_id=franchise_id,
                retrosheet_team_id=values["teamIDretro"] or None,
                baseball_reference_team_id=values["teamIDBR"] or None,
                lahman_1945_team_id=values["teamIDlahman45"] or None,
                display_name=values["name"] or None,
                resolution_status=status,
                conflict_namespaces=conflicts,
                source_rows=rows,
                derivation_checksum=_organization_checksum(
                    "team_season", {"canonical": canonical, **values}, rows
                ),
            )
        )
    teams_by_key = {(row.season, row.lahman_team_id): row for row in team_records}

    home_records: list[TeamParkLink] = []
    for natural in sorted(home_groups):
        item = home_groups[natural]
        values = item.values
        rows = tuple(item.source_rows)
        season = _parse_year(values["yearkey"], "yearkey")
        league = _identifier(values["leaguekey"], "leaguekey")
        team_id = _identifier(values["teamkey"], "teamkey")
        park_id = _identifier(values["parkkey"], "parkkey")
        team = teams_by_key.get((season, team_id))
        park = parks_by_id.get(park_id)
        if len(rows) > 1 or (
            team is not None
            and team.resolution_status is ResolutionStatus.CONFLICT
        ) or (
            park is not None
            and park.resolution_status is ResolutionStatus.CONFLICT
        ):
            status = ResolutionStatus.CONFLICT
        elif team is None or park is None or (
            team.resolution_status is not ResolutionStatus.EXACT
            or park.resolution_status is not ResolutionStatus.EXACT
        ):
            status = ResolutionStatus.UNRESOLVED
        else:
            status = ResolutionStatus.EXACT
        span_first = _optional_date(values["spanfirst"], "spanfirst")
        span_last = _optional_date(values["spanlast"], "spanlast")
        if span_first and span_last and span_last < span_first:
            raise FD1IdentityImportError("HomeGames span ends before it starts")
        home_records.append(
            TeamParkLink(
                season=season,
                league_id=league,
                lahman_team_id=team_id,
                lahman_park_id=park_id,
                canonical_team_season_id=(
                    team.canonical_team_season_id
                    if status is ResolutionStatus.EXACT and team is not None
                    else None
                ),
                canonical_park_id=(
                    park.canonical_park_id
                    if status is ResolutionStatus.EXACT and park is not None
                    else None
                ),
                span_first=span_first,
                span_last=span_last,
                games=_optional_nonnegative_int(values["games"], "games"),
                openings=_optional_nonnegative_int(values["openings"], "openings"),
                attendance=_optional_nonnegative_int(
                    values["attendance"], "attendance"
                ),
                resolution_status=status,
                source_rows=rows,
                derivation_checksum=_organization_checksum(
                    "team_park_link", values, rows
                ),
            )
        )

    all_records: Iterable[Any] = (
        *team_records,
        *franchise_records,
        *park_records,
        *home_records,
    )
    return OrganizationIdentityReport(
        team_season_count=len(team_records),
        franchise_count=len(franchise_records),
        park_count=len(park_records),
        team_park_link_count=len(home_records),
        exact_team_park_link_count=sum(
            row.resolution_status is ResolutionStatus.EXACT for row in home_records
        ),
        unresolved_team_park_link_count=sum(
            row.resolution_status is ResolutionStatus.UNRESOLVED
            for row in home_records
        ),
        conflict_count=sum(
            row.resolution_status is ResolutionStatus.CONFLICT for row in all_records
        ),
        name_based_match_count=0,
        source_files=(team_file, franchise_file, park_file, home_file),
        team_seasons=tuple(team_records),
        franchises=tuple(franchise_records),
        parks=tuple(park_records),
        team_park_links=tuple(home_records),
    )


def _optional_year(value: str, field_name: str) -> int | None:
    if value == "":
        return None
    return _parse_year(value, field_name)


def build_retrosheet_organization_crosswalk(
    organizations: OrganizationIdentityReport,
    *,
    retrosheet_teams: SourceFileSpec,
    retrosheet_ballparks: SourceFileSpec,
    limits: ImportLimits | None = None,
) -> RetrosheetOrganizationCrosswalkReport:
    """Corroborate Lahman organization IDs against Retrosheet catalogs.

    Team links require the exact Lahman ``teamIDretro`` value, an exact
    Retrosheet ``TEAM`` row, and a season inside that row's published
    ``FIRST``/``LAST`` window.  Park links use only exact ``parkkey``/``PARKID``
    equality.  Names, cities, aliases, and nicknames are never join keys.
    """

    bounds = limits or ImportLimits()
    _require_source(
        retrosheet_teams, RETROSHEET_AUXILIARY_SOURCE_KEY, "teams.csv"
    )
    _require_source(
        retrosheet_ballparks,
        RETROSHEET_AUXILIARY_SOURCE_KEY,
        "ballparks.csv",
    )
    retrosheet_team_file, retrosheet_team_groups = _group_source_rows(
        retrosheet_teams,
        ("TEAM", "LEAGUE", "CITY", "NICKNAME", "FIRST", "LAST"),
        lambda values: _identifier(values["TEAM"], "TEAM"),
        bounds,
    )
    retrosheet_park_file, retrosheet_park_groups = _group_source_rows(
        retrosheet_ballparks,
        ("PARKID", "NAME", "AKA", "CITY", "STATE", "START", "END"),
        lambda values: _identifier(values["PARKID"], "PARKID"),
        bounds,
    )

    team_records: list[RetrosheetTeamSeasonCrosswalk] = []
    claimed_retrosheet_team_ids: set[str] = set()
    for team in sorted(
        organizations.team_seasons,
        key=lambda item: (item.season, item.lahman_team_id),
    ):
        if team.retrosheet_team_id is None:
            continue
        retro_id = team.retrosheet_team_id
        claimed_retrosheet_team_ids.add(retro_id)
        retro = retrosheet_team_groups.get(retro_id)
        if len(team.source_rows) > 1 or "RETROSHEET_TEAM" in team.conflict_namespaces:
            status = ResolutionStatus.CONFLICT
        elif team.resolution_status is ResolutionStatus.UNRESOLVED:
            status = ResolutionStatus.UNRESOLVED
        elif retro is None:
            status = ResolutionStatus.UNRESOLVED
        elif len(retro.source_rows) > 1:
            status = ResolutionStatus.CONFLICT
        else:
            first = _optional_year(retro.values["FIRST"], "FIRST")
            last = _optional_year(retro.values["LAST"], "LAST")
            if first is None or last is None:
                status = ResolutionStatus.UNRESOLVED
            elif last < first:
                raise FD1IdentityImportError(
                    f"Retrosheet team {retro_id} LAST precedes FIRST"
                )
            elif not first <= team.season <= last:
                status = ResolutionStatus.CONFLICT
            else:
                status = ResolutionStatus.EXACT
        source_rows = tuple(
            sorted(
                (*team.source_rows, *(retro.source_rows if retro else ())),
                key=lambda item: (
                    item.source_key,
                    item.logical_table,
                    item.row_number,
                    item.row_sha256,
                ),
            )
        )
        canonical = (
            team.canonical_team_season_id
            if status is ResolutionStatus.EXACT
            else None
        )
        values = {
            "season": team.season,
            "retrosheet_team_id": retro_id,
            "canonical_team_season_id": canonical,
            "status": status.value,
        }
        team_records.append(
            RetrosheetTeamSeasonCrosswalk(
                season=team.season,
                retrosheet_team_id=retro_id,
                canonical_team_season_id=canonical,
                resolution_status=status,
                source_rows=source_rows,
                derivation_checksum=_organization_checksum(
                    "retrosheet_team_season", values, source_rows
                ),
            )
        )

    lahman_parks = {
        park.lahman_park_id: park for park in organizations.parks
    }
    park_records: list[RetrosheetParkCrosswalk] = []
    for retro_id in sorted(retrosheet_park_groups):
        retro = retrosheet_park_groups[retro_id]
        lahman = lahman_parks.get(retro_id)
        if len(retro.source_rows) > 1:
            status = ResolutionStatus.CONFLICT
        elif lahman is None:
            status = ResolutionStatus.UNRESOLVED
        elif lahman.resolution_status is ResolutionStatus.CONFLICT:
            status = ResolutionStatus.CONFLICT
        elif lahman.resolution_status is ResolutionStatus.UNRESOLVED:
            status = ResolutionStatus.UNRESOLVED
        else:
            status = ResolutionStatus.EXACT
        source_rows = tuple(
            sorted(
                (
                    *retro.source_rows,
                    *(lahman.source_rows if lahman is not None else ()),
                ),
                key=lambda item: (
                    item.source_key,
                    item.logical_table,
                    item.row_number,
                    item.row_sha256,
                ),
            )
        )
        canonical = (
            lahman.canonical_park_id
            if status is ResolutionStatus.EXACT and lahman is not None
            else None
        )
        values = {
            "retrosheet_park_id": retro_id,
            "canonical_park_id": canonical,
            "status": status.value,
        }
        park_records.append(
            RetrosheetParkCrosswalk(
                retrosheet_park_id=retro_id,
                canonical_park_id=canonical,
                resolution_status=status,
                source_rows=source_rows,
                derivation_checksum=_organization_checksum(
                    "retrosheet_park", values, source_rows
                ),
            )
        )

    source_files_by_checksum = {
        item.content_sha256: item
        for item in (
            *organizations.source_files,
            retrosheet_team_file,
            retrosheet_park_file,
        )
    }
    return RetrosheetOrganizationCrosswalkReport(
        team_season_count=len(team_records),
        exact_team_season_count=sum(
            item.resolution_status is ResolutionStatus.EXACT
            for item in team_records
        ),
        unresolved_team_season_count=sum(
            item.resolution_status is ResolutionStatus.UNRESOLVED
            for item in team_records
        ),
        conflict_team_season_count=sum(
            item.resolution_status is ResolutionStatus.CONFLICT
            for item in team_records
        ),
        park_count=len(park_records),
        exact_park_count=sum(
            item.resolution_status is ResolutionStatus.EXACT
            for item in park_records
        ),
        unresolved_park_count=sum(
            item.resolution_status is ResolutionStatus.UNRESOLVED
            for item in park_records
        ),
        conflict_park_count=sum(
            item.resolution_status is ResolutionStatus.CONFLICT
            for item in park_records
        ),
        unlinked_retrosheet_team_id_count=len(
            set(retrosheet_team_groups).difference(claimed_retrosheet_team_ids)
        ),
        unlinked_lahman_park_count=len(
            set(lahman_parks).difference(retrosheet_park_groups)
        ),
        name_based_match_count=0,
        source_files=tuple(
            source_files_by_checksum[key]
            for key in sorted(source_files_by_checksum)
        ),
        team_seasons=tuple(team_records),
        parks=tuple(park_records),
    )


def _first_source_row(
    rows: Sequence[SourceRowEvidence], logical_table: str
) -> SourceRowEvidence:
    selected = sorted(
        (row for row in rows if row.logical_table == logical_table),
        key=lambda item: (item.row_number, item.row_sha256),
    )
    if not selected:
        raise FD1IdentityImportError(
            f"Identity relation lacks {logical_table} row evidence"
        )
    return selected[0]


def iter_registry_organization_identity_mapping_specs(
    report: OrganizationIdentityReport,
    *,
    evidence_logical_uri_by_file_checksum: Mapping[str, str],
) -> Iterator[OpenDataIdentityMappingSpec]:
    """Yield row-bound Lahman team-season, franchise, and park assertions."""

    specs: list[OpenDataIdentityMappingSpec] = []
    for team in report.team_seasons:
        row = _first_source_row(team.source_rows, "Teams.csv")
        for internal_namespace, source_namespace, external_value in (
            ("LAHMAN_TEAM", "lahman_team_season", team.lahman_team_id),
            (
                "BASEBALL_REFERENCE_TEAM",
                "baseball_reference_team_season",
                team.baseball_reference_team_id,
            ),
            (
                "LAHMAN_1945_TEAM",
                "lahman_1945_team_season",
                team.lahman_1945_team_id,
            ),
        ):
            if external_value is None:
                continue
            if len(team.source_rows) > 1 or internal_namespace in team.conflict_namespaces:
                status = ResolutionStatus.CONFLICT
            elif team.resolution_status is ResolutionStatus.UNRESOLVED:
                status = ResolutionStatus.UNRESOLVED
            else:
                # A conflict isolated to another external namespace does not
                # invalidate this exact source-field assertion.
                status = ResolutionStatus.EXACT
            specs.append(
                OpenDataIdentityMappingSpec(
                    source_key=row.source_key,
                    entity_type="team",
                    source_namespace=source_namespace,
                    external_id=f"{team.season}:{external_value}",
                    canonical_namespace="lahman_team_season",
                    canonical_id=(
                        team.canonical_team_season_id
                        if status is ResolutionStatus.EXACT
                        else None
                    ),
                    source_row_number=row.row_number,
                    source_row_checksum=row.row_sha256,
                    confidence=status.value,
                    evidence_logical_uri=_logical_uri_for_row(
                        row, evidence_logical_uri_by_file_checksum
                    ),
                    valid_from=date(team.season, 1, 1),
                    valid_through=date(team.season, 12, 31),
                )
            )
    for franchise in report.franchises:
        row = _first_source_row(franchise.source_rows, "TeamsFranchises.csv")
        specs.append(
            OpenDataIdentityMappingSpec(
                source_key=row.source_key,
                entity_type="franchise",
                source_namespace="lahman_franchise",
                external_id=franchise.lahman_franchise_id,
                canonical_namespace="lahman_franchise",
                canonical_id=(
                    franchise.canonical_franchise_id
                    if franchise.resolution_status is ResolutionStatus.EXACT
                    else None
                ),
                source_row_number=row.row_number,
                source_row_checksum=row.row_sha256,
                confidence=franchise.resolution_status.value,
                evidence_logical_uri=_logical_uri_for_row(
                    row, evidence_logical_uri_by_file_checksum
                ),
            )
        )
    for park in report.parks:
        row = _first_source_row(park.source_rows, "Parks.csv")
        specs.append(
            OpenDataIdentityMappingSpec(
                source_key=row.source_key,
                entity_type="park",
                source_namespace="lahman_park",
                external_id=park.lahman_park_id,
                canonical_namespace="lahman_park",
                canonical_id=(
                    park.canonical_park_id
                    if park.resolution_status is ResolutionStatus.EXACT
                    else None
                ),
                source_row_number=row.row_number,
                source_row_checksum=row.row_sha256,
                confidence=park.resolution_status.value,
                evidence_logical_uri=_logical_uri_for_row(
                    row, evidence_logical_uri_by_file_checksum
                ),
            )
        )
    yield from sorted(
        specs,
        key=lambda item: (
            item.entity_type,
            item.source_namespace,
            item.external_id,
            item.source_row_number,
        ),
    )


def iter_registry_retrosheet_organization_identity_mapping_specs(
    report: RetrosheetOrganizationCrosswalkReport,
    *,
    evidence_logical_uri_by_file_checksum: Mapping[str, str],
) -> Iterator[OpenDataIdentityMappingSpec]:
    """Yield cross-source Retrosheet team-season and park assertions."""

    specs: list[OpenDataIdentityMappingSpec] = []
    for team in report.team_seasons:
        # Teams.csv is the season-specific row publishing teamIDretro.  The
        # Retrosheet catalog row remains in the crosswalk's full evidence set.
        row = _first_source_row(team.source_rows, "Teams.csv")
        specs.append(
            OpenDataIdentityMappingSpec(
                source_key=row.source_key,
                entity_type="team",
                source_namespace="retrosheet_team_season",
                external_id=f"{team.season}:{team.retrosheet_team_id}",
                canonical_namespace="lahman_team_season",
                canonical_id=team.canonical_team_season_id,
                source_row_number=row.row_number,
                source_row_checksum=row.row_sha256,
                confidence=team.resolution_status.value,
                evidence_logical_uri=_logical_uri_for_row(
                    row, evidence_logical_uri_by_file_checksum
                ),
                valid_from=date(team.season, 1, 1),
                valid_through=date(team.season, 12, 31),
            )
        )
    for park in report.parks:
        row = _first_source_row(park.source_rows, "ballparks.csv")
        specs.append(
            OpenDataIdentityMappingSpec(
                source_key=row.source_key,
                entity_type="park",
                source_namespace="retrosheet_park",
                external_id=park.retrosheet_park_id,
                canonical_namespace="lahman_park",
                canonical_id=park.canonical_park_id,
                source_row_number=row.row_number,
                source_row_checksum=row.row_sha256,
                confidence=park.resolution_status.value,
                evidence_logical_uri=_logical_uri_for_row(
                    row, evidence_logical_uri_by_file_checksum
                ),
            )
        )
    yield from sorted(
        specs,
        key=lambda item: (
            item.entity_type,
            item.source_namespace,
            item.external_id,
            item.source_row_number,
        ),
    )


def iter_registry_organization_identity_mapping_batches(
    report: OrganizationIdentityReport,
    *,
    evidence_logical_uri_by_file_checksum: Mapping[str, str],
    retrosheet_report: RetrosheetOrganizationCrosswalkReport | None = None,
    batch_size: int = 10_000,
) -> Iterator[RegistryIdentityMappingBatch]:
    """Yield bounded deterministic organization mapping batches."""

    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size <= 0:
        raise FD1IdentityImportError("identity mapping batch_size must be positive")
    specs = list(
        iter_registry_organization_identity_mapping_specs(
            report,
            evidence_logical_uri_by_file_checksum=evidence_logical_uri_by_file_checksum,
        )
    )
    if retrosheet_report is not None:
        specs.extend(
            iter_registry_retrosheet_organization_identity_mapping_specs(
                retrosheet_report,
                evidence_logical_uri_by_file_checksum=(
                    evidence_logical_uri_by_file_checksum
                ),
            )
        )
    ordered = sorted(
        specs,
        key=lambda item: (
            item.source_key,
            item.entity_type,
            item.source_namespace,
            item.external_id,
            item.source_row_number,
        ),
    )
    for index in range(0, len(ordered), batch_size):
        current = ordered[index : index + batch_size]
        yield RegistryIdentityMappingBatch(
            batch_number=(index // batch_size) + 1,
            specs=tuple(current),
            batch_checksum=_canonical_checksum(
                [_identity_mapping_spec_payload(item) for item in current]
            ),
        )


def describe_lahman_release_limitations(
    published_tables: Iterable[str],
) -> LahmanReleaseLimitations:
    """Return explicit, zero-fabrication limitations for the 2025 release."""

    published = tuple(sorted(published_tables))
    for name in published:
        _required_text(name, "published table name", 160)
        if Path(name).name != name:
            raise FD1IdentityImportError("published table must be a base file name")
    if len(set(published)) != len(published):
        raise FD1IdentityImportError("published table names must be unique")
    limitations = tuple(
        SourceLimitation(
            table_name=table,
            status="SOURCE_RELEASE_NOT_PUBLISHED",
            reason=(
                f"{table} is not present in the official SABR Lahman 2025 "
                "published CSV set; no replacement rows are inferred or fabricated."
            ),
            substitution_permitted=False,
            fabricated_row_count=0,
        )
        for table in EXPECTED_MISSING_LAHMAN_TABLES
        if table not in published
    )
    return LahmanReleaseLimitations(
        release=LAHMAN_RELEASE,
        published_tables=published,
        missing_expected_tables=limitations,
    )


def import_fd1_identity_priors(
    bundle: FD1IdentityPriorSourceBundle,
    *,
    limits: ImportLimits | None = None,
) -> FD1IdentityPriorImport:
    """Run the complete provider-free identity/prior import in memory."""

    bounds = limits or ImportLimits()
    people = build_person_crosswalk(
        bundle.chadwick_people,
        bundle.lahman_people,
        limits=bounds,
    )
    priors = build_player_priors(
        (
            bundle.lahman_batting,
            bundle.lahman_pitching,
            bundle.lahman_fielding,
        ),
        crosswalk=people,
        limits=bounds,
    )
    organizations = build_organization_crosswalk(
        teams=bundle.lahman_teams,
        franchises=bundle.lahman_franchises,
        parks=bundle.lahman_parks,
        home_games=bundle.lahman_home_games,
        limits=bounds,
    )
    limitations = describe_lahman_release_limitations(
        bundle.published_lahman_tables
    )
    return FD1IdentityPriorImport(
        schema_version=FD1_IDENTITY_SCHEMA_VERSION,
        people=people,
        priors=priors,
        organizations=organizations,
        limitations=limitations,
    )


__all__ = [
    "CHADWICK_RELEASE",
    "CHADWICK_SOURCE_KEY",
    "EXPECTED_MISSING_LAHMAN_TABLES",
    "FD1_IDENTITY_SCHEMA_VERSION",
    "GEN2_PRIMARY_POPULATION",
    "GEN2_PRIOR_ONLY_POPULATION",
    "LAHMAN_RELEASE",
    "LAHMAN_SOURCE_KEY",
    "RETROSHEET_AUXILIARY_SOURCE_KEY",
    "PRIMARY_SEASONS",
    "PRIOR_ONLY_SEASONS",
    "PRIOR_SEASONS",
    "PLAYER_PRIOR_EVENT_FAMILY",
    "PLAYER_PRIOR_PARTITION_MONTH",
    "PLAYER_PRIOR_TIMING_CLASSIFICATION",
    "CountByKey",
    "ExternalIdentifier",
    "FD1IdentityImportError",
    "FD1IdentityPriorImport",
    "FD1IdentityPriorSourceBundle",
    "FranchiseIdentity",
    "ImportLimits",
    "LahmanPersonCrosswalk",
    "LahmanReleaseLimitations",
    "NamespaceCount",
    "OrganizationIdentityReport",
    "ParkIdentity",
    "PersonCrosswalkReport",
    "PersonNamespaceAssertion",
    "PlayerPriorReport",
    "PlayerPriorRow",
    "PlayerPriorPartitionBatch",
    "PriorCoverageRole",
    "ResolutionStatus",
    "RetrosheetOrganizationCrosswalkReport",
    "RetrosheetParkCrosswalk",
    "RetrosheetTeamSeasonCrosswalk",
    "RegistryIdentityMappingBatch",
    "SourceFileEvidence",
    "SourceFileSpec",
    "SourceLimitation",
    "SourceRowEvidence",
    "TeamParkLink",
    "TeamSeasonIdentity",
    "build_organization_crosswalk",
    "build_person_crosswalk",
    "build_retrosheet_organization_crosswalk",
    "build_player_priors",
    "describe_lahman_release_limitations",
    "import_fd1_identity_priors",
    "iter_annual_player_prior_batches",
    "iter_all_registry_person_identity_mapping_batches",
    "iter_all_registry_person_identity_mapping_specs",
    "iter_monthly_player_prior_partition_batches",
    "iter_player_prior_family_payloads",
    "iter_registry_identity_mapping_batches",
    "iter_registry_identity_mapping_specs",
    "iter_registry_organization_identity_mapping_batches",
    "iter_registry_organization_identity_mapping_specs",
    "iter_registry_retrosheet_organization_identity_mapping_specs",
]

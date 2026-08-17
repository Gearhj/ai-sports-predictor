"""Package-only identity evidence scanner for the frozen MLB FD1 parent.

The scanner deliberately starts with the existing full exchange validator.  It
then reads only Parquet files declared by that validated manifest.  There is no
settings, database, provider, raw-source, sibling-application, or network import
path in this module, and the scan performs no filesystem writes.

Two Retrosheet inventories are kept separate:

* ``MINIMUM_JOIN_FIELD_SPECS`` covers every player-bearing field consumers need
  to join the manifested families.
* ``GOVERNED_REFERENCE_FIELD_SPECS`` is a non-duplicating projection of the
  original Retrosheet ``plays``, ``batting``, ``pitching``, and ``fielding``
  evidence physically retained by the exchange.  It intentionally excludes
  derived duplicate families and raw/normalized mirror columns.

The frozen active-identity denominator was computed from five upstream CSV
tables.  The parent package does not export the complete ``allplayers`` row set
or every player cell from ``plays`` (notably LOB, responsible-pitcher, and
fielding-position cells).  Consequently the typed result reports whether the
physical governed inventory reconciles the frozen 2,693 / 21,356,559 claim; it
never manufactures missing references or treats equal cardinalities as a join.
"""

from __future__ import annotations

import math
import re
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import polars as pl

from app.gen2.exchange.canonical_json import load_canonical_json
from app.gen2.exchange.contracts import (
    CANONICAL_PA_CLASS_ORDER,
    EXPECTED_PARTITION_AUTHORITY_SHA256,
    SportsDatasetManifestV1,
    partition_authority_sha256,
)
from app.gen2.exchange.filesystem import resolve_under
from app.gen2.exchange.identity_bridge.trust import (
    EXPECTED_PA_CLASS_ORDER,
    EXPECTED_PARENT_DATASET_ID,
    EXPECTED_PARENT_DATASET_VERSION_ID,
    EXPECTED_PARENT_MANIFEST_ID,
    EXPECTED_PARENT_MANIFEST_SHA256,
    EXPECTED_PARENT_PACKAGE_ID,
    EXPECTED_PARENT_PARTITION_AUTHORITY_SHA256,
)
from app.gen2.exchange.validator import (
    ExchangeValidationDeadline,
    ValidationProgress,
    validate_sports_dataset_exchange,
)


_RETROSHEET_PREFIX = "retrosheet:player:"
_RAW_RETROSHEET_ID = re.compile(r"^[a-z-]{5}[0-9]{3}$")
_NORMALIZED_RETROSHEET_ID = re.compile(
    rf"^{re.escape(_RETROSHEET_PREFIX)}[a-z-]{{5}}[0-9]{{3}}$"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LAHMAN_PLAYER_ID = re.compile(r"^[a-z0-9]{7,9}$")
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_ACTIVE_SEASONS = (2021, 2022, 2023, 2024, 2025)
_PRIMARY_SEASONS = frozenset({2023, 2024, 2025})
_EXPECTED_RETROSHEET_SOURCE_KEY = "RETROSHEET_CSV_DOWNLOADS"
_EXPECTED_RETROSHEET_SOURCE_VERSION = "64db602a-090a-4e37-b8f2-2a48d36dd7f2"
_EXPECTED_LAHMAN_SOURCE_KEY = "SABR_LAHMAN_BOX_DOWNLOADS"
_EXPECTED_LAHMAN_SOURCE_VERSION = "3af682f0-ecdb-47d8-8e14-660ec5f7455c"
_EXPECTED_LAHMAN_RELEASE = "lahman_1871-2025_csv"
_EXPECTED_PRIOR_DOMAIN_TABLE = MappingProxyType(
    {
        "BATTING": "Batting.csv",
        "FIELDING": "Fielding.csv",
        "PITCHING": "Pitching.csv",
    }
)


class ParentIdentityScanError(RuntimeError):
    """The validated parent cannot support an exact identity evidence scan."""


class ParentIdentityScanDeadline(ParentIdentityScanError):
    """The package-only identity scan reached its caller-supplied deadline."""

    def __init__(self, progress: Mapping[str, Any]):
        super().__init__("Parent identity scan reached its safe runtime bound")
        self.progress = dict(progress)


@dataclass(frozen=True, slots=True)
class ParentScanProgress:
    stage: str
    elapsed_seconds: float
    files_checked: int
    files_total: int
    current_family: str | None
    references_checked: int


@dataclass(frozen=True, slots=True)
class ParentIdentityFieldSpec:
    """One normalized player field and its optional raw Retrosheet mirror."""

    family: str
    field: str
    kind: str
    raw_mirror_field: str | None = None
    required: bool = True
    exact_list_length: int | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"SCALAR", "LIST"}:
            raise ValueError("Identity field kind must be SCALAR or LIST")
        if not self.family or not self.field:
            raise ValueError("Identity field family/name cannot be empty")
        if self.exact_list_length is not None and (
            self.kind != "LIST" or self.exact_list_length <= 0
        ):
            raise ValueError("Only list fields may declare a positive exact length")


@dataclass(frozen=True, slots=True)
class FamilyFieldSeasonEvidence:
    family: str
    field: str
    raw_mirror_field: str | None
    season: int
    reference_count: int
    unique_player_count: int


@dataclass(frozen=True, slots=True)
class PlateAppearanceRoleEvidence:
    retrosheet_player_id: str
    season: int
    batter_references: int
    pitcher_references: int


@dataclass(frozen=True, slots=True)
class PlayerFamilyCoverage:
    retrosheet_player_id: str
    family: str
    first_season: int
    last_season: int
    reference_count: int
    fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RetrosheetPlayerEvidence:
    retrosheet_player_id: str
    first_season: int
    last_season: int
    total_join_references: int
    plate_appearance_batter_references: int
    plate_appearance_pitcher_references: int
    seasons: tuple[int, ...]
    families: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlayerPriorEvidence:
    """Lossless typed projection of one ``player_prior`` Parquet row."""

    event_family: str
    canonical_game_id: None
    canonical_player_id: str
    player_id: None
    lahman_player_id: str
    identity_resolution_status: str
    season: int
    official_date: str
    official_date_semantics: str
    population: str
    coverage_role: str
    domain: str
    position: str | None
    eligible_from_season: int
    same_season_predictor_eligible: bool
    team_ids: tuple[str, ...]
    league_ids: tuple[str, ...]
    metrics_json: str
    source_key: str
    source_releases: tuple[str, ...]
    source_logical_tables: tuple[str, ...]
    source_logical_uris: tuple[str, ...]
    source_file_sha256: str
    source_file_sha256s: tuple[str, ...]
    source_row_numbers: tuple[int, ...]
    source_row_sha256s: tuple[str, ...]
    source_derivation_checksum: str
    timing_classification: str
    source_version_id: str
    source_partition_uri: str
    source_partition_sha256: str


@dataclass(frozen=True, slots=True)
class ReferenceCoverageEvidence:
    reference_count: int
    unique_player_count: int
    primary_reference_count: int
    primary_unique_player_count: int
    expected_reference_count: int
    expected_unique_player_count: int
    expected_primary_reference_count: int
    expected_primary_unique_player_count: int
    counts_reconcile: bool


@dataclass(frozen=True, slots=True)
class ParentIdentityEvidence:
    parent_root: Path
    parent_manifest_id: str
    parent_manifest_sha256: str
    parent_dataset_version_id: str
    parent_package_id: str
    parent_partition_authority_sha256: str
    pa_class_order: tuple[str, ...]
    minimum_join_field_specs: tuple[ParentIdentityFieldSpec, ...]
    governed_reference_field_specs: tuple[ParentIdentityFieldSpec, ...]
    field_season_evidence: tuple[FamilyFieldSeasonEvidence, ...]
    players: tuple[RetrosheetPlayerEvidence, ...]
    plate_appearance_role_evidence: tuple[PlateAppearanceRoleEvidence, ...]
    other_family_coverage: tuple[PlayerFamilyCoverage, ...]
    player_priors: tuple[PlayerPriorEvidence, ...]
    all_retrosheet_player_ids: tuple[str, ...]
    primary_retrosheet_player_ids: tuple[str, ...]
    minimum_join_coverage: ReferenceCoverageEvidence
    governed_reference_coverage: ReferenceCoverageEvidence
    governed_counts_reconcile: bool


@dataclass(frozen=True, slots=True)
class _FamilyAnchor:
    schema_id: str
    file_count: int
    row_count: int
    byte_count: int
    minimum_date: str
    maximum_date: str
    source_key: str
    source_version_id: str


_FAMILY_ANCHORS: Mapping[str, _FamilyAnchor] = MappingProxyType(
    {
        "bullpen_usage": _FamilyAnchor(
            "retrosheet_fd1_bullpen_usage_v6",
            36,
            80_314,
            9_902_702,
            "2021-04-01",
            "2025-09-28",
            _EXPECTED_RETROSHEET_SOURCE_KEY,
            _EXPECTED_RETROSHEET_SOURCE_VERSION,
        ),
        "pitch_sequence": _FamilyAnchor(
            "retrosheet_fd1_pitch_sequence_v5",
            36,
            943_682,
            80_347_024,
            "2021-04-01",
            "2025-09-28",
            _EXPECTED_RETROSHEET_SOURCE_KEY,
            _EXPECTED_RETROSHEET_SOURCE_VERSION,
        ),
        "plate_appearance": _FamilyAnchor(
            "retrosheet_fd1_plate_appearance_v5",
            36,
            913_349,
            79_421_951,
            "2021-04-01",
            "2025-09-28",
            _EXPECTED_RETROSHEET_SOURCE_KEY,
            _EXPECTED_RETROSHEET_SOURCE_VERSION,
        ),
        "play_transition": _FamilyAnchor(
            "retrosheet_fd1_play_transition_v5",
            36,
            949_217,
            88_260_346,
            "2021-04-01",
            "2025-09-28",
            _EXPECTED_RETROSHEET_SOURCE_KEY,
            _EXPECTED_RETROSHEET_SOURCE_VERSION,
        ),
        "player_game_batting": _FamilyAnchor(
            "retrosheet_fd1_player_game_batting_v5",
            36,
            356_498,
            30_584_501,
            "2021-04-01",
            "2025-09-28",
            _EXPECTED_RETROSHEET_SOURCE_KEY,
            _EXPECTED_RETROSHEET_SOURCE_VERSION,
        ),
        "player_game_fielding": _FamilyAnchor(
            "retrosheet_fd1_player_game_fielding_v5",
            36,
            332_159,
            27_997_674,
            "2021-04-01",
            "2025-09-28",
            _EXPECTED_RETROSHEET_SOURCE_KEY,
            _EXPECTED_RETROSHEET_SOURCE_VERSION,
        ),
        "player_game_pitching": _FamilyAnchor(
            "retrosheet_fd1_player_game_pitching_v5",
            36,
            104_610,
            10_736_025,
            "2021-04-01",
            "2025-09-28",
            _EXPECTED_RETROSHEET_SOURCE_KEY,
            _EXPECTED_RETROSHEET_SOURCE_VERSION,
        ),
        "player_prior": _FamilyAnchor(
            "gate3c_fd1_identity_prior_v1",
            5,
            21_823,
            2_512_357,
            "2021-12-31",
            "2025-12-31",
            _EXPECTED_LAHMAN_SOURCE_KEY,
            _EXPECTED_LAHMAN_SOURCE_VERSION,
        ),
        "starter_usage": _FamilyAnchor(
            "retrosheet_fd1_starter_usage_v6",
            36,
            24_296,
            4_558_900,
            "2021-04-01",
            "2025-09-28",
            _EXPECTED_RETROSHEET_SOURCE_KEY,
            _EXPECTED_RETROSHEET_SOURCE_VERSION,
        ),
    }
)


MINIMUM_JOIN_FIELD_SPECS = (
    ParentIdentityFieldSpec("pitch_sequence", "batter_id", "SCALAR"),
    ParentIdentityFieldSpec("pitch_sequence", "pitcher_id", "SCALAR"),
    ParentIdentityFieldSpec(
        "pitch_sequence",
        "automatic_runner_player_id",
        "SCALAR",
        "retrosheet_automatic_runner_id",
        required=False,
    ),
    ParentIdentityFieldSpec("plate_appearance", "batter_id", "SCALAR"),
    ParentIdentityFieldSpec("plate_appearance", "pitcher_id", "SCALAR"),
    ParentIdentityFieldSpec(
        "plate_appearance",
        "automatic_runner_player_id",
        "SCALAR",
        "retrosheet_automatic_runner_id",
        required=False,
    ),
    ParentIdentityFieldSpec("play_transition", "batter_id", "SCALAR"),
    ParentIdentityFieldSpec("play_transition", "pitcher_id", "SCALAR"),
    ParentIdentityFieldSpec(
        "play_transition",
        "automatic_runner_player_id",
        "SCALAR",
        "retrosheet_automatic_runner_id",
        required=False,
    ),
    ParentIdentityFieldSpec(
        "play_transition",
        "lineup_player_ids",
        "LIST",
        "retrosheet_lineup_slots",
        exact_list_length=9,
    ),
    ParentIdentityFieldSpec(
        "play_transition", "runner_on_first_before", "SCALAR", required=False
    ),
    ParentIdentityFieldSpec(
        "play_transition", "runner_on_second_before", "SCALAR", required=False
    ),
    ParentIdentityFieldSpec(
        "play_transition", "runner_on_third_before", "SCALAR", required=False
    ),
    ParentIdentityFieldSpec(
        "play_transition", "runner_on_first_after", "SCALAR", required=False
    ),
    ParentIdentityFieldSpec(
        "play_transition", "runner_on_second_after", "SCALAR", required=False
    ),
    ParentIdentityFieldSpec(
        "play_transition", "runner_on_third_after", "SCALAR", required=False
    ),
    ParentIdentityFieldSpec(
        "player_game_batting", "player_id", "SCALAR", "retrosheet_player_id"
    ),
    ParentIdentityFieldSpec(
        "player_game_fielding", "player_id", "SCALAR", "retrosheet_player_id"
    ),
    ParentIdentityFieldSpec(
        "player_game_pitching", "player_id", "SCALAR", "retrosheet_player_id"
    ),
    ParentIdentityFieldSpec(
        "starter_usage", "player_id", "SCALAR", "retrosheet_player_id"
    ),
    ParentIdentityFieldSpec(
        "bullpen_usage", "player_id", "SCALAR", "retrosheet_player_id"
    ),
)


# One retained physical projection for each non-duplicated upstream source cell.
# Automatic-runner values are already represented by the base-runner state and
# are excluded here.  PA/pitch-sequence and usage families are derived mirrors
# of play/pitching evidence and are likewise excluded from the denominator.
_GOVERNED_KEYS = frozenset(
    {
        ("play_transition", "batter_id"),
        ("play_transition", "pitcher_id"),
        ("play_transition", "lineup_player_ids"),
        ("play_transition", "runner_on_first_before"),
        ("play_transition", "runner_on_second_before"),
        ("play_transition", "runner_on_third_before"),
        ("play_transition", "runner_on_first_after"),
        ("play_transition", "runner_on_second_after"),
        ("play_transition", "runner_on_third_after"),
        ("player_game_batting", "player_id"),
        ("player_game_fielding", "player_id"),
        ("player_game_pitching", "player_id"),
    }
)
GOVERNED_REFERENCE_FIELD_SPECS = tuple(
    spec
    for spec in MINIMUM_JOIN_FIELD_SPECS
    if (spec.family, spec.field) in _GOVERNED_KEYS
)


_PLAYER_PRIOR_PHYSICAL_SCHEMA = (
    ("event_family", "String"),
    ("canonical_game_id", "Null"),
    ("canonical_player_id", "String"),
    ("player_id", "Null"),
    ("lahman_player_id", "String"),
    ("identity_resolution_status", "String"),
    ("season", "Int64"),
    ("official_date", "String"),
    ("official_date_semantics", "String"),
    ("population", "String"),
    ("coverage_role", "String"),
    ("domain", "String"),
    ("position", "String"),
    ("eligible_from_season", "Int64"),
    ("same_season_predictor_eligible", "Boolean"),
    ("team_ids", "List(String)"),
    ("league_ids", "List(String)"),
    ("metrics_json", "String"),
    ("source_key", "String"),
    ("source_releases", "List(String)"),
    ("source_logical_tables", "List(String)"),
    ("source_logical_uris", "List(String)"),
    ("source_file_sha256", "String"),
    ("source_file_sha256s", "List(String)"),
    ("source_row_numbers", "List(Int64)"),
    ("source_row_sha256s", "List(String)"),
    ("source_derivation_checksum", "String"),
    ("timing_classification", "String"),
)
PLAYER_PRIOR_PHYSICAL_SCHEMA = _PLAYER_PRIOR_PHYSICAL_SCHEMA


def scan_parent_identity(
    parent_root: Path,
    *,
    heartbeat_seconds: int = 30,
    max_runtime_minutes: float = 90,
    progress_callback: Callable[[ParentScanProgress], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> ParentIdentityEvidence:
    """Validate and scan the exact frozen parent using package bytes only."""

    if isinstance(heartbeat_seconds, bool) or not 1 <= heartbeat_seconds <= 30:
        raise ValueError("heartbeat_seconds must be between 1 and 30")
    if (
        isinstance(max_runtime_minutes, bool)
        or not isinstance(max_runtime_minutes, (int, float))
        or max_runtime_minutes <= 0
        or (
            isinstance(max_runtime_minutes, float)
            and not math.isfinite(max_runtime_minutes)
        )
    ):
        raise ValueError("max_runtime_minutes must be positive and finite")
    started = clock()
    deadline = started + float(max_runtime_minutes) * 60
    last_emit = started - heartbeat_seconds
    files_checked = 0
    references_checked = 0
    current_family: str | None = None

    target_partitions = sum(anchor.file_count for anchor in _FAMILY_ANCHORS.values())

    def snapshot(stage: str) -> ParentScanProgress:
        return ParentScanProgress(
            stage=stage,
            elapsed_seconds=max(clock() - started, 0.0),
            files_checked=files_checked,
            files_total=target_partitions,
            current_family=current_family,
            references_checked=references_checked,
        )

    def emit(stage: str, *, force: bool = False) -> None:
        nonlocal last_emit
        now = clock()
        if now >= deadline:
            raise ParentIdentityScanDeadline(asdict(snapshot(stage)))
        if progress_callback is not None and (force or now - last_emit >= heartbeat_seconds):
            progress_callback(snapshot(stage))
            last_emit = now

    def validation_progress(progress: ValidationProgress) -> None:
        if progress_callback is None:
            return
        progress_callback(
            ParentScanProgress(
                stage=f"VALIDATING_PARENT:{progress.stage}",
                elapsed_seconds=max(clock() - started, 0.0),
                files_checked=0,
                files_total=target_partitions,
                current_family=progress.current_family,
                references_checked=0,
            )
        )

    emit("VALIDATING_PARENT", force=True)
    remaining_minutes = max((deadline - clock()) / 60, 1e-9)
    try:
        validation = validate_sports_dataset_exchange(
            Path(parent_root),
            heartbeat_seconds=heartbeat_seconds,
            max_runtime_minutes=remaining_minutes,
            progress_callback=validation_progress if progress_callback is not None else None,
            clock=clock,
        )
    except ExchangeValidationDeadline as exc:
        raise ParentIdentityScanDeadline(exc.progress) from exc

    root = validation.package_root
    manifest = _load_and_anchor_manifest(root)
    partition_authority = partition_authority_sha256(manifest.partitions)
    _validate_parent_anchors(manifest, partition_authority)
    specs_by_family: dict[str, tuple[ParentIdentityFieldSpec, ...]] = defaultdict(tuple)
    mutable_specs: dict[str, list[ParentIdentityFieldSpec]] = defaultdict(list)
    for spec in MINIMUM_JOIN_FIELD_SPECS:
        mutable_specs[spec.family].append(spec)
    specs_by_family = {
        family: tuple(specs) for family, specs in mutable_specs.items()
    }

    field_player_counts: dict[tuple[str, str, int], Counter[str]] = defaultdict(Counter)
    family_player_counts: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    family_player_seasons: dict[tuple[str, str], set[int]] = defaultdict(set)
    player_join_reference_counts: Counter[str] = Counter()
    player_seasons: dict[str, set[int]] = defaultdict(set)
    player_families: dict[str, set[str]] = defaultdict(set)
    pa_roles: dict[tuple[str, int], Counter[str]] = defaultdict(Counter)
    join_ids_by_season: dict[int, set[str]] = defaultdict(set)
    join_references_by_season: Counter[int] = Counter()

    emit("SCANNING_RETROSHEET_IDENTITIES", force=True)
    for partition in manifest.partitions:
        family_specs = specs_by_family.get(partition.family)
        if not family_specs:
            continue
        current_family = partition.family
        _validate_partition_identity_fields(partition, family_specs)
        columns = {"season", "identity_namespace", "canonical_identity_resolved"}
        for spec in family_specs:
            columns.add(spec.field)
            if spec.raw_mirror_field is not None:
                columns.add(spec.raw_mirror_field)
        path = resolve_under(
            root,
            partition.package_relative_uri,
            require_exists=True,
            require_file=True,
        )
        try:
            frame = pl.read_parquet(path, columns=sorted(columns), hive_partitioning=False)
        except Exception as exc:
            raise ParentIdentityScanError(
                f"Cannot read identity fields from {partition.package_relative_uri}"
            ) from exc
        _validate_retrosheet_partition_guards(frame, partition)
        for spec in family_specs:
            counts = _raw_id_counts(frame, spec, partition.package_relative_uri)
            key = (spec.family, spec.field, partition.season)
            field_player_counts[key].update(counts)
            selected = sum(counts.values())
            references_checked += selected
            join_references_by_season[partition.season] += selected
            for player_id, count in counts.items():
                family_player_counts[(player_id, spec.family)][spec.field] += count
                family_player_seasons[(player_id, spec.family)].add(partition.season)
                player_join_reference_counts[player_id] += count
                player_seasons[player_id].add(partition.season)
                player_families[player_id].add(spec.family)
                join_ids_by_season[partition.season].add(player_id)
                if spec.family == "plate_appearance" and spec.field in {
                    "batter_id",
                    "pitcher_id",
                }:
                    pa_roles[(player_id, partition.season)][spec.field] += count
        files_checked += 1
        emit("SCANNING_RETROSHEET_IDENTITIES")

    emit("SCANNING_PLAYER_PRIORS", force=True)
    priors: list[PlayerPriorEvidence] = []
    canonical_to_lahman: dict[str, str] = {}
    lahman_to_canonical: dict[str, str] = {}
    prior_keys: set[tuple[str, int, str, str | None]] = set()
    for partition in manifest.partitions:
        if partition.family != "player_prior":
            continue
        current_family = partition.family
        _validate_player_prior_partition(partition)
        path = resolve_under(
            root,
            partition.package_relative_uri,
            require_exists=True,
            require_file=True,
        )
        try:
            frame = pl.read_parquet(path, hive_partitioning=False)
        except Exception as exc:
            raise ParentIdentityScanError(
                f"Cannot read player_prior from {partition.package_relative_uri}"
            ) from exc
        if frame.height != partition.row_count:
            raise ParentIdentityScanError("player_prior row count changed after validation")
        for raw in frame.to_dicts():
            prior = _player_prior_evidence(raw, partition)
            key = (
                prior.canonical_player_id,
                prior.season,
                prior.domain,
                prior.position,
            )
            if key in prior_keys:
                raise ParentIdentityScanError("player_prior semantic row is duplicated")
            prior_keys.add(key)
            previous_lahman = canonical_to_lahman.setdefault(
                prior.canonical_player_id, prior.lahman_player_id
            )
            previous_canonical = lahman_to_canonical.setdefault(
                prior.lahman_player_id, prior.canonical_player_id
            )
            if (
                previous_lahman != prior.lahman_player_id
                or previous_canonical != prior.canonical_player_id
            ):
                raise ParentIdentityScanError(
                    "player_prior canonical/Lahman identity is not one-to-one"
                )
            priors.append(prior)
        files_checked += 1
        emit("SCANNING_PLAYER_PRIORS")

    if len(priors) != _FAMILY_ANCHORS["player_prior"].row_count:
        raise ParentIdentityScanError("player_prior rows do not match the frozen anchor")

    field_evidence = tuple(
        FamilyFieldSeasonEvidence(
            family=family,
            field=field,
            raw_mirror_field=_field_spec(family, field).raw_mirror_field,
            season=season,
            reference_count=sum(counts.values()),
            unique_player_count=len(counts),
        )
        for (family, field, season), counts in sorted(field_player_counts.items())
    )
    players = tuple(
        RetrosheetPlayerEvidence(
            retrosheet_player_id=player_id,
            first_season=min(player_seasons[player_id]),
            last_season=max(player_seasons[player_id]),
            total_join_references=player_join_reference_counts[player_id],
            plate_appearance_batter_references=sum(
                pa_roles[(player_id, season)]["batter_id"] for season in _ACTIVE_SEASONS
            ),
            plate_appearance_pitcher_references=sum(
                pa_roles[(player_id, season)]["pitcher_id"] for season in _ACTIVE_SEASONS
            ),
            seasons=tuple(sorted(player_seasons[player_id])),
            families=tuple(sorted(player_families[player_id])),
        )
        for player_id in sorted(player_seasons)
    )
    pa_evidence = tuple(
        PlateAppearanceRoleEvidence(
            retrosheet_player_id=player_id,
            season=season,
            batter_references=counts["batter_id"],
            pitcher_references=counts["pitcher_id"],
        )
        for (player_id, season), counts in sorted(pa_roles.items())
    )
    other_coverage = tuple(
        PlayerFamilyCoverage(
            retrosheet_player_id=player_id,
            family=family,
            first_season=min(family_player_seasons[(player_id, family)]),
            last_season=max(family_player_seasons[(player_id, family)]),
            reference_count=sum(field_counts.values()),
            fields=tuple(sorted(field_counts)),
        )
        for (player_id, family), field_counts in sorted(family_player_counts.items())
        if family != "plate_appearance"
    )

    all_ids = tuple(sorted(player_seasons))
    primary_ids = tuple(
        sorted(
            player_id
            for player_id, seasons in player_seasons.items()
            if seasons & _PRIMARY_SEASONS
        )
    )
    expected_all = manifest.identity_coverage.all_2021_2025
    expected_primary = manifest.identity_coverage.primary_2023_2025
    minimum_join_coverage = _coverage(
        join_references_by_season,
        join_ids_by_season,
        expected_all.reference_count,
        expected_all.unique_players,
        expected_primary.reference_count,
        expected_primary.unique_players,
    )
    governed_references_by_season: Counter[int] = Counter()
    governed_ids_by_season: dict[int, set[str]] = defaultdict(set)
    for spec in GOVERNED_REFERENCE_FIELD_SPECS:
        for season in _ACTIVE_SEASONS:
            counts = field_player_counts[(spec.family, spec.field, season)]
            governed_references_by_season[season] += sum(counts.values())
            governed_ids_by_season[season].update(counts)
    governed_coverage = _coverage(
        governed_references_by_season,
        governed_ids_by_season,
        expected_all.reference_count,
        expected_all.unique_players,
        expected_primary.reference_count,
        expected_primary.unique_players,
    )

    emit("PARENT_IDENTITY_SCANNED", force=True)
    return ParentIdentityEvidence(
        parent_root=root,
        parent_manifest_id=manifest.manifest_id,
        parent_manifest_sha256=manifest.manifest_sha256,
        parent_dataset_version_id=manifest.dataset_version_id,
        parent_package_id=manifest.package_id,
        parent_partition_authority_sha256=partition_authority,
        pa_class_order=tuple(EXPECTED_PA_CLASS_ORDER),
        minimum_join_field_specs=MINIMUM_JOIN_FIELD_SPECS,
        governed_reference_field_specs=GOVERNED_REFERENCE_FIELD_SPECS,
        field_season_evidence=field_evidence,
        players=players,
        plate_appearance_role_evidence=pa_evidence,
        other_family_coverage=other_coverage,
        player_priors=tuple(
            sorted(
                priors,
                key=lambda row: (
                    row.canonical_player_id,
                    row.season,
                    row.domain,
                    row.position or "",
                    row.source_derivation_checksum,
                ),
            )
        ),
        all_retrosheet_player_ids=all_ids,
        primary_retrosheet_player_ids=primary_ids,
        minimum_join_coverage=minimum_join_coverage,
        governed_reference_coverage=governed_coverage,
        governed_counts_reconcile=governed_coverage.counts_reconcile,
    )


def _load_and_anchor_manifest(root: Path) -> SportsDatasetManifestV1:
    path = resolve_under(
        root,
        "sports-dataset-manifest.json",
        require_exists=True,
        require_file=True,
    )
    raw = load_canonical_json(path)
    if not isinstance(raw, dict):
        raise ParentIdentityScanError("Parent manifest must be an object")
    try:
        return SportsDatasetManifestV1.model_validate(raw)
    except ValueError as exc:
        raise ParentIdentityScanError("Parent manifest contract changed") from exc


def _validate_parent_anchors(
    manifest: SportsDatasetManifestV1, partition_authority: str
) -> None:
    expected = {
        "dataset_id": EXPECTED_PARENT_DATASET_ID,
        "manifest_id": EXPECTED_PARENT_MANIFEST_ID,
        "manifest_sha256": EXPECTED_PARENT_MANIFEST_SHA256,
        "dataset_version_id": EXPECTED_PARENT_DATASET_VERSION_ID,
        "package_id": EXPECTED_PARENT_PACKAGE_ID,
    }
    if any(getattr(manifest, field) != value for field, value in expected.items()):
        raise ParentIdentityScanError("Parent dataset identity differs from the trust anchor")
    if partition_authority != EXPECTED_PARENT_PARTITION_AUTHORITY_SHA256:
        raise ParentIdentityScanError("Parent partition authority differs from the trust anchor")
    pa = next(
        (
            target
            for target in manifest.target_definitions
            if target.target_id == "plate_appearance_outcome"
        ),
        None,
    )
    if pa is None or pa.ordered_classes != tuple(EXPECTED_PA_CLASS_ORDER):
        raise ParentIdentityScanError("Parent PA probability-vector class order changed")

    by_family = {row.family_id: row for row in manifest.data_families}
    for family, anchor in _FAMILY_ANCHORS.items():
        declared = by_family.get(family)
        if declared is None:
            raise ParentIdentityScanError(f"Parent family is absent: {family}")
        actual = (
            declared.schema_id,
            declared.file_count,
            declared.row_count,
            declared.byte_count,
            declared.date_range.minimum,
            declared.date_range.maximum,
            declared.source_key,
            declared.source_version_id,
        )
        expected_family = (
            anchor.schema_id,
            anchor.file_count,
            anchor.row_count,
            anchor.byte_count,
            anchor.minimum_date,
            anchor.maximum_date,
            anchor.source_key,
            anchor.source_version_id,
        )
        if actual != expected_family:
            raise ParentIdentityScanError(f"Parent family anchor changed: {family}")


def _validate_partition_identity_fields(
    partition: Any, specs: Sequence[ParentIdentityFieldSpec]
) -> None:
    anchor = _FAMILY_ANCHORS[partition.family]
    if (
        partition.schema_id != anchor.schema_id
        or partition.source_key != anchor.source_key
        or partition.source_version_id != anchor.source_version_id
    ):
        raise ParentIdentityScanError(
            f"Identity partition lineage changed: {partition.package_relative_uri}"
        )
    fields = {field.name: field.dtype for field in partition.physical_schema}
    required = {
        "season": {"Int64"},
        "identity_namespace": {"String"},
        "canonical_identity_resolved": {"Boolean"},
    }
    for spec in specs:
        allowed = {"String"}
        if spec.kind == "LIST":
            allowed = {"List(String)"}
        elif not spec.required:
            allowed = {"String", "Null"}
        required[spec.field] = allowed
        if spec.raw_mirror_field is not None:
            required[spec.raw_mirror_field] = allowed
    for name, allowed in required.items():
        if fields.get(name) not in allowed:
            raise ParentIdentityScanError(
                f"Identity field {partition.family}.{name} has dtype "
                f"{fields.get(name)!r}; expected {sorted(allowed)}"
            )


def _validate_retrosheet_partition_guards(frame: pl.DataFrame, partition: Any) -> None:
    if frame.height != partition.row_count:
        raise ParentIdentityScanError("Retrosheet identity partition row count changed")
    if frame["season"].null_count() or set(frame["season"].unique().to_list()) != {
        partition.season
    }:
        raise ParentIdentityScanError("Identity partition season values changed")
    if (
        frame["identity_namespace"].null_count()
        or set(frame["identity_namespace"].unique().to_list()) != {"RETROSHEET"}
    ):
        raise ParentIdentityScanError("Retrosheet identity namespace guard changed")
    if (
        frame["canonical_identity_resolved"].null_count()
        or set(frame["canonical_identity_resolved"].unique().to_list()) != {False}
    ):
        raise ParentIdentityScanError(
            "Parent Retrosheet IDs unexpectedly claim inline canonical resolution"
        )


def _raw_id_counts(
    frame: pl.DataFrame, spec: ParentIdentityFieldSpec, uri: str
) -> Counter[str]:
    dtype = frame.schema[spec.field]
    if dtype == pl.Null:
        if spec.required or frame[spec.field].null_count() != frame.height:
            raise ParentIdentityScanError(f"Required identity field is Null: {uri}:{spec.field}")
        if spec.raw_mirror_field is not None and frame.schema[spec.raw_mirror_field] != pl.Null:
            raise ParentIdentityScanError(
                f"Identity mirror dtype differs for all-Null field: {uri}:{spec.field}"
            )
        return Counter()

    if spec.kind == "LIST":
        return _raw_list_counts(frame, spec, uri)
    if dtype != pl.String:
        raise ParentIdentityScanError(f"Identity field is not String: {uri}:{spec.field}")
    normalized = frame[spec.field]
    if spec.required and normalized.null_count():
        raise ParentIdentityScanError(f"Required identity field has nulls: {uri}:{spec.field}")
    populated = normalized.drop_nulls()
    if populated.len() and not populated.str.contains(
        _NORMALIZED_RETROSHEET_ID.pattern
    ).all():
        raise ParentIdentityScanError(
            f"Identity field is not explicitly Retrosheet-prefixed: {uri}:{spec.field}"
        )

    if spec.raw_mirror_field is None:
        values = populated.str.slice(len(_RETROSHEET_PREFIX))
    else:
        mirror = frame[spec.raw_mirror_field]
        if mirror.dtype == pl.Null:
            if populated.len():
                raise ParentIdentityScanError(
                    f"Identity mirror is Null while normalized values exist: {uri}:{spec.field}"
                )
            return Counter()
        if mirror.dtype != pl.String:
            raise ParentIdentityScanError(
                f"Raw identity mirror is not String: {uri}:{spec.raw_mirror_field}"
            )
        if normalized.is_null().sum() != mirror.is_null().sum() or frame.filter(
            normalized.is_null() != mirror.is_null()
        ).height:
            raise ParentIdentityScanError(
                f"Identity field/mirror nullability differs: {uri}:{spec.field}"
            )
        raw_populated = mirror.drop_nulls()
        if raw_populated.len() and not raw_populated.str.contains(
            _RAW_RETROSHEET_ID.pattern
        ).all():
            raise ParentIdentityScanError(
                f"Raw Retrosheet mirror is malformed: {uri}:{spec.raw_mirror_field}"
            )
        if frame.filter(
            normalized.is_not_null()
            & (normalized != pl.lit(_RETROSHEET_PREFIX) + mirror)
        ).height:
            raise ParentIdentityScanError(
                f"Normalized identity differs from raw mirror: {uri}:{spec.field}"
            )
        values = raw_populated
    return _counts(values)


def _raw_list_counts(
    frame: pl.DataFrame, spec: ParentIdentityFieldSpec, uri: str
) -> Counter[str]:
    if frame.schema[spec.field] != pl.List(pl.String):
        raise ParentIdentityScanError(f"Identity list has wrong dtype: {uri}:{spec.field}")
    normalized = frame[spec.field]
    if spec.required and normalized.null_count():
        raise ParentIdentityScanError(f"Required identity list has nulls: {uri}:{spec.field}")
    if spec.exact_list_length is not None and frame.filter(
        normalized.list.len() != spec.exact_list_length
    ).height:
        raise ParentIdentityScanError(
            f"Identity list length changed: {uri}:{spec.field}"
        )
    flat_normalized = normalized.drop_nulls().explode().drop_nulls()
    if flat_normalized.len() and not flat_normalized.str.contains(
        _NORMALIZED_RETROSHEET_ID.pattern
    ).all():
        raise ParentIdentityScanError(
            f"Identity list is not explicitly Retrosheet-prefixed: {uri}:{spec.field}"
        )
    if spec.raw_mirror_field is None:
        return _counts(flat_normalized.str.slice(len(_RETROSHEET_PREFIX)))

    if frame.schema[spec.raw_mirror_field] != pl.List(pl.String):
        raise ParentIdentityScanError(
            f"Raw identity-list mirror has wrong dtype: {uri}:{spec.raw_mirror_field}"
        )
    mirror = frame[spec.raw_mirror_field]
    if mirror.null_count() != normalized.null_count() or frame.filter(
        mirror.is_null() != normalized.is_null()
    ).height:
        raise ParentIdentityScanError(
            f"Identity list/mirror nullability differs: {uri}:{spec.field}"
        )
    if frame.filter(mirror.list.len() != normalized.list.len()).height:
        raise ParentIdentityScanError(
            f"Identity list/mirror lengths differ: {uri}:{spec.field}"
        )
    flat_raw = mirror.drop_nulls().explode().drop_nulls()
    if flat_raw.len() and not flat_raw.str.contains(_RAW_RETROSHEET_ID.pattern).all():
        raise ParentIdentityScanError(
            f"Raw Retrosheet list mirror is malformed: {uri}:{spec.raw_mirror_field}"
        )
    normalized_raw = flat_normalized.str.slice(len(_RETROSHEET_PREFIX))
    if normalized_raw.len() != flat_raw.len() or not normalized_raw.equals(flat_raw):
        raise ParentIdentityScanError(
            f"Normalized identity list differs from raw mirror: {uri}:{spec.field}"
        )
    return _counts(flat_raw)


def _counts(values: pl.Series) -> Counter[str]:
    if not values.len():
        return Counter()
    grouped = values.rename("raw_id").to_frame().group_by("raw_id").len()
    return Counter({str(row["raw_id"]): int(row["len"]) for row in grouped.to_dicts()})


def _validate_player_prior_partition(partition: Any) -> None:
    anchor = _FAMILY_ANCHORS["player_prior"]
    if (
        partition.schema_id != anchor.schema_id
        or partition.source_key != anchor.source_key
        or partition.source_version_id != anchor.source_version_id
        or partition.month != 12
    ):
        raise ParentIdentityScanError("player_prior partition lineage changed")
    physical = tuple((field.name, field.dtype) for field in partition.physical_schema)
    if physical != _PLAYER_PRIOR_PHYSICAL_SCHEMA:
        raise ParentIdentityScanError("player_prior physical field contract changed")


def _player_prior_evidence(raw: Mapping[str, Any], partition: Any) -> PlayerPriorEvidence:
    if set(raw) != {name for name, _dtype in _PLAYER_PRIOR_PHYSICAL_SCHEMA}:
        raise ParentIdentityScanError("player_prior row fields differ from the contract")
    canonical = _nonblank(raw["canonical_player_id"], "canonical_player_id")
    if not _UUID.fullmatch(canonical):
        raise ParentIdentityScanError("player_prior canonical UUID is malformed")
    try:
        if str(uuid.UUID(canonical)) != canonical:
            raise ValueError
    except ValueError as exc:
        raise ParentIdentityScanError("player_prior canonical UUID is not canonical") from exc
    lahman = _nonblank(raw["lahman_player_id"], "lahman_player_id")
    if not _LAHMAN_PLAYER_ID.fullmatch(lahman):
        raise ParentIdentityScanError("player_prior Lahman ID is malformed")
    season = _integer(raw["season"], "season")
    eligible = _integer(raw["eligible_from_season"], "eligible_from_season")
    if season != partition.season or eligible != season + 1:
        raise ParentIdentityScanError("player_prior Y-1 eligibility changed")
    if raw["event_family"] != "player_prior":
        raise ParentIdentityScanError("player_prior event_family changed")
    if raw["canonical_game_id"] is not None or raw["player_id"] is not None:
        raise ParentIdentityScanError("player_prior Null identity guards changed")
    if raw["identity_resolution_status"] != "EXACT":
        raise ParentIdentityScanError("player_prior contains a non-exact identity")
    if raw["same_season_predictor_eligible"] is not False:
        raise ParentIdentityScanError("player_prior same-season guard changed")
    if raw["official_date"] != f"{season:04d}-12-31":
        raise ParentIdentityScanError("player_prior official date changed")
    if raw["official_date_semantics"] != "ANNUAL_REGULAR_SEASON_PERIOD_END":
        raise ParentIdentityScanError("player_prior date semantics changed")
    expected_population = (
        "GEN2_PRIMARY_2023_2025"
        if season in _PRIMARY_SEASONS
        else "GEN2_PRIOR_ONLY_2021_2022"
    )
    expected_coverage_role = "PRIMARY" if season in _PRIMARY_SEASONS else "PRIOR_ONLY"
    if (
        raw["population"] != expected_population
        or raw["coverage_role"] != expected_coverage_role
    ):
        raise ParentIdentityScanError("player_prior population/coverage role changed")
    domain = _nonblank(raw["domain"], "domain")
    expected_table = _EXPECTED_PRIOR_DOMAIN_TABLE.get(domain)
    if expected_table is None:
        raise ParentIdentityScanError("player_prior domain is not governed")
    position = None if raw["position"] is None else _nonblank(raw["position"], "position")
    if (domain == "FIELDING") != (position is not None):
        raise ParentIdentityScanError("player_prior domain/position contract changed")
    if raw["timing_classification"] != "RETROSPECTIVE_RECONSTRUCTION":
        raise ParentIdentityScanError("player_prior timing classification changed")
    if raw["source_key"] != _EXPECTED_LAHMAN_SOURCE_KEY:
        raise ParentIdentityScanError("player_prior source key changed")
    source_releases = _string_tuple(raw["source_releases"], "source_releases")
    if source_releases != (_EXPECTED_LAHMAN_RELEASE,):
        raise ParentIdentityScanError("player_prior source release changed")
    source_file_sha256 = _sha256(raw["source_file_sha256"], "source_file_sha256")
    source_file_sha256s = tuple(
        _sha256(value, "source_file_sha256s")
        for value in _string_tuple(raw["source_file_sha256s"], "source_file_sha256s")
    )
    if source_file_sha256 not in source_file_sha256s:
        raise ParentIdentityScanError("player_prior source file lineage is inconsistent")
    if source_file_sha256s != (source_file_sha256,):
        raise ParentIdentityScanError("player_prior source file lineage changed")
    source_logical_tables = _string_tuple(
        raw["source_logical_tables"], "source_logical_tables"
    )
    if source_logical_tables != (expected_table,):
        raise ParentIdentityScanError("player_prior domain/source table lineage differs")
    source_logical_uris = _string_tuple(
        raw["source_logical_uris"], "source_logical_uris"
    )
    source_row_numbers = _integer_tuple(raw["source_row_numbers"], "source_row_numbers")
    source_row_sha256s = tuple(
        _sha256(value, "source_row_sha256s")
        for value in _string_tuple(raw["source_row_sha256s"], "source_row_sha256s")
    )
    if not (
        len(source_logical_uris)
        == len(source_row_numbers)
        == len(source_row_sha256s)
    ):
        raise ParentIdentityScanError("player_prior row lineage lengths differ")
    team_ids = _string_tuple(raw["team_ids"], "team_ids")
    league_ids = _string_tuple(raw["league_ids"], "league_ids")
    return PlayerPriorEvidence(
        event_family="player_prior",
        canonical_game_id=None,
        canonical_player_id=canonical,
        player_id=None,
        lahman_player_id=lahman,
        identity_resolution_status="EXACT",
        season=season,
        official_date=str(raw["official_date"]),
        official_date_semantics=str(raw["official_date_semantics"]),
        population=expected_population,
        coverage_role=expected_coverage_role,
        domain=domain,
        position=position,
        eligible_from_season=eligible,
        same_season_predictor_eligible=False,
        team_ids=team_ids,
        league_ids=league_ids,
        metrics_json=_nonblank(raw["metrics_json"], "metrics_json"),
        source_key=_EXPECTED_LAHMAN_SOURCE_KEY,
        source_releases=source_releases,
        source_logical_tables=source_logical_tables,
        source_logical_uris=source_logical_uris,
        source_file_sha256=source_file_sha256,
        source_file_sha256s=source_file_sha256s,
        source_row_numbers=source_row_numbers,
        source_row_sha256s=source_row_sha256s,
        source_derivation_checksum=_sha256(
            raw["source_derivation_checksum"], "source_derivation_checksum"
        ),
        timing_classification="RETROSPECTIVE_RECONSTRUCTION",
        source_version_id=partition.source_version_id,
        source_partition_uri=partition.package_relative_uri,
        source_partition_sha256=partition.file_sha256,
    )


def _coverage(
    references_by_season: Mapping[int, int],
    ids_by_season: Mapping[int, set[str]],
    expected_references: int,
    expected_unique: int,
    expected_primary_references: int,
    expected_primary_unique: int,
) -> ReferenceCoverageEvidence:
    all_ids = set().union(*(ids_by_season.get(season, set()) for season in _ACTIVE_SEASONS))
    primary_ids = set().union(
        *(ids_by_season.get(season, set()) for season in sorted(_PRIMARY_SEASONS))
    )
    references = sum(int(references_by_season.get(season, 0)) for season in _ACTIVE_SEASONS)
    primary_references = sum(
        int(references_by_season.get(season, 0)) for season in _PRIMARY_SEASONS
    )
    reconcile = (
        references == expected_references
        and len(all_ids) == expected_unique
        and primary_references == expected_primary_references
        and len(primary_ids) == expected_primary_unique
    )
    return ReferenceCoverageEvidence(
        reference_count=references,
        unique_player_count=len(all_ids),
        primary_reference_count=primary_references,
        primary_unique_player_count=len(primary_ids),
        expected_reference_count=expected_references,
        expected_unique_player_count=expected_unique,
        expected_primary_reference_count=expected_primary_references,
        expected_primary_unique_player_count=expected_primary_unique,
        counts_reconcile=reconcile,
    )


def _field_spec(family: str, field: str) -> ParentIdentityFieldSpec:
    return next(
        spec
        for spec in MINIMUM_JOIN_FIELD_SPECS
        if spec.family == family and spec.field == field
    )


def _nonblank(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ParentIdentityScanError(f"player_prior {name} is invalid")
    return value


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ParentIdentityScanError(f"player_prior {name} is invalid")
    return value


def _string_tuple(value: Any, name: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ParentIdentityScanError(f"player_prior {name} is not a list")
    result = tuple(_nonblank(item, name) for item in value)
    if not allow_empty and not result:
        raise ParentIdentityScanError(f"player_prior {name} cannot be empty")
    return result


def _integer_tuple(value: Any, name: str) -> tuple[int, ...]:
    if not isinstance(value, list) or not value:
        raise ParentIdentityScanError(f"player_prior {name} is not a non-empty list")
    return tuple(_integer(item, name) for item in value)


def _sha256(value: Any, name: str) -> str:
    token = _nonblank(value, name)
    if not _SHA256.fullmatch(token):
        raise ParentIdentityScanError(f"player_prior {name} is not SHA-256")
    return token


def _self_check() -> None:
    keys = tuple((spec.family, spec.field) for spec in MINIMUM_JOIN_FIELD_SPECS)
    if len(keys) != len(set(keys)):
        raise RuntimeError("Parent identity field inventory contains duplicates")
    if not _GOVERNED_KEYS.issubset(set(keys)):
        raise RuntimeError("Governed reference inventory is not a join-field subset")
    if tuple(EXPECTED_PA_CLASS_ORDER) != CANONICAL_PA_CLASS_ORDER:
        raise RuntimeError("Identity bridge and parent PA class orders differ")
    if EXPECTED_PARENT_PARTITION_AUTHORITY_SHA256 != EXPECTED_PARTITION_AUTHORITY_SHA256:
        raise RuntimeError("Identity bridge and parent partition authorities differ")
    if set(_FAMILY_ANCHORS) != {
        "bullpen_usage",
        "pitch_sequence",
        "plate_appearance",
        "play_transition",
        "player_game_batting",
        "player_game_fielding",
        "player_game_pitching",
        "player_prior",
        "starter_usage",
    }:
        raise RuntimeError("Parent identity family anchor inventory is incomplete")
    prior_names = tuple(name for name, _dtype in _PLAYER_PRIOR_PHYSICAL_SCHEMA)
    if len(prior_names) != 28 or len(prior_names) != len(set(prior_names)):
        raise RuntimeError("player_prior physical schema anchor is invalid")


_self_check()


__all__ = [
    "FamilyFieldSeasonEvidence",
    "GOVERNED_REFERENCE_FIELD_SPECS",
    "MINIMUM_JOIN_FIELD_SPECS",
    "PLAYER_PRIOR_PHYSICAL_SCHEMA",
    "ParentIdentityEvidence",
    "ParentIdentityFieldSpec",
    "ParentIdentityScanDeadline",
    "ParentIdentityScanError",
    "ParentScanProgress",
    "PlateAppearanceRoleEvidence",
    "PlayerFamilyCoverage",
    "PlayerPriorEvidence",
    "ReferenceCoverageEvidence",
    "RetrosheetPlayerEvidence",
    "scan_parent_identity",
]

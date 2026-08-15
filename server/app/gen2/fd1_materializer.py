"""Bounded Retrosheet-to-Parquet staging for the Gate 3C-FD1 warehouse.

The official Daily Logs release is a seven-file CSV bundle.  Calling the existing
``parse_retrosheet_bundle`` on those complete files would duplicate several gigabytes in
Python objects.  This module instead retains only caller-bounded date windows and then
delegates the difficult game-level reconciliation to that parser.  Its full-range API
scans each source once and uses a capped, auto-deleted monthly spool.  No network, ORM,
database, durable raw-data, or Parquet writes occur here.

Output is deliberately a *staging* contract.  At most one payload is produced for a
family/season/month in a call, and each payload's rows can be passed directly to
``HistoricalEventParquetStore.write_partition``.  Partial-month calls are labeled as
pilot staging so they cannot be mistaken for complete monthly warehouse partitions.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
import csv
from dataclasses import dataclass
from datetime import date, timedelta
from hashlib import sha256
import io
import json
from pathlib import Path
import tempfile
from types import MappingProxyType
from typing import Any

from app.gen2.retrosheet import (
    AUTOMATIC_RUNNER_AFTER_NP_BRIDGE,
    AUTOMATIC_RUNNER_DIRECT_FIRST_PLAY,
    HISTORICAL_ACTUAL_LINEUP_TIMING_RISK,
    HISTORICAL_ACTUAL_RELIEVER_TIMING_RISK,
    HISTORICAL_ACTUAL_STARTER_TIMING_RISK,
    RETROSHEET_CSV_HEADERS,
    RETROSHEET_REQUIRED_FILENAMES,
    RETROSPECTIVE_ACTUAL_TIMING_SEMANTICS,
    RETROSPECTIVE_EVENT_TIMING_RISK,
    RetrosheetGame,
    RetrosheetImportBundle,
    RetrosheetImportError,
    RetrosheetPitcherAppearance,
    RetrosheetPitcherRole,
    RetrosheetStatRecord,
    parse_retrosheet_bundle,
    source_player_id,
    source_team_id,
)


RETROSHEET_OPEN_DATA = "RETROSHEET_OPEN_DATA"
RETROSHEET_SOURCE_KEY = "RETROSHEET_CSV_DOWNLOADS"
GEN2_PRIMARY_2023_2025 = "GEN2_PRIMARY_2023_2025"
GEN2_PRIOR_ONLY_2021_2022 = "GEN2_PRIOR_ONLY_2021_2022"
GEN2_ACTUAL_LINEUP_UPPER_BOUND = "GEN2_ACTUAL_LINEUP_UPPER_BOUND"
GEN2_STRICT_DEPLOYABLE_RECONSTRUCTION = "GEN2_STRICT_DEPLOYABLE_RECONSTRUCTION"
RETROSPECTIVE_RECONSTRUCTION = "RETROSPECTIVE_RECONSTRUCTION"
ACTUAL_STARTER_HISTORICAL_RECONSTRUCTION = (
    "ACTUAL_STARTER_HISTORICAL_RECONSTRUCTION"
)

PRIMARY_SEASONS = frozenset({2023, 2024, 2025})
PRIOR_ONLY_SEASONS = frozenset({2021, 2022})
SUPPORTED_SEASONS = PRIMARY_SEASONS | PRIOR_ONLY_SEASONS

MATERIALIZED_FAMILIES = (
    "plate_appearance",
    "play_transition",
    "pitch_sequence",
    "player_game_batting",
    "player_game_pitching",
    "player_game_fielding",
    "starter_usage",
    "bullpen_usage",
    "weather",
)
MATERIALIZED_FAMILY_SCHEMA_VERSIONS: Mapping[str, str] = MappingProxyType(
    {
        family: (
            "retrosheet_fd1_weather_v8"
            if family == "weather"
            else (
                f"retrosheet_fd1_{family}_v6"
                if family in {"starter_usage", "bullpen_usage"}
                else f"retrosheet_fd1_{family}_v5"
            )
        )
        for family in MATERIALIZED_FAMILIES
    }
)

MAX_WINDOW_DAYS = 31
DEFAULT_MAX_GAMES = 700
DEFAULT_MAX_SELECTED_ROWS = 500_000
DEFAULT_MAX_SELECTED_BYTES = 768 * 1024 * 1024
DEFAULT_MAXIMUM_DISPATCH_SPOOL_BYTES = 10 * 1024 * 1024 * 1024
_REGULAR_SEASON_TOKENS = frozenset({"regular", "r", "reg"})
_FILE_CHUNK_BYTES = 1024 * 1024
_SPOOL_ROW_NUMBER = "__source_row_number"
_SPOOL_ROW_CHECKSUM = "__source_row_checksum"


def _retrosheet_date(value: object, context: str) -> date:
    """Parse the compact current-release or documented ISO date token."""

    token = str(value).strip()
    try:
        if len(token) == 8 and token.isascii() and token.isdigit():
            return date(int(token[:4]), int(token[4:6]), int(token[6:8]))
        return date.fromisoformat(token)
    except ValueError as exc:
        raise RetrosheetImportError(f"{context}: invalid date {value!r}") from exc


class Fd1MaterializationError(ValueError):
    """A local Retrosheet window cannot safely be staged."""


@dataclass(frozen=True, slots=True)
class MaterializationWindow:
    """Inclusive event-date bounds with an exclusive evidence-knowledge cutoff."""

    start_date: date
    end_date: date
    knowledge_cutoff: date

    def __post_init__(self) -> None:
        if self.end_date < self.start_date:
            raise Fd1MaterializationError("window end_date cannot precede start_date")
        if (self.end_date - self.start_date).days + 1 > MAX_WINDOW_DAYS:
            raise Fd1MaterializationError(
                f"a materialization window cannot exceed {MAX_WINDOW_DAYS} days"
            )

    def contains(self, value: date) -> bool:
        """Return true only for completed historical evidence in this window."""

        return self.start_date <= value <= self.end_date and value < self.knowledge_cutoff

    def is_full_month(self, season: int, month: int) -> bool:
        first = date(season, month, 1)
        if month == 12:
            following = date(season + 1, 1, 1)
        else:
            following = date(season, month + 1, 1)
        return self.start_date <= first and self.end_date >= following - timedelta(days=1)


@dataclass(frozen=True, slots=True)
class GameOrder:
    """Conservative chronological key for completed-game prior construction."""

    game_id: str
    game_date: date
    completion_date: date | None
    game_number: int
    doubleheader_id: str | None

    @property
    def final_available_date(self) -> date:
        return self.completion_date or self.game_date


@dataclass(frozen=True, slots=True)
class SourceRowReference:
    filename: str
    row_number: int
    row_checksum: str
    file_sha256: str


@dataclass(frozen=True, slots=True)
class FamilyStagingPayload:
    """One coalesced family/season/month payload accepted by the Parquet store."""

    family: str
    season: int
    month: int
    schema_version: str
    publication_scope: str
    rows: tuple[Mapping[str, Any], ...]
    row_count: int
    game_count: int
    plate_appearance_count: int
    player_count: int
    min_event_date: date
    max_event_date: date
    source_file_checksums: Mapping[str, str]
    source_bundle_checksum: str
    partition_checksum: str
    leakage_policy: str
    quality_report: Mapping[str, Any]

    @property
    def partition_key(self) -> tuple[str, int, int]:
        return self.family, self.season, self.month

    def iter_rows(self) -> Iterator[Mapping[str, Any]]:
        """Yield rows without another materialization or an ORM accumulation."""

        return iter(self.rows)

    @property
    def raw_contributor_manifest(self) -> tuple[Mapping[str, Any], ...]:
        """Return deterministic contributor identities for the governed store call."""

        return tuple(
            MappingProxyType(
                {
                    "logical_uri": (
                        f"open-data/retrosheet/file-sha256={checksum}/{filename}"
                    ),
                    "file_sha256": checksum,
                    "payload_checksum": checksum,
                    "source_record_id": filename,
                }
            )
            for filename, checksum in sorted(self.source_file_checksums.items())
        )

    def parquet_store_metadata(self) -> dict[str, Any]:
        """Return the payload-derived arguments shared with ``write_partition``.

        Authorization grants, database UUIDs, and code revision belong to the caller and
        are intentionally absent; this materializer cannot manufacture governance state.
        """

        return {
            "rows": self.iter_rows(),
            "source_key": RETROSHEET_SOURCE_KEY,
            "event_family": self.family,
            "season": self.season,
            "month": self.month,
            "schema_version": self.schema_version,
            "raw_contributor_manifest": self.raw_contributor_manifest,
            "game_count": self.game_count,
            "plate_appearance_count": self.plate_appearance_count,
            "player_count": self.player_count,
            "min_event_date": self.min_event_date,
            "max_event_date": self.max_event_date,
        }


@dataclass(frozen=True, slots=True)
class MaterializationCounters:
    scanned_rows: Mapping[str, int]
    selected_rows: Mapping[str, int]
    exclusion_counts: Mapping[str, int]
    selected_games: int
    primary_games: int
    prior_only_games: int
    full_pbp_games: int
    deduced_pbp_games: int
    no_pbp_games: int
    completed_suspended_games: int
    relocated_suspended_games: int
    suspended_original_segment_plays: int
    suspended_completion_segment_plays: int
    automatic_runner_placements: int
    automatic_runner_direct_placements: int
    automatic_runner_np_bridge_placements: int
    lineup_position_reconciliations: int
    lineup_nonstarting_role_code_occurrences: int
    stattype_rows: Mapping[str, int]
    family_rows: Mapping[str, int]
    reconciliation_checks: Mapping[str, bool]

    def as_dict(self) -> dict[str, Any]:
        return {
            "scanned_rows": dict(self.scanned_rows),
            "selected_rows": dict(self.selected_rows),
            "exclusion_counts": dict(self.exclusion_counts),
            "selected_games": self.selected_games,
            "primary_games": self.primary_games,
            "prior_only_games": self.prior_only_games,
            "full_pbp_games": self.full_pbp_games,
            "deduced_pbp_games": self.deduced_pbp_games,
            "no_pbp_games": self.no_pbp_games,
            "completed_suspended_games": self.completed_suspended_games,
            "relocated_suspended_games": self.relocated_suspended_games,
            "suspended_original_segment_plays": self.suspended_original_segment_plays,
            "suspended_completion_segment_plays": self.suspended_completion_segment_plays,
            "automatic_runner_placements": self.automatic_runner_placements,
            "automatic_runner_direct_placements": self.automatic_runner_direct_placements,
            "automatic_runner_np_bridge_placements": (
                self.automatic_runner_np_bridge_placements
            ),
            "lineup_position_reconciliations": self.lineup_position_reconciliations,
            "lineup_nonstarting_role_code_occurrences": (
                self.lineup_nonstarting_role_code_occurrences
            ),
            "stattype_rows": dict(self.stattype_rows),
            "family_rows": dict(self.family_rows),
            "reconciliation_checks": dict(self.reconciliation_checks),
        }


@dataclass(frozen=True, slots=True)
class RetrosheetMaterializationResult:
    source_version_id: str
    window: MaterializationWindow
    source_file_checksums: Mapping[str, str]
    selected_bundle_checksum: str
    batches: tuple[FamilyStagingPayload, ...]
    game_orders: Mapping[str, GameOrder]
    counters: MaterializationCounters
    build_checksum: str

    def family(self, name: str) -> tuple[FamilyStagingPayload, ...]:
        if name not in MATERIALIZED_FAMILIES:
            raise KeyError(name)
        return tuple(batch for batch in self.batches if batch.family == name)


@dataclass(frozen=True, slots=True)
class _SelectedRow:
    values: Mapping[str, str]
    reference: SourceRowReference


class _CsvRowView(Mapping[str, str]):
    """Zero-copy named access over one positional ``csv.reader`` record."""

    __slots__ = ("_fields", "_header", "_index")

    def __init__(
        self,
        header: tuple[str, ...],
        fields: Sequence[str],
        index: Mapping[str, int],
    ) -> None:
        self._header = header
        self._fields = fields
        self._index = index

    def __getitem__(self, key: str) -> str:
        return self._fields[self._index[key]]

    def __iter__(self) -> Iterator[str]:
        return iter(self._header)

    def __len__(self) -> int:
        return len(self._header)


@dataclass(slots=True)
class _SelectionBudget:
    maximum_rows: int
    maximum_bytes: int
    selected_rows: int = 0
    selected_bytes: int = 0

    def add(self, filename: str, row_number: int, row: Mapping[str, str]) -> None:
        encoded = _canonical_json(row).encode("utf-8")
        self.selected_rows += 1
        self.selected_bytes += len(encoded)
        if self.selected_rows > self.maximum_rows:
            raise Fd1MaterializationError(
                f"selected window exceeded {self.maximum_rows} rows at "
                f"{filename}:{row_number}; split it into smaller windows"
            )
        if self.selected_bytes > self.maximum_bytes:
            raise Fd1MaterializationError(
                f"selected window exceeded {self.maximum_bytes} bytes at "
                f"{filename}:{row_number}; split it into smaller windows"
            )


class _MonthlyCsvSpool:
    """Transient per-source monthly CSV buckets with a bounded aggregate size.

    Only writers for the source currently being scanned remain open, so a five-season
    dispatch uses at most one handle per selected month rather than seven handles per
    month.  Files are private implementation staging and are removed before the iterator
    exits; they are never raw archives or warehouse partitions.
    """

    def __init__(
        self,
        root: Path,
        filename: str,
        *,
        reserve_bytes: Callable[[int], None],
    ) -> None:
        self.root = root
        self.filename = filename
        self._reserve_bytes = reserve_bytes
        self._streams: dict[tuple[int, int], io.TextIOWrapper] = {}
        self._writers: dict[tuple[int, int], csv.DictWriter] = {}

    def write(
        self,
        key: tuple[int, int],
        *,
        row_number: int,
        row_checksum: str,
        row: Mapping[str, str],
    ) -> None:
        writer = self._writers.get(key)
        if writer is None:
            path = _spool_path(self.root, key, self.filename)
            stream = path.open("x", encoding="utf-8", newline="")
            fields = (
                _SPOOL_ROW_NUMBER,
                _SPOOL_ROW_CHECKSUM,
                *RETROSHEET_CSV_HEADERS[self.filename],
            )
            writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            self._streams[key] = stream
            self._writers[key] = writer
        document = {
            _SPOOL_ROW_NUMBER: str(row_number),
            _SPOOL_ROW_CHECKSUM: row_checksum,
            **dict(row),
        }
        # The canonical length is a conservative and deterministic cap proxy.  It avoids
        # calling tell()/flush() for every one of millions of selected play rows.
        self._reserve_bytes(len(_canonical_json(document).encode("utf-8")) + 1)
        writer.writerow(document)

    def close(self) -> None:
        streams = tuple(self._streams.values())
        self._streams.clear()
        self._writers.clear()
        for stream in streams:
            stream.close()


def iter_month_windows(
    start_date: date,
    end_date: date,
    *,
    knowledge_cutoff: date | None = None,
) -> Iterator[MaterializationWindow]:
    """Split a longer range into bounded calendar-month calls."""

    if end_date < start_date:
        raise Fd1MaterializationError("end_date cannot precede start_date")
    cutoff = knowledge_cutoff or date.today()
    cursor = start_date
    while cursor <= end_date:
        if cursor.month == 12:
            next_month = date(cursor.year + 1, 1, 1)
        else:
            next_month = date(cursor.year, cursor.month + 1, 1)
        month_end = min(end_date, next_month - timedelta(days=1))
        yield MaterializationWindow(cursor, month_end, cutoff)
        cursor = month_end + timedelta(days=1)


def is_strictly_prior_game(observation: GameOrder, target: GameOrder) -> bool:
    """Exclude current/future games; admit same-day evidence only within a numbered DH."""

    if observation.final_available_date < target.game_date:
        return True
    if (
        observation.final_available_date > target.game_date
        or observation.game_id == target.game_id
    ):
        return False
    # A suspended final completed on the target date has no governed completion time.
    # Do not infer that it preceded the target from a date-only source field.
    if observation.completion_date is not None:
        return False
    return bool(
        observation.doubleheader_id
        and observation.doubleheader_id == target.doubleheader_id
        and observation.game_number > 0
        and target.game_number > 0
        and observation.game_number < target.game_number
    )


def iter_strict_prior_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    target_game_id: str,
    game_orders: Mapping[str, GameOrder],
) -> Iterator[Mapping[str, Any]]:
    """Yield only completed observations chronologically before a target game."""

    try:
        target = game_orders[target_game_id]
    except KeyError as exc:
        raise Fd1MaterializationError(f"unknown target game {target_game_id!r}") from exc
    for row in rows:
        game_id = str(row.get("game_id", ""))
        observation = game_orders.get(game_id)
        if observation is not None and is_strictly_prior_game(observation, target):
            yield row


def verify_payload_checksum(payload: FamilyStagingPayload) -> None:
    """Fail closed if a row or payload was mutated after staging."""

    for index, row in enumerate(payload.rows):
        expected = row.get("row_checksum")
        actual = _row_output_checksum(row)
        if expected != actual:
            raise Fd1MaterializationError(
                f"{payload.family} row {index} checksum does not match canonical content"
            )
    actual_partition = _canonical_checksum(
        {
            "family": payload.family,
            "season": payload.season,
            "month": payload.month,
            "schema_version": payload.schema_version,
            "source_bundle_checksum": payload.source_bundle_checksum,
            "rows": [dict(row) for row in payload.rows],
        }
    )
    if actual_partition != payload.partition_checksum:
        raise Fd1MaterializationError(
            f"{payload.family} partition checksum does not match canonical content"
        )


def materialize_retrosheet_window(
    bundle_directory: Path,
    *,
    source_version_id: str,
    start_date: date,
    end_date: date,
    knowledge_cutoff: date | None = None,
    maximum_games: int = DEFAULT_MAX_GAMES,
    maximum_selected_rows: int = DEFAULT_MAX_SELECTED_ROWS,
    maximum_selected_bytes: int = DEFAULT_MAX_SELECTED_BYTES,
) -> RetrosheetMaterializationResult:
    """Scan, validate, reconcile, and stage one bounded historical date window.

    ``knowledge_cutoff`` is exclusive.  A game on that date is the current game and is
    excluded along with all future evidence.  This is intentionally independent of the
    source file's date range so a fixture or future release cannot leak evidence.
    """

    if maximum_games <= 0 or maximum_selected_rows <= 0 or maximum_selected_bytes <= 0:
        raise Fd1MaterializationError("materialization limits must be positive")
    window = MaterializationWindow(
        start_date=start_date,
        end_date=end_date,
        knowledge_cutoff=knowledge_cutoff or date.today(),
    )
    directory, paths = _validated_bundle_paths(Path(bundle_directory))
    del directory
    file_checksums = MappingProxyType(
        {filename: _file_sha256(path) for filename, path in sorted(paths.items())}
    )

    scanned: Counter[str] = Counter()
    selected_counts: Counter[str] = Counter()
    exclusions: Counter[str] = Counter()
    budget = _SelectionBudget(maximum_selected_rows, maximum_selected_bytes)

    game_rows = _scan_rows(
        paths["gameinfo.csv"],
        "gameinfo.csv",
        file_checksums["gameinfo.csv"],
        scanned,
    )
    selected_games: list[_SelectedRow] = []
    selected_game_ids: set[str] = set()
    selected_seasons: set[int] = set()
    selected_teams: set[str] = set()
    for selected in game_rows:
        row = selected.values
        try:
            game_date = _retrosheet_date(
                row["date"],
                f"gameinfo.csv:{selected.reference.row_number}.date",
            )
            season = int(row["season"].strip())
        except (TypeError, ValueError) as exc:
            raise RetrosheetImportError(
                f"gameinfo.csv:{selected.reference.row_number}: invalid date/season"
            ) from exc
        if game_date < window.start_date or game_date > window.end_date:
            exclusions["outside_window"] += 1
            continue
        if game_date >= window.knowledge_cutoff:
            exclusions["current_or_future"] += 1
            continue
        if season not in SUPPORTED_SEASONS:
            exclusions["unsupported_season"] += 1
            continue
        reason = _game_exclusion_reason(
            row,
            game_date=game_date,
            season=season,
            window=window,
        )
        if reason is not None:
            exclusions[reason] += 1
            continue
        gid = row["gid"].strip()
        if gid in selected_game_ids:
            raise RetrosheetImportError(f"duplicate selected game id {gid!r}")
        selected_game_ids.add(gid)
        selected_seasons.add(season)
        selected_teams.update((row["visteam"].strip(), row["hometeam"].strip()))
        budget.add("gameinfo.csv", selected.reference.row_number, row)
        selected_counts["gameinfo.csv"] += 1
        selected_games.append(selected)
        if len(selected_games) > maximum_games:
            raise Fd1MaterializationError(
                f"selected window exceeded {maximum_games} games; split it into smaller windows"
            )

    retained: dict[str, list[_SelectedRow]] = {
        "gameinfo.csv": selected_games,
    }
    for filename in RETROSHEET_CSV_HEADERS:
        if filename == "gameinfo.csv":
            continue
        rows: list[_SelectedRow] = []
        for selected in _scan_rows(
            paths[filename], filename, file_checksums[filename], scanned
        ):
            row = selected.values
            if filename == "allplayers.csv":
                try:
                    keep = (
                        int(row["season"].strip()) in selected_seasons
                        and row["team"].strip() in selected_teams
                    )
                except ValueError as exc:
                    raise RetrosheetImportError(
                        f"{filename}:{selected.reference.row_number}: invalid season"
                    ) from exc
            else:
                keep = row["gid"].strip() in selected_game_ids
            if not keep:
                continue
            budget.add(filename, selected.reference.row_number, row)
            selected_counts[filename] += 1
            rows.append(selected)
        retained[filename] = rows

    selected_text = {
        filename: _selected_csv_text(filename, retained.get(filename, ()))
        for filename in RETROSHEET_CSV_HEADERS
    }
    bundle = parse_retrosheet_bundle(selected_text, source_version_id=source_version_id)
    source_refs = _source_reference_index(retained)
    result = _materialize_bundle(
        bundle,
        source_version_id=source_version_id,
        window=window,
        source_file_checksums=file_checksums,
        source_refs=source_refs,
        scanned=scanned,
        selected_counts=selected_counts,
        exclusions=exclusions,
    )
    for payload in result.batches:
        verify_payload_checksum(payload)
    return result


def iter_retrosheet_monthly_materializations(
    bundle_directory: Path,
    *,
    source_version_id: str,
    start_date: date,
    end_date: date,
    knowledge_cutoff: date | None = None,
    maximum_games_per_window: int = DEFAULT_MAX_GAMES,
    maximum_selected_rows_per_window: int = DEFAULT_MAX_SELECTED_ROWS,
    maximum_selected_bytes_per_window: int = DEFAULT_MAX_SELECTED_BYTES,
    maximum_dispatch_spool_bytes: int = DEFAULT_MAXIMUM_DISPATCH_SPOOL_BYTES,
    temporary_directory: Path | None = None,
    include_empty_windows: bool = False,
) -> Iterator[RetrosheetMaterializationResult]:
    """Scan each official source once and yield bounded month results chronologically.

    The 9+ GiB ``plays.csv`` makes one full rescan per month operationally unacceptable.
    This dispatcher performs exactly one sequential CSV pass per official file, hashing
    the bytes during that same pass.  Selected rows are written to a private, capped,
    auto-deleted monthly spool.  One month is then validated and materialized at a time,
    so neither five seasons of parsed plays nor all output partitions accumulate in RAM.

    The spool is not a raw archive, database, Parquet dataset, or durable warehouse.  It
    exists only for the lifetime of this iterator and is removed on success or failure.
    Callers that stop early should explicitly close the iterator to trigger cleanup.
    """

    limits = (
        maximum_games_per_window,
        maximum_selected_rows_per_window,
        maximum_selected_bytes_per_window,
        maximum_dispatch_spool_bytes,
    )
    if any(limit <= 0 for limit in limits):
        raise Fd1MaterializationError("dispatcher limits must be positive")
    cutoff = knowledge_cutoff or date.today()
    windows = tuple(
        iter_month_windows(start_date, end_date, knowledge_cutoff=cutoff)
    )
    window_by_key = {
        (window.start_date.year, window.start_date.month): window for window in windows
    }
    if len(window_by_key) != len(windows):
        raise Fd1MaterializationError("dispatcher produced duplicate calendar-month windows")

    source_root, paths = _validated_bundle_paths(Path(bundle_directory))
    temp_base = _validated_dispatch_temp_base(temporary_directory, source_root)
    with tempfile.TemporaryDirectory(
        prefix="asp-fd1-dispatch-",
        dir=str(temp_base) if temp_base is not None else None,
    ) as temporary:
        spool_root = Path(temporary).resolve()
        scanned: Counter[str] = Counter()
        selected_counts = {key: Counter() for key in window_by_key}
        exclusions = {key: Counter() for key in window_by_key}
        budgets = {
            key: _SelectionBudget(
                maximum_selected_rows_per_window,
                maximum_selected_bytes_per_window,
            )
            for key in window_by_key
        }
        selected_game_counts: Counter[tuple[int, int]] = Counter()
        inside_game_counts: Counter[tuple[int, int]] = Counter()
        selected_game_ids: set[str] = set()
        game_month_by_id: dict[str, tuple[int, int]] = {}
        seasons_by_key: dict[tuple[int, int], set[int]] = defaultdict(set)
        teams_by_key: dict[tuple[int, int], set[str]] = defaultdict(set)
        file_checksums: dict[str, str] = {}
        total_game_rows = 0
        spooled_bytes = 0

        def reserve_spool_bytes(additional: int) -> None:
            nonlocal spooled_bytes
            spooled_bytes += additional
            if spooled_bytes > maximum_dispatch_spool_bytes:
                raise Fd1MaterializationError(
                    "single-pass dispatch exceeded its temporary spool byte cap"
                )

        game_spool = _MonthlyCsvSpool(
            spool_root,
            "gameinfo.csv",
            reserve_bytes=reserve_spool_bytes,
        )

        def select_game(
            row_number: int, row: Mapping[str, str]
        ) -> None:
            nonlocal total_game_rows
            total_game_rows += 1
            try:
                game_date = _retrosheet_date(
                    row["date"], f"gameinfo.csv:{row_number}.date"
                )
                season = int(row["season"].strip())
            except (TypeError, ValueError) as exc:
                raise RetrosheetImportError(
                    f"gameinfo.csv:{row_number}: invalid date/season"
                ) from exc
            key = (game_date.year, game_date.month)
            window = window_by_key.get(key)
            if window is None or not (
                window.start_date <= game_date <= window.end_date
            ):
                return
            inside_game_counts[key] += 1
            reason = _game_exclusion_reason(
                row, game_date=game_date, season=season, window=window
            )
            if reason is not None:
                exclusions[key][reason] += 1
                return
            gid = row["gid"].strip()
            if gid in selected_game_ids:
                raise RetrosheetImportError(f"duplicate selected game id {gid!r}")
            selected_game_ids.add(gid)
            game_month_by_id[gid] = key
            seasons_by_key[key].add(season)
            teams_by_key[key].update(
                (row["visteam"].strip(), row["hometeam"].strip())
            )
            budget = budgets[key]
            budget.add("gameinfo.csv", row_number, row)
            selected_counts[key]["gameinfo.csv"] += 1
            selected_game_counts[key] += 1
            if selected_game_counts[key] > maximum_games_per_window:
                raise Fd1MaterializationError(
                    f"selected window exceeded {maximum_games_per_window} games; "
                    "split it into smaller windows"
                )
            game_spool.write(
                key,
                row_number=row_number,
                row_checksum=_source_row_checksum("gameinfo.csv", row),
                row=row,
            )

        try:
            file_checksums["gameinfo.csv"] = _scan_source_file_once(
                paths["gameinfo.csv"],
                "gameinfo.csv",
                scanned=scanned,
                on_row=select_game,
            )
        finally:
            game_spool.close()
        for key in window_by_key:
            outside = total_game_rows - inside_game_counts[key]
            if outside:
                exclusions[key]["outside_window"] = outside

        roster_spool = _MonthlyCsvSpool(
            spool_root,
            "allplayers.csv",
            reserve_bytes=reserve_spool_bytes,
        )

        def select_roster(
            row_number: int, row: Mapping[str, str]
        ) -> None:
            try:
                season = int(row["season"].strip())
            except ValueError as exc:
                raise RetrosheetImportError(
                    f"allplayers.csv:{row_number}: invalid season"
                ) from exc
            team = row["team"].strip()
            for key in window_by_key:
                if season not in seasons_by_key[key] or team not in teams_by_key[key]:
                    continue
                budgets[key].add("allplayers.csv", row_number, row)
                selected_counts[key]["allplayers.csv"] += 1
                roster_spool.write(
                    key,
                    row_number=row_number,
                    row_checksum=_source_row_checksum("allplayers.csv", row),
                    row=row,
                )

        try:
            file_checksums["allplayers.csv"] = _scan_source_file_once(
                paths["allplayers.csv"],
                "allplayers.csv",
                scanned=scanned,
                on_row=select_roster,
            )
        finally:
            roster_spool.close()

        for filename in RETROSHEET_CSV_HEADERS:
            if filename in {"allplayers.csv", "gameinfo.csv"}:
                continue
            source_spool = _MonthlyCsvSpool(
                spool_root,
                filename,
                reserve_bytes=reserve_spool_bytes,
            )

            def select_game_child(
                row_number: int,
                row: Mapping[str, str],
                *,
                selected_filename: str = filename,
                selected_spool: _MonthlyCsvSpool = source_spool,
            ) -> None:
                key = game_month_by_id.get(row["gid"].strip())
                if key is None:
                    return
                budgets[key].add(selected_filename, row_number, row)
                selected_counts[key][selected_filename] += 1
                selected_spool.write(
                    key,
                    row_number=row_number,
                    row_checksum=_source_row_checksum(selected_filename, row),
                    row=row,
                )

            try:
                file_checksums[filename] = _scan_source_file_once(
                    paths[filename],
                    filename,
                    scanned=scanned,
                    on_row=select_game_child,
                )
            finally:
                source_spool.close()

        frozen_checksums = MappingProxyType(
            {filename: file_checksums[filename] for filename in sorted(file_checksums)}
        )
        for key, window in sorted(window_by_key.items()):
            if selected_game_counts[key] == 0 and not include_empty_windows:
                continue
            retained = {
                filename: _read_spooled_rows(
                    spool_root,
                    key,
                    filename,
                    file_sha256=frozen_checksums[filename],
                )
                for filename in RETROSHEET_CSV_HEADERS
            }
            selected_text = {
                filename: _selected_csv_text(filename, retained[filename])
                for filename in RETROSHEET_CSV_HEADERS
            }
            bundle = parse_retrosheet_bundle(
                selected_text, source_version_id=source_version_id
            )
            result = _materialize_bundle(
                bundle,
                source_version_id=source_version_id,
                window=window,
                source_file_checksums=frozen_checksums,
                source_refs=_source_reference_index(retained),
                scanned=scanned,
                selected_counts=selected_counts[key],
                exclusions=exclusions[key],
            )
            for payload in result.batches:
                verify_payload_checksum(payload)
            yield result


def _validated_bundle_paths(directory: Path) -> tuple[Path, Mapping[str, Path]]:
    if not directory.exists() or not directory.is_dir() or directory.is_symlink():
        raise Fd1MaterializationError("Retrosheet bundle directory is not a real directory")
    root = directory.resolve()
    paths: dict[str, Path] = {}
    for filename in RETROSHEET_REQUIRED_FILENAMES:
        candidate = directory / filename
        if not candidate.exists() or not candidate.is_file() or candidate.is_symlink():
            raise Fd1MaterializationError(f"missing regular Retrosheet file {filename}")
        resolved = candidate.resolve()
        if root not in resolved.parents:
            raise Fd1MaterializationError(f"Retrosheet file escapes bundle root: {filename}")
        paths[filename] = resolved
    return root, MappingProxyType(paths)


def _validated_dispatch_temp_base(
    temporary_directory: Path | None, source_root: Path
) -> Path | None:
    if temporary_directory is None:
        return None
    base = Path(temporary_directory)
    if not base.exists() or not base.is_dir() or base.is_symlink():
        raise Fd1MaterializationError(
            "dispatcher temporary_directory must be an existing real directory"
        )
    resolved = base.resolve()
    if resolved == source_root or source_root in resolved.parents:
        raise Fd1MaterializationError(
            "dispatcher temporary staging cannot be created inside the raw bundle"
        )
    return resolved


def _game_exclusion_reason(
    row: Mapping[str, str],
    *,
    game_date: date,
    season: int,
    window: MaterializationWindow,
) -> str | None:
    """Mirror the one-window filter order for byte-identical dispatch results."""

    if game_date >= window.knowledge_cutoff:
        return "current_or_future"
    if season not in SUPPORTED_SEASONS:
        return "unsupported_season"
    if row["gametype"].strip().lower() not in _REGULAR_SEASON_TOKENS:
        return "non_regular_season"
    try:
        if int(row["vruns"].strip()) < 0 or int(row["hruns"].strip()) < 0:
            raise ValueError
    except ValueError:
        return "missing_or_invalid_final"
    suspend_token = row["suspend"].strip()
    if suspend_token:
        try:
            completion_date = _retrosheet_date(
                suspend_token, "gameinfo.suspend"
            )
        except RetrosheetImportError:
            return "suspended_missing_or_invalid_completion"
        if completion_date <= game_date:
            return "suspended_missing_or_invalid_completion"
        if completion_date >= window.knowledge_cutoff:
            return "suspended_not_final_by_cutoff"
        if (
            row["pbp"].strip().lower() not in {"y", "d"}
            or row["box"].strip().lower() not in {"y", "yes", "true", "1"}
            or row["lineups"].strip().lower() not in {"y", "yes", "true", "1"}
            or row["batteries"].strip().lower() != "both"
        ):
            return "suspended_incomplete_evidence"
    return None


def _scan_source_file_once(
    path: Path,
    filename: str,
    *,
    scanned: Counter[str],
    on_row: Callable[[int, Mapping[str, str]], None],
) -> str:
    """Hash and parse one source in the same sequential binary-file pass."""

    expected = RETROSHEET_CSV_HEADERS[filename]
    digest = sha256()

    def decoded_lines(stream: Any) -> Iterator[str]:
        first = True
        for encoded in stream:
            digest.update(encoded)
            try:
                decoded = encoded.decode("utf-8-sig" if first else "utf-8")
            except UnicodeDecodeError as exc:
                raise RetrosheetImportError(
                    f"{filename}: source is not valid UTF-8"
                ) from exc
            first = False
            yield decoded

    try:
        stream = path.open("rb")
    except OSError as exc:
        raise Fd1MaterializationError(f"unable to read {filename}: {exc}") from exc
    with stream:
        try:
            reader = csv.reader(decoded_lines(stream), strict=True)
            try:
                actual = tuple(next(reader))
            except StopIteration as exc:
                raise RetrosheetImportError(f"{filename}: missing header") from exc
            if actual != expected:
                raise RetrosheetImportError(
                    f"{filename}: header contract mismatch; expected {expected!r}, "
                    f"received {actual!r}"
                )
            index = MappingProxyType(
                {column: position for position, column in enumerate(expected)}
            )
            for row_number, fields in enumerate(reader, start=2):
                if len(fields) != len(expected):
                    raise RetrosheetImportError(
                        f"{filename}:{row_number}: malformed field count"
                    )
                if not any(fields):
                    raise RetrosheetImportError(
                        f"{filename}:{row_number}: blank row is not allowed"
                    )
                scanned[filename] += 1
                on_row(row_number, _CsvRowView(expected, fields, index))
        except csv.Error as exc:
            raise RetrosheetImportError(f"{filename}: malformed CSV: {exc}") from exc
    return digest.hexdigest()


def _spool_path(root: Path, key: tuple[int, int], filename: str) -> Path:
    season, month = key
    if season < 1800 or month < 1 or month > 12:
        raise Fd1MaterializationError("invalid monthly spool partition key")
    if filename not in RETROSHEET_CSV_HEADERS:
        raise Fd1MaterializationError("invalid monthly spool source filename")
    return root / f"{season:04d}-{month:02d}--{filename}"


def _read_spooled_rows(
    root: Path,
    key: tuple[int, int],
    filename: str,
    *,
    file_sha256: str,
) -> list[_SelectedRow]:
    path = _spool_path(root, key, filename)
    if not path.exists():
        return []
    expected = (
        _SPOOL_ROW_NUMBER,
        _SPOOL_ROW_CHECKSUM,
        *RETROSHEET_CSV_HEADERS[filename],
    )
    result: list[_SelectedRow] = []
    try:
        stream = path.open("r", encoding="utf-8", newline="")
    except OSError as exc:
        raise Fd1MaterializationError(f"unable to read transient {filename} spool") from exc
    with stream:
        try:
            reader = csv.DictReader(stream, strict=True)
            if tuple(reader.fieldnames or ()) != expected:
                raise Fd1MaterializationError(
                    f"transient {filename} spool header changed"
                )
            for spool_row_number, raw in enumerate(reader, start=2):
                if None in raw or set(raw) != set(expected):
                    raise Fd1MaterializationError(
                        f"transient {filename} spool row {spool_row_number} is malformed"
                    )
                try:
                    source_row_number = int(raw[_SPOOL_ROW_NUMBER])
                except ValueError as exc:
                    raise Fd1MaterializationError(
                        f"transient {filename} spool has an invalid source row number"
                    ) from exc
                if source_row_number < 2:
                    raise Fd1MaterializationError(
                        f"transient {filename} spool has an invalid source row number"
                    )
                row = {
                    column: str(raw[column])
                    for column in RETROSHEET_CSV_HEADERS[filename]
                }
                checksum = _source_row_checksum(filename, row)
                if checksum != raw[_SPOOL_ROW_CHECKSUM]:
                    raise Fd1MaterializationError(
                        f"transient {filename} spool source-row checksum changed"
                    )
                result.append(
                    _SelectedRow(
                        values=MappingProxyType(row),
                        reference=SourceRowReference(
                            filename=filename,
                            row_number=source_row_number,
                            row_checksum=checksum,
                            file_sha256=file_sha256,
                        ),
                    )
                )
        except csv.Error as exc:
            raise Fd1MaterializationError(
                f"transient {filename} spool is malformed"
            ) from exc
    return result


def _scan_rows(
    path: Path,
    filename: str,
    file_sha256: str,
    scanned: Counter[str],
) -> Iterator[_SelectedRow]:
    expected = RETROSHEET_CSV_HEADERS[filename]
    try:
        stream = path.open("r", encoding="utf-8-sig", newline="")
    except (OSError, UnicodeError) as exc:
        raise Fd1MaterializationError(f"unable to read {filename}: {exc}") from exc
    with stream:
        try:
            reader = csv.DictReader(stream, strict=True)
            actual = tuple(reader.fieldnames or ())
            if actual != expected:
                raise RetrosheetImportError(
                    f"{filename}: header contract mismatch; expected {expected!r}, "
                    f"received {actual!r}"
                )
            for row_number, raw in enumerate(reader, start=2):
                if None in raw or set(raw) != set(expected):
                    raise RetrosheetImportError(
                        f"{filename}:{row_number}: malformed field count"
                    )
                row = {column: str(raw[column]) for column in expected}
                if not any(row.values()):
                    raise RetrosheetImportError(
                        f"{filename}:{row_number}: blank row is not allowed"
                    )
                scanned[filename] += 1
                checksum = _source_row_checksum(filename, row)
                yield _SelectedRow(
                    values=MappingProxyType(row),
                    reference=SourceRowReference(
                        filename=filename,
                        row_number=row_number,
                        row_checksum=checksum,
                        file_sha256=file_sha256,
                    ),
                )
        except csv.Error as exc:
            raise RetrosheetImportError(f"{filename}: malformed CSV: {exc}") from exc


def _selected_csv_text(filename: str, rows: Sequence[_SelectedRow]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream,
        fieldnames=RETROSHEET_CSV_HEADERS[filename],
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(dict(selected.values) for selected in rows)
    return stream.getvalue()


def _source_reference_index(
    retained: Mapping[str, Sequence[_SelectedRow]],
) -> Mapping[tuple[str, str], SourceRowReference]:
    result: dict[tuple[str, str], SourceRowReference] = {}
    for filename, rows in retained.items():
        for selected in rows:
            key = (filename, _canonical_checksum(selected.values))
            previous = result.setdefault(key, selected.reference)
            if previous.row_checksum != selected.reference.row_checksum:
                raise RetrosheetImportError(
                    f"{filename}: non-deterministic source-row identity collision"
                )
    return MappingProxyType(result)


def _reference_for(
    source_refs: Mapping[tuple[str, str], SourceRowReference],
    filename: str,
    row: Mapping[str, str],
) -> SourceRowReference:
    try:
        return source_refs[(filename, _canonical_checksum(row))]
    except KeyError as exc:
        raise Fd1MaterializationError(
            f"parsed {filename} row has no original source-row reference"
        ) from exc


def _materialize_bundle(
    bundle: RetrosheetImportBundle,
    *,
    source_version_id: str,
    window: MaterializationWindow,
    source_file_checksums: Mapping[str, str],
    source_refs: Mapping[tuple[str, str], SourceRowReference],
    scanned: Counter[str],
    selected_counts: Counter[str],
    exclusions: Counter[str],
) -> RetrosheetMaterializationResult:
    games = {game.raw_game_id: game for game in bundle.games}
    gameinfo_rows = {
        row["gid"]: row for row in bundle.tables["gameinfo.csv"].rows
    }
    play_rows = {
        (row["gid"], _required_int(row["pn"], "plays.pn")): row
        for row in bundle.tables["plays.csv"].rows
    }
    lineage = {
        (filename, _canonical_checksum(row)): _reference_for(source_refs, filename, row)
        for filename, table in bundle.tables.items()
        for row in table.rows
    }

    orders = MappingProxyType(
        {
            game.source_game_id: GameOrder(
                game_id=game.source_game_id,
                game_date=game.game_date,
                completion_date=game.completion_date,
                game_number=game.game_number,
                doubleheader_id=game.doubleheader_id,
            )
            for game in sorted(bundle.games, key=_game_sort_key)
        }
    )
    lineup_by_player = {
        (entry.raw_game_id, entry.team_id, entry.player_id): entry
        for entry in bundle.lineups
    }
    value_pitching = {
        (record.raw_game_id, record.team_id, record.player_id): record
        for record in bundle.stats
        if record.filename == "pitching.csv" and record.is_point_estimate
    }
    value_teamstats = {
        (record.raw_game_id, record.team_id): record
        for record in bundle.stats
        if record.filename == "teamstats.csv" and record.is_point_estimate
    }

    family_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    transitions_by_game: dict[str, list[Any]] = defaultdict(list)
    for transition in bundle.plays:
        transitions_by_game[transition.raw_game_id].append(transition)

    for game in sorted(bundle.games, key=_game_sort_key):
        game_row = gameinfo_rows[game.raw_game_id]
        game_reference = lineage[("gameinfo.csv", _canonical_checksum(game_row))]
        common = _game_common(game, source_version_id)
        play_context = _plate_appearance_context(
            sorted(transitions_by_game.get(game.raw_game_id, ()), key=lambda row: row.play_number)
        )
        for transition in sorted(
            transitions_by_game.get(game.raw_game_id, ()), key=lambda row: row.play_number
        ):
            raw_play = play_rows[(game.raw_game_id, transition.play_number)]
            reference = lineage[("plays.csv", _canonical_checksum(raw_play))]
            pa_id = play_context[transition.play_number]
            automatic_runner_context = {
                "half_play_number": transition.half_play_number,
                "automatic_runner_placement_before": (
                    transition.automatic_runner_placement_before
                ),
                "automatic_runner_placement_mode": (
                    transition.automatic_runner_placement_mode
                ),
                "automatic_runner_base": (
                    game.tiebreaker_base
                    if transition.automatic_runner_placement_before
                    else None
                ),
                "automatic_runner_player_id": _source_player_or_none(
                    transition.automatic_runner_id
                ),
                "retrosheet_automatic_runner_id": transition.automatic_runner_id,
            }
            transition_row = {
                **common,
                **automatic_runner_context,
                "retrosheet_event_date": transition.event_date.isoformat(),
                "retrosheet_event_site": transition.site,
                "event_park_id": f"retrosheet:park:{transition.site}",
                "resumed_segment": transition.event_date != game.game_date,
                "event_site_changed": transition.site != game.site,
                "play_id": transition.source_play_id,
                "plate_appearance_id": pa_id,
                "play_number": transition.play_number,
                "inning": transition.inning,
                "top_bottom": transition.top_bottom,
                "batting_team_id": source_team_id(transition.batting_team),
                "pitching_team_id": source_team_id(transition.pitching_team),
                "batter_id": _source_player_or_none(transition.batter_id),
                "pitcher_id": _source_player_or_none(transition.pitcher_id),
                "event_text": transition.event_text,
                "retrosheet_lineup_slots": list(transition.lineup_slots),
                "lineup_player_ids": [
                    _source_player_or_none(player) for player in transition.lineup_slots
                ],
                "retrosheet_lineup_role_codes": list(
                    transition.lineup_fielding_positions
                ),
                "retrosheet_lineup_role_code_domain": "1_12_DEFENSE_DH_PH_PR",
                "pa_terminal_token": transition.pa_terminal_token,
                "pa_outcome": (
                    transition.pa_outcome.value if transition.pa_outcome is not None else None
                ),
                "outs_before": transition.before.outs,
                "base_mask_before": transition.before.base_mask,
                "runner_on_first_before": _source_player_or_none(
                    transition.before.runner_on_first
                ),
                "runner_on_second_before": _source_player_or_none(
                    transition.before.runner_on_second
                ),
                "runner_on_third_before": _source_player_or_none(
                    transition.before.runner_on_third
                ),
                "visiting_score_before": transition.before.visiting_score,
                "home_score_before": transition.before.home_score,
                "outs_after": transition.after.outs,
                "base_mask_after": transition.after.base_mask,
                "runner_on_first_after": _source_player_or_none(
                    transition.after.runner_on_first
                ),
                "runner_on_second_after": _source_player_or_none(
                    transition.after.runner_on_second
                ),
                "runner_on_third_after": _source_player_or_none(
                    transition.after.runner_on_third
                ),
                "visiting_score_after": transition.after.visiting_score,
                "home_score_after": transition.after.home_score,
                "runs_scored": transition.runs_scored,
                "timing_risk_label": transition.timing_risk_label,
                "target_only_not_pregame_feature": True,
                "strict_same_game_pregame_eligible": False,
                "enhanced_research_eligible": True,
            }
            family_rows["play_transition"].append(_finalize_row(transition_row, reference))
            if transition.pa_terminal_token is not None:
                pa_row = {
                    **common,
                    **automatic_runner_context,
                    "retrosheet_event_date": transition.event_date.isoformat(),
                    "retrosheet_event_site": transition.site,
                    "event_park_id": f"retrosheet:park:{transition.site}",
                    "resumed_segment": transition.event_date != game.game_date,
                    "event_site_changed": transition.site != game.site,
                    "plate_appearance_id": pa_id,
                    "terminal_play_id": transition.source_play_id,
                    "terminal_play_number": transition.play_number,
                    "inning": transition.inning,
                    "top_bottom": transition.top_bottom,
                    "batting_team_id": source_team_id(transition.batting_team),
                    "pitching_team_id": source_team_id(transition.pitching_team),
                    "batter_id": _source_player_or_none(transition.batter_id),
                    "pitcher_id": _source_player_or_none(transition.pitcher_id),
                    "source_terminal_token": transition.pa_terminal_token,
                    "outcome": transition.pa_outcome.value,
                    "outs_before": transition.before.outs,
                    "base_mask_before": transition.before.base_mask,
                    "visiting_score_before": transition.before.visiting_score,
                    "home_score_before": transition.before.home_score,
                    "outs_after": transition.after.outs,
                    "base_mask_after": transition.after.base_mask,
                    "runs_scored": transition.runs_scored,
                    "taxonomy_version": "retrosheet_to_mlb_gen2_pa_v1",
                    "timing_risk_label": RETROSPECTIVE_EVENT_TIMING_RISK,
                    "target_only_not_pregame_feature": True,
                    "strict_same_game_pregame_eligible": False,
                    "enhanced_research_eligible": True,
                }
                family_rows["plate_appearance"].append(_finalize_row(pa_row, reference))
            raw_sequence = raw_play["pitches"].strip()
            if raw_sequence:
                pitch_row = {
                    **common,
                    **automatic_runner_context,
                    "retrosheet_event_date": transition.event_date.isoformat(),
                    "retrosheet_event_site": transition.site,
                    "event_park_id": f"retrosheet:park:{transition.site}",
                    "resumed_segment": transition.event_date != game.game_date,
                    "event_site_changed": transition.site != game.site,
                    "pitch_sequence_id": transition.source_play_id,
                    "play_id": transition.source_play_id,
                    "plate_appearance_id": pa_id,
                    "play_number": transition.play_number,
                    "batter_id": _source_player_or_none(transition.batter_id),
                    "pitcher_id": _source_player_or_none(transition.pitcher_id),
                    "source_pitch_sequence": raw_sequence,
                    "source_pitch_code_characters": list(raw_sequence),
                    "coverage_indicator": True,
                    "uncertainty": "SOURCE_CODES_AND_MODIFIERS_PRESERVED_UNINTERPRETED",
                    "source_event_granularity": (
                        "RETROSHEET_PLAY_PITCH_SEQUENCE_NOT_STATCAST_PITCH"
                    ),
                    "statcast_pitch_event": False,
                    "velocity_available": False,
                    "movement_available": False,
                    "location_available": False,
                    "contact_tracking_available": False,
                    "strict_same_game_pregame_eligible": False,
                    "enhanced_research_eligible": True,
                }
                family_rows["pitch_sequence"].append(_finalize_row(pitch_row, reference))

        official_scorers = _official_scorer_fields(
            game_row["oscorer"],
            game=game,
            reference=game_reference,
        )
        umpire_ids = {
            role: _retrosheet_person_token(game_row[column])
            for role, column in (
                ("home", "umphome"),
                ("first_base", "ump1b"),
                ("second_base", "ump2b"),
                ("third_base", "ump3b"),
                ("left_field", "umplf"),
                ("right_field", "umprf"),
            )
        }
        weather_row = {
            **common,
            "weather_id": f"retrosheet:weather:{game.raw_game_id}",
            "park_id": f"retrosheet:park:{game.site}",
            "start_time_source": _none_if_blank(game_row["starttime"]),
            "day_night": _none_if_blank(game_row["daynight"]),
            "field_condition": _none_if_blank(game_row["fieldcond"]),
            "precipitation": _none_if_blank(game_row["precip"]),
            "sky": _none_if_blank(game_row["sky"]),
            "temperature_f": _optional_int(game_row["temp"], "gameinfo.temp"),
            "wind_direction": _none_if_blank(game_row["winddir"]),
            "wind_speed_mph": _optional_int(game_row["windspeed"], "gameinfo.windspeed"),
            "weather_coverage": _weather_coverage(game_row),
            "weather_source": RETROSHEET_OPEN_DATA,
            **official_scorers,
            "umpire_home_id": _source_player_or_none(umpire_ids["home"]),
            "umpire_first_base_id": _source_player_or_none(
                umpire_ids["first_base"]
            ),
            "umpire_second_base_id": _source_player_or_none(
                umpire_ids["second_base"]
            ),
            "umpire_third_base_id": _source_player_or_none(
                umpire_ids["third_base"]
            ),
            "umpire_left_field_id": _source_player_or_none(
                umpire_ids["left_field"]
            ),
            "umpire_right_field_id": _source_player_or_none(
                umpire_ids["right_field"]
            ),
            "retrosheet_umpire_ids": {
                role: _none_if_blank(game_row[column])
                for role, column in (
                    ("home", "umphome"),
                    ("first_base", "ump1b"),
                    ("second_base", "ump2b"),
                    ("third_base", "ump3b"),
                    ("left_field", "umplf"),
                    ("right_field", "umprf"),
                )
            },
            "noaa_gap_fill": False,
            "observation_timestamp_known": False,
            "timing_risk_label": "RETROSPECTIVE_GAME_WEATHER_TIMING_RISK",
            "strict_same_game_pregame_eligible": False,
            "enhanced_research_eligible": True,
        }
        family_rows["weather"].append(_finalize_row(weather_row, game_reference))

    for record in sorted(bundle.stats, key=lambda item: (item.filename, item.row_number)):
        if record.filename not in {"batting.csv", "pitching.csv", "fielding.csv"}:
            continue
        game = games[record.raw_game_id]
        reference = lineage[(record.filename, _canonical_checksum(record.values))]
        family = {
            "batting.csv": "player_game_batting",
            "pitching.csv": "player_game_pitching",
            "fielding.csv": "player_game_fielding",
        }[record.filename]
        row = _player_stat_row(
            record,
            game,
            source_version_id,
            lineup_by_player.get((record.raw_game_id, record.team_id, record.player_id or "")),
        )
        family_rows[family].append(_finalize_row(row, reference))

    for appearance in sorted(
        bundle.pitchers,
        key=lambda item: (item.raw_game_id, item.team_id, item.appearance_sequence),
    ):
        record = value_pitching[
            (appearance.raw_game_id, appearance.team_id, appearance.player_id)
        ]
        game = games[appearance.raw_game_id]
        reference = lineage[("pitching.csv", _canonical_checksum(record.values))]
        family = (
            "starter_usage"
            if appearance.role is RetrosheetPitcherRole.STARTER
            else "bullpen_usage"
        )
        row = _pitcher_usage_row(appearance, record, game, source_version_id)
        team_record = value_teamstats[(appearance.raw_game_id, appearance.team_id)]
        team_reference = lineage[
            ("teamstats.csv", _canonical_checksum(team_record.values))
        ]
        raw_manager_id = team_record.values["mgr"].strip()
        row.update(
            {
                "manager_id": _source_player_or_none(raw_manager_id),
                "retrosheet_manager_id": raw_manager_id or None,
                "manager_evidence_source_filename": team_reference.filename,
                "manager_evidence_source_row_number": team_reference.row_number,
                "manager_evidence_source_row_checksum": team_reference.row_checksum,
                "manager_evidence_source_file_sha256": team_reference.file_sha256,
            }
        )
        family_rows[family].append(_finalize_row(row, reference))

    batches = _make_batches(
        family_rows,
        window=window,
        source_file_checksums=source_file_checksums,
        source_bundle_checksum=bundle.bundle_checksum,
    )
    stattypes = Counter(record.stattype.value for record in bundle.stats)
    quality = Counter(game.pbp_quality.value for game in bundle.games)
    suspended_games = tuple(game for game in bundle.games if game.suspended)
    suspended_game_ids = {game.raw_game_id for game in suspended_games}
    relocated_suspended_game_ids = {
        transition.raw_game_id
        for transition in bundle.plays
        if transition.raw_game_id in suspended_game_ids
        and transition.site != games[transition.raw_game_id].site
    }
    automatic_runner_modes = Counter(
        transition.automatic_runner_placement_mode
        for transition in bundle.plays
        if transition.automatic_runner_placement_before
    )
    family_counts = Counter({family: len(rows) for family, rows in family_rows.items()})
    counters = MaterializationCounters(
        scanned_rows=MappingProxyType(
            {filename: scanned.get(filename, 0) for filename in RETROSHEET_CSV_HEADERS}
        ),
        selected_rows=MappingProxyType(
            {
                filename: selected_counts.get(filename, 0)
                for filename in RETROSHEET_CSV_HEADERS
            }
        ),
        exclusion_counts=MappingProxyType(dict(sorted(exclusions.items()))),
        selected_games=len(bundle.games),
        primary_games=sum(game.season in PRIMARY_SEASONS for game in bundle.games),
        prior_only_games=sum(game.season in PRIOR_ONLY_SEASONS for game in bundle.games),
        full_pbp_games=quality["FULL"],
        deduced_pbp_games=quality["DEDUCED"],
        no_pbp_games=quality["NONE"],
        completed_suspended_games=len(suspended_games),
        relocated_suspended_games=len(relocated_suspended_game_ids),
        suspended_original_segment_plays=sum(
            transition.raw_game_id in suspended_game_ids
            and transition.event_date == games[transition.raw_game_id].game_date
            for transition in bundle.plays
        ),
        suspended_completion_segment_plays=sum(
            transition.raw_game_id in suspended_game_ids
            and transition.event_date == games[transition.raw_game_id].completion_date
            for transition in bundle.plays
        ),
        automatic_runner_placements=sum(automatic_runner_modes.values()),
        automatic_runner_direct_placements=automatic_runner_modes[
            AUTOMATIC_RUNNER_DIRECT_FIRST_PLAY
        ],
        automatic_runner_np_bridge_placements=automatic_runner_modes[
            AUTOMATIC_RUNNER_AFTER_NP_BRIDGE
        ],
        lineup_position_reconciliations=sum(
            entry.position_reconciled_from_play_state for entry in bundle.lineups
        ),
        lineup_nonstarting_role_code_occurrences=(
            bundle.lineup_nonstarting_role_code_occurrence_count
        ),
        stattype_rows=MappingProxyType(dict(sorted(stattypes.items()))),
        family_rows=MappingProxyType(
            {family: family_counts.get(family, 0) for family in MATERIALIZED_FAMILIES}
        ),
        reconciliation_checks=MappingProxyType(
            {
                "exact_seven_file_schema": True,
                "identity_references": True,
                "plate_appearance_taxonomy": True,
                "base_out_conservation": True,
                "runner_conservation": True,
                "score_conservation": True,
                "starter_sequence": True,
                "current_and_future_excluded": True,
                "actual_participant_timing_isolated": True,
                "statcast_claims_absent": True,
                "completed_suspension_semantics": True,
                "automatic_runner_rule_placements": True,
                "zero_stat_lineup_position_reconciliation": True,
            }
        ),
    )
    build_document = {
        "source_version_id": source_version_id,
        "window": {
            "start": window.start_date.isoformat(),
            "end": window.end_date.isoformat(),
            "knowledge_cutoff": window.knowledge_cutoff.isoformat(),
        },
        "source_file_checksums": dict(source_file_checksums),
        "selected_bundle_checksum": bundle.bundle_checksum,
        "partition_checksums": [batch.partition_checksum for batch in batches],
        "counters": counters.as_dict(),
    }
    return RetrosheetMaterializationResult(
        source_version_id=source_version_id,
        window=window,
        source_file_checksums=source_file_checksums,
        selected_bundle_checksum=bundle.bundle_checksum,
        batches=batches,
        game_orders=orders,
        counters=counters,
        build_checksum=_canonical_checksum(build_document),
    )


def _game_common(game: RetrosheetGame, source_version_id: str) -> dict[str, Any]:
    population = (
        GEN2_PRIMARY_2023_2025
        if game.season in PRIMARY_SEASONS
        else GEN2_PRIOR_ONLY_2021_2022
    )
    return {
        "source_key": RETROSHEET_SOURCE_KEY,
        "source_mode": RETROSHEET_OPEN_DATA,
        "source_version_id": source_version_id,
        "evidence_mode": RETROSPECTIVE_RECONSTRUCTION,
        "population_category": population,
        "strict_population": GEN2_STRICT_DEPLOYABLE_RECONSTRUCTION,
        "game_id": game.source_game_id,
        "retrosheet_game_id": game.raw_game_id,
        "game_date": game.game_date.isoformat(),
        "event_date": game.game_date.isoformat(),
        "season": game.season,
        "month": game.game_date.month,
        "game_number": game.game_number,
        "doubleheader_id": game.doubleheader_id,
        "visiting_team_id": game.visiting_source_team_id,
        "home_team_id": game.home_source_team_id,
        "park_id": f"retrosheet:park:{game.site}",
        "game_type": game.gametype,
        "regulation_innings": game.regulation_innings,
        "tiebreaker_base": game.tiebreaker_base,
        "designated_hitter_used": game.designated_hitter_used,
        "pbp_quality": game.pbp_quality.value,
        "game_suspended": game.suspended,
        "suspension_completion_date": (
            game.completion_date.isoformat() if game.completion_date is not None else None
        ),
        "final_available_date": game.final_available_date.isoformat(),
        "historical_reconstruction": True,
        "available_only_after_game_final": True,
        "future_evidence_allowed": False,
        "identity_namespace": "RETROSHEET",
        "canonical_identity_resolved": False,
    }


def _plate_appearance_context(transitions: Sequence[Any]) -> Mapping[int, str | None]:
    result: dict[int, str | None] = {}
    pa_ordinal = 1
    pending: list[int] = []
    for transition in transitions:
        pending.append(transition.play_number)
        if transition.pa_terminal_token is not None:
            pa_id = f"retrosheet:pa:{transition.raw_game_id}:{pa_ordinal}"
            result.update({play_number: pa_id for play_number in pending})
            pending.clear()
            pa_ordinal += 1
    # A game-ending non-PA action (for example a caught stealing) is not silently
    # promoted into a plate appearance merely to increase coverage.
    result.update({play_number: None for play_number in pending})
    return MappingProxyType(result)


def _player_stat_row(
    record: RetrosheetStatRecord,
    game: RetrosheetGame,
    source_version_id: str,
    lineup_entry: Any | None,
) -> dict[str, Any]:
    row = record.values
    result: dict[str, Any] = {
        **_game_common(game, source_version_id),
        "player_game_stat_id": (
            f"retrosheet:stat:{record.filename}:{record.raw_game_id}:"
            f"{record.row_number}"
        ),
        "player_id": source_player_id(record.player_id or ""),
        "retrosheet_player_id": record.player_id,
        "team_id": source_team_id(record.team_id),
        "stattype": record.stattype.value,
        "stat_semantics": record.semantics.value,
        "is_point_estimate": record.is_point_estimate,
        "uncertainty_preserved": True,
        "strict_prior_eligible_after_game_final": True,
        "strict_same_game_pregame_eligible": False,
        "enhanced_research_eligible": True,
    }
    if record.filename == "batting.csv":
        result.update(_numeric_fields(row, "b_"))
        result.update(
            {
                "lineup_position": _optional_int(row["b_lp"], "batting.b_lp"),
                "appearance_sequence": _optional_int(row["b_seq"], "batting.b_seq"),
                "actual_starting_lineup": lineup_entry is not None,
                "actual_batting_order": (
                    lineup_entry.batting_order if lineup_entry is not None else None
                ),
                "actual_fielding_positions": (
                    list(lineup_entry.fielding_positions) if lineup_entry is not None else []
                ),
                "actual_fielding_positions_source": (
                    lineup_entry.fielding_positions_source
                    if lineup_entry is not None
                    else None
                ),
                "position_reconciled_from_play_state": (
                    lineup_entry.position_reconciled_from_play_state
                    if lineup_entry is not None
                    else False
                ),
                "teamstats_position_discrepancy": (
                    lineup_entry.teamstats_position_discrepancy
                    if lineup_entry is not None
                    else False
                ),
                "position_proof_play_id": (
                    lineup_entry.position_proof_play_id
                    if lineup_entry is not None
                    else None
                ),
                "position_proof_event_date": (
                    lineup_entry.position_proof_event_date.isoformat()
                    if lineup_entry is not None
                    and lineup_entry.position_proof_event_date is not None
                    else None
                ),
                "actual_lineup_timing_risk": (
                    HISTORICAL_ACTUAL_LINEUP_TIMING_RISK
                    if lineup_entry is not None
                    else None
                ),
                "enhanced_population": (
                    GEN2_ACTUAL_LINEUP_UPPER_BOUND if lineup_entry is not None else None
                ),
                "actual_lineup_never_strict_pregame": lineup_entry is not None,
            }
        )
    elif record.filename == "pitching.csv":
        result.update(_numeric_fields(row, "p_"))
        result.update(
            {
                "appearance_sequence": _optional_int(row["p_seq"], "pitching.p_seq"),
                "actual_start_flag": _optional_int(row["p_gs"], "pitching.p_gs"),
            }
        )
    else:
        result.update(_numeric_fields(row, "d_"))
        result.update(
            {
                "appearance_sequence": _optional_int(row["d_seq"], "fielding.d_seq"),
                "fielding_position": _optional_int(row["d_pos"], "fielding.d_pos"),
                "actual_fielding_only_not_oaa": True,
                "outs_above_average_available": False,
            }
        )
    return result


def _pitcher_usage_row(
    appearance: RetrosheetPitcherAppearance,
    record: RetrosheetStatRecord,
    game: RetrosheetGame,
    source_version_id: str,
) -> dict[str, Any]:
    is_starter = appearance.role is RetrosheetPitcherRole.STARTER
    row: dict[str, Any] = {
        **_game_common(game, source_version_id),
        "usage_id": (
            f"retrosheet:usage:{appearance.raw_game_id}:{appearance.team_id}:"
            f"{appearance.appearance_sequence}"
        ),
        "player_id": appearance.source_player_id,
        "retrosheet_player_id": appearance.player_id,
        "team_id": appearance.source_team_id,
        "appearance_sequence": appearance.appearance_sequence,
        "pitcher_role": appearance.role.value,
        "actual_appearance_target": True,
        "actual_starter_target": is_starter,
        "participant_evidence": (
            ACTUAL_STARTER_HISTORICAL_RECONSTRUCTION
            if is_starter
            else "ACTUAL_RELIEVER_HISTORICAL_RECONSTRUCTION"
        ),
        "evidence_kind": appearance.evidence_kind,
        "temporal_semantics": RETROSPECTIVE_ACTUAL_TIMING_SEMANTICS,
        "timing_risk_label": (
            HISTORICAL_ACTUAL_STARTER_TIMING_RISK
            if is_starter
            else HISTORICAL_ACTUAL_RELIEVER_TIMING_RISK
        ),
        "strict_same_game_pregame_eligible": False,
        "strict_prior_eligible_after_game_final": True,
        "enhanced_research_eligible": True,
        "announcement_time_known": False,
        "stattype": record.stattype.value,
        "stat_semantics": record.semantics.value,
    }
    row.update(_numeric_fields(record.values, "p_"))
    return row


def _make_batches(
    family_rows: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    window: MaterializationWindow,
    source_file_checksums: Mapping[str, str],
    source_bundle_checksum: str,
) -> tuple[FamilyStagingPayload, ...]:
    grouped: dict[tuple[str, int, int], list[Mapping[str, Any]]] = defaultdict(list)
    for family, rows in family_rows.items():
        for row in rows:
            grouped[(family, int(row["season"]), int(row["month"]))].append(row)

    payloads: list[FamilyStagingPayload] = []
    family_rank = {family: index for index, family in enumerate(MATERIALIZED_FAMILIES)}
    for (family, season, month), rows in sorted(
        grouped.items(), key=lambda item: (item[0][1], item[0][2], family_rank[item[0][0]])
    ):
        if not rows:
            continue
        ordered = tuple(
            MappingProxyType(dict(row))
            for row in sorted(
                rows,
                key=lambda row: (
                    str(row.get("game_date", "")),
                    int(row.get("game_number", 0)),
                    str(row.get("game_id", "")),
                    str(row.get("play_number", "")),
                    str(row.get("appearance_sequence", "")),
                    str(row.get("player_id", "")),
                    str(row.get("row_checksum", "")),
                ),
            )
        )
        games = {str(row["game_id"]) for row in ordered}
        pas = {
            str(row["plate_appearance_id"])
            for row in ordered
            if row.get("plate_appearance_id")
        }
        players: set[str] = set()
        for row in ordered:
            for field in ("player_id", "batter_id", "pitcher_id"):
                if row.get(field):
                    players.add(str(row[field]))
        dates = [date.fromisoformat(str(row["game_date"])) for row in ordered]
        schema_version = MATERIALIZED_FAMILY_SCHEMA_VERSIONS[family]
        partition_checksum = _canonical_checksum(
            {
                "family": family,
                "season": season,
                "month": month,
                "schema_version": schema_version,
                "source_bundle_checksum": source_bundle_checksum,
                "rows": [dict(row) for row in ordered],
            }
        )
        scope = (
            "COMPLETE_MONTH_PARTITION"
            if window.is_full_month(season, month)
            else "PILOT_OR_PARTIAL_MONTH_STAGING"
        )
        payloads.append(
            FamilyStagingPayload(
                family=family,
                season=season,
                month=month,
                schema_version=schema_version,
                publication_scope=scope,
                rows=ordered,
                row_count=len(ordered),
                game_count=len(games),
                plate_appearance_count=len(pas),
                player_count=len(players),
                min_event_date=min(dates),
                max_event_date=max(dates),
                source_file_checksums=source_file_checksums,
                source_bundle_checksum=source_bundle_checksum,
                partition_checksum=partition_checksum,
                leakage_policy=(
                    "RETROSPECTIVE_TARGETS_AND_POSTGAME_PRIORS_ONLY; CURRENT_GAME_AND_"
                    "FUTURE_EXCLUDED; ACTUAL_LINEUP_STARTER_RELIEVER_FIELDS_ENHANCED_ONLY"
                ),
                quality_report=MappingProxyType(
                    {
                        "coalescing_policy": "ONE_FILE_PER_FAMILY_SEASON_MONTH",
                        "partial_month": scope != "COMPLETE_MONTH_PARTITION",
                        "source_rows_have_checksums": True,
                        "retrosheet_not_statcast": True,
                        "completed_suspended_games": len(
                            {
                                str(row["game_id"])
                                for row in ordered
                                if row.get("game_suspended") is True
                            }
                        ),
                        "suspended_game_rows": sum(
                            row.get("game_suspended") is True for row in ordered
                        ),
                        "suspended_completion_event_rows": sum(
                            row.get("game_suspended") is True
                            and row.get("suspension_completion_date") is not None
                            and row.get("retrosheet_event_date")
                            == row.get("suspension_completion_date")
                            for row in ordered
                        ),
                        "relocated_suspended_event_rows": sum(
                            row.get("game_suspended") is True
                            and row.get("event_site_changed") is True
                            for row in ordered
                        ),
                        "automatic_runner_placement_rows": sum(
                            row.get("automatic_runner_placement_before") is True
                            for row in ordered
                        ),
                        "automatic_runner_direct_rows": sum(
                            row.get("automatic_runner_placement_mode")
                            == AUTOMATIC_RUNNER_DIRECT_FIRST_PLAY
                            for row in ordered
                        ),
                        "automatic_runner_np_bridge_rows": sum(
                            row.get("automatic_runner_placement_mode")
                            == AUTOMATIC_RUNNER_AFTER_NP_BRIDGE
                            for row in ordered
                        ),
                        "lineup_position_reconciliation_rows": sum(
                            row.get("position_reconciled_from_play_state") is True
                            for row in ordered
                        ),
                        "teamstats_position_discrepancy_rows": sum(
                            row.get("teamstats_position_discrepancy") is True
                            for row in ordered
                        ),
                        "lineup_nonstarting_role_code_occurrences": sum(
                            sum(
                                code in {11, 12}
                                for code in row.get(
                                    "retrosheet_lineup_role_codes", ()
                                )
                            )
                            for row in ordered
                        ),
                        "positionless_actual_lineup_rows": sum(
                            int(
                                row.get("actual_starting_lineup") is True
                                and not row.get("actual_fielding_positions")
                            )
                            for row in ordered
                        ),
                        "manager_assignment_rows": sum(
                            row.get("manager_id") is not None for row in ordered
                        ),
                        "official_scorer_game_rows": sum(
                            bool(row.get("official_scorer_ids")) for row in ordered
                        ),
                        "official_scorer_assignments": sum(
                            len(row.get("official_scorer_ids", ())) for row in ordered
                        ),
                        "multi_official_scorer_games": sum(
                            len(row.get("official_scorer_ids", ())) > 1
                            for row in ordered
                        ),
                        "umpire_game_rows": sum(
                            row.get("umpire_home_id") is not None for row in ordered
                        ),
                        "umpire_role_assignments": sum(
                            row.get(field) is not None
                            for row in ordered
                            for field in (
                                "umpire_home_id",
                                "umpire_first_base_id",
                                "umpire_second_base_id",
                                "umpire_third_base_id",
                                "umpire_left_field_id",
                                "umpire_right_field_id",
                            )
                        ),
                    }
                ),
            )
        )
    keys = [payload.partition_key for payload in payloads]
    if len(keys) != len(set(keys)):
        raise Fd1MaterializationError("tiny-file guard found duplicate partition payloads")
    return tuple(payloads)


def _finalize_row(
    row: Mapping[str, Any], reference: SourceRowReference
) -> dict[str, Any]:
    result = dict(row)
    result.update(
        {
            "source_filename": reference.filename,
            "source_row_number": reference.row_number,
            "source_row_checksum": reference.row_checksum,
            "source_file_sha256": reference.file_sha256,
        }
    )
    result["row_checksum"] = _row_output_checksum(result)
    return result


def _row_output_checksum(row: Mapping[str, Any]) -> str:
    return _canonical_checksum({key: value for key, value in row.items() if key != "row_checksum"})


def _numeric_fields(row: Mapping[str, str], prefix: str) -> dict[str, int | None]:
    excluded = {f"{prefix}lp", f"{prefix}seq", f"{prefix}pos"}
    return {
        f"stat_{column}": _optional_int(value, column)
        for column, value in row.items()
        if column.startswith(prefix) and column not in excluded
    }


def _optional_int(value: object, context: str) -> int | None:
    token = str(value).strip()
    if not token:
        return None
    try:
        return int(token)
    except ValueError as exc:
        raise RetrosheetImportError(f"{context}: expected an integer, got {value!r}") from exc


def _required_int(value: object, context: str) -> int:
    parsed = _optional_int(value, context)
    if parsed is None:
        raise RetrosheetImportError(f"{context}: integer is required")
    return parsed


def _source_player_or_none(raw_player_id: str | None) -> str | None:
    return source_player_id(raw_player_id) if raw_player_id else None


def _retrosheet_person_token(value: object) -> str | None:
    """Normalize only Retrosheet's documented missing-person cell shape.

    Daily Logs encode an unassigned umpire (including every modern left- and
    right-field slot) as the literal ``(none)`` rather than an empty CSV cell.
    All other nonblank tokens remain exact source identifiers and therefore
    still pass through :func:`source_player_id`'s fail-closed ID grammar.  The
    raw cell remains separately retained in the materialized audit columns.
    """

    token = str(value).strip()
    return None if token in {"", "(none)"} else token


def _official_scorer_fields(
    value: object,
    *,
    game: RetrosheetGame,
    reference: SourceRowReference,
) -> dict[str, Any]:
    """Normalize the source-ordered official-scorer collection without guessing.

    Within the exact FD1 2021--2025 target release slice, one compound ``oscorer``
    cell exists: ``BOS202507010`` stores two source-ordered IDs and declares a
    suspended-game completion date.  This is a frozen target-release contract,
    not a claim about universal Retrosheet semantics.
    The local source does *not* assign either component to a specific game segment
    or date.  A semicolon is therefore accepted only for exactly two distinct,
    valid Retrosheet IDs on a completed suspended game, while the dates remain
    collection-level context.  Repeated source-row evidence keeps each normalized
    ID traceable to the exact raw cell and component order.
    """

    raw_token = _none_if_blank(value)
    normalized = _retrosheet_person_token(value)
    if normalized is None:
        components: tuple[str, ...] = ()
        semantics = "MISSING_RETROSHEET_OFFICIAL_SCORER"
    elif ";" not in normalized:
        components = (normalized,)
        semantics = "SINGLE_SOURCE_OFFICIAL_SCORER_GAME_LEVEL_DATES_ONLY"
    else:
        components = tuple(normalized.split(";"))
        if any(not component or component != component.strip() for component in components):
            raise RetrosheetImportError(
                f"{game.raw_game_id}.oscorer: compound scorer has a blank or padded component"
            )
        if len(components) != 2:
            raise RetrosheetImportError(
                f"{game.raw_game_id}.oscorer: compound scorer must contain exactly two IDs"
            )
        if len(set(components)) != len(components):
            raise RetrosheetImportError(
                f"{game.raw_game_id}.oscorer: compound scorer IDs must be distinct"
            )
        if not game.suspended or game.completion_date is None:
            raise RetrosheetImportError(
                f"{game.raw_game_id}.oscorer: compound scorer requires a completed "
                "suspended game"
            )
        semantics = (
            "ORDERED_SOURCE_COMPONENTS_GAME_LEVEL_DATES_ONLY_"
            "NO_COMPONENT_DATE_ASSIGNMENT"
        )

    namespaced_ids = tuple(source_player_id(component) for component in components)
    component_count = len(components)
    return {
        # Backward-compatible singular fields are populated only when the source
        # cell itself identifies exactly one scorer; a compound cell is never
        # silently collapsed to its first component.
        "official_scorer_id": namespaced_ids[0] if component_count == 1 else None,
        "retrosheet_official_scorer_id": raw_token,
        "official_scorer_ids": list(namespaced_ids),
        "retrosheet_official_scorer_ids": list(components),
        "official_scorer_collection_game_date": game.game_date.isoformat(),
        "official_scorer_collection_completion_date": (
            game.completion_date.isoformat() if game.completion_date else None
        ),
        "official_scorer_component_date_assignment": "UNAVAILABLE",
        "official_scorer_source_positions": list(range(1, component_count + 1)),
        "official_scorer_source_filenames": [reference.filename] * component_count,
        "official_scorer_source_row_numbers": [reference.row_number] * component_count,
        "official_scorer_source_row_checksums": [reference.row_checksum] * component_count,
        "official_scorer_collection_contract": (
            "FROZEN_RETROSHEET_THROUGH_2025_TARGET_SEASONS_2021_2025"
        ),
        "official_scorer_collection_semantics": semantics,
    }


def _none_if_blank(value: object) -> str | None:
    token = str(value).strip()
    return token or None


def _weather_coverage(row: Mapping[str, str]) -> str:
    fields = ("fieldcond", "precip", "sky", "temp", "winddir", "windspeed")
    present = sum(bool(row[field].strip()) for field in fields)
    if present == 0:
        return "NONE_RETROSHEET_NOAA_GAP_REQUIRED"
    if present == len(fields):
        return "COMPLETE_RETROSHEET"
    return "PARTIAL_RETROSHEET_NOAA_GAP_REQUIRED"


def _game_sort_key(game: RetrosheetGame) -> tuple[date, str, int, str]:
    return game.game_date, game.home_team, game.game_number, game.raw_game_id


def _source_row_checksum(filename: str, row: Mapping[str, str]) -> str:
    return _canonical_checksum({"filename": filename, "values": dict(row)})


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_FILE_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(
        _plain_json(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json(item) for item in value]
    if isinstance(value, date):
        return value.isoformat()
    return value


def _canonical_checksum(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


__all__ = [
    "ACTUAL_STARTER_HISTORICAL_RECONSTRUCTION",
    "DEFAULT_MAX_GAMES",
    "DEFAULT_MAXIMUM_DISPATCH_SPOOL_BYTES",
    "DEFAULT_MAX_SELECTED_BYTES",
    "DEFAULT_MAX_SELECTED_ROWS",
    "FamilyStagingPayload",
    "Fd1MaterializationError",
    "GEN2_ACTUAL_LINEUP_UPPER_BOUND",
    "GEN2_PRIMARY_2023_2025",
    "GEN2_PRIOR_ONLY_2021_2022",
    "GEN2_STRICT_DEPLOYABLE_RECONSTRUCTION",
    "GameOrder",
    "MATERIALIZED_FAMILIES",
    "MATERIALIZED_FAMILY_SCHEMA_VERSIONS",
    "MAX_WINDOW_DAYS",
    "MaterializationCounters",
    "MaterializationWindow",
    "RETROSHEET_OPEN_DATA",
    "RetrosheetMaterializationResult",
    "SourceRowReference",
    "is_strictly_prior_game",
    "iter_month_windows",
    "iter_retrosheet_monthly_materializations",
    "iter_strict_prior_rows",
    "materialize_retrosheet_window",
    "verify_payload_checksum",
]

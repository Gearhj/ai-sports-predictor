from __future__ import annotations

from collections import Counter
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any

import polars as pl
import pytest

import app.gen2.exchange.identity_bridge.parent as parent


RAW_ALPHA = "alpha001"
RAW_BETA = "bravo002"
RAW_GAMMA = "gamma003"
RAW_DELTA = "delta004"
PREFIX = "retrosheet:player:"
CANONICAL_UUID = "550e8400-e29b-41d4-a716-446655440000"


def _field(name: str, dtype: str) -> SimpleNamespace:
    return SimpleNamespace(name=name, dtype=dtype)


def _identity_partition(
    family: str,
    season: int,
    uri: str,
    row_count: int,
    fields: tuple[tuple[str, str], ...],
) -> SimpleNamespace:
    anchor = parent._FAMILY_ANCHORS[family]
    return SimpleNamespace(
        family=family,
        season=season,
        month=12 if family == "player_prior" else 4,
        package_relative_uri=uri,
        row_count=row_count,
        schema_id=anchor.schema_id,
        source_key=anchor.source_key,
        source_version_id=anchor.source_version_id,
        file_sha256="f" * 64,
        physical_schema=tuple(_field(name, dtype) for name, dtype in fields),
    )


def _plate_appearance_partition(
    season: int, uri: str, row_count: int
) -> SimpleNamespace:
    return _identity_partition(
        "plate_appearance",
        season,
        uri,
        row_count,
        (
            ("season", "Int64"),
            ("identity_namespace", "String"),
            ("canonical_identity_resolved", "Boolean"),
            ("batter_id", "String"),
            ("pitcher_id", "String"),
            ("automatic_runner_player_id", "String"),
            ("retrosheet_automatic_runner_id", "String"),
        ),
    )


def _prior_partition(season: int, uri: str, row_count: int) -> SimpleNamespace:
    return _identity_partition(
        "player_prior",
        season,
        uri,
        row_count,
        parent.PLAYER_PRIOR_PHYSICAL_SCHEMA,
    )


def _prior_row(
    *,
    season: int = 2023,
    canonical_player_id: str = CANONICAL_UUID,
    lahman_player_id: str = "troutmi01",
) -> dict[str, Any]:
    return {
        "event_family": "player_prior",
        "canonical_game_id": None,
        "canonical_player_id": canonical_player_id,
        "player_id": None,
        "lahman_player_id": lahman_player_id,
        "identity_resolution_status": "EXACT",
        "season": season,
        "official_date": f"{season:04d}-12-31",
        "official_date_semantics": "ANNUAL_REGULAR_SEASON_PERIOD_END",
        "population": (
            "GEN2_PRIMARY_2023_2025"
            if season >= 2023
            else "GEN2_PRIOR_ONLY_2021_2022"
        ),
        "coverage_role": "PRIMARY" if season >= 2023 else "PRIOR_ONLY",
        "domain": "BATTING",
        "position": None,
        "eligible_from_season": season + 1,
        "same_season_predictor_eligible": False,
        "team_ids": ["LAA"],
        "league_ids": ["AL"],
        "metrics_json": '{"AB":1}',
        "source_key": parent._EXPECTED_LAHMAN_SOURCE_KEY,
        "source_releases": [parent._EXPECTED_LAHMAN_RELEASE],
        "source_logical_tables": ["Batting.csv"],
        "source_logical_uris": ["open-data/lahman/batting.blob"],
        "source_file_sha256": "a" * 64,
        "source_file_sha256s": ["a" * 64],
        "source_row_numbers": [42],
        "source_row_sha256s": ["b" * 64],
        "source_derivation_checksum": "c" * 64,
        "timing_classification": "RETROSPECTIVE_RECONSTRUCTION",
    }


def _plate_appearance_frame(
    season: int,
    batters: list[str],
    pitchers: list[str],
    automatic_runners: list[str | None],
) -> pl.DataFrame:
    normalized_automatic = [
        None if raw is None else f"{PREFIX}{raw}" for raw in automatic_runners
    ]
    return pl.DataFrame(
        {
            "season": pl.Series([season] * len(batters), dtype=pl.Int64),
            "identity_namespace": pl.Series(
                ["RETROSHEET"] * len(batters), dtype=pl.String
            ),
            "canonical_identity_resolved": pl.Series(
                [False] * len(batters), dtype=pl.Boolean
            ),
            "batter_id": pl.Series(
                [f"{PREFIX}{raw}" for raw in batters], dtype=pl.String
            ),
            "pitcher_id": pl.Series(
                [f"{PREFIX}{raw}" for raw in pitchers], dtype=pl.String
            ),
            "automatic_runner_player_id": pl.Series(
                normalized_automatic, dtype=pl.String
            ),
            "retrosheet_automatic_runner_id": pl.Series(
                automatic_runners, dtype=pl.String
            ),
        }
    )


def _anchored_manifest() -> SimpleNamespace:
    families = []
    for family, anchor in parent._FAMILY_ANCHORS.items():
        families.append(
            SimpleNamespace(
                family_id=family,
                schema_id=anchor.schema_id,
                file_count=anchor.file_count,
                row_count=anchor.row_count,
                byte_count=anchor.byte_count,
                date_range=SimpleNamespace(
                    minimum=anchor.minimum_date,
                    maximum=anchor.maximum_date,
                ),
                source_key=anchor.source_key,
                source_version_id=anchor.source_version_id,
            )
        )
    return SimpleNamespace(
        dataset_id=parent.EXPECTED_PARENT_DATASET_ID,
        manifest_id=parent.EXPECTED_PARENT_MANIFEST_ID,
        manifest_sha256=parent.EXPECTED_PARENT_MANIFEST_SHA256,
        dataset_version_id=parent.EXPECTED_PARENT_DATASET_VERSION_ID,
        package_id=parent.EXPECTED_PARENT_PACKAGE_ID,
        target_definitions=(
            SimpleNamespace(
                target_id="plate_appearance_outcome",
                ordered_classes=tuple(parent.EXPECTED_PA_CLASS_ORDER),
            ),
        ),
        data_families=tuple(families),
    )


def _patch_small_scan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    duplicate_prior: bool = False,
) -> None:
    pa_2022 = _plate_appearance_frame(
        2022,
        [RAW_ALPHA, RAW_GAMMA],
        [RAW_BETA, RAW_BETA],
        [None, RAW_ALPHA],
    )
    pa_2023 = _plate_appearance_frame(
        2023,
        [RAW_ALPHA],
        [RAW_DELTA],
        [None],
    )
    prior_rows = [_prior_row()]
    if duplicate_prior:
        prior_rows.append(_prior_row())
    frames = {
        "pa-2022.parquet": pa_2022,
        "pa-2023.parquet": pa_2023,
        "prior-2023.parquet": pl.DataFrame(prior_rows),
    }
    partitions = (
        _plate_appearance_partition(2022, "pa-2022.parquet", 2),
        _plate_appearance_partition(2023, "pa-2023.parquet", 1),
        _prior_partition(2023, "prior-2023.parquet", len(prior_rows)),
    )
    pa_anchor = parent._FAMILY_ANCHORS["plate_appearance"]
    prior_anchor = parent._FAMILY_ANCHORS["player_prior"]
    small_anchors = MappingProxyType(
        {
            "plate_appearance": parent._FamilyAnchor(
                pa_anchor.schema_id,
                2,
                3,
                1,
                "2022-04-01",
                "2023-04-01",
                pa_anchor.source_key,
                pa_anchor.source_version_id,
            ),
            "player_prior": parent._FamilyAnchor(
                prior_anchor.schema_id,
                1,
                len(prior_rows),
                1,
                "2023-12-31",
                "2023-12-31",
                prior_anchor.source_key,
                prior_anchor.source_version_id,
            ),
        }
    )
    manifest = SimpleNamespace(
        partitions=partitions,
        identity_coverage=SimpleNamespace(
            all_2021_2025=SimpleNamespace(reference_count=999, unique_players=99),
            primary_2023_2025=SimpleNamespace(reference_count=888, unique_players=88),
        ),
        manifest_id=parent.EXPECTED_PARENT_MANIFEST_ID,
        manifest_sha256=parent.EXPECTED_PARENT_MANIFEST_SHA256,
        dataset_version_id=parent.EXPECTED_PARENT_DATASET_VERSION_ID,
        package_id=parent.EXPECTED_PARENT_PACKAGE_ID,
    )

    monkeypatch.setattr(parent, "_FAMILY_ANCHORS", small_anchors)
    monkeypatch.setattr(
        parent,
        "validate_sports_dataset_exchange",
        lambda *_args, **_kwargs: SimpleNamespace(package_root=tmp_path),
    )
    monkeypatch.setattr(parent, "_load_and_anchor_manifest", lambda _root: manifest)
    monkeypatch.setattr(parent, "_validate_parent_anchors", lambda *_args: None)
    monkeypatch.setattr(
        parent,
        "partition_authority_sha256",
        lambda _partitions: parent.EXPECTED_PARENT_PARTITION_AUTHORITY_SHA256,
    )
    monkeypatch.setattr(
        parent,
        "resolve_under",
        lambda _root, uri, **_kwargs: Path(uri),
    )

    def read_parquet(path: Path, *, columns: list[str] | None = None, **_kwargs: Any):
        frame = frames[str(path)]
        return frame.select(columns) if columns is not None else frame

    monkeypatch.setattr(parent.pl, "read_parquet", read_parquet)


def test_identity_field_specs_are_exact_and_nonduplicating() -> None:
    minimum = tuple(
        (spec.family, spec.field, spec.raw_mirror_field, spec.kind, spec.required)
        for spec in parent.MINIMUM_JOIN_FIELD_SPECS
    )
    assert minimum == (
        ("pitch_sequence", "batter_id", None, "SCALAR", True),
        ("pitch_sequence", "pitcher_id", None, "SCALAR", True),
        (
            "pitch_sequence",
            "automatic_runner_player_id",
            "retrosheet_automatic_runner_id",
            "SCALAR",
            False,
        ),
        ("plate_appearance", "batter_id", None, "SCALAR", True),
        ("plate_appearance", "pitcher_id", None, "SCALAR", True),
        (
            "plate_appearance",
            "automatic_runner_player_id",
            "retrosheet_automatic_runner_id",
            "SCALAR",
            False,
        ),
        ("play_transition", "batter_id", None, "SCALAR", True),
        ("play_transition", "pitcher_id", None, "SCALAR", True),
        (
            "play_transition",
            "automatic_runner_player_id",
            "retrosheet_automatic_runner_id",
            "SCALAR",
            False,
        ),
        (
            "play_transition",
            "lineup_player_ids",
            "retrosheet_lineup_slots",
            "LIST",
            True,
        ),
        ("play_transition", "runner_on_first_before", None, "SCALAR", False),
        ("play_transition", "runner_on_second_before", None, "SCALAR", False),
        ("play_transition", "runner_on_third_before", None, "SCALAR", False),
        ("play_transition", "runner_on_first_after", None, "SCALAR", False),
        ("play_transition", "runner_on_second_after", None, "SCALAR", False),
        ("play_transition", "runner_on_third_after", None, "SCALAR", False),
        (
            "player_game_batting",
            "player_id",
            "retrosheet_player_id",
            "SCALAR",
            True,
        ),
        (
            "player_game_fielding",
            "player_id",
            "retrosheet_player_id",
            "SCALAR",
            True,
        ),
        (
            "player_game_pitching",
            "player_id",
            "retrosheet_player_id",
            "SCALAR",
            True,
        ),
        ("starter_usage", "player_id", "retrosheet_player_id", "SCALAR", True),
        ("bullpen_usage", "player_id", "retrosheet_player_id", "SCALAR", True),
    )
    governed = {
        (spec.family, spec.field) for spec in parent.GOVERNED_REFERENCE_FIELD_SPECS
    }
    assert len(minimum) == len({(family, field) for family, field, *_rest in minimum})
    assert governed == {
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
    assert len(parent.PLAYER_PRIOR_PHYSICAL_SCHEMA) == 28


def test_scalar_ids_require_explicit_prefix_and_exact_raw_mirror() -> None:
    mirrored_spec = parent._field_spec("player_game_batting", "player_id")
    frame = pl.DataFrame(
        {
            "player_id": [f"{PREFIX}{RAW_ALPHA}", f"{PREFIX}{RAW_ALPHA}"],
            "retrosheet_player_id": [RAW_ALPHA, RAW_ALPHA],
        }
    )
    assert parent._raw_id_counts(frame, mirrored_spec, "fixture") == Counter(
        {RAW_ALPHA: 2}
    )

    unprefixed = frame.with_columns(pl.lit(RAW_ALPHA).alias("player_id"))
    with pytest.raises(parent.ParentIdentityScanError, match="explicitly Retrosheet"):
        parent._raw_id_counts(unprefixed, mirrored_spec, "fixture")

    mismatched = frame.with_columns(pl.lit(RAW_BETA).alias("retrosheet_player_id"))
    with pytest.raises(parent.ParentIdentityScanError, match="differs from raw mirror"):
        parent._raw_id_counts(mismatched, mirrored_spec, "fixture")

    malformed = frame.with_columns(pl.lit("not-an-id").alias("retrosheet_player_id"))
    with pytest.raises(parent.ParentIdentityScanError, match="mirror is malformed"):
        parent._raw_id_counts(malformed, mirrored_spec, "fixture")


def test_scalar_null_policy_distinguishes_required_and_optional() -> None:
    required = parent._field_spec("plate_appearance", "batter_id")
    required_null = pl.DataFrame(
        {"batter_id": pl.Series([None], dtype=pl.String)}
    )
    with pytest.raises(parent.ParentIdentityScanError, match="Required identity field"):
        parent._raw_id_counts(required_null, required, "fixture")

    optional = parent._field_spec(
        "plate_appearance", "automatic_runner_player_id"
    )
    optional_null = pl.DataFrame(
        {
            "automatic_runner_player_id": pl.Series([None], dtype=pl.Null),
            "retrosheet_automatic_runner_id": pl.Series([None], dtype=pl.Null),
        }
    )
    assert parent._raw_id_counts(optional_null, optional, "fixture") == Counter()

    mixed = pl.DataFrame(
        {
            "automatic_runner_player_id": pl.Series(
                [None, f"{PREFIX}{RAW_ALPHA}"], dtype=pl.String
            ),
            "retrosheet_automatic_runner_id": pl.Series(
                [None, RAW_ALPHA], dtype=pl.String
            ),
        }
    )
    assert parent._raw_id_counts(mixed, optional, "fixture") == Counter(
        {RAW_ALPHA: 1}
    )


def test_list_ids_enforce_prefix_mirror_length_and_required_rows() -> None:
    spec = parent._field_spec("play_transition", "lineup_player_ids")
    raw = [RAW_ALPHA, RAW_BETA, None, None, None, None, None, None, None]
    normalized = [None if value is None else f"{PREFIX}{value}" for value in raw]
    frame = pl.DataFrame(
        {
            "lineup_player_ids": pl.Series(
                "lineup_player_ids", [normalized], dtype=pl.List(pl.String)
            ),
            "retrosheet_lineup_slots": pl.Series(
                "retrosheet_lineup_slots", [raw], dtype=pl.List(pl.String)
            ),
        }
    )
    assert parent._raw_id_counts(frame, spec, "fixture") == Counter(
        {RAW_ALPHA: 1, RAW_BETA: 1}
    )

    short = frame.with_columns(
        pl.Series(
            "lineup_player_ids",
            [[f"{PREFIX}{RAW_ALPHA}"]],
            dtype=pl.List(pl.String),
        ),
        pl.Series(
            "retrosheet_lineup_slots", [[RAW_ALPHA]], dtype=pl.List(pl.String)
        ),
    )
    with pytest.raises(parent.ParentIdentityScanError, match="list length changed"):
        parent._raw_id_counts(short, spec, "fixture")

    null_list = pl.DataFrame(
        {
            "lineup_player_ids": pl.Series(
                "lineup_player_ids", [None], dtype=pl.List(pl.String)
            ),
            "retrosheet_lineup_slots": pl.Series(
                "retrosheet_lineup_slots", [None], dtype=pl.List(pl.String)
            ),
        }
    )
    with pytest.raises(parent.ParentIdentityScanError, match="list has nulls"):
        parent._raw_id_counts(null_list, spec, "fixture")


def test_coverage_uses_only_active_seasons_and_primary_split() -> None:
    coverage = parent._coverage(
        {2020: 100, 2021: 2, 2023: 3, 2025: 4, 2026: 100},
        {
            2020: {"outside-before"},
            2021: {"a", "b"},
            2023: {"b", "c"},
            2025: {"c"},
            2026: {"outside-after"},
        },
        expected_references=9,
        expected_unique=3,
        expected_primary_references=7,
        expected_primary_unique=2,
    )
    assert coverage.reference_count == 9
    assert coverage.unique_player_count == 3
    assert coverage.primary_reference_count == 7
    assert coverage.primary_unique_player_count == 2
    assert coverage.counts_reconcile is True


def test_player_prior_preserves_exact_join_and_y_minus_one_lineage() -> None:
    partition = _prior_partition(2023, "prior.parquet", 1)
    parent._validate_player_prior_partition(partition)
    evidence = parent._player_prior_evidence(_prior_row(), partition)
    assert evidence.canonical_player_id == CANONICAL_UUID
    assert evidence.lahman_player_id == "troutmi01"
    assert evidence.identity_resolution_status == "EXACT"
    assert evidence.season == 2023
    assert evidence.eligible_from_season == 2024
    assert evidence.same_season_predictor_eligible is False
    assert evidence.domain == "BATTING"
    assert evidence.source_version_id == parent._EXPECTED_LAHMAN_SOURCE_VERSION
    assert evidence.source_partition_uri == "prior.parquet"
    assert evidence.source_partition_sha256 == "f" * 64
    assert evidence.source_logical_tables == ("Batting.csv",)
    assert evidence.source_row_numbers == (42,)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("canonical_player_id", "not-a-uuid", "canonical UUID is malformed"),
        ("lahman_player_id", "BAD!", "Lahman ID is malformed"),
        ("eligible_from_season", 2023, "Y-1 eligibility changed"),
        ("identity_resolution_status", "AMBIGUOUS", "non-exact identity"),
        ("source_key", "OTHER", "source key changed"),
    ),
)
def test_player_prior_rejects_malformed_or_unsafe_values(
    field: str, value: object, message: str
) -> None:
    row = _prior_row()
    row[field] = value
    partition = _prior_partition(2023, "prior.parquet", 1)
    with pytest.raises(parent.ParentIdentityScanError, match=message):
        parent._player_prior_evidence(row, partition)


def test_wrong_parent_identity_class_order_and_family_anchor_are_rejected() -> None:
    manifest = _anchored_manifest()
    parent._validate_parent_anchors(
        manifest, parent.EXPECTED_PARENT_PARTITION_AUTHORITY_SHA256
    )

    wrong_identity = _anchored_manifest()
    wrong_identity.manifest_id = "sports-dataset-manifest:sha256:" + "0" * 64
    with pytest.raises(parent.ParentIdentityScanError, match="identity differs"):
        parent._validate_parent_anchors(
            wrong_identity, parent.EXPECTED_PARENT_PARTITION_AUTHORITY_SHA256
        )

    wrong_order = _anchored_manifest()
    wrong_order.target_definitions[0].ordered_classes = tuple(
        reversed(parent.EXPECTED_PA_CLASS_ORDER)
    )
    with pytest.raises(parent.ParentIdentityScanError, match="class order changed"):
        parent._validate_parent_anchors(
            wrong_order, parent.EXPECTED_PARENT_PARTITION_AUTHORITY_SHA256
        )

    wrong_family = _anchored_manifest()
    wrong_family.data_families[0].schema_id = "changed"
    with pytest.raises(parent.ParentIdentityScanError, match="family anchor changed"):
        parent._validate_parent_anchors(
            wrong_family, parent.EXPECTED_PARENT_PARTITION_AUTHORITY_SHA256
        )


def test_wrong_identity_schema_and_source_version_are_rejected() -> None:
    partition = _plate_appearance_partition(2023, "pa.parquet", 1)
    specs = tuple(
        spec
        for spec in parent.MINIMUM_JOIN_FIELD_SPECS
        if spec.family == "plate_appearance"
    )
    parent._validate_partition_identity_fields(partition, specs)

    partition.source_version_id = "wrong"
    with pytest.raises(parent.ParentIdentityScanError, match="lineage changed"):
        parent._validate_partition_identity_fields(partition, specs)

    wrong_dtype = _plate_appearance_partition(2023, "pa.parquet", 1)
    next(field for field in wrong_dtype.physical_schema if field.name == "batter_id").dtype = (
        "Int64"
    )
    with pytest.raises(parent.ParentIdentityScanError, match="has dtype"):
        parent._validate_partition_identity_fields(wrong_dtype, specs)


def test_wrong_player_prior_schema_or_source_version_is_rejected() -> None:
    partition = _prior_partition(2023, "prior.parquet", 1)
    partition.source_version_id = "wrong"
    with pytest.raises(parent.ParentIdentityScanError, match="lineage changed"):
        parent._validate_player_prior_partition(partition)

    wrong_schema = _prior_partition(2023, "prior.parquet", 1)
    wrong_schema.physical_schema = wrong_schema.physical_schema[:-1]
    with pytest.raises(parent.ParentIdentityScanError, match="physical field contract"):
        parent._validate_player_prior_partition(wrong_schema)


def test_small_package_scan_reports_progress_primary_ids_and_nonreconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_root = Path("fixture-parent")
    _patch_small_scan(monkeypatch, fixture_root)
    progress: list[parent.ParentScanProgress] = []
    evidence = parent.scan_parent_identity(
        fixture_root,
        heartbeat_seconds=1,
        progress_callback=progress.append,
    )
    assert evidence.all_retrosheet_player_ids == tuple(
        sorted({RAW_ALPHA, RAW_BETA, RAW_GAMMA, RAW_DELTA})
    )
    assert evidence.primary_retrosheet_player_ids == tuple(
        sorted({RAW_ALPHA, RAW_DELTA})
    )
    assert evidence.minimum_join_coverage.reference_count == 7
    assert evidence.minimum_join_coverage.primary_reference_count == 2
    assert evidence.minimum_join_coverage.counts_reconcile is False
    assert evidence.governed_reference_coverage.reference_count == 0
    assert evidence.governed_counts_reconcile is False
    assert len(evidence.player_priors) == 1
    assert progress[0].stage == "VALIDATING_PARENT"
    assert progress[-1].stage == "PARENT_IDENTITY_SCANNED"


def test_duplicate_player_prior_semantic_row_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_root = Path("fixture-parent")
    _patch_small_scan(monkeypatch, fixture_root, duplicate_prior=True)
    with pytest.raises(parent.ParentIdentityScanError, match="semantic row is duplicated"):
        parent.scan_parent_identity(fixture_root)


def test_deadline_fails_closed_with_typed_progress() -> None:
    ticks = iter((0.0, 61.0, 61.0))
    with pytest.raises(parent.ParentIdentityScanDeadline) as raised:
        parent.scan_parent_identity(
            Path("fixture-parent"),
            heartbeat_seconds=1,
            max_runtime_minutes=1,
            clock=lambda: next(ticks),
        )
    assert raised.value.progress["stage"] == "VALIDATING_PARENT"
    assert raised.value.progress["files_checked"] == 0


@pytest.mark.parametrize("interval_name", ["max_runtime_minutes", "heartbeat_seconds"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_scan_intervals_are_rejected_before_parent_reads(
    monkeypatch: pytest.MonkeyPatch,
    interval_name: str,
    value: float,
) -> None:
    def forbid_parent_read(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("parent validation must not run")

    monkeypatch.setattr(
        parent, "validate_sports_dataset_exchange", forbid_parent_read
    )
    arguments: dict[str, Any] = {
        "heartbeat_seconds": 1,
        "max_runtime_minutes": 1,
    }
    arguments[interval_name] = value
    with pytest.raises(ValueError):
        parent.scan_parent_identity(Path("fixture-parent"), **arguments)


def test_physical_governed_inventory_explicitly_does_not_reconcile_frozen_claim() -> None:
    physical_by_season = {
        2021: 2_495_479,
        2022: 2_456_056,
        2023: 2_500_006,
        2024: 2_476_231,
        2025: 2_483_906,
    }
    all_ids = {f"player-{index}" for index in range(2_692)}
    primary_ids = {f"player-{index}" for index in range(2_070)}
    coverage = parent._coverage(
        physical_by_season,
        {
            2021: all_ids,
            2022: all_ids,
            2023: primary_ids,
            2024: primary_ids,
            2025: primary_ids,
        },
        expected_references=21_356_559,
        expected_unique=2_693,
        expected_primary_references=12_838_083,
        expected_primary_unique=2_071,
    )
    assert coverage.reference_count == 12_411_678
    assert coverage.unique_player_count == 2_692
    assert coverage.primary_reference_count == 7_460_143
    assert coverage.primary_unique_player_count == 2_070
    assert coverage.expected_reference_count == 21_356_559
    assert coverage.expected_unique_player_count == 2_693
    assert coverage.counts_reconcile is False

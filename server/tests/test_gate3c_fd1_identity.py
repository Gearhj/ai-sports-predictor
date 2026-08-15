from __future__ import annotations

import csv
from hashlib import sha256
import io
import json
from pathlib import Path

import polars as pl
import pytest

from app.gen2.fd1_identity import (
    CHADWICK_SOURCE_KEY,
    EXPECTED_MISSING_LAHMAN_TABLES,
    GEN2_PRIMARY_POPULATION,
    GEN2_PRIOR_ONLY_POPULATION,
    LAHMAN_SOURCE_KEY,
    RETROSHEET_AUXILIARY_SOURCE_KEY,
    FD1IdentityImportError,
    ImportLimits,
    PLAYER_PRIOR_EVENT_FAMILY,
    PLAYER_PRIOR_PARTITION_MONTH,
    PriorCoverageRole,
    ResolutionStatus,
    SourceFileSpec,
    build_organization_crosswalk,
    build_person_crosswalk,
    build_player_priors,
    build_retrosheet_organization_crosswalk,
    describe_lahman_release_limitations,
    iter_annual_player_prior_batches,
    iter_all_registry_person_identity_mapping_batches,
    iter_all_registry_person_identity_mapping_specs,
    iter_monthly_player_prior_partition_batches,
    iter_player_prior_family_payloads,
    iter_registry_identity_mapping_batches,
    iter_registry_identity_mapping_specs,
    iter_registry_organization_identity_mapping_batches,
    iter_registry_organization_identity_mapping_specs,
    iter_registry_retrosheet_organization_identity_mapping_specs,
)
from app.gen2.open_data_registry import OpenDataIdentityMappingSpec


UUID_1 = "11111111-1111-4111-8111-111111111111"
UUID_2 = "22222222-2222-4222-8222-222222222222"
UUID_3 = "33333333-3333-4333-8333-333333333333"
UUID_4 = "44444444-4444-4444-8444-444444444444"


def _csv_spec(
    tmp_path: Path,
    *,
    disk_name: str,
    logical_table: str,
    source_key: str,
    header: list[str] | tuple[str, ...],
    rows: list[list[object]],
) -> SourceFileSpec:
    path = tmp_path / disk_name
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\r\n")
        writer.writerow(header)
        writer.writerows(rows)
    payload = path.read_bytes()
    return SourceFileSpec(
        path=path,
        source_key=source_key,
        source_release="fixture-release",
        logical_table=logical_table,
        expected_sha256=sha256(payload).hexdigest(),
        expected_content_length=len(payload),
    )


def _person_sources(tmp_path: Path) -> tuple[tuple[SourceFileSpec, ...], SourceFileSpec]:
    chadwick_header = [
        "key_person",
        "key_uuid",
        "key_mlbam",
        "key_retro",
        "key_bbref",
        "name_first",
        "name_last",
    ]
    chadwick = _csv_spec(
        tmp_path,
        disk_name="people-0.fixture",
        logical_table="people-0.csv",
        source_key=CHADWICK_SOURCE_KEY,
        header=chadwick_header,
        rows=[
            ["person1", UUID_1, "1001", "retro1", "bbref1", "Same", "Name"],
            ["person2", UUID_2, "1002", "retro2", "bbref2", "Second", "Player"],
            ["person3", UUID_3, "1003", "retro3", "bbref3", "Third", "Player"],
            ["person4", UUID_4, "1004", "retro4", "bbref4", "Fourth", "Player"],
        ],
    )
    lahman = _csv_spec(
        tmp_path,
        disk_name="People.fixture",
        logical_table="People.csv",
        source_key=LAHMAN_SOURCE_KEY,
        header=["playerID", "bbrefID", "retroID", "nameFirst", "nameLast"],
        rows=[
            ["lahman1", "bbref1", "retro1", "Same", "Name"],
            # A matching name is deliberately not identity evidence.
            ["lahman2", "not-in-chadwick", "also-missing", "Same", "Name"],
            # Two exact namespaces point at different Chadwick UUIDs.
            ["lahman3", "bbref2", "retro3", "Conflict", "Player"],
            # One candidate is found, but its published Retrosheet ID disagrees.
            ["lahman4", "bbref4", "different-retro", "Fourth", "Player"],
        ],
    )
    return (chadwick,), lahman


def _logical_uri_map(*source_files) -> dict[str, str]:
    return {
        item.content_sha256: (
            f"open-data/{item.source_key.lower()}/{item.logical_table.lower()}/"
            f"{item.content_sha256}.csv"
        )
        for item in source_files
    }


def test_person_crosswalk_uses_only_exact_identifiers_and_keeps_conflicts(
    tmp_path: Path,
) -> None:
    chadwick, lahman = _person_sources(tmp_path)
    report = build_person_crosswalk(chadwick, lahman)

    assert report.chadwick_source_rows == 4
    assert report.chadwick_unique_uuids == 4
    assert report.lahman_source_rows == 4
    assert (report.exact_count, report.unresolved_count, report.conflict_count) == (
        1,
        1,
        2,
    )
    assert report.name_based_match_count == 0

    by_id = {link.lahman_player_id: link for link in report.links}
    exact = by_id["lahman1"]
    assert exact.resolution_status is ResolutionStatus.EXACT
    assert exact.confidence == "EXACT_MULTI_NAMESPACE"
    assert exact.canonical_chadwick_uuid == UUID_1
    assert exact.chadwick_person_key == "person1"
    assert exact.mlbam_id == "1001"
    assert exact.retrosheet_id == "retro1"
    assert exact.evidence_namespaces == ("BASEBALL_REFERENCE", "RETROSHEET")
    assert len(exact.source_rows) == 2
    assert {row.logical_table for row in exact.source_rows} == {
        "People.csv",
        "people-0.csv",
    }
    assert all(len(row.file_sha256) == 64 for row in exact.source_rows)
    assert all(len(row.row_sha256) == 64 for row in exact.source_rows)
    raw_lahman_row = lahman.path.read_bytes().splitlines(keepends=True)[1]
    lahman_evidence = next(
        row for row in exact.source_rows if row.logical_table == "People.csv"
    )
    assert lahman_evidence.row_sha256 == sha256(raw_lahman_row).hexdigest()

    unresolved = by_id["lahman2"]
    assert unresolved.resolution_status is ResolutionStatus.UNRESOLVED
    assert unresolved.confidence == "UNRESOLVED"
    assert unresolved.canonical_chadwick_uuid is None
    assert unresolved.candidate_chadwick_uuids == ()
    assert len(unresolved.source_rows) == 1

    divergent = by_id["lahman3"]
    assert divergent.resolution_status is ResolutionStatus.CONFLICT
    assert divergent.confidence == "CONFLICT"
    assert divergent.canonical_chadwick_uuid is None
    assert divergent.candidate_chadwick_uuids == (UUID_2, UUID_3)
    assert len(divergent.source_rows) == 3

    mismatch = by_id["lahman4"]
    assert mismatch.resolution_status is ResolutionStatus.CONFLICT
    assert mismatch.canonical_chadwick_uuid is None
    assert mismatch.mismatch_namespaces == ("RETROSHEET",)
    assert report.exact_uuid_by_lahman_id() == {"lahman1": UUID_1}
    assert report.lahman_to_chadwick_match_rate == pytest.approx(0.25)
    assert report.retrosheet_identifier_count == 4
    assert report.retrosheet_to_chadwick_count == 1
    assert report.retrosheet_to_mlbam_count == 1


def test_source_checksums_and_bounds_fail_closed(tmp_path: Path) -> None:
    chadwick, lahman = _person_sources(tmp_path)
    bad = SourceFileSpec(
        path=lahman.path,
        source_key=lahman.source_key,
        source_release=lahman.source_release,
        logical_table=lahman.logical_table,
        expected_sha256="0" * 64,
        expected_content_length=lahman.expected_content_length,
    )
    with pytest.raises(FD1IdentityImportError, match="SHA-256 mismatch"):
        build_person_crosswalk(chadwick, bad)

    with pytest.raises(FD1IdentityImportError, match="exceeds max_rows_per_file"):
        build_person_crosswalk(
            chadwick,
            lahman,
            limits=ImportLimits(max_rows_per_file=2),
        )
    with pytest.raises(FD1IdentityImportError, match="exceeds max_file_bytes"):
        build_person_crosswalk(
            chadwick,
            lahman,
            limits=ImportLimits(max_file_bytes=32),
        )


def test_registry_mapping_adapters_are_bounded_deterministic_and_row_bound(
    tmp_path: Path,
) -> None:
    chadwick, lahman = _person_sources(tmp_path)
    report = build_person_crosswalk(chadwick, lahman)
    uri_map = _logical_uri_map(*report.source_files)

    specs = list(
        iter_registry_identity_mapping_specs(
            report,
            evidence_logical_uri_by_file_checksum=uri_map,
        )
    )
    assert len(specs) == report.lahman_people == 4
    assert all(isinstance(spec, OpenDataIdentityMappingSpec) for spec in specs)
    by_id = {spec.external_id: spec for spec in specs}
    assert by_id["lahman1"].confidence == "EXACT"
    assert by_id["lahman1"].canonical_id == UUID_1
    assert by_id["lahman2"].confidence == "UNRESOLVED"
    assert by_id["lahman2"].canonical_id is None
    assert by_id["lahman3"].confidence == "CONFLICT"
    assert by_id["lahman3"].canonical_id is None
    exact_link = next(link for link in report.links if link.lahman_player_id == "lahman1")
    exact_row = next(
        row for row in exact_link.source_rows if row.logical_table == "People.csv"
    )
    assert by_id["lahman1"].source_row_number == exact_row.row_number
    assert by_id["lahman1"].source_row_checksum == exact_row.row_sha256
    assert by_id["lahman1"].evidence_logical_uri == uri_map[exact_row.file_sha256]

    batches = list(
        iter_registry_identity_mapping_batches(
            report,
            evidence_logical_uri_by_file_checksum=uri_map,
            batch_size=2,
        )
    )
    repeated = list(
        iter_registry_identity_mapping_batches(
            report,
            evidence_logical_uri_by_file_checksum=uri_map,
            batch_size=2,
        )
    )
    assert [len(batch.specs) for batch in batches] == [2, 2]
    assert [batch.batch_number for batch in batches] == [1, 2]
    assert [batch.batch_checksum for batch in batches] == [
        batch.batch_checksum for batch in repeated
    ]
    assert all(len(batch.batch_checksum) == 64 for batch in batches)
    first_batch_payload = [
        {
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
        for spec in batches[0].specs
    ]
    expected_identity_batch_checksum = sha256(
        json.dumps(
            first_batch_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    assert batches[0].batch_checksum == expected_identity_batch_checksum

    with pytest.raises(FD1IdentityImportError, match="No logical URI supplied"):
        list(
            iter_registry_identity_mapping_specs(
                report,
                evidence_logical_uri_by_file_checksum={},
            )
        )

    # A single exact namespace is HIGH rather than multi-source EXACT.
    single_chadwick = _csv_spec(
        tmp_path,
        disk_name="people-single.fixture",
        logical_table="people-f.csv",
        source_key=CHADWICK_SOURCE_KEY,
        header=["key_person", "key_uuid", "key_mlbam", "key_retro", "key_bbref"],
        rows=[["single", UUID_1, "1", "", "single-bbref"]],
    )
    single_lahman = _csv_spec(
        tmp_path,
        disk_name="lahman-single.fixture",
        logical_table="People.csv",
        source_key=LAHMAN_SOURCE_KEY,
        header=["playerID", "bbrefID", "retroID"],
        rows=[["single01", "single-bbref", ""]],
    )
    single_report = build_person_crosswalk((single_chadwick,), single_lahman)
    single_map = _logical_uri_map(*single_report.source_files)
    single_spec = next(
        iter_registry_identity_mapping_specs(
            single_report,
            evidence_logical_uri_by_file_checksum=single_map,
        )
    )
    assert single_spec.confidence == "HIGH"
    assert single_spec.canonical_id == UUID_1


def test_all_person_namespaces_emit_exact_row_bound_registry_assertions(
    tmp_path: Path,
) -> None:
    chadwick, lahman = _person_sources(tmp_path)
    report = build_person_crosswalk(chadwick, lahman)
    uri_map = _logical_uri_map(*report.source_files)

    specs = list(
        iter_all_registry_person_identity_mapping_specs(
            report,
            evidence_logical_uri_by_file_checksum=uri_map,
        )
    )
    counts: dict[str, int] = {}
    for spec in specs:
        counts[spec.source_namespace] = counts.get(spec.source_namespace, 0) + 1
    assert counts == {
        "baseball_reference": 4,
        "chadwick_person": 4,
        "lahman": 4,
        "mlbam": 4,
        "retrosheet": 4,
    }
    retrosheet = next(
        spec
        for spec in specs
        if spec.source_namespace == "retrosheet" and spec.external_id == "retro1"
    )
    assert retrosheet.source_key == CHADWICK_SOURCE_KEY
    assert retrosheet.canonical_id == UUID_1
    assert retrosheet.confidence == "EXACT"
    assert retrosheet.evidence_logical_uri.startswith(
        f"open-data/{CHADWICK_SOURCE_KEY.lower()}/"
    )

    batches = list(
        iter_all_registry_person_identity_mapping_batches(
            report,
            evidence_logical_uri_by_file_checksum=uri_map,
            batch_size=7,
        )
    )
    repeated = list(
        iter_all_registry_person_identity_mapping_batches(
            report,
            evidence_logical_uri_by_file_checksum=uri_map,
            batch_size=7,
        )
    )
    assert [len(batch.specs) for batch in batches] == [7, 7, 6]
    assert [batch.batch_checksum for batch in batches] == [
        batch.batch_checksum for batch in repeated
    ]


def test_duplicate_external_identifier_claims_remain_unresolved(
    tmp_path: Path,
) -> None:
    chadwick = _csv_spec(
        tmp_path,
        disk_name="people-duplicates.fixture",
        logical_table="people-a.csv",
        source_key=CHADWICK_SOURCE_KEY,
        header=["key_person", "key_uuid", "key_mlbam", "key_retro", "key_bbref"],
        rows=[
            ["one", UUID_1, "1", "retro-one", "shared-bbref"],
            ["two", UUID_2, "2", "retro-two", "shared-bbref"],
        ],
    )
    lahman = _csv_spec(
        tmp_path,
        disk_name="lahman-People-duplicates.fixture",
        logical_table="People.csv",
        source_key=LAHMAN_SOURCE_KEY,
        header=["playerID", "bbrefID", "retroID"],
        rows=[["duplicate01", "shared-bbref", ""]],
    )

    report = build_person_crosswalk((chadwick,), lahman)
    assert report.duplicate_identifier_claims == 1
    assert report.conflict_count == 1
    assert report.links[0].canonical_chadwick_uuid is None
    assert report.links[0].candidate_chadwick_uuids == (UUID_1, UUID_2)

    uri_map = _logical_uri_map(*report.source_files)
    shared = next(
        spec
        for spec in iter_all_registry_person_identity_mapping_specs(
            report,
            evidence_logical_uri_by_file_checksum=uri_map,
        )
        if spec.source_namespace == "baseball_reference"
        and spec.external_id == "shared-bbref"
    )
    assertion = next(
        item
        for item in report.namespace_assertions
        if item.namespace == "BASEBALL_REFERENCE"
        and item.external_id == "shared-bbref"
    )
    assert shared.confidence == "CONFLICT"
    assert shared.canonical_id is None
    assert len(assertion.source_rows) == 2


def _metric_row(
    header: tuple[str, ...],
    *,
    player: str,
    year: int,
    stint: int = 1,
    team: str = "T1",
    league: str = "AL",
    position: str | None = None,
    value: int = 1,
    blanks: set[str] | None = None,
) -> list[object]:
    fixed: dict[str, object] = {
        "playerID": player,
        "yearID": year,
        "stint": stint,
        "teamID": team,
        "lgID": league,
    }
    if position is not None:
        fixed["POS"] = position
    missing = blanks or set()
    return [fixed.get(column, "" if column in missing else value) for column in header]


def test_lahman_priors_are_chronological_labeled_and_next_season_only(
    tmp_path: Path,
) -> None:
    chadwick, lahman_people = _person_sources(tmp_path)
    crosswalk = build_person_crosswalk(chadwick, lahman_people)

    batting_metrics = (
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
    )
    pitching_metrics = (
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
    )
    fielding_metrics = (
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
    )
    common = ("playerID", "yearID", "stint", "teamID", "lgID")
    batting_header = common + batting_metrics
    pitching_header = common + pitching_metrics
    fielding_header = common + ("POS",) + fielding_metrics

    batting = _csv_spec(
        tmp_path,
        disk_name="Batting.fixture",
        logical_table="Batting.csv",
        source_key=LAHMAN_SOURCE_KEY,
        header=batting_header,
        rows=[
            _metric_row(batting_header, player="lahman1", year=2020),
            _metric_row(
                batting_header,
                player="lahman1",
                year=2021,
                stint=1,
                value=1,
                blanks={"HBP"},
            ),
            _metric_row(
                batting_header,
                player="lahman1",
                year=2021,
                stint=2,
                team="T2",
                value=2,
            ),
            _metric_row(batting_header, player="lahman2", year=2023, value=4),
        ],
    )
    pitching = _csv_spec(
        tmp_path,
        disk_name="Pitching.fixture",
        logical_table="Pitching.csv",
        source_key=LAHMAN_SOURCE_KEY,
        header=pitching_header,
        rows=[
            _metric_row(pitching_header, player="lahman1", year=2022, value=3),
            _metric_row(pitching_header, player="lahman3", year=2025, value=5),
        ],
    )
    fielding = _csv_spec(
        tmp_path,
        disk_name="Fielding.fixture",
        logical_table="Fielding.csv",
        source_key=LAHMAN_SOURCE_KEY,
        header=fielding_header,
        rows=[
            _metric_row(
                fielding_header,
                player="lahman1",
                year=2024,
                position="P",
                value=6,
            ),
            _metric_row(
                fielding_header,
                player="lahman1",
                year=2024,
                stint=2,
                position="1B",
                value=7,
            ),
        ],
    )

    report = build_player_priors(
        (pitching, fielding, batting),
        crosswalk=crosswalk,
    )
    assert report.source_rows_scanned == 8
    assert report.selected_source_rows == 7
    assert report.prior_row_count == 6
    assert [row.season for row in report.rows] == [2021, 2022, 2023, 2024, 2024, 2025]
    assert [item.count for item in report.counts_by_season] == [1, 1, 1, 2, 1]
    assert sum(item.count for item in report.counts_by_domain) == 6

    first = report.rows[0]
    assert first.coverage_role is PriorCoverageRole.PRIOR_ONLY
    assert first.population_label == GEN2_PRIOR_ONLY_POPULATION
    assert first.eligible_from_season == 2022
    assert first.same_season_predictor_eligible is False
    assert first.canonical_chadwick_uuid == UUID_1
    assert first.team_ids == ("T1", "T2")
    assert dict(first.metrics)["G"] == 3
    # One missing source component remains missing rather than becoming zero.
    assert dict(first.metrics)["HBP"] is None
    assert len(first.source_rows) == 2

    primary = next(row for row in report.rows if row.season == 2023)
    assert primary.coverage_role is PriorCoverageRole.PRIMARY
    assert primary.population_label == GEN2_PRIMARY_POPULATION
    assert primary.identity_resolution_status is ResolutionStatus.UNRESOLVED
    conflict = next(row for row in report.rows if row.season == 2025)
    assert conflict.identity_resolution_status is ResolutionStatus.CONFLICT
    assert conflict.canonical_chadwick_uuid is None

    uri_map = _logical_uri_map(*report.source_files)
    payloads = list(
        iter_player_prior_family_payloads(
            report,
            evidence_logical_uri_by_file_checksum=uri_map,
        )
    )
    assert len(payloads) == report.prior_row_count == 6
    assert all(payload["event_family"] == PLAYER_PRIOR_EVENT_FAMILY for payload in payloads)
    assert all(payload["canonical_game_id"] is None for payload in payloads)
    assert [payload["season"] for payload in payloads] == [2021, 2022, 2023, 2024, 2024, 2025]
    assert payloads[0]["canonical_player_id"] == UUID_1
    assert payloads[0]["player_id"] is None
    unresolved_payload = next(
        payload for payload in payloads if payload["lahman_player_id"] == "lahman2"
    )
    assert unresolved_payload["canonical_player_id"] is None
    assert unresolved_payload["player_id"] == "lahman:lahman2"
    assert all(len(value) == 64 for value in payloads[0]["source_file_sha256s"])
    assert all(len(value) == 64 for value in payloads[0]["source_row_sha256s"])
    assert payloads[0]["source_derivation_checksum"] == first.derivation_checksum

    annual = list(
        iter_annual_player_prior_batches(
            report,
            evidence_logical_uri_by_file_checksum=uri_map,
        )
    )
    monthly = list(
        iter_monthly_player_prior_partition_batches(
            report,
            evidence_logical_uri_by_file_checksum=uri_map,
        )
    )
    assert [batch.season for batch in annual] == [2021, 2022, 2023, 2024, 2025]
    assert [batch.row_count for batch in annual] == [1, 1, 1, 2, 1]
    assert [batch.player_count for batch in annual] == [1, 1, 1, 1, 1]
    assert all(batch.month == PLAYER_PRIOR_PARTITION_MONTH for batch in annual)
    assert [batch.population for batch in annual[:2]] == [
        GEN2_PRIOR_ONLY_POPULATION,
        GEN2_PRIOR_ONLY_POPULATION,
    ]
    assert [batch.population for batch in annual[2:]] == [
        GEN2_PRIMARY_POPULATION,
        GEN2_PRIMARY_POPULATION,
        GEN2_PRIMARY_POPULATION,
    ]
    assert [batch.payload_checksum for batch in monthly] == [
        batch.payload_checksum for batch in annual
    ]

    first_batch = annual[0]
    expected_payload_checksum = sha256(
        json.dumps(
            [dict(item) for item in first_batch.rows],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    expected_contributor_checksum = sha256(
        json.dumps(
            [dict(item) for item in first_batch.raw_contributor_manifest],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    assert first_batch.payload_checksum == expected_payload_checksum
    assert first_batch.raw_contributor_checksum == expected_contributor_checksum
    write_kwargs = first_batch.parquet_write_kwargs()
    assert write_kwargs["event_family"] == PLAYER_PRIOR_EVENT_FAMILY
    assert write_kwargs["game_count"] == 0
    assert write_kwargs["plate_appearance_count"] == 0
    assert write_kwargs["player_count"] == 1
    assert write_kwargs["min_event_date"].isoformat() == "2021-12-31"
    frame = pl.DataFrame(list(first_batch.rows))
    assert frame.height == first_batch.row_count
    assert frame.get_column("canonical_game_id").null_count() == frame.height
    assert frame.get_column("season").unique().to_list() == [2021]
    parquet_buffer = io.BytesIO()
    frame.write_parquet(parquet_buffer, compression="zstd")
    assert len(parquet_buffer.getvalue()) > 0
    assert pl.read_parquet(io.BytesIO(parquet_buffer.getvalue())).height == frame.height

    split = list(
        iter_annual_player_prior_batches(
            report,
            evidence_logical_uri_by_file_checksum=uri_map,
            max_rows_per_batch=1,
        )
    )
    assert len(split) == 6
    assert [batch.batch_number for batch in split if batch.season == 2024] == [1, 2]


def test_team_franchise_and_park_links_use_official_keys_not_names(
    tmp_path: Path,
) -> None:
    franchises = _csv_spec(
        tmp_path,
        disk_name="Franchises.fixture",
        logical_table="TeamsFranchises.csv",
        source_key=LAHMAN_SOURCE_KEY,
        header=["franchID", "franchName", "active", "NAassoc"],
        rows=[["FR1", "Fixture Franchise", "Y", ""]],
    )
    parks = _csv_spec(
        tmp_path,
        disk_name="Parks.fixture",
        logical_table="Parks.csv",
        source_key=LAHMAN_SOURCE_KEY,
        header=["parkkey", "parkalias", "parkname", "city", "state", "country"],
        rows=[
            ["P1", "Old Park; Test Field", "Official Park", "City", "ST", "US"],
            ["P2", "", "Name That Must Not Resolve PX", "City", "ST", "US"],
        ],
    )
    teams = _csv_spec(
        tmp_path,
        disk_name="Teams.fixture",
        logical_table="Teams.csv",
        source_key=LAHMAN_SOURCE_KEY,
        header=[
            "yearID",
            "teamID",
            "franchID",
            "name",
            "teamIDBR",
            "teamIDlahman45",
            "teamIDretro",
        ],
        rows=[
            [2023, "T1", "FR1", "Team One", "BR1", "L1", "RX"],
            [2023, "T2", "FR1", "Team Two", "BR2", "L2", "RX"],
            [2024, "T1", "FR1", "Team One", "BR1", "L1", "RX"],
        ],
    )
    home_games = _csv_spec(
        tmp_path,
        disk_name="HomeGames.fixture",
        logical_table="HomeGames.csv",
        source_key=LAHMAN_SOURCE_KEY,
        header=[
            "yearkey",
            "leaguekey",
            "teamkey",
            "parkkey",
            "spanfirst",
            "spanlast",
            "games",
            "openings",
            "attendance",
        ],
        rows=[
            [2023, "AL", "T1", "P1", "2023-04-01", "2023-09-30", 81, 80, 1000],
            [2024, "AL", "T1", "P1", "2024-04-01", "2024-09-30", 81, 81, 1200],
            # PX has no official park-key row.  A similar display name cannot resolve it.
            [2024, "AL", "T1", "PX", "2024-05-01", "2024-05-01", 1, 1, 10],
        ],
    )

    report = build_organization_crosswalk(
        teams=teams,
        franchises=franchises,
        parks=parks,
        home_games=home_games,
    )
    assert report.team_season_count == 3
    assert report.franchise_count == 1
    assert report.park_count == 2
    assert report.team_park_link_count == 3
    assert report.name_based_match_count == 0
    assert report.exact_team_park_link_count == 1
    assert report.unresolved_team_park_link_count == 1

    team_conflicts = [
        team
        for team in report.team_seasons
        if team.season == 2023
    ]
    assert all(team.resolution_status is ResolutionStatus.CONFLICT for team in team_conflicts)
    assert all(team.conflict_namespaces == ("RETROSHEET_TEAM",) for team in team_conflicts)

    links = {
        (link.season, link.lahman_park_id): link for link in report.team_park_links
    }
    exact = links[(2024, "P1")]
    assert exact.resolution_status is ResolutionStatus.EXACT
    assert exact.canonical_team_season_id == "LAHMAN_TEAM_SEASON:2024:T1"
    assert exact.canonical_park_id == "LAHMAN_PARK:P1"
    assert len(exact.source_rows[0].row_sha256) == 64
    unresolved = links[(2024, "PX")]
    assert unresolved.resolution_status is ResolutionStatus.UNRESOLVED
    assert unresolved.canonical_park_id is None
    assert links[(2023, "P1")].resolution_status is ResolutionStatus.CONFLICT

    retrosheet_teams = _csv_spec(
        tmp_path,
        disk_name="retrosheet-teams.fixture",
        logical_table="teams.csv",
        source_key=RETROSHEET_AUXILIARY_SOURCE_KEY,
        header=["TEAM", "LEAGUE", "CITY", "NICKNAME", "FIRST", "LAST"],
        rows=[
            ["RX", "AL", "City", "Team", 2024, 2025],
            ["RZ", "NL", "Elsewhere", "Other", 2020, 2025],
        ],
    )
    retrosheet_ballparks = _csv_spec(
        tmp_path,
        disk_name="retrosheet-ballparks.fixture",
        logical_table="ballparks.csv",
        source_key=RETROSHEET_AUXILIARY_SOURCE_KEY,
        header=["PARKID", "NAME", "AKA", "CITY", "STATE", "START", "END"],
        rows=[
            ["P1", "A deliberately different display name", "", "X", "ST", "", ""],
            ["PR", "Retrosheet only", "", "Y", "ST", "", ""],
        ],
    )
    retrosheet_report = build_retrosheet_organization_crosswalk(
        report,
        retrosheet_teams=retrosheet_teams,
        retrosheet_ballparks=retrosheet_ballparks,
    )
    assert (
        retrosheet_report.team_season_count,
        retrosheet_report.exact_team_season_count,
        retrosheet_report.unresolved_team_season_count,
        retrosheet_report.conflict_team_season_count,
    ) == (3, 1, 0, 2)
    assert (
        retrosheet_report.park_count,
        retrosheet_report.exact_park_count,
        retrosheet_report.unresolved_park_count,
        retrosheet_report.conflict_park_count,
    ) == (2, 1, 1, 0)
    assert retrosheet_report.unlinked_retrosheet_team_id_count == 1
    assert retrosheet_report.unlinked_lahman_park_count == 1
    assert retrosheet_report.name_based_match_count == 0

    uri_map = _logical_uri_map(*retrosheet_report.source_files)
    direct_specs = list(
        iter_registry_organization_identity_mapping_specs(
            report,
            evidence_logical_uri_by_file_checksum=uri_map,
        )
    )
    assert len(direct_specs) == 12
    assert {spec.entity_type for spec in direct_specs} == {
        "team",
        "franchise",
        "park",
    }
    team_2024 = next(
        spec
        for spec in direct_specs
        if spec.source_namespace == "lahman_team_season"
        and spec.external_id == "2024:T1"
    )
    assert team_2024.confidence == "EXACT"
    assert team_2024.valid_from.isoformat() == "2024-01-01"
    assert team_2024.valid_through.isoformat() == "2024-12-31"

    retrosheet_specs = list(
        iter_registry_retrosheet_organization_identity_mapping_specs(
            retrosheet_report,
            evidence_logical_uri_by_file_checksum=uri_map,
        )
    )
    by_external = {
        (spec.entity_type, spec.external_id): spec for spec in retrosheet_specs
    }
    assert by_external[("team", "2024:RX")].confidence == "EXACT"
    assert by_external[("team", "2023:RX")].confidence == "CONFLICT"
    assert by_external[("park", "P1")].confidence == "EXACT"
    assert by_external[("park", "PR")].confidence == "UNRESOLVED"
    assert by_external[("park", "PR")].canonical_id is None

    batches = list(
        iter_registry_organization_identity_mapping_batches(
            report,
            retrosheet_report=retrosheet_report,
            evidence_logical_uri_by_file_checksum=uri_map,
            batch_size=5,
        )
    )
    assert [len(batch.specs) for batch in batches] == [5, 5, 5, 2]


def test_missing_lahman_tables_are_explicit_zero_fabrication_limitations() -> None:
    published = (
        "Batting.csv",
        "Fielding.csv",
        "People.csv",
        "Pitching.csv",
        "Teams.csv",
    )
    report = describe_lahman_release_limitations(published)
    assert tuple(item.table_name for item in report.missing_expected_tables) == (
        EXPECTED_MISSING_LAHMAN_TABLES
    )
    assert all(
        item.status == "SOURCE_RELEASE_NOT_PUBLISHED"
        and item.substitution_permitted is False
        and item.fabricated_row_count == 0
        for item in report.missing_expected_tables
    )

    one_published = describe_lahman_release_limitations(
        (*published, "Appearances.csv")
    )
    assert tuple(item.table_name for item in one_published.missing_expected_tables) == (
        "AllstarFull.csv",
        "AwardsPlayers.csv",
    )

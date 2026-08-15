from __future__ import annotations

import csv
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path

import pytest

from app.gen2.fd1_identity import (
    CHADWICK_SOURCE_KEY,
    LAHMAN_SOURCE_KEY,
    GEN2_PRIMARY_POPULATION,
    ResolutionStatus,
    SourceFileSpec,
    build_person_crosswalk,
)
from app.gen2.fd1_identity_coverage import (
    FD1IdentityCoverageError,
    IdentityNamespace,
    IdentityRelation,
    build_active_identity_coverage,
)
from app.gen2.fd1_materializer import RETROSHEET_SOURCE_KEY
from app.gen2.retrosheet import RETROSHEET_CSV_HEADERS


UUID_1 = "11111111-1111-4111-8111-111111111111"
UUID_2 = "22222222-2222-4222-8222-222222222222"
UUID_3 = "33333333-3333-4333-8333-333333333333"
UUID_4 = "44444444-4444-4444-8444-444444444444"


def _spec(
    tmp_path: Path,
    *,
    disk_name: str,
    logical_table: str,
    source_key: str,
    header: tuple[str, ...],
    rows: list[list[object]],
) -> SourceFileSpec:
    path = tmp_path / disk_name
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\r\n")
        writer.writerow(header)
        writer.writerows(rows)
    payload = path.read_bytes()
    return SourceFileSpec(
        path=path,
        source_key=source_key,
        source_release="exact-fixture-release",
        logical_table=logical_table,
        expected_sha256=sha256(payload).hexdigest(),
        expected_content_length=len(payload),
    )


def _row(filename: str, **values: object) -> list[object]:
    return [values.get(column, "") for column in RETROSHEET_CSV_HEADERS[filename]]


def _crosswalk(tmp_path: Path):
    chadwick_header = (
        "key_person",
        "key_uuid",
        "key_mlbam",
        "key_retro",
        "key_bbref",
    )
    chadwick = _spec(
        tmp_path,
        disk_name="people-0.fixture",
        logical_table="people-0.csv",
        source_key=CHADWICK_SOURCE_KEY,
        header=chadwick_header,
        rows=[
            ["person1", UUID_1, "1001", "retro1", "bbref1"],
            ["person2", UUID_2, "", "retro2", "bbref2"],
            ["person3", UUID_3, "1003", "shared", "bbref3"],
            # The duplicate Retrosheet claim makes shared an explicit conflict.
            ["person4", UUID_4, "1004", "shared", "bbref4"],
        ],
    )
    lahman = _spec(
        tmp_path,
        disk_name="People.fixture",
        logical_table="People.csv",
        source_key=LAHMAN_SOURCE_KEY,
        header=("playerID", "bbrefID", "retroID"),
        rows=[
            ["lahman1", "bbref1", "retro1"],
            ["lahman2", "bbref2", "retro2"],
            # Exact namespaces point to multiple candidates: retain conflict.
            ["lahmanConflict", "bbref3", "shared"],
            # No Chadwick claim exists: retain unresolved.
            ["lahmanMissing", "", "missing"],
        ],
    )
    return build_person_crosswalk((chadwick,), lahman)


def _retrosheet_sources(tmp_path: Path) -> tuple[SourceFileSpec, ...]:
    rows = {
        "allplayers.csv": [
            _row("allplayers.csv", id="retro1", team="AAA", season=2021),
            _row("allplayers.csv", id="missing", team="AAA", season=2021),
            _row("allplayers.csv", id="shared", team="AAA", season=2023),
            _row("allplayers.csv", id="retro2", team="AAA", season=2023),
        ],
        "batting.csv": [
            _row(
                "batting.csv",
                gid="AAA202304010",
                id="retro1",
                gametype="regular",
                date="20230401",
            ),
            # A duplicate player reference increments references, not unique players.
            _row(
                "batting.csv",
                gid="AAA202304020",
                id="retro1",
                gametype="regular",
                date="20230402",
            ),
            # Non-regular rows are outside the readiness population.
            _row(
                "batting.csv",
                gid="AAA202310010",
                id="postonly",
                gametype="postseason",
                date="20231001",
            ),
        ],
        "pitching.csv": [
            _row(
                "pitching.csv",
                gid="AAA202304010",
                id="shared",
                gametype="regular",
                date="20230401",
            )
        ],
        "fielding.csv": [
            _row(
                "fielding.csv",
                gid="AAA202105010",
                id="missing",
                gametype="regular",
                date="20210501",
            ),
            _row(
                "fielding.csv",
                gid="AAA202304010",
                id="retro2",
                gametype="regular",
                date="20230401",
            ),
        ],
        "plays.csv": [
            _row(
                "plays.csv",
                gid="AAA202304010",
                batter="retro1",
                pitcher="shared",
                br1_pre="missing",
                pr1_pre="retro2",
                l1="retro1",
                f2="retro2",
                gametype="regular",
                date="20230401",
            )
        ],
    }
    return tuple(
        _spec(
            tmp_path,
            disk_name=f"retrosheet-{filename}",
            logical_table=filename,
            source_key=RETROSHEET_SOURCE_KEY,
            header=RETROSHEET_CSV_HEADERS[filename],
            rows=rows[filename],
        )
        for filename in (
            "allplayers.csv",
            "batting.csv",
            "pitching.csv",
            "fielding.csv",
            "plays.csv",
        )
    )


def test_active_identity_coverage_is_exact_reference_weighted_and_fail_closed(
    tmp_path: Path,
) -> None:
    report = build_active_identity_coverage(
        _crosswalk(tmp_path),
        _retrosheet_sources(tmp_path),
    )

    assert [row.rows_scanned for row in report.source_files] == [4, 3, 1, 2, 1]
    assert [row.active_references for row in report.source_files] == [4, 2, 1, 2, 6]
    assert len(report.derivation_checksum) == 64
    assert report.name_based_match_count == 0
    serialized = report.as_dict()
    assert json.loads(json.dumps(serialized))["exact_reference_rates"] == {
        "RETROSHEET_TO_CHADWICK": 0.6,
        "RETROSHEET_TO_MLBAM": 1 / 3,
        "LAHMAN_TO_CHADWICK": 0.6,
    }

    season_2021 = report.season_coverage[0]
    assert (season_2021.unique_retrosheet_players, season_2021.reference_count) == (2, 3)
    season_2023 = report.season_coverage[2]
    assert (season_2023.unique_retrosheet_players, season_2023.reference_count) == (4, 12)
    assert season_2023.namespace(IdentityNamespace.RETROSHEET).exact_reference_rate == 1.0

    retro_chadwick = season_2023.relation(
        IdentityRelation.RETROSHEET_TO_CHADWICK
    )
    assert (
        retro_chadwick.exact_unique_link_count,
        retro_chadwick.conflict_unique_link_count,
        retro_chadwick.unresolved_unique_link_count,
    ) == (2, 1, 1)
    assert (
        retro_chadwick.exact_reference_count,
        retro_chadwick.conflict_reference_count,
        retro_chadwick.unresolved_reference_count,
    ) == (8, 3, 1)

    mlbam = season_2023.relation(IdentityRelation.RETROSHEET_TO_MLBAM)
    assert (
        mlbam.exact_reference_count,
        mlbam.conflict_reference_count,
        mlbam.unresolved_reference_count,
    ) == (4, 3, 5)
    lahman = season_2023.relation(IdentityRelation.LAHMAN_TO_CHADWICK)
    assert (
        lahman.source_unique_id_count,
        lahman.exact_unique_link_count,
        lahman.conflict_unique_link_count,
        lahman.unresolved_unique_link_count,
    ) == (4, 2, 1, 1)
    assert (
        lahman.source_reference_count,
        lahman.exact_reference_count,
        lahman.conflict_reference_count,
        lahman.unresolved_reference_count,
    ) == (12, 8, 3, 1)

    primary = next(
        row
        for row in report.population_coverage
        if row.population == GEN2_PRIMARY_POPULATION
    )
    assert primary.reference_count == 12
    assert report.primary_unique_meets_threshold is False
    assert report.primary_reference_meets_threshold is False
    assert report.primary_meets_threshold is False

    issues = {row.retrosheet_id: row for row in report.affected_players}
    assert set(issues) == {"missing", "retro2", "shared"}
    assert issues["shared"].retrosheet_to_chadwick_status is ResolutionStatus.CONFLICT
    assert issues["missing"].retrosheet_to_chadwick_status is ResolutionStatus.UNRESOLVED
    assert issues["retro2"].mlbam_status is ResolutionStatus.UNRESOLVED
    missing_roles = {role.role: role for role in issues["missing"].roles}
    assert missing_roles["allplayers.csv.id"].affected_game_ids == ()
    assert missing_roles["fielding.csv.id"].affected_game_ids == ("AAA202105010",)
    assert missing_roles["plays.csv.br1_pre"].affected_game_ids == ("AAA202304010",)

    sources = _retrosheet_sources(tmp_path / "second")
    unpinned = replace(sources[0], expected_sha256=None)
    with pytest.raises(FD1IdentityCoverageError, match="requires expected SHA-256"):
        build_active_identity_coverage(
            _crosswalk(tmp_path / "third"),
            (unpinned, *sources[1:]),
        )

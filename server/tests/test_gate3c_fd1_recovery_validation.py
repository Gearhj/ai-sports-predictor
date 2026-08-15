from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import polars as pl
import pytest

from app.gen2.fd1_recovery_manifest import (
    FinalizerLineage,
    GenerationLineage,
    IdentitySpoolBinding,
    InterruptedExecutionContext,
    PhysicalSchemaField,
    RecoveryFileEntry,
    RecoveryManifest,
    SourceArchiveBinding,
    SourceReleaseBinding,
    TrustedDryRunLedger,
    parse_partition_uri,
)
from app.gen2.fd1_recovery_validation import (
    FD1WarehouseValidationError,
    REHEARSAL_VALIDATION_MODE,
    validate_recovery_warehouse,
)


RETRO_VERSION = "64db602a-090a-4e37-b8f2-2a48d36dd7f2"
LAHMAN_VERSION = "3af682f0-ecdb-47d8-8e14-660ec5f7455c"


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _common(row_checksum: str) -> dict[str, object]:
    return {
        "source_key": "RETROSHEET_CSV_DOWNLOADS",
        "source_mode": "RETROSHEET_OPEN_DATA",
        "source_version_id": RETRO_VERSION,
        "evidence_mode": "RETROSPECTIVE_RECONSTRUCTION",
        "population_category": "GEN2_PRIMARY_2023_2025",
        "strict_population": "GEN2_STRICT_DEPLOYABLE_RECONSTRUCTION",
        "game_id": "retrosheet:game:AAA202304010",
        "retrosheet_game_id": "AAA202304010",
        "game_date": "2023-04-01",
        "event_date": "2023-04-01",
        "season": 2023,
        "month": 4,
        "final_available_date": "2023-04-01",
        "historical_reconstruction": True,
        "available_only_after_game_final": True,
        "future_evidence_allowed": False,
        "strict_same_game_pregame_eligible": False,
        "enhanced_research_eligible": True,
        "timing_risk_label": "RETROSPECTIVE_EVENT_OUTCOME_TIMING_RISK",
        "source_filename": "plays.csv",
        "source_row_number": 1,
        "source_row_checksum": _sha(f"source-{row_checksum}"),
        "source_file_sha256": _sha("plays.csv"),
        "row_checksum": row_checksum,
    }


def _plate_rows(*, duplicate_checksum: bool = False) -> list[dict[str, object]]:
    outcomes = [("single", "SINGLE"), ("k", "STRIKEOUT")]
    rows: list[dict[str, object]] = []
    for index, (token, outcome) in enumerate(outcomes, start=1):
        checksum = _sha("plate-1" if duplicate_checksum else f"plate-{index}")
        row = {
            **_common(checksum),
            "plate_appearance_id": f"retrosheet:pa:AAA202304010:{index}",
            "source_terminal_token": token,
            "outcome": outcome,
            "taxonomy_version": "retrosheet_to_mlb_gen2_pa_v1",
        }
        rows.append(_pad(row, 69))
    return rows


def _play_rows(*, broken_continuity: bool = False) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for number in (1, 2):
        before_outs = 0 if number == 1 or broken_continuity else 1
        row = {
            **_common(_sha(f"play-{number}")),
            "play_id": f"retrosheet:play:AAA202304010:{number}",
            "event_text": "K",
            "play_number": number,
            "half_play_number": number,
            "inning": 1,
            "top_bottom": 0,
            "batting_team_id": "retrosheet:team:VIS",
            "visiting_team_id": "retrosheet:team:VIS",
            "home_team_id": "retrosheet:team:HOM",
            "outs_before": before_outs,
            "outs_after": number,
            "base_mask_before": 0,
            "base_mask_after": 0,
            "runner_on_first_before": None,
            "runner_on_second_before": None,
            "runner_on_third_before": None,
            "runner_on_first_after": None,
            "runner_on_second_after": None,
            "runner_on_third_after": None,
            "visiting_score_before": 0,
            "home_score_before": 0,
            "visiting_score_after": 0,
            "home_score_after": 0,
            "runs_scored": 0,
            "automatic_runner_placement_before": False,
            "automatic_runner_placement_mode": None,
            "automatic_runner_base": None,
            "automatic_runner_player_id": None,
            "retrosheet_automatic_runner_id": None,
        }
        result.append(_pad(row, 81))
    return result


def _prior_rows() -> list[dict[str, object]]:
    row: dict[str, object] = {
        "event_family": "player_prior",
        "canonical_player_id": "00000000-0000-0000-0000-000000000001",
        "identity_resolution_status": "EXACT",
        "season": 2023,
        "official_date": "2023-12-31",
        "eligible_from_season": 2024,
        "same_season_predictor_eligible": False,
        "source_key": "SABR_LAHMAN_BOX_DOWNLOADS",
        "source_releases": ["lahman_1871-2025_csv"],
        "source_logical_uris": ["open-data/lahman/Batting.csv"],
        "source_derivation_checksum": _sha("prior-row"),
        "timing_classification": "RETROSPECTIVE_RECONSTRUCTION",
    }
    return [_pad(row, 28)]


def _pad(row: dict[str, object], count: int) -> dict[str, object]:
    while len(row) < count:
        row[f"pad_{len(row):03d}"] = "SOURCE_PROVEN"
    assert len(row) == count
    return row


def _write_partition(
    store: Path,
    family: str,
    rows: list[dict[str, object]],
) -> RecoveryFileEntry:
    source = (
        "sabr_lahman_box_downloads"
        if family == "player_prior"
        else "retrosheet_csv_downloads"
    )
    version = LAHMAN_VERSION if family == "player_prior" else RETRO_VERSION
    month = 12 if family == "player_prior" else 4
    schema_version = {
        "plate_appearance": "retrosheet_fd1_plate_appearance_v5",
        "play_transition": "retrosheet_fd1_play_transition_v5",
        "player_prior": "gate3c_fd1_identity_prior_v1",
    }[family]
    probe = store.parent / f"{family}.probe.parquet"
    pl.DataFrame(rows).write_parquet(probe, compression="zstd", statistics=True)
    digest = hashlib.sha256(probe.read_bytes()).hexdigest()
    logical = (
        f"historical-events/source={source}/family={family}/season=2023/"
        f"month={month:02d}/schema={schema_version}/source-version={version}/"
        f"checkpoint={uuid5(NAMESPACE_URL, family)}/governance={_sha('gov-' + family)}/"
        f"part-{digest}.parquet"
    )
    target = store.joinpath(*logical.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    probe.replace(target)
    file_stat = target.stat()
    schema = tuple(
        PhysicalSchemaField(name=name, dtype=str(dtype))
        for name, dtype in pl.read_parquet_schema(target).items()
    )
    schema_payload = [field.to_dict() for field in schema]
    fingerprint = hashlib.sha256(
        json.dumps(
            schema_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()
    parsed = parse_partition_uri(logical)
    date_column = "official_date" if family == "player_prior" else "game_date"
    dates = [str(row[date_column]) for row in rows]
    return RecoveryFileEntry(
        logical_uri=logical,
        size_bytes=file_stat.st_size,
        mtime_ns=file_stat.st_mtime_ns,
        sha256=digest,
        physical_schema_fingerprint=fingerprint,
        physical_schema=schema,
        declared_schema=schema_version,
        row_count=len(rows),
        min_date=min(dates),
        max_date=max(dates),
        source_key=source,
        source_version_id=version,
        checkpoint_id=parsed.checkpoint_id,
        governance_checksum=parsed.governance_checksum,
        family=family,
        season=2023,
        month=month,
        partition_identity_checksum=parsed.partition_identity_checksum,
    )


def _fixture(
    tmp_path: Path,
    *,
    duplicate_checksum: bool = False,
    broken_continuity: bool = False,
) -> tuple[RecoveryManifest, Path, Path]:
    store = tmp_path / "store"
    store.mkdir()
    entries = (
        _write_partition(
            store,
            "plate_appearance",
            _plate_rows(duplicate_checksum=duplicate_checksum),
        ),
        _write_partition(
            store,
            "play_transition",
            _play_rows(broken_continuity=broken_continuity),
        ),
        _write_partition(store, "player_prior", _prior_rows()),
    )
    ledger_payload = b'{"schema_version":"ledger-v1"}\n'
    ledger_sha = hashlib.sha256(ledger_payload).hexdigest()
    ledger_uri = f".runtime/gate3c-fd1/artifacts/ledger-sha256-{ledger_sha}.json"
    ledger = tmp_path.joinpath(*ledger_uri.split("/"))
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_bytes(ledger_payload)
    releases = (
        SourceReleaseBinding(
            source_key="RETROSHEET_CSV_DOWNLOADS",
            source_version_id=RETRO_VERSION,
            dataset_version="retrosheet_through_2025",
            identity_checksum=_sha("retro-release"),
            source_mode="LIVE",
        ),
        SourceReleaseBinding(
            source_key="SABR_LAHMAN_BOX_DOWNLOADS",
            source_version_id=LAHMAN_VERSION,
            dataset_version="lahman_1871-2025_csv",
            identity_checksum=_sha("lahman-release"),
            source_mode="LIVE",
        ),
    )
    archives = tuple(
        SourceArchiveBinding(
            source_key=release.source_key,
            object_key=release.source_key.lower(),
            source_release=release.dataset_version,
            raw_logical_uri=f"open-data/{release.source_key.lower()}/source.blob",
            archive_sha256=_sha(release.source_key + "-archive"),
            payload_sha256=_sha(release.source_key + "-payload"),
            content_length=10,
            download_identity_checksum=_sha(release.source_key + "-download"),
        )
        for release in releases
    )
    generation = GenerationLineage(
        code_revision="a" * 64,
        trusted_dry_run_ledger=TrustedDryRunLedger(
            logical_uri=ledger_uri,
            sha256=ledger_sha,
            schema_version="ledger-v1",
        ),
        source_releases=releases,
        source_archives=archives,
        interrupted_execution=InterruptedExecutionContext(
            attempted_command="python -m app.cli publish-fd1",
            started_at_utc="2026-08-14T00:00:00Z",
            stopped_at_utc="2026-08-15T00:00:00Z",
            completed_files=len(entries),
            completed_bytes=sum(entry.size_bytes for entry in entries),
            sql_rolled_back=True,
            final_execute_evidence_present=False,
            suspected_slow_stage="IDENTITY_MAPPING",
        ),
    )
    manifest = RecoveryManifest.create(
        generation_lineage=generation,
        finalizer_lineage=FinalizerLineage(
            code_revision="b" * 64,
            command_schema_version="recovery-v1",
        ),
        identity_spool=IdentitySpoolBinding(
            logical_uri=".runtime/gate3c-fd1/recovery/identity.csv",
            schema_version="identity-v1",
            sha256=_sha("identity"),
            byte_count=10,
            row_count=1,
        ),
        files=entries,
    )
    return manifest, store, ledger


def test_bounded_rehearsal_is_json_ready_and_non_publishable(tmp_path: Path) -> None:
    manifest, store, ledger = _fixture(tmp_path)
    progress = []
    result = validate_recovery_warehouse(
        manifest,
        store_root=store,
        trusted_ledger_path=ledger,
        active_identity_report=None,
        full=False,
        entries=manifest.files,
        progress_callback=progress.append,
    )

    assert result.mode == REHEARSAL_VALIDATION_MODE
    assert result.passed is True
    assert result.publishable is False
    assert result.partitions == 3
    assert result.manifest_file_count == 3
    assert result.row_count == 5
    assert result.pa_taxonomy_counts["SINGLE"] == 1
    assert result.metrics.transition_adjacencies_checked == 1
    assert len(result.validation_checksum) == 64
    assert result.to_dict()["partitions"] == 3
    json.dumps(result.to_dict())
    assert progress[-1].files_validated == 3


def test_duplicate_row_checksum_fails_closed(tmp_path: Path) -> None:
    manifest, store, ledger = _fixture(tmp_path, duplicate_checksum=True)
    with pytest.raises(FD1WarehouseValidationError, match="unique"):
        validate_recovery_warehouse(
            manifest,
            store_root=store,
            trusted_ledger_path=ledger,
            active_identity_report=None,
            full=False,
            entries=manifest.files,
        )


def test_transition_continuity_fails_closed(tmp_path: Path) -> None:
    manifest, store, ledger = _fixture(tmp_path, broken_continuity=True)
    with pytest.raises(FD1WarehouseValidationError, match="conservation"):
        validate_recovery_warehouse(
            manifest,
            store_root=store,
            trusted_ledger_path=ledger,
            active_identity_report=None,
            full=False,
            entries=manifest.files,
        )


def test_rehearsal_still_stat_checks_unselected_manifest_files(tmp_path: Path) -> None:
    manifest, store, ledger = _fixture(tmp_path)
    unselected = next(entry for entry in manifest.files if entry.family == "play_transition")
    target = store.joinpath(*unselected.logical_uri.split("/"))
    target.write_bytes(target.read_bytes() + b"changed")

    with pytest.raises(FD1WarehouseValidationError, match="stat binding"):
        validate_recovery_warehouse(
            manifest,
            store_root=store,
            trusted_ledger_path=ledger,
            active_identity_report=None,
            full=False,
            entries=[manifest.files[0]],
        )


def test_full_mode_rejects_any_population_other_than_exact_recovery(tmp_path: Path) -> None:
    manifest, store, ledger = _fixture(tmp_path)
    with pytest.raises(FD1WarehouseValidationError, match="exact interrupted"):
        validate_recovery_warehouse(
            manifest,
            store_root=store,
            trusted_ledger_path=ledger,
            active_identity_report=None,
            full=True,
        )


def test_ledger_checksum_change_fails_before_parquet_scan(tmp_path: Path) -> None:
    manifest, store, ledger = _fixture(tmp_path)
    ledger.write_text('{"schema_version":"ledger-v1","changed":true}\n', encoding="utf-8")
    with pytest.raises(FD1WarehouseValidationError, match="checksum"):
        validate_recovery_warehouse(
            manifest,
            store_root=store,
            trusted_ledger_path=ledger,
            active_identity_report=None,
            full=False,
        )


def test_checksummed_active_identity_report_is_accepted_without_raw_scan(
    tmp_path: Path,
) -> None:
    manifest, store, ledger = _fixture(tmp_path)
    report = {
        "active_identity_coverage_report": {
            "affected_players": [],
            "derivation_checksum": _sha("active-identity-derivation"),
            "population_coverage": [
                {
                    "label": "GEN2_PRIMARY_2023_2025",
                    "exact_link_rates": [
                        {
                            "exact_reference_rate": 1.0,
                            "exact_unique_rate": 1.0,
                            "conflict_reference_count": 0,
                        }
                    ],
                }
            ],
            "schema_version": "gate3c_fd1_active_identity_coverage_v1",
        },
        "threshold_decision": {
            "primary_meets_threshold": True,
            "primary_reference_meets_threshold": True,
            "primary_threshold": 0.995,
            "primary_unique_meets_threshold": True,
        },
    }
    encoded = json.dumps(report, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    identity_path = (
        tmp_path
        / ".runtime"
        / "gate3c-fd1"
        / "artifacts"
        / f"active-identity-actual-sha256-{digest}.json"
    )
    identity_path.parent.mkdir(parents=True, exist_ok=True)
    identity_path.write_bytes(encoded)

    result = validate_recovery_warehouse(
        manifest,
        store_root=store,
        trusted_ledger_path=ledger,
        active_identity_report=identity_path,
        full=False,
        entries=manifest.files,
    )

    assert result.active_identity is not None
    assert result.active_identity.passes is True
    assert result.active_identity.artifact_sha256 == digest

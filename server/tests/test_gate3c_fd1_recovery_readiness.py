from __future__ import annotations

import ast
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import polars as pl
import pytest

import app.gen2.fd1_recovery_readiness as readiness
from app.gen2.fd1_readiness import ReadinessDecision
from app.gen2.fd1_reconciliation import (
    FIELD_FAMILIES,
    AspGameEvidence,
    ComparisonStatus,
    FieldComparison,
    GameReconciliation,
    ReconciliationResult,
    RetrosheetGameEvidence,
)


def test_sql_boundary_allows_only_read_statements() -> None:
    assert readiness._statement_kind(" SET TRANSACTION READ ONLY ") == "CONTROL"
    assert readiness._statement_kind("SELECT id FROM evidence") == "READ"
    assert readiness._statement_kind(
        "WITH chosen AS (SELECT 1) SELECT * FROM chosen"
    ) == "READ"
    assert readiness._statement_kind("SHOW transaction_read_only") == "READ"
    assert readiness._statement_kind("UPDATE evidence SET changed=true") == "REJECT"
    assert readiness._statement_kind("SELECT 1; DELETE FROM evidence") == "REJECT"
    assert (
        readiness._statement_kind(
            "WITH removed AS (DELETE FROM evidence RETURNING id) SELECT id FROM removed"
        )
        == "REJECT"
    )
    assert readiness._statement_kind("SELECT id FROM evidence FOR UPDATE") == "REJECT"


def test_recovery_result_must_be_original_finalized_content_addressed_evidence(
    tmp_path: Path,
) -> None:
    report_root = tmp_path / "recovery" / "reports"
    report_root.mkdir(parents=True)
    unsigned = {
        "schema_version": readiness.RECOVERY_REPORT_SCHEMA_VERSION,
        "mode": "finalize",
        "status": "FINALIZED",
        "last_completed_stage": "FINALIZED",
        "idempotent_reuse": False,
        "provider_calls": 0,
        "network_requests": 0,
        "provider_transports_constructed": 0,
        "mapping_conflicts": 0,
    }
    document = {**unsigned, "result_checksum": readiness._checksum(unsigned)}
    payload = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    digest = sha256(payload).hexdigest()
    path = report_root / f"fd1-recovery-result-sha256-{digest}.json"
    path.write_bytes(payload)

    restored, artifact = readiness._load_recovery_result(
        path, runtime_root=tmp_path, project_root=tmp_path
    )

    assert restored == document
    assert artifact.sha256 == digest
    reused = dict(document)
    reused["mode"] = "idempotency-reuse"
    reused_unsigned = dict(reused)
    reused_unsigned.pop("result_checksum")
    reused["result_checksum"] = readiness._checksum(reused_unsigned)
    reused_payload = (
        json.dumps(reused, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    reused_digest = sha256(reused_payload).hexdigest()
    reused_path = report_root / f"fd1-recovery-result-sha256-{reused_digest}.json"
    reused_path.write_bytes(reused_payload)
    with pytest.raises(readiness.FD1RecoveryReadinessError, match="original successful"):
        readiness._load_recovery_result(
            reused_path, runtime_root=tmp_path, project_root=tmp_path
        )


def test_readiness_evidence_is_content_addressed_immutable_and_reusable(
    tmp_path: Path,
) -> None:
    root = tmp_path / "readiness"
    document = {
        "schema_version": readiness.FD1_RECOVERY_READINESS_SCHEMA_VERSION,
        "decision": ReadinessDecision.READY_FOR_GEN2_FREE_DATA_MODELING.value,
    }

    first, first_reused = readiness._publish_json(document, artifact_root=root)
    second, second_reused = readiness._publish_json(document, artifact_root=root)

    assert first == second
    assert first_reused is False
    assert second_reused is True
    target = root / f"fd1-recovery-readiness-sha256-{first.sha256}.json"
    original = target.read_bytes()
    target.write_bytes(b"tampered\n")
    with pytest.raises(readiness.FD1RecoveryReadinessError, match="conflicting"):
        readiness._publish_json(document, artifact_root=root)
    target.write_bytes(original)


def test_database_evidence_projects_immutable_checksum_maps_to_json() -> None:
    database = readiness.RecoveryDatabaseEvidence(
        backend="postgresql",
        transaction_read_only=True,
        transaction_rolled_back=True,
        read_statements=12,
        rejected_statements=0,
        checkpoint_count=6,
        checkpoint_output_checksums=MappingProxyType({"FINALIZED": "a" * 64}),
        canonical_counts=MappingProxyType({"PARTITION": 329}),
        identity_outcomes=MappingProxyType({"REUSE": 1}),
        quarantine_rows=0,
        resolved_mapping_rows=1,
    )

    projected = readiness._plain(database)

    assert projected["checkpoint_output_checksums"] == {"FINALIZED": "a" * 64}
    assert json.loads(readiness._canonical_bytes(projected))["canonical_counts"] == {
        "PARTITION": 329
    }


def test_partition_target_reconstruction_removes_operational_id_and_binds_manifest() -> None:
    source_version_id = "00000000-0000-0000-0000-000000000001"
    checkpoint_id = "00000000-0000-0000-0000-000000000002"
    family = "plate_appearance"
    schema_version = readiness.MATERIALIZED_FAMILY_SCHEMA_VERSIONS[family]
    contributors = (
        {
            "logical_uri": "raw/retrosheet/2025/plays.csv",
            "file_sha256": "a" * 64,
        },
    )
    governance = {
        "source_key": readiness.RETROSHEET_SOURCE_KEY,
        "source_version_id": source_version_id,
        "checkpoint_id": checkpoint_id,
        "event_family": family,
        "season": 2025,
        "month": 4,
        "schema_version": schema_version,
        "schema_hash": "b" * 64,
        "raw_contributor_checksum": readiness.canonical_sha256(contributors),
        "code_revision": "c" * 64,
        "evidence_tier": "A",
        "timing_classification": "AVAILABLE_ONLY_AFTER_GAME_FINAL",
    }
    semantic = {
        **governance,
        "logical_uri": "historical-events/test.parquet",
        "row_count": 1,
        "game_count": 1,
        "plate_appearance_count": 1,
        "player_count": 2,
        "min_event_date": "2025-04-01",
        "max_event_date": "2025-04-01",
        "min_event_at": None,
        "max_event_at": None,
        "storage_format": "PARQUET",
        "compression": "ZSTD",
        "file_sha256": "d" * 64,
        "file_size_bytes": 123,
        "raw_contributor_manifest": contributors,
    }
    identity = readiness.canonical_sha256(semantic)
    artifact = readiness.HistoricalEventPartitionArtifact(
        identity_checksum=identity,
        reused=True,
        **semantic,
    )
    target = readiness.PublicationTarget.create(
        object_kind="PARTITION",
        target_id="00000000-0000-0000-0000-000000000003",
        natural_key={"logical_uri": artifact.logical_uri},
        semantic_identity_checksum=identity,
        target_payload=artifact.orm_values(),
    )
    entry = SimpleNamespace(
        source_key="retrosheet_csv_downloads",
        source_version_id=artifact.source_version_id,
        checkpoint_id=artifact.checkpoint_id,
        family=artifact.event_family,
        season=artifact.season,
        month=artifact.month,
        logical_uri=artifact.logical_uri,
        row_count=artifact.row_count,
        declared_schema=artifact.schema_version,
        physical_schema_fingerprint=artifact.schema_hash,
        sha256=artifact.file_sha256,
        size_bytes=artifact.file_size_bytes,
        governance_checksum=readiness.canonical_sha256(governance),
        partition_identity_checksum="e" * 64,
    )

    restored = readiness._artifact_from_partition_target(target, entry)

    assert restored == artifact
    assert restored.identity_checksum != entry.partition_identity_checksum


def test_reconciliation_round_trip_remains_typed_and_lossless() -> None:
    asp = AspGameEvidence(
        game_id="asp-game-1",
        official_date=date(2025, 4, 1),
        scheduled_at=readiness.datetime.fromisoformat("2025-04-01T23:00:00+00:00"),
        home_team_id="asp-home",
        away_team_id="asp-away",
        home_score=5,
        away_score=3,
        source_record_id="source-record",
        source_version_id="source-version",
        game_number=1,
    )
    retro = RetrosheetGameEvidence(
        raw_game_id="AAA202504010",
        source_game_id="retrosheet:game:AAA202504010",
        game_date=date(2025, 4, 1),
        home_team_raw_id="AAA",
        away_team_raw_id="BBB",
        game_number=1,
        source_game_number=0,
        home_score=5,
        away_score=3,
    )
    comparisons = MappingProxyType(
        {
            family: FieldComparison(
                status=ComparisonStatus.MATCH,
                asp_value="same",
                retrosheet_value="same",
                details=MappingProxyType({"exact": True}),
            )
            for family in FIELD_FAMILIES
        }
    )
    result = ReconciliationResult(
        target_season=2025,
        expected_target_games=1,
        asp_target_count=1,
        retrosheet_target_count=1,
        matched_game_count=1,
        mapping_method_counts=MappingProxyType({"EXACT_SOURCE_ID": 1}),
        family_counts=MappingProxyType(
            {
                family: MappingProxyType({ComparisonStatus.MATCH.value: 1})
                for family in FIELD_FAMILIES
            }
        ),
        games=(
            GameReconciliation(
                asp=asp,
                retrosheet=retro,
                mapping_method="EXACT_SOURCE_ID",
                mapped_retrosheet=MappingProxyType({"game_id": asp.game_id}),
                comparisons=comparisons,
            ),
        ),
        unmatched_asp=(),
        unmatched_retrosheet=(),
        crosswalk_summary=MappingProxyType({"matching_method": "EXPLICIT_ID_ONLY"}),
    )

    restored = readiness._reconciliation_result(result.as_dict(include_games=True))

    assert restored.as_dict(include_games=True) == result.as_dict(include_games=True)


def test_officials_and_usage_semantics_are_measured_from_parquet_rows() -> None:
    season = readiness._SeasonAccumulator.create(2025)
    checksum = "a" * 64
    starters = []
    for team, manager in (
        ("retrosheet:team:AAA", "mgraa001"),
        ("retrosheet:team:BBB", "mgrbb001"),
    ):
        starters.append(
            {
                "usage_id": f"usage:{team}",
                "game_id": "retrosheet:game:AAA202504010",
                "team_id": team,
                "appearance_sequence": 1,
                "pitcher_role": "STARTER",
                "actual_starter_target": True,
                "participant_evidence": "ACTUAL_STARTER_HISTORICAL_RECONSTRUCTION",
                "timing_risk_label": "HISTORICAL_ACTUAL_STARTER_TIMING_RISK",
                "strict_same_game_pregame_eligible": False,
                "strict_prior_eligible_after_game_final": True,
                "enhanced_research_eligible": True,
                "manager_id": f"retrosheet:player:{manager}",
                "retrosheet_manager_id": manager,
                "manager_evidence_source_filename": "teamstats.csv",
                "manager_evidence_source_row_number": 2,
                "manager_evidence_source_row_checksum": "b" * 64,
                "manager_evidence_source_file_sha256": checksum,
                "stat_p_ipouts": 18,
            }
        )
    assert readiness._scan_usage(
        pl.DataFrame(starters),
        season,
        starter=True,
        contributor_hashes={checksum},
    ) == 2

    weather = pl.DataFrame(
        [
            {
                "game_id": "retrosheet:game:AAA202504010",
                "weather_coverage": "COMPLETE_RETROSHEET",
                "weather_source": "RETROSHEET_OPEN_DATA",
                "noaa_gap_fill": False,
                "umpire_home_id": "retrosheet:player:umpaa001",
                "umpire_first_base_id": "retrosheet:player:umpbb001",
                "umpire_second_base_id": None,
                "umpire_third_base_id": "retrosheet:player:umpcc001",
                "umpire_left_field_id": None,
                "umpire_right_field_id": None,
                "retrosheet_umpire_ids": {
                    "home": "umpaa001",
                    "first_base": "umpbb001",
                    "second_base": "(none)",
                    "third_base": "umpcc001",
                    "left_field": "(none)",
                    "right_field": "(none)",
                },
                "source_filename": "gameinfo.csv",
                "source_row_number": 2,
                "source_row_checksum": "c" * 64,
                "source_file_sha256": checksum,
            }
        ]
    )
    assert readiness._scan_weather(weather, season) == 1
    assert season.umpire_assignments == 3
    assert season.manager_ids == {
        "retrosheet:player:mgraa001",
        "retrosheet:player:mgrbb001",
    }


def test_tier_projection_is_disjoint_and_prior_only_has_precedence() -> None:
    primary = readiness._SeasonAccumulator.create(2025)
    prior = readiness._SeasonAccumulator.create(2022)
    rows = pl.DataFrame(
        [
            {"actual_starting_lineup": True},
            {"actual_starting_lineup": False},
            {"actual_starting_lineup": False},
        ]
    )
    readiness._classify_tiers(
        SimpleNamespace(event_family="player_game_batting", season=2025),
        rows,
        primary,
    )
    readiness._classify_tiers(
        SimpleNamespace(event_family="play_transition", season=2022),
        pl.DataFrame({"row": [1, 2, 3]}),
        prior,
    )

    assert (primary.tier_candidate_rows, primary.strict_rows, primary.enhanced_rows) == (
        3,
        2,
        1,
    )
    assert prior.tier_candidate_rows == prior.prior_only_rows == 3


def test_transition_scan_accepts_only_the_governed_terminal_np_bridge() -> None:
    bridge = {
        "game_id": "retrosheet:game:AAA202504010",
        "play_id": "retrosheet:play:AAA202504010:57",
        "play_number": 57,
        "event_text": "NP",
        "outs_before": 3,
        "outs_after": 3,
        "base_mask_before": 0,
        "base_mask_after": 0,
        "visiting_score_before": 4,
        "home_score_before": 3,
        "visiting_score_after": 4,
        "home_score_after": 3,
        "runs_scored": 0,
        "pa_outcome": None,
    }
    season = readiness._SeasonAccumulator.create(2025)

    readiness._scan_transitions(pl.DataFrame([bridge]), season, set())

    assert season.transitions == 1
    assert season.final_scores == {
        "retrosheet:game:AAA202504010": (57, 3, 4)
    }

    with pytest.raises(readiness.FD1RecoveryReadinessError, match="conservation"):
        readiness._scan_transitions(
            pl.DataFrame([{**bridge, "event_text": "play"}]),
            readiness._SeasonAccumulator.create(2025),
            set(),
        )


def test_projection_calls_the_exact_pure_readiness_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = {"lineage": "frozen"}
    reconciliation = SimpleNamespace(as_dict=lambda include_games: {"games": 2430})
    identity = SimpleNamespace(derivation_checksum="b" * 64)
    warehouse = SimpleNamespace(
        operator_ledger_checksum=readiness._checksum(ledger),
        identity_evidence_checksum="b" * 64,
        reconciliation_result_checksum=readiness._checksum({"games": 2430}),
        seasons=tuple(SimpleNamespace(season=year) for year in range(2021, 2026)),
        tier_leakage=(),
        temporal=SimpleNamespace(),
        transition_state_outcome_cells=8,
        conservation_conflicts=(),
    )
    season_rows = tuple(
        SimpleNamespace(
            season=year,
            tier_candidate_rows=1,
            tier_eligible_rows=1,
            strict_deployable_rows=0 if year < 2023 else 1,
            enhanced_research_rows=0,
            prior_only_rows=1 if year < 2023 else 0,
            taxonomy_outcome_counts=tuple(
                SimpleNamespace(outcome=name, count=1) for name in readiness.PA_OUTCOME_NAMES
            ),
            taxonomy_mapped_terminal_events=8,
            transitions=1,
            valid_final_games=1,
            starter_duration_rows=1,
            bullpen_usage_rows=1,
        )
        for year in range(2021, 2026)
    )
    for raw, projected in zip(warehouse.seasons, season_rows, strict=True):
        raw.historical_actual_lineup_rows = 0
        raw.isolated_enhanced_lineup_rows = 0
        raw.historical_actual_starter_rows = 0
        raw.isolated_enhanced_starter_rows = 0
    monkeypatch.setattr(readiness, "_validate_active_identity_summary", lambda *_: None)
    monkeypatch.setattr(readiness, "_validate_identity_conflict_projection", lambda *_: None)
    monkeypatch.setattr(readiness, "_source_summaries", lambda *_: ())
    monkeypatch.setattr(
        readiness, "_prior_rows_by_season", lambda *_: {year: 1 for year in range(2021, 2026)}
    )
    monkeypatch.setattr(readiness, "_park_rows_by_target_season", lambda *_: {2023: 1})
    monkeypatch.setattr(
        readiness,
        "_season_summary",
        lambda row, *_args, **_kwargs: season_rows[row.season - 2021],
    )
    monkeypatch.setattr(readiness, "_asp_summary", lambda *_: SimpleNamespace())
    terminal = SimpleNamespace(
        decision=ReadinessDecision.READY_WITH_DOCUMENTED_FREE_DATA_LIMITATIONS,
        modeling_can_proceed=True,
    )
    calls = []

    def audit(evidence):
        calls.append(evidence)
        return terminal

    monkeypatch.setattr(readiness, "audit_fd1_readiness", audit)

    evidence, result = readiness._project_readiness_input(
        ledger, warehouse, identity, reconciliation
    )

    assert result is terminal
    assert calls == [evidence]
    assert evidence.training.event_training_rows == 24


def test_source_has_no_provider_raw_discovery_or_mutating_sql_capability() -> None:
    path = Path(readiness.__file__)
    source = path.read_text(encoding="utf-8")
    module = ast.parse(source)
    imports = {
        alias.name
        for node in ast.walk(module)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert not any("provider" in name.lower() for name in imports)
    assert ".rglob(" not in source
    assert ".glob(" not in source
    assert "os.walk(" not in source
    assert ".write_parquet(" not in source
    assert ".commit(" not in source
    assert "audit_fd1_readiness(evidence)" in source
    assert '"raw_source_scans": 0' in source

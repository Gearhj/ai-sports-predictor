from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, date, datetime
import importlib.util
from pathlib import Path
import socket
import sys
from types import MappingProxyType, SimpleNamespace

import polars as pl
import pytest

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


OPERATOR_PATH = (
    Path(__file__).resolve().parents[2]
    / ".runtime"
    / "gate3c-fd1"
    / "readiness_actual.py"
)


def _load_operator():
    spec = importlib.util.spec_from_file_location(
        "gate3c_fd1_readiness_actual_test_module", OPERATOR_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_readiness_actual_sql_and_network_boundaries_are_fail_closed() -> None:
    operator = _load_operator()

    assert operator._statement_kind(" SET TRANSACTION READ ONLY ") == "CONTROL"
    assert operator._statement_kind("SELECT id FROM evidence") == "READ"
    assert operator._statement_kind("WITH chosen AS (SELECT 1) SELECT * FROM chosen") == "READ"
    assert operator._statement_kind("SHOW transaction_read_only") == "READ"
    assert operator._statement_kind("UPDATE evidence SET changed = true") == "REJECT"
    assert operator._statement_kind("SELECT 1; DELETE FROM evidence") == "REJECT"

    with operator._provider_free_network_guard() as boundary:
        assert socket.getaddrinfo("localhost", 5432)
        with pytest.raises(operator.ReadinessActualError, match="external network"):
            socket.getaddrinfo("example.invalid", 443)

    assert boundary.loopback_connections_permitted == 1
    assert boundary.blocked_external_attempts == 1
    assert boundary.external_requests == 0


def test_readiness_actual_artifacts_are_content_addressed_and_immutable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operator = _load_operator()
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    monkeypatch.setattr(operator, "ARTIFACT_ROOT", artifact_root)
    document = {"schema_version": "test", "measured": {"rows": 42}}

    first, first_reused = operator._publish_json("readiness-actual-input", document)
    second, second_reused = operator._publish_json("readiness-actual-input", document)

    assert first_reused is False
    assert second_reused is True
    assert second == first
    path = tmp_path / Path(first.logical_path).name
    # The reported logical path is project-relative in production.  Use the
    # exact content-addressed file selected inside the patched artifact root.
    path = artifact_root / f"readiness-actual-input-sha256-{first.sha256}.json"
    original = path.read_bytes()
    path.write_bytes(b"tampered\n")
    with pytest.raises(operator.ReadinessActualError, match="changed"):
        operator._publish_json("readiness-actual-input", document)
    path.write_bytes(original)


def test_reconciliation_json_round_trip_is_typed_and_lossless() -> None:
    operator = _load_operator()
    asp = AspGameEvidence(
        game_id="asp-game-1",
        official_date=date(2025, 4, 1),
        scheduled_at=datetime(2025, 4, 1, 23, tzinfo=UTC),
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
    game = GameReconciliation(
        asp=asp,
        retrosheet=retro,
        mapping_method="EXACT_SOURCE_ID",
        mapped_retrosheet=MappingProxyType({"game_id": asp.game_id}),
        comparisons=comparisons,
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
        games=(game,),
        unmatched_asp=(),
        unmatched_retrosheet=(),
        crosswalk_summary=MappingProxyType({"matching_method": "EXPLICIT_ID_ONLY"}),
    )

    restored = operator._reconciliation_result(result.as_dict(include_games=True))

    assert restored.as_dict(include_games=True) == result.as_dict(include_games=True)


def test_officials_lineage_and_sentinel_semantics_are_measured_exactly() -> None:
    operator = _load_operator()
    season = operator.SeasonAccumulator.create(2025)
    checksum = "a" * 64
    starter_rows = []
    for team, manager in (("retrosheet:team:AAA", "mgraa001"), ("retrosheet:team:BBB", "mgrbb001")):
        starter_rows.append(
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
    populated, lineage = operator._scan_usage(
        pl.DataFrame(starter_rows),
        season,
        starter=True,
        contributor_hashes={checksum},
    )
    bullpen_rows = [
        {
            **row,
            "usage_id": f"{row['usage_id']}:reliever",
            "appearance_sequence": 2,
            "pitcher_role": "RELIEVER",
            "actual_starter_target": False,
            "participant_evidence": "ACTUAL_RELIEVER_HISTORICAL_RECONSTRUCTION",
            "timing_risk_label": "HISTORICAL_ACTUAL_RELIEVER_USAGE_TIMING_RISK",
        }
        for row in starter_rows
    ]
    bullpen_populated, bullpen_lineage = operator._scan_usage(
        pl.DataFrame(bullpen_rows),
        season,
        starter=False,
        contributor_hashes={checksum},
    )

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
    umpire_lineage = operator._scan_weather(weather, season)

    assert populated == lineage == 2
    assert bullpen_populated == bullpen_lineage == 2
    assert season.bullpen_rows == 2
    assert season.usage_sequences[
        ("retrosheet:game:AAA202504010", "retrosheet:team:AAA")
    ] == {1, 2}
    assert season.manager_ids == {
        "retrosheet:player:mgraa001",
        "retrosheet:player:mgrbb001",
    }
    assert season.manager_pairs["retrosheet:game:AAA202504010"] == {
        ("retrosheet:team:AAA", "retrosheet:player:mgraa001"),
        ("retrosheet:team:BBB", "retrosheet:player:mgrbb001"),
    }
    assert umpire_lineage == 1
    assert season.umpire_assignments == 3
    assert season.umpire_plate_games == {"retrosheet:game:AAA202504010"}

    broken = weather.with_columns(
        pl.lit("retrosheet:player:nonee000").alias("umpire_second_base_id")
    )
    with pytest.raises(operator.ReadinessActualError, match="normalization"):
        operator._scan_weather(broken, operator.SeasonAccumulator.create(2025))

    legacy_bullpen_timing = pl.DataFrame(bullpen_rows).with_columns(
        pl.lit("HISTORICAL_ACTUAL_RELIEVER_TIMING_RISK").alias("timing_risk_label")
    )
    with pytest.raises(operator.ReadinessActualError, match="timing/role"):
        operator._scan_usage(
            legacy_bullpen_timing,
            operator.SeasonAccumulator.create(2025),
            starter=False,
            contributor_hashes={checksum},
        )


def test_tier_projection_is_disjoint_with_enhanced_only_precedence() -> None:
    operator = _load_operator()
    primary = operator.SeasonAccumulator.create(2025)
    prior = operator.SeasonAccumulator.create(2022)
    artifact = SimpleNamespace(event_family="player_game_batting", season=2025)
    rows = pl.DataFrame(
        [
            {"actual_starting_lineup": True},
            {"actual_starting_lineup": False},
            {"actual_starting_lineup": False},
        ]
    )

    operator._classify_tiers(artifact, rows, primary)
    operator._classify_tiers(
        SimpleNamespace(event_family="weather", season=2025),
        pl.DataFrame({"row": [1, 2]}),
        primary,
    )
    operator._classify_tiers(
        SimpleNamespace(event_family="play_transition", season=2022),
        pl.DataFrame({"row": [1, 2, 3, 4]}),
        prior,
    )

    assert (primary.tier_candidate_rows, primary.strict_rows, primary.enhanced_rows) == (
        5,
        2,
        3,
    )
    assert primary.prior_only_rows == 0
    assert prior.tier_candidate_rows == prior.prior_only_rows == 4
    assert prior.strict_rows == prior.enhanced_rows == 0


def test_partition_row_checksum_and_source_lineage_fail_closed() -> None:
    operator = _load_operator()
    artifact = SimpleNamespace(
        event_family="weather",
        season=2025,
        month=4,
        source_version_id="11111111-1111-1111-1111-111111111111",
        raw_contributor_manifest=({"file_sha256": "a" * 64},),
    )
    record = SimpleNamespace(artifact=artifact)
    frame = pl.DataFrame(
        [
            {
                "source_key": "RETROSHEET_CSV_DOWNLOADS",
                "source_version_id": artifact.source_version_id,
                "population_category": "GEN2_PRIMARY_2023_2025",
                "game_id": "retrosheet:game:AAA202504010",
                "game_date": "2025-04-01",
                "event_date": "2025-04-01",
                "season": 2025,
                "month": 4,
                "final_available_date": "2025-04-01",
                "future_evidence_allowed": False,
                "available_only_after_game_final": True,
                "source_filename": "gameinfo.csv",
                "source_row_number": 2,
                "source_row_checksum": "b" * 64,
                "source_file_sha256": "a" * 64,
                "row_checksum": "c" * 64,
            }
        ]
    )

    assert operator._validate_common_rows(record, frame) == 1
    with pytest.raises(operator.ReadinessActualError, match="lineage"):
        operator._validate_common_rows(
            record, frame.with_columns(pl.lit("not-a-checksum").alias("row_checksum"))
        )
    with pytest.raises(operator.ReadinessActualError, match="leaks in time"):
        operator._validate_common_rows(
            record,
            frame.with_columns(
                pl.lit("2026-01-01").alias("final_available_date")
            ),
        )


def test_transition_cells_conservation_and_measured_temporal_folds() -> None:
    operator = _load_operator()
    seasons = {
        season: operator.SeasonAccumulator.create(season)
        for season in operator.ACTIVE_SEASONS
    }
    cells = set()
    transitions = pl.DataFrame(
        [
            {
                "game_id": "retrosheet:game:AAA202504010",
                "play_id": "play-1",
                "play_number": 1,
                "outs_before": 0,
                "outs_after": 1,
                "base_mask_before": 0,
                "base_mask_after": 0,
                "visiting_score_before": 0,
                "home_score_before": 0,
                "visiting_score_after": 0,
                "home_score_after": 0,
                "runs_scored": 0,
                "pa_outcome": "BIP_OUT",
            },
            {
                "game_id": "retrosheet:game:AAA202504010",
                "play_id": "play-2",
                "play_number": 2,
                "outs_before": 1,
                "outs_after": 1,
                "base_mask_before": 0,
                "base_mask_after": 0,
                "visiting_score_before": 0,
                "home_score_before": 0,
                "visiting_score_after": 0,
                "home_score_after": 1,
                "runs_scored": 1,
                "pa_outcome": "HOME_RUN",
            },
        ]
    )

    operator._scan_transitions(transitions, seasons[2025], cells)

    assert cells == {(0, 0, "BIP_OUT"), (1, 0, "HOME_RUN")}
    assert seasons[2025].final_scores["retrosheet:game:AAA202504010"] == (
        2,
        1,
        0,
    )
    broken = transitions.with_columns(
        pl.when(pl.col("play_number") == 2)
        .then(0)
        .otherwise(pl.col("runs_scored"))
        .alias("runs_scored")
    )
    with pytest.raises(operator.ReadinessActualError, match="conservation"):
        operator._scan_transitions(broken, operator.SeasonAccumulator.create(2025), set())

    for season, start, end, rows in (
        (2023, date(2023, 3, 30), date(2023, 10, 1), 100),
        (2024, date(2024, 3, 28), date(2024, 9, 30), 110),
        (2025, date(2025, 3, 27), date(2025, 9, 28), 120),
    ):
        seasons[season].pa_dates.update((start, end))
        seasons[season].primary_pa_rows = rows
        seasons[season].chronology_checked_rows = rows + 10
    temporal = operator._temporal_evidence(seasons)

    assert temporal.formable_fold_count == 2
    assert temporal.folds[0].training_end == date(2023, 10, 1)
    assert temporal.folds[0].validation_start == date(2024, 3, 28)
    assert temporal.folds[1].training_rows == 210
    assert temporal.folds[1].validation_rows == 120


def test_run_orchestrates_governed_audit_and_three_evidence_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operator = _load_operator()
    committed = operator.ArtifactEvidence("committed.json", "a" * 64, 1)
    identity_artifact = operator.ArtifactEvidence("identity.json", "b" * 64, 2)
    reconciliation_artifact = operator.ArtifactEvidence("reconciliation.json", "c" * 64, 3)
    identity = SimpleNamespace(derivation_checksum="d" * 64)
    reconciliation = object()
    population = operator.CommittedPopulation(
        partitions=(),
        partitions_by_family=MappingProxyType({"weather": 1}),
        rows_by_family=MappingProxyType({"weather": 10}),
        file_bytes=100,
        sql_population_checksum="e" * 64,
        parquet_population_checksum="f" * 64,
    )
    database = operator.DatabaseBoundary(
        backend="postgresql",
        transaction_read_only=True,
        transaction_rolled_back=True,
    )
    network = operator.NetworkBoundary()
    evidence = SimpleNamespace(
        derivation_checksum="1" * 64,
        tier_projection_unit="DISJOINT_TRAINING_ELIGIBILITY_ROWS",
    )
    scan = operator.WarehouseScan(
        evidence=evidence,
        partition_counts=population.partitions_by_family,
        row_counts=population.rows_by_family,
        schema_version_counts=MappingProxyType({"schema": 1}),
        file_bytes=100,
        tier_projection_policy=operator.TIER_PROJECTION_POLICY,
        validated_partition_count=1,
        validated_row_count=10,
        row_lineage_checked_count=10,
        manager_lineage_checked_count=2,
        umpire_lineage_checked_count=1,
    )
    result = SimpleNamespace(
        decision=ReadinessDecision.READY_WITH_DOCUMENTED_FREE_DATA_LIMITATIONS,
        modeling_can_proceed=True,
    )
    closeout = SimpleNamespace(
        result=result,
        derivation_checksum="2" * 64,
        as_dict=lambda: {"readiness_result": {"decision": result.decision.value}},
    )
    governance = SimpleNamespace()

    monkeypatch.setattr(operator, "_readiness_operator_revision", lambda: "3" * 64)
    monkeypatch.setattr(operator, "_load_committed_evidence", lambda path: ({"ledger": True}, committed))
    monkeypatch.setattr(operator, "_load_identity_artifact", lambda path: (identity, identity_artifact, {}))
    monkeypatch.setattr(operator, "_load_reconciliation", lambda value: (reconciliation, reconciliation_artifact))
    monkeypatch.setattr(operator, "_governance_evidence", lambda: governance)
    monkeypatch.setattr(operator, "_provider_free_network_guard", lambda: nullcontext(network))
    monkeypatch.setattr(operator, "_load_committed_population", lambda value: (population, database))
    monkeypatch.setattr(operator, "_scan_warehouse", lambda *args: scan)
    monkeypatch.setattr(operator, "build_fd1_readiness_closeout", lambda *args: closeout)
    monkeypatch.setattr(operator, "_input_document", lambda **kwargs: {"schema_version": "input"})
    monkeypatch.setattr(operator, "_report_document", lambda **kwargs: {"schema_version": "report"})
    published = []

    def publish(prefix, document):
        published.append((prefix, document))
        return operator.ArtifactEvidence(f"{prefix}.json", "4" * 64, 4), False

    monkeypatch.setattr(operator, "_publish_json", publish)

    output = operator.run(
        committed_evidence=tmp_path / "committed.json",
        identity_artifact=tmp_path / "identity.json",
    )

    assert output["ready"] is True
    assert output["decision"] == "READY_WITH_DOCUMENTED_FREE_DATA_LIMITATIONS"
    assert output["database_writes"] == 0
    assert output["governed_parquet_writes"] == 0
    assert output["network_requests"] == 0
    assert [prefix for prefix, _ in published] == [
        "readiness-actual-input",
        "readiness-actual-result",
        "readiness-actual-report-evidence",
    ]
    assert published[1][1]["execution_boundary"]["evidence_artifact_writes"] == 3


def test_operator_source_has_no_publication_provider_or_model_capability() -> None:
    source = OPERATOR_PATH.read_text(encoding="utf-8")

    assert "publish_actual.py" not in source
    assert ".write_partition(" not in source
    assert "session.commit(" not in source
    assert "mlb_statsapi" not in source.lower()
    assert "baseballsavant" not in source.lower()
    assert "fit(" not in source
    assert "predict(" not in source
    assert "raw_source_scans\": 0" in source

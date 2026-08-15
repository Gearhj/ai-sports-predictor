from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
import importlib.util
import inspect
from pathlib import Path
import sys
from types import MappingProxyType, SimpleNamespace

import pytest

from app.gen2.fd1_materializer import MaterializationCounters
from app.gen2.fd1_reconciliation import (
    ASP_GAME_NUMBER_DERIVED_SINGLETON,
    ASP_GAME_NUMBER_DERIVED_START_TIME,
    AspGameEvidence,
)
from app.gen2.fd1_readiness_closeout import OPERATOR_SCHEMA_VERSION


OPERATOR_PATH = (
    Path(__file__).resolve().parents[2]
    / ".runtime"
    / "gate3c-fd1"
    / "publish_actual.py"
)


def _load_operator():
    spec = importlib.util.spec_from_file_location(
        "gate3c_fd1_publish_actual_test_module", OPERATOR_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _live_shaped_missing_number_cohort() -> tuple[AspGameEvidence, ...]:
    games: list[AspGameEvidence] = []
    first_day = date(2025, 1, 1)
    for index in range(2_374):
        game_date = first_day + timedelta(days=index % 300)
        games.append(
            AspGameEvidence(
                game_id=f"asp-single-{index:04d}",
                official_date=game_date,
                scheduled_at=datetime(
                    game_date.year,
                    game_date.month,
                    game_date.day,
                    18,
                    tzinfo=UTC,
                ),
                home_team_id=f"home-single-{index:04d}",
                away_team_id=f"away-single-{index:04d}",
                home_score=3,
                away_score=1,
                source_record_id=f"mlb-single-{index:04d}",
                source_version_id="live-v1",
                game_number=None,
            )
        )
    first_doubleheader = date(2025, 11, 1)
    for index in range(28):
        game_date = first_doubleheader + timedelta(days=index)
        for ordinal, hour in ((1, 13), (2, 19)):
            games.append(
                AspGameEvidence(
                    game_id=f"asp-double-{index:02d}-{ordinal}",
                    official_date=game_date,
                    scheduled_at=datetime(
                        game_date.year,
                        game_date.month,
                        game_date.day,
                        hour,
                        tzinfo=UTC,
                    ),
                    home_team_id=f"home-double-{index:02d}",
                    away_team_id=f"away-double-{index:02d}",
                    home_score=ordinal + 2,
                    away_score=ordinal,
                    source_record_id=f"mlb-double-{index:02d}-{ordinal}",
                    source_version_id="live-v1",
                    game_number=None,
                )
            )
    return tuple(games)


def test_operator_schema_matches_readiness_closeout_contract() -> None:
    operator = _load_operator()

    assert operator.SCHEMA_VERSION == OPERATOR_SCHEMA_VERSION


def test_content_addressed_operator_artifacts_reuse_without_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operator = _load_operator()
    runtime_root = tmp_path / "runtime"
    artifact_root = runtime_root / "artifacts"
    artifact_root.mkdir(parents=True)
    monkeypatch.setattr(operator, "RUNTIME_ROOT", runtime_root)

    pending, count, digest, byte_count = operator._stage_jsonl(
        artifact_root / "staging.jsonl", ({"game": 1},)
    )
    assert count == 1
    final = operator._content_addressed_artifact_path(
        mode="dry-run",
        artifact_name="2025-retrosheet-source-evidence",
        content_sha256=digest,
        suffix="jsonl",
    )
    assert (
        operator._publish_content_addressed_artifact(
            pending,
            final,
            expected_sha256=digest,
            expected_bytes=byte_count,
        )
        is False
    )
    original = final.read_bytes()

    repeated, _, repeated_digest, repeated_bytes = operator._stage_jsonl(
        artifact_root / "staging.jsonl", ({"game": 1},)
    )
    assert repeated_digest == digest
    assert (
        operator._publish_content_addressed_artifact(
            repeated,
            final,
            expected_sha256=repeated_digest,
            expected_bytes=repeated_bytes,
        )
        is True
    )
    assert final.read_bytes() == original

    conflicting, _, conflicting_digest, conflicting_bytes = operator._stage_jsonl(
        artifact_root / "staging.jsonl", ({"game": 2},)
    )
    with pytest.raises(operator.OperatorError, match="checksum changed"):
        operator._publish_content_addressed_artifact(
            conflicting,
            final,
            expected_sha256=conflicting_digest,
            expected_bytes=conflicting_bytes,
        )
    assert final.read_bytes() == original
    conflicting.unlink()


def test_operator_finalizes_required_artifacts_before_transaction_resolution() -> None:
    operator = _load_operator()
    operator_revision = operator._operator_code_revision()
    assert len(operator_revision) == 64
    assert operator_revision != operator.build_fd1_code_revision()
    source = inspect.getsource(operator.run)
    first_finalization = source.index("_publish_content_addressed_artifact(")
    last_finalization = source.rindex("_publish_content_addressed_artifact(")
    assert source.count("_publish_content_addressed_artifact(") == 4
    assert last_finalization < source.index("session.commit()")
    assert last_finalization < source.index("session.rollback()", first_finalization)
    assert ".replace(" not in source
    assert '"retrosheet_source_evidence"' in source
    assert '"schedule_fingerprint_bridge"' in source
    assert '"asp_retrosheet_full_reconciliation"' in source
    assert "_build_full_2025_reconciliation(" in source
    assert source.index("_load_asp_game_number_preflight(") < source.index(
        "_prepare_inputs("
    )
    assert source.index("_load_asp_game_number_preflight(") < source.index(
        "_retrosheet_batches("
    )
    closeout_source = inspect.getsource(operator._closeout_document)
    assert '"asp_game_number_preflight": dict(asp_game_number_preflight)' in (
        closeout_source
    )


def test_operator_game_number_preflight_loads_and_normalizes_before_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operator = _load_operator()
    monkeypatch.setattr(operator, "TARGET_GAME_COUNT", 2)
    asp_games = (SimpleNamespace(game_id="asp-1"), SimpleNamespace(game_id="asp-2"))
    normalization = SimpleNamespace(game_count=2)
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        operator,
        "load_asp_2025_live_evidence",
        lambda session: calls.append(("load", session)) or asp_games,
    )
    monkeypatch.setattr(
        operator,
        "normalize_asp_schedule_game_numbers",
        lambda games: calls.append(("normalize", games)) or normalization,
    )
    session = SimpleNamespace(no_autoflush=nullcontext())

    actual_games, actual_normalization = operator._load_asp_game_number_preflight(
        session
    )

    assert actual_games is asp_games
    assert actual_normalization is normalization
    assert calls == [("load", session), ("normalize", asp_games)]


def test_operator_preflight_accepts_exact_live_shaped_all_missing_cohort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operator = _load_operator()
    asp_games = _live_shaped_missing_number_cohort()
    monkeypatch.setattr(
        operator,
        "load_asp_2025_live_evidence",
        lambda session: asp_games,
    )
    session = SimpleNamespace(no_autoflush=nullcontext())

    loaded, normalization = operator._load_asp_game_number_preflight(session)

    assert loaded is asp_games
    assert normalization.method_counts == {
        ASP_GAME_NUMBER_DERIVED_SINGLETON: 2_374,
        ASP_GAME_NUMBER_DERIVED_START_TIME: 56,
    }
    assert normalization.as_dict()["source_field_persistence"] == {
        "status": "UNAVAILABLE_FOR_ALL_GAMES",
        "persistence_gap": True,
        "source_evidence_preserved": True,
    }
    assert normalization.as_dict()["source_missing_derived_count"] == 2_430
    assert all(game.game_number is None for game in loaded)


def test_operator_builds_complete_name_free_reconciliation_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operator = _load_operator()
    monkeypatch.setattr(operator, "TARGET_GAME_COUNT", 2)
    asp_games = (SimpleNamespace(game_id="asp-1"), SimpleNamespace(game_id="asp-2"))
    retro_games = (
        SimpleNamespace(raw_game_id="retro-1"),
        SimpleNamespace(raw_game_id="retro-2"),
    )
    crosswalk = object()
    normalization = object()
    bridge = SimpleNamespace(
        crosswalk=crosswalk,
        game_ids=MappingProxyType({"retro-1": "asp-1", "retro-2": "asp-2"}),
        score_conflict_game_count=1,
    )
    reconciliation = SimpleNamespace(
        complete_target_mapping=True,
        matched_game_count=2,
        games=(
            SimpleNamespace(asp=asp_games[0], retrosheet=retro_games[0]),
            SimpleNamespace(asp=asp_games[1], retrosheet=retro_games[1]),
        ),
        family_counts=MappingProxyType(
            {"final_score": MappingProxyType({"CONFLICT": 1})}
        ),
    )
    calls: list[tuple[object, ...]] = []

    def build(asp, retro, **kwargs):
        calls.append(("bridge", asp, retro, kwargs))
        return bridge

    def reconcile(asp, retro, selected_crosswalk, **kwargs):
        calls.append(("reconcile", asp, retro, selected_crosswalk, kwargs))
        return reconciliation

    monkeypatch.setattr(operator, "build_2025_schedule_fingerprint_crosswalk", build)
    monkeypatch.setattr(operator, "reconcile_2025_retrosheet_to_asp", reconcile)

    actual_bridge, actual_reconciliation = operator._build_full_2025_reconciliation(
        retro_games,
        asp_games=asp_games,
        asp_game_number_normalization=normalization,
    )

    assert actual_bridge is bridge
    assert actual_reconciliation is reconciliation
    assert calls == [
        (
            "bridge",
            asp_games,
            retro_games,
            {
                "asp_game_number_normalization": normalization,
                "expected_target_games": 2,
            },
        ),
        (
            "reconcile",
            asp_games,
            retro_games,
            crosswalk,
            {"expected_target_games": 2},
        ),
    ]


def test_operator_rejects_incomplete_full_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operator = _load_operator()
    monkeypatch.setattr(operator, "TARGET_GAME_COUNT", 1)
    asp_games = (SimpleNamespace(game_id="asp-1"),)
    normalization = object()
    retro_games = (SimpleNamespace(raw_game_id="retro-1"),)
    bridge = SimpleNamespace(crosswalk=object())
    incomplete = SimpleNamespace(
        complete_target_mapping=False,
        matched_game_count=0,
    )
    monkeypatch.setattr(
        operator,
        "build_2025_schedule_fingerprint_crosswalk",
        lambda *args, **kwargs: bridge,
    )
    monkeypatch.setattr(
        operator,
        "reconcile_2025_retrosheet_to_asp",
        lambda *args, **kwargs: incomplete,
    )

    with pytest.raises(operator.OperatorError, match="complete target"):
        operator._build_full_2025_reconciliation(
            retro_games,
            asp_games=asp_games,
            asp_game_number_normalization=normalization,
        )


def test_operator_preserves_v5_counters_and_partition_quality() -> None:
    operator = _load_operator()
    counters = MaterializationCounters(
        scanned_rows=MappingProxyType({"gameinfo.csv": 9}),
        selected_rows=MappingProxyType({"gameinfo.csv": 3}),
        exclusion_counts=MappingProxyType({"postseason": 1}),
        selected_games=3,
        primary_games=3,
        prior_only_games=0,
        full_pbp_games=2,
        deduced_pbp_games=1,
        no_pbp_games=0,
        completed_suspended_games=1,
        relocated_suspended_games=0,
        suspended_original_segment_plays=12,
        suspended_completion_segment_plays=2,
        automatic_runner_placements=4,
        automatic_runner_direct_placements=3,
        automatic_runner_np_bridge_placements=1,
        lineup_position_reconciliations=5,
        lineup_nonstarting_role_code_occurrences=6,
        stattype_rows=MappingProxyType({"value": 7}),
        family_rows=MappingProxyType({"weather": 3}),
        reconciliation_checks=MappingProxyType({"runner_conservation": True}),
    )
    quality = {
        "automatic_runner_placement_rows": 4,
        "lineup_position_reconciliation_rows": 5,
        "lineup_nonstarting_role_code_occurrences": 6,
    }
    result = SimpleNamespace(
        window=SimpleNamespace(
            start_date=operator.date(2025, 6, 1),
            end_date=operator.date(2025, 6, 30),
        ),
        counters=counters,
        selected_bundle_checksum="a" * 64,
        build_checksum="b" * 64,
        batches=(
            SimpleNamespace(
                family="weather",
                partition_checksum="c" * 64,
                quality_report=MappingProxyType(quality),
                rows=(
                    {"weather_coverage": "COMPLETE_RETROSHEET"},
                    {"weather_coverage": "COMPLETE_RETROSHEET"},
                    {"weather_coverage": "PARTIAL_RETROSHEET_NOAA_GAP_REQUIRED"},
                ),
            ),
        ),
    )
    ledger = operator.MaterializationLedger()

    operator._track_materialization_result(result, ledger)

    monthly = ledger.monthly[0]
    assert monthly["counters"] == counters.as_dict()
    assert monthly["partition_quality_reports"]["weather"] == quality
    season = ledger.seasons[2025]
    assert season["scanned_source_rows"] == {"gameinfo.csv": 9}
    assert season["automatic_runner_placements"] == 4
    assert season["automatic_runner_direct_placements"] == 3
    assert season["automatic_runner_np_bridge_placements"] == 1
    assert season["lineup_position_reconciliations"] == 5
    assert season["lineup_nonstarting_role_code_occurrences"] == 6
    assert season["weather_coverage_counts"] == {
        "COMPLETE_RETROSHEET": 2,
        "PARTIAL_RETROSHEET_NOAA_GAP_REQUIRED": 1,
    }
    assert season["reconciliation_checks"] == {"runner_conservation": True}

from __future__ import annotations

import base64
import csv
import gzip
import hashlib
import json
from datetime import UTC, date, datetime
from io import StringIO
from pathlib import Path
from typing import Any

import pytest

from app.data_sources.statcast import (
    STATCAST_DETAIL_ENDPOINT,
    STATCAST_DETAIL_SCHEMA_VERSION,
    statcast_detail_query,
)
from app.historical.statcast_promotion import (
    EVIDENCE_CLASSIFICATION,
    FORBIDDEN_FEATURE_FIELDS,
    QUALITY_TIER,
    StatcastGameMapping,
    StatcastPlayerMapping,
    StatcastPromotionError,
    StatcastPromotionOptions,
    assert_historical_cutoff_eligible,
    build_research_aggregates,
    inspect_statcast_archive,
    promote_statcast_archive,
    validate_research_feature_payload,
)


TARGET_DATE = date(2025, 6, 17)
INGESTED_AT = datetime(2026, 8, 14, 1, 34, 19, 983534, tzinfo=UTC)
SOURCE_VERSION = "baseball-savant-statcast-detail-2025-a2-v1"
FIELDNAMES = (
    "game_date",
    "game_pk",
    "at_bat_number",
    "pitch_number",
    "batter",
    "pitcher",
    "home_team",
    "away_team",
    "inning",
    "inning_topbot",
    "outs_when_up",
    "balls",
    "strikes",
    "stand",
    "p_throws",
    "pitch_type",
    "pitch_name",
    "description",
    "type",
    "events",
    "zone",
    "release_speed",
    "effective_speed",
    "release_spin_rate",
    "spin_axis",
    "plate_x",
    "plate_z",
    "pfx_x",
    "pfx_z",
    "release_extension",
    "launch_speed",
    "launch_angle",
    "hit_distance_sc",
    "bb_type",
    "home_score",
    "post_home_score",
    "home_win_exp",
    "delta_run_exp",
    "pitcher_days_until_next_game",
    "batter_days_until_next_game",
)


def _row(**updates: str) -> dict[str, str]:
    value = {
        "game_date": TARGET_DATE.isoformat(),
        "game_pk": "777001",
        "at_bat_number": "1",
        "pitch_number": "1",
        "batter": "101",
        "pitcher": "201",
        "home_team": "NYY",
        "away_team": "BOS",
        "inning": "1",
        "inning_topbot": "Top",
        "outs_when_up": "0",
        "balls": "0",
        "strikes": "0",
        "stand": "R",
        "p_throws": "L",
        "pitch_type": "FF",
        "pitch_name": "4-Seam Fastball",
        "description": "swinging_strike",
        "type": "S",
        "events": "",
        "zone": "5",
        "release_speed": "95.0",
        "effective_speed": "94.2",
        "release_spin_rate": "2300",
        "spin_axis": "210",
        "plate_x": "0.1",
        "plate_z": "2.5",
        "pfx_x": "-0.5",
        "pfx_z": "1.4",
        "release_extension": "6.2",
        "launch_speed": "",
        "launch_angle": "",
        "hit_distance_sc": "",
        "bb_type": "",
        # These remain in the exact raw archive but must never enter research_features.
        "home_score": "0",
        "post_home_score": "0",
        "home_win_exp": "0.54",
        "delta_run_exp": "-0.04",
        "pitcher_days_until_next_game": "5",
        "batter_days_until_next_game": "1",
    }
    value.update(updates)
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _request_fingerprint(target_date: date) -> str:
    return _sha256_json({
        "endpoint": STATCAST_DETAIL_ENDPOINT,
        "query": statcast_detail_query(target_date),
        "scope": "RAW_ARCHIVE_ONLY",
        "schema_version": STATCAST_DETAIL_SCHEMA_VERSION,
    })


def _write_archive(
    root: Path,
    rows: list[dict[str, str]],
    *,
    target_date: date = TARGET_DATE,
) -> Path:
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=FIELDNAMES, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    body = output.getvalue()
    body_bytes = body.encode("utf-8")
    payload = {
        "format": "CSV",
        "content_type": "text/csv; charset=utf-8",
        "requested_date": target_date.isoformat(),
        "query": statcast_detail_query(target_date),
        "body": body,
        "body_base64": base64.b64encode(body_bytes).decode("ascii"),
        "body_encoding": "BASE64_EXACT_RESPONSE_BYTES",
    }
    document = {
        "metadata": {
            "source": "BASEBALL_SAVANT_STATCAST",
            "ingestion_timestamp": INGESTED_AT.isoformat(),
            "source_event_timestamp": None,
            "checksum": _sha256_json(payload),
            "status": "success",
            "record_count": 1,
            "schema_version": STATCAST_DETAIL_SCHEMA_VERSION,
            "historical": True,
            "request_fingerprint": _request_fingerprint(target_date),
            "source_mode": "LIVE",
            "research_provenance": "HISTORICAL_RECONSTRUCTION",
            "temporal_semantics": "RETROSPECTIVE_RECONSTRUCTION",
            "evidence_class": "PARTIALLY_RECONSTRUCTABLE",
            "checkpoint_id": f"statcast-one-day-trial:{target_date.isoformat()}",
            "run_id": None,
        },
        "payload": payload,
    }
    path = root / f"{target_date.isoformat()}_{_request_fingerprint(target_date)[:24]}.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as stream:
            stream.write(_canonical_json(document).encode("utf-8"))
    return path


def _game_mappings() -> list[StatcastGameMapping]:
    return [
        StatcastGameMapping(
            source_game_id="777001",
            game_id="game-777001",
            official_date=TARGET_DATE,
            home_team_code="NYY",
            away_team_code="BOS",
            final_at=datetime(2025, 6, 18, 2, 45, tzinfo=UTC),
        )
    ]


def _player_mappings() -> list[StatcastPlayerMapping]:
    return [
        StatcastPlayerMapping(source_player_id="101", player_id="player-101"),
        StatcastPlayerMapping(source_player_id="102", player_id="player-102"),
        StatcastPlayerMapping(source_player_id="201", player_id="player-201"),
    ]


def _promote(tmp_path: Path, rows: list[dict[str, str]]):
    archive = _write_archive(tmp_path / "raw", rows)
    return promote_statcast_archive(
        StatcastPromotionOptions(
            archive_path=archive,
            artifact_root=tmp_path / "artifacts",
            target_date=TARGET_DATE,
            source_version=SOURCE_VERSION,
        ),
        game_mappings=_game_mappings(),
        player_mappings=_player_mappings(),
    )


def test_existing_ignored_one_day_artifact_revalidates_when_present() -> None:
    repository = Path(__file__).resolve().parents[2]
    archive = repository / (
        "data/raw/historical/baseball_savant_statcast/2025/06/"
        "2025-06-17_9400cceb7314b4d99cbe37b8.json.gz"
    )
    if not archive.exists():
        pytest.skip("ignored one-day Statcast evidence is intentionally not in Git")

    inspected = inspect_statcast_archive(archive, target_date=TARGET_DATE)

    assert inspected.archive_bytes == 2_523_608
    assert inspected.archive_file_checksum == (
        "601af8d47af4dfe66102efd4df38ecd324e8ebf38449ddc8eb0c708337e79f2d"
    )
    assert inspected.payload_checksum == (
        "bcaa171d4adfb8666ab3ee85eb0490710103825ecf973f8b07a80e55c53a4251"
    )
    assert inspected.request_fingerprint == (
        "9400cceb7314b4d99cbe37b892dfc4fb4f6e20e7a65eccb6c93f20010121f6f1"
    )
    assert inspected.ingestion_timestamp == INGESTED_AT
    assert inspected.source_event_timestamp is None
    assert len(inspected.rows) == 4_375
    assert len({row["game_pk"] for row in inspected.rows}) == 15


def test_promotion_is_tier_b_compact_deterministic_and_idempotent(
    artifact_root: Path,
) -> None:
    rows = [
        _row(),
        _row(
            pitch_number="2",
            balls="0",
            strikes="1",
            description="hit_into_play",
            type="X",
            events="single",
            release_speed="96.0",
            launch_speed="101.2",
            launch_angle="12",
            hit_distance_sc="280",
            bb_type="line_drive",
        ),
    ]
    archive = _write_archive(artifact_root / "raw", rows)
    options = StatcastPromotionOptions(
        archive_path=archive,
        artifact_root=artifact_root / "artifacts",
        target_date=TARGET_DATE,
        source_version=SOURCE_VERSION,
    )

    first = promote_statcast_archive(
        options,
        game_mappings=_game_mappings(),
        player_mappings=_player_mappings(),
    )
    second = promote_statcast_archive(
        options,
        game_mappings=_game_mappings(),
        player_mappings=_player_mappings(),
    )

    assert first.idempotent_reuse is False
    assert second.idempotent_reuse is True
    assert first.artifact_id == second.artifact_id
    assert first.manifest == second.manifest
    assert {path.name for path in first.artifact_path.iterdir()} == {
        "events.jsonl",
        "aggregates.jsonl",
        "manifest.json",
    }
    assert first.manifest["counts"] == {
        "records_received": 2,
        "records_accepted": 2,
        "records_rejected": 0,
        "duplicate_count": 0,
        "canonical_game_count": 1,
        "canonical_player_count": 2,
        "research_aggregate_count": 6,
    }
    for event in first.events:
        assert event["evidence_classification"] == EVIDENCE_CLASSIFICATION
        assert event["quality_tier"] == QUALITY_TIER
        assert event["training_eligible"] is event["evaluation_eligible"] is False
        assert event["available_at"] == INGESTED_AT.isoformat()
        assert event["source_published_at"] is None
        assert not FORBIDDEN_FEATURE_FIELDS.intersection(event["research_features"])
    for name in ("events.jsonl", "aggregates.jsonl"):
        body = (first.artifact_path / name).read_bytes()
        assert hashlib.sha256(body).hexdigest() == first.manifest["files"][name]["sha256"]


def test_current_retrieval_cannot_be_tier_a_or_enter_2025_cutoff(
    artifact_root: Path,
) -> None:
    result = _promote(artifact_root, [_row()])
    event = result.events[0]

    assert result.manifest["historical_availability_proven"] is False
    assert result.manifest["cutoff_policy"]["historical_2025_t_minus_6h_eligible"] is False
    assert all(item["historical_2025_t_minus_6h_eligible"] is False for item in result.aggregates)
    with pytest.raises(StatcastPromotionError, match="retrieved after historical cutoff"):
        assert_historical_cutoff_eligible(
            event,
            cutoff=datetime(2025, 6, 17, 17, 0, tzinfo=UTC),
        )


def test_target_game_date_and_unavailable_cutoff_leakage_fail_closed(
    artifact_root: Path,
) -> None:
    result = _promote(artifact_root, [_row()])

    with pytest.raises(StatcastPromotionError, match="Target-date or future"):
        build_research_aggregates(result.events, target_official_date=TARGET_DATE)
    with pytest.raises(StatcastPromotionError, match="Target-game"):
        build_research_aggregates(
            result.events,
            target_official_date=date(2025, 6, 18),
            target_game_ids={"game-777001"},
        )
    with pytest.raises(StatcastPromotionError, match="unavailable at historical cutoff"):
        build_research_aggregates(
            result.events,
            target_official_date=date(2025, 6, 18),
            historical_cutoff=datetime(2025, 6, 18, 12, 0, tzinfo=UTC),
        )
    late_final = dict(result.events[0])
    late_final["source_game_final_at"] = datetime(
        2025, 6, 18, 13, 0, tzinfo=UTC
    ).isoformat()
    late_final["available_at"] = datetime(
        2025, 6, 18, 11, 0, tzinfo=UTC
    ).isoformat()
    with pytest.raises(StatcastPromotionError, match="not final before cutoff"):
        build_research_aggregates(
            [late_final],
            target_official_date=date(2025, 6, 18),
            historical_cutoff=datetime(2025, 6, 18, 12, 0, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    ("games", "players", "message"),
    [
        ([], _player_mappings(), "unmapped identities"),
        (_game_mappings(), [], "unmapped identities"),
        (_game_mappings() * 2, _player_mappings(), "Ambiguous Statcast game mapping"),
        (_game_mappings(), _player_mappings() * 2, "Ambiguous Statcast player mapping"),
    ],
)
def test_mapping_must_be_exactly_one(
    artifact_root: Path,
    games: list[StatcastGameMapping],
    players: list[StatcastPlayerMapping],
    message: str,
) -> None:
    archive = _write_archive(artifact_root / "raw", [_row()])
    with pytest.raises(StatcastPromotionError, match=message):
        promote_statcast_archive(
            StatcastPromotionOptions(
                archive_path=archive,
                artifact_root=artifact_root / "artifacts",
                target_date=TARGET_DATE,
                source_version=SOURCE_VERSION,
            ),
            game_mappings=games,
            player_mappings=players,
        )


def test_team_identity_and_off_date_rows_fail_closed(artifact_root: Path) -> None:
    wrong_team = _game_mappings()[0]
    wrong_team = StatcastGameMapping(
        **{**wrong_team.__dict__, "home_team_code": "LAD"}
    )
    archive = _write_archive(artifact_root / "team", [_row()])
    with pytest.raises(StatcastPromotionError, match="home-team identity mismatch"):
        promote_statcast_archive(
            StatcastPromotionOptions(
                archive_path=archive,
                artifact_root=artifact_root / "artifacts-team",
                target_date=TARGET_DATE,
                source_version=SOURCE_VERSION,
            ),
            game_mappings=[wrong_team],
            player_mappings=_player_mappings(),
        )

    off_date = _write_archive(
        artifact_root / "date",
        [_row(game_date="2025-06-18")],
    )
    with pytest.raises(StatcastPromotionError, match="outside the exact requested date"):
        inspect_statcast_archive(off_date, target_date=TARGET_DATE)


def test_identical_duplicate_is_idempotent_but_conflict_fails(
    artifact_root: Path,
) -> None:
    identical = _row()
    result = _promote(artifact_root / "same", [identical, dict(identical)])
    assert result.manifest["counts"]["records_received"] == 2
    assert result.manifest["counts"]["records_accepted"] == 1
    assert result.manifest["counts"]["duplicate_count"] == 1

    conflict_archive = _write_archive(
        artifact_root / "conflict" / "raw",
        [_row(), _row(release_speed="96.1")],
    )
    with pytest.raises(StatcastPromotionError, match="Conflicting Statcast rows"):
        promote_statcast_archive(
            StatcastPromotionOptions(
                archive_path=conflict_archive,
                artifact_root=artifact_root / "conflict" / "artifacts",
                target_date=TARGET_DATE,
                source_version=SOURCE_VERSION,
            ),
            game_mappings=_game_mappings(),
            player_mappings=_player_mappings(),
        )


def test_event_and_aggregate_checksums_are_input_order_independent(
    artifact_root: Path,
) -> None:
    rows = [
        _row(),
        _row(
            pitch_number="2",
            strikes="1",
            description="hit_into_play",
            type="X",
            events="single",
            launch_speed="100",
            launch_angle="10",
        ),
    ]
    forward = _promote(artifact_root / "forward", rows)
    reverse = _promote(artifact_root / "reverse", list(reversed(rows)))

    assert [item["event_checksum"] for item in forward.events] == [
        item["event_checksum"] for item in reverse.events
    ]
    assert [item["aggregate_checksum"] for item in forward.aggregates] == [
        item["aggregate_checksum"] for item in reverse.aggregates
    ]


def test_aggregate_denominators_are_explicit_and_missing_is_not_zero(
    artifact_root: Path,
) -> None:
    result = _promote(
        artifact_root,
        [
            _row(),
            _row(
                pitch_number="2",
                strikes="1",
                release_speed="96.0",
                description="hit_into_play",
                type="X",
                events="single",
                launch_speed="100.0",
                launch_angle="10.0",
            ),
        ],
    )
    batter = next(
        item
        for item in result.aggregates
        if item["window"] == "ROLLING_14_DAY"
        and item["role"] == "BATTER"
        and item["player_id"] == "player-101"
    )

    assert batter["denominators"] == {
        "pitch_count": 2,
        "swing_count": 2,
        "terminal_plate_appearance_count": 1,
        "release_speed_count": 2,
        "batted_ball_speed_count": 1,
        "batted_ball_angle_count": 1,
    }
    assert batter["values"]["swing_rate"] == 1.0
    assert batter["values"]["whiff_rate"] == 0.5
    assert batter["values"]["strikeout_rate"] == 0.0
    assert batter["values"]["walk_rate"] == 0.0
    assert batter["values"]["average_release_speed"] == 95.5
    assert batter["values"]["average_launch_speed"] == 100.0
    pitcher = next(
        item
        for item in result.aggregates
        if item["window"] == "ROLLING_14_DAY"
        and item["role"] == "PITCHER"
    )
    assert pitcher["denominators"]["pitch_count"] == 2


def test_forbidden_or_unknown_feature_payload_is_rejected() -> None:
    with pytest.raises(StatcastPromotionError, match="unsafe"):
        validate_research_feature_payload({"home_score": 5})
    with pytest.raises(StatcastPromotionError, match="unsafe"):
        validate_research_feature_payload({"future_magic_metric": 1})


def test_checksum_and_size_caps_fail_before_artifact_write(artifact_root: Path) -> None:
    archive = _write_archive(artifact_root / "raw", [_row()])
    archive_bytes = bytearray(archive.read_bytes())
    archive_bytes[-1] ^= 1
    archive.write_bytes(archive_bytes)
    with pytest.raises(StatcastPromotionError, match="gzip integrity"):
        inspect_statcast_archive(archive, target_date=TARGET_DATE)

    valid = _write_archive(artifact_root / "valid", [_row()])
    with pytest.raises(StatcastPromotionError, match="compressed-byte cap"):
        inspect_statcast_archive(
            valid,
            target_date=TARGET_DATE,
            maximum_archive_bytes=valid.stat().st_size - 1,
        )

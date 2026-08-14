"""Provider-free Tier-B promotion for an existing Statcast raw archive.

The boundary in this module is intentionally file-to-file.  It never constructs
an adapter, opens a provider connection, writes canonical ORM rows, or exposes
the promoted evidence to training.  Historical publication time is unknown, so
the durable archive-ingestion time is also the earliest allowed availability
time and every output remains research-only ``PARTIALLY_RECONSTRUCTABLE`` data.
"""

from __future__ import annotations

import base64
import binascii
import gzip
import hashlib
import json
import math
import os
import shutil
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.data_sources.base import SourceError
from app.data_sources.statcast import (
    STATCAST_DETAIL_ENDPOINT,
    STATCAST_DETAIL_SCHEMA_VERSION,
    parse_statcast_detail_csv,
    statcast_detail_query,
)


SOURCE = "BASEBALL_SAVANT_STATCAST"
SOURCE_MODE = "LIVE"
RESEARCH_PROVENANCE = "HISTORICAL_RECONSTRUCTION"
TEMPORAL_SEMANTICS = "RETROSPECTIVE_RECONSTRUCTION"
EVIDENCE_CLASSIFICATION = "PARTIALLY_RECONSTRUCTABLE"
QUALITY_TIER = "B"
PROMOTION_CONTRACT_VERSION = "gate3a_a2_statcast_tier_b_v1"
EVENT_SCHEMA_VERSION = "gate3a_a2_statcast_event_minimal_v1"
AGGREGATE_SCHEMA_VERSION = "gate3a_a2_statcast_research_aggregate_v1"
AVAILABILITY_BASIS = "CURRENT_ARCHIVE_INGESTION_NO_HISTORICAL_PUBLICATION_TIME"

DEFAULT_MAX_ARCHIVE_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_UNCOMPRESSED_BYTES = 32 * 1024 * 1024
DEFAULT_MAX_CSV_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_ROWS = 5_000
DEFAULT_MAX_ARTIFACT_BYTES = 32 * 1024 * 1024

REQUIRED_PROMOTION_FIELDS = frozenset({
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
    "description",
    "type",
})

CONTEXT_FIELDS = (
    "inning",
    "inning_topbot",
    "outs_when_up",
    "balls",
    "strikes",
    "stand",
    "p_throws",
)
OUTCOME_FIELDS = (
    "pitch_type",
    "pitch_name",
    "description",
    "type",
    "events",
    "zone",
)
MEASUREMENT_FIELDS = (
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
)
ALLOWED_RESEARCH_FEATURE_FIELDS = frozenset(
    {*CONTEXT_FIELDS, *OUTCOME_FIELDS, *MEASUREMENT_FIELDS}
)
FORBIDDEN_FEATURE_FIELDS = frozenset({
    "home_score",
    "away_score",
    "bat_score",
    "fld_score",
    "post_home_score",
    "post_away_score",
    "post_bat_score",
    "post_fld_score",
    "home_score_diff",
    "bat_score_diff",
    "home_win_exp",
    "bat_win_exp",
    "delta_home_win_exp",
    "delta_run_exp",
    "delta_pitcher_run_exp",
    "pitcher_days_until_next_game",
    "batter_days_until_next_game",
})

_SWING_DESCRIPTIONS = frozenset({
    "swinging_strike",
    "swinging_strike_blocked",
    "foul",
    "foul_bunt",
    "foul_tip",
    "hit_into_play",
    "hit_into_play_no_out",
    "hit_into_play_score",
    "missed_bunt",
})
_WHIFF_DESCRIPTIONS = frozenset({
    "swinging_strike",
    "swinging_strike_blocked",
    "missed_bunt",
})
_STRIKEOUT_EVENTS = frozenset({"strikeout", "strikeout_double_play"})
_WALK_EVENTS = frozenset({"walk", "intent_walk"})


class StatcastPromotionError(RuntimeError):
    """Fail-closed contract violation during provider-free promotion."""


@dataclass(frozen=True)
class StatcastGameMapping:
    source_game_id: str
    game_id: str
    official_date: date
    home_team_code: str
    away_team_code: str
    final_at: datetime


@dataclass(frozen=True)
class StatcastPlayerMapping:
    source_player_id: str
    player_id: str


@dataclass(frozen=True)
class StatcastPromotionOptions:
    archive_path: Path
    artifact_root: Path
    target_date: date
    source_version: str
    aggregate_as_of_date: date | None = None
    maximum_archive_bytes: int = DEFAULT_MAX_ARCHIVE_BYTES
    maximum_uncompressed_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES
    maximum_csv_bytes: int = DEFAULT_MAX_CSV_BYTES
    maximum_rows: int = DEFAULT_MAX_ROWS
    maximum_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES

    def __post_init__(self) -> None:
        if self.target_date.year < 2015:
            raise ValueError("Statcast promotion target date must be in 2015 or later")
        if not self.source_version.strip():
            raise ValueError("Statcast promotion requires an immutable source version")
        for name in (
            "maximum_archive_bytes",
            "maximum_uncompressed_bytes",
            "maximum_csv_bytes",
            "maximum_rows",
            "maximum_artifact_bytes",
        ):
            if int(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.aggregate_as_of_date is not None and self.aggregate_as_of_date <= self.target_date:
            raise ValueError("Research aggregate as-of date must follow the promoted date")


@dataclass(frozen=True)
class ValidatedStatcastArchive:
    archive_path: Path
    archive_bytes: int
    archive_file_checksum: str
    uncompressed_bytes: int
    payload_checksum: str
    csv_body_bytes: int
    csv_body_checksum: str
    schema_version: str
    schema_fingerprint: str
    observed_fields: tuple[str, ...]
    request_fingerprint: str
    ingestion_timestamp: datetime
    source_event_timestamp: None
    requested_date: date
    query: dict[str, str]
    rows: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class StatcastPromotionResult:
    artifact_id: str
    artifact_path: Path
    manifest: dict[str, Any]
    events: tuple[dict[str, Any], ...]
    aggregates: tuple[dict[str, Any], ...]
    idempotent_reuse: bool


def inspect_statcast_archive(
    archive_path: Path,
    *,
    target_date: date,
    maximum_archive_bytes: int = DEFAULT_MAX_ARCHIVE_BYTES,
    maximum_uncompressed_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES,
    maximum_csv_bytes: int = DEFAULT_MAX_CSV_BYTES,
    maximum_rows: int = DEFAULT_MAX_ROWS,
) -> ValidatedStatcastArchive:
    """Read and fully revalidate one immutable RawArchive gzip wrapper."""

    path = Path(archive_path)
    if not path.is_file() or path.suffixes[-2:] != [".json", ".gz"]:
        raise StatcastPromotionError("Statcast promotion requires an existing .json.gz archive")
    archive_bytes = path.stat().st_size
    if archive_bytes <= 0 or archive_bytes > maximum_archive_bytes:
        raise StatcastPromotionError("Statcast archive exceeds the declared compressed-byte cap")
    archive_file_checksum = _file_sha256(path)
    encoded = _read_gzip_bounded(path, maximum_uncompressed_bytes)
    try:
        document = json.loads(encoded.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise StatcastPromotionError(
            "Statcast archive is not strict unique-key UTF-8 JSON"
        ) from exc
    if not isinstance(document, dict) or set(document) != {"metadata", "payload"}:
        raise StatcastPromotionError("Statcast archive wrapper shape is invalid")
    metadata = document["metadata"]
    payload = document["payload"]
    if not isinstance(metadata, dict) or not isinstance(payload, dict):
        raise StatcastPromotionError("Statcast archive metadata/payload must be objects")

    expected_metadata: dict[str, Any] = {
        "source": SOURCE,
        "status": "success",
        "record_count": 1,
        "schema_version": STATCAST_DETAIL_SCHEMA_VERSION,
        "historical": True,
        "source_mode": SOURCE_MODE,
        "research_provenance": RESEARCH_PROVENANCE,
        "temporal_semantics": TEMPORAL_SEMANTICS,
        "evidence_class": EVIDENCE_CLASSIFICATION,
        "source_event_timestamp": None,
    }
    mismatches = sorted(
        key for key, value in expected_metadata.items() if metadata.get(key) != value
    )
    if mismatches:
        raise StatcastPromotionError(f"Statcast archive metadata mismatch: {mismatches}")
    ingestion_timestamp = _aware_datetime(
        metadata.get("ingestion_timestamp"), "archive ingestion_timestamp"
    )
    if ingestion_timestamp.date() <= target_date:
        raise StatcastPromotionError("Historical Statcast availability must not be backdated")

    # RawArchive uses the stdlib JSON defaults (notably ensure_ascii=True) for
    # this checksum.  Preserve that exact historical byte contract separately
    # from the UTF-8 semantic hashes used by the promoted artifact.
    payload_checksum = _raw_archive_payload_sha256(payload)
    if metadata.get("checksum") != payload_checksum:
        raise StatcastPromotionError("Statcast canonical payload checksum mismatch")
    expected_query = statcast_detail_query(target_date)
    expected_payload = {
        "format": "CSV",
        "requested_date": target_date.isoformat(),
        "query": expected_query,
        "body_encoding": "BASE64_EXACT_RESPONSE_BYTES",
    }
    payload_mismatches = sorted(
        key for key, value in expected_payload.items() if payload.get(key) != value
    )
    if payload_mismatches or not isinstance(payload.get("body"), str):
        raise StatcastPromotionError(
            f"Statcast raw request/body contract mismatch: {payload_mismatches}"
        )
    if set(payload) != {
        "format",
        "content_type",
        "requested_date",
        "query",
        "body",
        "body_base64",
        "body_encoding",
    }:
        raise StatcastPromotionError("Statcast raw payload contains an unexpected field set")
    encoded_body = payload.get("body_base64")
    if not isinstance(encoded_body, str) or len(encoded_body) > (maximum_csv_bytes * 4 // 3) + 8:
        raise StatcastPromotionError("Statcast exact response body exceeds the encoded-byte cap")
    try:
        body_bytes = base64.b64decode(encoded_body, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise StatcastPromotionError("Statcast exact response body is invalid base64") from exc
    if len(body_bytes) <= 0 or len(body_bytes) > maximum_csv_bytes:
        raise StatcastPromotionError("Statcast exact response body exceeds the CSV-byte cap")
    try:
        decoded_body = body_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise StatcastPromotionError("Statcast exact response body is not UTF-8") from exc
    if decoded_body != payload["body"]:
        raise StatcastPromotionError("Statcast decoded body does not match exact response bytes")
    content_type = payload.get("content_type")
    if not isinstance(content_type, str):
        raise StatcastPromotionError("Statcast content type is absent")
    try:
        rows, schema = parse_statcast_detail_csv(
            decoded_body,
            content_type=content_type,
            target_date=target_date,
        )
    except SourceError as exc:
        raise StatcastPromotionError(f"Statcast CSV contract failed: {exc}") from exc
    if len(rows) > maximum_rows:
        raise StatcastPromotionError("Statcast row count exceeds the declared cap")
    observed_fields = tuple(str(value) for value in schema["observed_fields"])
    missing = sorted(REQUIRED_PROMOTION_FIELDS.difference(observed_fields))
    if missing:
        raise StatcastPromotionError(f"Statcast promotion schema lacks fields: {missing}")
    request_fingerprint = _request_fingerprint(target_date)
    if metadata.get("request_fingerprint") != request_fingerprint:
        raise StatcastPromotionError("Statcast request fingerprint mismatch")
    expected_name = f"{target_date.isoformat()}_{request_fingerprint[:24]}.json.gz"
    if path.name != expected_name:
        raise StatcastPromotionError("Statcast archive semantic filename mismatch")
    return ValidatedStatcastArchive(
        archive_path=path,
        archive_bytes=archive_bytes,
        archive_file_checksum=archive_file_checksum,
        uncompressed_bytes=len(encoded),
        payload_checksum=payload_checksum,
        csv_body_bytes=len(body_bytes),
        csv_body_checksum=hashlib.sha256(body_bytes).hexdigest(),
        schema_version=str(schema["contract_version"]),
        schema_fingerprint=str(schema["schema_fingerprint"]),
        observed_fields=observed_fields,
        request_fingerprint=request_fingerprint,
        ingestion_timestamp=ingestion_timestamp,
        source_event_timestamp=None,
        requested_date=target_date,
        query=expected_query,
        rows=tuple(rows),
    )


def promote_statcast_archive(
    options: StatcastPromotionOptions,
    *,
    game_mappings: Iterable[StatcastGameMapping],
    player_mappings: Iterable[StatcastPlayerMapping],
) -> StatcastPromotionResult:
    """Promote an already archived date to deterministic Tier-B research artifacts."""

    archive = inspect_statcast_archive(
        options.archive_path,
        target_date=options.target_date,
        maximum_archive_bytes=options.maximum_archive_bytes,
        maximum_uncompressed_bytes=options.maximum_uncompressed_bytes,
        maximum_csv_bytes=options.maximum_csv_bytes,
        maximum_rows=options.maximum_rows,
    )
    games = _game_index(game_mappings)
    players = _player_index(player_mappings)
    accepted: dict[tuple[str, str, int, int], dict[str, Any]] = {}
    source_checksums: dict[tuple[str, str, int, int], str] = {}
    duplicate_count = 0
    mapped_game_ids: set[str] = set()
    mapped_player_ids: set[str] = set()
    mapped_external_players: set[str] = set()

    for row_number, row in enumerate(archive.rows, start=2):
        game_pk = _positive_external_id(row.get("game_pk"), "game_pk", row_number)
        batter_external = _positive_external_id(row.get("batter"), "batter", row_number)
        pitcher_external = _positive_external_id(row.get("pitcher"), "pitcher", row_number)
        game = games.get(game_pk)
        batter = players.get(batter_external)
        pitcher = players.get(pitcher_external)
        missing_mapping = [
            name
            for name, value in (
                (f"game:{game_pk}", game),
                (f"player:{batter_external}", batter),
                (f"player:{pitcher_external}", pitcher),
            )
            if value is None
        ]
        if missing_mapping:
            raise StatcastPromotionError(
                f"Statcast row {row_number} has unmapped identities: {missing_mapping}"
            )
        assert game is not None and batter is not None and pitcher is not None
        event = _canonical_event(
            row,
            row_number=row_number,
            source_version=options.source_version,
            target_date=options.target_date,
            available_at=archive.ingestion_timestamp,
            game=game,
            batter=batter,
            pitcher=pitcher,
            archive=archive,
        )
        key = _semantic_key_tuple(event)
        source_row_checksum = str(event["lineage"]["source_row_checksum"])
        prior = accepted.get(key)
        if prior is not None:
            if source_checksums[key] != source_row_checksum:
                raise StatcastPromotionError(
                    "Conflicting Statcast rows share one semantic pitch key"
                )
            duplicate_count += 1
            continue
        accepted[key] = event
        source_checksums[key] = source_row_checksum
        mapped_game_ids.add(game.game_id)
        mapped_player_ids.update((batter.player_id, pitcher.player_id))
        mapped_external_players.update((batter_external, pitcher_external))

    events = tuple(accepted[key] for key in sorted(accepted, key=_semantic_sort_key))
    if not events:
        raise StatcastPromotionError("Statcast promotion produced no canonical events")
    aggregate_as_of = options.aggregate_as_of_date or (options.target_date + timedelta(days=1))
    aggregates = build_research_aggregates(
        events,
        target_official_date=aggregate_as_of,
    )
    events_bytes = _jsonl_bytes(events)
    aggregates_bytes = _jsonl_bytes(aggregates)
    mapping_report = {
        "source_game_ids_observed": len({row["game_pk"].strip() for row in archive.rows}),
        "source_game_ids_mapped": len(mapped_game_ids),
        "source_player_ids_observed": len(mapped_external_players),
        "canonical_player_ids_mapped": len(mapped_player_ids),
        "unmapped_game_ids": [],
        "unmapped_player_ids": [],
        "ambiguous_game_ids": [],
        "ambiguous_player_ids": [],
        "team_identity_mismatches": 0,
    }
    manifest_core: dict[str, Any] = {
        "contract_version": PROMOTION_CONTRACT_VERSION,
        "event_schema_version": EVENT_SCHEMA_VERSION,
        "aggregate_schema_version": AGGREGATE_SCHEMA_VERSION,
        "source": SOURCE,
        "source_mode": SOURCE_MODE,
        "source_version": options.source_version,
        "research_provenance": RESEARCH_PROVENANCE,
        "temporal_semantics": TEMPORAL_SEMANTICS,
        "evidence_classification": EVIDENCE_CLASSIFICATION,
        "quality_tier": QUALITY_TIER,
        "training_eligible": False,
        "evaluation_eligible": False,
        "publication_timing_proven": False,
        "historical_availability_proven": False,
        "availability_basis": AVAILABILITY_BASIS,
        "target_date": options.target_date.isoformat(),
        "aggregate_as_of_date": aggregate_as_of.isoformat(),
        "durable_retrieved_at": _iso(archive.ingestion_timestamp),
        "available_at": _iso(archive.ingestion_timestamp),
        "source_event_timestamp": None,
        "source_evidence": {
            "raw_reference": _logical_raw_reference(archive),
            "archive_bytes": archive.archive_bytes,
            "archive_file_sha256": archive.archive_file_checksum,
            "uncompressed_archive_bytes": archive.uncompressed_bytes,
            "payload_sha256": archive.payload_checksum,
            "csv_body_bytes": archive.csv_body_bytes,
            "csv_body_sha256": archive.csv_body_checksum,
            "request_fingerprint": archive.request_fingerprint,
            "query": archive.query,
        },
        "schema": {
            "provider_contract": archive.schema_version,
            "provider_schema_sha256": archive.schema_fingerprint,
            "observed_field_count": len(archive.observed_fields),
            "observed_fields": list(archive.observed_fields),
            "promoted_feature_fields": sorted(ALLOWED_RESEARCH_FEATURE_FIELDS),
            "discarded_forbidden_fields": sorted(
                FORBIDDEN_FEATURE_FIELDS.intersection(archive.observed_fields)
            ),
        },
        "counts": {
            "records_received": len(archive.rows),
            "records_accepted": len(events),
            "records_rejected": 0,
            "duplicate_count": duplicate_count,
            "canonical_game_count": len(mapped_game_ids),
            "canonical_player_count": len(mapped_player_ids),
            "research_aggregate_count": len(aggregates),
        },
        "mapping_report": mapping_report,
        "cutoff_policy": {
            "historical_2025_t_minus_6h_eligible": False,
            "reason": AVAILABILITY_BASIS,
            "target_game_and_date_events_forbidden": True,
            "aggregate_windows_days": [14, 30],
            "season_to_date": True,
        },
        "files": {
            "events.jsonl": {
                "bytes": len(events_bytes),
                "sha256": hashlib.sha256(events_bytes).hexdigest(),
                "rows": len(events),
            },
            "aggregates.jsonl": {
                "bytes": len(aggregates_bytes),
                "sha256": hashlib.sha256(aggregates_bytes).hexdigest(),
                "rows": len(aggregates),
            },
        },
        "caps": {
            "maximum_archive_bytes": options.maximum_archive_bytes,
            "maximum_uncompressed_bytes": options.maximum_uncompressed_bytes,
            "maximum_csv_bytes": options.maximum_csv_bytes,
            "maximum_rows": options.maximum_rows,
            "maximum_artifact_bytes": options.maximum_artifact_bytes,
        },
    }
    semantic_checksum = _sha256_json(manifest_core)
    artifact_id = f"statcast-a2-{semantic_checksum[:16]}"
    manifest = {
        **manifest_core,
        "artifact_id": artifact_id,
        "manifest_semantic_sha256": semantic_checksum,
        "manifest_checksum_method": "sha256_canonical_json_without_identity_fields_v1",
    }
    manifest_bytes = (_canonical_json(manifest) + "\n").encode("utf-8")
    total_bytes = len(events_bytes) + len(aggregates_bytes) + len(manifest_bytes)
    if total_bytes > options.maximum_artifact_bytes:
        raise StatcastPromotionError("Statcast promoted artifact exceeds the declared byte cap")
    artifact_path, reused = _persist_artifact(
        Path(options.artifact_root),
        artifact_id,
        {
            "events.jsonl": events_bytes,
            "aggregates.jsonl": aggregates_bytes,
            "manifest.json": manifest_bytes,
        },
    )
    return StatcastPromotionResult(
        artifact_id=artifact_id,
        artifact_path=artifact_path,
        manifest=manifest,
        events=events,
        aggregates=aggregates,
        idempotent_reuse=reused,
    )


def build_research_aggregates(
    events: Sequence[Mapping[str, Any]],
    *,
    target_official_date: date,
    target_game_ids: Iterable[str] = (),
    historical_cutoff: datetime | None = None,
) -> tuple[dict[str, Any], ...]:
    """Build compact research aggregates while failing closed on target leakage."""

    forbidden_games = {str(value) for value in target_game_ids}
    cutoff = _as_utc(historical_cutoff) if historical_cutoff is not None else None
    validated: list[Mapping[str, Any]] = []
    for event in events:
        if (
            event.get("evidence_classification") != EVIDENCE_CLASSIFICATION
            or event.get("quality_tier") != QUALITY_TIER
            or event.get("training_eligible") is not False
            or event.get("evaluation_eligible") is not False
            or event.get("availability_basis") != AVAILABILITY_BASIS
            or event.get("source_published_at") is not None
        ):
            raise StatcastPromotionError("Research aggregate input is not Tier-B Statcast")
        event_date = _date_value(event.get("event_date"), "event_date")
        game_id = str(event.get("game_id") or "")
        if not game_id or game_id in forbidden_games:
            raise StatcastPromotionError("Target-game Statcast evidence is forbidden")
        if event_date >= target_official_date:
            raise StatcastPromotionError("Target-date or future Statcast evidence is forbidden")
        available_at = _aware_datetime(event.get("available_at"), "event available_at")
        if cutoff is not None:
            final_at = _aware_datetime(
                event.get("source_game_final_at"), "source game final_at"
            )
            if final_at >= cutoff:
                raise StatcastPromotionError("Statcast source game was not final before cutoff")
            if available_at > cutoff:
                raise StatcastPromotionError(
                    "Statcast evidence was unavailable at historical cutoff"
                )
        features = event.get("research_features")
        if not isinstance(features, Mapping):
            raise StatcastPromotionError("Statcast event research feature payload is invalid")
        validate_research_feature_payload(features)
        validated.append(event)

    windows: tuple[tuple[str, date], ...] = (
        ("ROLLING_14_DAY", target_official_date - timedelta(days=14)),
        ("ROLLING_30_DAY", target_official_date - timedelta(days=30)),
        ("SEASON_TO_DATE", date(target_official_date.year, 1, 1)),
    )
    output: list[dict[str, Any]] = []
    for window_name, window_start in windows:
        grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
        for event in validated:
            event_date = _date_value(event["event_date"], "event_date")
            if event_date < window_start:
                continue
            grouped[(str(event["batter_id"]), "BATTER")].append(event)
            grouped[(str(event["pitcher_id"]), "PITCHER")].append(event)
        for (player_id, role), player_events in sorted(grouped.items()):
            ordered = sorted(player_events, key=lambda item: _semantic_key_tuple(item))
            features = [_feature_dict(item) for item in ordered]
            descriptions = [str(item.get("description") or "") for item in features]
            terminal_events = [str(item.get("events") or "") for item in features]
            swings = sum(value in _SWING_DESCRIPTIONS for value in descriptions)
            whiffs = sum(value in _WHIFF_DESCRIPTIONS for value in descriptions)
            called_strikes = sum(value == "called_strike" for value in descriptions)
            plate_appearances = sum(bool(value) for value in terminal_events)
            strikeouts = sum(value in _STRIKEOUT_EVENTS for value in terminal_events)
            walks = sum(value in _WALK_EVENTS for value in terminal_events)
            release_speeds = _present_floats(features, "release_speed")
            launch_speeds = _present_floats(features, "launch_speed")
            launch_angles = _present_floats(features, "launch_angle")
            contributor_manifest = sorted(
                (
                    {
                        "semantic_key": dict(item["semantic_key"]),
                        "event_checksum": str(item["event_checksum"]),
                        "identity_checksum": str(item["identity_checksum"]),
                    }
                    for item in ordered
                ),
                key=lambda item: _canonical_json(item["semantic_key"]),
            )
            contributor_manifest_checksum = _sha256_json(contributor_manifest)
            source_versions = sorted({
                str(item["semantic_key"]["source_version"]) for item in ordered
            })
            if len(source_versions) != 1:
                raise StatcastPromotionError("Research aggregate mixes Statcast source versions")
            available_at = max(
                _aware_datetime(item["available_at"], "event available_at")
                for item in ordered
            )
            denominators = {
                "pitch_count": len(ordered),
                "swing_count": swings,
                "terminal_plate_appearance_count": plate_appearances,
                "release_speed_count": len(release_speeds),
                "batted_ball_speed_count": len(launch_speeds),
                "batted_ball_angle_count": len(launch_angles),
            }
            values = {
                "swing_rate": _ratio(swings, len(ordered)),
                "whiff_rate": _ratio(whiffs, swings),
                "called_strike_rate": _ratio(called_strikes, len(ordered)),
                "strikeout_rate": _ratio(strikeouts, plate_appearances),
                "walk_rate": _ratio(walks, plate_appearances),
                "average_release_speed": _mean(release_speeds),
                "average_launch_speed": _mean(launch_speeds),
                "average_launch_angle": _mean(launch_angles),
            }
            identity = {
                "schema_version": AGGREGATE_SCHEMA_VERSION,
                "player_id": player_id,
                "role": role,
                "window": window_name,
                "window_start_date": window_start.isoformat(),
                "target_official_date_exclusive": target_official_date.isoformat(),
                "source_version": source_versions[0],
                "available_at": _iso(available_at),
                "availability_basis": AVAILABILITY_BASIS,
                "evidence_classification": EVIDENCE_CLASSIFICATION,
                "quality_tier": QUALITY_TIER,
                "training_eligible": False,
                "evaluation_eligible": False,
                "contributing_event_manifest_sha256": contributor_manifest_checksum,
                "denominators": denominators,
                "values": values,
            }
            output.append({
                **identity,
                "historical_2025_t_minus_6h_eligible": False,
                "contributing_event_count": len(ordered),
                "aggregate_checksum": _sha256_json(identity),
            })
    return tuple(
        sorted(output, key=lambda item: (item["window"], item["role"], item["player_id"]))
    )


def validate_research_feature_payload(payload: Mapping[str, Any]) -> None:
    """Reject leakage-prone or out-of-contract fields from promoted feature data."""

    keys = {str(value) for value in payload}
    forbidden = sorted(keys.intersection(FORBIDDEN_FEATURE_FIELDS))
    unknown = sorted(keys.difference(ALLOWED_RESEARCH_FEATURE_FIELDS))
    if forbidden or unknown:
        raise StatcastPromotionError(
            f"Statcast research feature payload is unsafe: forbidden={forbidden}, unknown={unknown}"
        )
    for key, value in payload.items():
        if isinstance(value, float) and not math.isfinite(value):
            raise StatcastPromotionError(f"Statcast research feature {key} is non-finite")


def assert_historical_cutoff_eligible(
    event_or_aggregate: Mapping[str, Any],
    *,
    cutoff: datetime,
) -> None:
    """Always fail current-retrieval Tier-B evidence as historical training input."""

    available_at = _aware_datetime(event_or_aggregate.get("available_at"), "available_at")
    boundary = _as_utc(cutoff)
    if available_at > boundary:
        raise StatcastPromotionError("Statcast evidence was retrieved after historical cutoff")
    raise StatcastPromotionError(
        "Statcast historical publication timing is unproven; evidence is research-only Tier B"
    )


def _canonical_event(
    row: Mapping[str, str],
    *,
    row_number: int,
    source_version: str,
    target_date: date,
    available_at: datetime,
    game: StatcastGameMapping,
    batter: StatcastPlayerMapping,
    pitcher: StatcastPlayerMapping,
    archive: ValidatedStatcastArchive,
) -> dict[str, Any]:
    row_date = _date_value(row.get("game_date"), "game_date")
    if row_date != target_date or game.official_date != target_date:
        raise StatcastPromotionError(f"Statcast row {row_number} has an off-date game identity")
    home_team = _team_code(row.get("home_team"), "home_team", row_number)
    away_team = _team_code(row.get("away_team"), "away_team", row_number)
    if home_team != _team_code(game.home_team_code, "mapped home team", row_number):
        raise StatcastPromotionError(f"Statcast row {row_number} home-team identity mismatch")
    if away_team != _team_code(game.away_team_code, "mapped away team", row_number):
        raise StatcastPromotionError(f"Statcast row {row_number} away-team identity mismatch")
    final_at = _as_utc(game.final_at)
    if final_at <= datetime.combine(target_date, datetime.min.time(), tzinfo=UTC):
        raise StatcastPromotionError("Mapped game final time is inconsistent with official date")

    game_pk = _positive_external_id(row.get("game_pk"), "game_pk", row_number)
    at_bat_number = _positive_int(row.get("at_bat_number"), "at_bat_number", row_number)
    pitch_number = _positive_int(row.get("pitch_number"), "pitch_number", row_number)
    context = {
        "inning": _positive_int(row.get("inning"), "inning", row_number),
        "inning_topbot": _enum(row.get("inning_topbot"), {"Top", "Bot"}, "inning_topbot"),
        "outs_when_up": _bounded_int(row.get("outs_when_up"), 0, 2, "outs_when_up"),
        "balls": _bounded_int(row.get("balls"), 0, 3, "balls"),
        "strikes": _bounded_int(row.get("strikes"), 0, 2, "strikes"),
        "stand": _enum(row.get("stand"), {"L", "R"}, "stand"),
        "p_throws": _enum(row.get("p_throws"), {"L", "R"}, "p_throws"),
    }
    outcome = {
        "pitch_type": _optional_text(row.get("pitch_type")),
        "pitch_name": _optional_text(row.get("pitch_name")),
        "description": _required_text(row.get("description"), "description", row_number),
        "type": _enum(row.get("type"), {"B", "S", "X"}, "type"),
        "events": _optional_text(row.get("events")),
        "zone": _optional_int(row.get("zone"), "zone", row_number),
    }
    measurements = {
        "release_speed": _optional_float(row.get("release_speed"), "release_speed", row_number),
        "effective_speed": _optional_float(
            row.get("effective_speed"), "effective_speed", row_number
        ),
        "release_spin_rate": _optional_float(
            row.get("release_spin_rate"), "release_spin_rate", row_number
        ),
        "spin_axis": _optional_float(row.get("spin_axis"), "spin_axis", row_number),
        "plate_x": _optional_float(row.get("plate_x"), "plate_x", row_number),
        "plate_z": _optional_float(row.get("plate_z"), "plate_z", row_number),
        "pfx_x": _optional_float(row.get("pfx_x"), "pfx_x", row_number),
        "pfx_z": _optional_float(row.get("pfx_z"), "pfx_z", row_number),
        "release_extension": _optional_float(
            row.get("release_extension"), "release_extension", row_number
        ),
        "launch_speed": _optional_float(row.get("launch_speed"), "launch_speed", row_number),
        "launch_angle": _optional_float(row.get("launch_angle"), "launch_angle", row_number),
        "hit_distance_sc": _optional_float(
            row.get("hit_distance_sc"), "hit_distance_sc", row_number
        ),
        "bb_type": _optional_text(row.get("bb_type")),
    }
    research_features = {**context, **outcome, **measurements}
    validate_research_feature_payload(research_features)
    semantic_key = {
        "source_version": source_version,
        "game_pk": game_pk,
        "at_bat_number": at_bat_number,
        "pitch_number": pitch_number,
    }
    source_row_checksum = _sha256_json(dict(row))
    event_content = {
        "semantic_key": semantic_key,
        "event_date": target_date.isoformat(),
        "game_id": game.game_id,
        "source_game_final_at": _iso(final_at),
        "batter_id": batter.player_id,
        "pitcher_id": pitcher.player_id,
        "home_team_code": home_team,
        "away_team_code": away_team,
        "research_features": research_features,
        "source_row_checksum": source_row_checksum,
    }
    event_checksum = _sha256_json(event_content)
    identity_checksum = _sha256_json({
        "semantic_key": semantic_key,
        "event_checksum": event_checksum,
        "available_at": _iso(available_at),
        "schema_version": EVENT_SCHEMA_VERSION,
    })
    return {
        "schema_version": EVENT_SCHEMA_VERSION,
        "semantic_key": semantic_key,
        "event_date": target_date.isoformat(),
        "source_event_timestamp": None,
        "game_id": game.game_id,
        "source_game_final_at": _iso(final_at),
        "batter_id": batter.player_id,
        "pitcher_id": pitcher.player_id,
        "batter_external_mlb_id": _positive_external_id(
            batter.source_player_id, "mapped batter ID", row_number
        ),
        "pitcher_external_mlb_id": _positive_external_id(
            pitcher.source_player_id, "mapped pitcher ID", row_number
        ),
        "home_team_code": home_team,
        "away_team_code": away_team,
        "research_features": research_features,
        "retrieved_at": _iso(available_at),
        "available_at": _iso(available_at),
        "source_published_at": None,
        "availability_basis": AVAILABILITY_BASIS,
        "evidence_classification": EVIDENCE_CLASSIFICATION,
        "quality_tier": QUALITY_TIER,
        "training_eligible": False,
        "evaluation_eligible": False,
        "historical_2025_t_minus_6h_eligible": False,
        "lineage": {
            "source": SOURCE,
            "source_mode": SOURCE_MODE,
            "source_version": source_version,
            "raw_reference": _logical_raw_reference(archive),
            "archive_file_sha256": archive.archive_file_checksum,
            "payload_sha256": archive.payload_checksum,
            "csv_body_sha256": archive.csv_body_checksum,
            "provider_schema_sha256": archive.schema_fingerprint,
            "source_row_checksum": source_row_checksum,
        },
        "event_checksum": event_checksum,
        "identity_checksum": identity_checksum,
    }


def _game_index(rows: Iterable[StatcastGameMapping]) -> dict[str, StatcastGameMapping]:
    result: dict[str, StatcastGameMapping] = {}
    canonical_ids: set[str] = set()
    for row in rows:
        key = _positive_external_id(row.source_game_id, "mapped game ID", 0)
        if key in result:
            raise StatcastPromotionError(f"Ambiguous Statcast game mapping: {key}")
        if not row.game_id.strip():
            raise StatcastPromotionError("Canonical game mapping ID is empty")
        if row.game_id in canonical_ids:
            raise StatcastPromotionError(f"Ambiguous canonical game mapping: {row.game_id}")
        _as_utc(row.final_at)
        result[key] = row
        canonical_ids.add(row.game_id)
    return result


def _player_index(rows: Iterable[StatcastPlayerMapping]) -> dict[str, StatcastPlayerMapping]:
    result: dict[str, StatcastPlayerMapping] = {}
    canonical_ids: set[str] = set()
    for row in rows:
        key = _positive_external_id(row.source_player_id, "mapped player ID", 0)
        if key in result:
            raise StatcastPromotionError(f"Ambiguous Statcast player mapping: {key}")
        if not row.player_id.strip():
            raise StatcastPromotionError("Canonical player mapping ID is empty")
        if row.player_id in canonical_ids:
            raise StatcastPromotionError(f"Ambiguous canonical player mapping: {row.player_id}")
        result[key] = row
        canonical_ids.add(row.player_id)
    return result


def _persist_artifact(
    root: Path,
    artifact_id: str,
    files: Mapping[str, bytes],
) -> tuple[Path, bool]:
    root.mkdir(parents=True, exist_ok=True)
    destination = root / artifact_id
    if destination.exists():
        if not destination.is_dir():
            raise StatcastPromotionError("Statcast artifact identity collides with a file")
        for name, expected in files.items():
            path = destination / name
            if not path.is_file() or path.read_bytes() != expected:
                raise StatcastPromotionError("Existing Statcast artifact differs from identity")
        if {path.name for path in destination.iterdir()} != set(files):
            raise StatcastPromotionError("Existing Statcast artifact has unexpected files")
        return destination, True
    # ``tempfile.mkdtemp`` can inherit restrictive ACLs from a system temp
    # policy on Windows even when ``dir`` is supplied.  A normal exclusive
    # directory under the already-authorized artifact root stays portable.
    temporary = root / f".{artifact_id}.{uuid4().hex}.tmp"
    temporary.mkdir(parents=False, exist_ok=False)
    try:
        for name, content in files.items():
            path = temporary / name
            with path.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return destination, False


def _semantic_key_tuple(event: Mapping[str, Any]) -> tuple[str, str, int, int]:
    key = event.get("semantic_key")
    if not isinstance(key, Mapping):
        raise StatcastPromotionError("Statcast event semantic key is invalid")
    return (
        str(key.get("source_version") or ""),
        str(key.get("game_pk") or ""),
        int(key.get("at_bat_number") or 0),
        int(key.get("pitch_number") or 0),
    )


def _semantic_sort_key(value: tuple[str, str, int, int]) -> tuple[str, int, int, int]:
    source_version, game_pk, at_bat_number, pitch_number = value
    return source_version, int(game_pk), at_bat_number, pitch_number


def _feature_dict(event: Mapping[str, Any]) -> Mapping[str, Any]:
    value = event.get("research_features")
    if not isinstance(value, Mapping):
        raise StatcastPromotionError("Statcast research feature payload is invalid")
    return value


def _logical_raw_reference(archive: ValidatedStatcastArchive) -> str:
    """Return a portable reference without leaking a workstation path."""

    return (
        "historical/baseball_savant_statcast/"
        f"{archive.requested_date:%Y/%m}/{archive.archive_path.name}"
    )


def _present_floats(rows: Sequence[Mapping[str, Any]], key: str) -> list[float]:
    return [float(row[key]) for row in rows if row.get(key) is not None]


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 12) if denominator else None


def _mean(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 12) if values else None


def _jsonl_bytes(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return "".join(f"{_canonical_json(dict(row))}\n" for row in rows).encode("utf-8")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _raw_archive_payload_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_gzip_bounded(path: Path, maximum_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    try:
        with gzip.open(path, "rb") as stream:
            while True:
                chunk = stream.read(min(1024 * 1024, maximum_bytes - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > maximum_bytes:
                    raise StatcastPromotionError(
                        "Statcast archive exceeds the uncompressed-byte cap"
                    )
                chunks.append(chunk)
    except (OSError, EOFError) as exc:
        raise StatcastPromotionError("Statcast archive gzip integrity check failed") from exc
    return b"".join(chunks)


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _request_fingerprint(target_date: date) -> str:
    return _sha256_json({
        "endpoint": STATCAST_DETAIL_ENDPOINT,
        "query": statcast_detail_query(target_date),
        "scope": "RAW_ARCHIVE_ONLY",
        "schema_version": STATCAST_DETAIL_SCHEMA_VERSION,
    })


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise StatcastPromotionError("Statcast timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _aware_datetime(value: Any, name: str) -> datetime:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise StatcastPromotionError(f"{name} is invalid") from exc
    return _as_utc(parsed)


def _date_value(value: Any, name: str) -> date:
    try:
        return (
            value
            if isinstance(value, date) and not isinstance(value, datetime)
            else date.fromisoformat(str(value))
        )
    except (TypeError, ValueError) as exc:
        raise StatcastPromotionError(f"Statcast {name} is invalid") from exc


def _iso(value: datetime) -> str:
    return _as_utc(value).isoformat()


def _positive_external_id(value: Any, name: str, row_number: int) -> str:
    return str(_positive_int(value, name, row_number))


def _positive_int(value: Any, name: str, row_number: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise StatcastPromotionError(f"Statcast row {row_number} {name} is invalid") from exc
    if parsed <= 0:
        raise StatcastPromotionError(f"Statcast row {row_number} {name} must be positive")
    return parsed


def _bounded_int(value: Any, minimum: int, maximum: int, name: str) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise StatcastPromotionError(f"Statcast {name} is invalid") from exc
    if not minimum <= parsed <= maximum:
        raise StatcastPromotionError(f"Statcast {name} is outside {minimum}..{maximum}")
    return parsed


def _optional_int(value: Any, name: str, row_number: int) -> int | None:
    if value is None or not str(value).strip():
        return None
    try:
        return int(str(value).strip())
    except ValueError as exc:
        raise StatcastPromotionError(f"Statcast row {row_number} {name} is invalid") from exc


def _optional_float(value: Any, name: str, row_number: int) -> float | None:
    if value is None or not str(value).strip():
        return None
    try:
        parsed = float(str(value).strip())
    except ValueError as exc:
        raise StatcastPromotionError(f"Statcast row {row_number} {name} is invalid") from exc
    if not math.isfinite(parsed):
        raise StatcastPromotionError(f"Statcast row {row_number} {name} is non-finite")
    return parsed


def _enum(value: Any, allowed: set[str], name: str) -> str:
    parsed = str(value or "").strip()
    if parsed not in allowed:
        raise StatcastPromotionError(f"Statcast {name} is invalid")
    return parsed


def _required_text(value: Any, name: str, row_number: int) -> str:
    parsed = str(value or "").strip()
    if not parsed:
        raise StatcastPromotionError(f"Statcast row {row_number} {name} is empty")
    return parsed


def _optional_text(value: Any) -> str | None:
    parsed = str(value or "").strip()
    return parsed or None


def _team_code(value: Any, name: str, row_number: int) -> str:
    parsed = _required_text(value, name, row_number).upper()
    if not 2 <= len(parsed) <= 4 or not parsed.isalnum():
        raise StatcastPromotionError(f"Statcast row {row_number} {name} is invalid")
    return parsed


__all__ = [
    "AGGREGATE_SCHEMA_VERSION",
    "AVAILABILITY_BASIS",
    "EVIDENCE_CLASSIFICATION",
    "EVENT_SCHEMA_VERSION",
    "FORBIDDEN_FEATURE_FIELDS",
    "PROMOTION_CONTRACT_VERSION",
    "QUALITY_TIER",
    "StatcastGameMapping",
    "StatcastPlayerMapping",
    "StatcastPromotionError",
    "StatcastPromotionOptions",
    "StatcastPromotionResult",
    "ValidatedStatcastArchive",
    "assert_historical_cutoff_eligible",
    "build_research_aggregates",
    "inspect_statcast_archive",
    "promote_statcast_archive",
    "validate_research_feature_payload",
]

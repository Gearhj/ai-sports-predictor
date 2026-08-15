from __future__ import annotations

from hashlib import sha256
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.gen2.fd1_recovery_identity import IDENTITY_SPOOL_SCHEMA_VERSION
from app.gen2.fd1_recovery_manifest import ContentAddressedJsonArtifact
from scripts import prepare_fd1_recovery as prepare


GIB = 1024**3
SOURCE_VERSIONS = {
    "CHADWICK_REGISTER_ARCHIVES": {
        "dataset_version": "chadwick-release",
        "id": "11111111-1111-4111-8111-111111111111",
        "identity_checksum": "1" * 64,
        "source_mode": "LIVE",
    },
    "RETROSHEET_AUXILIARY_DOWNLOADS": {
        "dataset_version": "retrosheet-release",
        "id": "22222222-2222-4222-8222-222222222222",
        "identity_checksum": "2" * 64,
        "source_mode": "LIVE",
    },
    "RETROSHEET_CSV_DOWNLOADS": {
        "dataset_version": "retrosheet-release",
        "id": "33333333-3333-4333-8333-333333333333",
        "identity_checksum": "3" * 64,
        "source_mode": "LIVE",
    },
    "SABR_LAHMAN_BOX_DOWNLOADS": {
        "dataset_version": "lahman-release",
        "id": "44444444-4444-4444-8444-444444444444",
        "identity_checksum": "4" * 64,
        "source_mode": "LIVE",
    },
}
ROLE_TABLES = {
    "lahman_batting": "Batting.csv",
    "lahman_fielding": "Fielding.csv",
    "lahman_franchises": "TeamsFranchises.csv",
    "lahman_home_games": "HomeGames.csv",
    "lahman_parks": "Parks.csv",
    "lahman_people": "People.csv",
    "lahman_pitching": "Pitching.csv",
    "lahman_teams": "Teams.csv",
    "retrosheet_ballparks": "ballparks.csv",
    "retrosheet_teams": "teams.csv",
}


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _download(source_key: str, index: int) -> dict[str, object]:
    release = SOURCE_VERSIONS[source_key]["dataset_version"]
    return {
        "archive_sha256": f"{index:x}" * 64,
        "content_length": 100 + index,
        "download_identity_checksum": f"{index + 4:x}" * 64,
        "object_key": f"object-{index}",
        "payload_sha256": f"{index:x}" * 64,
        "raw_logical_uri": f"open-data/{source_key.lower()}/object-{index}.blob",
        "source_key": source_key,
        "source_release": release,
        "upstream_revision": 1,
    }


def _fixture_inputs(root: Path) -> dict[str, Path]:
    ledger_document = {
        "identity": {"mappings": 11},
        "mode": "dry-run",
        "prohibited_activity": {
            "network_requests": 0,
            "provider_transport_constructed": False,
        },
        "publication": {
            "code_revision": "a" * 64,
            "identity_mappings_seen": 11,
            "mode": "dry-run",
            "network_requests": 0,
            "provider_transport_constructed": False,
        },
        "registered_downloads_used": [
            _download(source_key, index)
            for index, source_key in enumerate(sorted(SOURCE_VERSIONS), start=5)
        ],
        "sanitized": True,
        "schema_version": prepare.TRUSTED_LEDGER_SCHEMA_VERSION,
        "source_versions": SOURCE_VERSIONS,
        "success": True,
    }
    ledger_payload = _canonical_bytes(ledger_document)
    ledger_sha = sha256(ledger_payload).hexdigest()
    ledger_relative = (
        Path(".runtime")
        / "gate3c-fd1"
        / "artifacts"
        / f"trusted-sha256-{ledger_sha}.json"
    )
    ledger_path = root / ledger_relative
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_bytes(ledger_payload)

    envelope_directory = root / ".runtime" / "gate3c-fd1" / "recovery" / "inputs"
    source_directory = envelope_directory / "sources"
    source_directory.mkdir(parents=True)
    source_files = []
    identity_tables = [
        *(('chadwick_people', f"people-{digit:x}.csv") for digit in range(16)),
        *((role, ROLE_TABLES[role]) for role in ROLE_TABLES),
    ]
    for index, (role, table) in enumerate(identity_tables, start=1):
        source_path = source_directory / f"{index:02d}-{table}"
        payload = f"synthetic-{role}-{table}\n".encode("utf-8")
        source_path.write_bytes(payload)
        source_key = prepare._ROLE_SOURCE_KEY[role]
        source_files.append(
            {
                "content_length": len(payload),
                "logical_table": table,
                "logical_uri": f"open-data/identity/{index:02d}-{table}",
                "path": f"sources/{source_path.name}",
                "role": role,
                "sha256": sha256(payload).hexdigest(),
                "source_key": source_key,
                "source_release": SOURCE_VERSIONS[source_key]["dataset_version"],
            }
        )
    envelope = {
        "additional_governance_source": {
            "source_archives": [
                {
                    "archive_sha256": "5" * 64,
                    "content_length": 55,
                    "download_identity_checksum": "6" * 64,
                    "object_key": "retrosheet-events-2025",
                    "payload_sha256": "5" * 64,
                    "raw_logical_uri": "open-data/retrosheet-events/2025.zip",
                    "source_key": "RETROSHEET_EVENT_ARCHIVES",
                    "source_release": "events-through-2025",
                }
            ],
            "source_release": {
                "dataset_version": "events-through-2025",
                "identity_checksum": "7" * 64,
                "source_key": "RETROSHEET_EVENT_ARCHIVES",
                "source_mode": "LIVE",
                "source_version_id": "55555555-5555-4555-8555-555555555555",
            },
        },
        "identity": {
            "expected_row_count": 11,
            "published_lahman_tables": sorted(
                table for role, table in ROLE_TABLES.items() if role.startswith("lahman_")
            ),
            "source_files": source_files,
        },
        "interrupted_execution": {
            "attempted_command": "python publish_actual.py --execute",
            "final_execute_evidence_present": False,
            "sql_rolled_back": True,
            "started_at_utc": "2026-08-15T12:39:06Z",
            "stopped_at_utc": "2026-08-15T14:29:54Z",
            "suspected_slow_stage": "IDENTITY_MAPPING_ORM_FLUSH",
        },
        "schema_version": prepare.PREPARE_INPUT_SCHEMA_VERSION,
        "trusted_ledger": {
            "logical_uri": ledger_relative.as_posix(),
            "schema_version": prepare.TRUSTED_LEDGER_SCHEMA_VERSION,
            "sha256": ledger_sha,
        },
    }
    envelope_path = envelope_directory / "identity-inputs.json"
    envelope_path.write_bytes(_canonical_bytes(envelope))

    store = root / "store"
    parquet_uris = (
        "historical-events/source=retrosheet_csv_downloads/"
        "family=weather/season=2025/month=01/"
        "schema=retrosheet_fd1_weather_v8/"
        f"source-version={SOURCE_VERSIONS['RETROSHEET_CSV_DOWNLOADS']['id']}/"
        "checkpoint=00000000-0000-5000-8000-000000000001/"
        f"governance={'b' * 64}/part-{'c' * 64}.parquet",
        "historical-events/source=sabr_lahman_box_downloads/"
        "family=player_prior/season=2025/month=12/"
        "schema=gate3c_fd1_identity_prior_v1/"
        f"source-version={SOURCE_VERSIONS['SABR_LAHMAN_BOX_DOWNLOADS']['id']}/"
        "checkpoint=00000000-0000-5000-8000-000000000002/"
        f"governance={'d' * 64}/part-{'e' * 64}.parquet",
    )
    for index, uri in enumerate(parquet_uris, start=1):
        target = store.joinpath(*Path(uri).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f"parquet-{index}".encode("ascii"))
    parquet_list = envelope_directory / "parquet-files.txt"
    parquet_list.write_text("\n".join(reversed(parquet_uris)) + "\n", encoding="utf-8")
    return {
        "envelope": envelope_path,
        "ledger": ledger_path,
        "list": parquet_list,
        "output": root / ".runtime" / "gate3c-fd1" / "recovery",
        "store": store,
    }


def test_bounded_preparation_uses_only_explicit_inputs_and_emits_bindings(
    artifact_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _fixture_inputs(artifact_root)
    captured: dict[str, object] = {}

    def build_spool(**kwargs):
        captured["spool"] = kwargs
        spool_path = kwargs["output_directory"] / f"fd1-identity-spool-sha256-{'f' * 64}.csv"
        spool_path.parent.mkdir(parents=True)
        spool_path.write_bytes(b"synthetic-spool")
        return SimpleNamespace(
            batch_count=1,
            byte_count=len(b"synthetic-spool"),
            path=spool_path,
            row_count=11,
            schema_version=IDENTITY_SPOOL_SCHEMA_VERSION,
            sha256="f" * 64,
        )

    def build_manifest(**kwargs):
        captured["manifest"] = kwargs
        aggregate = SimpleNamespace(file_count=2, row_count=22)
        manifest = SimpleNamespace(
            aggregate=aggregate,
            manifest_checksum="7" * 64,
            recovery_id="55555555-5555-5555-8555-555555555555",
        )
        return SimpleNamespace(
            bytes_hashed=19,
            cache_hits=1,
            elapsed_seconds=1.25,
            files_hashed=1,
            hash_cache=SimpleNamespace(entries=(object(), object())),
            manifest=manifest,
        )

    def write_artifact(_value, *, output_root, **_kwargs):
        output_root.mkdir(parents=True, exist_ok=True)
        target = output_root / f"artifact-sha256-{'8' * 64}.json"
        target.write_bytes(b"{}\n")
        return ContentAddressedJsonArtifact(target, "8" * 64, 3, False)

    monkeypatch.setattr(prepare, "build_identity_spool_from_frozen_sources", build_spool)
    monkeypatch.setattr(prepare, "build_recovery_manifest", build_manifest)
    monkeypatch.setattr(prepare, "write_recovery_hash_cache", write_artifact)
    monkeypatch.setattr(prepare, "write_recovery_manifest", write_artifact)
    monkeypatch.setattr(prepare, "build_fd1_recovery_finalizer_revision", lambda: "9" * 64)

    progress = io.StringIO()
    result = prepare.prepare_fd1_recovery(
        ledger_path=paths["ledger"],
        parquet_list_path=paths["list"],
        store_root=paths["store"],
        output_root=paths["output"],
        identity_envelope_path=paths["envelope"],
        repository_root=artifact_root,
        expected_file_count=2,
        minimum_free_bytes=100 * GIB,
        progress_stream=progress,
        free_space=lambda _path: 200 * GIB,
    )

    assert result["provider_calls"] == 0
    assert result["network_requests"] == 0
    assert result["database_writes"] == 0
    assert result["raw_directory_walks"] == 0
    assert result["identity_spool"]["batch_count"] == 1
    assert result["identity_spool"]["rows"] == 11
    assert result["manifest"]["rows"] == 22
    assert result["hash_cache"]["rows"] == 2
    assert result["hash_cache"]["cache_hits"] == 1
    assert result["hash_cache"]["files_hashed"] == 1
    assert result["hash_cache"]["bytes_hashed"] == 19
    assert result["hash_cache"]["elapsed_seconds"] == 1.25
    assert result["run_id"] == "55555555-5555-5555-8555-555555555555"
    assert result["identity_spool"]["path"].startswith(
        ".runtime/gate3c-fd1/recovery/"
    )
    spool_call = captured["spool"]
    assert spool_call["batch_size"] == 10_000
    assert spool_call["expected_row_count"] == 11
    assert {item.source_key for item in spool_call["source_releases"]} == {
        "CHADWICK_REGISTER_ARCHIVES",
        "RETROSHEET_AUXILIARY_DOWNLOADS",
        "SABR_LAHMAN_BOX_DOWNLOADS",
    }
    manifest_call = captured["manifest"]
    assert manifest_call["logical_uris"] == tuple(sorted(manifest_call["logical_uris"]))
    assert manifest_call["generation_lineage"].interrupted_execution.completed_files == 2
    assert {
        item.source_key
        for item in manifest_call["generation_lineage"].source_releases
    } == {*SOURCE_VERSIONS, "RETROSHEET_EVENT_ARCHIVES"}
    assert manifest_call["finalizer_lineage"].code_revision == "9" * 64
    heartbeats = [json.loads(line) for line in progress.getvalue().splitlines()]
    assert heartbeats[0]["stage"] == "PREFLIGHT"
    assert heartbeats[-1]["stage"] == "COMPLETE"


def test_preparation_rejects_ledger_tampering_and_prohibited_identity_inputs(
    artifact_root: Path,
) -> None:
    paths = _fixture_inputs(artifact_root)
    envelope = prepare.load_preparation_envelope(
        paths["envelope"], repository_root=artifact_root
    )
    paths["ledger"].write_bytes(paths["ledger"].read_bytes() + b" ")
    with pytest.raises(prepare.FD1RecoveryPreparationError, match="SHA-256"):
        prepare._load_trusted_ledger(
            paths["ledger"],
            expected=envelope.trusted_ledger,
            repository_root=artifact_root,
        )

    inputs = json.loads(paths["envelope"].read_text(encoding="utf-8"))
    inputs["identity"]["source_files"][0]["logical_table"] = "plays.csv"
    paths["envelope"].write_bytes(_canonical_bytes(inputs))
    with pytest.raises(prepare.FD1RecoveryPreparationError, match="prohibited"):
        prepare.load_preparation_envelope(
            paths["envelope"], repository_root=artifact_root
        )

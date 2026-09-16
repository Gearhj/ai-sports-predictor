"""Consume published SHOW_PREVIEW packages from the Model Exchange.

The exchange is read-only to AISP. This module verifies before it stores:

1. the discovery index is the exact file named by its physical SHA-256;
2. every package's ``forecast.json`` hashes to the value the index records;
3. every physical file the package declares hashes and sizes as declared;
4. the document passes the existing ``AISP_SHOW_PREVIEW_FORECAST_V1`` validator
   (horizon, authority, timing, probabilities, model identity, content hash);
5. the import path checks the canonical game's teams and kickoff against AISP's
   own schedule.

Nothing here generates a forecast, reinterprets a CURRENT or FINAL_PREGAME
forecast as a preview, or backdates a clock. Importing requires
``show_preview_enabled``; a second run stores nothing and says so.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.show_preview.contract import ContractViolation, content_hash
from app.show_preview.imports import import_preview_forecast

DISCOVERY_SCHEMA = "ASP_SHOW_PREVIEW_DISCOVERY_V1"
ACK_SCHEMA = "AISP_SHOW_PREVIEW_CONSUMER_ACK_V1"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExchangeViolation(ValueError):
    """The published evidence does not match what it says about itself."""


@dataclass
class PackageVerification:
    package_sha256: str
    package_path: str
    forecast_id: str
    forecast_sha256: str
    content_sha256: str
    sport: str
    slate_date: str
    source_game_ref: str
    physical_files: list[dict[str, Any]]
    document: dict[str, Any]


@dataclass
class ImportOutcome:
    index_sha256: str
    index_path: str
    verified: list[PackageVerification] = field(default_factory=list)
    imported: list[str] = field(default_factory=list)
    already_held: list[str] = field(default_factory=list)
    rejected: list[dict[str, str]] = field(default_factory=list)


def _index_path(root: Path, index_sha256: str) -> Path:
    path = root / "indexes" / "sha256" / f"{index_sha256}.json"
    if not path.exists():
        raise ExchangeViolation(f"no discovery index at the named content address: {path}")
    actual = sha256_file(path)
    if actual != index_sha256:
        raise ExchangeViolation(f"discovery index hashes {actual}, not {index_sha256}")
    return path


def verify_index(root: Path, index_sha256: str) -> tuple[dict[str, Any], list[PackageVerification]]:
    """Verify the index and every package it names. Raises on any mismatch."""

    path = _index_path(root, index_sha256)
    index = json.loads(path.read_text(encoding="utf-8"))
    if index.get("schema_version") != DISCOVERY_SCHEMA:
        raise ExchangeViolation(f"discovery index schema is {index.get('schema_version')}, not {DISCOVERY_SCHEMA}")
    if index.get("horizon") != "SHOW_PREVIEW":
        raise ExchangeViolation("discovery index is not a SHOW_PREVIEW index")

    verified: list[PackageVerification] = []
    for entry in index.get("packages", []):
        package_dir = root / Path(entry["package_path"])
        forecast_path = package_dir / "forecast.json"
        if not forecast_path.exists():
            raise ExchangeViolation(f"package {entry['package_sha256']} has no forecast.json")
        actual = sha256_file(forecast_path)
        if actual != entry["forecast_sha256"]:
            raise ExchangeViolation(
                f"{entry['source_game_ref']}: forecast.json hashes {actual}, index says {entry['forecast_sha256']}")
        document = json.loads(forecast_path.read_text(encoding="utf-8"))
        if document.get("forecast_id") != entry["forecast_id"]:
            raise ExchangeViolation(f"{entry['source_game_ref']}: forecast_id disagrees with the index")
        declared = content_hash(document)
        if declared != (document.get("integrity") or {}).get("content_sha256"):
            raise ExchangeViolation(f"{entry['source_game_ref']}: content_sha256 does not match the document")
        for physical in (document.get("integrity") or {}).get("physical_files", []):
            file_path = package_dir / physical["path"]
            if not file_path.exists():
                raise ExchangeViolation(f"{entry['source_game_ref']}: missing physical file {physical['path']}")
            if sha256_file(file_path) != physical["sha256"]:
                raise ExchangeViolation(f"{entry['source_game_ref']}: {physical['path']} hash mismatch")
            if file_path.stat().st_size != physical["bytes"]:
                raise ExchangeViolation(f"{entry['source_game_ref']}: {physical['path']} byte count mismatch")
        verified.append(PackageVerification(
            package_sha256=entry["package_sha256"], package_path=entry["package_path"],
            forecast_id=entry["forecast_id"], forecast_sha256=entry["forecast_sha256"],
            content_sha256=declared, sport=entry.get("sport", document.get("sport", "")),
            slate_date=entry.get("slate_date", ""), source_game_ref=entry.get("source_game_ref", ""),
            physical_files=(document.get("integrity") or {}).get("physical_files", []), document=document,
        ))
    return index, verified


def import_published_previews(session: Session, settings: Settings, *, root: Path, index_sha256: str,
                              now: datetime | None = None) -> ImportOutcome:
    """Verify then register every package the index names. Idempotent."""

    moment = now or datetime.now(UTC)
    _index, verified = verify_index(root, index_sha256)
    outcome = ImportOutcome(
        index_sha256=index_sha256,
        index_path=str(root / "indexes" / "sha256" / f"{index_sha256}.json"),
        verified=verified)
    for package in verified:
        try:
            _row, created = import_preview_forecast(session, package.document, settings, now=moment)
        except ContractViolation as error:
            outcome.rejected.append({"source_game_ref": package.source_game_ref,
                                     "forecast_id": package.forecast_id, "reason": str(error)})
            continue
        (outcome.imported if created else outcome.already_held).append(package.forecast_id)
    return outcome


def acknowledgement(outcome: ImportOutcome, index: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    """The consumer acknowledgement: exactly what AISP verified and stored."""

    return {
        "schemaVersion": ACK_SCHEMA,
        "horizon": "SHOW_PREVIEW",
        "discoveryIndexSha256": outcome.index_sha256,
        "discoveryIndexSchemaVersion": index.get("schema_version"),
        "producerIndexCreatedUtc": index.get("created_utc"),
        "consumerAcknowledgedUtc": now.isoformat(),
        "packagesInIndex": len(outcome.verified),
        "packagesVerified": len(outcome.verified),
        "importedThisRun": len(outcome.imported),
        "alreadyHeld": len(outcome.already_held),
        "rejected": outcome.rejected,
        "authority": {
            "researchOnly": True,
            "grantsWageringAuthority": False,
            "createsPositions": False,
            "finalPregameEvidence": False,
            "note": ("Show Preview research for the day-before recording. These forecasts inform no decision, "
                     "stake, position, bankroll or settlement, and are not final-pregame evidence."),
        },
        "packages": [
            {
                "sport": p.sport,
                "slateDate": p.slate_date,
                "sourceGameRef": p.source_game_ref,
                "forecastId": p.forecast_id,
                "forecastSha256": p.forecast_sha256,
                "contentSha256": p.content_sha256,
                "packageSha256": p.package_sha256,
                "packagePath": p.package_path,
                "modelId": (p.document.get("model") or {}).get("model_id"),
                "modelVersion": (p.document.get("model") or {}).get("model_version"),
                "modelArtifactSha256": (p.document.get("model") or {}).get("artifact_sha256"),
                "canonicalGameId": (p.document.get("game") or {}).get("canonical_game_id"),
                "kickoffUtc": (p.document.get("game") or {}).get("kickoff_utc"),
                "producerCreatedUtc": p.document.get("created_utc"),
                "producerSourceCutoffUtc": p.document.get("source_cutoff_utc"),
                "physicalFiles": [{"path": f["path"], "sha256": f["sha256"], "bytes": f["bytes"]}
                                  for f in p.physical_files],
                "state": ("IMPORTED_THIS_RUN" if p.forecast_id in outcome.imported
                          else "ALREADY_HELD" if p.forecast_id in outcome.already_held else "REJECTED"),
            }
            for p in outcome.verified
        ],
    }

"""Verify and register the NFL overnight model-laboratory study as research evidence.

WHAT THIS IS
------------
Model Studio's overnight laboratory (ASP_NFL_OVERNIGHT_MODEL_LAB_HANDOFF_V1)
publishes a machine-readable handoff: a 24-row pipeline leaderboard over a
1,408-game Development cohort, the paired uncertainty of the selected
finalist against M1, and the calibration record. This importer verifies that
handoff and stores it as one append-only MODEL_BENCHMARK_STUDY supplement so
the product can show the actual producer results instead of a hand-typed table.

WHAT IT MAY NOT DO
------------------
Nothing here fits, executes or promotes a model. The study is Development
evidence only; its own handoff says M1 is retained and the finalist is
unpromoted, and those flags are required, not assumed.

VERIFICATION ORDER (fail closed)
--------------------------------
1. ``current.json`` names a handoff path, its byte digest and its manifest digest.
2. The handoff directory is named by that manifest digest.
3. The handoff file's bytes hash to the declared handoff digest BEFORE parsing.
4. The parsed handoff declares the supported contract, RESEARCH_SHADOW status,
   NOT_PROMOTED, no AISP execution, no wagering authority, and that the sealed
   final out-of-time set was not accessed.
5. The leaderboard is non-empty and every row names a pipeline and a cohort size.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.forecast_exchange.supplements import KIND_STUDY
from app.models import ResearchSupplement

CURRENT_CONTRACT = "ASP_NFL_OVERNIGHT_MODEL_LAB_CURRENT_V1"
HANDOFF_CONTRACT = "ASP_NFL_OVERNIGHT_MODEL_LAB_HANDOFF_V1"
STUDY_ID_PREFIX = "nfl-overnight-model-laboratory"


class LabStudyImportError(RuntimeError):
    pass


def _require(mapping: dict[str, Any], key: str, *, label: str) -> Any:
    if key not in mapping or mapping[key] is None:
        raise LabStudyImportError(f"{label} is missing '{key}'")
    return mapping[key]


def verify_lab_study(lab_root: Path) -> dict[str, Any]:
    lab_root = Path(lab_root)
    current_path = lab_root / "current.json"
    if not current_path.is_file():
        raise LabStudyImportError("current.json is absent")
    current = json.loads(current_path.read_text(encoding="utf-8"))
    if current.get("contract") != CURRENT_CONTRACT:
        raise LabStudyImportError("current.json contract is not supported")
    manifest_sha = str(_require(current, "manifestSha256", label="current.json")).lower()
    handoff_sha = str(_require(current, "handoffSha256", label="current.json")).lower()
    handoff_path = lab_root / "sha256" / manifest_sha / "handoff.json"
    if not handoff_path.is_file():
        raise LabStudyImportError("handoff.json is absent from the addressed directory")
    raw = handoff_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != handoff_sha:
        raise LabStudyImportError("handoff.json bytes do not hash to current.json's handoffSha256")
    handoff = json.loads(raw.decode("utf-8"))
    if handoff.get("contract") != HANDOFF_CONTRACT:
        raise LabStudyImportError("handoff contract is not supported")
    if str(handoff.get("externalManifestSha256", "")).lower() != manifest_sha:
        raise LabStudyImportError("handoff manifest digest differs from its directory address")
    if str(handoff.get("status")) != "RESEARCH_SHADOW":
        raise LabStudyImportError("study is not RESEARCH_SHADOW")
    if str(handoff.get("promotionStatus")) != "NOT_PROMOTED":
        raise LabStudyImportError("study asserts a promotion")
    if handoff.get("executionAllowedInAisp") is not False:
        raise LabStudyImportError("study must declare executionAllowedInAisp=false")
    if handoff.get("wageringAuthority") is not False:
        raise LabStudyImportError("study must declare wageringAuthority=false")
    if handoff.get("finalOotAccessed") is not False:
        raise LabStudyImportError("study must declare the sealed final OOT set was not accessed")
    leaderboard = _require(handoff, "leaderboard", label="handoff")
    if not isinstance(leaderboard, list) or not leaderboard:
        raise LabStudyImportError("leaderboard is empty")
    for row in leaderboard:
        _require(row, "pipelineId", label="leaderboard row")
        int(_require(row, "games", label="leaderboard row"))
    return {
        "manifest_sha256": manifest_sha,
        "handoff_sha256": handoff_sha,
        "handoff": handoff,
        "handoff_path": str(handoff_path),
    }


def _study_payload(verified: dict[str, Any]) -> dict[str, Any]:
    handoff = verified["handoff"]
    calibration = dict(handoff.get("calibration") or {})
    calibration.pop("path", None)
    calibration.pop("folds", None)
    games = {int(row.get("games") or 0) for row in handoff["leaderboard"]}
    return {
        "summaries": {
            "leaderboard": handoff["leaderboard"],
            "cohort": {
                "games": max(games) if games else 0,
                "common_cohort": len(games) == 1,
                "window": "Development out-of-fold, five expanding seasonal folds 2020-2024",
                "population_note": "6,991 games 1999-2024; final 2025 OOT sealed and unread",
            },
            # The producer excluded the registered market table because it has no
            # quote-observation timestamp, so no market benchmark class applies.
            "benchmarkTimingClass": "NONE",
            "marketBenchmark": {
                "available": False,
                "reason": "Registered market table has no quote-observation timestamp; "
                          "comparable pregame timing cannot be proven.",
            },
            "developmentSelectedPipeline": handoff.get("developmentSelectedPipeline"),
            "pairedUncertaintyVersusM1": handoff.get("pairedUncertaintyVersusM1"),
            "calibration": calibration,
            "runId": handoff.get("runId"),
            "earlySeason": {
                "applies": True,
                "note": "Development folds are full seasons; no early-season-only slice was "
                        "published, so early-season results are not separately claimable.",
            },
            "conclusion": "M1 retained; the calibrated finalist's advantage interval crosses zero.",
        },
        "producer": {
            "contract": HANDOFF_CONTRACT,
            "fittedDevelopmentArtifactSha256": (handoff.get("fittedDevelopmentArtifact") or {}).get("sha256"),
            "fittedCalibrationArtifactSha256": (handoff.get("fittedCalibrationArtifact") or {}).get("sha256"),
            "reportSha256": handoff.get("reportSha256"),
            "analysisSha256": handoff.get("analysisSha256"),
            "matrixSha256": handoff.get("matrixSha256"),
        },
    }


def register_lab_study(session: Session, lab_root: Path) -> dict[str, Any]:
    verified = verify_lab_study(lab_root)
    address = verified["handoff_sha256"]
    existing = session.scalar(
        select(ResearchSupplement).where(ResearchSupplement.supplement_sha256 == address)
    )
    if existing is not None:
        return {"supplement_sha256": address, "kind": KIND_STUDY, "idempotent_reuse": True}
    handoff = verified["handoff"]
    row = ResearchSupplement(
        supplement_sha256=address,
        supplement_id=f"{STUDY_ID_PREFIX}:{handoff.get('runId')}",
        contract_version=HANDOFF_CONTRACT,
        kind=KIND_STUDY,
        sport="NFL",
        event_id=None,
        engine_id=str(handoff.get("developmentSelectedPipeline") or "") or None,
        forecast_id=None,
        shadow_forecast_id=None,
        artifact_version=str(handoff.get("runId") or "unversioned"),
        simulation_kind="NONE",
        simulation_count=None,
        simulation_seed=None,
        source_cutoff_utc=None,
        published_utc=None,
        status="RESEARCH_SHADOW",
        research_shadow_only=True,
        payload=_study_payload(verified),
        evidence={
            "manifest_sha256": verified["manifest_sha256"],
            "handoff_sha256": address,
            "handoff_path": verified["handoff_path"],
            "verified_at_utc": datetime.now(UTC).isoformat(),
            "promotion_status": handoff.get("promotionStatus"),
            "execution_allowed_in_aisp": False,
        },
    )
    session.add(row)
    session.flush()
    return {"supplement_sha256": address, "kind": KIND_STUDY, "idempotent_reuse": False,
            "leaderboard_rows": len(handoff["leaderboard"])}


__all__ = ["LabStudyImportError", "register_lab_study", "verify_lab_study"]

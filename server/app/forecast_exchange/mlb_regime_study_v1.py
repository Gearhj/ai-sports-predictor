"""Read-only verification and append-only registration of MLB_REGIME_HYBRID_STUDY_V1.

WHAT THIS FAMILY IS
-------------------
A completed Model Studio *study*, not a forecast family. It publishes no
forecast (``forecastCount`` is 0), changes no official forecast
(``officialForecastsChanged`` is 0) and ships a joblib model that AISP must
never load. Its only privilege is to be *displayed* as research evidence
beside the official model.

The conclusion it carries is a negative one, and the display depends on that
staying negative: V18 is retained, the ensemble is a research shadow, the
residual specialists earned zero weight, and the Final OOT window is sealed
and unread. So this importer refuses -- before any byte is interpreted as
data -- any artifact that claims otherwise.

WHAT IS VERIFIED, IN ORDER, FAIL CLOSED
---------------------------------------
1.  The manifest is canonical JSON at the expected schema, and its bytes hash
    to the address the caller asked for.
2.  Its authority block is governance-clean: RESEARCH_SHADOW, NOT_PROMOTED,
    no wagering authority, no execution in AISP, Final OOT never accessed,
    zero forecasts published, zero official forecasts changed, and the global
    model still named as the accepted frozen V18 folds.
3.  Every file the manifest lists -- evidence, code references, the result and
    its predecessor -- exists and its bytes hash and length match exactly.
    That is ~350 MB of governed cache and fold atlases; it is hashed anyway,
    because a manifest that cannot be checked is a claim, not evidence.
4.  The result document is canonical JSON, agrees with the manifest on study
    identity, and repeats the same governance answers.
5.  THE HEADLINE ARITHMETIC IS RE-DERIVED FROM THE LEADERBOARD.
    The ensemble's advantage is 2.6 ten-thousandths of a nat. A transcription
    slip anywhere in that chain is invisible to the eye and reverses the sign
    of a claim. So the paired difference is recomputed as challenger minus
    champion within one phase, the published interval must bracket it, the
    ensemble weights must sum to one with an exactly zero specialist weight,
    the monthly slices must sum to the evaluation cohort, and each cluster's
    inside/outside split must sum to the evaluation observations.

WHAT IT MAY NOT DO
------------------
It executes nothing, promotes nothing, and writes exactly one
``ResearchSupplement`` row keyed by the manifest address. Re-running it is a
no-op.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import math
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.forecast_exchange.supplements import (
    KIND_STUDY, SupplementImportError, _iso, _load_canonical,
)
from app.gen2.exchange.canonical_json import canonical_json_bytes
from app.models import ResearchSupplement

MANIFEST = "MLB_REGIME_RESEARCH_PUBLICATION_MANIFEST_V1"
CONTRACT = "MLB_REGIME_HYBRID_STUDY_V1"
POINTER_NAME = "latest.json"

#: The retained official model. An artifact naming anything else is a
#: governance event, not an import.
GLOBAL_MODEL = "V18_ACCEPTED_FROZEN_FOLD_MODELS"
CHAMPION = "V18"
CHALLENGER = "RF_RESIDUAL_ENSEMBLE"
SPECIALIST_BRANCH = "QUALIFIED_RESIDUAL_SYSTEM"
#: The one phase both headline figures are read from. Mixing a calibrated
#: figure with a raw one reverses the sign of the difference.
HEADLINE_PHASE = "EVALUATION_CALIBRATED"
EVALUATION_PHASE = "evaluation"

_TOLERANCE = 1e-9


@dataclass(frozen=True)
class VerifiedRegimeStudy:
    """One byte-verified publication, reduced to what the page renders."""

    address: str
    manifest: dict[str, Any]
    result: dict[str, Any]
    files: dict[str, Any]
    summaries: dict[str, Any]

    @property
    def study_id(self) -> str:
        return str(self.manifest["studyId"])


# ---------------------------------------------------------------------------
# governance
# ---------------------------------------------------------------------------

def _authority(manifest: dict[str, Any]) -> dict[str, Any]:
    """Refuse anything that changes model identity, policy or authority."""

    authority = manifest.get("authority")
    if not isinstance(authority, dict):
        raise SupplementImportError("MLB regime publication declares no authority block")
    if authority.get("status") != "RESEARCH_SHADOW":
        raise SupplementImportError("MLB regime publication is not RESEARCH_SHADOW")
    if authority.get("promotionStatus") != "NOT_PROMOTED":
        raise SupplementImportError("MLB regime publication claims a promotion status")
    for flag in ("wageringAuthority", "executionAllowedInAISP", "finalOotAccessed"):
        if authority.get(flag) is not False:
            raise SupplementImportError(f"MLB regime publication must declare {flag}=false")
    if authority.get("globalModel") != GLOBAL_MODEL:
        raise SupplementImportError(
            f"MLB regime publication names global model {authority.get('globalModel')!r}, "
            f"not the retained {GLOBAL_MODEL}"
        )
    final_oot = authority.get("finalOot")
    if not isinstance(final_oot, list) or len(final_oot) != 2:
        raise SupplementImportError("MLB regime publication does not state its sealed Final OOT window")
    if int(manifest.get("forecastCount", -1)) != 0:
        raise SupplementImportError("MLB regime publication carries forecasts; a study may not")
    if int(manifest.get("officialForecastsChanged", -1)) != 0:
        raise SupplementImportError("MLB regime publication claims official forecasts changed")
    return authority


def _verified_file(reference: Any, *, label: str) -> bytes:
    if not isinstance(reference, dict):
        raise SupplementImportError(f"{label} is not a file reference")
    path = Path(str(reference.get("path", "")))
    if not path.is_file():
        raise SupplementImportError(f"{label} does not exist at its declared path")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != str(reference.get("sha256", "")).lower():
        raise SupplementImportError(f"{label} bytes do not hash to the declared checksum")
    if len(raw) != int(reference.get("byteCount", -1)):
        raise SupplementImportError(f"{label} byte count does not match the manifest")
    return raw


# ---------------------------------------------------------------------------
# arithmetic re-derivation
# ---------------------------------------------------------------------------

def _row(leaderboard: list[dict[str, Any]], phase: str, model: str) -> dict[str, Any]:
    matches = [r for r in leaderboard if r.get("phase") == phase and r.get("model") == model]
    if len(matches) != 1:
        raise SupplementImportError(f"leaderboard has {len(matches)} rows for {phase}/{model}, expected 1")
    return matches[0]


def _close(a: float, b: float, *, label: str, tol: float = _TOLERANCE) -> None:
    if not math.isclose(a, b, abs_tol=tol):
        raise SupplementImportError(f"{label}: {a!r} does not reconcile to {b!r}")


def _verify_arithmetic(result: dict[str, Any]) -> dict[str, Any]:
    """Recompute the four numbers the page states as fact.

    Returns the paired-difference block, already reconciled, so the caller
    cannot accidentally render a different one.
    """

    leaderboard = result.get("leaderboard")
    if not isinstance(leaderboard, list) or not leaderboard:
        raise SupplementImportError("MLB regime result has no leaderboard")

    champion = _row(leaderboard, HEADLINE_PHASE, CHAMPION)
    challenger = _row(leaderboard, HEADLINE_PHASE, CHALLENGER)
    if float(champion["deltaLogLoss"]) != 0.0:
        raise SupplementImportError("the champion row is not the leaderboard's own baseline")

    # Every delta on the board must be that row minus the champion of its own
    # phase. One row measured against another phase's baseline would be
    # invisible in the table and wrong by more than the whole effect.
    for row in leaderboard:
        base = _row(leaderboard, str(row["phase"]), CHAMPION)
        for metric, delta in (("logLoss", "deltaLogLoss"), ("brier", "deltaBrier")):
            _close(float(row[delta]), float(row[metric]) - float(base[metric]),
                   label=f"{row['phase']}/{row['model']} {delta}")

    paired = (result.get("uncertaintyIntervals") or {}).get(CHALLENGER)
    if not isinstance(paired, dict):
        raise SupplementImportError(f"MLB regime result publishes no interval for {CHALLENGER}")
    if not str(paired.get("direction", "")).startswith("CANDIDATE_MINUS_BASELINE"):
        raise SupplementImportError("paired interval is not stated as candidate minus baseline")

    difference = float(challenger["logLoss"]) - float(champion["logLoss"])
    _close(float(paired["estimate"]["logLoss"]), difference,
           label="published paired log-loss estimate")
    _close(float(challenger["deltaLogLoss"]), difference, label="leaderboard deltaLogLoss")

    cluster = paired.get("gameClusterBootstrap") or {}
    interval = ((cluster.get("intervals") or {}).get("logLoss") or {})
    lower, upper = float(interval["lower"]), float(interval["upper"])
    if not lower <= difference <= upper:
        raise SupplementImportError("the game-clustered interval does not bracket its own estimate")
    if float(interval.get("confidenceLevel", 0)) != 0.95:
        raise SupplementImportError("the published game-clustered interval is not 95%")

    # The ensemble is a convex mixture. Weights that do not sum to one are not
    # a mixture, and a nonzero specialist weight would contradict the whole
    # negative finding this page reports.
    composition = (result.get("selection") or {}).get("frozenEnsembleComposition") or {}
    order = list(composition.get("branchOrder") or [])
    weights = [float(w) for w in composition.get("savedWeights") or []]
    if len(order) != len(weights) or not weights:
        raise SupplementImportError("ensemble branch order and saved weights do not correspond")
    _close(sum(weights), 1.0, label="ensemble weights", tol=1e-9)
    if SPECIALIST_BRANCH not in order:
        raise SupplementImportError("ensemble composition does not name the residual specialist branch")
    if weights[order.index(SPECIALIST_BRANCH)] != 0.0:
        raise SupplementImportError(
            "the residual specialist branch carries nonzero weight; this publication's own "
            "conclusion is that it earned none"
        )
    if weights[order.index(CHAMPION)] < 0.5:
        raise SupplementImportError("the ensemble does not retain V18 as its majority branch")

    evaluation = next((p for p in (result.get("cohort") or {}).get("phases") or []
                       if p.get("phase") == EVALUATION_PHASE), None)
    if evaluation is None:
        raise SupplementImportError("MLB regime cohort declares no evaluation phase")
    if int(evaluation["observations"]) != int(champion["observations"]):
        raise SupplementImportError("the evaluation cohort and the leaderboard disagree on observations")
    if int(evaluation["games"]) != int(champion["games"]):
        raise SupplementImportError("the evaluation cohort and the leaderboard disagree on games")
    if int(paired.get("observations", -1)) != int(evaluation["observations"]):
        raise SupplementImportError("the paired interval was not computed on the evaluation cohort")

    monthly = result.get("monthlyEvaluation") or []
    if sum(int(m["observations"]) for m in monthly) != int(evaluation["observations"]):
        raise SupplementImportError("the monthly slices do not sum to the evaluation cohort")

    # Each cluster partitions the same evaluation population into inside and
    # outside. A split that does not add up means the heatmap denominators are
    # not the denominators the metrics were computed over.
    by_cluster: dict[str, int] = {}
    for entry in result.get("regimeMetrics") or []:
        if str(entry.get("scope")) not in ("INSIDE", "OUTSIDE"):
            raise SupplementImportError("a regime slice is neither inside nor outside its cluster")
        by_cluster[str(entry["clusterId"])] = by_cluster.get(str(entry["clusterId"]), 0) + int(entry["observations"])
    for cluster_id, total in sorted(by_cluster.items()):
        if total != int(evaluation["observations"]):
            raise SupplementImportError(
                f"cluster {cluster_id} inside+outside is {total}, not the evaluation cohort's "
                f"{evaluation['observations']}"
            )
    return paired


# ---------------------------------------------------------------------------
# display projection
# ---------------------------------------------------------------------------

def _leaderboard(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [{
        "phase": row["phase"], "model": row["model"],
        "logLoss": row["logLoss"], "brier": row["brier"],
        "deltaLogLoss": row["deltaLogLoss"], "deltaBrier": row["deltaBrier"],
        "entropy": row.get("entropy"), "temperature": row.get("temperature"),
        "observations": row["observations"], "games": row["games"],
    } for row in result["leaderboard"]]


def _calibration(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Only the two headline-phase rows, and only what a reliability plot needs."""

    out = []
    for entry in result.get("calibration") or []:
        if entry.get("phase") != HEADLINE_PHASE or entry.get("model") not in (CHAMPION, CHALLENGER):
            continue
        out.append({
            "model": entry["model"], "phase": entry["phase"],
            "logLoss": entry["logLoss"], "brier": entry["brier"],
            "brierDefinition": entry.get("brierDefinition"),
            "entropy": entry.get("entropy"), "entropyUnits": entry.get("entropyUnits"),
            "observations": entry["observations"],
            "meanMaximumProbability": entry.get("meanMaximumProbability"),
            "perClass": [{
                "className": cls["className"], "classIndex": cls["classIndex"],
                "observations": cls["observations"], "observedCount": cls.get("observedCount"),
                "observedRate": cls.get("observedRate"), "predictedRate": cls.get("predictedRate"),
                "rateBias": cls.get("rateBias"), "oneVsRestBrier": cls.get("oneVsRestBrier"),
                "slope": (cls.get("calibration") or {}).get("slope"),
                "intercept": (cls.get("calibration") or {}).get("intercept"),
                "calibrationScope": (cls.get("calibration") or {}).get("scope"),
                "reliability": [{
                    "bin": b["bin"], "lowerInclusive": b["lowerInclusive"], "upper": b["upper"],
                    "meanProbability": b["meanProbability"], "observedRate": b["observedRate"],
                    "observations": b["observations"],
                } for b in cls.get("reliability") or []],
            } for cls in entry.get("perClass") or []],
        })
    return out


def _regimes(result: dict[str, Any]) -> list[dict[str, Any]]:
    profiles = {str(p["clusterId"]): p for p in result.get("clusterProfiles") or []}
    out = []
    for entry in result.get("regimeMetrics") or []:
        profile = profiles.get(str(entry["clusterId"])) or {}
        out.append({
            "clusterId": entry["clusterId"], "scope": entry["scope"],
            "domain": profile.get("domain"),
            "observations": entry["observations"],
            "baselineLogLoss": entry["baseline"]["logLoss"],
            "challengerLogLoss": entry["challenger"]["logLoss"],
            "deltaLogLoss": entry["challenger"]["logLoss"] - entry["baseline"]["logLoss"],
            "baselineBrier": entry["baseline"]["brier"],
            "challengerBrier": entry["challenger"]["brier"],
            "deltaBrier": entry["challenger"]["brier"] - entry["baseline"]["brier"],
        })
    return out


def _cluster_profiles(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [{
        "clusterId": p["clusterId"], "domain": p.get("domain"),
        "interpretation": p.get("interpretation"), "recommendation": p.get("recommendation"),
        "stabilityARI": p.get("stabilityARI"),
        "hardInsidePAs": p.get("hardInsidePAs"), "observations": p.get("observations"),
        "games": p.get("games"), "hardInsideGames": p.get("hardInsideGames"),
        "effectivePAs": p.get("effectivePAs"),
        "globalLogLoss": p.get("globalLogLoss"),
        # The standalone specialist's own error, published separately from the
        # ensemble's, because the predecessor field conflated the two.
        "specialistLogLoss": p.get("specialistLogLoss"),
        "selectedEnsembleLogLoss": p.get("selectedEnsembleLogLoss"),
        "globalBrier": p.get("globalBrier"), "specialistBrier": p.get("specialistBrier"),
        "selectedEnsembleBrier": p.get("selectedEnsembleBrier"),
        "meanAdjustment": p.get("meanAdjustment"), "maximumAdjustment": p.get("maximumAdjustment"),
        "selectedEnsembleMeanAdjustment": p.get("selectedEnsembleMeanAdjustment"),
        "features": p.get("features") or [],
    } for p in result.get("clusterProfiles") or []]


def _summaries(manifest: dict[str, Any], result: dict[str, Any], paired: dict[str, Any]) -> dict[str, Any]:
    champion = _row(result["leaderboard"], HEADLINE_PHASE, CHAMPION)
    challenger = _row(result["leaderboard"], HEADLINE_PHASE, CHALLENGER)
    composition = (result.get("selection") or {}).get("frozenEnsembleComposition") or {}
    order = list(composition.get("branchOrder") or [])
    weights = [float(w) for w in composition.get("savedWeights") or []]
    authority = manifest["authority"]
    evaluation = next(p for p in result["cohort"]["phases"] if p["phase"] == EVALUATION_PHASE)
    return {
        "schemaVersion": "AISP_MLB_REGIME_STUDY_SUMMARY_V1",
        "studyId": manifest["studyId"],
        "contract": result.get("contract"),
        "publicationRevision": result.get("publicationRevision"),
        "verdict": result.get("verdict"),
        "conclusion": (result.get("selection") or {}).get("conclusion"),
        "recommendedAction": manifest.get("recommendedAction"),
        "authority": {
            "status": authority["status"], "promotionStatus": authority["promotionStatus"],
            "wageringAuthority": authority["wageringAuthority"],
            "executionAllowedInAISP": authority["executionAllowedInAISP"],
            "finalOot": authority["finalOot"], "finalOotAccessed": authority["finalOotAccessed"],
            "globalModel": authority["globalModel"],
            "classOrder": authority.get("classOrder") or [],
            "sourceUseRestrictions": authority.get("sourceUseRestrictions") or [],
            "treatmentManifestSha256": authority.get("treatmentManifestSha256"),
        },
        "champion": {
            "modelId": CHAMPION, "role": "OFFICIAL",
            "logLoss": champion["logLoss"], "brier": champion["brier"],
            "temperature": champion.get("temperature"),
        },
        "challenger": {
            "modelId": CHALLENGER, "role": "RESEARCH_SHADOW",
            "logLoss": challenger["logLoss"], "brier": challenger["brier"],
            "temperature": challenger.get("temperature"),
        },
        "ensembleComposition": {
            "branchOrder": order, "savedWeights": weights,
            "weightsByBranch": dict(zip(order, weights, strict=True)),
            "specialistWeight": weights[order.index(SPECIALIST_BRANCH)],
            "basis": (result.get("selection") or {}).get("basis"),
            "interpretation": composition.get("interpretation"),
            "capOrSimplexModifiedPAs": composition.get("capOrSimplexModifiedPAs"),
            "fitReceipt": composition.get("fitReceipt"),
        },
        "cohort": {
            "phases": result["cohort"]["phases"],
            "evaluation": evaluation,
            "seasons": result["cohort"].get("seasons") or [],
            "eligibleDevelopmentPAs": result["cohort"].get("eligibleDevelopmentPAs"),
            "excludedOofPAs": result["cohort"].get("excludedOofPAs"),
            "oofGames": result["cohort"].get("oofGames"),
            "oofPAs": result["cohort"].get("oofPAs"),
            "registeredOofPAs": result["cohort"].get("registeredOofPAs"),
            "metricDirection": "LOWER_IS_BETTER",
        },
        "pairedDifference": {
            "metric": "logLoss",
            "estimate": paired["estimate"],
            "direction": paired["direction"],
            "games": paired.get("games"), "observations": paired.get("observations"),
            "dates": paired.get("dates"),
            "startDate": paired.get("startDate"), "endDate": paired.get("endDate"),
            "gameClusterBootstrap": paired.get("gameClusterBootstrap"),
            "calendarWeekBlockBootstrap": paired.get("calendarWeekBlockBootstrap"),
            "limitations": paired.get("limitations") or [],
        },
        "leaderboard": _leaderboard(result),
        "monthlyEvaluation": result.get("monthlyEvaluation") or [],
        "regimeMetrics": _regimes(result),
        "clusterProfiles": _cluster_profiles(result),
        "calibration": _calibration(result),
        "reportingDefinitions": result.get("reportingDefinitions") or {},
        "limitations": result.get("limitations") or [],
        "verification": result.get("verification") or {},
        "startedAtUtc": result.get("startedAtUtc"),
        "completedAtUtc": result.get("completedAtUtc"),
    }


# ---------------------------------------------------------------------------
# verification entry points
# ---------------------------------------------------------------------------

def resolve_manifest(root: Path) -> Path:
    """The publication directory's own pointer, verified by bytes."""

    pointer_path = Path(root) / POINTER_NAME
    pointer = _load_canonical(pointer_path, label=f"MLB regime {POINTER_NAME}")
    reference = pointer.get("manifest") or {}
    _verified_file(reference, label="publication-manifest.json")
    return Path(str(reference["path"]))


def verify_regime_study(manifest_path: Path, *, expected_sha256: str | None = None) -> VerifiedRegimeStudy:
    manifest_path = Path(manifest_path)
    raw = manifest_path.read_bytes()
    address = hashlib.sha256(raw).hexdigest()
    if expected_sha256 and address != str(expected_sha256).lower():
        raise SupplementImportError("publication manifest does not hash to the expected address")

    manifest = _load_canonical(manifest_path, label="publication-manifest.json")
    if manifest.get("schemaVersion") != MANIFEST:
        raise SupplementImportError("unsupported MLB regime publication manifest schema")
    _authority(manifest)
    for key in ("studyId", "result", "predecessor", "publicationCreatedAtUtc", "researchCompletedAtUtc"):
        if not manifest.get(key):
            raise SupplementImportError(f"publication manifest is missing '{key}'")
    _iso(manifest["publicationCreatedAtUtc"], label="publicationCreatedAtUtc")
    _iso(manifest["researchCompletedAtUtc"], label="researchCompletedAtUtc")

    # Hash EVERY listed byte before any of it is read as data.
    listed = list(manifest.get("files") or [])
    references = list(manifest.get("codeReferences") or [])
    if not listed:
        raise SupplementImportError("publication manifest lists no files")
    for item in listed + references:
        _verified_file(item, label=str(item.get("label") or item.get("path")))
    _verified_file(manifest["predecessor"], label="predecessor study-result.json")
    result_raw = _verified_file(manifest["result"], label="study-result.json")

    import json as _json
    result = _json.loads(result_raw.decode("utf-8"))
    if result_raw != canonical_json_bytes(result):
        raise SupplementImportError("study-result.json is not encoded with canonical exchange JSON")
    if result.get("contract") != CONTRACT:
        raise SupplementImportError("study-result.json is not the supported study contract")
    if result.get("studyId") != manifest["studyId"]:
        raise SupplementImportError("study-result.json and the manifest disagree on study identity")
    if result.get("status") != "COMPLETED":
        raise SupplementImportError("study-result.json is not a completed study")
    if result.get("promotionStatus") not in (None, "NOT_PROMOTED"):
        raise SupplementImportError("study-result.json claims a promotion status")
    if result.get("wageringAuthority") not in (None, False):
        raise SupplementImportError("study-result.json claims wagering authority")
    if result.get("finalOotAccessed") not in (None, False):
        raise SupplementImportError("study-result.json claims Final OOT was accessed")

    paired = _verify_arithmetic(result)
    files = {
        "manifest": {"sha256": address, "byteCount": len(raw), "path": str(manifest_path)},
        "result": {k: manifest["result"][k] for k in ("sha256", "byteCount", "path")},
        "predecessor": {k: manifest["predecessor"][k] for k in ("sha256", "byteCount", "path")},
        "evidence": [{"label": f.get("label"), "category": f.get("category"),
                      "sha256": f["sha256"], "byteCount": f["byteCount"]} for f in listed],
        "codeReferences": [{"label": f.get("label"), "sha256": f["sha256"],
                            "byteCount": f["byteCount"]} for f in references],
    }
    return VerifiedRegimeStudy(
        address=address, manifest=manifest, result=result, files=files,
        summaries=_summaries(manifest, result, paired),
    )


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------

def register_regime_study(
    session: Session, verified: VerifiedRegimeStudy, *, dry_run: bool = False,
) -> dict[str, Any]:
    """One append-only ResearchSupplement row, idempotent by manifest address."""

    existing = session.scalar(select(ResearchSupplement).where(
        ResearchSupplement.supplement_sha256 == verified.address))
    record = {
        "supplement_sha256": verified.address, "kind": KIND_STUDY, "sport": "MLB",
        "study_id": verified.study_id, "idempotent_reuse": existing is not None,
    }
    if existing is not None or dry_run:
        return record
    manifest = verified.manifest
    session.add(ResearchSupplement(
        supplement_sha256=verified.address,
        supplement_id=f"mlb-regime-hybrid:sha256:{verified.address}",
        contract_version=CONTRACT, kind=KIND_STUDY, sport="MLB",
        event_id=None, engine_id=None, forecast_id=None, shadow_forecast_id=None,
        artifact_version=verified.study_id,
        # A study runs no simulation. Saying NONE is the contract's own answer;
        # inventing a draw count would make it look like one did.
        simulation_kind="NONE", simulation_count=None, simulation_seed=None,
        source_cutoff_utc=_iso(manifest["researchCompletedAtUtc"], label="researchCompletedAtUtc"),
        published_utc=_iso(manifest["publicationCreatedAtUtc"], label="publicationCreatedAtUtc"),
        status="RESEARCH_SHADOW", research_shadow_only=True,
        payload={"summaries": verified.summaries,
                 "producer": {"manifestSchema": MANIFEST, "studyId": verified.study_id}},
        evidence={"files": verified.files, "manifest_schema": MANIFEST,
                  "authority": manifest["authority"],
                  "recommended_action": manifest.get("recommendedAction"),
                  "official_forecasts_changed": manifest.get("officialForecastsChanged"),
                  "forecast_count": manifest.get("forecastCount"),
                  "verified_at_utc": datetime.now(UTC).isoformat()},
    ))
    session.flush()
    return record


__all__ = [
    "CHALLENGER", "CHAMPION", "CONTRACT", "GLOBAL_MODEL", "HEADLINE_PHASE", "MANIFEST",
    "SPECIALIST_BRANCH", "VerifiedRegimeStudy", "register_regime_study", "resolve_manifest",
    "verify_regime_study",
]

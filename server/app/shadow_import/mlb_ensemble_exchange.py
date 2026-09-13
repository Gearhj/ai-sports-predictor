"""Consume Model Studio's real ensemble release-candidate exchange.

WHY THIS EXISTS BESIDE THE GENERIC CONTRACT
-------------------------------------------
``mlb_ensemble_candidate`` defines the forward contract AISP asked for
(``AISP_MLB_ENSEMBLE_CANDIDATE_HANDOFF_V1``). Model Studio publishes its own,
richer shape: ``MLB_V18_ENSEMBLE_RELEASE_CANDIDATE_V1``, reached through an
exchange pointer and a research handoff. Rather than ask the producer to
re-publish, this module adapts the real shape into the same verified record and
reuses the same registration, the same engine id and the same replay semantics.

It is an adapter, not a second importer: nothing here writes rows itself, and
the binding rules below are the same questions the generic contract asks, put
to the field names Studio actually publishes.

WHAT IS VERIFIED, AND WHAT IS ONLY RECORDED
-------------------------------------------
Two different claims, never blurred:

*   **Verified in AISP.** The exchange pointer, handoff and package each hash to
    their declared digest; every object the package references is present in the
    exchange and hashes correctly; and the baseline the package names agrees,
    value for value, with the V18 forecast AISP already stores for that game --
    estimator artifact, parent bundle, simulator identity and version, master
    seed, simulation count, source evidence cutoff, canonical game, orientation
    and scheduled start.

*   **Producer-declared, recorded not verified.** Digests for artifacts AISP has
    never held -- the transition model, the feature-treatment manifest, the
    frozen prior and semantic checksums. AISP has no counterpart to compare them
    against, so they are stored verbatim and labelled as declared. Calling them
    "verified" would be a lie that a reader could not detect.

FOUR CONFIGURATIONS, ONE FORECAST
---------------------------------
The package's release default is ``A_FROZEN_V18_BASELINE_HOOK``: calibration
disabled by its own selection gate, starter duration shadow-only and
unassessed, no matchup specialist. So the candidate's shipped numbers are
unchanged V18, and B/C/D are retrospective comparison configurations. One
candidate forecast is registered, carrying all four configurations as evidence.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Game, ShadowForecast, ShadowForecastBundle
from app.shadow_import.mlb_ensemble_candidate import (
    ENGINE_ID, HISTORICAL_REPLAY, PROSPECTIVE, STATUS_BY_TYPE,
    BaselineBindingError, EnsembleCandidateImportError, _mismatch,
)
from app.shadow_import.mlb_v18 import ENGINE_ID as BASELINE_ENGINE_ID

POINTER_SCHEMA = "MLB_V18_ENSEMBLE_RESEARCH_EXCHANGE_CURRENT_V1"
HANDOFF_SCHEMA = "AISP_MLB_V18_ENSEMBLE_RESEARCH_HANDOFF_V1"
PACKAGE_SCHEMA = "MLB_V18_ENSEMBLE_RELEASE_CANDIDATE_V1"
COMPARISON_SCHEMA = "MLB_V18_ENSEMBLE_RC_GAME_COMPARISON_V1"
CONTRACT_VERSION = "AISP_MLB_V18_ENSEMBLE_RESEARCH_EXCHANGE_V1"

#: The package's own label for a replay. Anything else is refused here: this
#: adapter exists for the published research candidate, and a package claiming
#: to be a forecast needs the prospective path's timing checks, not this one.
REPLAY_FORECAST_TYPE = "LABELED_HISTORICAL_REPLAY_ONLY"


@dataclass(frozen=True)
class VerifiedExchangeCandidate:
    pointer_path: Path
    pointer: dict[str, Any]
    handoff: dict[str, Any]
    package: dict[str, Any]
    comparison: dict[str, Any]
    package_sha256: str
    handoff_sha256: str
    verified_objects: dict[str, str]
    external_objects: dict[str, str]

    @property
    def candidate_id(self) -> str:
        return str(self.package["candidateId"])

    @property
    def artifact_type(self) -> str:
        return (HISTORICAL_REPLAY
                if self.package.get("forecastType") == REPLAY_FORECAST_TYPE
                else PROSPECTIVE)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _object_file(root: Path, digest: str) -> Path:
    """The single file stored under objects/sha256/<digest>/."""

    folder = root / "objects" / "sha256" / digest
    if not folder.is_dir():
        raise EnsembleCandidateImportError(
            f"the exchange holds no object directory for {digest[:16]}..."
        )
    files = sorted(child for child in folder.iterdir() if child.is_file())
    if len(files) != 1:
        raise EnsembleCandidateImportError(
            f"object {digest[:16]}... holds {len(files)} files; expected exactly one"
        )
    return files[0]


def _verified_object(root: Path, digest: str, label: str) -> tuple[Path, str]:
    path = _object_file(root, digest)
    actual = _sha256_file(path)
    if actual != digest:
        raise EnsembleCandidateImportError(
            f"{label} fails its checksum: declared {digest[:16]}..., read {actual[:16]}..."
        )
    return path, actual


def _aware(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise EnsembleCandidateImportError("a required timestamp is missing")
    normalised = text.replace("Z", "+00:00").replace(" ", "T", 1)
    try:
        moment = datetime.fromisoformat(normalised)
    except ValueError as exc:
        raise EnsembleCandidateImportError(f"unparseable timestamp {text!r}") from exc
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


# ------------------------------------------------------------ verification --

def verify_exchange(pointer_path: str | Path) -> VerifiedExchangeCandidate:
    """Walk pointer -> handoff -> package -> objects, hashing every step."""

    pointer_path = Path(pointer_path)
    root = pointer_path.parent
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    if pointer.get("schemaVersion") != POINTER_SCHEMA:
        raise EnsembleCandidateImportError(
            f"exchange pointer declares {pointer.get('schemaVersion')!r}, "
            f"expected {POINTER_SCHEMA!r}"
        )
    # Safety flags on the pointer, before anything is opened.
    for flag in ("executionAllowedInAISP", "wageringAuthority"):
        if pointer.get(flag) is not False:
            raise EnsembleCandidateImportError(
                f"exchange pointer declares {flag}={pointer.get(flag)!r}; refused"
            )
    if pointer.get("researchShadowOnly") is not True:
        raise EnsembleCandidateImportError("exchange pointer is not researchShadowOnly")

    handoff_sha = str((pointer.get("handoff") or {}).get("sha256") or "")
    package_sha = str((pointer.get("package") or {}).get("sha256") or "")
    handoff_path, _ = _verified_object(root, handoff_sha, "research handoff")
    package_path, _ = _verified_object(root, package_sha, "candidate package")

    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    if handoff.get("schemaVersion") != HANDOFF_SCHEMA:
        raise EnsembleCandidateImportError(
            f"research handoff declares {handoff.get('schemaVersion')!r}, "
            f"expected {HANDOFF_SCHEMA!r}"
        )
    if str((handoff.get("package") or {}).get("sha256")) != package_sha:
        raise EnsembleCandidateImportError(
            "the research handoff names a different package than the exchange pointer"
        )
    for flag in ("executionAllowedInAISP", "wageringAuthority"):
        if handoff.get(flag) is not False:
            raise EnsembleCandidateImportError(f"research handoff declares {flag}; refused")
    if handoff.get("researchShadowOnly") is not True:
        raise EnsembleCandidateImportError("research handoff is not researchShadowOnly")
    if handoff.get("promotionStatus") != "NOT_PROMOTED":
        raise EnsembleCandidateImportError(
            f"research handoff declares promotionStatus "
            f"{handoff.get('promotionStatus')!r}; AISP imports research only"
        )

    package = json.loads(package_path.read_text(encoding="utf-8"))
    if package.get("schemaVersion") != PACKAGE_SCHEMA:
        raise EnsembleCandidateImportError(
            f"candidate package declares {package.get('schemaVersion')!r}, "
            f"expected {PACKAGE_SCHEMA!r}"
        )
    for flag in ("executionAllowedInAISP", "wageringAuthority"):
        if package.get(flag) is not False:
            raise EnsembleCandidateImportError(f"candidate package declares {flag}; refused")
    if package.get("researchShadowOnly") is not True:
        raise EnsembleCandidateImportError("candidate package is not researchShadowOnly")
    if package.get("promotionStatus") != "NOT_PROMOTED":
        raise EnsembleCandidateImportError("candidate package claims promotion; refused")
    if package.get("officialV18Changed") is not False:
        raise EnsembleCandidateImportError(
            "candidate package declares officialV18Changed; refused"
        )
    if str(handoff.get("candidateId")) != str(package.get("candidateId")):
        raise EnsembleCandidateImportError("handoff and package name different candidates")

    # Every artifact the package points at must be in the exchange and hash
    # correctly. Checked before any number inside them is read.
    verified: dict[str, str] = {}
    external: dict[str, str] = {}
    v18 = package.get("components", {}).get("v18") or {}
    # Must be in THIS exchange: the package's own documents and the artifacts it
    # publishes itself.
    required = {
        "candidate package": package_sha,
        "research handoff": handoff_sha,
        "protocol": (package.get("protocol") or {}).get("sha256"),
        "game comparison": (package.get("comparison", {}).get("game") or {}).get("sha256"),
        "configuration table": (
            package.get("comparison", {}).get("configurationTable") or {}).get("sha256"),
        "calibration comparison artifact": (
            (package.get("components", {}).get("calibration") or {})
            .get("comparisonArtifact") or {}).get("sha256"),
    }
    for label, digest in required.items():
        if not digest:
            continue
        _verified_object(root, str(digest), label)
        verified[label] = str(digest)

    # Artifacts owned by another research stream -- the starter-duration pilot
    # publishes its own fitted hazard in its own exchange. AISP records the
    # digest the package names and does NOT reach into another owner's tree to
    # bless it. It is declared, not verified, and labelled that way.
    for label, digest in {
        "starter-duration fitted hazard": (
            (package.get("components", {}).get("starterDuration") or {})
            .get("artifact") or {}).get("sha256"),
        "V18 PA estimator artifact": (v18.get("paArtifact") or {}).get("sha256"),
        "V18 transition artifact": (v18.get("transitionArtifact") or {}).get("sha256"),
    }.items():
        if not digest:
            continue
        folder = root / "objects" / "sha256" / str(digest)
        if folder.is_dir():
            _verified_object(root, str(digest), label)
            verified[label] = str(digest)
        else:
            external[label] = str(digest)

    comparison_sha = str(
        (package.get("comparison", {}).get("game") or {}).get("sha256") or "")
    comparison_path, _ = _verified_object(root, comparison_sha, "game comparison")
    comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
    if comparison.get("schemaVersion") != COMPARISON_SCHEMA:
        raise EnsembleCandidateImportError(
            f"game comparison declares {comparison.get('schemaVersion')!r}, "
            f"expected {COMPARISON_SCHEMA!r}"
        )
    if package.get("forecastType") != REPLAY_FORECAST_TYPE:
        raise EnsembleCandidateImportError(
            f"this adapter consumes {REPLAY_FORECAST_TYPE}; the package declares "
            f"{package.get('forecastType')!r}. A prospective package must go through "
            "the prospective path, which applies the publication-timing checks."
        )
    if comparison.get("forecast") is not False:
        raise EnsembleCandidateImportError(
            "game comparison claims to be a forecast while the package is labelled a replay"
        )
    if not str((v18.get("paArtifact") or {}).get("sha256") or ""):
        raise EnsembleCandidateImportError("the package names no V18 PA estimator artifact")

    return VerifiedExchangeCandidate(
        pointer_path=pointer_path, pointer=pointer, handoff=handoff, package=package,
        comparison=comparison, package_sha256=package_sha, handoff_sha256=handoff_sha,
        verified_objects=verified, external_objects=external,
    )


# ---------------------------------------------------------------- binding --

def bind_baseline(
    session: Session, verified: VerifiedExchangeCandidate,
) -> tuple[ShadowForecast, dict[str, Any]]:
    """Agree the package's baseline with the V18 forecast AISP already stores.

    Returns the stored baseline row and a record of what was checked against
    what -- including, explicitly, the digests AISP could not check.
    """

    package, comparison = verified.package, verified.comparison
    v18 = package.get("components", {}).get("v18") or {}
    game_id = str(comparison.get("gameId") or "")
    try:
        canonical = uuid.UUID(game_id)
    except ValueError as exc:
        raise BaselineBindingError(f"comparison gameId {game_id!r} is not a uuid") from exc

    stored = session.scalar(
        select(ShadowForecast).where(
            ShadowForecast.engine_id == BASELINE_ENGINE_ID,
            ShadowForecast.canonical_game_id == canonical,
        ).order_by(ShadowForecast.available_as_of_utc.desc()).limit(1)
    )
    if stored is None:
        raise BaselineBindingError(
            f"AISP holds no V18 forecast for canonical game {game_id}; the candidate's "
            "baseline cannot be verified"
        )

    # --- identity -------------------------------------------------------
    if str(stored.source_event_id) != str(comparison.get("sourceGameId")):
        raise _mismatch("gamePk", comparison.get("sourceGameId"), stored.source_event_id)
    teams = comparison.get("teams") or {}
    for side, column, label in (("HOME", "home_team_id", "home team"),
                                ("AWAY", "away_team_id", "away team")):
        declared = str((teams.get(side) or {}).get("uuid") or "")
        if declared != str(getattr(stored, column)):
            raise BaselineBindingError(
                f"orientation disagrees: package puts {(teams.get(side) or {}).get('abbreviation')} "
                f"({declared[:8]}) at {side}, AISP stores {str(getattr(stored, column))[:8]}"
            )
    stored_start = stored.scheduled_start_utc
    if stored_start is not None:
        stored_start = stored_start if stored_start.tzinfo else stored_start.replace(tzinfo=UTC)
        if stored_start != _aware(comparison.get("scheduledStartUtc")):
            raise _mismatch("forecast horizon (scheduled start)",
                            comparison.get("scheduledStartUtc"), stored_start.isoformat())

    # --- the frozen system ----------------------------------------------
    if str((v18.get("paArtifact") or {}).get("sha256")) != str(stored.model_artifact_sha256):
        raise _mismatch("PA estimator artifact",
                        (v18.get("paArtifact") or {}).get("sha256"), stored.model_artifact_sha256)
    lineage = stored.lineage_detail or {}
    if str(v18.get("parentBundleSha256")) != str(lineage.get("parentBundleSha256")):
        raise _mismatch("parent V18 bundle digest",
                        v18.get("parentBundleSha256"), lineage.get("parentBundleSha256"))
    if v18.get("preprocessorAndEncoderPreservedInsidePaArtifact") is not True:
        raise BaselineBindingError(
            "the package does not state that the fitted preprocessor and encoder are "
            "preserved inside the PA artifact, so binding the PA digest would not bind "
            "the preprocessor"
        )
    if v18.get("fitCount") != 0:
        raise BaselineBindingError(
            f"the package reports fitCount={v18.get('fitCount')!r}; the baseline must be "
            "frozen, not re-fitted"
        )

    # --- simulation identity and clocks ---------------------------------
    stored_count = (stored.uncertainty or {}).get("simulationCount")
    if comparison.get("simulationCountPerConfiguration") != stored_count:
        raise _mismatch("simulation count",
                        comparison.get("simulationCountPerConfiguration"), stored_count)
    if comparison.get("masterSeed") != stored.simulator_seed:
        raise _mismatch("master seed", comparison.get("masterSeed"), stored.simulator_seed)
    capture = (comparison.get("paReload") or {}).get("archivedPregameCapture")
    stored_cutoff = stored.source_cutoff_utc
    if stored_cutoff is not None:
        stored_cutoff = stored_cutoff if stored_cutoff.tzinfo else stored_cutoff.replace(tzinfo=UTC)
        if _aware(capture) != stored_cutoff:
            raise _mismatch("source evidence cutoff", capture, stored_cutoff.isoformat())

    # --- the release default must reproduce the stored baseline ----------
    # A is declared to be unchanged V18. If it does not reproduce the numbers
    # AISP already holds, then "unchanged" is false and the package is refused.
    default_config = str(package.get("defaultConfiguration") or "")
    results = comparison.get("configurationResults") or {}
    default_result = results.get(default_config)
    if default_result is None:
        raise BaselineBindingError(
            f"the package's defaultConfiguration {default_config!r} has no result in the "
            "game comparison"
        )
    for label, declared, stored_value in (
        ("home win probability", default_result["homeWin"]["mean"],
         stored.full_home_win_probability),
        ("expected home runs", default_result["homeRuns"]["mean"],
         stored.full_expected_home_score),
        ("expected away runs", default_result["awayRuns"]["mean"],
         stored.full_expected_away_score),
    ):
        if round(float(declared), 4) != round(float(stored_value), 4):
            raise BaselineBindingError(
                f"the release-default configuration does not reproduce AISP's stored V18 "
                f"{label}: package {declared}, AISP {stored_value}. The package declares "
                "the default is unchanged V18; it is not."
            )

    record = {
        "verifiedInAisp": {
            "paArtifactSha256": stored.model_artifact_sha256,
            "parentBundleSha256": lineage.get("parentBundleSha256"),
            "acceptanceProtocolV2Sha256": lineage.get("acceptanceProtocolV2Sha256"),
            "simulatorId": stored.simulator_id,
            "simulatorVersion": stored.simulator_version,
            "masterSeed": stored.simulator_seed,
            "simulationCount": stored_count,
            "sourceCutoffUtc": stored_cutoff.isoformat() if stored_cutoff else None,
            "canonicalGameId": str(stored.canonical_game_id),
            "sourceGameId": stored.source_event_id,
            "homeTeamId": str(stored.home_team_id),
            "awayTeamId": str(stored.away_team_id),
            "scheduledStartUtc": stored_start.isoformat() if stored_start else None,
            "baselineForecastId": stored.forecast_id,
            "releaseDefaultReproducesStoredBaseline": True,
            "preprocessorInsidePaArtifact": True,
            "fitCount": 0,
        },
        "producerDeclaredNotVerifiableInAisp": {
            "note": "AISP holds no counterpart for these digests, so they are recorded "
                    "verbatim and not described as verified.",
            "transitionArtifactSha256": (v18.get("transitionArtifact") or {}).get("sha256"),
            "transitionFold": v18.get("transitionFold"),
            "treatmentManifestSha256": v18.get("treatmentManifestSha256"),
            "treatmentSpecificationId": v18.get("treatmentSpecificationId"),
            "frozenPriorChecksum": v18.get("frozenPriorChecksum"),
            "frozenSemanticChecksum": v18.get("frozenSemanticChecksum"),
            "paClassOrder": v18.get("paClassOrder"),
            "rawInputCount": v18.get("rawInputCount"),
            "treatedInputCount": v18.get("treatedInputCount"),
            "paFold": v18.get("paFold"),
        },
    }
    return stored, record


# ----------------------------------------------------------- registration --

def register_exchange(
    session: Session, verified: VerifiedExchangeCandidate, *, now: datetime | None = None,
) -> dict[str, Any]:
    """Register the candidate. Idempotent on the package digest."""

    now = now or datetime.now(UTC)
    package, comparison = verified.package, verified.comparison
    baseline, binding = bind_baseline(session, verified)
    artifact_type = verified.artifact_type
    forecast_id = f"mlb-v18-ensemble-rc:{verified.package_sha256}"

    bundle = session.scalar(select(ShadowForecastBundle).where(
        ShadowForecastBundle.bundle_sha256 == verified.package_sha256))
    created = False
    if bundle is None:
        bundle = ShadowForecastBundle(
            bundle_sha256=verified.package_sha256,
            bundle_id=verified.candidate_id,
            logical_uri=verified.pointer_path.as_uri(),
            source_fixture_id=str(package.get("head") or ""),
            slate_date=_aware(comparison["scheduledStartUtc"]).date(),
            league="MLB", sport="MLB",
            status=STATUS_BY_TYPE[artifact_type],
            research_shadow_only=True,
            generated_at=_aware(package["createdAt"]),
            imported_at=now,
            forecast_count=1, quote_count=0, candidate_count=1,
            contract_version=CONTRACT_VERSION,
            evidence={
                "candidateId": verified.candidate_id,
                "artifactType": artifact_type,
                "forecastType": package.get("forecastType"),
                "packageSha256": verified.package_sha256,
                "handoffSha256": verified.handoff_sha256,
                "protocolSha256": (package.get("protocol") or {}).get("sha256"),
                "verifiedObjects": verified.verified_objects,
                "externalObjectsDeclaredNotVerified": verified.external_objects,
                "components": package.get("components"),
                "defaultConfiguration": package.get("defaultConfiguration"),
                "gameComparisonStatus": package.get("gameComparisonStatus"),
                "promotionStatus": package.get("promotionStatus"),
                "officialV18Changed": False,
                "executionAllowed": False,
                "wageringAuthority": False,
                "activatesModel": False,
                "baselineSystemBinding": binding,
            },
        )
        session.add(bundle)
        session.flush()
        created = True

    existing = session.scalar(select(ShadowForecast).where(
        ShadowForecast.forecast_id == forecast_id))
    if existing is not None:
        session.flush()
        return {"imported": False, "reused": True, "bundle_created": False,
                "forecast_id": forecast_id, "artifact_type": artifact_type,
                "shadow_forecast_id": str(existing.id),
                "baseline_forecast_id": baseline.forecast_id}

    results = comparison["configurationResults"]
    default_config = str(package["defaultConfiguration"])
    default_result = results[default_config]
    home_win = float(default_result["homeWin"]["mean"])
    scheduled = _aware(comparison["scheduledStartUtc"])

    row = ShadowForecast(
        bundle_id=bundle.id,
        forecast_id=forecast_id,
        engine_id=ENGINE_ID,
        engine_version=verified.candidate_id,
        model_artifact_sha256=verified.package_sha256,
        model_manifest_sha256=(package.get("protocol") or {}).get("sha256"),
        feature_vector_sha256=baseline.feature_vector_sha256,
        simulator_id=baseline.simulator_id,
        simulator_version=baseline.simulator_version,
        simulator_seed=int(comparison["masterSeed"]),
        event_id=f"ensemble-rc-{comparison['sourceGameId']}",
        source_event_id=str(comparison["sourceGameId"]),
        event_date=scheduled.date(),
        season=baseline.season,
        canonical_game_id=baseline.canonical_game_id,
        home_team_id=str(baseline.home_team_id),
        away_team_id=str(baseline.away_team_id),
        snapshot_type=artifact_type,
        snapshot_version=1,
        forecast_schema_version=PACKAGE_SCHEMA,
        status=STATUS_BY_TYPE[artifact_type],
        is_ready=False,
        readiness_score=0.0, readiness_threshold=1.0,
        scheduled_start_utc=scheduled,
        # A replay is published AFTER the game. The clocks say so plainly; the
        # scorer refuses it on status regardless.
        available_as_of_utc=_aware(package["createdAt"]),
        source_cutoff_utc=baseline.source_cutoff_utc,
        forecast_cutoff_utc=_aware(package["createdAt"]),
        full_home_win_probability=home_win,
        full_away_win_probability=round(1.0 - home_win, 6),
        full_expected_home_score=float(default_result["homeRuns"]["mean"]),
        full_expected_away_score=float(default_result["awayRuns"]["mean"]),
        distributions={},
        uncertainty={
            "simulationCount": int(comparison["simulationCountPerConfiguration"]),
            "monteCarloStandardError": default_result["homeWin"]["monteCarloStandardError"],
            "illegalStateCount": default_result.get("illegalStateCount"),
            "nonterminationCount": default_result.get("nonterminationCount"),
        },
        readiness_detail={
            "artifactType": artifact_type,
            "eligibility": {
                "eligible": False,
                "reason": "LABELED_HISTORICAL_REPLAY_ONLY",
            },
            "componentsApplied": _components_applied(package),
        },
        identity_evidence={
            "candidateId": verified.candidate_id,
            "packageSha256": verified.package_sha256,
            "sourceGameId": str(comparison["sourceGameId"]),
            "baselineForecastId": baseline.forecast_id,
        },
        lineage_detail={
            "artifactType": artifact_type,
            "candidateId": verified.candidate_id,
            "packageSha256": verified.package_sha256,
            "handoffSha256": verified.handoff_sha256,
            "protocolSha256": (package.get("protocol") or {}).get("sha256"),
            "baselineForecastId": baseline.forecast_id,
            "baselineShadowForecastId": str(baseline.id),
            "baselineBinding": binding,
            "components": package.get("components"),
            "componentsApplied": _components_applied(package),
            "defaultConfiguration": default_config,
            "configurations": _configurations(package, comparison),
            "comparisonStatus": package.get("gameComparisonStatus"),
            "limitations": comparison.get("limitations"),
            "replayGeneratedAfterGameStart": comparison.get("replayGeneratedAfterGameStart"),
            "selectionUse": comparison.get("selectionUse"),
            "executionAllowed": False,
            "wageringAuthority": False,
            "activatesModel": False,
        },
    )
    session.add(row)
    session.flush()
    return {"imported": True, "reused": False, "bundle_created": created,
            "forecast_id": forecast_id, "artifact_type": artifact_type,
            "shadow_forecast_id": str(row.id),
            "baseline_forecast_id": baseline.forecast_id,
            "configurations": len(results)}


def _components_applied(package: dict[str, Any]) -> list[dict[str, Any]]:
    """What each component did in the RELEASE default, with its own reason."""

    components = package.get("components") or {}
    calibration = components.get("calibration") or {}
    duration = components.get("starterDuration") or {}
    specialist = components.get("matchupSpecialist") or {}
    return [
        {"componentId": "MLB_V18_HGB_PA", "role": "BASE", "ran": True,
         "fallbackReason": None,
         "detail": "Unchanged frozen V18; the release default is the baseline hook."},
        {"componentId": (calibration.get("comparisonMethod")
                         or "CALIBRATION"), "role": "CALIBRATION", "ran": False,
         "fallbackReason": calibration.get("disabledReason"),
         "detail": f"status {calibration.get('status')}; comparison-only "
                   f"({calibration.get('rollback')})"},
        {"componentId": "STARTER_DURATION_ADAPTER", "role": "DURATION", "ran": False,
         "fallbackReason": "Shadow-only and unassessed at game level; not enabled in the "
                           "release default",
         "detail": f"status {duration.get('status')}; {duration.get('fallback')}"},
        {"componentId": "MATCHUP_SPECIALIST", "role": "SPECIALIST", "ran": False,
         "fallbackReason": "No validated specialist",
         "detail": f"status {specialist.get('status')}"},
    ]


def _configurations(package: dict[str, Any], comparison: dict[str, Any]) -> list[dict[str, Any]]:
    """The four configurations, each labelled with what it actually is."""

    labels = {
        "A_FROZEN_V18_BASELINE_HOOK": (
            "A", "Enabled release default", "ENABLED_RELEASE_DEFAULT",
            "Unchanged frozen V18. This is what the candidate would ship."),
        "B_V18_PLUS_CALIBRATION": (
            "B", "Comparison-only calibration", "DISABLED_COMPARISON_ONLY",
            "Calibration is disabled by its own selection gate; shown for comparison only."),
        "C_V18_PLUS_DURATION": (
            "C", "Duration shadow / unassessed", "SHADOW_ONLY_UNASSESSED",
            "Starter duration is shadow-only and unassessed at game level."),
        "D_V18_PLUS_CALIBRATION_PLUS_DURATION": (
            "D", "Combined replay / unassessed", "DISABLED_AND_UNASSESSED",
            "Both non-default components together; neither is enabled."),
    }
    reused = set(comparison.get("savedConfigurationsReused") or ())
    out: list[dict[str, Any]] = []
    for key, result in (comparison.get("configurationResults") or {}).items():
        letter, title, status, note = labels.get(
            key, (key[:1], key, "UNKNOWN", "Unlabelled configuration"))
        delta = result.get("pairedDeltaVersusA") or {}
        out.append({
            "key": key, "letter": letter, "title": title, "status": status, "note": note,
            "enabled_in_release": key == package.get("defaultConfiguration"),
            "provenance": "SAVED_REUSED" if key in reused else "NEW_RETROSPECTIVE",
            "simulations": result.get("simulations"),
            "home_win_probability": result["homeWin"]["mean"],
            "home_win_monte_carlo_standard_error": result["homeWin"]["monteCarloStandardError"],
            "expected_home_runs": result["homeRuns"]["mean"],
            "expected_away_runs": result["awayRuns"]["mean"],
            "expected_total_runs": result["totalRuns"]["mean"],
            "extra_inning_rate": (result.get("extraInningRate") or {}).get("mean"),
            "paired_delta_vs_a": {
                "home_win_probability": (delta.get("homeWin") or {}).get("mean"),
                "home_win_paired_standard_error": (
                    delta.get("homeWin") or {}).get("pairedMonteCarloStandardError"),
                "expected_home_runs": (delta.get("homeRuns") or {}).get("mean"),
                "expected_away_runs": (delta.get("awayRuns") or {}).get("mean"),
                "expected_total_runs": (delta.get("totalRuns") or {}).get("mean"),
            },
            "illegal_state_count": result.get("illegalStateCount"),
            "nontermination_count": result.get("nonterminationCount"),
        })
    out.sort(key=lambda row: row["letter"])
    return out


__all__ = [
    "COMPARISON_SCHEMA", "CONTRACT_VERSION", "HANDOFF_SCHEMA", "PACKAGE_SCHEMA",
    "POINTER_SCHEMA", "REPLAY_FORECAST_TYPE", "VerifiedExchangeCandidate",
    "bind_baseline", "register_exchange", "verify_exchange",
]

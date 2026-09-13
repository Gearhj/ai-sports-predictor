"""Verify and register the MLB ensemble release candidate as a research shadow.

WHAT THIS IS
------------
Model Studio composes a candidate from a FROZEN V18 base, a tested calibration
component and — where the game supports it — the starter-duration adapter. It
publishes the candidate's precomputed forecasts as a checksum-addressed package.
This module verifies that package and registers it under its own engine id.

WHAT THIS IS NOT
----------------
It is not an execution path and it is not a promotion. AISP never loads or runs
the candidate's estimator, exactly as it never runs V18's: it imports numbers
Model Studio already produced. A package that claims execution or wagering
authority is refused before its payload is interpreted.

Registering a candidate changes no model authority. The controlling engine for
official decisions is whatever ``model_champion_authorities`` says it is, and
nothing here writes that table.

BASELINE AND CANDIDATE STAY SEPARATE
------------------------------------
The candidate registers under ``MLB_ENSEMBLE_CANDIDATE``; the baseline stays
under ``MLB_V18_WHOLE_GAME``. Two engine ids, two sets of rows, never merged and
never averaged. The candidate must name the baseline artifact it was built on,
and that artifact must already be registered here -- a candidate claiming a
baseline AISP has never seen is refused, because "built on frozen V18" is then
an unverifiable assertion.

PROSPECTIVE VERSUS REPLAY
-------------------------
A package declares ``artifactType``: ``PROSPECTIVE`` for genuine future pregame
forecasts, or ``HISTORICAL_REPLAY`` for a labelled rerun over games that have
already happened. Both are legitimate research. Only the first can ever be
prospective evidence, so replays register with a distinct status that the
research scorer refuses, and they can reach no decision and no position.

TWO ENVELOPE VERSIONS, ONE PAYLOAD FORMAT
-----------------------------------------
``AISP_MLB_ENSEMBLE_CANDIDATE_HANDOFF_V2`` exists for exactly one reason: to
carry a ``TRANSITION`` component. Everything else is unchanged -- the object
manifest and per-game forecast stay at their V1 schemas, and every check below
applies identically to both versions.

The version gates the component vocabulary, not the other way round. A V1
package naming a ``TRANSITION`` component is refused with that reason, because
otherwise a producer could widen the contract simply by writing a new component
name into an old envelope, and "this package was verified against V1" would stop
meaning anything. The bundle records the version actually verified rather than a
fixed constant, so a V2 import stays distinguishable afterwards.

VERIFICATION ORDER (fail closed at the first violation)
-------------------------------------------------------
1.  The handoff parses, declares a supported schema version, and self-addresses:
    its ``handoffSha256`` is the SHA-256 of its own canonical bytes.
2.  The safety invariants are declared false. Checked BEFORE any payload is
    read, so a package cannot claim authority and be believed later.
3.  The candidate engine id differs from the baseline engine id.
4.  The named baseline model artifact is already registered in AISP.
5.  Every component declares an id, a role, a 64-hex digest and a status; the
    base component's digest must equal the declared baseline artifact.
6.  For each game object: the object directory is addressed by its digest, the
    manifest self-addresses, and every listed artifact's bytes hash and byte
    count match -- checked before the forecast is read as anything but bytes.
7.  The forecast's ``artifactType`` matches the handoff's; a PROSPECTIVE
    forecast published at or after its scheduled start is rejected with that
    reason recorded rather than silently dropped.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Game, ShadowForecast, ShadowForecastBundle
from app.shadow_import.mlb_v18 import ENGINE_ID as BASELINE_ENGINE_ID

HANDOFF_SCHEMA_V1 = "AISP_MLB_ENSEMBLE_CANDIDATE_HANDOFF_V1"
HANDOFF_SCHEMA_V2 = "AISP_MLB_ENSEMBLE_CANDIDATE_HANDOFF_V2"

#: Handoff envelopes this consumer accepts. V2 exists for exactly one reason:
#: to carry a TRANSITION component. It is not a new payload format -- the object
#: manifest and the per-game forecast stay at their V1 schemas, and every
#: existing check (self-addressing, safety invariants, baseline system binding,
#: per-game baseline binding, pregame clock, idempotency) applies unchanged.
SUPPORTED_HANDOFF_SCHEMAS = frozenset({HANDOFF_SCHEMA_V1, HANDOFF_SCHEMA_V2})

#: Kept as the V1 name so existing importers and tests that reference the single
#: schema constant continue to mean what they meant.
HANDOFF_SCHEMA = HANDOFF_SCHEMA_V1

OBJECT_SCHEMA = "MLB_ENSEMBLE_CANDIDATE_OBJECT_MANIFEST_V1"
FORECAST_SCHEMA = "MLB_ENSEMBLE_CANDIDATE_GAME_FORECAST_V1"
CONTRACT_VERSION = HANDOFF_SCHEMA_V1

#: The candidate's own track. Deliberately not the baseline's, not Gen 1's and
#: not the NFL shadow's, so a candidate row can never be joined into official
#: evidence or counted as a V18 forecast.
ENGINE_ID = "MLB_ENSEMBLE_CANDIDATE"

#: Status by artifact type. The replay status is what the research scorer
#: refuses; see app/services/forecast_evaluation.py::freeze_eligible.
PROSPECTIVE = "PROSPECTIVE"
HISTORICAL_REPLAY = "HISTORICAL_REPLAY"
STATUS_BY_TYPE = {
    PROSPECTIVE: "PROSPECTIVE_RESEARCH_SHADOW",
    HISTORICAL_REPLAY: "HISTORICAL_REPLAY_RESEARCH_SHADOW",
}

#: Component roles the contract recognises. BASE is the frozen V18 model the
#: candidate is composed on; CALIBRATION is the tested calibration component;
#: DURATION is the starter-duration adapter, which is expected to be
#: unavailable for some games and must say so rather than silently vanish.
ROLE_BASE = "BASE"
ROLE_CALIBRATION = "CALIBRATION"
ROLE_DURATION = "DURATION"

#: The base-out transition adapter. Recognised ONLY under a V2 envelope: a V1
#: package that names a TRANSITION component is declaring a component the V1
#: contract never defined, and accepting it would mean a producer could widen
#: the contract by writing a different component name into an old envelope.
ROLE_TRANSITION = "TRANSITION"

ROLES_V1 = frozenset({ROLE_BASE, ROLE_CALIBRATION, ROLE_DURATION})
ROLES_V2 = ROLES_V1 | {ROLE_TRANSITION}
ROLES_BY_SCHEMA: dict[str, frozenset[str]] = {
    HANDOFF_SCHEMA_V1: ROLES_V1,
    HANDOFF_SCHEMA_V2: ROLES_V2,
}

#: Kept for callers that predate the versioned sets. It is the V1 set, so a
#: reader who has not been updated still sees the narrower contract.
KNOWN_ROLES = ROLES_V1

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class EnsembleCandidateImportError(RuntimeError):
    """The package, its declarations or the requested registration is unsafe."""


@dataclass(frozen=True)
class VerifiedCandidateForecast:
    object_sha256: str
    manifest: dict[str, Any]
    forecast: dict[str, Any]

    @property
    def source_game_id(self) -> str:
        return str(self.forecast.get("sourceGameId") or "")


@dataclass(frozen=True)
class VerifiedCandidatePackage:
    handoff_path: Path
    handoff: dict[str, Any]
    forecasts: tuple[VerifiedCandidateForecast, ...]
    rejected: tuple[dict[str, str], ...]

    @property
    def handoff_sha256(self) -> str:
        return str(self.handoff["handoffSha256"])

    @property
    def artifact_type(self) -> str:
        return str(self.handoff["artifactType"])

    @property
    def contract_version(self) -> str:
        """The envelope the producer actually published.

        Stored on the bundle rather than a fixed constant, so a V2 import is
        distinguishable from a V1 one after the fact. Writing the constant meant
        every bundle claimed V1 regardless of what was verified.
        """

        return str(self.handoff["schemaVersion"])

    @property
    def is_replay(self) -> bool:
        return self.artifact_type == HISTORICAL_REPLAY


@dataclass
class CandidateRegistrationOutcome:
    imported: list[str] = field(default_factory=list)
    already_present: list[str] = field(default_factory=list)
    rejected: list[dict[str, str]] = field(default_factory=list)
    bundle_created: bool = False
    artifact_type: str = PROSPECTIVE


# ------------------------------------------------------------------ helpers --

def _require_sha(value: Any, label: str) -> str:
    text = str(value or "")
    if not _SHA256.match(text):
        raise EnsembleCandidateImportError(f"{label} is not a 64-hex SHA-256 digest")
    return text


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    """The bytes a digest is taken over: the document without its own digest."""

    body = {k: v for k, v in payload.items() if k != "handoffSha256"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EnsembleCandidateImportError(f"unreadable JSON at {path}: {exc}") from exc


def _aware(value: Any) -> datetime:
    text = str(value or "")
    if not text:
        raise EnsembleCandidateImportError("a required timestamp is missing")
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EnsembleCandidateImportError(f"unparseable timestamp {text!r}") from exc
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _safe_child(root: Path, relative: str) -> Path:
    """Resolve a manifest-supplied relative path without escaping the package."""

    if not relative or Path(relative).is_absolute():
        raise EnsembleCandidateImportError(f"unsafe relative path {relative!r}")
    candidate = (root / relative).resolve()
    if not str(candidate).startswith(str(root.resolve())):
        raise EnsembleCandidateImportError(f"path {relative!r} escapes the package root")
    return candidate


# ------------------------------------------------------------ verification --

def verify_package(handoff_path: Path, *, package_root: Path | None = None) -> VerifiedCandidatePackage:
    """Verify a candidate package end to end. Raises on the first violation."""

    handoff_path = Path(handoff_path)
    root = Path(package_root) if package_root else handoff_path.parent
    handoff = _read_json(handoff_path)

    schema_version = str(handoff.get("schemaVersion") or "")
    if schema_version not in SUPPORTED_HANDOFF_SCHEMAS:
        raise EnsembleCandidateImportError(
            f"handoff declares {handoff.get('schemaVersion')!r}, expected one of "
            + ", ".join(sorted(SUPPORTED_HANDOFF_SCHEMAS))
        )
    # Which component roles this envelope is allowed to name. Resolved from the
    # declared version before any component is read, so the envelope decides the
    # vocabulary rather than the component list widening it.
    allowed_roles = ROLES_BY_SCHEMA[schema_version]
    declared = _require_sha(handoff.get("handoffSha256"), "handoffSha256")
    actual = hashlib.sha256(_canonical_bytes(handoff)).hexdigest()
    if declared != actual:
        raise EnsembleCandidateImportError(
            "handoff does not self-address: declared "
            f"{declared[:16]}..., computed {actual[:16]}..."
        )

    # Safety invariants FIRST, before any payload is interpreted. A package that
    # claims execution or wagering authority is refused, not quarantined and
    # read anyway.
    for flag, expected in (
        ("executionAllowed", False),
        ("wageringAuthority", False),
        ("activatesModel", False),
    ):
        if handoff.get(flag) is not expected:
            raise EnsembleCandidateImportError(
                f"handoff declares {flag}={handoff.get(flag)!r}; AISP imports "
                "research evidence only and refuses a package claiming authority"
            )

    artifact_type = str(handoff.get("artifactType") or "")
    if artifact_type not in STATUS_BY_TYPE:
        raise EnsembleCandidateImportError(
            f"artifactType {artifact_type!r} is not {PROSPECTIVE} or {HISTORICAL_REPLAY}"
        )

    candidate = handoff.get("candidate") or {}
    baseline = handoff.get("baseline") or {}
    candidate_engine = str(candidate.get("engineId") or "")
    baseline_engine = str(baseline.get("engineId") or "")
    if candidate_engine != ENGINE_ID:
        raise EnsembleCandidateImportError(
            f"candidate engineId {candidate_engine!r} is not {ENGINE_ID!r}"
        )
    if baseline_engine != BASELINE_ENGINE_ID:
        raise EnsembleCandidateImportError(
            f"baseline engineId {baseline_engine!r} is not {BASELINE_ENGINE_ID!r}"
        )
    if candidate_engine == baseline_engine:
        raise EnsembleCandidateImportError(
            "candidate and baseline declare the same engine id; they must stay separate"
        )
    _require_sha(candidate.get("modelArtifactSha256"), "candidate modelArtifactSha256")
    baseline_artifact = _require_sha(
        baseline.get("modelArtifactSha256"), "baseline modelArtifactSha256"
    )

    components = handoff.get("components") or []
    if not components:
        raise EnsembleCandidateImportError("handoff declares no components")
    roles: list[str] = []
    for component in components:
        role = str(component.get("role") or "")
        if role not in allowed_roles:
            if role in ROLES_V2:
                raise EnsembleCandidateImportError(
                    f"component role {role!r} requires handoff schema "
                    f"{HANDOFF_SCHEMA_V2}; this package declares {schema_version}"
                )
            raise EnsembleCandidateImportError(f"unknown component role {role!r}")
        if not str(component.get("componentId") or ""):
            raise EnsembleCandidateImportError(f"component with role {role} has no componentId")
        digest = _require_sha(component.get("sha256"), f"component {role} sha256")
        if not str(component.get("status") or ""):
            raise EnsembleCandidateImportError(f"component {role} declares no status")
        if role == ROLE_BASE and digest != baseline_artifact:
            raise EnsembleCandidateImportError(
                "the BASE component digest does not equal the declared baseline "
                "model artifact; the candidate's base is not the frozen baseline"
            )
        roles.append(role)
    if ROLE_BASE not in roles:
        raise EnsembleCandidateImportError("handoff declares no BASE component")

    entries = handoff.get("forecasts")
    if not isinstance(entries, list) or not entries:
        raise EnsembleCandidateImportError("handoff lists no forecasts")

    verified: list[VerifiedCandidateForecast] = []
    rejected: list[dict[str, str]] = []
    for entry in entries:
        try:
            verified.append(_verify_object(root, entry, artifact_type))
        except EnsembleCandidateImportError as exc:
            rejected.append({
                "sourceGameId": str(entry.get("sourceGameId") or entry.get("gameId") or ""),
                "objectSha256": str(entry.get("objectSha256") or ""),
                "reason": str(exc),
            })

    return VerifiedCandidatePackage(
        handoff_path=handoff_path, handoff=handoff,
        forecasts=tuple(verified), rejected=tuple(rejected),
    )


def _verify_object(
    root: Path, entry: dict[str, Any], artifact_type: str,
) -> VerifiedCandidateForecast:
    object_sha = _require_sha(entry.get("objectSha256"), "objectSha256")
    object_root = _safe_child(root, str(entry.get("relativePath") or ""))
    if object_root.name != object_sha:
        raise EnsembleCandidateImportError(
            f"object directory {object_root.name!r} is not addressed by its digest"
        )

    manifest_name = str(entry.get("manifestFile") or "manifest.json")
    manifest = _read_json(object_root / manifest_name)
    if manifest.get("schemaVersion") != OBJECT_SCHEMA:
        raise EnsembleCandidateImportError(
            f"object manifest declares {manifest.get('schemaVersion')!r}, expected {OBJECT_SCHEMA!r}"
        )
    if manifest.get("objectSha256") != object_sha:
        raise EnsembleCandidateImportError("object manifest does not self-address")

    # Bytes and byte counts verified BEFORE the forecast is interpreted.
    verified_files: set[str] = set()
    for artifact in manifest.get("artifacts") or []:
        relative = str(artifact.get("relativePath") or "")
        expected = _require_sha(artifact.get("sha256"), f"artifact {relative} sha256")
        payload = _safe_child(object_root, relative).read_bytes()
        if hashlib.sha256(payload).hexdigest() != expected:
            raise EnsembleCandidateImportError(f"artifact {relative} fails its checksum")
        if len(payload) != int(artifact.get("bytes", -1)):
            raise EnsembleCandidateImportError(f"artifact {relative} fails its byte count")
        verified_files.add(relative)

    forecast_name = str(entry.get("forecastFile") or "forecast.json")
    if forecast_name not in verified_files:
        raise EnsembleCandidateImportError(
            f"forecast file {forecast_name!r} is not a verified member of the object"
        )
    forecast = _read_json(object_root / forecast_name)
    if forecast.get("schemaVersion") != FORECAST_SCHEMA:
        raise EnsembleCandidateImportError(
            f"forecast declares {forecast.get('schemaVersion')!r}, expected {FORECAST_SCHEMA!r}"
        )
    if str(forecast.get("artifactType") or "") != artifact_type:
        raise EnsembleCandidateImportError(
            f"forecast artifactType {forecast.get('artifactType')!r} contradicts the "
            f"handoff's {artifact_type!r}"
        )
    source_game_id = str(forecast.get("sourceGameId") or "")
    if not source_game_id or source_game_id != str(entry.get("sourceGameId") or ""):
        raise EnsembleCandidateImportError("forecast sourceGameId disagrees with the handoff entry")
    if not str(forecast.get("baselineForecastId") or ""):
        raise EnsembleCandidateImportError(
            "forecast names no baselineForecastId; a candidate must say which "
            "baseline forecast it was composed on"
        )

    probabilities = forecast.get("probabilities") or {}
    for key in ("homeWin", "awayWin"):
        if not isinstance(probabilities.get(key), (int, float)):
            raise EnsembleCandidateImportError(f"forecast publishes no numeric {key} probability")
    expectations = forecast.get("expectations") or {}
    for key in ("homeRuns", "awayRuns"):
        if not isinstance(expectations.get(key), (int, float)):
            raise EnsembleCandidateImportError(f"forecast publishes no numeric {key} expectation")

    published = _aware(forecast.get("publishedAt"))
    scheduled = _aware(forecast.get("scheduledStart"))
    if artifact_type == PROSPECTIVE and published >= scheduled:
        raise EnsembleCandidateImportError(
            f"PROSPECTIVE forecast published {published.isoformat()} at or after its "
            f"scheduled start {scheduled.isoformat()}"
        )

    # Fields the research-shadow table requires. They are validated here, with
    # a named reason, rather than being defaulted at insert time -- a forecast
    # whose simulator or teams are unknown is not a forecast we can file.
    simulation = forecast.get("simulation") or {}
    for field_name, container, label in (
        ("simulatorId", simulation, "simulation.simulatorId"),
        ("simulatorVersion", simulation, "simulation.simulatorVersion"),
        ("homeTeam", forecast, "homeTeam"),
        ("awayTeam", forecast, "awayTeam"),
    ):
        if not str(container.get(field_name) or ""):
            raise EnsembleCandidateImportError(f"forecast declares no {label}")
    if not isinstance(simulation.get("seed"), int):
        raise EnsembleCandidateImportError("forecast declares no integer simulation.seed")
    if not isinstance(forecast.get("season"), int):
        raise EnsembleCandidateImportError("forecast declares no integer season")

    applied = forecast.get("componentsApplied")
    if not isinstance(applied, list) or not applied:
        raise EnsembleCandidateImportError("forecast records no componentsApplied")
    for record in applied:
        if not str(record.get("componentId") or ""):
            raise EnsembleCandidateImportError("a componentsApplied record has no componentId")
        if not isinstance(record.get("ran"), bool):
            raise EnsembleCandidateImportError(
                f"component {record.get('componentId')!r} does not say whether it ran"
            )
        if record["ran"] is False and not str(record.get("fallbackReason") or ""):
            raise EnsembleCandidateImportError(
                f"component {record.get('componentId')!r} did not run and gives no "
                "fallbackReason; a component that silently vanishes is not auditable"
            )

    return VerifiedCandidateForecast(
        object_sha256=object_sha, manifest=manifest, forecast=forecast,
    )


# --------------------------------------------------- baseline system binding --
#
# The estimator artifact alone identifies the PA model and nothing else. A
# candidate "composed on frozen V18" has to name the whole frozen system, or the
# claim is unverifiable: the same estimator behind a different preprocessor, a
# different ordered feature contract or a different accepted transition is a
# different forecasting system that happens to share one file.
#
# Every digest below is checked against something AISP already stores from the
# champion authority supplement or from the stored baseline forecast itself.
# Nothing is taken on the package's word.

#: handoff baseline field -> (column on ModelChampionAuthority, human label)
AUTHORITY_BINDINGS: dict[str, tuple[str, str]] = {
    "modelArtifactSha256": ("model_artifact_sha256", "PA estimator artifact"),
    "championManifestSha256": ("champion_manifest_sha256", "frozen champion/system manifest"),
    "frozenBindingsSha256": ("frozen_bindings_sha256",
                             "fitted preprocessor and ordered feature contract"),
    "parentBundleId": ("parent_bundle_id", "parent V18 bundle id"),
    "trainingRunId": ("training_run_id", "training run id"),
}

#: handoff baseline field -> (key in ModelChampionAuthority.evidence, label)
#: NOTE: the development-integrity manifest and the feature TREATMENT manifest
#: are different artifacts with different digests. An earlier draft of this
#: mapping called the first one "treatment", which would have accepted a package
#: that named the wrong document. They are separated here.
EVIDENCE_BINDINGS: dict[str, tuple[str, str]] = {
    "developmentIntegritySha256": ("development_integrity_sha256",
                                   "development-integrity manifest"),
    "registrationReceiptSha256": ("registration_receipt_sha256", "registration receipt"),
}

#: per-game baselineBinding field -> (key in the stored forecast's lineage_detail)
LINEAGE_BINDINGS: dict[str, tuple[str, str]] = {
    "parentBundleSha256": ("parentBundleSha256", "parent V18 bundle digest"),
    "acceptanceProtocolSha256": ("acceptanceProtocolV2Sha256", "accepted transition artifact"),
}

#: per-game baselineBinding field -> (column on ShadowForecast, label)
FORECAST_BINDINGS: dict[str, tuple[str, str]] = {
    "numericalArtifactSha256": ("model_manifest_sha256", "baseline per-game numerical artifact"),
    "featureVectorSha256": ("feature_vector_sha256", "baseline feature vector"),
    "simulatorId": ("simulator_id", "simulator identity"),
    "simulatorVersion": ("simulator_version", "simulator version"),
}


class BaselineBindingError(EnsembleCandidateImportError):
    """The candidate's declared baseline does not match the frozen V18 system."""


def _mismatch(label: str, declared: Any, stored: Any) -> BaselineBindingError:
    return BaselineBindingError(
        f"baseline {label} does not match the frozen V18 system registered in AISP: "
        f"package declares {str(declared)[:24]!r}, AISP holds {str(stored)[:24]!r}"
    )


def verify_baseline_system(
    session: Session, handoff: dict[str, Any],
) -> "ModelChampionAuthority":
    """Bind the package's baseline declarations to the registered champion.

    Raises on the first disagreement, before anything is written.
    """

    from app.models.entities import ModelChampionAuthority

    baseline = handoff.get("baseline") or {}
    authority = session.scalar(
        select(ModelChampionAuthority)
        .where(ModelChampionAuthority.engine_id == BASELINE_ENGINE_ID)
        .order_by(ModelChampionAuthority.effective_at.desc())
        .limit(1)
    )
    if authority is None:
        raise BaselineBindingError(
            "no V18 champion authority is registered in AISP, so the candidate's "
            "baseline system cannot be verified at all"
        )
    evidence = authority.evidence or {}

    for field_name, (column, label) in AUTHORITY_BINDINGS.items():
        declared = baseline.get(field_name)
        if not declared:
            raise BaselineBindingError(
                f"handoff baseline declares no {field_name} ({label}); the estimator "
                "digest alone does not identify the frozen V18 system"
            )
        stored = getattr(authority, column, None)
        if str(declared) != str(stored):
            raise _mismatch(label, declared, stored)

    for field_name, (key, label) in EVIDENCE_BINDINGS.items():
        declared = baseline.get(field_name)
        if not declared:
            raise BaselineBindingError(
                f"handoff baseline declares no {field_name} ({label})"
            )
        stored = evidence.get(key)
        if stored is None:
            raise BaselineBindingError(
                f"AISP holds no {label} for the registered champion, so "
                f"{field_name} cannot be verified; refusing rather than assuming"
            )
        if str(declared) != str(stored):
            raise _mismatch(label, declared, stored)
    return authority


def verify_baseline_forecast(
    session: Session, forecast: dict[str, Any], baseline: dict[str, Any],
) -> ShadowForecast:
    """Resolve baselineForecastId to a real stored V18 forecast and agree with it.

    Same canonical game, same orientation, same horizon, same numerical artifact
    and same simulation identity -- not merely "a V18 forecast exists somewhere".
    """

    identity = str(forecast.get("baselineForecastId") or "")
    stored = session.scalar(
        select(ShadowForecast).where(
            ShadowForecast.forecast_id == identity,
            ShadowForecast.engine_id == BASELINE_ENGINE_ID,
        )
    )
    if stored is None:
        raise BaselineBindingError(
            f"baselineForecastId {identity!r} does not resolve to a stored V18 "
            "forecast; the candidate names a baseline AISP has never held"
        )

    declared_game = str(forecast.get("canonicalGameId") or "")
    if declared_game and str(stored.canonical_game_id) != declared_game:
        raise BaselineBindingError(
            f"baseline forecast {identity!r} belongs to game {stored.canonical_game_id}, "
            f"not the candidate's {declared_game}"
        )
    if str(stored.source_event_id) != str(forecast.get("sourceGameId")):
        raise BaselineBindingError(
            f"baseline forecast {identity!r} is for gamePk {stored.source_event_id}, "
            f"not the candidate's {forecast.get('sourceGameId')}"
        )
    # Orientation. A candidate whose home and away are the other way round is
    # not a comparison, it is a sign error waiting to be plotted.
    for side, column in (("homeTeam", "home_team_id"), ("awayTeam", "away_team_id")):
        if str(forecast.get(side)) != str(getattr(stored, column)):
            raise BaselineBindingError(
                f"baseline forecast {identity!r} has {column}={getattr(stored, column)!r}, "
                f"candidate declares {side}={forecast.get(side)!r}; orientation disagrees"
            )
    # Horizon: the same scheduled first pitch.
    declared_start = _aware(forecast.get("scheduledStart"))
    stored_start = stored.scheduled_start_utc
    if stored_start is not None:
        stored_start = stored_start if stored_start.tzinfo else stored_start.replace(tzinfo=UTC)
        if stored_start != declared_start:
            raise BaselineBindingError(
                f"baseline forecast {identity!r} has scheduled start "
                f"{stored_start.isoformat()}, candidate declares {declared_start.isoformat()}"
            )
    if str(stored.model_artifact_sha256) != str(baseline.get("modelArtifactSha256")):
        raise _mismatch(
            "per-game estimator artifact",
            baseline.get("modelArtifactSha256"), stored.model_artifact_sha256,
        )

    binding = forecast.get("baselineBinding") or {}
    if not binding:
        raise BaselineBindingError(
            "forecast declares no baselineBinding; the per-game baseline numerical "
            "artifact, simulator identity, seed, count and source clocks must be bound"
        )
    lineage = stored.lineage_detail or {}
    for field_name, (key, label) in LINEAGE_BINDINGS.items():
        declared = binding.get(field_name)
        if not declared:
            raise BaselineBindingError(f"baselineBinding declares no {field_name} ({label})")
        if str(declared) != str(lineage.get(key)):
            raise _mismatch(label, declared, lineage.get(key))
    for field_name, (column, label) in FORECAST_BINDINGS.items():
        declared = binding.get(field_name)
        if not declared:
            raise BaselineBindingError(f"baselineBinding declares no {field_name} ({label})")
        if str(declared) != str(getattr(stored, column)):
            raise _mismatch(label, declared, getattr(stored, column))

    # Simulation seed and count, and the source clock the baseline was cut at.
    if binding.get("simulatorSeed") != stored.simulator_seed:
        raise _mismatch("simulator seed", binding.get("simulatorSeed"), stored.simulator_seed)
    stored_count = (stored.uncertainty or {}).get("simulationCount")
    if binding.get("simulationCount") != stored_count:
        raise _mismatch("simulation count", binding.get("simulationCount"), stored_count)
    declared_cutoff = binding.get("sourceCutoffUtc")
    if not declared_cutoff:
        raise BaselineBindingError("baselineBinding declares no sourceCutoffUtc")
    stored_cutoff = stored.source_cutoff_utc
    if stored_cutoff is not None:
        stored_cutoff = stored_cutoff if stored_cutoff.tzinfo else stored_cutoff.replace(tzinfo=UTC)
        if _aware(declared_cutoff) != stored_cutoff:
            raise _mismatch("source evidence cutoff", declared_cutoff, stored_cutoff.isoformat())
    return stored


def _resolve_game(session: Session, value: Any) -> Game | None:
    """The canonical game this forecast binds to, or None when unbound.

    A malformed id is not a silent miss: it binds nothing, and the forecast is
    still registered so the package stays auditable, but it will not join to a
    game anywhere downstream.
    """

    if not value:
        return None
    try:
        game_id = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (AttributeError, ValueError):
        return None
    return session.scalar(select(Game).where(Game.id == game_id))


# ------------------------------------------------------------ registration --

def register_package(
    session: Session, verified: VerifiedCandidatePackage, *, now: datetime | None = None,
) -> CandidateRegistrationOutcome:
    """Register a verified package. Idempotent: a re-import writes nothing new."""

    now = now or datetime.now(UTC)
    handoff = verified.handoff
    outcome = CandidateRegistrationOutcome(artifact_type=verified.artifact_type)
    outcome.rejected.extend(dict(row) for row in verified.rejected)

    candidate = handoff["candidate"]
    baseline = handoff["baseline"]
    baseline_artifact = str(baseline["modelArtifactSha256"])

    # The baseline must already be here. "Composed on frozen V18" is only a
    # verifiable claim if AISP holds that exact artifact.
    known_baseline = session.scalar(
        select(ShadowForecast.id)
        .where(
            ShadowForecast.engine_id == BASELINE_ENGINE_ID,
            ShadowForecast.model_artifact_sha256 == baseline_artifact,
        )
        .limit(1)
    )
    if known_baseline is None:
        raise EnsembleCandidateImportError(
            f"the declared baseline artifact {baseline_artifact[:16]}... is not "
            "registered in AISP; the candidate's baseline cannot be verified"
        )

    # The whole frozen system, not just the estimator. Checked before a single
    # row is written, so a mismatch anywhere rejects the package rather than
    # importing part of it.
    champion = verify_baseline_system(session, handoff)
    bound: dict[str, ShadowForecast] = {}
    for item in verified.forecasts:
        bound[item.forecast["forecastId"]] = verify_baseline_forecast(
            session, item.forecast, baseline,
        )

    bundle = session.scalar(
        select(ShadowForecastBundle)
        .where(ShadowForecastBundle.bundle_sha256 == verified.handoff_sha256)
    )
    if bundle is None:
        bundle = ShadowForecastBundle(
            bundle_sha256=verified.handoff_sha256,
            bundle_id=str(handoff.get("bundleId") or f"mlb-ensemble-candidate-{verified.handoff_sha256[:24]}"),
            logical_uri=str(verified.handoff_path.as_uri()),
            source_fixture_id=str(candidate.get("engineVersion") or ""),
            slate_date=_aware(handoff.get("slateDate") + "T00:00:00+00:00").date()
            if isinstance(handoff.get("slateDate"), str) else None,
            league="MLB", sport="MLB",
            status=STATUS_BY_TYPE[verified.artifact_type],
            research_shadow_only=True,
            generated_at=_aware(handoff.get("generatedAt")),
            imported_at=now,
            forecast_count=len(verified.forecasts),
            quote_count=0, candidate_count=len(verified.forecasts),
            contract_version=verified.contract_version,
            evidence={
                "artifactType": verified.artifact_type,
                "candidate": candidate,
                "baseline": baseline,
                "components": handoff.get("components"),
                "executionAllowed": False,
                "wageringAuthority": False,
                "activatesModel": False,
                "baselineRegisteredInAisp": True,
                # What the baseline chain was actually checked against.
                "baselineSystemBinding": {
                    "verifiedAgainstAuthorityId": str(champion.id),
                    "modelEraId": champion.model_era_id,
                    "estimatorSha256": champion.model_artifact_sha256,
                    "championManifestSha256": champion.champion_manifest_sha256,
                    "frozenBindingsSha256": champion.frozen_bindings_sha256,
                    "developmentIntegritySha256": (champion.evidence or {}).get(
                        "development_integrity_sha256"),
                    "registrationReceiptSha256": (champion.evidence or {}).get(
                        "registration_receipt_sha256"),
                    "parentBundleId": champion.parent_bundle_id,
                    "trainingRunId": champion.training_run_id,
                },
            },
        )
        session.add(bundle)
        session.flush()
        outcome.bundle_created = True

    for item in verified.forecasts:
        forecast = item.forecast
        forecast_id = str(forecast.get("forecastId") or f"mlb-ensemble-{item.object_sha256}")
        existing = session.scalar(
            select(ShadowForecast.id).where(ShadowForecast.forecast_id == forecast_id)
        )
        if existing is not None:
            outcome.already_present.append(forecast_id)
            continue

        game = _resolve_game(session, forecast.get("canonicalGameId"))

        probabilities = forecast["probabilities"]
        expectations = forecast["expectations"]
        published = _aware(forecast.get("publishedAt"))
        scheduled = _aware(forecast.get("scheduledStart"))
        captured = _aware(forecast.get("capturedAt")) if forecast.get("capturedAt") else published

        session.add(ShadowForecast(
            bundle_id=bundle.id,
            forecast_id=forecast_id,
            engine_id=ENGINE_ID,
            engine_version=str(candidate.get("engineVersion") or ""),
            model_artifact_sha256=str(candidate.get("modelArtifactSha256") or ""),
            model_manifest_sha256=str(candidate.get("manifestSha256") or "") or None,
            feature_vector_sha256=str(forecast.get("featureVectorSha256") or "") or None,
            simulator_id=str((forecast.get("simulation") or {})["simulatorId"]),
            simulator_version=str((forecast.get("simulation") or {})["simulatorVersion"]),
            simulator_seed=int((forecast.get("simulation") or {})["seed"]),
            event_id=str(forecast.get("eventId") or item.object_sha256),
            source_event_id=item.source_game_id,
            event_date=scheduled.date(),
            season=int(forecast["season"]),
            canonical_game_id=game.id if game is not None else None,
            home_team_id=str(forecast["homeTeam"]),
            away_team_id=str(forecast["awayTeam"]),
            snapshot_type=verified.artifact_type,
            snapshot_version=int(forecast.get("snapshotVersion") or 1),
            forecast_schema_version=FORECAST_SCHEMA,
            status=STATUS_BY_TYPE[verified.artifact_type],
            is_ready=bool((forecast.get("eligibility") or {}).get("eligible", True)),
            readiness_score=1.0, readiness_threshold=1.0,
            scheduled_start_utc=scheduled,
            available_as_of_utc=published,
            source_cutoff_utc=captured,
            forecast_cutoff_utc=published,
            full_home_win_probability=float(probabilities["homeWin"]),
            full_away_win_probability=float(probabilities["awayWin"]),
            full_expected_home_score=float(expectations["homeRuns"]),
            full_expected_away_score=float(expectations["awayRuns"]),
            distributions=forecast.get("distributions") or {},
            uncertainty=forecast.get("simulation") or {},
            readiness_detail={
                "artifactType": verified.artifact_type,
                "eligibility": forecast.get("eligibility") or {},
                "componentsApplied": forecast["componentsApplied"],
            },
            identity_evidence={
                "sourceGameId": item.source_game_id,
                "objectSha256": item.object_sha256,
                "baselineForecastId": forecast.get("baselineForecastId"),
            },
            lineage_detail={
                "artifactType": verified.artifact_type,
                "contractVersion": verified.contract_version,
                # The producer's own configuration identity. Two configurations
                # of one engine family (survival, transition) publish separate
                # forecasts for the same game; without this the only thing
                # telling them apart downstream is the engine version string.
                # `actualConfiguration` differs from `configurationId` exactly
                # when a component fell back -- a survival forecast that reused
                # the baseline draws reports V18_BASE, and a reader must be able
                # to see that rather than counting it as a component trial.
                "configurationId": forecast.get("configurationId"),
                "actualConfiguration": forecast.get("actualConfiguration"),
                "componentTrialSupported": (
                    forecast.get("eligibility") or {}).get("componentTrialSupported"),
                "fallbackToOriginalDraws": (
                    forecast.get("simulation") or {}).get("fallbackToOriginalDraws"),
                "handoffSha256": verified.handoff_sha256,
                "objectSha256": item.object_sha256,
                "candidate": candidate,
                "baseline": baseline,
                "baselineForecastId": forecast.get("baselineForecastId"),
                "baselineBinding": forecast.get("baselineBinding"),
                "baselineShadowForecastId": str(bound[forecast_id].id)
                if forecast_id in bound else None,
                "components": handoff.get("components"),
                "componentsApplied": forecast["componentsApplied"],
                "simulation": forecast.get("simulation") or {},
                "executionAllowed": False,
                "wageringAuthority": False,
                "activatesModel": False,
            },
        ))
        outcome.imported.append(forecast_id)

    session.flush()
    return outcome


__all__ = [
    "AUTHORITY_BINDINGS", "BaselineBindingError", "CONTRACT_VERSION",
    "EVIDENCE_BINDINGS", "FORECAST_BINDINGS", "LINEAGE_BINDINGS",
    "ENGINE_ID", "FORECAST_SCHEMA", "HANDOFF_SCHEMA",
    "HANDOFF_SCHEMA_V1", "HANDOFF_SCHEMA_V2", "SUPPORTED_HANDOFF_SCHEMAS",
    "HISTORICAL_REPLAY", "OBJECT_SCHEMA", "PROSPECTIVE", "ROLE_BASE",
    "ROLE_CALIBRATION", "ROLE_DURATION", "ROLE_TRANSITION",
    "ROLES_V1", "ROLES_V2", "ROLES_BY_SCHEMA", "STATUS_BY_TYPE",
    "CandidateRegistrationOutcome", "EnsembleCandidateImportError",
    "VerifiedCandidateForecast", "VerifiedCandidatePackage",
    "register_package", "verify_package",
    "verify_baseline_forecast", "verify_baseline_system",
]

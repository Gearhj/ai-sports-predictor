"""Verify and register the immutable MLB V18 whole-game challenger bundle.

WHAT THIS IS
------------
Model Studio produces a checksum-addressed bundle containing a frozen model, a
declared participation policy and prospectively generated forecasts. This module
verifies that bundle and registers it into AISP as a RESEARCH_SHADOW track. It
is the only place that understands the ``MLB_V18_WHOLE_GAME_CHALLENGER_BUNDLE_V1``
shape.

WHAT THIS IS NOT
----------------
It is not an execution path. The bundle's own handoff declares
``executionAllowed = false``, and the handoff lists that flag among the checks a
consumer must make. AISP therefore never runs the V18 model: it imports
forecasts Model Studio already produced, and refuses to register a bundle whose
handoff would claim otherwise.

It grants no wagering authority, promotes nothing, and touches no Gen 1
forecast, decision, position or experiment. Every row it writes lives in the
existing research-shadow tables under a distinct ``engine_id``.

VERIFICATION ORDER (fail closed at the first violation)
-------------------------------------------------------
1. The bundle directory is addressed by a 64-hex SHA-256 segment.
2. ``manifest.json`` parses and declares that same address as ``bundleSha256``.
3. Every artifact listed in the manifest exists, and its bytes hash and length
   match the manifest exactly -- checked BEFORE any serialized model artifact or
   implementation file is read as anything but bytes.
4. ``aisp-handoff.json`` declares the research-shadow safety invariants.
5. The named forecast/evidence files are members of the verified artifact set.
6. Each forecast is checked for prospective validity (captured before the
   scheduled start, no illegal states, no nonterminations, enough paths).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BUNDLE_SCHEMA = "MLB_V18_WHOLE_GAME_CHALLENGER_BUNDLE_V1"
HANDOFF_SCHEMA = "AISP_MLB_V18_RESEARCH_SHADOW_HANDOFF_V1"
FORECAST_SCHEMA = "MLB_V18_PROSPECTIVE_WHOLE_GAME_SIMULATION_V1"
SNAPSHOT_SCHEMA = "AISP_MLB_READ_ONLY_PREGAME_SNAPSHOT_V1"
CONTRACT_SCHEMA = "MLB_V18_EVALUATION_CONTRACT_SUPPLEMENT_MANIFEST_V1"

#: The research-shadow track this bundle registers under. It is deliberately
#: distinct from Gen 1 (``AISP_BASELINE``) and from the NFL shadow engine, so a
#: V18 row can never be mistaken for, or joined into, official evidence.
ENGINE_ID = "MLB_V18_WHOLE_GAME"
CONTRACT_VERSION = "AISP_MLB_V18_RESEARCH_SHADOW_BUNDLE_V1"
SHADOW_STATUS = "PROSPECTIVE_RESEARCH_SHADOW"

#: Minimum simulated paths the acceptance contract requires of a published
#: forecast. A bundle below this is evidence, but not a registrable forecast.
MINIMUM_SIMULATION_PATHS = 5000

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class V18ImportError(RuntimeError):
    """The bundle is missing, corrupted, or unsafe to register."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:  # pragma: no cover - message clarity
        raise V18ImportError(f"Required bundle file is missing: {path.name}") from exc
    except json.JSONDecodeError as exc:
        raise V18ImportError(f"{path.name} is not valid JSON: {exc}") from exc


def _aware(value: Any) -> datetime | None:
    """Parse an ISO timestamp, keeping its own offset. Never invents one."""

    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).strip().replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True)
class VerifiedBundle:
    """An MLB V18 bundle whose every artifact hash has been checked."""

    root: Path
    bundle_sha256: str
    bundle_id: str
    manifest: dict[str, Any]
    handoff: dict[str, Any]
    forecasts: list[dict[str, Any]]
    pregame_snapshot: dict[str, Any]
    authority_inventory: dict[str, Any]
    calibration: dict[str, Any]
    artifact_count: int

    @property
    def limitations(self) -> list[str]:
        return list(self.manifest.get("limitations") or [])

    @property
    def simulation_policy(self) -> dict[str, Any]:
        return dict(self.manifest.get("simulation") or {})

    @property
    def feature_contract(self) -> dict[str, Any]:
        return dict(self.manifest.get("featureContract") or {})


@dataclass(frozen=True)
class VerifiedContract:
    """The predeclared evaluation supplement this bundle is judged under."""

    contract_id: str
    contract_sha256: str
    referenced_bundle_sha256: str
    contract: dict[str, Any]


@dataclass
class RegistrationOutcome:
    """What one registration run did, and what it deliberately did not do."""

    bundle_sha256: str
    bundle_row_created: bool = False
    registered: list[str] = field(default_factory=list)
    already_present: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)

    @property
    def persisted_count(self) -> int:
        return len(self.registered) + len(self.already_present)


# -- verification ---------------------------------------------------------


def verify_bundle(root: str | Path) -> VerifiedBundle:
    """Verify a checksum-addressed V18 bundle, or fail closed."""

    root = Path(root)
    if not root.is_dir():
        raise V18ImportError(f"Bundle root is not a directory: {root}")

    address = root.name.lower()
    if not _SHA256.match(address):
        raise V18ImportError(
            "Bundle root is not checksum-addressed; expected a 64-hex sha256 "
            f"directory name, got {root.name!r}"
        )

    manifest = _read_json(root / "manifest.json")
    if manifest.get("schemaVersion") != BUNDLE_SCHEMA:
        raise V18ImportError(
            f"Unsupported bundle schema {manifest.get('schemaVersion')!r}; "
            f"this consumer implements {BUNDLE_SCHEMA}"
        )
    declared = str(manifest.get("bundleSha256", "")).lower()
    if declared != address:
        raise V18ImportError(
            "Manifest bundleSha256 does not match the directory address"
        )

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise V18ImportError("Manifest declares no artifacts")

    # Hash every artifact before anything is interpreted. A serialized model or
    # an implementation module is only ever opened as bytes until it passes.
    verified: set[str] = set()
    for entry in artifacts:
        relative = str(entry.get("relativePath", ""))
        expected = str(entry.get("sha256", "")).lower()
        if not relative or not _SHA256.match(expected):
            raise V18ImportError(f"Malformed artifact entry: {entry!r}")
        path = (root / relative).resolve()
        if root.resolve() not in path.parents and path != root.resolve():
            raise V18ImportError(f"Artifact escapes the bundle root: {relative}")
        if not path.is_file():
            raise V18ImportError(f"Declared artifact is missing: {relative}")
        payload = path.read_bytes()
        actual = hashlib.sha256(payload).hexdigest()
        if actual != expected:
            raise V18ImportError(f"Artifact checksum mismatch: {relative}")
        declared_bytes = entry.get("bytes")
        if declared_bytes is not None and len(payload) != int(declared_bytes):
            raise V18ImportError(f"Artifact byte count mismatch: {relative}")
        verified.add(relative)

    # Every executable payload member must also be a verified artifact.
    for entry in manifest.get("executionPayload") or []:
        relative = str(entry.get("relativePath", ""))
        if relative not in verified:
            raise V18ImportError(
                f"Execution payload member is not a verified artifact: {relative}"
            )

    handoff = _read_json(root / "aisp-handoff.json")
    _assert_handoff_is_safe(handoff, verified)

    forecast_doc = _read_json(root / str(handoff["forecastFile"]))
    forecasts = forecast_doc if isinstance(forecast_doc, list) else [forecast_doc]
    for item in forecasts:
        if item.get("schemaVersion") != FORECAST_SCHEMA:
            raise V18ImportError(
                f"Unsupported forecast schema {item.get('schemaVersion')!r}"
            )

    snapshot = _read_json(root / str(handoff["pregameEvidenceFile"]))
    if snapshot.get("schemaVersion") != SNAPSHOT_SCHEMA:
        raise V18ImportError(
            f"Unsupported pregame snapshot schema {snapshot.get('schemaVersion')!r}"
        )

    return VerifiedBundle(
        root=root,
        bundle_sha256=address,
        bundle_id=str(manifest.get("bundleId") or ""),
        manifest=manifest,
        handoff=handoff,
        forecasts=forecasts,
        pregame_snapshot=snapshot,
        authority_inventory=_read_json(root / "authority-inventory.json"),
        calibration=_read_json(root / "calibration.json"),
        artifact_count=len(verified),
    )


def _assert_handoff_is_safe(handoff: dict[str, Any], verified: set[str]) -> None:
    """The research-shadow invariants a consumer is required to check."""

    if handoff.get("schemaVersion") != HANDOFF_SCHEMA:
        raise V18ImportError(
            f"Unsupported handoff schema {handoff.get('schemaVersion')!r}"
        )
    required = {
        "sport": "MLB",
        "status": "RESEARCH_SHADOW",
        "promotionStatus": "NOT_PROMOTED",
    }
    for key, value in required.items():
        if handoff.get(key) != value:
            raise V18ImportError(
                f"Handoff {key} must be {value!r}, got {handoff.get(key)!r}"
            )
    if handoff.get("wageringAuthority") is not False:
        raise V18ImportError("Handoff must declare wageringAuthority=false")
    # AISP imports what Model Studio produced; it does not run the bundle. A
    # bundle that claimed execution was allowed would be a different contract
    # and is refused rather than silently executed.
    if handoff.get("executionAllowed") is not False:
        raise V18ImportError(
            "This consumer only imports precomputed forecasts; a bundle with "
            "executionAllowed=true is not registrable here"
        )
    for key in ("forecastFile", "pregameEvidenceFile"):
        relative = str(handoff.get(key) or "")
        if relative not in verified:
            raise V18ImportError(
                f"Handoff {key} names an unverified file: {relative!r}"
            )


def verify_contract(root: str | Path, *, bundle_sha256: str) -> VerifiedContract:
    """Verify the predeclared evaluation supplement and its bundle binding."""

    root = Path(root)
    address = root.name.lower()
    if not _SHA256.match(address):
        raise V18ImportError(
            f"Contract root is not checksum-addressed: {root.name!r}"
        )
    manifest = _read_json(root / "manifest.json")
    if manifest.get("schemaVersion") != CONTRACT_SCHEMA:
        raise V18ImportError(
            f"Unsupported contract schema {manifest.get('schemaVersion')!r}"
        )
    if str(manifest.get("contractSha256", "")).lower() != address:
        raise V18ImportError("Contract manifest sha256 does not match its address")
    referenced = str(manifest.get("referencedBundleSha256", "")).lower()
    if referenced != bundle_sha256.lower():
        raise V18ImportError(
            "Contract supplement references a different bundle "
            f"({referenced[:12]}...), not {bundle_sha256[:12]}..."
        )
    return VerifiedContract(
        contract_id=str(manifest.get("contractId") or ""),
        contract_sha256=address,
        referenced_bundle_sha256=referenced,
        contract=_read_json(root / "evaluation-contract.json"),
    )


# -- prospective validity -------------------------------------------------


def prospective_rejection(forecast: dict[str, Any], *, now: datetime) -> str | None:
    """Why this forecast may not be registered as prospective, or None.

    ``now`` is only used to describe a capture that has already been overtaken
    by the event; it never changes a stored timestamp.
    """

    captured = _aware(forecast.get("capturedAt"))
    start = _aware(forecast.get("scheduledStart"))
    if captured is None:
        return "no capture timestamp is recorded"
    if start is None:
        return "no scheduled start is recorded"
    if captured >= start:
        # A run at or after first pitch is not a pregame forecast and must
        # never be labelled one.
        return (
            f"captured at {captured.isoformat()} which is not before the "
            f"scheduled start {start.isoformat()}"
        )
    if int(forecast.get("illegalStateCount", -1)) != 0:
        return "the simulation reported illegal states"
    if int(forecast.get("nonterminationCount", -1)) != 0:
        return "the simulation reported nonterminating paths"
    count = int(forecast.get("simulationCount", 0))
    if count < MINIMUM_SIMULATION_PATHS:
        return (
            f"{count} simulated paths is below the contract minimum of "
            f"{MINIMUM_SIMULATION_PATHS}"
        )
    if forecast.get("deterministicReplayVerified") is not True:
        return "deterministic replay was not verified"
    if forecast.get("track") != "PROSPECTIVE_RESEARCH_SHADOW":
        return f"track is {forecast.get('track')!r}, not PROSPECTIVE_RESEARCH_SHADOW"
    probability = forecast.get("homeWinProbability")
    if probability is None or not 0.0 < float(probability) < 1.0:
        return "home win probability is missing or outside (0, 1)"
    return None


def sampling_uncertainty(probability: float, count: int) -> dict[str, float]:
    """Monte Carlo sampling error only -- explicitly not model uncertainty."""

    standard_error = math.sqrt(probability * (1.0 - probability) / count)
    return {
        "simulationCount": count,
        "monteCarloStandardError": round(standard_error, 8),
        "monteCarlo95HalfWidth": round(1.959964 * standard_error, 8),
        "excludes": "MODEL_UNCERTAINTY",
    }


def path_digest(traces: Any) -> str | None:
    """Stable digest of the persisted sequential path, or None when absent."""

    if not traces:
        return None
    payload = json.dumps(traces, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

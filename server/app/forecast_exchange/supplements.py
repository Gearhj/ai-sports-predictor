"""Verify and register research supplements (AISP_RESEARCH_SUPPLEMENT_V1).

WHAT THIS IS
------------
Forecast bundles enforce an exact three-file inventory, so simulation and
benchmark evidence cannot be dropped into them. A SUPPLEMENT is the separate,
content-addressed container for that evidence:

    <exchange>/nfl-labs-supplements/nfl-labs-supplement-index.json
    <exchange>/nfl-labs-supplements/sha256/<64-hex>/manifest.json
    <exchange>/nfl-labs-supplements/sha256/<64-hex>/supplement.json

Four kinds are supported, each with its own required summaries:

    JOINT_SCORE_SIMULATION   a joint final-score distribution for one event
    DRIVE_SIMULATION         persisted possessions for one simulated game
    SCENARIO_COMPARISON      precomputed alternate-assumption results
    MODEL_BENCHMARK_STUDY    machine-readable model comparison results

WHAT IT MAY NOT DO
------------------
It executes no model, calls no provider, grants no wagering authority and
promotes nothing. Every supplement must declare research-shadow-only status
and must bind itself to its event/engine/forecast by explicit identifiers;
the importer never infers a binding.

VERIFICATION ORDER (fail closed at the first violation)
-------------------------------------------------------
1. The directory name is a 64-hex address.
2. The directory holds exactly ``manifest.json`` and ``supplement.json``.
3. ``manifest.json`` is canonical JSON, declares that address, and its own
   canonical identity (the manifest minus ``supplementSha256``) recomputes to it.
4. Every artifact the manifest lists exists and its bytes hash and length match
   exactly, BEFORE any file is interpreted as data.
5. ``supplement.json`` is canonical JSON and agrees with the manifest on id,
   kind, sport and contract.
6. Governance flags are fail-closed in both documents.
7. The kind-specific summaries are internally consistent (a distribution sums
   to one and reproduces its own stated outcome probabilities; a drive replay
   reconciles to its own final score; a scenario names the inputs it changed).
8. At registration an event-bound supplement must match a registered shadow
   forecast on event, engine AND forecast id.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.gen2.exchange.canonical_json import canonical_json_bytes, canonical_sha256
from app.models import ResearchSupplement, ShadowForecast

CONTRACT_VERSION = "AISP_RESEARCH_SUPPLEMENT_V1"
MANIFEST_SCHEMA = "AISP_RESEARCH_SUPPLEMENT_MANIFEST_V1"
INDEX_SCHEMA = "AISP_NFL_LABS_SUPPLEMENT_INDEX_V1"
INDEX_FILENAME = "nfl-labs-supplement-index.json"
SUPPLEMENT_ROOT = "nfl-labs-supplements"

KIND_JOINT = "JOINT_SCORE_SIMULATION"
KIND_DRIVE = "DRIVE_SIMULATION"
KIND_SCENARIO = "SCENARIO_COMPARISON"
KIND_STUDY = "MODEL_BENCHMARK_STUDY"
KINDS = frozenset({KIND_JOINT, KIND_DRIVE, KIND_SCENARIO, KIND_STUDY})
EVENT_BOUND_KINDS = frozenset({KIND_JOINT, KIND_DRIVE, KIND_SCENARIO})

SIMULATION_KINDS = frozenset({"EXACT_DISTRIBUTION", "MONTE_CARLO", "DETERMINISTIC", "NONE"})
DRIVE_RESULTS = frozenset({
    "TOUCHDOWN", "FIELD_GOAL", "MISSED_FIELD_GOAL", "PUNT", "TURNOVER", "TURNOVER_ON_DOWNS",
    "SAFETY", "END_OF_HALF", "END_OF_GAME", "KNEEL",
})
BENCHMARK_TIMING_CLASSES = frozenset({"HISTORICAL_UNTIMED", "TIMESTAMP_MATCHED", "NONE"})

_ADDRESS = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_FILES = frozenset({"manifest.json", "supplement.json"})
_PROBABILITY_TOLERANCE = 5e-4


class SupplementImportError(RuntimeError):
    """A supplement is missing, corrupted, non-canonical, unbound or unsafe."""


@dataclass
class VerifiedSupplement:
    address: str
    manifest: dict[str, Any]
    supplement: dict[str, Any]
    files: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def kind(self) -> str:
        return str(self.supplement["kind"])

    @property
    def binding(self) -> dict[str, Any]:
        return dict(self.supplement.get("binding") or {})


# ---------------------------------------------------------------------------
# loading helpers
# ---------------------------------------------------------------------------

def _load_canonical(path: Path, *, label: str) -> dict[str, Any]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SupplementImportError(f"{label} is not valid JSON: {error}") from error
    if not isinstance(value, dict):
        raise SupplementImportError(f"{label} must be a JSON object")
    if raw != canonical_json_bytes(value):
        raise SupplementImportError(f"{label} is not encoded with canonical exchange JSON")
    return value


def _require(mapping: dict[str, Any], key: str, *, label: str) -> Any:
    if key not in mapping or mapping[key] is None:
        raise SupplementImportError(f"{label} is missing required field '{key}'")
    return mapping[key]


def _iso(value: Any, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise SupplementImportError(f"{label} is not an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise SupplementImportError(f"{label} must carry a timezone")
    return parsed.astimezone(UTC)


def _governance_ok(doc: dict[str, Any], *, label: str) -> None:
    gov = doc.get("governance")
    if not isinstance(gov, dict):
        raise SupplementImportError(f"{label} declares no governance block")
    if str(gov.get("status", "")).upper() != "RESEARCH_SHADOW":
        raise SupplementImportError(f"{label} is not RESEARCH_SHADOW")
    if gov.get("researchShadowOnly") is not True:
        raise SupplementImportError(f"{label} is not research-shadow-only")
    for flag in ("executionAllowedInAisp", "modelPromotionAllowed", "wageringAuthority"):
        if gov.get(flag) is not False:
            raise SupplementImportError(f"{label} must declare {flag}=false")


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------

def verify_supplement(directory: Path) -> VerifiedSupplement:
    directory = Path(directory)
    address = directory.name.lower()
    if not _ADDRESS.fullmatch(address):
        raise SupplementImportError("Supplement directory is not a 64-hex content address")
    if not directory.is_dir():
        raise SupplementImportError("Supplement directory does not exist")
    names = {child.name for child in directory.iterdir()}
    if names != _REQUIRED_FILES:
        raise SupplementImportError(
            f"Supplement must contain exactly {sorted(_REQUIRED_FILES)}; found {sorted(names)}"
        )

    manifest = _load_canonical(directory / "manifest.json", label="manifest.json")
    if manifest.get("schemaVersion") != MANIFEST_SCHEMA:
        raise SupplementImportError("manifest.json schemaVersion is not supported")
    declared = str(_require(manifest, "supplementSha256", label="manifest.json")).lower()
    if declared != address:
        raise SupplementImportError("manifest.json declares a different supplement address")
    identity = {k: v for k, v in manifest.items() if k != "supplementSha256"}
    if canonical_sha256(identity) != address:
        raise SupplementImportError("manifest.json canonical identity does not recompute to its address")
    if manifest.get("contractVersion") != CONTRACT_VERSION:
        raise SupplementImportError("manifest.json contractVersion is not supported")
    _governance_ok(manifest, label="manifest.json")

    # Hash every listed artifact BEFORE interpreting any of it.
    artifacts = _require(manifest, "artifacts", label="manifest.json")
    if not isinstance(artifacts, list) or not artifacts:
        raise SupplementImportError("manifest.json lists no artifacts")
    files: dict[str, dict[str, Any]] = {}
    for item in artifacts:
        name = str(_require(item, "file", label="manifest artifact"))
        if name not in _REQUIRED_FILES or name == "manifest.json":
            raise SupplementImportError(f"manifest.json lists an unsupported artifact '{name}'")
        raw = (directory / name).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != str(item.get("sha256", "")).lower():
            raise SupplementImportError(f"{name} bytes do not hash to the manifest digest")
        if int(item.get("bytes", -1)) != len(raw):
            raise SupplementImportError(f"{name} length does not match the manifest")
        files[name] = {"sha256": digest, "bytes": len(raw)}
    if "supplement.json" not in files:
        raise SupplementImportError("manifest.json does not list supplement.json")

    supplement = _load_canonical(directory / "supplement.json", label="supplement.json")
    if supplement.get("schemaVersion") != CONTRACT_VERSION:
        raise SupplementImportError("supplement.json schemaVersion is not supported")
    for key in ("supplementId", "kind", "sport"):
        if supplement.get(key) != manifest.get(key):
            raise SupplementImportError(f"supplement.json and manifest.json disagree on '{key}'")
    _governance_ok(supplement, label="supplement.json")

    kind = str(supplement["kind"])
    if kind not in KINDS:
        raise SupplementImportError(f"Unsupported supplement kind '{kind}'")
    _require(supplement, "artifactVersion", label="supplement.json")
    _iso(_require(supplement, "sourceCutoffUtc", label="supplement.json"), label="sourceCutoffUtc")
    _iso(_require(supplement, "publishedUtc", label="supplement.json"), label="publishedUtc")
    simulation = _require(supplement, "simulation", label="supplement.json")
    if str(simulation.get("kind")) not in SIMULATION_KINDS:
        raise SupplementImportError("simulation.kind is not supported")
    if simulation["kind"] == "MONTE_CARLO":
        if int(simulation.get("count") or 0) <= 0:
            raise SupplementImportError("A Monte Carlo supplement must state a positive draw count")
        if "seed" not in simulation:
            raise SupplementImportError("A Monte Carlo supplement must state its seed")
    if kind in EVENT_BOUND_KINDS:
        binding = _require(supplement, "binding", label="supplement.json")
        for key in ("eventId", "engineId", "forecastId"):
            _require(binding, key, label="supplement.json binding")

    summaries = _require(supplement, "summaries", label="supplement.json")
    _verify_kind(kind, supplement, summaries)
    return VerifiedSupplement(address=address, manifest=manifest, supplement=supplement, files=files)


def _verify_kind(kind: str, supplement: dict[str, Any], summaries: dict[str, Any]) -> None:
    if kind == KIND_JOINT:
        _verify_joint(summaries)
    elif kind == KIND_DRIVE:
        _verify_drives(summaries)
    elif kind == KIND_SCENARIO:
        _verify_scenarios(summaries)
    elif kind == KIND_STUDY:
        _verify_study(summaries)


def joint_outcomes(cells: list[dict[str, Any]]) -> dict[str, float]:
    """Home/away/tie mass of a joint distribution, plus its expected scores."""

    home = away = tie = 0.0
    exp_home = exp_away = 0.0
    total = 0.0
    for cell in cells:
        p = float(cell["probability"])
        h, a = int(cell["homeScore"]), int(cell["awayScore"])
        total += p
        exp_home += p * h
        exp_away += p * a
        if h > a:
            home += p
        elif a > h:
            away += p
        else:
            tie += p
    return {
        "total": total, "home": home, "away": away, "tie": tie,
        "expectedHome": exp_home, "expectedAway": exp_away,
    }


def _verify_joint(summaries: dict[str, Any]) -> None:
    cells = _require(summaries, "jointScoreDistribution", label="summaries")
    if not isinstance(cells, list) or not cells:
        raise SupplementImportError("jointScoreDistribution is empty")
    seen: set[tuple[int, int]] = set()
    for cell in cells:
        key = (int(cell["homeScore"]), int(cell["awayScore"]))
        if key in seen:
            raise SupplementImportError(f"jointScoreDistribution repeats score {key}")
        seen.add(key)
        if not (0.0 <= float(cell["probability"]) <= 1.0):
            raise SupplementImportError("jointScoreDistribution carries a probability outside [0, 1]")
    outcome = joint_outcomes(cells)
    if abs(outcome["total"] - 1.0) > 1e-3:
        raise SupplementImportError(f"jointScoreDistribution sums to {outcome['total']:.6f}, not 1")
    # The stated outcome probabilities must be the distribution's own.
    for stated, derived in (("homeWinProbability", "home"), ("awayWinProbability", "away"),
                            ("tieProbability", "tie")):
        value = summaries.get(stated)
        if value is None:
            raise SupplementImportError(f"summaries is missing '{stated}'")
        if abs(float(value) - outcome[derived]) > _PROBABILITY_TOLERANCE:
            raise SupplementImportError(
                f"{stated} {float(value):.4f} is not the joint distribution's own {outcome[derived]:.4f}"
            )


def _verify_drives(summaries: dict[str, Any]) -> None:
    drives = _require(summaries, "drives", label="summaries")
    final = _require(summaries, "finalScore", label="summaries")
    if not isinstance(drives, list) or not drives:
        raise SupplementImportError("drives is empty")
    home = away = 0
    previous_index = -1
    for drive in drives:
        index = int(_require(drive, "driveIndex", label="drive"))
        if index != previous_index + 1:
            raise SupplementImportError("drives are not contiguous from 0")
        previous_index = index
        if str(drive.get("offense")) not in ("HOME", "AWAY"):
            raise SupplementImportError("drive.offense must be HOME or AWAY")
        for key in ("startYardLine", "endYardLine"):
            yard = float(_require(drive, key, label="drive"))
            if not (0.0 <= yard <= 100.0):
                raise SupplementImportError(f"drive.{key} must be within 0..100")
        if str(drive.get("result")) not in DRIVE_RESULTS:
            raise SupplementImportError(f"drive.result '{drive.get('result')}' is not supported")
        int(_require(drive, "period", label="drive"))
        home_after = int(_require(drive, "homeScoreAfter", label="drive"))
        away_after = int(_require(drive, "awayScoreAfter", label="drive"))
        if home_after < home or away_after < away:
            raise SupplementImportError("a drive lowered a score")
        home, away = home_after, away_after
    if int(final.get("home", -1)) != home or int(final.get("away", -1)) != away:
        raise SupplementImportError("the drive replay does not reconcile to its own final score")


def _verify_scenarios(summaries: dict[str, Any]) -> None:
    baseline = _require(summaries, "baseline", label="summaries")
    scenarios = _require(summaries, "scenarios", label="summaries")
    inputs = _require(summaries, "inputsUsed", label="summaries")
    if not isinstance(inputs, list) or not inputs:
        raise SupplementImportError("inputsUsed must name at least one model input")
    for key in ("homeWinProbability", "awayWinProbability", "tieProbability"):
        _require(baseline, key, label="baseline")
    if not isinstance(scenarios, list) or not scenarios:
        raise SupplementImportError("scenarios is empty")
    used = set(map(str, inputs))
    ids: set[str] = set()
    for scenario in scenarios:
        sid = str(_require(scenario, "scenarioId", label="scenario"))
        if sid in ids:
            raise SupplementImportError(f"scenario '{sid}' repeats")
        ids.add(sid)
        changes = _require(scenario, "changedAssumptions", label="scenario")
        if not isinstance(changes, list) or not changes:
            raise SupplementImportError(f"scenario '{sid}' changes nothing")
        for change in changes:
            name = str(_require(change, "input", label="changed assumption"))
            if name not in used:
                raise SupplementImportError(
                    f"scenario '{sid}' changes '{name}', which the engine does not declare as an input"
                )
        result = _require(scenario, "result", label="scenario")
        for key in ("homeWinProbability", "awayWinProbability", "tieProbability"):
            _require(result, key, label=f"scenario '{sid}' result")


def _verify_study(summaries: dict[str, Any]) -> None:
    rows = _require(summaries, "leaderboard", label="summaries")
    if not isinstance(rows, list) or not rows:
        raise SupplementImportError("leaderboard is empty")
    cohort = _require(summaries, "cohort", label="summaries")
    int(_require(cohort, "games", label="cohort"))
    timing = str(_require(summaries, "benchmarkTimingClass", label="summaries"))
    if timing not in BENCHMARK_TIMING_CLASSES:
        raise SupplementImportError("benchmarkTimingClass is not supported")
    for row in rows:
        _require(row, "pipelineId", label="leaderboard row")


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------

def current_supplement_addresses(root: Path) -> set[str] | None:
    """Addresses the index declares current; None when there is no index."""

    path = Path(root) / SUPPLEMENT_ROOT / INDEX_FILENAME
    if not path.is_file():
        return None
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(index, dict) or index.get("schemaVersion") != INDEX_SCHEMA:
        return None
    found: set[str] = set()
    for value in index.get("currentSupplementSha256s") or []:
        text = str(value).lower()
        if _ADDRESS.fullmatch(text):
            found.add(text)
    return found


def discover_supplements(root: Path) -> list[Path]:
    base = (Path(root) / SUPPLEMENT_ROOT / "sha256")
    if not base.is_dir():
        return []
    resolved_root = Path(root).resolve()
    found: list[Path] = []
    for entry in sorted(base.iterdir()):
        if not _ADDRESS.fullmatch(entry.name.lower()):
            continue
        candidate = entry.resolve()
        if resolved_root not in candidate.parents or not candidate.is_dir():
            continue
        found.append(candidate)
    return found


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------

def register_supplement(session: Session, verified: VerifiedSupplement) -> dict[str, Any]:
    """Persist one verified supplement; idempotent by content address."""

    existing = session.scalar(
        select(ResearchSupplement).where(ResearchSupplement.supplement_sha256 == verified.address)
    )
    if existing is not None:
        return {"supplement_sha256": existing.supplement_sha256, "kind": existing.kind,
                "idempotent_reuse": True}

    doc = verified.supplement
    binding = verified.binding
    forecast: ShadowForecast | None = None
    if verified.kind in EVENT_BOUND_KINDS:
        forecast = session.scalar(select(ShadowForecast).where(
            ShadowForecast.forecast_id == str(binding["forecastId"]),
            ShadowForecast.event_id == str(binding["eventId"]),
            ShadowForecast.engine_id == str(binding["engineId"]),
        ))
        if forecast is None:
            raise SupplementImportError(
                "Supplement binds to a forecast AISP has not registered "
                f"({binding['engineId']} / {binding['eventId']} / {binding['forecastId']})"
            )
        declared_artifact = binding.get("modelArtifactSha256")
        if declared_artifact and str(declared_artifact).lower() != forecast.model_artifact_sha256:
            raise SupplementImportError("Supplement binds to a different model artifact than the forecast")

    simulation = doc["simulation"]
    row = ResearchSupplement(
        supplement_sha256=verified.address,
        supplement_id=str(doc["supplementId"]),
        contract_version=CONTRACT_VERSION,
        kind=verified.kind,
        sport=str(doc["sport"]).upper(),
        event_id=str(binding["eventId"]) if binding.get("eventId") else None,
        engine_id=str(binding["engineId"]) if binding.get("engineId") else None,
        forecast_id=str(binding["forecastId"]) if binding.get("forecastId") else None,
        shadow_forecast_id=forecast.id if forecast else None,
        artifact_version=str(doc["artifactVersion"]),
        simulation_kind=str(simulation["kind"]),
        simulation_count=int(simulation["count"]) if simulation.get("count") is not None else None,
        simulation_seed=int(simulation["seed"]) if simulation.get("seed") is not None else None,
        source_cutoff_utc=_iso(doc["sourceCutoffUtc"], label="sourceCutoffUtc"),
        published_utc=_iso(doc["publishedUtc"], label="publishedUtc"),
        status="RESEARCH_SHADOW",
        research_shadow_only=True,
        payload={
            "summaries": doc.get("summaries") or {},
            "paths": doc.get("paths") or [],
            "scenarios": doc.get("scenarios") or [],
            "producer": doc.get("producer") or verified.manifest.get("producer") or {},
        },
        evidence={
            "files": verified.files,
            "manifest_schema": MANIFEST_SCHEMA,
            "binding": binding,
            "governance": doc.get("governance"),
            "verified_at_utc": datetime.now(UTC).isoformat(),
        },
    )
    session.add(row)
    session.flush()
    return {"supplement_sha256": verified.address, "kind": verified.kind,
            "event_id": row.event_id, "engine_id": row.engine_id,
            "forecast_id": row.forecast_id, "idempotent_reuse": False}


__all__ = [
    "CONTRACT_VERSION", "INDEX_SCHEMA", "KIND_DRIVE", "KIND_JOINT", "KIND_SCENARIO", "KIND_STUDY",
    "SupplementImportError", "VerifiedSupplement", "current_supplement_addresses",
    "discover_supplements", "joint_outcomes", "register_supplement", "verify_supplement",
]

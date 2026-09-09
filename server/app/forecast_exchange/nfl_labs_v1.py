"""Read-only verification and append-only registration of NFL_LABS_CONTRACT_V1.

This producer family has an artifact-addressed, two-file inventory. It stays
separate from both forecast bundles and the older generic supplement format.
Drive engines retain their own identity; relatedM1ForecastId binds only the
event relationship and never makes a drive distribution an M1 forecast.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import math
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.forecast_exchange.supplements import (
    KIND_DRIVE, KIND_JOINT, SupplementImportError, _iso, _load_canonical,
    _verify_joint, joint_outcomes,
)
from app.models import ResearchSupplement, ShadowForecast, ShadowForecastBundle

CONTRACT = "NFL_LABS_CONTRACT_V1"
CURRENT = "NFL_LABS_CURRENT_SIMULATIONS_V1"
MANIFEST = "NFL_LABS_ARTIFACT_MANIFEST_V1"
SCORE = "NFL_LABS_SCORE_DISTRIBUTION_V1"
DRIVE = "NFL_LABS_DRIVE_SIMULATION_V1"
M1 = "NFL_M1_XGBOOST"
DRIVE_ENGINE = "NFL_DEVELOPMENT_CONDITIONAL_DRIVE_V2"
ROOT_NAME = "nfl-labs-simulation-supplements"


@dataclass(frozen=True)
class VerifiedLabsArtifact:
    address: str
    artifact: dict[str, Any]
    files: dict[str, Any]

    @property
    def kind(self) -> str:
        return KIND_DRIVE if self.artifact["schemaVersion"] == DRIVE else KIND_JOINT


def _governance(doc: dict[str, Any]) -> None:
    if doc.get("status") != "RESEARCH_SHADOW" or doc.get("promotionStatus") != "NOT_PROMOTED":
        raise SupplementImportError("NFL Labs artifact must remain unpromoted research shadow")
    if doc.get("executionAllowedInAISP") is not False or doc.get("wageringAuthority") is not False:
        raise SupplementImportError("NFL Labs execution and wagering authority must be false")


def _verified_file(reference: dict[str, Any], expected: Path) -> bytes:
    path = Path(str(reference.get("path", ""))).resolve()
    if path != expected.resolve():
        raise SupplementImportError("NFL Labs file reference escapes its declared location")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != reference.get("sha256"):
        raise SupplementImportError(f"{path.name} bytes do not hash to the declared checksum")
    if len(raw) != reference.get("byteCount"):
        raise SupplementImportError(f"{path.name} byte count does not match")
    return raw


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or int(value) != value:
        raise SupplementImportError(f"{label} must be a finite integer")
    return int(value)


def _verify_distribution(doc: dict[str, Any]) -> None:
    cells = doc.get("jointScoreDistribution") or []
    summary = doc.get("scoreSummary") or {}
    _verify_joint({"jointScoreDistribution": cells, **summary})
    count = _integer(doc.get("drawCount"), "drawCount")
    if count <= 0:
        raise SupplementImportError("drawCount must be positive")
    _integer(doc.get("seed"), "seed")
    margin: dict[int, float] = defaultdict(float)
    total: dict[int, float] = defaultdict(float)
    for cell in cells:
        h, a = _integer(cell["homeScore"], "homeScore"), _integer(cell["awayScore"], "awayScore")
        if min(h, a) < 0:
            raise SupplementImportError("score must not be negative")
        p = float(cell["probability"])
        if abs(p * count - round(p * count)) > 1e-5:
            raise SupplementImportError("joint distribution does not reconcile to drawCount")
        margin[h-a] += p
        total[h+a] += p
    for key, field, expected in (("scoreMarginDistribution", "homeMinusAway", margin),
                                 ("totalScoreDistribution", "totalScore", total)):
        rows = doc.get(key) or []
        supplied = {int(r[field]): float(r["probability"]) for r in rows}
        if len(rows) != len(supplied) or set(supplied) != set(expected) or any(
            not math.isclose(supplied[k], v, abs_tol=1e-6) for k, v in expected.items()
        ):
            raise SupplementImportError(f"{key} differs from its own joint distribution")
    outcomes = joint_outcomes(cells)
    for key, value in (("expectedHomeScore", outcomes["expectedHome"]),
                       ("expectedAwayScore", outcomes["expectedAway"]),
                       ("expectedTotal", outcomes["expectedHome"] + outcomes["expectedAway"]),
                       ("expectedMarginHomeMinusAway", outcomes["expectedHome"] - outcomes["expectedAway"])):
        if not math.isclose(float(summary.get(key, float("nan"))), value, abs_tol=1e-5):
            raise SupplementImportError(f"{key} differs from its own joint distribution")
    supported = {(int(c["homeScore"]), int(c["awayScore"])) for c in cells}
    for final in doc.get("representativeFinals") or []:
        if (final["homeScore"], final["awayScore"]) not in supported:
            raise SupplementImportError("representative final has no mass in its distribution")


def _verify_paths(doc: dict[str, Any]) -> None:
    paths = doc.get("simulatedDrives") or []
    population = doc.get("populationFinals") or []
    if len(population) != doc["drawCount"] or not paths:
        raise SupplementImportError("drive population or representative games are missing")
    population_counts = Counter((p["homeScore"], p["awayScore"]) for p in population)
    if population_counts != Counter({(c["homeScore"], c["awayScore"]): round(c["probability"] * doc["drawCount"])
                                     for c in doc["jointScoreDistribution"]}):
        raise SupplementImportError("drive population differs from the published distribution")
    for path in paths:
        index = _integer(path.get("simulationIndex"), "simulationIndex")
        if index < 0 or index >= len(population) or any(path[k] != population[index][k] for k in ("homeScore", "awayScore")):
            raise SupplementImportError("representative simulation does not match its population index")
        home = away = 0
        prior = None
        for number, drive in enumerate(path.get("drives") or [], 1):
            if drive.get("driveNumber") != number or drive.get("possession") not in ("HOME", "AWAY"):
                raise SupplementImportError("drive numbering or possession is invalid")
            side = "homeTeam" if drive["possession"] == "HOME" else "awayTeam"
            if drive.get("offenseTeam") not in {doc[side].get(key) for key in ("teamId", "sourceTeamId", "abbreviation")}:
                raise SupplementImportError("drive offense does not match its team identity")
            period = drive.get("period")
            if period not in ("REGULATION_HALF_1", "REGULATION_HALF_2", "OVERTIME"):
                raise SupplementImportError("drive period is unsupported")
            before, after, duration = (float(drive[k]) for k in ("periodSecondsBefore", "periodSecondsAfter", "durationSeconds"))
            if not all(math.isfinite(v) for v in (before, after, duration)) or min(before, after, duration) < -1e-6 or not math.isclose(before-after, duration, abs_tol=1e-5):
                raise SupplementImportError("drive clock does not reconcile")
            if prior is not None and prior["period"] == period and not math.isclose(prior["periodSecondsAfter"], before, abs_tol=1e-5):
                raise SupplementImportError("drive clocks are not contiguous")
            for key in ("startYardline100", "endYardline100"):
                yard = drive.get(key)
                if yard is None and key == "endYardline100" and drive.get("outcome") == "CLOCK_EXPIRED":
                    continue
                if not isinstance(yard, (int, float)) or not math.isfinite(yard) or not 0 <= yard <= 100:
                    raise SupplementImportError("drive yard line is invalid")
            if drive.get("outcome") not in {"TD", "FG", "DEF_TD", "PUNT", "TURNOVER", "DOWNS", "SAFETY", "MISSED_FG", "CLOCK_EXPIRED"}:
                raise SupplementImportError("drive outcome is unsupported")
            hp, ap = (_integer(drive[k], k) for k in ("homePointsAdded", "awayPointsAdded"))
            if min(hp, ap) < 0 or home+hp != drive.get("homeScoreAfter") or away+ap != drive.get("awayScoreAfter"):
                raise SupplementImportError("drive score accounting does not reconcile")
            home, away = home+hp, away+ap
            prior = drive
        if prior is None or home != path["homeScore"] or away != path["awayScore"]:
            raise SupplementImportError("drive replay does not reconcile to its final score")


def verify_labs_current(exchange_root: Path) -> list[VerifiedLabsArtifact]:
    """Verify the current manifest and every byte before producing registerable objects."""
    root = Path(exchange_root).resolve()
    directory = root / ROOT_NAME
    pointer = _load_canonical(directory / "current.json", label="NFL Labs current.json")
    if pointer.get("schemaVersion") != CURRENT:
        raise SupplementImportError("unsupported NFL Labs pointer")
    _governance(pointer)
    contract_path = root / "contracts" / f"{CONTRACT}.json"
    _verified_file(pointer.get("coordinationContract") or {}, contract_path)
    contract = _load_canonical(contract_path, label=CONTRACT)
    if contract.get("schemaVersion") != CONTRACT:
        raise SupplementImportError("unsupported NFL Labs contract")
    _governance(contract)
    verified = []
    seen = set()
    for entry in pointer.get("artifacts") or []:
        reference = entry.get("artifact") or {}
        address = str(reference.get("sha256", ""))
        if len(address) != 64 or any(c not in "0123456789abcdef" for c in address) or address in seen:
            raise SupplementImportError("invalid or repeated NFL Labs content address")
        seen.add(address)
        artifact_dir = directory / "sha256" / address
        if directory not in artifact_dir.resolve().parents or {p.name for p in artifact_dir.iterdir()} != {"manifest.json", "artifact.json"}:
            raise SupplementImportError("NFL Labs artifact inventory or directory is invalid")
        _verified_file(entry.get("manifest") or {}, artifact_dir / "manifest.json")
        manifest = _load_canonical(artifact_dir / "manifest.json", label="NFL Labs manifest")
        if manifest.get("schemaVersion") != MANIFEST or manifest.get("files") != [reference]:
            raise SupplementImportError("NFL Labs manifest differs from the current pointer")
        _governance(manifest)
        _verified_file(reference, artifact_dir / "artifact.json")
        doc = _load_canonical(artifact_dir / "artifact.json", label="NFL Labs artifact")
        _governance(doc)
        schema = doc.get("schemaVersion")
        if schema not in (SCORE, DRIVE) or schema != entry.get("artifactSchemaVersion") or schema != manifest.get("artifactSchemaVersion"):
            raise SupplementImportError("NFL Labs schema binding mismatch")
        if doc.get("sport") != "NFL" or doc.get("eventId") != entry.get("eventId") or doc.get("forecastId") != entry.get("forecastId") or (doc.get("model") or {}).get("engineId") != entry.get("modelId"):
            raise SupplementImportError("NFL Labs event/forecast/model binding mismatch")
        for key in ("sourceCutoffUtc", "forecastCreatedUtc", "scheduledStartUtc", "publishedAtUtc"):
            _iso(doc.get(key), label=key)
        _verify_distribution(doc)
        if schema == DRIVE:
            if entry["modelId"] != DRIVE_ENGINE or doc.get("explainsM1Forecast") is not False or not doc.get("relatedM1ForecastId"):
                raise SupplementImportError("drive artifact must identify its independent engine and related M1 forecast")
            scenario = doc.get("scenario") or {}
            expected = {"BASE": (1.0, False), "PACE_DURATION_0.95": (0.95, True)}.get(scenario.get("scenarioId"))
            if expected is None or (scenario.get("paceDurationMultiplier"), scenario.get("modelInputChange")) != expected:
                raise SupplementImportError("unsupported drive scenario assumption")
            _verify_paths(doc)
        elif entry["modelId"] != M1 or (doc.get("sourceForecast") or {}).get("forecastId") != doc["forecastId"]:
            raise SupplementImportError("score artifact must bind to its exact M1 source forecast")
        verified.append(VerifiedLabsArtifact(address, doc, {"artifact": reference, "manifest": entry["manifest"], "contract": pointer["coordinationContract"]}))
    if not verified:
        raise SupplementImportError("NFL Labs pointer has no artifacts")
    if sum(v.kind == KIND_JOINT for v in verified) != pointer.get("forecastCount") or sum(v.kind == KIND_DRIVE for v in verified) != pointer.get("driveResearchArtifactCount", 0):
        raise SupplementImportError("NFL Labs artifact counts differ from the pointer")
    return verified


def _parent(session: Session, item: VerifiedLabsArtifact) -> ShadowForecast:
    doc = item.artifact
    source_id = doc.get("relatedM1ForecastId") if item.kind == KIND_DRIVE else doc["forecastId"]
    row = session.scalar(select(ShadowForecast).where(ShadowForecast.forecast_id == source_id,
        ShadowForecast.event_id == doc["eventId"], ShadowForecast.engine_id == M1))
    if row is None:
        raise SupplementImportError("NFL Labs source forecast is not registered for the exact event and engine")
    if (row.home_team_id, row.away_team_id) != (doc["homeTeam"]["teamId"], doc["awayTeam"]["teamId"]):
        raise SupplementImportError("NFL Labs home/away identity differs from the registered forecast")
    kickoff = row.scheduled_start_utc
    if kickoff is not None and kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=UTC)
    if _iso(doc["scheduledStartUtc"], label="scheduledStartUtc") != kickoff:
        raise SupplementImportError("NFL Labs kickoff differs from the registered forecast")
    if item.kind == KIND_JOINT and doc["model"].get("modelBinarySha256") != row.model_artifact_sha256:
        raise SupplementImportError("NFL Labs M1 model artifact differs from the registered forecast")
    if item.kind == KIND_JOINT:
        bundle = session.get(ShadowForecastBundle, row.bundle_id)
        if bundle is None or (doc.get("sourceForecast") or {}).get("sha256") != bundle.bundle_sha256:
            raise SupplementImportError("NFL Labs source bundle differs from the registered forecast")
    return row


def register_labs_current(session: Session, verified: list[VerifiedLabsArtifact], *, dry_run: bool = False) -> list[dict[str, Any]]:
    """Only ResearchSupplement inserts; validate all bindings before any write."""
    if not dry_run and session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": 0x4E464C4C414253})
    parents = [_parent(session, item) for item in verified]
    records = []
    for item, parent in zip(verified, parents, strict=True):
        doc = item.artifact
        existing = session.scalar(select(ResearchSupplement).where(ResearchSupplement.supplement_sha256 == item.address))
        record = {"supplement_sha256": item.address, "kind": item.kind, "event_id": doc["eventId"],
                  "engine_id": doc["model"]["engineId"], "forecast_id": doc["forecastId"],
                  "scenario_id": (doc.get("scenario") or {}).get("scenarioId"), "idempotent_reuse": existing is not None}
        if existing is None and not dry_run:
            session.add(ResearchSupplement(
                supplement_sha256=item.address, supplement_id=f"nfl-labs:sha256:{item.address}",
                contract_version=CONTRACT, kind=item.kind, sport="NFL", event_id=doc["eventId"],
                engine_id=doc["model"]["engineId"], forecast_id=doc["forecastId"],
                shadow_forecast_id=parent.id if item.kind == KIND_JOINT else None,
                artifact_version=doc["schemaVersion"], simulation_kind="MONTE_CARLO",
                simulation_count=doc["drawCount"], simulation_seed=doc["seed"],
                source_cutoff_utc=_iso(doc["sourceCutoffUtc"], label="sourceCutoffUtc"),
                published_utc=_iso(doc["publishedAtUtc"], label="publishedAtUtc"),
                status="RESEARCH_SHADOW", research_shadow_only=True,
                payload={"summaries": {**doc["scoreSummary"], "jointScoreDistribution": doc["jointScoreDistribution"]},
                         "paths": doc.get("simulatedDrives") or [], "artifact": doc},
                evidence={"files": item.files, "manifest_schema": MANIFEST,
                          "related_m1_forecast_id": parent.forecast_id,
                          "independent_drive_engine": item.kind == KIND_DRIVE,
                          "verified_at_utc": datetime.now(UTC).isoformat()},
            ))
            session.flush()
        records.append(record)
    return records

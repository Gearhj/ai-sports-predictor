"""Read-only verification and append-only registration of the repaired NFL
current-state research forecasts (NFL_CURRENT_STATE_JOINT_DISTRIBUTION_HANDOFF_V2).

WHY THIS FAMILY IS SEPARATE FROM M1, AND MUST STAY SEPARATE
-----------------------------------------------------------
These 60 documents are NOT current forecasts and must never be shown as if
they were. Every one of them declares itself a ``HISTORICAL_PRIOR_BASELINE``:
its evidence stops at the 2024 season, it knows no confirmed starter, no
injury and no weather, and the 2025 initialisation it would need is sealed.
Its own producer says so in ``missingInputs`` and in
``sourceCutoffMeaning`` -- "latest registered retrieval of RECONSTRUCTED
HISTORICAL inputs, not current team evidence".

M1 is the retained NFL model. These are independent of it -- ``independentOfM1``
is true and ``explainsM1Forecast`` is false -- so they may not be pooled with
M1's cohort, ranked against it in a shared table, or averaged into anything.
They are registered under their own supplement kind precisely so that no
existing view can pick them up as M1 evidence by accident.

WHAT IS VERIFIED, IN ORDER, FAIL CLOSED
---------------------------------------
1.  The handoff is canonical JSON at the expected schema and is
    governance-clean: research shadow, unpromoted, no wagering authority, no
    execution in AISP, zero model fits, zero provider calls, zero AISP writes,
    and the sealed 2025 payload unread.
2.  Every forecast it lists is hashed and length-checked BEFORE it is read as
    data, and its own governance answers must repeat the handoff's.
3.  Each joint distribution reconciles to itself: 5,000 draws, cell counts and
    probabilities that agree, and stated win/tie probabilities and expected
    scores that are the distribution's own.
4.  The handoff's stated forecast, artifact and event counts must equal what
    was actually verified. A count that disagrees means a document was added
    or dropped between publication and import.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import math
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.forecast_exchange.supplements import (
    SupplementImportError, _iso, _load_canonical, _verify_joint, joint_outcomes,
)
from app.models import ResearchSupplement

HANDOFF = "NFL_CURRENT_STATE_JOINT_DISTRIBUTION_HANDOFF_V2"
FORECAST = "NFL_CURRENT_STATE_GAME_FORECAST_V2"
CONTRACT = HANDOFF

#: A supplement kind of its own. Reusing JOINT_SCORE_SIMULATION would make
#: these historical-prior documents surface in the NFL game views as if they
#: were current research candidates alongside M1, which is exactly the
#: confusion the producer's own classification exists to prevent.
KIND_HISTORICAL_PRIOR = "HISTORICAL_PRIOR_BASELINE"

FORECAST_TYPE = "HISTORICAL_PRIOR_BASELINE"
STATUS = "RESEARCH_SHADOW"
REPAIR_AUTHORITY = "UNCERTAINTY_ONLY_MEAN_EXCLUSION_AND_INNER_DROPOUT_VARIANCE_V1"
MODEL_VERSION = "NFL_UNCERTAINTY_MEAN_SEPARATION_REPAIR_V1"

#: The four repaired branches, and the one the study reports as primary.
ENGINES = (
    "NFL_CURRENT_STATE_DYNAMIC_V2_REPAIRED",
    "NFL_CURRENT_STATE_HIERARCHICAL_V2_REPAIRED",
    "NFL_CURRENT_STATE_RESIDUAL_V2_REPAIRED",
    "NFL_CURRENT_STATE_ENSEMBLE_V2_REPAIRED",
)
PRIMARY_ENGINE = "NFL_CURRENT_STATE_RESIDUAL_V2_REPAIRED"

_PROBABILITY_TOLERANCE = 5e-4


@dataclass(frozen=True)
class VerifiedCurrentStateForecast:
    address: str
    forecast: dict[str, Any]
    files: dict[str, Any]

    @property
    def engine_id(self) -> str:
        return str(self.forecast["model"]["engineId"])


def _governance(doc: dict[str, Any], *, label: str) -> None:
    if doc.get("status") != STATUS:
        raise SupplementImportError(f"{label} is not {STATUS}")
    if doc.get("promotionStatus") != "NOT_PROMOTED":
        raise SupplementImportError(f"{label} claims a promotion status")
    for flag in ("wageringAuthority", "executionAllowedInAISP"):
        if doc.get(flag) is not False:
            raise SupplementImportError(f"{label} must declare {flag}=false")
    if doc.get("forecastType") != FORECAST_TYPE:
        raise SupplementImportError(
            f"{label} is not classified {FORECAST_TYPE}; only historical-prior research "
            "documents may be imported from this family"
        )
    if doc.get("repairAuthority") != REPAIR_AUTHORITY:
        raise SupplementImportError(f"{label} claims a different repair authority")


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
        raise SupplementImportError(f"{label} byte count does not match the handoff")
    return raw


def _verify_distribution(doc: dict[str, Any], *, draws: int) -> None:
    """The distribution must reconcile to itself, not merely be present."""

    cells = doc.get("jointScoreDistribution") or []
    _verify_joint({
        "jointScoreDistribution": cells,
        "homeWinProbability": doc.get("homeWinProbability"),
        "awayWinProbability": doc.get("awayWinProbability"),
        "tieProbability": doc.get("tieProbability"),
    })
    counted = 0
    for cell in cells:
        count = int(cell["count"])
        if count <= 0:
            raise SupplementImportError("a joint cell carries a non-positive draw count")
        if abs(float(cell["probability"]) * draws - count) > 1e-6:
            raise SupplementImportError("joint probabilities do not reconcile to the draw count")
        counted += count
    if counted != draws:
        raise SupplementImportError(f"joint cells sum to {counted} draws, not {draws}")
    outcome = joint_outcomes(cells)
    for key, value in (("expectedHomeScore", outcome["expectedHome"]),
                       ("expectedAwayScore", outcome["expectedAway"]),
                       ("expectedTotal", outcome["expectedHome"] + outcome["expectedAway"]),
                       ("expectedMarginHomeMinusAway", outcome["expectedHome"] - outcome["expectedAway"])):
        if not math.isclose(float(doc.get(key, float("nan"))), value, abs_tol=1e-4):
            raise SupplementImportError(f"{key} is not the joint distribution's own value")


def verify_current_state_handoff(handoff_path: Path, *, expected_sha256: str | None = None
                                 ) -> tuple[dict[str, Any], list[VerifiedCurrentStateForecast]]:
    handoff_path = Path(handoff_path)
    raw = handoff_path.read_bytes()
    address = hashlib.sha256(raw).hexdigest()
    if expected_sha256 and address != str(expected_sha256).lower():
        raise SupplementImportError("handoff does not hash to the expected address")

    handoff = _load_canonical(handoff_path, label="handoff.json")
    if handoff.get("schemaVersion") != HANDOFF or handoff.get("contract") != CONTRACT:
        raise SupplementImportError("unsupported NFL current-state handoff schema")
    _governance(handoff, label="handoff.json")
    if handoff.get("sealed2025Read") is not False:
        raise SupplementImportError("handoff claims the sealed 2025 payload was read")
    for key in ("aispWrites", "modelFits", "providerCalls"):
        if int(handoff.get(key, -1)) != 0:
            raise SupplementImportError(f"handoff declares nonzero {key}")
    if handoff.get("modelVersion") != MODEL_VERSION:
        raise SupplementImportError("handoff names a different repaired model version")
    draws = int(handoff.get("drawsPerForecast", 0))
    if draws <= 0:
        raise SupplementImportError("handoff states no draw count")
    _iso(handoff.get("publishedAtUtc"), label="publishedAtUtc")

    entries = list(handoff.get("forecasts") or [])
    if not entries:
        raise SupplementImportError("handoff lists no forecasts")
    verified: list[VerifiedCurrentStateForecast] = []
    seen: set[str] = set()
    for entry in entries:
        digest = str(entry.get("sha256", "")).lower()
        if len(digest) != 64 or digest in seen:
            raise SupplementImportError("invalid or repeated current-state content address")
        seen.add(digest)
        body = _verified_file(entry, label=f"forecast {digest[:12]}")
        doc = _load_canonical(Path(str(entry["path"])), label="forecast.json")
        if doc.get("schemaVersion") != FORECAST:
            raise SupplementImportError("unsupported current-state forecast schema")
        _governance(doc, label=f"forecast {doc.get('forecastId')}")
        if doc.get("sport") != "NFL":
            raise SupplementImportError("current-state forecast is not NFL")
        if doc.get("independentOfM1") is not True or doc.get("explainsM1Forecast") is not False:
            raise SupplementImportError(
                "current-state forecast must declare itself independent of M1 and must not "
                "claim to explain an M1 forecast"
            )
        if doc.get("sealed2025Excluded") is not True:
            raise SupplementImportError("current-state forecast does not exclude the sealed 2025 payload")
        engine = ((doc.get("model") or {}).get("engineId"))
        if engine not in ENGINES:
            raise SupplementImportError(f"unsupported repaired engine {engine!r}")
        # The classification is the whole point: these know no current starter,
        # roster depth or context, and the document has to keep saying so.
        for flag in ("currentTargetStarterKnown", "currentContextNumericallyIncorporated",
                     "currentRosterDepthNumericallyIncorporated"):
            if doc.get(flag) is not False:
                raise SupplementImportError(
                    f"{flag} is not false; a historical-prior baseline may not claim current inputs")
        if doc.get("publicationClassification") != FORECAST_TYPE:
            raise SupplementImportError("current-state forecast publication classification is wrong")
        for key in ("eventId", "forecastId", "numericalIdentitySha256", "historicalEvidenceThroughDate"):
            if not doc.get(key):
                raise SupplementImportError(f"current-state forecast is missing '{key}'")
        for key in ("sourceCutoffUtc", "forecastCreatedUtc", "forecastCutoffUtc",
                    "scheduledStartUtc", "publishedAtUtc"):
            _iso(doc.get(key), label=key)
        _verify_distribution(doc, draws=draws)
        verified.append(VerifiedCurrentStateForecast(
            address=digest, forecast=doc,
            files={"forecast": {"sha256": digest, "byteCount": len(body)},
                   "handoff": {"sha256": address, "byteCount": len(raw)}},
        ))

    if len(verified) != int(handoff.get("forecastCount", -1)):
        raise SupplementImportError("handoff forecast count differs from what was verified")
    if len(verified) != int(handoff.get("artifactCount", -1)):
        raise SupplementImportError("handoff artifact count differs from what was verified")
    events = {v.forecast["eventId"] for v in verified}
    if len(events) != int(handoff.get("forecastEventCount", -1)):
        raise SupplementImportError("handoff forecast event count differs from what was verified")
    engines = Counter(v.engine_id for v in verified)
    if set(engines) != set(ENGINES):
        raise SupplementImportError("the handoff does not carry all four repaired branches")
    if len(set(engines.values())) != 1:
        raise SupplementImportError("the repaired branches do not cover the same events")
    return handoff, verified


def register_current_state_forecasts(
    session: Session, handoff: dict[str, Any], verified: list[VerifiedCurrentStateForecast],
    *, handoff_sha256: str, dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Only ResearchSupplement inserts, idempotent by forecast content address."""

    records: list[dict[str, Any]] = []
    for item in verified:
        doc = item.forecast
        existing = session.scalar(select(ResearchSupplement).where(
            ResearchSupplement.supplement_sha256 == item.address))
        record = {
            "supplement_sha256": item.address, "kind": KIND_HISTORICAL_PRIOR,
            "event_id": doc["eventId"], "engine_id": item.engine_id,
            "forecast_id": doc["forecastId"], "idempotent_reuse": existing is not None,
        }
        if existing is None and not dry_run:
            session.add(ResearchSupplement(
                supplement_sha256=item.address,
                supplement_id=f"nfl-current-state-repaired-v2:sha256:{item.address}",
                contract_version=CONTRACT, kind=KIND_HISTORICAL_PRIOR, sport="NFL",
                event_id=doc["eventId"], engine_id=item.engine_id,
                forecast_id=doc["forecastId"],
                # Never joined to an M1 shadow forecast. These are independent
                # of M1 and binding them to one would assert a relationship the
                # producer explicitly denies.
                shadow_forecast_id=None,
                artifact_version=FORECAST, simulation_kind="MONTE_CARLO",
                simulation_count=int(handoff["drawsPerForecast"]), simulation_seed=None,
                source_cutoff_utc=_iso(doc["sourceCutoffUtc"], label="sourceCutoffUtc"),
                published_utc=_iso(doc["publishedAtUtc"], label="publishedAtUtc"),
                status=STATUS, research_shadow_only=True,
                payload={"summaries": {
                    "modelLabel": item.engine_id,
                    "forecastType": doc["forecastType"],
                    "homeWinProbability": doc["homeWinProbability"],
                    "awayWinProbability": doc["awayWinProbability"],
                    "tieProbability": doc["tieProbability"],
                    "expectedHomeScore": doc["expectedHomeScore"],
                    "expectedAwayScore": doc["expectedAwayScore"],
                    "expectedTotal": doc["expectedTotal"],
                    "expectedMarginHomeMinusAway": doc["expectedMarginHomeMinusAway"],
                    "homeTeam": doc["homeTeam"], "awayTeam": doc["awayTeam"],
                    "scheduledStartUtc": doc["scheduledStartUtc"],
                    "historicalEvidenceThroughDate": doc["historicalEvidenceThroughDate"],
                    "sourceCutoffMeaning": doc.get("sourceCutoffMeaning"),
                    "missingInputs": doc.get("missingInputs") or [],
                    "uncertainty": doc.get("uncertainty") or {},
                    "evidenceClass": doc.get("evidenceClass"),
                    "trainingEvidenceClass": doc.get("trainingEvidenceClass"),
                    "jointScoreDistribution": doc["jointScoreDistribution"],
                }},
                evidence={"files": item.files, "handoff_schema": HANDOFF,
                          "handoff_sha256": handoff_sha256,
                          "repair_authority": doc["repairAuthority"],
                          "numerical_identity_sha256": doc["numericalIdentitySha256"],
                          "independent_of_m1": True, "explains_m1_forecast": False,
                          "sealed_2025_excluded": True,
                          "verified_at_utc": datetime.now(UTC).isoformat()},
            ))
            session.flush()
        records.append(record)
    return records


__all__ = [
    "CONTRACT", "ENGINES", "FORECAST", "FORECAST_TYPE", "HANDOFF", "KIND_HISTORICAL_PRIOR",
    "PRIMARY_ENGINE", "VerifiedCurrentStateForecast", "register_current_state_forecasts",
    "verify_current_state_handoff",
]

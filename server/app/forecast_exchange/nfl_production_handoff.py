"""Fail-closed adapter for a FUTURE Codex NFL M1 production handoff.

STATUS TODAY
------------
No production handoff exists. NFL M1 is a research-shadow model with paper
research evidence only. This module exists so that, when Model Studio (Codex)
publishes a qualified handoff, AISP needs only identity verification and an
operator pin -- not a redesign. Until then ``production_status`` reports
``NOT_RECEIVED`` and no production label may be displayed.

WHAT IS REQUIRED BEFORE A PRODUCTION LABEL
------------------------------------------
1. The operator pins the exact handoff bytes: ``nfl_production_handoff_path``
   and ``nfl_production_handoff_sha256``. A file that does not hash to the pin
   is refused before it is parsed.
2. The document declares ``HANDOFF_CONTRACT`` and one of ``KNOWN_STATUSES``.
   Anything else -- including a plausible-looking new status -- is refused.
3. Every field in ``REQUIRED_IDENTITY_FIELDS`` is present and well formed, and
   the model identity matches what AISP already admits for NFL M1.
4. The document is not a fixture and does not authorise substituting the
   play-level simulator for M1.
5. Each forecast later bound to it must match the model/configuration/artifact,
   the package (when the handoff pins one), the declared draw count and
   population (when the handoff lists them), and must have been published
   before kickoff.

Nothing is inferred from branch names, directory names or documentation text.
The contract name and field names below are AISP's REQUEST to Codex; they are
provisional until the real handoff confirms or amends them.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HANDOFF_CONTRACT = "ASP_NFL_M1_PRODUCTION_HANDOFF_V1"

PRODUCTION_BASELINE = "NFL_M1_PRODUCTION_BASELINE_PROSPECTIVE_MONITORING"
SHADOW_ONLY = "NFL_M1_SHADOW_ONLY"
PRODUCTION_BLOCKED = "NFL_M1_PRODUCTION_BLOCKED"
KNOWN_STATUSES = frozenset({PRODUCTION_BASELINE, SHADOW_ONLY, PRODUCTION_BLOCKED})

NOT_RECEIVED = "NOT_RECEIVED"
REJECTED = "REJECTED"

#: The M1 identity AISP already admits (app/probability/adapter.ADMITTED["NFL"]).
EXPECTED_MODEL_ID = "NFL_M1_XGBOOST"
EXPECTED_CONFIGURATION_ID = "NFL_M1_2026_RESEARCH_SHADOW_V1"
#: Engines that may never stand in for M1 under a production handoff.
FORBIDDEN_SUBSTITUTES = frozenset({"NFL_DEVELOPMENT_HIERARCHICAL_PLAY_V1", "NFL_DEVELOPMENT_CONDITIONAL_DRIVE_V2"})

REQUIRED_IDENTITY_FIELDS = (
    "contract",            # HANDOFF_CONTRACT
    "status",              # one of KNOWN_STATUSES
    "modelId",             # e.g. NFL_M1_XGBOOST
    "configurationId",     # the exact frozen configuration
    "modelRevision",       # Codex's model revision / version label
    "modelArtifactSha256", # the model artifact
    "discoveryPath",       # exchange-relative index that declares current forecasts
    "discoveryIndexSha256",
    "drawCount",           # simulations per forecast population
    "tiePolicy",
    "forecastContract",    # the per-forecast schema the handoff governs
    "effectiveFromUtc",    # first kickoff the handoff governs
    "calibrationAuthority",
    "issuedAtUtc",
    "issuer",
)

_SHA = re.compile(r"^[0-9a-f]{64}$")


class HandoffRejected(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class HandoffVerdict:
    status: str
    accepted: bool
    fixture: bool = False
    handoff_sha256: str | None = None
    identity: dict[str, Any] = field(default_factory=dict)
    reasons: tuple[dict[str, str], ...] = ()

    @property
    def production_label_allowed(self) -> bool:
        return self.accepted and not self.fixture and self.status == PRODUCTION_BASELINE

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "accepted": self.accepted,
            "fixture": self.fixture,
            "production_label_allowed": self.production_label_allowed,
            "handoff_sha256": self.handoff_sha256,
            "identity": self.identity,
            "reasons": list(self.reasons),
            "required_identity_fields": list(REQUIRED_IDENTITY_FIELDS),
            "contract": HANDOFF_CONTRACT,
            "statement": (
                "NFL M1 production authority is displayed only after the exact pinned Codex "
                "handoff verifies. Research and paper evidence never imply it."
            ),
        }


def _reject(code: str, detail: str, *, sha: str | None = None) -> HandoffVerdict:
    return HandoffVerdict(status=REJECTED, accepted=False, handoff_sha256=sha,
                          reasons=({"code": code, "detail": detail},))


def _parse_utc(value: Any, name: str) -> datetime:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise HandoffRejected("TIMESTAMP_INVALID", f"{name} is not an ISO-8601 time") from exc
    if moment.tzinfo is None:
        raise HandoffRejected("TIMESTAMP_INVALID", f"{name} has no timezone")
    return moment.astimezone(UTC)


def verify_handoff_bytes(raw: bytes, *, pinned_sha256: str, allow_fixture: bool = False) -> HandoffVerdict:
    """Verify one handoff document against the operator's pin. Never raises."""

    pin = (pinned_sha256 or "").strip().lower()
    if not _SHA.match(pin):
        return HandoffVerdict(status=NOT_RECEIVED, accepted=False,
                              reasons=({"code": "NOT_PINNED",
                                        "detail": "No handoff SHA-256 is pinned by the operator."},))
    digest = hashlib.sha256(raw).hexdigest()
    if digest != pin:
        return _reject("HASH_MISMATCH", "The handoff bytes do not hash to the pinned SHA-256.", sha=digest)
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return _reject("NOT_JSON", "The handoff is not UTF-8 JSON.", sha=digest)
    if not isinstance(document, dict):
        return _reject("NOT_AN_OBJECT", "The handoff is not a JSON object.", sha=digest)
    try:
        identity = _verify_document(document, allow_fixture=allow_fixture)
    except HandoffRejected as exc:
        return _reject(exc.code, exc.detail, sha=digest)
    return HandoffVerdict(status=identity["status"], accepted=True, fixture=bool(document.get("fixture")),
                          handoff_sha256=digest, identity=identity)


def _verify_document(document: dict[str, Any], *, allow_fixture: bool) -> dict[str, Any]:
    if document.get("fixture") is True and not allow_fixture:
        raise HandoffRejected("FIXTURE_REFUSED", "A labelled test fixture is never a handoff.")
    missing = [name for name in REQUIRED_IDENTITY_FIELDS if document.get(name) in (None, "")]
    if missing:
        raise HandoffRejected("FIELDS_MISSING", "Missing required field(s): " + ", ".join(missing))
    if document["contract"] != HANDOFF_CONTRACT:
        raise HandoffRejected("CONTRACT_UNSUPPORTED", f"Unsupported contract {document['contract']!r}.")
    if document["status"] not in KNOWN_STATUSES:
        raise HandoffRejected("STATUS_UNKNOWN", f"Unknown status {document['status']!r}.")
    if document["modelId"] in FORBIDDEN_SUBSTITUTES or document.get("playLevelSubstitutionAllowed") is not False:
        raise HandoffRejected("SUBSTITUTION_NOT_EXCLUDED",
                              "The handoff must name M1 and declare playLevelSubstitutionAllowed=false.")
    if document["modelId"] != EXPECTED_MODEL_ID:
        raise HandoffRejected("MODEL_MISMATCH", f"modelId {document['modelId']!r} is not {EXPECTED_MODEL_ID}.")
    if document["configurationId"] != EXPECTED_CONFIGURATION_ID:
        raise HandoffRejected("CONFIGURATION_MISMATCH",
                              f"configurationId {document['configurationId']!r} is not the admitted configuration.")
    for name in ("modelArtifactSha256", "discoveryIndexSha256"):
        if not _SHA.match(str(document[name]).lower()):
            raise HandoffRejected("HASH_INVALID", f"{name} is not a SHA-256.")
    package = document.get("packageSha256")
    if package is not None and not _SHA.match(str(package).lower()):
        raise HandoffRejected("HASH_INVALID", "packageSha256 is not a SHA-256.")
    try:
        draws = int(document["drawCount"])
    except (TypeError, ValueError) as exc:
        raise HandoffRejected("DRAW_COUNT_INVALID", "drawCount is not an integer.") from exc
    if draws <= 0:
        raise HandoffRejected("DRAW_COUNT_INVALID", "drawCount must be positive.")
    discovery = str(document["discoveryPath"])
    if discovery.startswith(("/", "\\")) or ".." in Path(discovery).parts or ":" in discovery:
        raise HandoffRejected("DISCOVERY_PATH_INVALID", "discoveryPath must be exchange-relative.")
    populations = document.get("forecastPopulations") or {}
    if not isinstance(populations, dict) or any(not _SHA.match(str(v).lower()) for v in populations.values()):
        raise HandoffRejected("POPULATIONS_INVALID", "forecastPopulations must map forecast ids to SHA-256s.")
    return {
        "status": document["status"],
        "model_id": document["modelId"],
        "configuration_id": document["configurationId"],
        "model_revision": str(document["modelRevision"]),
        "model_artifact_sha256": str(document["modelArtifactSha256"]).lower(),
        "package_sha256": str(package).lower() if package else None,
        "discovery_path": discovery,
        "discovery_index_sha256": str(document["discoveryIndexSha256"]).lower(),
        "draw_count": draws,
        "tie_policy": document["tiePolicy"],
        "forecast_contract": document["forecastContract"],
        "effective_from_utc": _parse_utc(document["effectiveFromUtc"], "effectiveFromUtc").isoformat(),
        "issued_at_utc": _parse_utc(document["issuedAtUtc"], "issuedAtUtc").isoformat(),
        "calibration_authority": document["calibrationAuthority"],
        "issuer": document["issuer"],
        "forecast_populations": {str(k): str(v).lower() for k, v in populations.items()},
    }


def verify_forecast(verdict: HandoffVerdict, *, forecast_id: str, engine_id: str, engine_version: str,
                    model_artifact_sha256: str | None, package_sha256: str | None,
                    population_sha256: str | None, draw_count: int | None,
                    published_utc: datetime | None, kickoff_utc: datetime | None) -> dict[str, Any]:
    """Whether ONE stored forecast is governed by a verified production handoff."""

    def refuse(code: str, detail: str) -> dict[str, Any]:
        return {"governed": False, "code": code, "detail": detail}

    if not verdict.accepted:
        return refuse("HANDOFF_NOT_VERIFIED", "No verified production handoff.")
    if verdict.fixture:
        return refuse("FIXTURE_HANDOFF", "A fixture handoff never governs a stored forecast.")
    if verdict.status != PRODUCTION_BASELINE:
        return refuse("NOT_PRODUCTION_STATUS", f"Handoff status is {verdict.status}.")
    identity = verdict.identity
    if engine_id != identity["model_id"] or engine_version != identity["configuration_id"]:
        return refuse("MODEL_MISMATCH", "Forecast model or configuration differs from the handoff.")
    if (model_artifact_sha256 or "").lower() != identity["model_artifact_sha256"]:
        return refuse("ARTIFACT_MISMATCH", "Forecast model artifact differs from the handoff.")
    if identity["package_sha256"] and (package_sha256 or "").lower() != identity["package_sha256"]:
        return refuse("PACKAGE_MISMATCH", "Forecast package differs from the handoff.")
    if draw_count != identity["draw_count"]:
        return refuse("DRAW_COUNT_MISMATCH", "Forecast population draw count differs from the handoff.")
    expected_population = identity["forecast_populations"].get(forecast_id)
    if identity["forecast_populations"] and expected_population is None:
        return refuse("FORECAST_NOT_LISTED", "The handoff lists populations and this forecast is not one of them.")
    if expected_population and (population_sha256 or "").lower() != expected_population:
        return refuse("POPULATION_MISMATCH", "Forecast population differs from the handoff.")
    if published_utc is None or kickoff_utc is None:
        return refuse("FORECAST_TIME_MISSING", "Forecast publication or kickoff time is missing.")
    if published_utc >= kickoff_utc:
        return refuse("LATE_FORECAST", "Forecast was published at or after kickoff.")
    if kickoff_utc < datetime.fromisoformat(identity["effective_from_utc"]):
        return refuse("BEFORE_EFFECTIVE_FROM", "The game kicks off before the handoff takes effect.")
    return {"governed": True, "code": "GOVERNED", "detail": "Forecast matches the verified production handoff."}


def production_status(settings: Any) -> HandoffVerdict:
    """The live status: NOT_RECEIVED unless an operator-pinned handoff verifies."""

    path = str(getattr(settings, "nfl_production_handoff_path", "") or "").strip()
    pin = str(getattr(settings, "nfl_production_handoff_sha256", "") or "").strip()
    if not path or not pin:
        return HandoffVerdict(status=NOT_RECEIVED, accepted=False, reasons=({
            "code": "NOT_RECEIVED",
            "detail": "No Codex NFL M1 production handoff has been received and pinned. NFL M1 remains "
                      "research-shadow with paper research evidence only.",
        },))
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        return _reject("HANDOFF_UNREADABLE", f"The pinned handoff cannot be read ({type(exc).__name__}).")
    return verify_handoff_bytes(raw, pinned_sha256=pin)


__all__ = [
    "EXPECTED_CONFIGURATION_ID", "EXPECTED_MODEL_ID", "HANDOFF_CONTRACT", "HandoffRejected",
    "HandoffVerdict", "KNOWN_STATUSES", "NOT_RECEIVED", "PRODUCTION_BASELINE", "PRODUCTION_BLOCKED",
    "REJECTED", "REQUIRED_IDENTITY_FIELDS", "SHADOW_ONLY", "production_status", "verify_forecast",
    "verify_handoff_bytes",
]

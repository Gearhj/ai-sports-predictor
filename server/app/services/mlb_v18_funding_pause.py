"""MLB_V18_FUNDING_PAUSE_V1 -- stop funding the unsupported V18 two-point edge
gate, while keeping every V18 decision and the evidence prospective
model-versus-market analysis needs.

WHAT IT DOES
------------
Inside ``ShadowExperimentService._create_canonical_position``, AFTER the
decision-integrity guards (FINAL_PREGAME ``QUALIFIED_BET``, cutoff-safe quote and
forecast clocks, exact forecast / snapshot / market / price agreement, ``LIVE``
source mode, the duplicate-position check) and BEFORE any experiment-account
read, bankroll read, ``compute_position_stake``, ``_stake_used``, cap arithmetic
or ``OfficialAspPosition`` insert, the decision's complete authority tuple is
classified against the frozen mapping below:

* exact match of every bound field            -> PAUSE_FUNDING: create or reuse
  one immutable ``MlbV18FundingPause`` record; no position.
* no V18 identity anywhere in the tuple        -> PASS_THROUGH: the ordinary
  funded path runs unchanged (baseline, practical-policy and any other
  non-V18 MLB experiment).
* V18 identity present with ANY mismatch       -> V18_FUNDING_AUTHORITY_MISMATCH:
  no position, a durable idempotent mismatch artifact, an operating alert. A
  single altered field never turns the governed V18 path back into funding.

A pause means only: a genuine qualified decision was blocked before funding
evaluation. Staking never ran, so nothing here claims a position would
otherwise have been funded, and no stake / payout / profit / bankroll /
exposure / cap / settlement / outcome figure exists in the record or receipts.

WHAT IT NEVER DOES
------------------
It never writes, edits or re-reads-for-rewrite a decision, forecast, threshold,
odds snapshot, position or settlement. It never runs when
``settings.mlb_v18_funding_pause_enabled`` is false (the default), and it is
unreachable for any non-MLB game (it sits after ``_is_mlb_game``).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Callable, Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

FEATURE_VERSION = "MLB_V18_FUNDING_PAUSE_V1"
RECORD_SCHEMA_VERSION = "MLB_V18_FUNDING_PAUSE_RECORD_V1"
MISMATCH_SCHEMA_VERSION = "MLB_V18_FUNDING_AUTHORITY_MISMATCH_V1"
AUTHORITY_SCHEMA_VERSION = "MLB_V18_FUNDING_PAUSE_AUTHORITY_MAPPING_V1"
#: SHA-256 of the Model Studio file the pinned values were copied from, verbatim:
#: asp-model-studio/outputs/production-followups-20260924-v1/
#: MLB_V18_FUNDING_PAUSE_V1_AUTHORITY.json. Provenance only; never read at runtime.
AUTHORITY_SOURCE_SHA256 = "1fc4281d23078ab9c5f10b802c8295171d40b25a2714f95dd6ea805a80bd4bac"

REASON_CODE = "FUNDING_EDGE_NOT_ESTABLISHED"
VALIDATION_STATUS = "PENDING_PROSPECTIVE_VALIDATION"
MISMATCH_CODE = "V18_FUNDING_AUTHORITY_MISMATCH"
INTEGRITY_CODE = "V18_FUNDING_PAUSE_RECORD_INTEGRITY_FAILURE"

#: Operating alerts are AuditEvent rows (the established alert channel, see
#: services/pregame_evidence_monitor), one per decision, never repeated.
MISMATCH_ACTION = "mlb_v18_funding.authority_mismatch"
INTEGRITY_ACTION = "mlb_v18_funding.pause_record_integrity_failure"
ALERT_ENTITY_TYPE = "shadow_experiment_decision"

# -- classification ----------------------------------------------------------
PAUSE_FUNDING = "PAUSE_FUNDING"
PASS_THROUGH = "PASS_THROUGH"
AUTHORITY_MISMATCH_CLASS = MISMATCH_CODE

# -- position-path dispositions ----------------------------------------------
#: A funded position was created (ordinary path).
CREATED = "CREATED"
#: A decision-integrity guard stopped it before the pause was reached.
INELIGIBLE = "INELIGIBLE"
#: Passed through the pause (non-V18); staking or caps produced no position.
NOT_FUNDED = "NOT_FUNDED"
PAUSE_NEW = "PAUSE_NEW"
PAUSE_REUSED = "PAUSE_REUSED"
AUTHORITY_MISMATCH = "AUTHORITY_MISMATCH"
PAUSE_INTEGRITY_FAILURE = "PAUSE_INTEGRITY_FAILURE"

#: The frozen authority, exactly as Model Studio bound it. One place, asserted
#: verbatim by the tests; a scattered string comparison is how the wrong
#: experiment gets defunded.
AUTHORITY: Mapping[str, Any] = MappingProxyType({
    "sport": "MLB",
    "experimentId": "b7532da1-27b2-4d7a-b373-bbfb3166bd3a",
    "modelEraId": "ASP_MLB_V18_CONDITIONAL_VIRTUAL_20260912_V1_R1",
    "controllingEngine": "MLB_V18_WHOLE_GAME",
    "modelId": "MLB_V18_HGB_PA",
    "modelConfigurationIdentity": "UNRESTRICTED_V18_FOLD5_PA_TRANSITION_FOLD4",
    "modelArtifactSha256": "44ec4f457741f1aaeff42b81af1be5fd2712e494709ff1a0a8f0742b88d71c8b",
    "parentLogicalBundleSha256": "a6e7e99122f1b60701a4728b97b32fcc11e03405bfd456ed6cd54f69b69adb27",
    "recommendationPolicyVersion": "AISP_PRACTICAL_PREGAME_POLICY_V2_1",
    "decisionPolicyVersion": "AISP_PRACTICAL_PREGAME_POLICY_V2_1",
    "stakingPolicyVersion": "AISP_QUARTER_KELLY_EVIDENCE_V2",
    "policyConfigurationSha256": "7e439d4bed6ec67e261dfc272e3b1ced93f01f0d59a8813b867dc1b39620a8d2",
    "qualifiedEdgeThreshold": 0.02,
    "requiredPhase": "FINAL_PREGAME",
    "requiredRecommendation": "QUALIFIED_BET",
    "requiredSourceMode": "LIVE",
})

#: Every dimension that must match, as (observed key, expected value). The
#: forecast's own engine must be the controlling engine, and the experiment's
#: frozen policy configuration must recompute to its own recorded checksum.
EXPECTED: Mapping[str, Any] = MappingProxyType({
    "experimentId": AUTHORITY["experimentId"],
    "modelEraId": AUTHORITY["modelEraId"],
    "controllingEngine": AUTHORITY["controllingEngine"],
    "forecastEngine": AUTHORITY["controllingEngine"],
    "modelId": AUTHORITY["modelId"],
    "modelConfigurationIdentity": AUTHORITY["modelConfigurationIdentity"],
    "modelArtifactSha256": AUTHORITY["modelArtifactSha256"],
    "parentLogicalBundleSha256": AUTHORITY["parentLogicalBundleSha256"],
    "recommendationPolicyVersion": AUTHORITY["recommendationPolicyVersion"],
    "decisionPolicyVersion": AUTHORITY["decisionPolicyVersion"],
    "stakingPolicyVersion": AUTHORITY["stakingPolicyVersion"],
    "policyConfigurationSha256": AUTHORITY["policyConfigurationSha256"],
    "policyConfigurationIntegrity": "VERIFIED",
    "qualifiedEdgeThreshold": AUTHORITY["qualifiedEdgeThreshold"],
    "requiredPhase": AUTHORITY["requiredPhase"],
    "requiredRecommendation": AUTHORITY["requiredRecommendation"],
    "requiredSourceMode": AUTHORITY["requiredSourceMode"],
})

ARMED_ERA_SUFFIX = ":ARMED"


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


#: Identity of the mapping above, bound into every record, receipt and alert.
AUTHORITY_MAPPING_SHA256 = _sha256({"schemaVersion": AUTHORITY_SCHEMA_VERSION,
                                    "authority": dict(AUTHORITY)})


def _wall_clock() -> datetime:
    return datetime.now(UTC)


#: Injectable for tests; the actual detection clock in production.
clock: Callable[[], datetime] = _wall_clock


# -- observation and classification ------------------------------------------

def _shadow_row(forecast: Any) -> Any | None:
    """The ShadowForecast behind a V18 forecast view, or None for a Gen1 row."""

    from app.models.entities import ShadowForecast

    row = getattr(forecast, "row", None)
    return row if isinstance(row, ShadowForecast) else None


def _policy_integrity(policy_config: Any) -> str | None:
    if not isinstance(policy_config, Mapping) or "checksum" not in policy_config:
        return None
    body = {key: value for key, value in policy_config.items() if key != "checksum"}
    recomputed = hashlib.sha256(_canonical_json(body)).hexdigest()
    return "VERIFIED" if recomputed == policy_config.get("checksum") else "FAILED"


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def observe(experiment: Any, decision: Any, forecast: Any) -> dict[str, Any]:
    """The decision's complete authority tuple, read from persisted rows only."""

    from app.probability.adapter import ADMITTED

    row = _shadow_row(forecast)
    admitted = ADMITTED.get("MLB") or {}
    lineage = (row.lineage_detail or {}) if row is not None else {}
    engine_admitted = row is not None and row.engine_id == admitted.get("engine_id")
    policy_config = experiment.policy_config if isinstance(experiment.policy_config, Mapping) else {}
    threshold = policy_config.get("min_adjusted_edge")
    return {
        "experimentId": str(experiment.id) if experiment.id is not None else None,
        "modelEraId": experiment.model_era_id,
        "controllingEngine": experiment.controlling_engine,
        "forecastEngine": row.engine_id if row is not None else None,
        # Model and configuration identity are not columns on a forecast row;
        # they are AISP's admitted binding for the engine (probability.adapter,
        # independently acknowledged by the consumer). A drift in that binding
        # is therefore a mismatch here too.
        "modelId": admitted.get("model_id") if engine_admitted else None,
        "modelConfigurationIdentity": admitted.get("configuration_id") if engine_admitted else None,
        "modelArtifactSha256": row.model_artifact_sha256 if row is not None else None,
        "parentLogicalBundleSha256": (str(lineage.get("parentBundleSha256") or "").lower() or None),
        "recommendationPolicyVersion": experiment.recommendation_policy_version,
        "decisionPolicyVersion": experiment.decision_policy,
        "stakingPolicyVersion": experiment.staking_policy_version,
        "policyConfigurationSha256": policy_config.get("checksum"),
        "policyConfigurationIntegrity": _policy_integrity(experiment.policy_config),
        "qualifiedEdgeThreshold": float(threshold) if isinstance(threshold, (int, float)) else None,
        "requiredPhase": _enum_value(decision.phase),
        "requiredRecommendation": decision.recommendation,
        "requiredSourceMode": decision.source_mode,
    }


def v18_markers(observed: Mapping[str, Any]) -> list[str]:
    """Which parts of the tuple carry a V18 identity. Empty means non-V18."""

    markers: list[str] = []
    if observed.get("experimentId") == AUTHORITY["experimentId"]:
        markers.append("experimentId")
    era = str(observed.get("modelEraId") or "")
    if era and (era.removesuffix(ARMED_ERA_SUFFIX) == AUTHORITY["modelEraId"] or "V18" in era.upper()):
        markers.append("modelEraId")
    if "V18" in str(observed.get("controllingEngine") or "").upper():
        markers.append("controllingEngine")
    if "V18" in str(observed.get("forecastEngine") or "").upper():
        markers.append("forecastEngine")
    if observed.get("modelArtifactSha256") == AUTHORITY["modelArtifactSha256"]:
        markers.append("modelArtifactSha256")
    if observed.get("parentLogicalBundleSha256") == AUTHORITY["parentLogicalBundleSha256"]:
        markers.append("parentLogicalBundleSha256")
    return markers


@dataclass(frozen=True)
class Classification:
    kind: str
    observed: dict[str, Any]
    markers: tuple[str, ...]
    mismatched: tuple[str, ...]


def classify(observed: Mapping[str, Any]) -> Classification:
    markers = tuple(v18_markers(observed))
    if not markers:
        return Classification(PASS_THROUGH, dict(observed), markers, ())
    mismatched = tuple(key for key, expected in EXPECTED.items() if observed.get(key) != expected)
    kind = PAUSE_FUNDING if not mismatched else AUTHORITY_MISMATCH_CLASS
    return Classification(kind, dict(observed), markers, mismatched)


# -- the record ----------------------------------------------------------------

#: Every business column of the record, in a fixed order: the content hash is
#: computed over exactly these (everything except id, created_at and the hash).
HASHED_FIELDS: tuple[str, ...] = (
    "schema_version", "feature_version",
    "decision_id", "game_id", "decision_phase", "decision_recommendation",
    "decision_source_mode", "decision_recommended_at",
    "experiment_id", "model_era_id", "controlling_engine", "model_id",
    "model_configuration_identity", "model_artifact_sha256", "parent_bundle_sha256",
    "recommendation_policy_version", "decision_policy_version", "staking_policy_version",
    "policy_configuration_sha256", "authority_mapping_sha256",
    "shadow_forecast_id", "forecast_identity", "forecast_package_sha256",
    "market_id", "market_outcome_id", "odds_snapshot_id", "sportsbook", "market_type",
    "selection", "american_odds", "decimal_odds",
    "model_probability", "market_probability", "edge", "policy_adjusted_edge",
    "qualified_edge_threshold",
    "quote_observed_at", "forecast_cutoff_at", "forecast_available_at",
    "checkpoint_evaluated_at", "paused_at",
    "reason_code", "validation_status",
)

_DATETIME_FIELDS = frozenset({
    "decision_recommended_at", "quote_observed_at", "forecast_cutoff_at",
    "forecast_available_at", "checkpoint_evaluated_at", "paused_at",
})
_UUID_FIELDS = frozenset({
    "decision_id", "game_id", "experiment_id", "shadow_forecast_id",
    "market_id", "market_outcome_id", "odds_snapshot_id",
})
_FLOAT_FIELDS = frozenset({
    "decimal_odds", "model_probability", "market_probability", "edge",
    "policy_adjusted_edge", "qualified_edge_threshold",
})


def _utc(value: datetime) -> datetime:
    """Aware UTC. A naive value (SQLite) is UTC; an aware one (the production
    session hands back America/New_York) is converted, never relabelled."""

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def canonical_field(name: str, value: Any) -> Any:
    if value is None:
        return None
    if name in _DATETIME_FIELDS:
        return _utc(value).isoformat()
    if name in _UUID_FIELDS:
        return str(value)
    if name in _FLOAT_FIELDS:
        return float(value)
    if name == "american_odds":
        return int(value)
    return str(value)


def canonical_record(source: Any) -> dict[str, Any]:
    """The hashed body, from a row or a mapping of the same field names."""

    get = source.get if isinstance(source, Mapping) else (lambda key: getattr(source, key))
    return {name: canonical_field(name, get(name)) for name in HASHED_FIELDS}


def record_content_sha256(source: Any) -> str:
    return hashlib.sha256(_canonical_json(canonical_record(source))).hexdigest()


def verify_record(row: Any) -> bool:
    return bool(row.content_sha256) and record_content_sha256(row) == row.content_sha256


def _uuid(value: Any) -> UUID | None:
    try:
        return value if isinstance(value, UUID) else (UUID(str(value)) if value else None)
    except (TypeError, ValueError):
        return None


def build_record_fields(
    session: Session, *, experiment: Any, game: Any, decision: Any, forecast: Any,
    snapshot: Any, observed: Mapping[str, Any], now: datetime, paused_at: datetime,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Every bound field, or (None, the missing fields). Nothing is defaulted."""

    from app.models import MarketOutcome

    row = _shadow_row(forecast)
    lineage = (row.lineage_detail or {}) if row is not None else {}
    outcome = session.get(MarketOutcome, decision.market_outcome_id) if decision.market_outcome_id else None
    evidence = decision.evidence if isinstance(decision.evidence, Mapping) else {}
    adjusted = evidence.get("adjusted_edge")
    package = str(lineage.get("objectSha256") or "").lower()
    fields: dict[str, Any] = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "feature_version": FEATURE_VERSION,
        "decision_id": decision.id,
        "game_id": game.id,
        "decision_phase": _enum_value(decision.phase),
        "decision_recommendation": decision.recommendation,
        "decision_source_mode": decision.source_mode,
        "decision_recommended_at": decision.recommended_at,
        "experiment_id": experiment.id,
        "model_era_id": observed.get("modelEraId"),
        "controlling_engine": observed.get("controllingEngine"),
        "model_id": observed.get("modelId"),
        "model_configuration_identity": observed.get("modelConfigurationIdentity"),
        "model_artifact_sha256": observed.get("modelArtifactSha256"),
        "parent_bundle_sha256": observed.get("parentLogicalBundleSha256"),
        "recommendation_policy_version": observed.get("recommendationPolicyVersion"),
        "decision_policy_version": observed.get("decisionPolicyVersion"),
        "staking_policy_version": observed.get("stakingPolicyVersion"),
        "policy_configuration_sha256": observed.get("policyConfigurationSha256"),
        "authority_mapping_sha256": AUTHORITY_MAPPING_SHA256,
        "shadow_forecast_id": decision.shadow_forecast_id,
        "forecast_identity": row.forecast_id if row is not None else None,
        # The forecast's content-addressed package object: the same identity
        # the probability adapter binds (forecast_id == mlb-v18-daily-<sha>).
        "forecast_package_sha256": (package if len(package) == 64 and row is not None
                                    and row.forecast_id == f"mlb-v18-daily-{package}" else None),
        "market_id": outcome.market_id if outcome is not None else None,
        "market_outcome_id": decision.market_outcome_id,
        "odds_snapshot_id": decision.odds_snapshot_id,
        "sportsbook": decision.sportsbook,
        "market_type": decision.market,
        "selection": decision.selection,
        "american_odds": decision.american_odds,
        "decimal_odds": decision.decimal_odds,
        "model_probability": decision.model_probability,
        "market_probability": decision.no_vig_probability,
        "edge": decision.edge,
        "policy_adjusted_edge": float(adjusted) if isinstance(adjusted, (int, float)) else None,
        "qualified_edge_threshold": observed.get("qualifiedEdgeThreshold"),
        "quote_observed_at": decision.quote_observed_at,
        "forecast_cutoff_at": decision.forecast_cutoff,
        "forecast_available_at": forecast.generated_at if forecast is not None else None,
        "checkpoint_evaluated_at": now,
        "paused_at": paused_at,
        "reason_code": REASON_CODE,
        "validation_status": VALIDATION_STATUS,
    }
    missing = [name for name in HASHED_FIELDS if fields.get(name) is None]
    return (None, missing) if missing else (fields, [])


# -- receipts ------------------------------------------------------------------

COUNT_KEYS = (
    "newly_paused", "previously_paused_reused", "ineligible_not_paused",
    "mismatch_alerts", "integrity_failures", "pass_through_to_funding_path",
    "positions_created",
)
_COUNT_OF = {
    PAUSE_NEW: "newly_paused",
    PAUSE_REUSED: "previously_paused_reused",
    INELIGIBLE: "ineligible_not_paused",
    AUTHORITY_MISMATCH: "mismatch_alerts",
    PAUSE_INTEGRITY_FAILURE: "integrity_failures",
    CREATED: "positions_created",
}


@dataclass
class FundingPauseReceipt:
    """What one checkpoint pass did, per experiment. No money figure anywhere."""

    experiment_id: str
    counts: dict[str, int] = field(default_factory=lambda: {key: 0 for key in COUNT_KEYS})
    paused: list[dict[str, Any]] = field(default_factory=list)
    mismatches: list[dict[str, Any]] = field(default_factory=list)
    integrity_failures: list[dict[str, Any]] = field(default_factory=list)

    def count(self, disposition: str) -> None:
        key = _COUNT_OF.get(disposition)
        if key is not None:
            self.counts[key] += 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "feature": FEATURE_VERSION, "enabled": True,
            "experiment_id": self.experiment_id,
            "authority_mapping_sha256": AUTHORITY_MAPPING_SHA256,
            "counts": dict(self.counts),
            "paused": list(self.paused),
            "mismatches": list(self.mismatches),
            "integrity_failures": list(self.integrity_failures),
        }


def receipt_entry(row: Any, disposition: str) -> dict[str, Any]:
    """One pause record, listed directly (not by reference)."""

    body = canonical_record(row)
    return {
        "disposition": disposition,
        "record_id": str(row.id),
        "content_sha256": row.content_sha256,
        "decision_id": body["decision_id"],
        "game_id": body["game_id"],
        "experiment_id": body["experiment_id"],
        "model_era_id": body["model_era_id"],
        "controlling_engine": body["controlling_engine"],
        "authority_mapping_sha256": body["authority_mapping_sha256"],
        "shadow_forecast_id": body["shadow_forecast_id"],
        "forecast_identity": body["forecast_identity"],
        "forecast_package_sha256": body["forecast_package_sha256"],
        "odds_snapshot_id": body["odds_snapshot_id"],
        "market_id": body["market_id"],
        "market_outcome_id": body["market_outcome_id"],
        "sportsbook": body["sportsbook"],
        "market_type": body["market_type"],
        "selection": body["selection"],
        "american_odds": body["american_odds"],
        "decimal_odds": body["decimal_odds"],
        "model_probability": body["model_probability"],
        "market_probability": body["market_probability"],
        "edge": body["edge"],
        "policy_adjusted_edge": body["policy_adjusted_edge"],
        "qualified_edge_threshold": body["qualified_edge_threshold"],
        "quote_observed_at": body["quote_observed_at"],
        "forecast_cutoff_at": body["forecast_cutoff_at"],
        "forecast_available_at": body["forecast_available_at"],
        "paused_at": body["paused_at"],
        "recommendation_policy_version": body["recommendation_policy_version"],
        "decision_policy_version": body["decision_policy_version"],
        "staking_policy_version": body["staking_policy_version"],
        "policy_configuration_sha256": body["policy_configuration_sha256"],
        "model_id": body["model_id"],
        "model_configuration_identity": body["model_configuration_identity"],
        "reason_code": body["reason_code"],
        "validation_status": body["validation_status"],
    }


# -- alerts ----------------------------------------------------------------------

def _record_alert(session: Session, *, action: str, decision_id: Any,
                  metadata: dict[str, Any]) -> tuple[bool, Any]:
    """One durable AuditEvent per (action, decision); a repeat writes nothing.

    The production session does not autoflush, so pending state is flushed
    before the lookup and the new row is flushed immediately: a second call in
    the same transaction therefore sees the first.
    """

    from app.models import AuditEvent

    session.flush()
    existing = session.scalar(select(AuditEvent).where(
        AuditEvent.action == action,
        AuditEvent.entity_type == ALERT_ENTITY_TYPE,
        AuditEvent.entity_id == _uuid(decision_id),
    ).limit(1))
    if existing is not None:
        return False, existing
    body = dict(metadata)
    body["content_sha256"] = _sha256({k: v for k, v in body.items() if k != "content_sha256"})
    event = AuditEvent(
        actor_user_id=None, action=action, entity_type=ALERT_ENTITY_TYPE,
        entity_id=_uuid(decision_id), occurred_at=_utc(metadata_clock(metadata)),
        metadata_json=body,
    )
    session.add(event)
    session.flush()
    return True, event


def metadata_clock(metadata: Mapping[str, Any]) -> datetime:
    return datetime.fromisoformat(str(metadata["detected_at"]))


def _identity(decision: Any, game: Any, forecast: Any) -> dict[str, Any]:
    row = _shadow_row(forecast)
    return {
        "decision_id": str(decision.id), "game_id": str(game.id),
        "experiment_id": str(decision.experiment_id),
        "forecast_id": str(decision.forecast_id) if decision.forecast_id else None,
        "shadow_forecast_id": str(decision.shadow_forecast_id) if decision.shadow_forecast_id else None,
        "forecast_identity": row.forecast_id if row is not None else None,
        "odds_snapshot_id": str(decision.odds_snapshot_id) if decision.odds_snapshot_id else None,
        "market_outcome_id": str(decision.market_outcome_id) if decision.market_outcome_id else None,
        "sportsbook": decision.sportsbook, "selection": decision.selection,
    }


# -- the guard ---------------------------------------------------------------------

def _lookup(session: Session, decision_id: Any) -> Any | None:
    from app.models import MlbV18FundingPause

    session.flush()
    return session.scalar(select(MlbV18FundingPause).where(
        MlbV18FundingPause.decision_id == _uuid(decision_id)))


def _reuse(session: Session, row: Any, *, decision: Any, game: Any, forecast: Any,
           receipt: FundingPauseReceipt | None, now: datetime) -> str:
    if verify_record(row):
        if receipt is not None:
            receipt.paused.append(receipt_entry(row, PAUSE_REUSED))
        return PAUSE_REUSED
    return _integrity_failure(session, decision=decision, game=game, forecast=forecast,
                              receipt=receipt, now=now, problem="STORED_CONTENT_SHA256_MISMATCH",
                              detail={"record_id": str(row.id), "stored_content_sha256": row.content_sha256,
                                      "recomputed_content_sha256": record_content_sha256(row)})


def _integrity_failure(session: Session, *, decision: Any, game: Any, forecast: Any,
                       receipt: FundingPauseReceipt | None, now: datetime, problem: str,
                       detail: dict[str, Any]) -> str:
    detected = clock()
    metadata = {
        "code": INTEGRITY_CODE, "feature": FEATURE_VERSION, "schema_version": MISMATCH_SCHEMA_VERSION,
        "problem": problem, **_identity(decision, game, forecast), **detail,
        "authority_mapping_sha256": AUTHORITY_MAPPING_SHA256,
        "detected_at": _utc(detected).isoformat(), "checkpoint_evaluated_at": _utc(now).isoformat(),
        "position_created": False,
    }
    new, event = _record_alert(session, action=INTEGRITY_ACTION, decision_id=decision.id, metadata=metadata)
    logger.error("mlb_v18_funding_pause_integrity_failure decision=%s problem=%s new_alert=%s",
                 decision.id, problem, new)
    if receipt is not None:
        receipt.integrity_failures.append({
            "alert_id": str(event.id), "new_alert": new, "problem": problem,
            "content_sha256": (event.metadata_json or {}).get("content_sha256"),
            **_identity(decision, game, forecast)})
    return PAUSE_INTEGRITY_FAILURE


def funding_pause_disposition(
    session: Session,
    *,
    experiment: Any,
    game: Any,
    decision: Any,
    forecast: Any,
    snapshot: Any,
    now: datetime,
    receipt: FundingPauseReceipt | None = None,
) -> str | None:
    """None: continue on the ordinary funded path. Otherwise the disposition,
    and the caller returns it without funding anything.

    Called only after every decision-integrity guard has passed, and only when
    the feature flag is on.
    """

    classification = classify(observe(experiment, decision, forecast))
    if classification.kind == PASS_THROUGH:
        if receipt is not None:
            receipt.counts["pass_through_to_funding_path"] += 1
        return None

    if classification.kind == AUTHORITY_MISMATCH_CLASS:
        detected = clock()
        metadata = {
            "code": MISMATCH_CODE, "feature": FEATURE_VERSION, "schema_version": MISMATCH_SCHEMA_VERSION,
            **_identity(decision, game, forecast),
            "observed": classification.observed,
            "expected": dict(EXPECTED),
            "authority": dict(AUTHORITY),
            "authority_mapping_sha256": AUTHORITY_MAPPING_SHA256,
            "v18_markers": list(classification.markers),
            "mismatched_dimensions": list(classification.mismatched),
            "detected_at": _utc(detected).isoformat(),
            "checkpoint_evaluated_at": _utc(now).isoformat(),
            "position_created": False,
        }
        new, event = _record_alert(session, action=MISMATCH_ACTION, decision_id=decision.id,
                                   metadata=metadata)
        logger.error("mlb_v18_funding_authority_mismatch decision=%s dimensions=%s new_alert=%s",
                     decision.id, ",".join(classification.mismatched), new)
        if receipt is not None:
            stored = event.metadata_json or {}
            receipt.mismatches.append({
                "alert_id": str(event.id), "new_alert": new,
                "content_sha256": stored.get("content_sha256"),
                "mismatched_dimensions": list(stored.get("mismatched_dimensions") or []),
                "detected_at": stored.get("detected_at"),
                **_identity(decision, game, forecast)})
        return AUTHORITY_MISMATCH

    # PAUSE_FUNDING: reuse the one record for this decision, or create it.
    existing = _lookup(session, decision.id)
    if existing is not None:
        return _reuse(session, existing, decision=decision, game=game, forecast=forecast,
                      receipt=receipt, now=now)
    fields, missing = build_record_fields(
        session, experiment=experiment, game=game, decision=decision, forecast=forecast,
        snapshot=snapshot, observed=classification.observed, now=now, paused_at=clock(),
    )
    if fields is None:
        return _integrity_failure(session, decision=decision, game=game, forecast=forecast,
                                  receipt=receipt, now=now, problem="REQUIRED_FIELDS_MISSING",
                                  detail={"missing_fields": missing})

    from app.models import MlbV18FundingPause

    row = MlbV18FundingPause(**fields)
    row.content_sha256 = record_content_sha256(fields)
    try:
        # A savepoint, so a concurrent writer's row (the unique constraint on
        # decision_id) costs this insert only, never the checkpoint's other work.
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        existing = _lookup(session, decision.id)
        if existing is None:
            raise
        return _reuse(session, existing, decision=decision, game=game, forecast=forecast,
                      receipt=receipt, now=now)
    if receipt is not None:
        receipt.paused.append(receipt_entry(row, PAUSE_NEW))
    return PAUSE_NEW


# -- read ---------------------------------------------------------------------------

def funding_pause_view(row: Any) -> dict[str, Any]:
    """The read-only surface: what the pause is, never a money figure."""

    return {
        "record_id": str(row.id),
        "content_sha256": row.content_sha256,
        "verified": verify_record(row),
        "feature": row.feature_version,
        "reason": row.reason_code,
        "status": row.validation_status,
        "paused_at": _utc(row.paused_at).isoformat() if row.paused_at else None,
        "qualified_edge_threshold": row.qualified_edge_threshold,
        "policy_adjusted_edge": row.policy_adjusted_edge,
        "position_created": False,
    }


def funding_pauses_for_decisions(session: Session, decision_ids: list[Any]) -> dict[str, dict[str, Any]]:
    """Pause views keyed by decision id. Portable ORM read; no write."""

    from app.models import MlbV18FundingPause

    ids = [value for value in (_uuid(d) for d in decision_ids) if value is not None]
    if not ids:
        return {}
    rows = session.scalars(select(MlbV18FundingPause).where(MlbV18FundingPause.decision_id.in_(ids)))
    return {str(row.decision_id): funding_pause_view(row) for row in rows}


def aggregate_receipts(blocks: list[Mapping[str, Any]]) -> dict[str, Any]:
    """One job-level receipt from every per-experiment, per-phase block."""

    counts = {key: 0 for key in COUNT_KEYS}
    paused: list[Any] = []
    mismatches: list[Any] = []
    failures: list[Any] = []
    for block in blocks:
        for key in COUNT_KEYS:
            counts[key] += int((block.get("counts") or {}).get(key, 0))
        paused.extend(block.get("paused") or [])
        mismatches.extend(block.get("mismatches") or [])
        failures.extend(block.get("integrity_failures") or [])
    return {
        "feature": FEATURE_VERSION, "enabled": True,
        "authority_mapping_sha256": AUTHORITY_MAPPING_SHA256,
        "authority_source_sha256": AUTHORITY_SOURCE_SHA256,
        "counts": counts,
        "newly_paused": [p for p in paused if p.get("disposition") == PAUSE_NEW],
        "previously_paused_reused": [p for p in paused if p.get("disposition") == PAUSE_REUSED],
        "mismatch_alerts": mismatches,
        "integrity_failures": failures,
    }


__all__ = [
    "AUTHORITY", "AUTHORITY_MAPPING_SHA256", "AUTHORITY_SOURCE_SHA256", "EXPECTED",
    "FEATURE_VERSION", "REASON_CODE", "VALIDATION_STATUS", "MISMATCH_CODE",
    "CREATED", "INELIGIBLE", "NOT_FUNDED", "PAUSE_NEW", "PAUSE_REUSED",
    "AUTHORITY_MISMATCH", "PAUSE_INTEGRITY_FAILURE",
    "FundingPauseReceipt", "classify", "observe", "funding_pause_disposition",
    "funding_pauses_for_decisions", "aggregate_receipts", "record_content_sha256", "verify_record",
]

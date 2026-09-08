"""Verify and record a completed Model Studio selection-policy study.

WHAT THIS IS
------------
A read-only receipt. Model Studio runs a policy study against frozen evidence
and returns a verdict; AISP records THAT THE STUDY EXISTS and what it concluded.

WHAT IT MAY NOT DO
------------------
Recording a study activates nothing. It cannot change a threshold, create or
arm a challenger policy, evaluate a game, create a decision, or touch a
position or settlement. The only write is one immutable ``AuditEvent`` row, so
no migration and no new table are required.

A study that asks for anything else is refused before it is recorded: the
verdict must be ``MLB_STRICT_POLICY_RETAINED``, the study must declare that it
changed no official policy and granted no wagering authority, and the control
rules it says it retained must still match the policy this AISP actually runs.
A study that disagrees with the live policy is a governance event, not a
receipt, and is rejected.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import AuditEvent

STUDY_SCHEMA = "MLB_SELECTION_POLICY_OPTIMIZATION_STUDY_V1"
REQUIRED_VERDICT = "MLB_STRICT_POLICY_RETAINED"
REQUIRED_OUTCOME = "KEEP_STRICT_CONTROL_UNCHANGED"
RETAINED_POLICY_ID = "AISP_PRACTICAL_PREGAME_POLICY_V2_1"

#: The audit action and entity that carry the receipt. Nothing else writes them.
AUDIT_ACTION = "RESEARCH_POLICY_STUDY_RECORDED"
AUDIT_ENTITY = "MLB_SELECTION_POLICY_STUDY"
RECEIPT_SCHEMA = "AISP_MLB_POLICY_STUDY_RECEIPT_V1"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")

#: Self-referential fields excluded from the study's own canonical identity.
_IDENTITY_EXCLUDED = ("studyId", "studySha256")


class PolicyStudyImportError(RuntimeError):
    """The study is missing, altered, mismatched, or asks to change policy."""


@dataclass(frozen=True)
class VerifiedPolicyStudy:
    path: Path
    study_id: str
    study_sha256: str
    verdict: str
    study: dict[str, Any]

    @property
    def retained_rules(self) -> dict[str, Any]:
        return dict(self.study.get("retainedControlRules") or {})


def _canonical_sha(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def verify_policy_study(
    path: str | Path,
    *,
    settings: Settings | None = None,
    expected_sha256: str | None = None,
) -> VerifiedPolicyStudy:
    """Verify the study fail-closed, or refuse it."""

    path = Path(path).resolve()
    try:
        study = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise PolicyStudyImportError(f"Study file is missing: {path}") from error
    except json.JSONDecodeError as error:
        raise PolicyStudyImportError(f"Study is not valid JSON: {error}") from error
    if not isinstance(study, dict):
        raise PolicyStudyImportError("Study must be a JSON object")

    if study.get("schemaVersion") != STUDY_SCHEMA:
        raise PolicyStudyImportError(
            f"Unsupported study schema {study.get('schemaVersion')!r}"
        )

    declared = str(study.get("studySha256") or "").lower()
    if not _SHA256.fullmatch(declared):
        raise PolicyStudyImportError("studySha256 is not a lowercase SHA-256 digest")
    if expected_sha256 and declared != expected_sha256.lower():
        raise PolicyStudyImportError(
            "Study identity does not match the expected digest"
        )
    body = {k: v for k, v in study.items() if k not in _IDENTITY_EXCLUDED}
    if _canonical_sha(body) != declared:
        raise PolicyStudyImportError(
            "The study body does not hash to its published identity"
        )
    # The study lives under its own digest, exactly like every other
    # content-addressed Model Studio artifact.
    if path.parent.name.lower() != declared:
        raise PolicyStudyImportError(
            "Study directory is not addressed by its own studySha256"
        )

    verdict = str(study.get("verdict") or "")
    if verdict != REQUIRED_VERDICT:
        raise PolicyStudyImportError(
            f"Only {REQUIRED_VERDICT} may be recorded; this study returned {verdict!r}"
        )

    decision = study.get("decision") or {}
    if decision.get("outcome") != REQUIRED_OUTCOME:
        raise PolicyStudyImportError(
            f"Study decision outcome must be {REQUIRED_OUTCOME}"
        )
    if decision.get("recommendedChallenger") is not None:
        raise PolicyStudyImportError(
            "Study recommends a challenger policy; AISP records no challenger"
        )
    if decision.get("aispShadowPolicyHandoff") is not None:
        raise PolicyStudyImportError(
            "Study carries a shadow-policy handoff; recording one would arm a policy"
        )

    _assert_authority(study.get("authority") or {})
    _assert_retained_rules_match_live_policy(study, settings)

    return VerifiedPolicyStudy(
        path=path,
        study_id=str(study.get("studyId") or ""),
        study_sha256=declared,
        verdict=verdict,
        study=study,
    )


def _assert_authority(authority: dict[str, Any]) -> None:
    """Every claim that would make the study unsafe to record."""

    expected = {
        "researchOnly": True,
        "wageringAuthorityGranted": False,
        "officialPolicyChanged": False,
        "finalOotOpened": False,
    }
    for key, value in expected.items():
        if authority.get(key) != value:
            raise PolicyStudyImportError(f"Study authority {key} must be {value!r}")
    for counter in ("aispWrites", "providerCalls", "positionsCreatedAlteredOrSettled",
                    "modelFits"):
        if int(authority.get(counter, -1)) != 0:
            raise PolicyStudyImportError(f"Study authority {counter} must be 0")


def _assert_retained_rules_match_live_policy(
    study: dict[str, Any], settings: Settings | None
) -> None:
    """The study must be describing the policy this AISP actually runs.

    A "retain" verdict whose retained rules differ from the live thresholds is
    not a receipt: recording it would document a policy that is not in force.
    """

    rules = study.get("retainedControlRules") or {}
    if rules.get("policyId") != RETAINED_POLICY_ID:
        raise PolicyStudyImportError(
            f"Study retains {rules.get('policyId')!r}, not the live {RETAINED_POLICY_ID}"
        )
    if settings is None:
        return

    from app.services.opportunity_service import MAX_QUOTE_STALENESS_MINUTES
    from app.services.practical_policy import MIN_ADJUSTED_EDGE
    from app.services.shadow_experiment_service import FINAL_PREGAME_CUTOFF_MINUTES

    live = {
        "minimumReadiness": settings.opportunity_qualified_readiness,
        "minimumAdjustedEdge": MIN_ADJUSTED_EDGE,
        "extremeDisagreementThreshold": settings.opportunity_extreme_disagreement_threshold,
        "finalPregameCutoffMinutes": FINAL_PREGAME_CUTOFF_MINUTES,
        "maximumBoardQuoteStalenessMinutes": MAX_QUOTE_STALENESS_MINUTES,
        "kellyFraction": settings.shadow_kelly_fraction,
        "unitPercentage": settings.shadow_unit_percentage,
        "maximumPositionPercentage": settings.shadow_max_position_percentage,
        "maximumSameGamePercentage": settings.shadow_max_same_game_percentage,
        "maximumDailyPercentage": settings.shadow_max_daily_percentage,
    }
    drift = {
        key: {"study": rules.get(key), "live": value}
        for key, value in live.items()
        if key in rules and float(rules[key]) != float(value)
    }
    if drift:
        raise PolicyStudyImportError(
            "Study retained rules do not match the live policy: "
            + json.dumps(drift, sort_keys=True)
        )


def _receipt(verified: VerifiedPolicyStudy, *, now: datetime) -> dict[str, Any]:
    study = verified.study
    source = study.get("source") or {}
    limitations = study.get("limitations") or {}
    return {
        "schemaVersion": RECEIPT_SCHEMA,
        "studyId": verified.study_id,
        "studySha256": verified.study_sha256,
        "verdict": verified.verdict,
        "decisionOutcome": (study.get("decision") or {}).get("outcome"),
        "decisionReasons": (study.get("decision") or {}).get("reasons") or [],
        "recommendedChallenger": None,
        "retainedPolicyId": (study.get("retainedControlRules") or {}).get("policyId"),
        "retainedControlRules": study.get("retainedControlRules") or {},
        "evidenceTimestamp": study.get("evidenceTimestamp"),
        "sourceCommits": {
            "baseCommit": source.get("baseCommit"),
            "acceptanceEvidenceCommit": source.get("acceptanceEvidenceCommit"),
            "aispOperationalAuditCommit": source.get("aispOperationalAuditCommit"),
        },
        "limitations": limitations,
        "artifactPath": str(verified.path),
        "recordedAt": now.isoformat(),
        # Restated on the row itself so the receipt cannot be read as an
        # activation, however it is queried later.
        "policyActivated": False,
        "challengerCreated": False,
        "decisionsCreated": 0,
        "positionsTouched": 0,
        "wageringAuthority": False,
        "v18DecisionAuthority": False,
    }


def record_policy_study(
    session: Session,
    verified: VerifiedPolicyStudy,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Record the receipt idempotently as one immutable audit event."""

    now = now or datetime.now(UTC)
    existing = _find_receipt(session, verified.study_sha256)
    if existing is not None:
        return {
            "recorded": False,
            "alreadyRecorded": True,
            "studySha256": verified.study_sha256,
            "auditEventId": str(existing.id),
            "recordedAt": (existing.metadata_json or {}).get("recordedAt"),
            "verdict": verified.verdict,
        }

    session.add(AuditEvent(
        action=AUDIT_ACTION,
        entity_type=AUDIT_ENTITY,
        occurred_at=now,
        metadata_json=_receipt(verified, now=now),
    ))
    session.flush()
    return {
        "recorded": True,
        "alreadyRecorded": False,
        "studySha256": verified.study_sha256,
        "studyId": verified.study_id,
        "verdict": verified.verdict,
        "recordedAt": now.isoformat(),
        "policyActivated": False,
        "challengerCreated": False,
    }


def _find_receipt(session: Session, study_sha256: str) -> AuditEvent | None:
    for row in session.scalars(
        select(AuditEvent)
        .where(AuditEvent.action == AUDIT_ACTION, AuditEvent.entity_type == AUDIT_ENTITY)
        .order_by(AuditEvent.occurred_at.desc())
    ).all():
        if (row.metadata_json or {}).get("studySha256") == study_sha256:
            return row
    return None


def recorded_policy_studies(session: Session) -> list[dict[str, Any]]:
    """Every recorded study receipt, newest first. Read-only."""

    rows = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.action == AUDIT_ACTION, AuditEvent.entity_type == AUDIT_ENTITY)
        .order_by(AuditEvent.occurred_at.desc())
    ).all()
    return [
        {
            "audit_event_id": str(row.id),
            "recorded_at": row.occurred_at.isoformat() if row.occurred_at else None,
            "study_id": (row.metadata_json or {}).get("studyId"),
            "study_sha256": (row.metadata_json or {}).get("studySha256"),
            "verdict": (row.metadata_json or {}).get("verdict"),
            "decision_outcome": (row.metadata_json or {}).get("decisionOutcome"),
            "decision_reasons": (row.metadata_json or {}).get("decisionReasons") or [],
            "retained_policy_id": (row.metadata_json or {}).get("retainedPolicyId"),
            "policy_activated": bool((row.metadata_json or {}).get("policyActivated")),
            "challenger_created": bool((row.metadata_json or {}).get("challengerCreated")),
            "source_commits": (row.metadata_json or {}).get("sourceCommits") or {},
            "artifact_path": (row.metadata_json or {}).get("artifactPath"),
        }
        for row in rows
    ]


__all__ = [
    "AUDIT_ACTION",
    "AUDIT_ENTITY",
    "PolicyStudyImportError",
    "REQUIRED_VERDICT",
    "VerifiedPolicyStudy",
    "record_policy_study",
    "recorded_policy_studies",
    "verify_policy_study",
]

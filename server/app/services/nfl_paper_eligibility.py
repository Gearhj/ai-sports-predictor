"""ASP_NFL_PAPER_RESEARCH_ELIGIBILITY_V1 — the consumer half of the binding.

WHAT THE PRODUCER SAID IS MISSING
---------------------------------
The 2026-09-14 handoff (SHA-256 45d2d835...) names the gap exactly:

    "The precise missing wire capability is an explicit binding to ONE
     separately authorized paper experiment with its cohort, original deadlines
     and stopping rules. The smallest proposed extension is
     ASP_NFL_PAPER_RESEARCH_ELIGIBILITY_V1, a specifically registered
     PAPER_RESEARCH_ELIGIBILITY kind in the existing research-supplement
     mechanism. ... Strict V1 fields cannot be bypassed by adding an ad hoc flag
     or renaming an existing supplement kind. Claude owns consumer
     implementation and verification."

So this module is the consumer implementation. It does NOT grant eligibility --
it CHECKS for a producer-issued, byte-verified eligibility supplement and
reports, in full, what is missing when there is none.

WHY IT CANNOT SIMPLY WRITE THE RECORD ITSELF
--------------------------------------------
The supplement mechanism is content-addressed: a manifest whose own canonical
identity recomputes to its directory name, with every artifact byte-verified
before it is read as data. Minting one here would be AISP asserting producer
evidence about a producer's model -- which is the definition of fabricated
provenance, and is precisely what the handoff forbids ("Strict V1 fields cannot
be bypassed"). The absence is reported instead.

WHAT A PASSING RECORD MUST BIND
-------------------------------
Every field in ``REQUIRED_BINDINGS``, because a binding that omits one of them
is not a binding to *this* experiment, *this* forecast and *this* cohort -- it
is a general permission wearing a specific name.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ResearchSupplement

#: The registered kind, exactly as the producer named it. Never an existing
#: kind renamed: reusing JOINT_SCORE_SIMULATION would make a distribution and a
#: permission indistinguishable in the same column.
KIND = "PAPER_RESEARCH_ELIGIBILITY"
CONTRACT_VERSION = "ASP_NFL_PAPER_RESEARCH_ELIGIBILITY_V1"

#: What the record must bind, from the handoff's own enumeration. A record that
#: is missing any of these is rejected rather than accepted with a gap.
REQUIRED_BINDINGS: tuple[str, ...] = (
    "paperExperimentId",
    "authorizationReceiptSha256",
    "cohortIdentity",
    "eventId",
    "forecastId",
    "bundleSha256",
    "modelBinarySha256",
    "featureContractSha256",
    "technicalEligibility",
    "originalClocks",
    "evidenceClocks",
)

#: Authority flags that must remain false in the record. If a supplement were
#: ever to arrive with one of these true, it would be granting execution under
#: the name of a paper permission, and it is refused.
MUST_REMAIN_FALSE: tuple[str, ...] = (
    "executionAllowed", "modelPromotionAllowed", "parlaysAllowed",
)


def eligibility_record(
    session: Session, *, event_id: str, forecast_id: str,
) -> ResearchSupplement | None:
    """The producer-issued eligibility supplement for one event, if it exists."""

    return session.scalar(
        select(ResearchSupplement).where(
            ResearchSupplement.kind == KIND,
            ResearchSupplement.contract_version == CONTRACT_VERSION,
            ResearchSupplement.sport == "NFL",
            ResearchSupplement.event_id == event_id,
            ResearchSupplement.forecast_id == forecast_id,
        ).order_by(ResearchSupplement.imported_at.desc()).limit(1)
    )


def check(
    session: Session, *, event_id: str, forecast_id: str,
    experiment_id: str, now: datetime | None = None,
) -> dict[str, Any]:
    """Whether this forecast may be used by this paper experiment.

    Fails closed and says why. "No record exists" and "a record exists but does
    not bind this experiment" are different answers and are reported as such.
    """

    moment = now or datetime.now(UTC)
    record = eligibility_record(session, event_id=event_id, forecast_id=forecast_id)
    if record is None:
        return {
            "eligible": False,
            "reason": "NO_PAPER_RESEARCH_ELIGIBILITY_RECORD",
            "detail": (
                "No producer-issued "
                f"{CONTRACT_VERSION} supplement binds forecast {forecast_id} to a "
                "paper experiment. The producer's own handoff names this as the "
                "missing wire capability. AISP will not mint one: the supplement "
                "mechanism is content-addressed producer evidence, and writing it "
                "here would be fabricating provenance."
            ),
            "required_bindings": list(REQUIRED_BINDINGS),
            "checked_at": moment.isoformat(),
            "blocks_position": True,
        }

    payload = dict(record.payload or {})
    missing = [field for field in REQUIRED_BINDINGS if not payload.get(field)]
    if missing:
        return {
            "eligible": False, "reason": "ELIGIBILITY_RECORD_INCOMPLETE",
            "detail": f"The record omits {', '.join(missing)}.",
            "missing_bindings": missing,
            "supplement_sha256": record.supplement_sha256,
            "checked_at": moment.isoformat(), "blocks_position": True,
        }

    granted = [flag for flag in MUST_REMAIN_FALSE if payload.get(flag)]
    if granted:
        return {
            "eligible": False, "reason": "ELIGIBILITY_RECORD_GRANTS_EXECUTION",
            "detail": (
                f"The record sets {', '.join(granted)} true. A paper permission "
                "may not carry execution or promotion authority, and one that "
                "does is refused rather than narrowed."
            ),
            "supplement_sha256": record.supplement_sha256,
            "checked_at": moment.isoformat(), "blocks_position": True,
        }

    if str(payload.get("paperExperimentId")) != str(experiment_id):
        return {
            "eligible": False, "reason": "ELIGIBILITY_BOUND_TO_ANOTHER_EXPERIMENT",
            "detail": (
                f"The record binds {payload.get('paperExperimentId')}, not "
                f"{experiment_id}. A binding to one experiment is not a binding "
                "to another."
            ),
            "supplement_sha256": record.supplement_sha256,
            "checked_at": moment.isoformat(), "blocks_position": True,
        }

    if not record.research_shadow_only:
        return {
            "eligible": False, "reason": "RECORD_NOT_RESEARCH_SHADOW_ONLY",
            "supplement_sha256": record.supplement_sha256,
            "checked_at": moment.isoformat(), "blocks_position": True,
        }

    return {
        "eligible": True, "reason": "PAPER_RESEARCH_ELIGIBILITY_BOUND",
        "supplement_sha256": record.supplement_sha256,
        "supplement_id": record.supplement_id,
        "paper_experiment_id": payload.get("paperExperimentId"),
        "cohort_identity": payload.get("cohortIdentity"),
        "authorization_receipt_sha256": payload.get("authorizationReceiptSha256"),
        "checked_at": moment.isoformat(), "blocks_position": False,
    }


def producer_request() -> dict[str, Any]:
    """The exact artifact AISP needs the producer to publish.

    Written as a request rather than a complaint: it names the kind, the
    contract, every required field and the flags that must stay false, so the
    producer can satisfy it without another round trip.
    """

    return {
        "request": "PUBLISH_PAPER_RESEARCH_ELIGIBILITY_SUPPLEMENT",
        "kind": KIND,
        "contract_version": CONTRACT_VERSION,
        "mechanism": (
            "The existing AISP_RESEARCH_SUPPLEMENT_V1 container: "
            "<exchange>/nfl-labs-supplements/sha256/<64-hex>/{manifest.json,"
            "supplement.json} plus the index entry. Content-addressed and "
            "byte-verified exactly like the joint-score supplements."
        ),
        "required_payload_fields": list(REQUIRED_BINDINGS),
        "flags_that_must_be_false": list(MUST_REMAIN_FALSE),
        "flags_that_must_be_true": ["researchShadowOnly"],
        "bind_to": {
            "paperExperimentId": "the AISP NFL 2026 experiment id (supplied below)",
            "authorizationReceiptSha256": (
                "the owner's approval receipt, so the permission is traceable to "
                "the decision that granted it"
            ),
            "cohortIdentity": "which games this permission covers, and until when",
            "eventId": "e.g. 2026_01_DEN_KC",
            "forecastId": "the exact asp-sports-game-forecast:sha256:... id",
            "originalClocks": (
                "source cutoff, forecast cutoff and frozen-at, unchanged from the "
                "forecast"
            ),
            "evidenceClocks": "when the evidence was gathered and published",
        },
        "must_not": [
            "grant execution or promotion authority",
            "change any forecast's existing restrictions",
            "reuse or rename an existing supplement kind",
        ],
        "consumer_status": (
            "Implemented and verifying. AISP checks for this record at every NFL "
            "final-pregame checkpoint and declines to record a position without "
            "it, retaining NO_PAPER_RESEARCH_ELIGIBILITY_RECORD as the reason."
        ),
    }


__all__ = ["CONTRACT_VERSION", "KIND", "MUST_REMAIN_FALSE", "REQUIRED_BINDINGS",
           "check", "eligibility_record", "producer_request"]

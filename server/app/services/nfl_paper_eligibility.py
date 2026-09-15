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

#: The experiment key the producer binds to.
#:
#: NOT an AISP database UUID. The delivered package states this explicitly --
#: `experimentIdMeaning: STABLE_POLICY_SPORT_SEASON_KEY_NOT_AISP_DATABASE_UUID`
#: -- and it is the right call: the producer cannot observe AISP's database, so
#: a UUID in a producer document would be a claim it could not have verified.
#: The consumer therefore checks the stable key it CAN verify, and separately
#: resolves that key to its own era.
def experiment_key(policy_version: str, sport: str, season_id: str) -> str:
    return f"{policy_version}:{sport}:{season_id}"


#: What the record must bind, at the paths the DELIVERED contract uses. These
#: are dotted paths into the supplement document, not flat keys: the producer's
#: shape is the contract, and reshaping it here would be inventing a second one.
REQUIRED_BINDINGS: tuple[str, ...] = (
    "binding.eventId",
    "binding.engineId",
    "binding.forecastId",
    "binding.modelArtifactSha256",
    "summaries.authorization.experimentId",
    "summaries.authorization.policyVersion",
    "summaries.authorization.mode",
    "summaries.authorization.consumerVirtualExperimentAuthorized",
    "summaries.frozenConfiguration.forecastId",
    "summaries.frozenConfiguration.homeWinProbability",
    "summaries.frozenConfiguration.awayWinProbability",
    "summaries.frozenConfiguration.tieProbability",
    "summaries.probabilitySemantics.settlementMustBeExplicit",
)


def _at(doc: dict[str, Any], path: str) -> Any:
    """Read a dotted path, returning None at the first missing step."""

    node: Any = doc
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def stored_document(record: ResearchSupplement) -> dict[str, Any]:
    """The supplement as the IMPORTER stored it, reassembled for checking.

    The importer splits one document across two columns: `binding` and
    `governance` go to `evidence` (they are what it verified), and the
    `summaries` go to `payload`. Reading the raw document shape from `payload`
    alone finds no binding at all -- and reports it as a producer omission,
    which is both wrong and the most misleading answer available. So the view
    is reassembled from where the rows actually live.
    """

    payload = dict(record.payload or {})
    evidence = dict(record.evidence or {})
    return {
        "binding": evidence.get("binding") or {},
        "governance": evidence.get("governance") or {},
        "summaries": payload.get("summaries") or {},
    }

#: Authority flags that must remain false, at the delivered contract's own
#: paths. If a supplement ever arrived with one of these true it would be
#: granting execution under the name of a paper permission, and it is refused
#: outright rather than narrowed to something acceptable.
MUST_REMAIN_FALSE: tuple[str, ...] = (
    "governance.executionAllowedInAisp",
    "governance.modelPromotionAllowed",
    "governance.wageringAuthority",
    "summaries.authorization.realMoneyAuthorized",
    "summaries.frozenConfiguration.executionAllowed",
    "summaries.frozenConfiguration.modelPromotionAllowed",
)

#: And the one that must remain true.
MUST_REMAIN_TRUE: tuple[str, ...] = ("governance.researchShadowOnly",)


def eligibility_record(
    session: Session, *, event_id: str, forecast_id: str,
) -> ResearchSupplement | None:
    """The producer-issued eligibility supplement for one event, if it exists."""

    # `artifact_version` carries the ELIGIBILITY contract; `contract_version`
    # carries the CONTAINER it travelled in (AISP_RESEARCH_SUPPLEMENT_V1). They
    # are different things and the registration writes them to different
    # columns, so matching the eligibility contract against contract_version
    # silently finds nothing -- which reads exactly like "the producer never
    # published it".
    return session.scalar(
        select(ResearchSupplement).where(
            ResearchSupplement.kind == KIND,
            ResearchSupplement.artifact_version == CONTRACT_VERSION,
            ResearchSupplement.sport == "NFL",
            ResearchSupplement.event_id == event_id,
            ResearchSupplement.forecast_id == forecast_id,
        ).order_by(ResearchSupplement.imported_at.desc()).limit(1)
    )


def check(
    session: Session, *, event_id: str, forecast_id: str,
    experiment_id: str, now: datetime | None = None,
    policy_version: str = "AISP_NFL_PAPER_POLICY_V1",
    sport: str = "NFL", season_id: str = "2026",
) -> dict[str, Any]:
    """Whether this forecast may be used by this paper experiment.

    Fails closed and says why. "No record exists", "a record exists but omits a
    binding" and "a record exists but binds another experiment" are different
    answers and are reported as such, because each sends a reader somewhere
    different.

    `experiment_id` is AISP's own era UUID and is reported for the receipt. It
    is deliberately NOT what the producer document is matched on: the producer
    cannot observe AISP's database, so it binds the stable
    `policy:sport:season` key instead, and that is what is compared.
    """

    moment = now or datetime.now(UTC)
    record = eligibility_record(session, event_id=event_id, forecast_id=forecast_id)
    if record is None:
        return {
            "eligible": False,
            "reason": "NO_PAPER_RESEARCH_ELIGIBILITY_RECORD",
            "detail": (
                f"No producer-issued {CONTRACT_VERSION} supplement binds "
                f"forecast {forecast_id} to a paper experiment. AISP will not "
                "mint one: the supplement mechanism is content-addressed "
                "producer evidence, and writing it here would be fabricating "
                "provenance."
            ),
            "required_bindings": list(REQUIRED_BINDINGS),
            "checked_at": moment.isoformat(),
            "blocks_position": True,
        }

    payload = stored_document(record)
    missing = [path for path in REQUIRED_BINDINGS if _at(payload, path) is None]
    if missing:
        return {
            "eligible": False, "reason": "ELIGIBILITY_RECORD_INCOMPLETE",
            "detail": f"The record omits {', '.join(missing)}.",
            "missing_bindings": missing,
            "supplement_sha256": record.supplement_sha256,
            "checked_at": moment.isoformat(), "blocks_position": True,
        }

    granted = [path for path in MUST_REMAIN_FALSE if _at(payload, path) is not False]
    if granted:
        return {
            "eligible": False, "reason": "ELIGIBILITY_RECORD_GRANTS_EXECUTION",
            "detail": (
                f"The record does not hold {', '.join(granted)} false. A paper "
                "permission may not carry execution, promotion or real-money "
                "authority, and one that does is refused rather than narrowed."
            ),
            "supplement_sha256": record.supplement_sha256,
            "checked_at": moment.isoformat(), "blocks_position": True,
        }

    withheld = [path for path in MUST_REMAIN_TRUE if _at(payload, path) is not True]
    if withheld or not record.research_shadow_only:
        return {
            "eligible": False, "reason": "RECORD_NOT_RESEARCH_SHADOW_ONLY",
            "detail": f"Not asserted: {', '.join(withheld) or 'research_shadow_only'}.",
            "supplement_sha256": record.supplement_sha256,
            "checked_at": moment.isoformat(), "blocks_position": True,
        }

    authorization = _at(payload, "summaries.authorization") or {}
    expected = experiment_key(policy_version, sport, season_id)
    if str(authorization.get("experimentId")) != expected:
        return {
            "eligible": False, "reason": "ELIGIBILITY_BOUND_TO_ANOTHER_EXPERIMENT",
            "detail": (
                f"The record binds {authorization.get('experimentId')}, not "
                f"{expected}. A binding to one experiment is not a binding to "
                "another."
            ),
            "supplement_sha256": record.supplement_sha256,
            "checked_at": moment.isoformat(), "blocks_position": True,
        }

    if str(authorization.get("policyVersion")) != policy_version:
        return {
            "eligible": False, "reason": "ELIGIBILITY_BOUND_TO_ANOTHER_POLICY",
            "detail": (
                f"The record binds policy {authorization.get('policyVersion')}, "
                f"not {policy_version}."
            ),
            "supplement_sha256": record.supplement_sha256,
            "checked_at": moment.isoformat(), "blocks_position": True,
        }

    if str(authorization.get("mode")) != "VIRTUAL_RESEARCH_ONLY":
        return {
            "eligible": False, "reason": "ELIGIBILITY_MODE_IS_NOT_VIRTUAL_RESEARCH",
            "supplement_sha256": record.supplement_sha256,
            "checked_at": moment.isoformat(), "blocks_position": True,
        }

    frozen = _at(payload, "summaries.frozenConfiguration") or {}
    if str(frozen.get("forecastId")) != forecast_id:
        return {
            "eligible": False, "reason": "ELIGIBILITY_FREEZES_ANOTHER_FORECAST",
            "detail": (
                f"The frozen configuration names {frozen.get('forecastId')}, "
                f"not the forecast being evaluated ({forecast_id})."
            ),
            "supplement_sha256": record.supplement_sha256,
            "checked_at": moment.isoformat(), "blocks_position": True,
        }

    return {
        "eligible": True, "reason": "PAPER_RESEARCH_ELIGIBILITY_BOUND",
        "supplement_sha256": record.supplement_sha256,
        "supplement_id": record.supplement_id,
        "paper_experiment_key": authorization.get("experimentId"),
        "aisp_experiment_id": str(experiment_id),
        "policy_version": authorization.get("policyVersion"),
        "producer_disposition": _at(payload, "summaries.producerDisposition"),
        # The frozen masses, carried through untouched. Every one of the three
        # travels: a downstream reader that sees only two cannot tell whether
        # the third was zero or was dropped.
        "frozen_probabilities": {
            "away_win": frozen.get("awayWinProbability"),
            "home_win": frozen.get("homeWinProbability"),
            "tie": frozen.get("tieProbability"),
        },
        "checked_at": moment.isoformat(), "blocks_position": False,
    }


#: Reasons that mean "the producer's permission is missing", as opposed to a
#: permission that exists but is defective. Only these can be superseded later
#: in the same window by a newly imported exact supplement.
MISSING_PERMISSION_REASONS = frozenset({
    "NO_PAPER_RESEARCH_ELIGIBILITY_RECORD", "AWAITING_SUCCESSOR_ELIGIBILITY",
})


def successor_gap(
    session: Session, *, event_id: str, forecast_id: str, binding: dict[str, Any],
) -> dict[str, Any]:
    """Name the successor case instead of reporting a generic absence.

    Eligibility is exact-object-specific. When a newer forecast (a later CURRENT
    revision or a FINAL_PREGAME) has replaced an object the producer DID
    authorize, the newer object is not eligible and the older one is not
    evaluated in its place: the producer only mints a successor when the
    verified feature vector changed, so the older object no longer describes the
    game. The honest state is AWAITING_SUCCESSOR_ELIGIBILITY, naming both.
    """

    if binding.get("eligible") or binding.get("reason") != "NO_PAPER_RESEARCH_ELIGIBILITY_RECORD":
        return binding
    prior = session.scalar(
        select(ResearchSupplement).where(
            ResearchSupplement.kind == KIND,
            ResearchSupplement.artifact_version == CONTRACT_VERSION,
            ResearchSupplement.sport == "NFL",
            ResearchSupplement.event_id == event_id,
            ResearchSupplement.forecast_id != forecast_id,
        ).order_by(ResearchSupplement.imported_at.desc()).limit(1)
    )
    if prior is None:
        return binding
    return {
        **binding,
        "reason": "AWAITING_SUCCESSOR_ELIGIBILITY",
        "detail": (
            f"Forecast {forecast_id} supersedes {prior.forecast_id}, which the producer "
            f"authorized (supplement {prior.supplement_sha256}). Eligibility is exact-object-"
            "specific and does not transfer; the superseded object is not evaluated in its "
            "place. A position requires the producer's successor supplement for the newer object."
        ),
        "eligible_predecessor": {"forecast_id": prior.forecast_id,
                                 "supplement_sha256": prior.supplement_sha256},
        "blocks_position": True,
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


__all__ = ["CONTRACT_VERSION", "KIND", "MISSING_PERMISSION_REASONS", "MUST_REMAIN_FALSE",
           "MUST_REMAIN_TRUE", "REQUIRED_BINDINGS", "check", "eligibility_record",
           "experiment_key", "producer_request", "stored_document", "successor_gap"]

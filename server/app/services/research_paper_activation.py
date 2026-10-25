"""Open the NBA and College Football virtual research accounts, once each.

Authorised by the project owner on 2026-09-15: each sport receives exactly one
$1,000 SEASON_OPENING contribution, a paper era under its own policy, and the
membership linking the two. The same shape as the NFL activation, deliberately,
so there is one way a paper account comes into existence.

FUNDED IS NOT ELIGIBLE
----------------------
Opening the account grants capital, not decision authority. Every evaluation
still requires the producer's own eligibility record for the exact forecast, and
declines with AWAITING_PRODUCER_ELIGIBILITY until one is imported. The era is
active so it appears honestly on every surface; its checkpoint refuses rather
than guesses.

IDEMPOTENT BY CONSTRUCTION
--------------------------
Every step asks before it writes, and the funding row carries the program's
unique idempotency key, so a second run returns the same account, funding row
and era with ``created=False``. A season receives exactly one contribution.

PROVENANCE IS RECORDED, NOT ASSUMED
-----------------------------------
Who opened an account and on what authority is written into the funding row
(``executed_by``, ``note``), the era membership (``role_basis``) and, for a new
era, its ``policy_config["approval"]``. NBA and CFB were opened on the project
owner's 2026-09-15 approval and every existing caller keeps exactly those words
(``ActivationProvenance.legacy``). NHL is NOT in ``POLICIES``, so
``activate_all`` never opens it; opening it is an explicit
``activate(session, settings, NHL, provenance=NHL_STANDING_AUTHORITY)``, which
records the authority it was actually opened under.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import MembershipRole, ShadowExperiment
from app.services.experiment_accounts import ExperimentAccountService
from app.services.research_paper_policy import (
    POLICIES, STARTING_BANKROLL, SportPaperPolicy, policy_config,
)

#: experiment_account_funding.executed_by is varchar(80).
EXECUTED_BY_MAX = 80


@dataclass(frozen=True)
class ActivationProvenance:
    """The truth about who opened a paper account and under what authority."""

    executed_by: str
    note: str
    role_basis: str
    #: Replaces ``policy_config["approval"]`` on a NEW era; None keeps the policy's own record.
    approval: dict[str, Any] | None = None
    decision_authority: str = "REQUIRES_PRODUCER_ELIGIBILITY"
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.executed_by or len(self.executed_by) > EXECUTED_BY_MAX:
            raise ValueError(f"executed_by must be 1..{EXECUTED_BY_MAX} characters: {self.executed_by!r}")
        if not self.note or not self.role_basis:
            raise ValueError("an activation records its note and its membership basis")

    @classmethod
    def legacy(cls, policy: SportPaperPolicy) -> ActivationProvenance:
        """Exactly what NBA/CFB activation has always written (the owner's 2026-09-15 approval)."""

        return cls(
            executed_by=f"{policy.sport.lower()}-activation/project-owner-approval-2026-09-15",
            note=(f"{policy.label} {policy.season_id} virtual research account, opened on the "
                  "project owner's explicit 2026-09-15 approval. Virtual research only; "
                  "positions additionally require producer eligibility evidence."),
            role_basis=(f"Opened with the season's one $1,000 contribution under the project "
                        "owner's explicit 2026-09-15 approval. Virtual research only."),
        )


#: Opening the NHL 2026-27 paper era: James's standing authority of 2026-10-15 and the coordinator's
#: 2026-10-16 decision to run the accepted, frozen NHL Gen1 model inside AISP. Never used by activate_all.
NHL_STANDING_AUTHORITY_REFERENCE = (
    "JAMES_STANDING_AUTHORITY_2026-10-15 (PRODUCER_STATUS 22:15Z; NFL/CFB/NBA/NHL 2026-27, existing models "
    "and policies, included allowances, no new USD)")
NHL_COORDINATOR_DECISION = "CLAUDE_COORDINATOR_NHL_SIMPLIFICATION_2026-10-16"
NHL_STANDING_AUTHORITY = ActivationProvenance(
    executed_by="nhl-activation/james-standing-authority-2026-10-15/claude-coordinator",
    note=("NHL 2026-27 virtual research account, opened under " + NHL_STANDING_AUTHORITY_REFERENCE
          + " by coordinator decision " + NHL_COORDINATOR_DECISION + " (AISP runs the accepted, frozen NHL "
          "Gen1 model in-process). Virtual research only; no new USD. Every position still requires "
          "eligibility: the producer's ASP_NHL_PAPER_RESEARCH_ELIGIBILITY_V1 record, or for an AISP "
          "in-process forecast AISP_NHL_IN_PROCESS_ELIGIBILITY_V1."),
    role_basis=("Opened with the season's one $1,000 contribution under " + NHL_STANDING_AUTHORITY_REFERENCE
                + "; coordinator decision " + NHL_COORDINATOR_DECISION + ". Virtual research only."),
    approval={"authority": NHL_STANDING_AUTHORITY_REFERENCE, "granted": "2026-10-15",
              "coordinator_decision": NHL_COORDINATOR_DECISION,
              "scope": "NHL 2026-27 $1,000 virtual research account and AISP_NHL_PAPER_POLICY_V1; no new USD"},
    decision_authority="REQUIRES_PRODUCER_OR_AISP_IN_PROCESS_ELIGIBILITY",
)


def _code_identity() -> tuple[str, str]:
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                                text=True, check=True, timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001 - identity must never block activation
        commit = "unknown"
    return commit, f"git:{commit}"


def activate(
    session: Session, settings: Settings, policy: SportPaperPolicy, *,
    now: datetime | None = None, provenance: ActivationProvenance | None = None,
) -> dict[str, Any]:
    """Open the account, fund it once, register and link the era. Idempotent.

    ``provenance`` records who opened it and on what authority; omitted, it is exactly the
    2026-09-15 owner-approval wording every existing caller has always written.
    """

    supplied = provenance is not None
    provenance = provenance or ActivationProvenance.legacy(policy)
    moment = now or datetime.now(UTC)
    service = ExperimentAccountService(session, settings)

    account = service.ensure_account(policy.sport, policy.season_id)
    # Tracking starts today where the accounting window is already open (CFB),
    # and at the window's own opening where it is not (NBA opens October 1) --
    # a contribution is never effective before the season it funds.
    tracking = moment.date() if moment.date() >= account.season_opens_on else None
    funding, funded_now = service.open_season(
        account, now=moment,
        executed_by=provenance.executed_by,
        tracking_started_on=tracking,
        note=provenance.note,
    )

    era = session.scalar(select(ShadowExperiment).where(
        ShadowExperiment.sport == policy.sport,
        ShadowExperiment.decision_policy == policy.policy_version,
    ))
    era_created = False
    if era is None:
        commit, identity = _code_identity()
        highest = session.scalar(
            select(ShadowExperiment.version).order_by(ShadowExperiment.version.desc()).limit(1)
        ) or 0
        era = ShadowExperiment(
            name=policy.era_name,
            version=highest + 1,
            started_at=moment,
            start_date=moment.date(),
            starting_bankroll=STARTING_BANKROLL,
            sport=policy.sport,
            recommendation_policy_version=policy.policy_version,
            staking_policy_version=policy.staking_version,
            decision_policy=policy.policy_version,
            controlling_engine=policy.controlling_engine,
            supported_markets=list(policy.markets),
            kelly_fraction=policy.kelly_fraction,
            unit_percentage=policy.max_position_percentage,
            max_position_percentage=policy.max_position_percentage,
            max_same_game_percentage=policy.max_same_game_percentage,
            max_daily_percentage=policy.max_daily_percentage,
            code_commit=commit,
            code_identity=identity,
            is_active=True,
            policy_config=_era_config(policy, provenance, code_commit=commit, code_identity=identity,
                                      supplied=supplied),
        )
        session.add(era)
        session.flush()
        era_created = True

    _membership, linked_now = service.attach(
        account, era, now=moment, role=MembershipRole.FUNDED_ERA, role_basis=provenance.role_basis,
    )
    extra = {"provenance": {"executed_by": funding.executed_by, "note": funding.note,
                            "approval": provenance.approval, **provenance.extra}} if supplied else {}
    return {
        "sport": policy.sport,
        "season_id": policy.season_id,
        "account_id": str(account.id),
        "funding_id": str(funding.id),
        "funding_idempotency_key": funding.idempotency_key,
        "funding_amount": float(funding.amount),
        "funding_effective_on": funding.effective_on.isoformat(),
        "funded_now": funded_now,
        "era_id": str(era.id),
        "era_created": era_created,
        "linked_now": linked_now,
        "policy_version": policy.policy_version,
        "decision_authority": provenance.decision_authority,
        **extra,
    }


def _era_config(policy: SportPaperPolicy, provenance: ActivationProvenance, *, code_commit: str,
                code_identity: str, supplied: bool) -> dict[str, Any]:
    config = policy_config(policy, code_commit=code_commit, code_identity=code_identity)
    if supplied:
        if provenance.approval is not None:
            config["approval"] = dict(provenance.approval)
            # The policy was DEFINED without authority; this era exists because of the approval above.
            authority = dict(config.get("authority") or {})
            config["authority"] = {**authority, "status": "OPENED_UNDER_RECORDED_APPROVAL",
                                   "status_when_defined": authority.get("status")}
        config["activation"] = {"executed_by": provenance.executed_by,
                                "decision_authority": provenance.decision_authority, **provenance.extra}
    return config


def activate_all(session: Session, settings: Settings, *, now: datetime | None = None) -> list[dict[str, Any]]:
    return [activate(session, settings, policy, now=now) for policy in POLICIES.values()]


__all__ = ["NHL_STANDING_AUTHORITY", "ActivationProvenance", "activate", "activate_all"]

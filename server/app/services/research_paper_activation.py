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
"""

from __future__ import annotations

import subprocess
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


def _code_identity() -> tuple[str, str]:
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                                text=True, check=True, timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001 - identity must never block activation
        commit = "unknown"
    return commit, f"git:{commit}"


def activate(
    session: Session, settings: Settings, policy: SportPaperPolicy, *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Open the account, fund it once, register and link the era. Idempotent."""

    moment = now or datetime.now(UTC)
    service = ExperimentAccountService(session, settings)

    account = service.ensure_account(policy.sport, policy.season_id)
    # Tracking starts today where the accounting window is already open (CFB),
    # and at the window's own opening where it is not (NBA opens October 1) --
    # a contribution is never effective before the season it funds.
    tracking = moment.date() if moment.date() >= account.season_opens_on else None
    funding, funded_now = service.open_season(
        account, now=moment,
        executed_by=f"{policy.sport.lower()}-activation/project-owner-approval-2026-09-15",
        tracking_started_on=tracking,
        note=(f"{policy.label} {policy.season_id} virtual research account, opened on the "
              "project owner's explicit 2026-09-15 approval. Virtual research only; "
              "positions additionally require producer eligibility evidence."),
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
            policy_config=policy_config(policy, code_commit=commit, code_identity=identity),
        )
        session.add(era)
        session.flush()
        era_created = True

    _membership, linked_now = service.attach(
        account, era, now=moment, role=MembershipRole.FUNDED_ERA,
        role_basis=(f"Opened with the season's one $1,000 contribution under the project "
                    "owner's explicit 2026-09-15 approval. Virtual research only."),
    )
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
        "decision_authority": "REQUIRES_PRODUCER_ELIGIBILITY",
    }


def activate_all(session: Session, settings: Settings, *, now: datetime | None = None) -> list[dict[str, Any]]:
    return [activate(session, settings, policy, now=now) for policy in POLICIES.values()]


__all__ = ["activate", "activate_all"]

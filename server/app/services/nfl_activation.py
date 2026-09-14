"""Open the NFL 2026 virtual research experiment, once.

WHAT THIS DOES
--------------
Turns the prepared ``AISP_NFL_PAPER_POLICY_V1`` into a running paper era under
the project owner's explicit 2026-09-14 approval: the NFL 2026 season account
receives its one $1,000 SEASON_OPENING contribution, an NFL-only decision era is
registered, and the two are linked.

IDEMPOTENT BY CONSTRUCTION
--------------------------
Every step asks before it writes, and the funding row additionally carries the
program's unique idempotency key, so a second activation returns the same
account, the same funding row and the same era with ``created=False``. Running
this twice must never produce a second $1,000 -- that would be inventing
capital, and the season-account rule is that a season receives exactly one
contribution.

WHAT IT REFUSES TO TOUCH
------------------------
The MLB account, its eras, its positions and its settlements. NFL capital is a
separate account with its own membership rows; nothing here reads or writes an
MLB balance. It also grants no execution authority: the producer's forecasts
remain RESEARCH_SHADOW / NOT_PROMOTED / NOT_EXECUTABLE, and a paper position is
not an execution.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import MembershipRole, ShadowExperiment
from app.services import nfl_paper_policy as policy
from app.services.experiment_accounts import ExperimentAccountService

SPORT = "NFL"
SEASON_ID = "2026"

#: The era's public name. Distinct from every MLB era so a reader scanning the
#: ledger can never mistake one programme for the other.
ERA_NAME = "ASP NFL RESEARCH PAPER BANKROLL — 2026 V1"

#: The owner's approval, recorded as evidence rather than assumed.
APPROVAL = {
    "authority": "project owner",
    "granted_utc": "2026-09-14T16:50:39+00:00",
    "scope": (
        "Open the NFL 2026 virtual research experiment and reallocate one daily "
        "SportsGameOdds event object from NFL live scores to NFL pregame odds."
    ),
    "real_money": "NEVER — virtual research only, no sportsbook execution",
    "producer_handoff_sha256": (
        "45d2d835fb9b8963f461ed03fec6cf915cada2fea6452a9d1efc03498dbbe5c9"
    ),
}

#: Producer authority, carried through unchanged. Activating a CONSUMER paper
#: experiment changes none of these, and a paper position is not an execution.
PRODUCER_FLAGS = {
    "researchShadowOnly": True,
    "promotionStatus": "NOT_PROMOTED",
    "executionStatus": "NOT_EXECUTABLE",
    "executionAllowed": False,
    "modelPromotionAllowed": False,
    "parlaysAllowed": False,
}

#: The exact authorised research configuration, from the producer handoff.
MODEL_BINDING = {
    "engine_id": "NFL_M1_XGBOOST",
    "engine_version": "NFL_M1_2026_RESEARCH_SHADOW_V1",
    "candidate_id": "nfl-m1-xgboost-fixed-research-challenger-2026-shadow",
    "producer_revision": "5d114f5502d875b012dc75672db975166e09e561",
    "model_binary_sha256": (
        "7bec06646fd5035380107501d07e139425ac483a47e02e0bbbd56301124fb8fd"),
    "model_manifest_sha256": (
        "ab9c066052b288f3f6162d8f8558e673988fb52172c72fe00aa855aa800a45e9"),
    "feature_contract_sha256": (
        "ef5c4f9ce8c3dd04a473e7ecb2ea3a255154f65a1bb3fda9d2ebc19bf1cb8f4d"),
    "training": "7,276 games, 1999-2025 seasons, cutoff 2026-02-08",
    "calibration": "NONE",
    "simulator_id": "NFL_M1_EMPIRICAL_PAIRED_RESIDUAL",
    "simulator_version": "NFL_M1_EMPIRICAL_CORRELATED_RESIDUAL_V1",
}


def _code_identity() -> tuple[str, str]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
            check=True, timeout=10,
        ).stdout.strip()
    except Exception:  # noqa: BLE001 - identity must never block activation
        commit = "unknown"
    return commit, f"git:{commit}"


def policy_config() -> dict[str, Any]:
    """The frozen policy contract this era runs under."""

    commit, identity = _code_identity()
    return {
        "policy_version": policy.POLICY_VERSION,
        "staking_version": policy.STAKING_VERSION,
        "sport": SPORT,
        "markets": ["MONEYLINE"],
        # The NFL-specific part. Baseball has no ties, so this has no MLB
        # analogue and is stated rather than inherited.
        "tie_treatment": policy.TIE_TREATMENT,
        "tie_settlement": (
            "A tie settles as a PUSH: the virtual stake is returned and the "
            "position's net is zero. It is neither a win nor a loss."
        ),
        "expected_net_return": "pWin x (decimalOdds - 1) - pLoss",
        "quarter_kelly": (
            "expectedNetReturn / [4 x (decimalOdds - 1) x (pWin + pLoss)], then "
            "the exposure caps"
        ),
        "kelly_fraction": policy.KELLY_FRACTION,
        "min_adjusted_edge": policy.MIN_ADJUSTED_EDGE,
        "max_position_percentage": policy.POSITION_CAP_UNCALIBRATED,
        "max_same_game_percentage": policy.MAX_SAME_GAME_PERCENTAGE,
        "max_daily_percentage": policy.MAX_DAILY_PERCENTAGE,
        # Explicitly absent, so a later reader does not go looking for them.
        "inherited_mlb_adjustments": None,
        "no_pitcher_penalty": True,
        "no_lineup_penalty": True,
        "pitcher_lineup_note": (
            "The MLB starter (2.0 pt) and lineup (0.5 pt) penalties are "
            "calibrated on starting pitchers and MLB lineup xwOBA. They have no "
            "NFL analogue and are deliberately not applied here."
        ),
        "timing": {
            "final_pregame_window": "30 to 15 minutes before kickoff",
            "quote_freshness_limit_minutes": 240,
            "note": (
                "The existing pregame timing, freshness and qualification "
                "requirements are unchanged; NFL adds no exception to them."
            ),
        },
        "model_binding": MODEL_BINDING,
        "producer_flags": PRODUCER_FLAGS,
        "approval": APPROVAL,
        "code_commit": commit,
        "code_identity": identity,
    }


def activate(
    session: Session, settings: Settings, *, now: datetime | None = None,
) -> dict[str, Any]:
    """Open the account, fund it once, and register the era. Idempotent."""

    moment = now or datetime.now(UTC)
    service = ExperimentAccountService(session, settings)

    account = service.ensure_account(SPORT, SEASON_ID)
    funding, funded_now = service.open_season(
        account, now=moment,
        executed_by="nfl-activation/project-owner-approval-2026-09-14",
        tracking_started_on=moment.date(),
        note=(
            "NFL 2026 virtual research experiment, opened on the project "
            "owner's explicit approval. Virtual research only."
        ),
    )

    era = session.scalar(
        select(ShadowExperiment).where(
            ShadowExperiment.sport == SPORT,
            ShadowExperiment.decision_policy == policy.POLICY_VERSION,
        )
    )
    era_created = False
    if era is None:
        commit, identity = _code_identity()
        highest = session.scalar(
            select(ShadowExperiment.version)
            .order_by(ShadowExperiment.version.desc()).limit(1)
        ) or 0
        era = ShadowExperiment(
            name=ERA_NAME,
            version=highest + 1,
            started_at=moment,
            start_date=moment.date(),
            starting_bankroll=policy.STARTING_BANKROLL,
            sport=SPORT,
            recommendation_policy_version=policy.POLICY_VERSION,
            staking_policy_version=policy.STAKING_VERSION,
            decision_policy=policy.POLICY_VERSION,
            controlling_engine=MODEL_BINDING["engine_id"],
            supported_markets=["MONEYLINE"],
            kelly_fraction=policy.KELLY_FRACTION,
            unit_percentage=policy.POSITION_CAP_UNCALIBRATED,
            max_position_percentage=policy.POSITION_CAP_UNCALIBRATED,
            max_same_game_percentage=policy.MAX_SAME_GAME_PERCENTAGE,
            max_daily_percentage=policy.MAX_DAILY_PERCENTAGE,
            code_commit=commit,
            code_identity=identity,
            is_active=True,
            policy_config=policy_config(),
        )
        session.add(era)
        session.flush()
        era_created = True

    membership, linked_now = service.attach(
        account, era, now=moment,
        role=MembershipRole.FUNDED_ERA,
        role_basis=(
            "Opened with the season's one $1,000 contribution under the project "
            "owner's explicit 2026-09-14 approval. Virtual research only."
        ),
    )

    return {
        "account_id": str(account.id),
        "account_status": str(account.status),
        "sport": account.sport,
        "season_id": account.season_id,
        "season_label": account.season_label,
        "funding_id": str(funding.id),
        "funding_amount": float(funding.amount),
        "funding_kind": str(funding.kind),
        "funding_idempotency_key": funding.idempotency_key,
        "experiment_id": str(era.id),
        "policy_version": era.decision_policy,
        "staking_version": era.staking_policy_version,
        "membership_id": str(membership.id),
        "created": {
            "funding": funded_now,
            "experiment": era_created,
            "membership": linked_now,
        },
        "already_active": not (funded_now or era_created or linked_now),
    }


__all__ = ["APPROVAL", "ERA_NAME", "MODEL_BINDING", "PRODUCER_FLAGS", "SEASON_ID",
           "SPORT", "activate", "policy_config"]

"""Governed VOID correction for an MLB paper position already settled on a makeup game.

Before AISP_MLB_POSTPONED_NOT_PLAYED_ON_ORIGINAL_ET_DATE_V1 existed, the
settlement owner settled any open position whose game row was FINAL -- so a
position placed on a rained-out occurrence was settled W/L on the makeup. A
settled position and its ``position_results`` row are immutable; the only
honest correction is ONE ``position_settlement_adjustments`` row beside the
original, which every bankroll reader adds (previous result, corrected VOID,
previous/corrected P&L and the delta), plus one audit event.

Nothing here runs on its own and nothing is applied by default:

* the registry ``GOVERNED_VOID_CORRECTIONS`` is EMPTY in code. A position is
  corrected only after a reviewed commit names it -- with the settlement id,
  result, stake and P&L the owner reviewed -- and sets ``mode="APPLY"``. An
  owner may equally decide a postponed bet keeps its action, in which case it
  is never added;
* ``plan`` is read-only and refuses unless the position still meets the SAME
  occurrence rule the settlement owner now applies (``app.bankroll.occurrence``),
  matches every registry expectation exactly and has never been adjusted;
* ``apply`` writes exactly one adjustment and one audit event inside a
  SAVEPOINT. ``UNIQUE(position_id, game_state_observation_id)`` with the
  non-NULL postponement observation id makes a second write impossible even
  when two runs race (the admin job route runs outside the job lanes).

Two admin-only jobs, never scheduled, return every plan as their
``job_runs.result``: ``plan-postponed-position-voids`` is the DRY RUN (it
never writes, whatever an entry's mode) and ``correct-postponed-position-voids``
applies the entries whose mode is APPLY. So one reviewed commit adding an
APPLY entry is deployed once, dry-run first, then applied.

An entry may name the original Eastern date the owner reviewed
(``expected_original_et_date``). It must equal the date the stored evidence
gives; where that evidence is AMBIGUOUS (the settlement owner then settled the
legacy way and raised ``position.occurrence_review_required``), the owner's
date is the one the occurrence rule is evaluated on -- the rule itself
(postponed/canceled on that date, never played on it) is never waived.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.bankroll import occurrence as occ
from app.models import (
    AuditEvent, Game, Market, MarketOutcome, OfficialAspPosition, PositionResult, PositionSettlement,
    PositionSettlementAdjustment, PositionStatus, ShadowExperiment, ShadowExperimentDecision,
)

JOB_NAME = "correct-postponed-position-voids"
JOB_LABEL = "Governed Postponement VOID Corrections (applies registry entries marked APPLY)"
PLAN_JOB_NAME = "plan-postponed-position-voids"
PLAN_JOB_LABEL = "Governed Postponement VOID Corrections: dry run (read-only plan of the registry)"
AUDIT_ACTION = "position.settlement_adjusted.postponement_void"
AUDIT_ENTITY = "official_asp_position"

MODE_DRY_RUN = "DRY_RUN"
MODE_APPLY = "APPLY"

READY = "READY"
APPLIED = "APPLIED"
REFUSED = "REFUSED"

POSITION_NOT_FOUND = "POSITION_NOT_FOUND"
NOT_MLB = "NOT_AN_MLB_EXPERIMENT_POSITION"
NO_ORIGINAL_SETTLEMENT = "NO_ORIGINAL_SETTLEMENT"
ALREADY_VOID = "ALREADY_VOID"
ALREADY_ADJUSTED = "ALREADY_ADJUSTED"
EXPECTATION_MISMATCH = "EXPECTATION_MISMATCH"
RULE_NOT_MET = "RULE_NOT_MET"
INVALID_ENTRY = "INVALID_REGISTRY_ENTRY"
WRITE_FAILED = "WRITE_FAILED"

#: Money comparisons are to the cent.
_CENT = 0.005


@dataclass(frozen=True)
class GovernedVoidCorrection:
    """One owner-approved correction, named exactly as the owner reviewed it."""

    position_id: str
    expected_original_settlement_id: str
    expected_previous_result: str
    expected_stake: float
    expected_profit_loss: float
    #: The owner's decision: what was chosen, when, and where it is recorded.
    owner_decision: str
    mode: str = MODE_DRY_RUN
    #: The original Eastern date the owner reviewed (ISO ``YYYY-MM-DD``). It
    #: must match the stored evidence; when that evidence is ambiguous it is
    #: the date the rule is evaluated on. Optional when the evidence is clear.
    expected_original_et_date: str | None = None


#: EMPTY ON PURPOSE. A position is added here only by a reviewed commit after
#: the owner chooses VOID for it (the owner may instead keep the bet as action).
#: For the 2026-10-16 CLE ML position the entry would read:
#:   GovernedVoidCorrection(
#:       position_id="0d721350-290c-411f-bfb7-384fe9496c45",
#:       expected_original_settlement_id="71a26ff2-aee4-460b-b259-759d43494b53",
#:       expected_previous_result="LOSS", expected_stake=2.42, expected_profit_loss=-2.42,
#:       expected_original_et_date="2026-10-16",
#:       owner_decision="<owner, date, reference>", mode="APPLY")
GOVERNED_VOID_CORRECTIONS: tuple[GovernedVoidCorrection, ...] = ()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _refused(entry: GovernedVoidCorrection, code: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"position_id": entry.position_id, "status": REFUSED, "refusal": code, "detail": detail,
            "mode": entry.mode, "applied": False, **extra}


def plan(session: Session, entry: GovernedVoidCorrection, *, now: datetime | None = None) -> dict[str, Any]:
    """What applying ``entry`` would write, or why it must not. Read-only."""

    now = _aware(now or datetime.now(UTC))
    if entry.mode not in (MODE_DRY_RUN, MODE_APPLY):
        return _refused(entry, INVALID_ENTRY, f"mode must be {MODE_DRY_RUN} or {MODE_APPLY}")
    pinned: date | None = None
    if entry.expected_original_et_date is not None:
        try:
            pinned = date.fromisoformat(str(entry.expected_original_et_date))
        except ValueError:
            return _refused(entry, INVALID_ENTRY, "expected_original_et_date is not an ISO date")
    try:
        position_id = UUID(str(entry.position_id))
    except ValueError:
        return _refused(entry, INVALID_ENTRY, "position_id is not a UUID")
    position = session.get(OfficialAspPosition, position_id)
    if position is None:
        return _refused(entry, POSITION_NOT_FOUND, "no official position has this id")
    experiment = (session.get(ShadowExperiment, position.shadow_experiment_id)
                  if position.shadow_experiment_id else None)
    if experiment is None or (experiment.sport or "").upper() != "MLB":
        return _refused(entry, NOT_MLB,
                        "only MLB experiment positions are corrected here; NHL and the other sports "
                        "have their own settlement owners")
    settlement = session.scalar(select(PositionSettlement).where(PositionSettlement.position_id == position.id))
    if settlement is None:
        return _refused(entry, NO_ORIGINAL_SETTLEMENT, "the position has no settlement to correct")
    if settlement.result == PositionResult.VOID or position.status == PositionStatus.VOID:
        return _refused(entry, ALREADY_VOID, "the position is already VOID")
    if session.scalar(select(PositionSettlementAdjustment.id).where(
            PositionSettlementAdjustment.position_id == position.id).limit(1)) is not None:
        return _refused(entry, ALREADY_ADJUSTED, "an adjustment already exists for this position")
    observed = {
        "original_settlement_id": str(settlement.id),
        "previous_result": settlement.result.name,
        "stake": round(float(position.stake), 2),
        "profit_loss": round(float(settlement.profit_loss), 2),
    }
    expected = {
        "original_settlement_id": str(entry.expected_original_settlement_id),
        "previous_result": str(entry.expected_previous_result).upper(),
        "stake": round(float(entry.expected_stake), 2),
        "profit_loss": round(float(entry.expected_profit_loss), 2),
    }
    mismatched = sorted(
        key for key in expected
        if (abs(observed[key] - expected[key]) > _CENT if isinstance(expected[key], float)
            else observed[key] != expected[key])
    )
    if mismatched:
        return _refused(entry, EXPECTATION_MISMATCH,
                        "the settled position no longer matches what the owner reviewed: " + ", ".join(mismatched),
                        observed=observed, expected=expected)
    decision = session.get(ShadowExperimentDecision, position.shadow_decision_id)
    outcome = session.get(MarketOutcome, position.market_outcome_id)
    market = session.get(Market, outcome.market_id) if outcome else None
    game = session.get(Game, market.game_id) if market else None
    if game is None:
        return _refused(entry, RULE_NOT_MET + ":GAME_NOT_FOUND", "the position's game could not be resolved")
    if pinned is not None:
        recorded = occ.original_occurrence(session, game=game, placed_at=position.placed_at, decision=decision)
        if recorded.et_date is not None and recorded.et_date != pinned:
            return _refused(entry, EXPECTATION_MISMATCH,
                            "the stored evidence dates the original occurrence differently than the owner "
                            "reviewed: original_et_date",
                            observed={**observed, "original_et_date": recorded.et_date.isoformat()},
                            expected={**expected, "original_et_date": pinned.isoformat()})
    verdict = occ.occurrence_verdict(session, position=position, decision=decision, game=game, now=now,
                                     pinned_et_date=pinned)
    if verdict.kind != occ.VOID or verdict.postponement_observation_id is None:
        return _refused(entry, f"{RULE_NOT_MET}:{verdict.reason}",
                        f"{occ.RULE_ID} does not void this position", verdict=verdict.as_dict())
    previous = round(float(settlement.profit_loss), 2)
    reason = (
        f"{verdict.void_note()} Governed correction: owner decision {entry.owner_decision}. "
        f"Original {settlement.result.name} settlement {settlement.id} ({previous:+.2f}) retained; "
        "this correction is recorded beside it."
    )
    return {
        "position_id": str(position.id),
        "status": READY,
        "refusal": None,
        "mode": entry.mode,
        "applied": False,
        "game_id": str(game.id),
        "original_settlement_id": str(settlement.id),
        "game_state_observation_id": str(verdict.postponement_observation_id),
        "previous_result": settlement.result.name,
        "corrected_result": PositionResult.VOID.name,
        "previous_profit_loss": previous,
        "corrected_profit_loss": 0.0,
        "profit_loss_delta": round(0.0 - previous, 2),
        "previous_home_score": settlement.game_home_score,
        "previous_away_score": settlement.game_away_score,
        "corrected_home_score": None,
        "corrected_away_score": None,
        "closing_line_value": None,
        "reason": reason,
        "verdict": verdict.as_dict(),
    }


def apply(session: Session, entry: GovernedVoidCorrection, *, now: datetime | None = None,
          actor_user_id: Any = None, job_run_id: Any = None) -> dict[str, Any]:
    """Write the one adjustment + audit event when the entry says APPLY and the plan is READY."""

    now = _aware(now or datetime.now(UTC))
    planned = plan(session, entry, now=now)
    if planned["status"] != READY or entry.mode != MODE_APPLY:
        return planned
    adjustment = PositionSettlementAdjustment(
        position_id=UUID(planned["position_id"]),
        original_settlement_id=UUID(planned["original_settlement_id"]),
        game_state_observation_id=UUID(planned["game_state_observation_id"]),
        previous_result=planned["previous_result"],
        corrected_result=planned["corrected_result"],
        previous_profit_loss=planned["previous_profit_loss"],
        corrected_profit_loss=planned["corrected_profit_loss"],
        profit_loss_delta=planned["profit_loss_delta"],
        previous_home_score=planned["previous_home_score"],
        previous_away_score=planned["previous_away_score"],
        corrected_home_score=None,
        corrected_away_score=None,
        reason=planned["reason"],
        adjusted_at=now,
        closing_line_value=None,
    )
    try:
        with session.begin_nested():
            session.add(adjustment)
            session.flush()
            session.add(AuditEvent(
                actor_user_id=actor_user_id,
                action=AUDIT_ACTION,
                entity_type=AUDIT_ENTITY,
                entity_id=UUID(planned["position_id"]),
                occurred_at=now,
                metadata_json={
                    "rule_id": occ.RULE_ID,
                    "adjustment_id": str(adjustment.id),
                    "job_run_id": str(job_run_id) if job_run_id else None,
                    "entry": asdict(entry),
                    "plan": {key: value for key, value in planned.items() if key != "verdict"},
                    "verdict": planned["verdict"],
                },
            ))
            session.flush()
    except IntegrityError as error:
        # The savepoint is rolled back. Only an adjustment that now exists for
        # this position and observation (a concurrent run won the
        # UNIQUE(position_id, game_state_observation_id) race) is
        # ALREADY_ADJUSTED; any other refusal (e.g. a foreign key) is reported
        # as the write failure it is.
        if session.scalar(select(PositionSettlementAdjustment.id).where(
                PositionSettlementAdjustment.position_id == adjustment.position_id,
                PositionSettlementAdjustment.game_state_observation_id == adjustment.game_state_observation_id,
        ).limit(1)) is not None:
            return _refused(entry, ALREADY_ADJUSTED, "an adjustment was recorded concurrently")
        return _refused(entry, WRITE_FAILED, f"the database refused the adjustment: {str(error.orig)[:300]}")
    return {**planned, "status": APPLIED, "applied": True, "adjustment_id": str(adjustment.id)}


def run(session: Session, *, now: datetime | None = None,
        registry: tuple[GovernedVoidCorrection, ...] | None = None,
        actor_user_id: Any = None, job_run_id: Any = None, dry_run: bool = False) -> dict[str, Any]:
    """Plan (and, for APPLY entries unless ``dry_run``, write) every registry correction.

    Commits only what it applied; a dry run never writes, whatever the modes.
    """

    entries = GOVERNED_VOID_CORRECTIONS if registry is None else registry
    now = _aware(now or datetime.now(UTC))
    if dry_run:
        plans = [plan(session, entry, now=now) for entry in entries]
    else:
        plans = [apply(session, entry, now=now, actor_user_id=actor_user_id, job_run_id=job_run_id)
                 for entry in entries]
    applied = sum(1 for item in plans if item.get("applied"))
    if applied:
        session.commit()
    if not entries:
        note = "No correction is registered; nothing was evaluated or written."
    elif dry_run:
        ready = sum(1 for item in plans if item["status"] == READY)
        note = (f"Dry run: nothing was written. {ready} of {len(entries)} registry entries are READY; "
                f"{JOB_NAME} writes those marked APPLY.")
    else:
        note = f"{applied} correction(s) written; every other entry is a dry run or a refusal."
    return {
        "records": applied,
        "rule_id": occ.RULE_ID,
        "dry_run": dry_run,
        "mode": (MODE_DRY_RUN if dry_run else
                 MODE_APPLY if any(entry.mode == MODE_APPLY for entry in entries) else MODE_DRY_RUN),
        "registry_entries": len(entries),
        "plans": plans,
        "note": note,
    }


__all__ = [
    "AUDIT_ACTION", "GOVERNED_VOID_CORRECTIONS", "GovernedVoidCorrection", "JOB_LABEL", "JOB_NAME",
    "PLAN_JOB_LABEL", "PLAN_JOB_NAME", "apply", "plan", "run",
]

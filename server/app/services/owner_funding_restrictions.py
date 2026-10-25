"""Owner-approved funding restriction; prospective and funding-only.

No probability adjustment, alternate selection, provider call, enrollment or
history rewrite. Activation requires an explicit actual-release UTC clock.
"""
from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction
from typing import Any

RULE = "AISP_OWNER_SIGNED_SAMEBOOK_GAP_GT_10_RECORD_ONLY_V1"
REASON = "OWNER_GAP_GT_10_RECORD_ONLY"
UNKNOWN = "OWNER_GAP_BASIS_UNVERIFIED"
BASIS = "SELECTED_RAW_MODEL_MINUS_SAMEBOOK_PROPORTIONAL_NO_VIG"
THRESHOLD_PP = Decimal("10")
SPORTS = frozenset({"NFL", "CFB", "NHL", "NBA", "MLB"})

def _clock(value):
    if value in (None, ""):
        return None
    result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("activation clock must include a timezone")
    return result.astimezone(timezone.utc)

def runtime_state(settings: Any, *, now: datetime | None = None) -> dict:
    value = getattr(settings, "owner_large_gap_record_only_effective_from", None)
    clock = _clock(value)
    observed = now or datetime.now(timezone.utc)
    active = clock is not None and observed.astimezone(timezone.utc) >= clock
    from app.services.nfl_qb_out_restriction import runtime_state as qb_runtime_state
    qb_state = qb_runtime_state(settings, now=observed)
    return {"observed_at_utc": observed.isoformat(), "active_at_observation": active, "rule": RULE, "effective_from_utc": clock.isoformat() if clock else None,
            "basis": BASIS, "signed_gap": True, "operator": ">", "threshold_percentage_points": 10,
            "exact_10_retained": True, "nfl_probability": "WIN_DIVIDED_BY_WIN_PLUS_LOSS",
            "mlb_v18_funding_pause_enabled": bool(getattr(settings, "mlb_v18_funding_pause_enabled", False)),
            "mlb_2026_remaining_postseason_record_only": active,
            "nba_paper_funding_record_only_effective_from_utc": (
                _clock(getattr(settings, "nba_paper_funding_record_only_effective_from", None)).isoformat()
                if _clock(getattr(settings, "nba_paper_funding_record_only_effective_from", None)) else None),
            "nba_paper_funding_record_only_active_at_observation": nba_record_only_active(moment=observed, settings=settings),
            "qb_out_gate_implemented_by_this_module": qb_state["implemented"], "nfl_qb_out_gate": qb_state}

def _number(value):
    if value is None or isinstance(value, bool):
        raise ValueError("missing/non-numeric probability")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("non-finite value")
    return Fraction(result)

def _implied(american):
    value = _number(american)
    if abs(value) < 100:
        raise ValueError("invalid American price")
    return Fraction(100)/(value+100) if value > 0 else -value/(-value+100)

NBA_RULE = "AISP_OWNER_NBA_PAPER_FUNDING_RECORD_ONLY_V1"
NBA_REASON = "OWNER_NBA_PAPER_FUNDING_RECORD_ONLY"
NBA_SETTING = "nba_paper_funding_record_only_effective_from"

def nba_record_only_active(*, moment, settings) -> bool:
    """Is the owner's NBA paper-funding record-only clock in force at ``moment``? Default OFF (unset)."""
    clock = _clock(getattr(settings, NBA_SETTING, None))
    return clock is not None and moment.astimezone(timezone.utc) >= clock

def _nba_record_only(decision: dict, moment: datetime, settings: Any) -> dict:
    """Owner NBA record-only (hotfix 2026-10-24 step 7): same shape as the gap receipt, no gap arithmetic."""
    selection = decision.get("selection") or decision.get("best") or {}
    receipt = {"rule": NBA_RULE, "effective_from_utc": _clock(getattr(settings, NBA_SETTING, None)).isoformat(),
               "sport": "NBA", "record_only": True, "reason": NBA_REASON,
               "scope": "ALL_NBA_PAPER_FUNDING_LANES_WHILE_THE_CLOCK_IS_SET",
               "original_qualification": {"outcome": decision.get("outcome"), "reason": decision.get("reason")},
               "sportsbook": selection.get("sportsbook"), "source": selection.get("source"),
               "snapshot_id": str(selection.get("odds_snapshot_id") or ""),
               "funding_checked_at_utc": moment.astimezone(timezone.utc).isoformat()}
    return {**decision, "funding_restriction": receipt, "outcome": "RECORD_ONLY", "reason": NBA_REASON,
            "detail": "Owner-approved funding restriction: retained decision and prices; no position. " + NBA_REASON}

def apply_to_decision(decision: dict, sport: str, moment: datetime, settings: Any) -> dict:
    """Keep value/quote evidence; prevent a new position only after activation.

    Prior persisted decisions are dealt with by each original record function.
    Nonqualifying/budget/prerequisite outcomes remain byte-for-byte inputs.
    """
    if sport == "NBA" and decision.get("outcome") == "QUALIFIED" and nba_record_only_active(moment=moment, settings=settings):
        # Independent of the gap clock: the owner may switch NBA to record-only on its own.
        return _nba_record_only(decision, moment, settings)
    clock = _clock(getattr(settings, "owner_large_gap_record_only_effective_from", None))
    if clock is None or moment.astimezone(timezone.utc) < clock:
        return decision
    if sport not in SPORTS or decision.get("outcome") != "QUALIFIED":
        return decision
    selection = decision.get("selection") or decision.get("best") or {}
    receipt = {"rule": RULE, "effective_from_utc": clock.isoformat(), "basis": BASIS,
               "sport": sport, "threshold_percentage_points": 10, "operator": ">",
               "original_qualification": {"outcome": decision.get("outcome"),
                                          "reason": decision.get("reason")},
               "sportsbook": selection.get("sportsbook"), "source": selection.get("source"),
               "snapshot_id": str(selection.get("odds_snapshot_id") or ""),
               "consensus_used_for_funding": False}
    try:
        with localcontext() as context:
            context.prec = 50
            p = _number(selection.get("win_probability"))
            if not Decimal(0) <= p <= Decimal(1):
                raise ValueError("model probability outside [0,1]")
            if sport == "NFL":
                loss = _number(selection.get("loss_probability"))
                tie = _number(selection.get("tie_probability"))
                if loss < 0 or tie < 0 or p+loss <= 0 or abs(p+loss+tie-1) > Decimal("0.000000001"):
                    raise ValueError("invalid NFL win/loss/tie mass")
                p = p/(p+loss)
                implied = _implied(selection.get("american_odds"))
                opposite = _implied(selection.get("opposite_american"))
                q = implied/(implied+opposite)
                channel = "CONDITIONAL_NONPUSH"
            else:
                if sport == "MLB":
                    # Existing canonical MLB decisions retain the verified same-book q.
                    q = _number((selection.get("terms") or {}).get("no_vig_probability"))
                else:
                    implied = _implied(selection.get("american_odds"))
                    opposite = _implied(selection.get("opposite_american"))
                    q = implied/(implied+opposite)
                channel = "RAW_FINAL_WIN"
            if not Decimal(0) < q < Decimal(1):
                raise ValueError("same-book no-vig probability outside (0,1)")
            gap = 100*(p-q)
            blocked = gap > THRESHOLD_PP
            receipt.update({"model_probability_for_gap": float(p), "samebook_no_vig_probability": float(q),
                            "probability_channel": channel, "gap_percentage_points": float(gap), "gap_percentage_points_exact_fraction": str(gap),
                            "record_only": blocked, "reason": REASON if blocked else "GAP_NOT_ABOVE_10"})
    except (ValueError, TypeError, InvalidOperation, ZeroDivisionError) as error:
        blocked = True
        receipt.update({"record_only": True, "reason": UNKNOWN, "missing_basis_detail": str(error)})
    result = {**decision, "funding_restriction": receipt}
    if blocked:
        result.update({"outcome": "RECORD_ONLY", "reason": receipt["reason"],
                       "detail": "Owner-approved funding restriction: retained decision and prices; no position. "
                                 + receipt["reason"]})
    return result

def mlb_record_only_active(starts_at, *, moment, settings) -> bool:
    """Is the Oct19 owner MLB-2026 record-only scope in force for a game starting ``starts_at`` at ``moment``?

    The ONE predicate behind ``mlb_position_restriction`` and the shared-allowance
    priority hold (``shared_priority_reservation._mlb_pending``): a game that can
    only ever be recorded holds no room (hotfix 2026-10-24, review MEDIUM-3).
    """
    clock = _clock(getattr(settings, "owner_large_gap_record_only_effective_from", None))
    if clock is None or moment.astimezone(timezone.utc) < clock:
        return False
    if starts_at.tzinfo is None:
        starts_at = starts_at.replace(tzinfo=timezone.utc)
    return starts_at.astimezone(timezone.utc).year == 2026

def mlb_position_restriction(*, starts_at, decision, moment, settings):
    """All current MLB 2026 remaining-postseason funding lanes, no era fallback.

    The Oct19 owner scope covers the remaining 2026 MLB schedule; it is not
    inferred from outcomes and does not automatically pause the 2027 season.
    Existing occurrence/clock/snapshot/duplicate guards run before this helper.
    Old decisions and existing positions are never rewritten.
    """
    clock = _clock(getattr(settings, "owner_large_gap_record_only_effective_from", None))
    if clock is None or moment.astimezone(timezone.utc) < clock:
        return None
    if mlb_record_only_active(starts_at, moment=moment, settings=settings):
        return {"rule": "AISP_OWNER_MLB_2026_REMAINING_POSTSEASON_RECORD_ONLY_V1",
                "reason": "OWNER_MLB_2026_POSTSEASON_RECORD_ONLY", "record_only": True,
                "effective_from_utc": clock.isoformat(),
                "scope": "ALL_REMAINING_2026_MLB_FUNDING_LANES_AFTER_OCT19_OWNER_DECISION",
                "original_decision_id": str(decision.id),
                "original_decision_clock": decision.recommended_at.isoformat(),
                "funding_checked_at_utc": moment.isoformat()}
    probe = {"outcome": "QUALIFIED", "reason": "ORIGINAL_QUALIFIED_BET",
             "selection": {"win_probability": decision.model_probability,
                           "sportsbook": decision.sportsbook,
                           "odds_snapshot_id": decision.odds_snapshot_id,
                           "terms": {"no_vig_probability": decision.no_vig_probability}}}
    checked = apply_to_decision(probe, "MLB", moment, settings)
    receipt = checked.get("funding_restriction") or {}
    return receipt if receipt.get("record_only") else None

def funding_receipt_views(session, decision_ids):
    """Read only the existing immutable funding receipts for displayed IDs."""
    if not decision_ids:
        return {}
    from uuid import UUID
    from sqlalchemy import select
    from app.models import AuditEvent
    ids = [UUID(str(value)) for value in decision_ids]
    rows = session.execute(select(AuditEvent.entity_id, AuditEvent.metadata_json).where(
        AuditEvent.action == "MLB_FINAL_PREGAME_FUNDING_V1", AuditEvent.entity_id.in_(ids)
    ).order_by(AuditEvent.occurred_at))
    result = {}
    for decision_id, metadata in rows:
        value = ((metadata or {}).get("limits") or {}).get("owner_funding_restriction")
        if isinstance(value, dict) and value.get("record_only"):
            result[str(decision_id)] = value
    return result

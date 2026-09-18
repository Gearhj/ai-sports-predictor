"""The CFB SportsGameOdds featured-slate lane: capacity first, and today it is zero.

THE AUTHORIZATION
-----------------
At most ten SGO CFB event objects in total for the 2026-09-18 assignment,
including repeats and charged failures, and only from capacity that is
GENUINELY uncommitted: after the reserve, after every remaining MLB/NFL
projected need this billing month, after in-flight reservations, under BOTH
budget guards. The smaller of ten and that capacity. Never a paid upgrade.

WHAT THIS MODULE DOES
---------------------
Computes that capacity read-only, from the same guards and the same
demand-shaped projection the allocator uses (the projection is built inside a
SAVEPOINT that is always rolled back, so no allocation snapshot is written). It
reports a state every CFB price surface can show verbatim.

WHAT IT DOES NOT DO
-------------------
Spend. ``app.cfb.sgo_price.collect`` is the only spender, and it asks this gate
first. After budget-guard phase 2 (2026-09-18) both guards hold the owner's
September 300 and had 216 objects against a projected 212 MLB/NFL need; the
owner directed that the remaining four stay with MLB/NFL, which the protected
headroom (``CFB_SGO_PROTECTED_HEADROOM``, 10) guarantees -- so the lane grants
nothing in September. The October plan (a CFB demand lane in the shaped
allocator) is a separate owner decision.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

LANE = "sgo_cfb_events"
NO_CAPACITY = "NO_UNCOMMITTED_SGO_CAPACITY"
LANE_DISABLED = "LANE_DISABLED"
AUTHORIZATION_EXPIRED = "AUTHORIZATION_EXPIRED"
CAP_REACHED = "ASSIGNMENT_CAP_REACHED"
AVAILABLE = "CAPACITY_AVAILABLE"


def _lane_spent(session: Session) -> int:
    from app.models import ProviderRequestLedger

    return int(session.scalar(select(func.coalesce(func.sum(ProviderRequestLedger.records_received), 0))
                              .where(ProviderRequestLedger.endpoint_key == LANE)) or 0)


def capacity(session: Session, settings: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Genuinely uncommitted SGO capacity for CFB, under both guards. Writes nothing."""

    from app.services import provider_allocation as pa
    from app.services.sgo_collection import object_budget

    moment = now or datetime.now(UTC)
    guard_b = object_budget(session, now=moment)
    plan = pa.build_plan(session, now=moment)
    plan_dict = plan.as_dict() if hasattr(plan, "as_dict") else dict(plan.__dict__)
    lanes = plan_dict.get("lanes") or {}
    today_room = sum(int((lanes.get(key) or {}).get("room") or 0)
                     for key in ("sgo_mlb_events", "sgo_nfl_events"))
    future_need = None
    nested = session.begin_nested()
    try:
        tomorrow = pa.daily_snapshot(session, now=moment + timedelta(days=1))
        shaping = (tomorrow.demand or {}).get("shaping") or {}
        future_need = int(shaping.get("month_projected_need") or 0)
    except Exception:  # noqa: BLE001 -- unknown need means no capacity, never optimism
        future_need = None
    finally:
        nested.rollback()
    guard_a_spendable = int(plan_dict.get("spendable") or 0)
    guard_b_spendable = int(guard_b.spendable or 0) if not guard_b.blocked_reason else 0
    binding = min(guard_a_spendable, guard_b_spendable)
    committed = None if future_need is None else today_room + future_need
    protected = int(getattr(settings, "cfb_sgo_protected_headroom", 10) or 0)
    uncommitted = 0 if committed is None else max(0, binding - committed - protected)
    cap = int(getattr(settings, "cfb_sgo_lane_object_cap", 10) or 0)
    spent = _lane_spent(session)
    until = datetime.fromisoformat(str(getattr(settings, "cfb_sgo_lane_authorized_until",
                                               "2026-10-01T00:00:00+00:00")))
    allowed = max(0, min(cap - spent, uncommitted))
    if moment >= until:
        state = AUTHORIZATION_EXPIRED
    elif spent >= cap:
        state = CAP_REACHED
    elif uncommitted <= 0:
        state = NO_CAPACITY
    elif not getattr(settings, "cfb_sgo_lane_enabled", False):
        state = LANE_DISABLED
    else:
        state = AVAILABLE
    return {
        "lane": LANE, "state": state, "checked_at": moment.isoformat(),
        "guard_a": {"spendable": guard_a_spendable, "reserve": plan_dict.get("reserve_never_spent"),
                    "used": plan_dict.get("used_this_month"), "cap": plan_dict.get("monthly_cap")},
        "guard_b": {"spendable": guard_b_spendable, "reserve": guard_b.reserve, "used": guard_b.objects_used,
                    "blocked_reason": guard_b.blocked_reason},
        "binding_guard": "B" if guard_b_spendable <= guard_a_spendable else "A",
        "mlb_nfl_committed": {"today_remaining_room": today_room, "future_projected_need": future_need,
                              "protected_headroom": protected},
        "uncommitted": uncommitted, "assignment_cap": cap, "spent_on_lane": spent,
        "objects_allowed": allowed if state == AVAILABLE else 0,
        "authorized_until": until.isoformat(),
        "explanation": (
            f"Guard B has {guard_b_spendable} objects left above its reserve and guard A {guard_a_spendable}; "
            f"MLB/NFL still need about {committed if committed is not None else 'an unknown number of'} objects "
            f"this month and {protected} more are kept as their margin, so {uncommitted} are genuinely "
            f"uncommitted. CFB takes the smaller of {cap - spent} "
            f"and that: {allowed if state == AVAILABLE else 0}."),
    }


__all__ = ["LANE", "NO_CAPACITY", "capacity"]

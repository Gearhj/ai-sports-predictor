"""Separate credit lanes, so optional collection can never starve MLB decisions.

THE PROBLEM A SINGLE BUDGET CANNOT SOLVE
----------------------------------------
One pooled budget is spent by whoever asks first. In September that was a
10-minute blanket poll asking for three markets: by the time an MLB game needed
its decision-critical moneyline, the credits were gone. A single remaining
balance cannot express "these 100 credits are not yours".

THE LANES
---------
Five, in strict precedence. A lane may spend only from its own allocation, and
an optional lane must additionally leave every protected lane whole:

    1 MLB_REQUIRED    same-book two-sided moneylines for games that can be
                      priced by the governing policy. Nothing outranks this.
    2 MLB_RECOVERY    the protected reserve. Only a game already diagnosed
                      RECOVERY_REQUIRED may draw on it, and only inside its own
                      recovery ladder. Optional lanes shut down before it is
                      touched -- that is what "protected" means here.
    3 NFL_RESEARCH    research moneylines. No wagering authority.
    4 OPTIONAL_MARKETS spreads and totals, for either sport.
    5 OTHER_RESEARCH  anything else.

ARITHMETIC THAT ACTUALLY BINDS
------------------------------
``LaneBudget.may_spend`` refuses on the first failing condition:

  * the lane's own allocation must cover the request;
  * the day's cap for that lane must cover it;
  * for a NON-protected lane, the credits left after the spend must still cover
    every protected lane's UNSPENT allocation. This is the rule that makes the
    reserve real rather than a number in a report;
  * the provider floor, unchanged at its configured value, has the final word.

RECONCILIATION
--------------
The provider's ``x-requests-used`` header is the authority on what has been
consumed. When it disagrees with our lane accounting we fail closed for optional
lanes and keep required MLB collection running, because the alternative --
trusting our own lower number -- is how a plan overspends. Both directions are
handled: if the provider says LESS remaining than we believe, the difference is
charged to the unattributed lane immediately; if it says MORE, we keep our own
pessimistic figure and merely record the discrepancy.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ProviderRequestLedger
from app.services import odds_budget

LANE_MLB_REQUIRED = "MLB_REQUIRED"
LANE_MLB_RECOVERY = "MLB_RECOVERY"
LANE_NFL_RESEARCH = "NFL_RESEARCH"
LANE_OPTIONAL_MARKETS = "OPTIONAL_MARKETS"
LANE_OTHER_RESEARCH = "OTHER_RESEARCH"
#: Spend the provider counted that we could not attribute to a lane.
LANE_UNATTRIBUTED = "UNATTRIBUTED"


@dataclass(frozen=True)
class LaneSpec:
    """One lane's identity, allocation and precedence."""

    key: str
    label: str
    #: Lower is served first when credits are scarce.
    precedence: int
    monthly_allocation: int
    daily_cap: int
    #: A protected lane's unspent allocation is subtracted from what any
    #: non-protected lane may spend.
    protected: bool
    purpose: str


#: The allocation the brief specifies, expressed as data so the operator view
#: and the guard read the same numbers. Total planned 400 of 500; the 100
#: unallocated credits are headroom above every lane.
DEFAULT_LANES: tuple[LaneSpec, ...] = (
    LaneSpec(
        key=LANE_MLB_REQUIRED, label="MLB required moneylines", precedence=1,
        monthly_allocation=150, daily_cap=8, protected=True,
        purpose=(
            "Complete same-book two-sided moneylines for MLB games the governing policy "
            "can price. Every other lane yields to this one."
        ),
    ),
    LaneSpec(
        key=LANE_MLB_RECOVERY, label="MLB recovery reserve", precedence=2,
        monthly_allocation=100, daily_cap=6, protected=True,
        purpose=(
            "Held back for games diagnosed RECOVERY_REQUIRED and for schedule or provider "
            "failures. Optional collection shuts down before this is touched."
        ),
    ),
    LaneSpec(
        key=LANE_NFL_RESEARCH, label="NFL research moneylines", precedence=3,
        monthly_allocation=60, daily_cap=4, protected=False,
        purpose="Research-shadow NFL moneylines. No wagering authority.",
    ),
    LaneSpec(
        key=LANE_OPTIONAL_MARKETS, label="Optional spreads and totals", precedence=4,
        monthly_allocation=90, daily_cap=6, protected=False,
        purpose=(
            "Spreads and totals for either sport. The governing policy supports MONEYLINE "
            "only, so nothing here can change a decision."
        ),
    ),
    LaneSpec(
        key=LANE_OTHER_RESEARCH, label="Other research markets", precedence=5,
        monthly_allocation=0, daily_cap=0, protected=False,
        purpose="Unallocated by default. Anything else must be given an allocation first.",
    ),
)

LANES: dict[str, LaneSpec] = {lane.key: lane for lane in DEFAULT_LANES}

#: How a ledger row is attributed to a lane, from what it actually asked for.
#: Recovery is distinguished by an explicit marker the caller sets, because a
#: recovery request is otherwise indistinguishable from a scheduled one.
RECOVERY_MARKER = "recovery"


def lane_for_request(
    *, league_code: str, markets: tuple[str, ...] | list[str], recovery: bool = False,
) -> str:
    """Which lane a request belongs to, from its sport and its market set."""

    markets = tuple(m.strip().lower() for m in markets if m and m.strip())
    league = (league_code or "").upper()
    optional = any(market in ("spreads", "totals") for market in markets)
    if league == "MLB":
        if optional:
            return LANE_OPTIONAL_MARKETS
        return LANE_MLB_RECOVERY if recovery else LANE_MLB_REQUIRED
    if league == "NFL":
        return LANE_OPTIONAL_MARKETS if optional else LANE_NFL_RESEARCH
    return LANE_OTHER_RESEARCH


def lane_of_ledger_row(row: ProviderRequestLedger) -> str:
    """Attribute a persisted request to a lane, from its recorded shape."""

    shape = (row.response_metadata or {}).get("request_shape") or {}
    markets = shape.get("markets") or []
    recovery = bool(shape.get(RECOVERY_MARKER))
    league = "MLB" if "baseball" in (row.endpoint_key or "") else (
        "NFL" if "americanfootball" in (row.endpoint_key or "") else ""
    )
    if not league:
        return LANE_OTHER_RESEARCH
    if not markets:
        # Historical rows predate lane attribution. They were three-market MLB
        # polls, which is the optional lane by today's definition.
        return LANE_OPTIONAL_MARKETS if league == "MLB" else LANE_OTHER_RESEARCH
    return lane_for_request(league_code=league, markets=markets, recovery=recovery)


@dataclass
class LaneState:
    """One lane's position this billing month."""

    spec: LaneSpec
    spent: int = 0
    spent_today: int = 0
    requests: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.spec.monthly_allocation - self.spent)

    @property
    def remaining_today(self) -> int:
        return max(0, self.spec.daily_cap - self.spent_today)

    def as_dict(self) -> dict[str, Any]:
        return {
            "lane": self.spec.key,
            "label": self.spec.label,
            "precedence": self.spec.precedence,
            "protected": self.spec.protected,
            "purpose": self.spec.purpose,
            "monthly_allocation": self.spec.monthly_allocation,
            "spent": self.spent,
            "remaining": self.remaining,
            "daily_cap": self.spec.daily_cap,
            "spent_today": self.spent_today,
            "remaining_today": self.remaining_today,
            "requests": self.requests,
        }


@dataclass
class LaneBudget:
    """Every lane's position, plus the provider reconciliation."""

    lanes: dict[str, LaneState]
    base: odds_budget.BudgetState
    unattributed_spend: int = 0
    reconciliation: dict[str, Any] = field(default_factory=dict)

    @property
    def protected_unspent(self) -> int:
        return sum(
            state.remaining for state in self.lanes.values() if state.spec.protected
        )

    @property
    def total_allocated(self) -> int:
        return sum(state.spec.monthly_allocation for state in self.lanes.values())

    def may_spend(self, lane_key: str, credits: int) -> tuple[bool, str | None]:
        """Fail-closed lane check. The provider floor still has the final word."""

        state = self.lanes.get(lane_key)
        if state is None:
            return False, f"{lane_key} is not a registered credit lane"
        if credits <= 0:
            return False, "A request must declare a positive credit cost"

        if self.base.floor_latched:
            return False, (
                f"Provider balance {self.base.provider_remaining} is at or below the floor "
                f"of {self.base.floor}; no lane may spend"
            )
        if credits > state.remaining:
            return False, (
                f"{state.spec.label} has {state.remaining} of its {state.spec.monthly_allocation} "
                f"monthly credits left; {credits} required"
            )
        if credits > state.remaining_today:
            return False, (
                f"{state.spec.label} has {state.remaining_today} of its {state.spec.daily_cap} "
                f"daily credits left; {credits} required"
            )
        if not state.spec.protected:
            # The rule that makes the reserve real: an optional lane may only
            # spend what is left AFTER every protected lane's unspent allocation.
            available = self.base.budgeted_remaining - self.protected_unspent
            if credits > available:
                return False, (
                    f"{state.spec.label} may not spend: {self.protected_unspent} credits are "
                    f"reserved for protected lanes and only {max(0, available)} are free"
                )
        elif credits > self.base.budgeted_remaining:
            return False, (
                f"Monthly budget exhausted: {self.base.spent}/{self.base.monthly_cap} used, "
                f"reserve {self.base.reserve} not spendable"
            )
        if self.reconciliation.get("optional_locked") and not state.spec.protected:
            return False, (
                "Provider balance disagrees with lane accounting; optional lanes are locked "
                f"until it is resolved ({self.reconciliation.get('detail')})"
            )
        return True, None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "AISP_ODDS_BUDGET_LANES_V1",
            "lanes": [
                state.as_dict() for state in sorted(
                    self.lanes.values(), key=lambda s: s.spec.precedence,
                )
            ],
            "total_allocated": self.total_allocated,
            "protected_unspent": self.protected_unspent,
            "unattributed_spend": self.unattributed_spend,
            "reconciliation": self.reconciliation,
            "base_budget": self.base.as_dict(),
        }


def _reconcile(
    *, provider_used: int | None, lane_total: int, unattributed: int,
) -> dict[str, Any]:
    """Compare the provider's own counter with our lane accounting.

    A disagreement is not resolved in our favour. If the provider has counted
    MORE than we attributed, the difference is real spend we cannot explain and
    optional lanes are locked until it is. If it has counted LESS, we keep our
    own higher figure and simply record it: believing the smaller number is how
    a budget is overrun.
    """

    if provider_used is None:
        return {
            "state": "PROVIDER_BALANCE_UNKNOWN",
            "optional_locked": True,
            "detail": (
                "No provider reading inside this billing window, so consumed credits cannot "
                "be confirmed. Optional lanes stay closed; required MLB collection continues "
                "under lane accounting alone."
            ),
            "provider_used": None,
            "lane_total": lane_total,
        }
    delta = provider_used - (lane_total + unattributed)
    if delta > 0:
        return {
            "state": "PROVIDER_COUNTED_MORE",
            "optional_locked": True,
            "detail": (
                f"The provider has counted {provider_used} credits this month; lanes account "
                f"for {lane_total + unattributed}. {delta} credits are unexplained, so "
                "optional lanes are locked and the difference is charged as unattributed."
            ),
            "provider_used": provider_used,
            "lane_total": lane_total,
            "delta": delta,
        }
    if delta < 0:
        return {
            "state": "PROVIDER_COUNTED_LESS",
            "optional_locked": False,
            "detail": (
                f"Lanes account for {lane_total + unattributed} credits; the provider has "
                f"counted {provider_used}. The higher figure is kept: a request we reserved "
                "may not yet be reflected in the provider's counter."
            ),
            "provider_used": provider_used,
            "lane_total": lane_total,
            "delta": delta,
        }
    return {
        "state": "RECONCILED",
        "optional_locked": False,
        "detail": "Lane accounting agrees with the provider's own counter.",
        "provider_used": provider_used,
        "lane_total": lane_total,
        "delta": 0,
    }


def lane_budget(
    session: Session,
    *,
    monthly_cap: int,
    reserve: int,
    daily_cap: int,
    floor: int,
    now: datetime | None = None,
    display_timezone: str = "America/New_York",
    lanes: tuple[LaneSpec, ...] = DEFAULT_LANES,
) -> LaneBudget:
    """Compute every lane's position from the provider request ledger."""

    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    base = odds_budget.budget_state(
        session, monthly_cap=monthly_cap, reserve=reserve, daily_cap=daily_cap,
        floor=floor, now=now, display_timezone=display_timezone,
    )
    window_start, window_end = odds_budget.billing_window(now)

    from app.core.time import local_day_bounds, local_today

    day_start, day_end = local_day_bounds(local_today(display_timezone, now), display_timezone)

    rows = list(session.scalars(
        select(ProviderRequestLedger).where(
            ProviderRequestLedger.provider == odds_budget.PROVIDER,
            ProviderRequestLedger.requested_at >= window_start,
            ProviderRequestLedger.requested_at < window_end,
        ).order_by(ProviderRequestLedger.requested_at)
    ))
    grouped: dict[Any, list[ProviderRequestLedger]] = defaultdict(list)
    for row in rows:
        grouped[row.reservation_id or row.id].append(row)

    states = {lane.key: LaneState(spec=lane) for lane in lanes}
    unattributed = 0
    for group in grouped.values():
        completed = [r for r in group if r.status in ("SUCCEEDED", "FAILED")]
        chosen = completed[-1] if completed else group[-1]
        if chosen.endpoint_key in odds_budget.ZERO_COST_ENDPOINTS:
            continue
        cost = odds_budget._policy_cost(chosen)
        key = lane_of_ledger_row(chosen)
        state = states.get(key)
        if state is None:
            unattributed += cost
            continue
        state.spent += cost
        state.requests += 1
        anchor = group[0].requested_at
        if anchor is not None:
            anchor = anchor if anchor.tzinfo else anchor.replace(tzinfo=UTC)
            if day_start <= anchor < day_end:
                state.spent_today += cost

    lane_total = sum(state.spent for state in states.values())
    return LaneBudget(
        lanes=states, base=base, unattributed_spend=unattributed,
        reconciliation=_reconcile(
            provider_used=base.provider_used, lane_total=lane_total, unattributed=unattributed,
        ),
    )


def projected_month(
    *, mlb_required: int, mlb_recovery: int, nfl_research: int, optional_markets: int,
) -> dict[str, Any]:
    """Check a projected allocation against the lane caps and the 500-credit plan."""

    planned = {
        LANE_MLB_REQUIRED: mlb_required,
        LANE_MLB_RECOVERY: mlb_recovery,
        LANE_NFL_RESEARCH: nfl_research,
        LANE_OPTIONAL_MARKETS: optional_markets,
    }
    breaches = [
        {
            "lane": key,
            "planned": value,
            "allocation": LANES[key].monthly_allocation,
        }
        for key, value in planned.items()
        if value > LANES[key].monthly_allocation
    ]
    total = sum(planned.values())
    return {
        "planned": planned,
        "total_planned": total,
        "total_allocated": sum(lane.monthly_allocation for lane in DEFAULT_LANES),
        "plan_ceiling": 500,
        "fits_in_allocation": not breaches and total <= 400,
        "fits_in_plan": total <= 500,
        "protected_reserve": LANES[LANE_MLB_RECOVERY].monthly_allocation,
        "lane_breaches": breaches,
    }


__all__ = [
    "DEFAULT_LANES",
    "LANES",
    "LANE_MLB_RECOVERY",
    "LANE_MLB_REQUIRED",
    "LANE_NFL_RESEARCH",
    "LANE_OPTIONAL_MARKETS",
    "LANE_OTHER_RESEARCH",
    "LANE_UNATTRIBUTED",
    "RECOVERY_MARKER",
    "LaneBudget",
    "LaneSpec",
    "LaneState",
    "lane_budget",
    "lane_for_request",
    "lane_of_ledger_row",
    "projected_month",
]

"""Separate credit lanes, so optional collection can never starve MLB decisions.

THE PROBLEM A SINGLE BUDGET CANNOT SOLVE
----------------------------------------
One pooled budget is spent by whoever asks first. In September that was a
10-minute blanket poll asking for three markets: by the time an MLB game needed
its decision-critical moneyline, the credits were gone. A single remaining
balance cannot express "these 100 credits are not yours".

THE LANES
---------
In strict precedence. A lane may spend only from its own allocation, and
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
    6 CFB_RESEARCH    CFB paper-policy moneylines (americanfootball_ncaaf).
                      Zero unless the deployer funds it (ODDS_CFB_LANE_*); it is
                      never NFL's lane, although both sport keys start with
                      ``americanfootball``.
    8 NHL_RESEARCH    NHL paper-policy moneylines (icehockey_nhl). Zero unless
                      the deployer funds it (ODDS_NHL_LANE_*). Every NHL request
                      is filed here (spreads and totals included), so nothing
                      NHL can draw on another sport's or a shared funded lane.
                      It YIELDS to NFL_RESEARCH, and to CFB_RESEARCH only while
                      ODDS_NHL_YIELDS_TO_CFB is on (off by default since the
                      owner's 2026-10-16 priority "MLB and NHL over CFB"; see
                      "A LANE THAT YIELDS"). (Precedence 7 is left to
                      NBA_RESEARCH, built on claude/nba-odds-api-lane, so the
                      two never tie.)

ARITHMETIC THAT ACTUALLY BINDS
------------------------------
``LaneBudget.may_spend`` refuses on the first failing condition:

  * the lane's own allocation must cover the request;
  * the day's cap for that lane must cover it;
  * for a NON-protected lane, the credits left after the spend must still cover
    every protected lane's UNSPENT allocation. This is the rule that makes the
    reserve real rather than a number in a report;
  * for a YIELDING lane, also what the lanes it yields to are owed (below);
  * the provider floor, unchanged at its configured value, has the final word.

A LANE THAT YIELDS (precedence enforced, not only displayed)
-----------------------------------------------------------
Between optional lanes precedence used to be display order only: the shared pool
(what is left after the protected holdback, ~100 credits a month at cap 500 /
reserve 150) and the shared daily guards went to whoever asked first. A lane with
``yields_to`` -- today only NHL_RESEARCH, yielding to NFL_RESEARCH (and to CFB_RESEARCH when the
deployer sets ODDS_NHL_YIELDS_TO_CFB; the compiled default below still lists both) -- must
additionally leave whole:

  * the UNSPENT MONTHLY allocation of every lane it yields to, always;
  * on an operational day when that lane's sport still has a SCHEDULED game to
    start (``lanes_in_play_today``), its UNSPENT DAILY allocation inside the shared
    AISP daily credit budget -- and the orchestrator holds back the same number of
    the shared ODDS_DAILY_REQUEST_BUDGET requests for it (a request costs >= 1 credit).

So a funded NHL lane can spend only what NFL and CFB could not use under their
own allocations; it cannot take the credits or requests those allocations entitle
them to. Every other lane's arithmetic is unchanged (their ``yields_to`` is empty).

RECONCILIATION
--------------
The provider's ``x-requests-used`` header is the authority on what has been
consumed. When it disagrees with our lane accounting we fail closed for optional
lanes and keep required MLB collection running, because the alternative --
trusting our own lower number -- is how a plan overspends. Both directions are
handled: if the provider says LESS remaining than we believe, the difference is
charged to the unattributed lane immediately; if it says MORE, we keep our own
pessimistic figure and merely record the discrepancy.

THE FIRST READING OF A BILLING MONTH
------------------------------------
With no provider reading inside the current UTC billing month there is nothing
to reconcile against, so optional lanes are locked (PROVIDER_BALANCE_UNKNOWN).
That lock used to be released only by a protected MLB request taking the
month's first reading -- and MLB makes none once its season ends in October, so
from 1 November every optional lane (NFL, CFB, NHL) would have stayed dark all
month. While no in-window reading exists AND fewer than
``FIRST_READING_ATTEMPTS`` chargeable requests have been reserved in the window,
an optional lane may therefore send the request that TAKES the reading
(PROVIDER_BALANCE_UNREAD_FIRST_READING). Nothing else is relaxed: the lane's own
monthly and daily allocation, the protected-lane holdback, the AISP monthly cap
and reserve, the daily request budget and the provider floor on the newest
reading all still apply to that request. The provider's counter itself reset at
the window start, so the only spend this cannot see is someone else's in the
first moments of the month -- and the response's reading reconciles it at once.
If those attempts all come back without a reading, the lock returns exactly as
before (a manual zero-credit balance probe still releases it).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
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
LANE_CFB_RESEARCH = "CFB_RESEARCH"
LANE_NHL_RESEARCH = "NHL_RESEARCH"
LANE_NBA_RESEARCH = "NBA_RESEARCH"
#: Spend the provider counted that we could not attribute to a lane.
LANE_UNATTRIBUTED = "UNATTRIBUTED"

#: How many chargeable requests a billing window may hold, with no provider
#: reading yet, while optional lanes may still take that reading (see "THE FIRST
#: READING OF A BILLING MONTH" above): the month's first request, and one retry
#: if it came back without a reading. Zero-cost balance probes do not count.
FIRST_READING_ATTEMPTS = 2
STATE_BALANCE_UNKNOWN = "PROVIDER_BALANCE_UNKNOWN"
STATE_FIRST_READING = "PROVIDER_BALANCE_UNREAD_FIRST_READING"


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
    #: Optional lanes this lane must leave whole (see "A LANE THAT YIELDS"): their
    #: unspent MONTHLY allocation always, and their unspent DAILY allocation of the
    #: shared daily guards on a day their sport is in play. Empty for every lane
    #: but NHL_RESEARCH, so no other lane's arithmetic changes.
    yields_to: tuple[str, ...] = ()


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
        # daily_cap 4 -> 6 approved by the owner (James) on 2026-10-06: Sundays with a
        # London game have five T-26 kickoff clusters (13:30, 17:00, 20:05, 20:25 and SNF
        # 00:20Z, still the same ET day), plus room for one owed retry. Monthly 60 unchanged.
        monthly_allocation=60, daily_cap=6, protected=False,
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
    LaneSpec(
        key=LANE_CFB_RESEARCH, label="CFB paper moneylines", precedence=6,
        monthly_allocation=0, daily_cap=0, protected=False,
        purpose=(
            "CFB paper-policy moneylines (americanfootball_ncaaf, h2h). Zero unless explicitly "
            "funded (ODDS_CFB_LANE_MONTHLY_CREDITS / ODDS_CFB_LANE_DAILY_CREDITS); never NFL's lane."
        ),
    ),
    LaneSpec(
        key=LANE_NHL_RESEARCH, label="NHL paper moneylines", precedence=8,
        monthly_allocation=0, daily_cap=0, protected=False,
        purpose=(
            "NHL paper-policy moneylines (icehockey_nhl, h2h). Zero unless explicitly funded "
            "(ODDS_NHL_LANE_MONTHLY_CREDITS / ODDS_NHL_LANE_DAILY_CREDITS); never another sport's lane. "
            "Yields to NFL and CFB: it leaves their unspent allocations whole."
        ),
        # Review MAJOR 2026-10-16: first come, first served, a funded NHL lane could spend the
        # whole optional pool (and the shared daily request budget) before NFL's T-26 fallback.
        yields_to=(LANE_NFL_RESEARCH, LANE_CFB_RESEARCH),
    ),
    LaneSpec(
            key=LANE_NBA_RESEARCH, label="NBA paper moneylines", precedence=9,
            monthly_allocation=0, daily_cap=0, protected=False,
            yields_to=(LANE_NFL_RESEARCH, LANE_CFB_RESEARCH, LANE_NHL_RESEARCH),
            purpose=(
                "NBA paper-policy moneylines (basketball_nba, h2h). Zero unless explicitly funded "
                "(ODDS_NBA_LANE_MONTHLY_CREDITS / ODDS_NBA_LANE_DAILY_CREDITS); never another sport's lane."
            ),
        ),
)

LANES: dict[str, LaneSpec] = {lane.key: lane for lane in DEFAULT_LANES}

#: How a ledger row is attributed to a lane, from what it actually asked for.
#: Recovery is distinguished by an explicit marker the caller sets, because a
#: recovery request is otherwise indistinguishable from a scheduled one.
RECOVERY_MARKER = "recovery"

#: Player-prop market keys never draw on an MLB lane: they buy no decision.
PLAYER_PROP_MARKET_PREFIXES = ("batter_", "pitcher_", "player_")


def lane_for_request(
    *, league_code: str, markets: tuple[str, ...] | list[str], recovery: bool = False,
) -> str:
    """Which lane a request belongs to, from its sport and its market set."""

    markets = tuple(m.strip().lower() for m in markets if m and m.strip())
    league = (league_code or "").upper()
    if any(market.startswith(PLAYER_PROP_MARKET_PREFIXES) for market in markets):
        return LANE_OTHER_RESEARCH
    optional = any(market in ("spreads", "totals") for market in markets)
    if league == "MLB":
        if optional:
            return LANE_OPTIONAL_MARKETS
        return LANE_MLB_RECOVERY if recovery else LANE_MLB_REQUIRED
    if league == "NFL":
        return LANE_OPTIONAL_MARKETS if optional else LANE_NFL_RESEARCH
    if league == "CFB":
        return LANE_OPTIONAL_MARKETS if optional else LANE_CFB_RESEARCH
    if league == "NBA":
        return LANE_NBA_RESEARCH
    if league == "NHL":
        # Spreads and totals included: an NHL request never draws on the shared,
        # funded OPTIONAL_MARKETS lane, so nothing NHL spends until NHL is funded.
        return LANE_NHL_RESEARCH
    return LANE_OTHER_RESEARCH


def league_of_endpoint(endpoint_key: str | None) -> str:
    """The league a THE_ODDS_API ledger endpoint key prices ('' when none).

    NCAAF is checked FIRST: ``americanfootball_ncaaf`` also contains
    ``americanfootball``, and must never be attributed to NFL's lane.
    """

    key = endpoint_key or ""
    if "basketball_nba" in key:
        return "NBA"
    if "icehockey_nhl" in key:
        return "NHL"
    if "ncaaf" in key:
        return "CFB"
    if "baseball" in key:
        return "MLB"
    if "americanfootball" in key:
        return "NFL"
    return ""


def lanes_with_cfb(monthly_credits: int, daily_credits: int,
                   lanes: tuple[LaneSpec, ...] = DEFAULT_LANES) -> tuple[LaneSpec, ...]:
    """``lanes`` with the CFB lane funded from explicit settings (zero by default)."""

    from dataclasses import replace

    return tuple(
        replace(lane, monthly_allocation=max(0, int(monthly_credits or 0)),
                daily_cap=max(0, int(daily_credits or 0)))
        if lane.key == LANE_CFB_RESEARCH else lane
        for lane in lanes
    )


def lanes_with_nhl(monthly_credits: int, daily_credits: int,
                   lanes: tuple[LaneSpec, ...] = DEFAULT_LANES, *,
                   yields_to_cfb: bool = True) -> tuple[LaneSpec, ...]:
    """``lanes`` with the NHL lane funded from explicit settings (zero by default).

    ``yields_to_cfb=False`` drops CFB_RESEARCH from the NHL lane's ``yields_to`` (it still
    yields to NFL_RESEARCH). The orchestrator passes ``ODDS_NHL_YIELDS_TO_CFB``, which is
    False by default since the owner's 2026-10-16 priority "MLB and NHL over CFB"; the
    compiled ``DEFAULT_LANES`` and this function's own default keep the 1aee96d arithmetic.
    """

    from dataclasses import replace

    def funded(lane: LaneSpec) -> LaneSpec:
        yields, purpose = lane.yields_to, lane.purpose
        if not yields_to_cfb:
            yields = tuple(k for k in lane.yields_to if k != LANE_CFB_RESEARCH)
            purpose = purpose.replace(
                "Yields to NFL and CFB: it leaves their unspent allocations whole.",
                "Yields to NFL: it leaves NFL's unspent allocation whole (not CFB's: owner priority "
                "2026-10-16, MLB and NHL over CFB).")
        return replace(lane, monthly_allocation=max(0, int(monthly_credits or 0)),
                       daily_cap=max(0, int(daily_credits or 0)), yields_to=yields, purpose=purpose)

    return tuple(funded(lane) if lane.key == LANE_NHL_RESEARCH else lane for lane in lanes)


def lane_of_ledger_row(
    row: ProviderRequestLedger, *, group: list[ProviderRequestLedger] | None = None,
) -> str:
    """Attribute a persisted request to a lane, from its recorded shape.

    The shape is written on the RESERVED row. Terminal rows now copy it, but
    every row written before that change carries only the response summary, so
    the whole reservation group is searched rather than the terminal row alone.
    Without this, a completed request is indistinguishable from a shapeless one
    and lands in the optional lane by default -- which would quietly report the
    protected lanes as unspent no matter how much they had actually spent.
    """

    shape: dict[str, Any] = {}
    for candidate in (group or [row]):
        found = (candidate.response_metadata or {}).get("request_shape") or {}
        if found:
            shape = found
            break
    if not shape:
        shape = (row.response_metadata or {}).get("request_shape") or {}
    markets = shape.get("markets") or []
    recovery = bool(shape.get(RECOVERY_MARKER))
    league = league_of_endpoint(row.endpoint_key)
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
            "yields_to": list(self.spec.yields_to),
        }


@dataclass
class LaneBudget:
    """Every lane's position, plus the provider reconciliation."""

    lanes: dict[str, LaneState]
    base: odds_budget.BudgetState
    unattributed_spend: int = 0
    reconciliation: dict[str, Any] = field(default_factory=dict)
    #: Lanes whose sport is in play today (``lanes_in_play_today``). ``None`` = not
    #: computed, and then every lane a yielding lane yields to counts as in play
    #: (the conservative reading). Only a yielding lane's arithmetic reads it.
    in_play_today: frozenset[str] | None = None

    @property
    def protected_unspent(self) -> int:
        return sum(
            state.remaining for state in self.lanes.values() if state.spec.protected
        )

    def yield_holdback(self, lane_key: str) -> dict[str, Any]:
        """What ``lane_key`` must leave to the lanes it yields to (zero for every other lane).

        ``monthly``: their unspent monthly allocations, always. ``daily``: their unspent
        daily allocations, for lanes in play today only. Credits are also an upper bound on
        requests (every request costs at least one), so ``daily`` doubles as the number of
        shared daily REQUESTS to leave them.
        """

        state = self.lanes.get(lane_key)
        held: list[dict[str, Any]] = []
        for key in (state.spec.yields_to if state is not None else ()):
            other = self.lanes.get(key)
            if other is None:
                continue
            playing = self.in_play_today is None or key in self.in_play_today
            held.append({"lane": key, "label": other.spec.label, "monthly": other.remaining,
                         "today": other.remaining_today if playing else 0, "in_play_today": playing})
        return {"monthly": sum(h["monthly"] for h in held), "daily": sum(h["today"] for h in held),
                "lanes": held}

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
            # A yielding lane (NHL) also leaves the lanes it yields to (NFL, CFB) whole: their
            # unspent monthly allocation, and on a day their sport plays, their unspent share
            # of the shared AISP daily credit budget. Precedence is enforced, not just displayed.
            hold = self.yield_holdback(lane_key)
            if hold["lanes"]:
                names = ", ".join(h["label"] for h in hold["lanes"])
                left = available - hold["monthly"]
                if credits > left:
                    return False, (
                        f"{state.spec.label} yields to {names}: {hold['monthly']} of their monthly credits "
                        f"stay unspent for them and only {max(0, left)} are free"
                    )
                today = self.base.daily_remaining - hold["daily"]
                if credits > today:
                    return False, (
                        f"{state.spec.label} yields to {names}: {hold['daily']} of today's shared "
                        f"{self.base.daily_cap}-credit daily budget stay for them and only {max(0, today)} are free"
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
            "in_play_today": None if self.in_play_today is None else sorted(self.in_play_today),
            "unattributed_spend": self.unattributed_spend,
            "reconciliation": self.reconciliation,
            "base_budget": self.base.as_dict(),
        }


def _reconcile(
    *, provider_used: int | None, lane_total: int, unattributed: int,
    chargeable_requests: int | None = None,
) -> dict[str, Any]:
    """Compare the provider's own counter with our lane accounting.

    A disagreement is not resolved in our favour. If the provider has counted
    MORE than we attributed, the difference is real spend we cannot explain and
    optional lanes are locked until it is. If it has counted LESS, we keep our
    own higher figure and simply record it: believing the smaller number is how
    a budget is overrun.

    ``chargeable_requests`` is the number of chargeable reservations in this
    billing window. With no reading yet and fewer than
    ``FIRST_READING_ATTEMPTS`` of them, an optional lane may take the month's
    first reading; ``None`` (a caller that does not say) keeps the lock.
    """

    if provider_used is None and chargeable_requests is not None \
            and 0 <= chargeable_requests < FIRST_READING_ATTEMPTS:
        return {
            "state": STATE_FIRST_READING,
            "optional_locked": False,
            "detail": (
                f"No provider reading inside this billing window yet, and {chargeable_requests} of "
                f"{FIRST_READING_ATTEMPTS} first-reading attempts used. One guarded request may take the "
                "month's first reading; every allocation, daily cap, protected holdback, reserve and "
                "the provider floor still apply to it."
            ),
            "provider_used": None,
            "lane_total": lane_total,
            "chargeable_requests": chargeable_requests,
            "first_reading_attempts": FIRST_READING_ATTEMPTS,
        }
    if provider_used is None:
        return {
            "state": STATE_BALANCE_UNKNOWN,
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
    in_play_today: frozenset[str] | None = None,
) -> LaneBudget:
    """Compute every lane's position from the provider request ledger.

    ``in_play_today`` (``lanes_in_play_today``) only matters to a yielding lane; ``None``
    treats every lane it yields to as in play today.
    """

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
        key = lane_of_ledger_row(chosen, group=group)
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
            chargeable_requests=base.requests_this_window,
        ),
        in_play_today=None if in_play_today is None else frozenset(in_play_today),
    )


#: The league whose SCHEDULED games put a yielded-to lane in play.
IN_PLAY_LEAGUES: dict[str, str] = {LANE_NFL_RESEARCH: "NFL", LANE_CFB_RESEARCH: "CFB"}
#: A game starting this long after the operational day ends still counts: its buy window
#: (T-75 at the earliest for any lane) opens before midnight.
IN_PLAY_AFTER_DAY_END = timedelta(hours=2)


def lanes_in_play_today(session: Session, *, now: datetime | None = None,
                        display_timezone: str = "America/New_York") -> frozenset[str]:
    """Yielded-to lanes whose sport has a SCHEDULED game still to start in this operational day.

    The daily part of a yield is held only for these: a sport with nothing left to price
    today needs none of today's shared requests or credits (NFL on a Saturday in October),
    while its MONTHLY holdback always stands.
    """

    from app.core.time import local_day_bounds, local_today
    from app.models import Game, GameStatus, League

    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    _start, day_end = local_day_bounds(local_today(display_timezone, now), display_timezone)
    playing = set()
    for lane, code in IN_PLAY_LEAGUES.items():
        found = session.scalar(
            select(Game.id).join(League, League.id == Game.league_id)
            .where(League.code == code, Game.status == GameStatus.SCHEDULED,
                   Game.starts_at > now, Game.starts_at < day_end + IN_PLAY_AFTER_DAY_END)
            .limit(1))
        if found is not None:
            playing.add(lane)
    return frozenset(playing)


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
    "FIRST_READING_ATTEMPTS",
    "STATE_BALANCE_UNKNOWN",
    "STATE_FIRST_READING",
    "IN_PLAY_AFTER_DAY_END",
    "IN_PLAY_LEAGUES",
    "lanes_in_play_today",
    "LANE_CFB_RESEARCH",
    "LANE_NHL_RESEARCH",
    "league_of_endpoint",
    "lanes_with_cfb",
    "lanes_with_nhl",
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


def lanes_with_nba(monthly_credits: int, daily_credits: int,
                   lanes: tuple[LaneSpec, ...] = DEFAULT_LANES) -> tuple[LaneSpec, ...]:
    """``lanes`` with the NBA lane funded from explicit settings (zero by default)."""

    from dataclasses import replace

    return tuple(
        replace(lane, monthly_allocation=max(0, int(monthly_credits or 0)),
                daily_cap=max(0, int(daily_credits or 0)))
        if lane.key == LANE_NBA_RESEARCH else lane
        for lane in lanes
    )

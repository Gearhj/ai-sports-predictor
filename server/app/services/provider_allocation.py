"""One budget, shared by every consumer, enforced where requests are reserved.

WHY THIS REPLACES THE PER-LANE FLOORS
-------------------------------------
Each consumer used to defend its own share with a floor: the MLB fallback had
the provider reserve, NFL in-game held a floor above it for MLB pregame. Floors
that overlap do not add up to a budget -- they are several consumers each
reasoning about the same pool from a different direction, and the arithmetic
only works if you read them together, which nothing did. The result was an MLB
cadence spending an order of magnitude more per day than the month could fund.

So: one plan, explicit allocations that sum to the daily total, and a single
enforcement point at the reservation boundary. A lane that has spent its day is
refused by name; a lane that has not cannot be starved by one that has.

THE NUMBERS COME FROM THE PROVIDER
----------------------------------
Not from a local count of what AISP believes it spent. The account's own usage
endpoint reports the monthly entity cap and the entities consumed, and the plan
is built from that. A local count cannot know the entitlement, cannot know
whether the window rolls or resets, and cannot see a spend made from anywhere
else -- and planning against a guess at those is how a budget runs out early.

The local ledger is still read, for the one thing the provider's counter cannot
give: what is in flight right now. A reservation written before its request is
visible to a concurrent job, so two jobs cannot both see room for the last
object.

REPLENISHMENT IS A DATE, NOT A ROLLING WINDOW
---------------------------------------------
The provider reports a `per-month` entity cap, and its `per-day` counter tracks
the UTC calendar day exactly. The month is read the same way, so the balance
refills at the start of the next UTC month. The remaining daily rate is
therefore the remaining objects divided by the days left in THIS month -- a
number that rises as a light day passes and falls as a heavy one does, which is
the behaviour a budget with a deadline should have.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ProviderRequestLedger

PROVIDER = "SPORTSGAMEODDS"

#: The ledger key the usage reading is recorded under. It spends no entities --
#: the endpoint returns account data -- so it is a zero-object ledger row that
#: carries the authoritative remaining balance.
USAGE_ENDPOINT_KEY = "sgo_account_usage"

#: Never spent, whatever the plan says. Unchanged from the previous budget.
OBJECT_RESERVE = 400

#: The published amateur-tier monthly entity cap. Used only when the provider's
#: own reading is unavailable; the reading itself is preferred every time.
FALLBACK_MONTHLY_CAP = 2500


@dataclass(frozen=True)
class Lane:
    """One consumer's share of the daily budget, and why it has that share."""

    key: str
    label: str
    share: float
    rationale: str
    #: A lane may be switched off entirely without disturbing the others' shares.
    funded: bool = True


#: The allocation. Shares are of the DAILY total and sum to 1.0.
#:
#: MLB pregame takes the majority because it is the only lane feeding a funded
#: decision policy: a missing price there is a decision AISP cannot make. NFL
#: is a research shadow with no wagering authority, so its two lanes are funded
#: deliberately and modestly rather than by default. Final-period enrichment is
#: last because it buys presentation detail for games that are already over --
#: real, but never at the cost of a price.
LANES: tuple[Lane, ...] = (
    Lane(
        key="sgo_mlb_events", label="MLB pregame quotes", share=0.62,
        rationale=(
            "The only lane feeding a funded decision policy. A price missed here "
            "is a decision that cannot be made."
        ),
    ),
    Lane(
        key="sgo_nfl_events", label="NFL pregame quotes", share=0.08,
        rationale=(
            "Funded on the project owner's explicit 2026-09-14 approval, which "
            "opened the NFL virtual research experiment and moved one object a "
            "day out of NFL live scores. A funded paper policy needs a quote "
            "that is fresh AT ITS CHECKPOINT, and no other lane may buy one for "
            "it: pregame retrieval is charged here, never to the live lane."
        ),
    ),
    Lane(
        key="sgo_nfl_live", label="NFL in-game score", share=0.22,
        rationale=(
            "Explicitly funded, and deliberately small -- one object a day "
            "smaller since 2026-09-14, when the owner moved it to NFL pregame. "
            "Four concurrent games is a ceiling on how many may be followed at "
            "once, not a promise that the day can pay for four complete games."
        ),
    ),
    Lane(
        key="sgo_nfl_periods", label="NFL final period breakdown", share=0.08,
        rationale=(
            "One object per completed game, once. It buys presentation detail "
            "for a game that is already over, so it yields to every other lane."
        ),
    ),
)

LANES_BY_KEY = {lane.key: lane for lane in LANES}

#: Allocation changes, recorded rather than silently applied. A budget that
#: changes shape with no record is one nobody can audit later, and "why does
#: NFL have an object today" needs an answer that outlives this conversation.
ALLOCATION_CHANGES: tuple[dict[str, Any], ...] = (
    {
        "effective_utc": "2026-09-14T16:50:39+00:00",
        "authority": "project owner, explicit approval 2026-09-14",
        "change": "FUND_NFL_PREGAME_FROM_NFL_LIVE",
        "detail": (
            "NFL pregame quotes moved from unfunded to funded, which at the "
            "current 11-object daily total yields MLB pregame 7, NFL pregame 1, "
            "NFL live 2, NFL final periods 1 -- the approved split. The declared "
            "shares are unchanged; only the funded flag moved, so the split is a "
            "consequence of the existing apportionment rather than a hand-set "
            "table that could drift from it."
        ),
        "scope": "virtual research only; no real-money wagering or execution",
        "preserved": [
            "the 400-object reserve",
            "all incurred and in-flight usage",
            "the shared reservation controls",
        ],
    },
)


@dataclass(frozen=True)
class Allocation:
    """One lane's position today."""

    lane: Lane
    daily_allowance: int
    spent_today: int
    in_flight: int

    @property
    def room(self) -> int:
        return max(0, self.daily_allowance - self.spent_today - self.in_flight)

    def as_dict(self) -> dict[str, Any]:
        return {
            "lane": self.lane.key,
            "label": self.lane.label,
            "share": self.lane.share,
            "funded": self.lane.funded,
            "daily_allowance": self.daily_allowance,
            "spent_today": self.spent_today,
            "in_flight": self.in_flight,
            "room": self.room,
            "rationale": self.lane.rationale,
        }


@dataclass(frozen=True)
class Plan:
    """The whole budget, as it stands right now."""

    monthly_cap: int
    used_this_month: int
    reserve: int
    replenishes_on: date
    days_remaining: int
    daily_total: int
    allocations: dict[str, Allocation]
    source: str
    read_at: datetime | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def remaining(self) -> int:
        return max(0, self.monthly_cap - self.used_this_month)

    @property
    def spendable(self) -> int:
        return max(0, self.remaining - self.reserve)

    def as_dict(self) -> dict[str, Any]:
        return {
            "monthly_cap": self.monthly_cap,
            "used_this_month": self.used_this_month,
            "remaining": self.remaining,
            "reserve_never_spent": self.reserve,
            "spendable": self.spendable,
            "replenishes_on": self.replenishes_on.isoformat(),
            "days_remaining": self.days_remaining,
            "daily_total": self.daily_total,
            "balance_source": self.source,
            "balance_read_at": self.read_at.isoformat() if self.read_at else None,
            "lanes": {key: value.as_dict() for key, value in self.allocations.items()},
            "warnings": list(self.warnings),
        }


def replenishment_date(now: datetime | None = None) -> date:
    """The first day of the next UTC month.

    The provider's per-day counter tracks the UTC calendar day exactly -- a
    reading of four requests matched the four made since UTC midnight -- so the
    per-month cap is read the same way.
    """

    moment = now or datetime.now(UTC)
    days_in_month = monthrange(moment.year, moment.month)[1]
    return (moment.date().replace(day=1) + timedelta(days=days_in_month)).replace(day=1)


def apportion(daily_total: int, *, include_unfunded: bool = False) -> dict[str, int]:
    """Whole objects per funded lane, summing exactly to the daily total.

    Largest remainder rather than truncation. At eleven objects a day a lane
    holding eight per cent truncates to nothing, which would quietly defund it
    for the rest of the month; this gives it its turn and keeps the parts adding
    up to the whole. Ties break on the declared lane order, so the same total
    always produces the same split -- a budget that reshuffles between two reads
    is not one anybody can reason about.

    `include_unfunded` answers a reporting question only: what an unfunded lane
    WOULD receive if it were switched on. It exists so a zero allowance can be
    shown as the policy choice it is rather than read as rounding, and it never
    grants anything -- `request_objects` refuses an unfunded lane by name
    whatever this returns.
    """

    funded = [lane for lane in LANES if lane.funded or include_unfunded]
    if not funded or daily_total <= 0:
        return {lane.key: 0 for lane in LANES}

    weight = sum(lane.share for lane in funded)
    exact = {lane.key: daily_total * lane.share / weight for lane in funded}
    out = {lane.key: int(exact[lane.key]) for lane in funded}

    remainder = daily_total - sum(out.values())
    ranked = sorted(
        funded,
        key=lambda lane: (-(exact[lane.key] - int(exact[lane.key])),
                          LANES.index(lane)),
    )
    for lane in ranked[:max(0, remainder)]:
        out[lane.key] += 1
    return {lane.key: out.get(lane.key, 0) for lane in LANES}


def latest_usage_reading(session: Session) -> ProviderRequestLedger | None:
    """The most recent authoritative balance AISP recorded from the provider."""

    return session.scalar(
        select(ProviderRequestLedger)
        .where(
            ProviderRequestLedger.provider == PROVIDER,
            ProviderRequestLedger.endpoint_key == USAGE_ENDPOINT_KEY,
            ProviderRequestLedger.status == "SUCCEEDED",
        )
        .order_by(ProviderRequestLedger.requested_at.desc())
        .limit(1)
    )


def _local_month_spend(session: Session, *, now: datetime) -> int:
    """What AISP's own ledger says it has spent this UTC month."""

    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total = session.scalar(
        select(func.coalesce(func.sum(ProviderRequestLedger.records_received), 0))
        .where(
            ProviderRequestLedger.provider == PROVIDER,
            ProviderRequestLedger.status == "SUCCEEDED",
            ProviderRequestLedger.endpoint_key != USAGE_ENDPOINT_KEY,
            ProviderRequestLedger.requested_at >= month_start,
        )
    )
    return int(total or 0)


def _lane_usage(session: Session, *, now: datetime) -> dict[str, tuple[int, int]]:
    """Per lane: objects settled today, and objects reserved but not yet settled.

    In-flight matters for concurrency. A reservation is written before its
    request goes out and carries the count it asked for, so a job checking the
    budget can see what another job is already committed to spending and the two
    cannot both claim the last object.
    """

    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

    settled = dict(session.execute(
        select(
            ProviderRequestLedger.endpoint_key,
            func.coalesce(func.sum(ProviderRequestLedger.records_received), 0),
        )
        .where(
            ProviderRequestLedger.provider == PROVIDER,
            ProviderRequestLedger.status == "SUCCEEDED",
            ProviderRequestLedger.requested_at >= midnight,
        )
        .group_by(ProviderRequestLedger.endpoint_key)
    ).all())

    # A reservation is in flight until a terminal row points back at it.
    terminal = select(ProviderRequestLedger.reservation_id).where(
        ProviderRequestLedger.reservation_id.is_not(None))
    pending = dict(session.execute(
        select(
            ProviderRequestLedger.endpoint_key,
            func.coalesce(func.sum(ProviderRequestLedger.quota_cost), 0),
        )
        .where(
            ProviderRequestLedger.provider == PROVIDER,
            ProviderRequestLedger.status == "RESERVED",
            ProviderRequestLedger.requested_at >= midnight,
            ProviderRequestLedger.id.not_in(terminal),
        )
        .group_by(ProviderRequestLedger.endpoint_key)
    ).all())

    return {
        lane.key: (int(settled.get(lane.key, 0)), int(pending.get(lane.key, 0)))
        for lane in LANES
    }


def build_plan(session: Session, *, now: datetime | None = None) -> Plan:
    """The current allocation, from the provider's own balance where possible."""

    moment = now or datetime.now(UTC)
    warnings: list[str] = []

    reading = latest_usage_reading(session)
    meta = dict((reading.response_metadata or {}) if reading else {})
    usage = dict(meta.get("usage") or {})
    cap = int(usage.get("monthly_cap") or 0)
    used = int(usage.get("used_this_month") or 0)
    source = "PROVIDER_ACCOUNT_USAGE"
    read_at = reading.requested_at if reading else None

    if not cap:
        cap, used = FALLBACK_MONTHLY_CAP, _local_month_spend(session, now=moment)
        source = "LOCAL_LEDGER_COUNT"
        warnings.append(
            "No provider usage reading is recorded, so the balance is AISP's own "
            "count against the published cap. It cannot see a spend made "
            "elsewhere."
        )
    else:
        # The provider's reading is a point in time; add what AISP has spent
        # since, or a heavy hour would be invisible until the next read.
        since = session.scalar(
            select(func.coalesce(func.sum(ProviderRequestLedger.records_received), 0))
            .where(
                ProviderRequestLedger.provider == PROVIDER,
                ProviderRequestLedger.status == "SUCCEEDED",
                ProviderRequestLedger.endpoint_key != USAGE_ENDPOINT_KEY,
                ProviderRequestLedger.requested_at > reading.requested_at,
            )
        )
        used += int(since or 0)
        if read_at is not None:
            aware = read_at if read_at.tzinfo else read_at.replace(tzinfo=UTC)
            if moment - aware > timedelta(hours=6):
                warnings.append(
                    f"The provider balance was last read {aware.isoformat()}; a "
                    "spend made outside AISP since then is not reflected."
                )

    replenishes = replenishment_date(moment)
    days_remaining = max(1, (replenishes - moment.date()).days)
    spendable = max(0, cap - used - OBJECT_RESERVE)
    daily_total = spendable // days_remaining

    lane_usage = _lane_usage(session, now=moment)
    allowances = apportion(daily_total)
    allocations: dict[str, Allocation] = {}
    for lane in LANES:
        spent, flight = lane_usage.get(lane.key, (0, 0))
        allocations[lane.key] = Allocation(
            lane=lane, daily_allowance=allowances.get(lane.key, 0),
            spent_today=spent, in_flight=flight,
        )

    if daily_total == 0:
        warnings.append(
            f"{spendable} objects are spendable across {days_remaining} days, "
            "so no lane has a whole object a day. Collection is paused until the "
            f"balance replenishes on {replenishes.isoformat()}."
        )

    return Plan(
        monthly_cap=cap, used_this_month=used, reserve=OBJECT_RESERVE,
        replenishes_on=replenishes, days_remaining=days_remaining,
        daily_total=daily_total, allocations=allocations,
        source=source, read_at=read_at, warnings=warnings,
    )


@dataclass(frozen=True)
class Grant:
    """How many objects a lane may spend right now, and why not more."""

    lane: str
    wanted: int
    granted: int
    reason: str
    plan: Plan

    @property
    def allowed(self) -> bool:
        return self.granted > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "lane": self.lane, "wanted": self.wanted, "granted": self.granted,
            "reason": self.reason,
            "allocation": self.plan.allocations[self.lane].as_dict()
            if self.lane in self.plan.allocations else None,
            "budget": {
                "spendable": self.plan.spendable,
                "daily_total": self.plan.daily_total,
                "replenishes_on": self.plan.replenishes_on.isoformat(),
                "days_remaining": self.plan.days_remaining,
                "balance_source": self.plan.source,
            },
        }


def request_objects(
    session: Session, *, lane: str, wanted: int, now: datetime | None = None,
) -> Grant:
    """The single enforcement point. Every consumer asks here before reserving.

    Grants at most what the lane's remaining daily allowance permits, after
    subtracting both what it has already spent today and what it has in flight.
    Never grants into the reserve, because the reserve is subtracted before the
    daily total is computed at all.
    """

    plan = build_plan(session, now=now)
    if lane not in plan.allocations:
        return Grant(lane=lane, wanted=wanted, granted=0, plan=plan,
                     reason=f"UNKNOWN_LANE:{lane}")

    allocation = plan.allocations[lane]
    if not allocation.lane.funded:
        return Grant(lane=lane, wanted=wanted, granted=0, plan=plan,
                     reason="LANE_NOT_FUNDED")
    if plan.daily_total == 0:
        return Grant(lane=lane, wanted=wanted, granted=0, plan=plan,
                     reason="BUDGET_PAUSED_UNTIL_REPLENISHMENT")

    granted = max(0, min(int(wanted), allocation.room))
    if granted == 0:
        return Grant(lane=lane, wanted=wanted, granted=0, plan=plan,
                     reason="LANE_DAILY_ALLOWANCE_SPENT")
    reason = "GRANTED" if granted == wanted else "GRANTED_PARTIAL"
    return Grant(lane=lane, wanted=wanted, granted=granted, plan=plan, reason=reason)


def quote_affordability(
    session: Session, *, lane: str = "sgo_nfl_events", objects: int = 1,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Whether one more capture on a lane fits the authorised allowance.

    Asks the enforcement point the same question a collector would, and reports
    the answer. It reserves nothing, writes nothing and moves no accounting: a
    refusal here is the guard working, not an obstacle to route around.
    """

    grant = request_objects(session, lane=lane, wanted=objects, now=now)
    allocation = grant.plan.allocations.get(lane)
    reasons = {
        "GRANTED": "The lane has room today and the capture fits.",
        "GRANTED_PARTIAL": (
            "The lane has some room but less than requested, so only part of "
            "the capture fits."
        ),
        "LANE_NOT_FUNDED": (
            "The lane is switched off by policy, so it has no allowance to "
            "spend. This is a funding decision, not a depleted balance, and "
            "turning it on changes every other lane's share."
        ),
        "LANE_DAILY_ALLOWANCE_SPENT": (
            "The lane's allowance for today is already spent or reserved. It "
            "replenishes at the next UTC day, not at the next request."
        ),
        "BUDGET_PAUSED_UNTIL_REPLENISHMENT": (
            "No lane has a whole object a day at the current balance, so "
            "collection is paused until the monthly balance replenishes."
        ),
    }
    return {
        "lane": lane,
        "objects_requested": objects,
        "fits": grant.allowed,
        "objects_granted": grant.granted,
        "reason": grant.reason,
        "explanation": reasons.get(grant.reason, grant.reason),
        "allocation": allocation.as_dict() if allocation else None,
        "replenishes_on": grant.plan.replenishes_on.isoformat(),
        "reserved_nothing": True,
    }


def coverage_forecast(
    session: Session, *, now: datetime | None = None,
) -> dict[str, Any]:
    """Achievable coverage through replenishment, from the real schedule.

    Counts the games actually scheduled between now and the replenishment date
    and compares them against what the allocation can pay for. The answer is
    usually "not all of them", and saying which fraction -- and by what rule the
    rest are dropped -- is the difference between a budget and a surprise.
    """

    from app.models import Game, League

    moment = now or datetime.now(UTC)
    plan = build_plan(session, now=moment)
    horizon = datetime.combine(plan.replenishes_on, datetime.min.time(), tzinfo=UTC)

    scheduled = dict(session.execute(
        select(League.code, func.count(Game.id))
        .join(Game, Game.league_id == League.id)
        .where(Game.starts_at >= moment, Game.starts_at < horizon)
        .group_by(League.code)
    ).all())

    mlb_games = int(scheduled.get("MLB", 0))
    nfl_games = int(scheduled.get("NFL", 0))
    mlb_daily = plan.allocations["sgo_mlb_events"].daily_allowance
    live_daily = plan.allocations["sgo_nfl_live"].daily_allowance

    mlb_fundable = mlb_daily * plan.days_remaining

    # AISP registers canonical events only a short horizon ahead, so the count
    # above is what is REGISTERED, not what the league will play. Comparing a
    # seventeen-day budget against a two-day schedule would report three times
    # coverage where the real answer is under a half. The per-day comparison is
    # the one that means something, so it is the one reported as coverage.
    horizon_days = max(1, len({
        row[0] for row in session.execute(
            select(func.date(Game.starts_at))
            .join(League, League.id == Game.league_id)
            .where(League.code == "MLB", Game.starts_at >= moment,
                   Game.starts_at < horizon)
        ).all()
    }))
    mlb_per_day = mlb_games / horizon_days if horizon_days else 0.0

    return {
        "through": plan.replenishes_on.isoformat(),
        "days_remaining": plan.days_remaining,
        "daily_total": plan.daily_total,
        "mlb": {
            "games_registered_ahead": mlb_games,
            "registered_days_ahead": horizon_days,
            "typical_games_per_day": round(mlb_per_day, 1),
            "objects_per_day": mlb_daily,
            "objects_fundable_through_replenishment": mlb_fundable,
            # The unit is an EVENT OBJECT, not a request and not a game. One
            # slate request today returned fourteen event objects and was billed
            # fourteen, so counting requests would have read as one.
            "distinct_games_per_day_if_never_recaptured": mlb_daily,
            "distinct_games_per_day_if_each_game_captured_twice": mlb_daily // 2,
            "daily_coverage_fraction": (
                round(mlb_daily / mlb_per_day, 3) if mlb_per_day else None),
            "checkpoints_per_game": 1,
            "note": (
                f"{mlb_daily} objects a day covers {mlb_daily} DISTINCT games "
                "only while no game is captured more than once. The provider "
                "bills per event object, so a second capture of a game already "
                "captured -- a refresh after a staleness expiry, a re-read of a "
                "slate that includes games already priced -- costs another "
                "object and takes a distinct game off the day one for one. The "
                "coverage fraction below assumes the no-repeat case, which is "
                "the most favourable one and not the observed one."
            ),
            "coverage_fraction_assumes": "NO_REPEAT_CAPTURES",
        },
        "nfl_in_game": {
            "games_scheduled": nfl_games,
            "objects_per_day": live_daily,
            # An in-game check is only worth anything WHILE the game is being
            # played, so the honest unit is objects available on the one day the
            # game runs. The previous figure multiplied the daily allowance by
            # the days left in the month and divided by thirteen, which reported
            # "3.9 complete games fundable" from an allowance that cannot fund
            # thirteen checks in any single game. Objects do not roll forward
            # into a game that has already ended.
            "checks_in_one_game_today": live_daily,
            "objects_for_one_complete_game_at_15_minutes": 13,
            "complete_games_fundable_today": live_daily // 13,
            "objects_unspent_through_replenishment": live_daily * plan.days_remaining,
            "note": (
                f"{live_daily} objects a day buys {live_daily} in-game checks "
                "during one game on the day it is played -- not thirteen, and "
                "not a complete game. Fifteen-minute coverage of one full game "
                "needs 13 objects on a single day. Unspent objects from earlier "
                "days do not accumulate into a later game: the month's total is "
                "what limits how many days can be funded, while this per-day "
                "number is what limits any single game. Four concurrent games "
                "is a ceiling on how many may be TRACKED at once, never a claim "
                "the day pays for four. A tracked game is followed until the "
                "day's allocation is spent and then reports its last confirmed "
                "score with its real age."
            ),
        },
        "nfl_pregame": {
            # Named explicitly because a reader who sees 0 next to a 0.08 share
            # will assume rounding, and would then expect the zero to fix itself
            # as the budget grows. It will not: the lane is switched off.
            "objects_per_day": plan.allocations["sgo_nfl_events"].daily_allowance,
            "funded": LANES_BY_KEY["sgo_nfl_events"].funded,
            "objects_per_day_if_funded": apportion(
                plan.daily_total, include_unfunded=True).get("sgo_nfl_events", 0),
            "zero_is": "AN_EXPLICIT_POLICY_CHOICE",
            "note": (
                "NFL pregame receives nothing because the lane is declared "
                "unfunded, not because its share rounded down. Its declared "
                "share is "
                f"{LANES_BY_KEY['sgo_nfl_events'].share:.2f} of the daily total, "
                "and at today's total a funded lane at that share would receive "
                f"{apportion(plan.daily_total, include_unfunded=True).get('sgo_nfl_events', 0)} "
                "objects a day. Turning it on is James's decision and changes "
                "every other lane's allowance, because the shares are of one "
                "fixed total. Nothing here bypasses the reservation guard: an "
                "unfunded lane is refused by name at the enforcement point."
            ),
        },
        "free_and_unaffected": {
            "mlb_scoreboard": (
                "Every two minutes from the free MLB Stats API. No provider "
                "allowance is involved, so it is unchanged by any of this."
            ),
            "nfl_finals": "Hourly from the free nflverse release.",
            "governed_finals": "Free MLB Stats API, one feed per missing game.",
        },
        "plan": plan.as_dict(),
    }


__all__ = [
    "ALLOCATION_CHANGES",
    "FALLBACK_MONTHLY_CAP",
    "LANES",
    "LANES_BY_KEY",
    "OBJECT_RESERVE",
    "PROVIDER",
    "USAGE_ENDPOINT_KEY",
    "Allocation",
    "Grant",
    "Lane",
    "Plan",
    "apportion",
    "build_plan",
    "coverage_forecast",
    "latest_usage_reading",
    "replenishment_date",
    "request_objects",
]

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
            "NFL is a research shadow with no wagering authority, so its pregame "
            "prices are evidence rather than input to a stake. No collector "
            "consumes this lane today, so it is unfunded and its share goes to "
            "the lanes that do -- funding it would strand objects nothing spends."
        ),
        funded=False,
    ),
    Lane(
        key="sgo_nfl_live", label="NFL in-game score", share=0.22,
        rationale=(
            "Explicitly funded, and deliberately small. Four concurrent games is "
            "a ceiling on how many may be followed at once, not a promise that "
            "the day can pay for four complete games."
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


def apportion(daily_total: int) -> dict[str, int]:
    """Whole objects per funded lane, summing exactly to the daily total.

    Largest remainder rather than truncation. At eleven objects a day a lane
    holding eight per cent truncates to nothing, which would quietly defund it
    for the rest of the month; this gives it its turn and keeps the parts adding
    up to the whole. Ties break on the declared lane order, so the same total
    always produces the same split -- a budget that reshuffles between two reads
    is not one anybody can reason about.
    """

    funded = [lane for lane in LANES if lane.funded]
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
            "pregame_captures_per_day": mlb_daily,
            "pregame_captures_fundable_through_replenishment": mlb_fundable,
            "daily_coverage_fraction": (
                round(mlb_daily / mlb_per_day, 3) if mlb_per_day else None),
            "checkpoints_per_game": 1,
            "note": (
                "One capture per game, at the checkpoint nearest its decision "
                "cutoff. The fraction is per day, against the slate size AISP "
                "actually has registered -- canonical events exist only a short "
                "horizon ahead, so a whole-window game count would understate "
                "the shortfall badly. Games beyond the day's allocation are "
                "reported by id as unsupported rather than dropped silently."
            ),
        },
        "nfl_in_game": {
            "games_scheduled": nfl_games,
            "objects_fundable": live_daily * plan.days_remaining,
            "objects_per_game_at_15_minutes": 13,
            "complete_games_fundable": round(
                (live_daily * plan.days_remaining) / 13, 1) if live_daily else 0.0,
            "note": (
                "Four concurrent games is a ceiling on how many may be followed "
                "at once, not a promise the budget pays for four. At this "
                "allowance the day funds part of one game, so a tracked game is "
                "followed until its allocation is spent and then reports its "
                "last confirmed score with its real age."
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

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

#: Never spent, whatever the plan says.
#:
#: A SCHEDULE, not a constant. The owner reduced it to 300 for the remainder of
#: September 2026 to fund price coverage, and it returns to 400 at the October
#: monthly reset automatically -- so the temporary relaxation expires on its own
#: rather than depending on someone remembering to put it back. A reserve that
#: has to be manually restored is a reserve that eventually is not.
DEFAULT_OBJECT_RESERVE = 400
RESERVE_SCHEDULE: tuple[dict[str, Any], ...] = (
    {
        "from_service_date": "2026-09-14",
        "until_service_date": "2026-09-30",
        "reserve": 300,
        "authority": "project owner, 2026-09-14",
        "reason": (
            "Bounded owner-directed reduction from 400 to fund MLB price "
            "coverage for the remainder of September 2026. Restores to 400 on "
            "the October monthly reset without further action."
        ),
    },
)


def reserve_for(day: date) -> int:
    """The protected reserve in force on one Eastern service day."""

    for entry in RESERVE_SCHEDULE:
        start = date.fromisoformat(entry["from_service_date"])
        end = date.fromisoformat(entry["until_service_date"])
        if start <= day <= end:
            return int(entry["reserve"])
    return DEFAULT_OBJECT_RESERVE


def reserve_note(day: date) -> dict[str, Any]:
    """The reserve and why it is what it is, for a receipt."""

    for entry in RESERVE_SCHEDULE:
        start = date.fromisoformat(entry["from_service_date"])
        end = date.fromisoformat(entry["until_service_date"])
        if start <= day <= end:
            return {"reserve": int(entry["reserve"]), "scheduled": True,
                    "restores_to": DEFAULT_OBJECT_RESERVE,
                    "restores_on": "2026-10-01 (monthly reset)",
                    "authority": entry["authority"], "reason": entry["reason"]}
    return {"reserve": DEFAULT_OBJECT_RESERVE, "scheduled": False,
            "authority": "standing", "reason": "The standing protected reserve."}


#: Kept as a name for callers that still import it. Reads the schedule for
#: today rather than pretending the reserve never changes.
def _today_reserve() -> int:
    from app.services.service_day import service_date
    return reserve_for(service_date())


OBJECT_RESERVE = DEFAULT_OBJECT_RESERVE

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
    Lane(
        key="sgo_show_preview_events", label="Show Preview day-before quotes",
        share=0.0, funded=False,
        rationale=(
            "Declared so day-before show collection has a lane of its own, and "
            "unfunded so it has no claim on the daily total. A preview quote "
            "and a checkpoint quote are different purchases: the checkpoint "
            "quote has to be fresh AT its checkpoint, and letting the show draw "
            "from the same allowance would spend a decision's price on a "
            "recording. The zero share is why funding this lane cannot quietly "
            "shrink the other four -- it takes an explicit share, which is an "
            "owner decision. Until then `request_objects` refuses it by name."
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


#: The league each lane is actually allowed to buy. A row whose own retained
#: request evidence names a different league was booked to the wrong lane.
LANE_LEAGUE: dict[str, str] = {
    "sgo_mlb_events": "MLB",
    "sgo_nfl_events": "NFL",
    "sgo_nfl_live": "NFL",
    "sgo_nfl_periods": "NFL",
}


def _recorded_league(row: ProviderRequestLedger) -> str | None:
    """The leagueID the request actually carried, from its retained evidence."""

    meta = row.response_metadata or {}
    filters = ((meta.get("request_shape") or {}).get("filters") or {})
    league = filters.get("leagueID")
    return str(league).upper() if league else None


def misattributed_rows(
    session: Session, *, now: datetime,
) -> list[ProviderRequestLedger]:
    """Today's rows whose retained evidence contradicts the lane they charge.

    This is not hypothetical. Before the NFL collectors were taught to name
    their own lane (2026-09-14, commits 60167ee and 8800f26), they called
    `record_request` without an `endpoint_key` and fell through to its default
    -- which is the MLB lane. Eighteen NFL objects were therefore booked as MLB
    pregame spend on the evening of 2026-09-13, and because the budget day is
    the UTC day, they exhausted the MLB allowance for the whole of 09-14 before
    that slate had any chance to be priced.

    The collector defect is fixed. The ledger rows are left exactly as written
    -- they are the record of what was requested, and rewriting them would
    destroy the evidence this correction depends on. Instead the ATTRIBUTION is
    corrected here, from the rows' own retained filters.
    """

    from app.services.service_day import service_day_start

    midnight = service_day_start(now)
    rows = session.scalars(
        select(ProviderRequestLedger).where(
            ProviderRequestLedger.provider == PROVIDER,
            ProviderRequestLedger.status == "SUCCEEDED",
            ProviderRequestLedger.endpoint_key != USAGE_ENDPOINT_KEY,
            ProviderRequestLedger.requested_at >= midnight,
        )
    ).all()
    out = []
    for row in rows:
        expected = LANE_LEAGUE.get(row.endpoint_key or "")
        actual = _recorded_league(row)
        if expected and actual and expected != actual:
            out.append(row)
    return out


def _lane_usage(session: Session, *, now: datetime) -> dict[str, tuple[int, int]]:
    """Per lane: objects settled today, and objects reserved but not yet settled.

    In-flight matters for concurrency. A reservation is written before its
    request goes out and carries the count it asked for, so a job checking the
    budget can see what another job is already committed to spending and the two
    cannot both claim the last object.
    """

    # The EASTERN service day, not the UTC day. An NFL request at 22:00 ET
    # belongs to that evening's budget, not to the next morning's slate --
    # which is precisely the confusion that cost 2026-09-14 its MLB prices.
    from app.services.service_day import service_day_start

    midnight = service_day_start(now)

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

    # Subtract spend that its own evidence says belongs to another league. The
    # objects are still counted against the MONTHLY total -- they were really
    # bought -- but a lane must not be charged for a request it never made.
    misbooked: dict[str, int] = {}
    for row in misattributed_rows(session, now=now):
        key = row.endpoint_key or ""
        misbooked[key] = misbooked.get(key, 0) + int(row.records_received or 0)

    return {
        lane.key: (
            max(0, int(settled.get(lane.key, 0)) - misbooked.get(lane.key, 0)),
            int(pending.get(lane.key, 0)),
        )
        for lane in LANES
    }


def daily_snapshot(session: Session, *, now: datetime | None = None):
    """This Eastern service day's allocation, computed once and then held.

    THE BEHAVIOUR THIS REPLACES
    ---------------------------
    The allowance used to be recomputed from the remaining balance on every
    read, so spending inside a day lowered that same day's allowance underneath
    itself -- a lane told it had eight objects could draw only seven. An
    allowance that shrinks as it is used cannot be planned against.

    So the day's capacity is fixed at its first calculation, from the
    provider's actual balance at that moment, and reconciled against
    afterwards rather than reduced. Tomorrow reads tomorrow's balance, so a
    heavy day still costs the next one -- which is the part that should carry.

    The hard remaining balance and the reserve are enforced separately on every
    grant, so a stale snapshot can never authorise a spend the account cannot
    fund.
    """

    from app.models import ProviderAllocationSnapshot
    from app.services.allocation_demand import measure
    from app.services.service_day import ALLOCATION_DAY_SEMANTICS, service_date

    moment = now or datetime.now(UTC)
    day = service_date(moment)

    existing = session.scalar(select(ProviderAllocationSnapshot).where(
        ProviderAllocationSnapshot.provider == PROVIDER,
        ProviderAllocationSnapshot.service_date == day))
    if existing is not None:
        return existing

    cap, used, source, read_at = _provider_balance(session, moment)
    reserve = reserve_for(day)
    replenishes = replenishment_date(moment)
    days_remaining = max(1, (replenishes - moment.date()).days)
    spendable = max(0, cap - used - reserve)
    daily_total = spendable // days_remaining

    demand = measure(session, now=moment)
    lanes = _demand_aware_split(daily_total, demand)

    snapshot = ProviderAllocationSnapshot(
        provider=PROVIDER, service_date=day,
        semantics=ALLOCATION_DAY_SEMANTICS,
        monthly_cap=cap, used_this_month=used, remaining=max(0, cap - used),
        balance_source=source, balance_read_at=read_at,
        reserve=reserve, days_remaining=days_remaining,
        daily_total=daily_total, lanes=lanes, demand=demand, taken_at=moment)
    session.add(snapshot)
    try:
        session.flush()
    except Exception:  # noqa: BLE001 - another worker won the race; use theirs
        session.rollback()
        return session.scalar(select(ProviderAllocationSnapshot).where(
            ProviderAllocationSnapshot.provider == PROVIDER,
            ProviderAllocationSnapshot.service_date == day))
    return snapshot


def _demand_aware_split(daily_total: int, demand: dict[str, Any]) -> dict[str, int]:
    """Reserved NFL objects first, then every remaining object to MLB.

    Reservations come off the top because they are specific: one object for one
    named game with a decision to make. What is left is price coverage, which
    is the lane that can always use more and is never harmed by being last in
    an order it would otherwise dominate.
    """

    reservations = dict(demand.get("reservations") or {})
    out = {lane.key: 0 for lane in LANES}
    budget = max(0, int(daily_total))
    for key in ("sgo_nfl_events", "sgo_nfl_live", "sgo_nfl_periods"):
        want = max(0, int(reservations.get(key, 0)))
        take = min(want, budget)
        out[key] = take
        budget -= take
    # Any OTHER funded lane takes its own declared share before MLB absorbs the
    # remainder. Today that loop does nothing: the only lane it can reach is
    # Show Preview, which is unfunded with a zero share. It exists so that
    # funding that lane later grants it its declared share explicitly, rather
    # than silently shrinking MLB's remainder by an amount nobody wrote down.
    named = {"sgo_mlb_events", "sgo_nfl_events", "sgo_nfl_live", "sgo_nfl_periods"}
    for lane in LANES:
        if lane.key in named or not lane.funded:
            continue
        take = min(budget, max(0, int(daily_total * lane.share)))
        out[lane.key] = take
        budget -= take
    out["sgo_mlb_events"] = budget
    return out


def _same_billing_month(read_at: datetime | None, moment: datetime) -> bool:
    """Does a usage reading describe the billing month ``moment`` falls in?

    The provider bills a UTC calendar month and refills at 00:00 UTC on the 1st.
    A reading taken at 23:50 UTC on September 30 reports SEPTEMBER's usage; used
    as October's opening balance it says the account is nearly spent when it has
    just refilled, and every lane goes dark for the day. So a reading from an
    earlier month is not a balance -- it is history.
    """

    if read_at is None:
        return False
    aware = _aware_dt(read_at).astimezone(UTC)
    current = moment.astimezone(UTC) if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return (aware.year, aware.month) == (current.year, current.month)


def _provider_balance(session: Session, moment: datetime):
    """The provider's own cap and usage, plus anything spent since the reading.

    Falls back to AISP's own current-month count -- which is the conservative
    direction, since it can only over-report what has been spent -- when there
    is no reading, when the reading carries no cap, or when the newest reading
    belongs to an earlier billing month.
    """

    reading = latest_usage_reading(session)
    meta = dict((reading.response_metadata or {}) if reading else {})
    usage = dict(meta.get("usage") or {})
    cap = int(usage.get("monthly_cap") or 0)
    used = int(usage.get("used_this_month") or 0)
    source = "PROVIDER_ACCOUNT_USAGE"
    read_at = reading.requested_at if reading else None
    if cap and not _same_billing_month(read_at, moment):
        # Provenance cannot be established for THIS month, so the reading is
        # refused rather than trusted. Fail closed on the source, not on the
        # account: the local count for the new month is still authoritative
        # about what AISP itself has spent.
        return (cap, _local_month_spend(session, now=moment),
                "LOCAL_LEDGER_COUNT_PRIOR_MONTH_READING_IGNORED", None)
    if not cap:
        return (FALLBACK_MONTHLY_CAP, _local_month_spend(session, now=moment),
                "LOCAL_LEDGER_COUNT", None)
    since = session.scalar(
        select(func.coalesce(func.sum(ProviderRequestLedger.records_received), 0))
        .where(
            ProviderRequestLedger.provider == PROVIDER,
            ProviderRequestLedger.status == "SUCCEEDED",
            ProviderRequestLedger.endpoint_key != USAGE_ENDPOINT_KEY,
            ProviderRequestLedger.requested_at > reading.requested_at,
        ))
    return cap, used + int(since or 0), source, read_at


def build_plan(session: Session, *, now: datetime | None = None) -> Plan:
    """This service day's ISSUED allocation, plus what has been spent against it.

    The allowances come from the day's snapshot and do not move as objects are
    consumed. Only `spent_today` and `room` move, which is what a reader
    actually needs: how much of a fixed allowance is left, not a fixed-looking
    number that is quietly falling.
    """

    moment = now or datetime.now(UTC)
    warnings: list[str] = []

    snapshot = daily_snapshot(session, now=moment)
    cap = int(snapshot.monthly_cap)
    reserve = int(snapshot.reserve)
    issued = dict(snapshot.lanes or {})

    if snapshot.balance_source == "LOCAL_LEDGER_COUNT_PRIOR_MONTH_READING_IGNORED":
        warnings.append(
            "The newest provider usage reading belongs to an earlier billing "
            "month, so it was ignored and this day's balance is AISP's own "
            "count for the current month against the provider's cap. It cannot "
            "see a spend made elsewhere. A fresh usage reading -- which costs "
            "no objects -- restores the provider's own figure.")
    elif snapshot.balance_source == "LOCAL_LEDGER_COUNT":
        warnings.append(
            "No provider usage reading was recorded when this day's allocation "
            "was taken, so its balance is AISP's own count against the "
            "published cap. It cannot see a spend made elsewhere.")

    # The LIVE balance, enforced on every grant regardless of the snapshot. A
    # fixed allowance must never outlive the money behind it.
    live_cap, live_used, live_source, live_read_at = _provider_balance(session, moment)
    if live_read_at is not None:
        aware = _aware_dt(live_read_at)
        if moment - aware > timedelta(hours=6):
            warnings.append(
                f"The provider balance was last read {aware.isoformat()}; a "
                "spend made outside AISP since then is not reflected.")

    lane_usage = _lane_usage(session, now=moment)
    allocations: dict[str, Allocation] = {}
    for lane in LANES:
        spent, flight = lane_usage.get(lane.key, (0, 0))
        allocations[lane.key] = Allocation(
            lane=lane, daily_allowance=int(issued.get(lane.key, 0)),
            spent_today=spent, in_flight=flight)

    if int(snapshot.daily_total) == 0:
        warnings.append(
            f"{max(0, live_cap - live_used - reserve)} objects are spendable "
            f"across {snapshot.days_remaining} days, so no lane has a whole "
            "object a day. Collection is paused until the balance replenishes "
            f"on {snapshot.replenishes_on if hasattr(snapshot, 'replenishes_on') else replenishment_date(moment).isoformat()}.")

    return Plan(
        monthly_cap=live_cap, used_this_month=live_used, reserve=reserve,
        replenishes_on=replenishment_date(moment),
        days_remaining=int(snapshot.days_remaining),
        daily_total=int(snapshot.daily_total), allocations=allocations,
        source=live_source, read_at=live_read_at, warnings=warnings)


def _aware_dt(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


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
    "LANE_LEAGUE",
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
    "misattributed_rows",
    "replenishment_date",
    "request_objects",
]

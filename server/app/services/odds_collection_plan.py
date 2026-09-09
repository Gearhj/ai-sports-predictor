"""Event-aware odds collection planning: which requests are worth a credit.

WHY THIS EXISTS
---------------
The previous cadence polled the whole sport every ``ODDS_PREGAME_REFRESH_MINUTES``
for as long as any game was inside a 4-hour window, and every poll asked for
three markets. On a normal MLB slate that is ~40 requests x 3 credits = ~120
credits a day against a 500-credit month: the plan is exhausted in about four
days, and two of every three credits buy spreads and totals that the governing
MONEYLINE-only policy can never act on.

THE INSIGHT
-----------
One ``/v4/sports/{sport}/odds`` request returns EVERY upcoming event for that
sport, and its cost is ``len(markets) * len(regions)`` regardless of how many
events come back. So collection is not a per-game problem: it is a *covering*
problem over the slate's decision cutoffs.

THE CONTRACT WE MUST SATISFY (measured, not assumed)
-----------------------------------------------------
* ``FINAL_PREGAME_CUTOFF_MINUTES = 15``  -> cutoff = first pitch - 15 min
* ``FINAL_FREEZE_WINDOW_MINUTES = 15``   -> the final evaluation happens at some
  instant in ``[cutoff - 15min, cutoff]``
* ``MAX_QUOTE_STALENESS_MINUTES = 240``  -> ``_is_stale`` compares
  ``observed_at < now - 240min`` where ``now`` is that evaluation instant

Taking the worst case ``now = cutoff``, a game is priceable only if at least one
observation falls in::

    observed_at in [cutoff - 240min, cutoff]

so ONE request at time ``t`` covers exactly the games whose cutoff lies in::

    cutoff in [t + INGEST_HEADROOM, t + 240min]

The lower bound is headroom so the response is fetched, normalised and committed
before the final freeze window opens; without it a request fired at the cutoff
would land too late to be read.

THE ALGORITHM
-------------
Minimum point cover over those intervals. Sort the cutoffs; repeatedly place a
request at ``earliest_uncovered_cutoff - INGEST_HEADROOM`` (the LATEST instant
that still covers it, which therefore also covers the most later games) and drop
every cutoff it covers. This is the classic greedy interval-stabbing optimum: no
schedule with fewer requests can cover the same slate.

WHAT THIS MODULE DOES NOT DO
----------------------------
It performs no I/O, spends no credit, and knows nothing about budgets. It turns
a list of scheduled starts into a list of instants. Whether a due checkpoint may
actually be spent is decided by :mod:`app.services.odds_budget`, and the request
itself is still made by the existing reserved-and-ledgered path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

#: The governing decision contract, restated here as data so the planner cannot
#: silently drift from the policy it serves. These mirror
#: shadow_experiment_service.FINAL_PREGAME_CUTOFF_MINUTES /
#: FINAL_FREEZE_WINDOW_MINUTES and opportunity_service.MAX_QUOTE_STALENESS_MINUTES.
FINAL_PREGAME_CUTOFF_MINUTES = 15
FINAL_FREEZE_WINDOW_MINUTES = 15
MAX_QUOTE_STALENESS_MINUTES = 240

#: Minutes a request needs before a cutoff to be fetched, normalised, committed
#: and readable by the freeze job. One full freeze cadence (5 min) plus the
#: freeze window (15 min) plus slack.
INGEST_HEADROOM_MINUTES = 25

#: How late a missed checkpoint may still be served. Past this it is abandoned
#: rather than fired at a useless moment; the next checkpoint takes over.
CHECKPOINT_GRACE_MINUTES = 20


@dataclass(frozen=True)
class SportCollectionPolicy:
    """What one sport is allowed to ask for, and how much that costs."""

    league_code: str
    sport_key: str
    #: Ordered by importance. MLB asks for the single market V2.1 can act on.
    markets: tuple[str, ...]
    regions: tuple[str, ...]
    #: 1 is served first when the budget is tight; higher numbers yield.
    priority: int
    rationale: str
    #: Which Game.source_mode values hold this sport's schedule. MLB's live
    #: slate is LIVE; the NFL schedule is imported as RESEARCH_SHADOW, and
    #: filtering it out would silently plan zero NFL checkpoints forever.
    source_modes: tuple[str, ...] = ("LIVE",)

    @property
    def credit_cost(self) -> int:
        """The Odds API charges one credit per market per region per request."""

        return max(1, len(self.markets) * len(self.regions))


#: MLB is the only sport whose quotes can reach a governed V2.1 decision, and
#: V2.1's policy_config declares supported_markets = ["MONEYLINE"]. Asking for
#: spreads and totals therefore triples the cost of every MLB request and buys
#: nothing the policy can use. NFL is research-only, so it may take the wider
#: market set but yields to MLB whenever the budget is short.
MLB_POLICY = SportCollectionPolicy(
    league_code="MLB",
    sport_key="baseball_mlb",
    markets=("h2h",),
    regions=("us",),
    priority=1,
    rationale=(
        "AISP_PRACTICAL_PREGAME_POLICY_V2_1 supports MONEYLINE only; h2h is the "
        "only market a governed decision can price."
    ),
)
NFL_POLICY = SportCollectionPolicy(
    league_code="NFL",
    sport_key="americanfootball_nfl",
    markets=("h2h", "spreads", "totals"),
    regions=("us",),
    priority=2,
    rationale=(
        "NFL is a research shadow with no wagering authority. Spreads and totals "
        "are lower-priority research data and are budgeted only after MLB."
    ),
    source_modes=("LIVE", "RESEARCH_SHADOW"),
)
SPORT_POLICIES: dict[str, SportCollectionPolicy] = {
    "MLB": MLB_POLICY,
    "NFL": NFL_POLICY,
}


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def decision_cutoff(first_pitch: datetime) -> datetime:
    """The last instant a quote can be observed and still bind to a decision."""

    return _aware(first_pitch) - timedelta(minutes=FINAL_PREGAME_CUTOFF_MINUTES)


def coverage_window(checkpoint: datetime) -> tuple[datetime, datetime]:
    """The cutoffs a request at ``checkpoint`` can serve, inclusive."""

    checkpoint = _aware(checkpoint)
    return (
        checkpoint + timedelta(minutes=INGEST_HEADROOM_MINUTES),
        checkpoint + timedelta(minutes=MAX_QUOTE_STALENESS_MINUTES),
    )


def covers(checkpoint: datetime, cutoff: datetime) -> bool:
    earliest, latest = coverage_window(checkpoint)
    return earliest <= _aware(cutoff) <= latest


def plan_checkpoints(
    first_pitches: list[datetime],
    *,
    now: datetime,
) -> list[datetime]:
    """The minimum set of request instants covering every still-open game.

    Games whose cutoff has already passed are dropped: nothing collected now can
    reach a decision that has already been made, so collection stops at the
    cutoff exactly as required. A checkpoint that would fall in the past is
    pulled forward to ``now`` when it can still cover its game, and abandoned
    when it cannot -- an unreachable checkpoint is never scheduled, because
    firing it would spend a credit that cannot change any decision.
    """

    now = _aware(now)
    cutoffs = sorted(
        cutoff for cutoff in (decision_cutoff(start) for start in first_pitches)
        # Strictly future cutoffs only. A cutoff inside INGEST_HEADROOM cannot be
        # served by a request issued now, so it is already lost.
        if cutoff >= now + timedelta(minutes=INGEST_HEADROOM_MINUTES)
    )
    checkpoints: list[datetime] = []
    index = 0
    while index < len(cutoffs):
        target = cutoffs[index]
        # Place the request at the LATEST instant that still covers the earliest
        # uncovered cutoff. Any later and this game is missed; any earlier and
        # the 240-minute window is spent on time that has already elapsed, so
        # fewer later games ride along on the same credit. This right-endpoint
        # choice is what makes the cover minimal.
        checkpoint = max(now, target - timedelta(minutes=INGEST_HEADROOM_MINUTES))
        checkpoints.append(checkpoint)
        while index < len(cutoffs) and covers(checkpoint, cutoffs[index]):
            index += 1
    return checkpoints


@dataclass(frozen=True)
class DueCheckpoint:
    """A checkpoint that is ready to be served right now."""

    league_code: str
    sport_key: str
    checkpoint: datetime
    markets: tuple[str, ...]
    regions: tuple[str, ...]
    credit_cost: int
    covered_cutoffs: int
    priority: int

    def as_dict(self) -> dict[str, object]:
        return {
            "league_code": self.league_code,
            "sport_key": self.sport_key,
            "checkpoint_utc": self.checkpoint.isoformat(),
            "markets": list(self.markets),
            "regions": list(self.regions),
            "credit_cost": self.credit_cost,
            "covered_cutoffs": self.covered_cutoffs,
            "priority": self.priority,
        }


@dataclass
class SportPlan:
    """One sport's full plan for the operational day."""

    policy: SportCollectionPolicy
    checkpoints: list[datetime] = field(default_factory=list)
    cutoffs: list[datetime] = field(default_factory=list)

    @property
    def projected_credits(self) -> int:
        return len(self.checkpoints) * self.policy.credit_cost

    def as_dict(self) -> dict[str, object]:
        return {
            "league_code": self.policy.league_code,
            "sport_key": self.policy.sport_key,
            "markets": list(self.policy.markets),
            "regions": list(self.policy.regions),
            "credit_cost_per_request": self.policy.credit_cost,
            "open_games": len(self.cutoffs),
            "checkpoints": [c.isoformat() for c in self.checkpoints],
            "projected_credits": self.projected_credits,
            "rationale": self.policy.rationale,
        }


def build_sport_plan(
    policy: SportCollectionPolicy,
    first_pitches: list[datetime],
    *,
    now: datetime,
) -> SportPlan:
    cutoffs = sorted(decision_cutoff(start) for start in first_pitches)
    return SportPlan(
        policy=policy,
        checkpoints=plan_checkpoints(first_pitches, now=now),
        cutoffs=[c for c in cutoffs if c >= _aware(now) + timedelta(minutes=INGEST_HEADROOM_MINUTES)],
    )


def due_checkpoint(
    plan: SportPlan,
    *,
    now: datetime,
    last_success_at: datetime | None,
) -> DueCheckpoint | None:
    """The one checkpoint this sport should serve now, if any.

    A checkpoint is DUE when ``now`` has reached it, it has not been overtaken by
    the next checkpoint, it is still inside its grace period, and no successful
    request for this sport has already happened at or after it. Deriving
    "already served" from the persisted ledger rather than in-memory state makes
    this idempotent across restarts and impossible to double-spend: two ticks in
    the same window see the same answer, and the second one sees the first one's
    success.
    """

    now = _aware(now)
    last_success_at = _aware(last_success_at) if last_success_at else None
    for index, checkpoint in enumerate(plan.checkpoints):
        following = plan.checkpoints[index + 1] if index + 1 < len(plan.checkpoints) else None
        if now < checkpoint:
            return None  # the next checkpoint is still in the future
        if following is not None and now >= following:
            continue  # overtaken; the later checkpoint is the live one
        if now > checkpoint + timedelta(minutes=CHECKPOINT_GRACE_MINUTES):
            continue  # missed beyond its grace; do not spend on a stale slot
        if last_success_at is not None and last_success_at >= checkpoint:
            continue  # already served by a successful request
        covered = sum(1 for cutoff in plan.cutoffs if covers(checkpoint, cutoff))
        return DueCheckpoint(
            league_code=plan.policy.league_code,
            sport_key=plan.policy.sport_key,
            checkpoint=checkpoint,
            markets=plan.policy.markets,
            regions=plan.policy.regions,
            credit_cost=plan.policy.credit_cost,
            covered_cutoffs=covered,
            priority=plan.policy.priority,
        )
    return None


def project_month(
    *,
    mlb_days: int,
    mlb_checkpoints_per_day: int,
    nfl_weeks: float,
    nfl_checkpoints_per_week: int,
) -> dict[str, object]:
    """Projected monthly credits under this strategy. Pure arithmetic."""

    mlb = mlb_days * mlb_checkpoints_per_day * MLB_POLICY.credit_cost
    nfl = round(nfl_weeks * nfl_checkpoints_per_week * NFL_POLICY.credit_cost)
    return {
        "mlb_credits": mlb,
        "nfl_credits": nfl,
        "total_credits": mlb + nfl,
        "mlb_cost_per_request": MLB_POLICY.credit_cost,
        "nfl_cost_per_request": NFL_POLICY.credit_cost,
    }


__all__ = [
    "CHECKPOINT_GRACE_MINUTES",
    "DueCheckpoint",
    "FINAL_FREEZE_WINDOW_MINUTES",
    "FINAL_PREGAME_CUTOFF_MINUTES",
    "INGEST_HEADROOM_MINUTES",
    "MAX_QUOTE_STALENESS_MINUTES",
    "MLB_POLICY",
    "NFL_POLICY",
    "SPORT_POLICIES",
    "SportCollectionPolicy",
    "SportPlan",
    "build_sport_plan",
    "coverage_window",
    "covers",
    "decision_cutoff",
    "due_checkpoint",
    "plan_checkpoints",
    "project_month",
]

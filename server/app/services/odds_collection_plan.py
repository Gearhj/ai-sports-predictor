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
#: freeze window (15 min) plus slack. This is the HARD requirement.
INGEST_HEADROOM_MINUTES = 25

#: A scheduler ticking every 5 minutes cannot fire at an exact instant, so a
#: request lands somewhere AFTER its checkpoint. Planning therefore assumes
#: worst-case lateness on both sides of the coverage window: the checkpoint is
#: placed this much earlier than the strict right endpoint, and the window it
#: is credited with is shrunk by the same amount at the far end. Without this
#: the cover is correct only for a tick that lands on the instant, and a real
#: tick misses the very game the checkpoint existed to serve.
CHECKPOINT_TICK_SLACK_MINUTES = 10

#: How late a missed checkpoint may still be served. Past this it is abandoned
#: rather than fired at a useless moment; the next checkpoint takes over. It
#: must exceed the scheduler tick so a tick always lands inside some window.
CHECKPOINT_GRACE_MINUTES = 20

#: A success this recently already did what the checkpoint was for. Safely
#: below the 215-minute minimum spacing between checkpoints in a minimal
#: cover, so it can never suppress a genuinely distinct checkpoint.
CHECKPOINT_SATISFIED_LOOKBACK_MINUTES = 60


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


def coverage_window(observed_at: datetime) -> tuple[datetime, datetime]:
    """The cutoffs an observation at ``observed_at`` actually serves, inclusive.

    This is the TRUE predicate, derived from the decision contract. Use it to
    verify coverage after the fact, with the instant the request really happened.
    """

    observed_at = _aware(observed_at)
    return (
        observed_at + timedelta(minutes=INGEST_HEADROOM_MINUTES),
        observed_at + timedelta(minutes=MAX_QUOTE_STALENESS_MINUTES),
    )


def covers(observed_at: datetime, cutoff: datetime) -> bool:
    earliest, latest = coverage_window(observed_at)
    return earliest <= _aware(cutoff) <= latest


def planned_coverage_window(checkpoint: datetime) -> tuple[datetime, datetime]:
    """What a checkpoint may be CREDITED with, assuming it fires late.

    Narrower than the true window by the tick slack at both ends, so a request
    that lands anywhere in ``[checkpoint, checkpoint + slack]`` still genuinely
    covers everything the plan credited to it.
    """

    checkpoint = _aware(checkpoint)
    slack = timedelta(minutes=CHECKPOINT_TICK_SLACK_MINUTES)
    return (
        checkpoint + timedelta(minutes=INGEST_HEADROOM_MINUTES) + slack,
        checkpoint + timedelta(minutes=MAX_QUOTE_STALENESS_MINUTES) - slack,
    )


def planned_covers(checkpoint: datetime, cutoff: datetime) -> bool:
    earliest, latest = planned_coverage_window(checkpoint)
    return earliest <= _aware(cutoff) <= latest


def plan_checkpoints(first_pitches: list[datetime]) -> list[datetime]:
    """The minimum set of request instants covering every game on the slate.

    Deliberately a pure function of the SLATE, never of ``now``. An earlier
    draft derived the cover relative to the current instant, which produced two
    failures at once: every checkpoint landed strictly in the future so a tick
    could never reach one (nothing ever fired), and the cover re-anchored on
    every tick so the request count drifted from 1-3 a day up to 6-13. A cover
    that is a function of the day's cutoffs alone is stable across ticks and
    across restarts, and can be reasoned about in advance.
    """

    cutoffs = sorted(decision_cutoff(start) for start in first_pitches)
    checkpoints: list[datetime] = []
    index = 0
    while index < len(cutoffs):
        # Place the request at the LATEST instant that still covers the earliest
        # uncovered cutoff. Any later and this game is missed; any earlier and
        # the 240-minute window is spent on time that has already elapsed, so
        # fewer later games ride along on the same credit. This right-endpoint
        # choice is what makes the cover minimal.
        checkpoint = cutoffs[index] - timedelta(
            minutes=INGEST_HEADROOM_MINUTES + CHECKPOINT_TICK_SLACK_MINUTES
        )
        checkpoints.append(checkpoint)
        while index < len(cutoffs) and planned_covers(checkpoint, cutoffs[index]):
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
    #: Checkpoints that can still fire; the rest are already past their grace.
    outstanding: list[datetime] = field(default_factory=list)

    @property
    def projected_credits(self) -> int:
        """What the REST of today will cost, not what the whole day cost."""

        return len(self.outstanding) * self.policy.credit_cost

    @property
    def day_credits(self) -> int:
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
            "outstanding_checkpoints": [c.isoformat() for c in self.outstanding],
            "day_credits": self.day_credits,
            "projected_credits": self.projected_credits,
            "rationale": self.policy.rationale,
        }


def build_sport_plan(
    policy: SportCollectionPolicy,
    first_pitches: list[datetime],
    *,
    now: datetime,
) -> SportPlan:
    """The day's stable cover, plus the cutoffs that are still reachable.

    ``now`` narrows what is REPORTED as still outstanding; it never moves a
    checkpoint. ``projected_credits`` counts only the checkpoints that can still
    fire, so the operator sees what today will still cost, not what it cost.
    """

    now = _aware(now)
    checkpoints = plan_checkpoints(first_pitches)
    cutoffs = sorted(decision_cutoff(start) for start in first_pitches)
    return SportPlan(
        policy=policy,
        checkpoints=checkpoints,
        cutoffs=cutoffs,
        outstanding=[
            checkpoint for checkpoint in checkpoints
            if checkpoint + timedelta(minutes=CHECKPOINT_GRACE_MINUTES) >= now
        ],
    )


def due_checkpoint(
    plan: SportPlan,
    *,
    now: datetime,
    last_success_at: datetime | None,
) -> DueCheckpoint | None:
    """The one checkpoint this sport should serve now, if any.

    A checkpoint is DUE when ``now`` lies inside its serving window
    ``[checkpoint, checkpoint + grace]`` and no successful request has already
    satisfied it. The window matters: a scheduler ticking every few minutes
    will never land on an instant, so an instant-equality rule fires never.

    "Already satisfied" is read from the persisted ledger rather than memory, so
    two ticks in the same window cannot double-spend and a restart changes
    nothing. The lookback is generous because consecutive checkpoints in a
    minimal cover are at least ``MAX_QUOTE_STALENESS - INGEST_HEADROOM`` apart,
    so it can suppress a repeat but never a genuinely different checkpoint.
    """

    now = _aware(now)
    last_success_at = _aware(last_success_at) if last_success_at else None
    for checkpoint in plan.checkpoints:
        if now < checkpoint:
            return None  # the next checkpoint is still ahead; nothing is due yet
        if now > checkpoint + timedelta(minutes=CHECKPOINT_GRACE_MINUTES):
            continue  # missed beyond its grace; the credit can no longer help
        if (
            last_success_at is not None
            and last_success_at >= checkpoint - timedelta(minutes=CHECKPOINT_SATISFIED_LOOKBACK_MINUTES)
        ):
            continue  # a recent success already covers what this checkpoint is for
        covered = sum(1 for cutoff in plan.cutoffs if planned_covers(checkpoint, cutoff))
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
    "CHECKPOINT_TICK_SLACK_MINUTES",
    "CHECKPOINT_SATISFIED_LOOKBACK_MINUTES",
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
    "planned_covers",
    "planned_coverage_window",
    "covers",
    "decision_cutoff",
    "due_checkpoint",
    "plan_checkpoints",
    "project_month",
]

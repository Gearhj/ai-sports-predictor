"""Decide, from persisted rows alone, whether a credit should be spent right now.

This is the replacement for blanket polling. A scheduler tick asks one question
-- "is a checkpoint due, and may it be paid for?" -- and this service answers it
from the canonical schedule and the provider request ledger. It makes no
network request itself; it returns a decision that the existing reserved-and-
ledgered refresh path then executes.

ORDER OF CHECKS, ALL FAIL-CLOSED
--------------------------------
1. Feature flag off               -> DISABLED
2. No API key                     -> NO_CREDENTIAL
3. Provider floor latched         -> PROVIDER_FLOOR_LATCHED  (never relaxed here)
4. Monthly or daily budget spent  -> BUDGET_EXHAUSTED
5. No eligible game               -> NO_ELIGIBLE_GAMES       (no inactive-sport polling)
6. No checkpoint due now          -> NO_CHECKPOINT_DUE       (no duplicate requests)
7. Otherwise                      -> the single highest-priority due checkpoint

MLB is served before NFL whenever both are due in the same tick, because MLB is
the only sport whose quotes can reach a governed decision and its request costs
one credit while NFL's costs three.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Game, GameStatus, League, ProviderRequestLedger
from app.services import odds_budget
from app.services.odds_budget_lanes import LANE_MLB_RECOVERY, lane_for_request
from app.services.odds_collection_plan import (
    MAX_QUOTE_STALENESS_MINUTES,
    SPORT_POLICIES,
    DueCheckpoint,
    SportCollectionPolicy,
    SportPlan,
    build_sport_plan,
)

REASON_DISABLED = "DISABLED"
REASON_NO_CREDENTIAL = "NO_CREDENTIAL"
REASON_FLOOR = "PROVIDER_FLOOR_LATCHED"
REASON_BUDGET = "BUDGET_EXHAUSTED"
REASON_NO_GAMES = "NO_ELIGIBLE_GAMES"
REASON_NOT_DUE = "NO_CHECKPOINT_DUE"
REASON_DUE = "CHECKPOINT_DUE"
REASON_RECOVERY = "RECOVERY_REQUIRED"

#: Ledger endpoint keys, per sport key, used both to record and to detect a
#: checkpoint that has already been served.
ENDPOINT_KEYS = {
    "baseball_mlb": "baseball_mlb_odds",
    "americanfootball_nfl": "americanfootball_nfl_odds",
}


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def parse_market_overrides(raw: str) -> dict[str, tuple[str, ...]]:
    """Parse ``MLB=h2h;NFL=h2h,spreads`` into per-league market tuples."""

    overrides: dict[str, tuple[str, ...]] = {}
    for clause in (raw or "").split(";"):
        clause = clause.strip()
        if not clause or "=" not in clause:
            continue
        league, _, markets = clause.partition("=")
        parsed = tuple(m.strip() for m in markets.split(",") if m.strip())
        if parsed:
            overrides[league.strip().upper()] = parsed
    return overrides


def effective_policy(league_code: str, settings: Settings) -> SportCollectionPolicy | None:
    base = SPORT_POLICIES.get(league_code.upper())
    if base is None:
        return None
    override = parse_market_overrides(settings.odds_market_overrides).get(league_code.upper())
    if not override:
        return base
    from dataclasses import replace

    return replace(base, markets=override)


@dataclass(frozen=True)
class CollectionDecision:
    """What this tick should do, and why."""

    reason: str
    checkpoint: DueCheckpoint | None
    budget: odds_budget.BudgetState
    plans: list[SportPlan]
    detail: str

    @property
    def should_request(self) -> bool:
        return self.reason in (REASON_DUE, REASON_RECOVERY) and self.checkpoint is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "detail": self.detail,
            "should_request": self.should_request,
            "checkpoint": self.checkpoint.as_dict() if self.checkpoint else None,
            "plans": [plan.as_dict() for plan in self.plans],
            "budget": self.budget.as_dict(),
        }


class OddsCollectionService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    # -- schedule ---------------------------------------------------------
    def _open_first_pitches(
        self, league_code: str, now: datetime, source_modes: tuple[str, ...] = ("LIVE",),
    ) -> list[datetime]:
        """Scheduled LIVE starts still ahead of us, from the canonical schedule.

        This is the zero-credit discovery step: AISP already owns the schedule
        (MLB StatsAPI, nflverse), so knowing which games matter costs nothing.
        """

        horizon = now + timedelta(minutes=MAX_QUOTE_STALENESS_MINUTES + 24 * 60)
        statement = (
            select(Game.starts_at)
            .join(League, League.id == Game.league_id)
            .where(
                League.code == league_code.upper(),
                Game.source_mode.in_(source_modes),
                Game.status.in_((GameStatus.SCHEDULED, GameStatus.LIVE)),
                Game.starts_at > now,
                Game.starts_at <= horizon,
            )
            .order_by(Game.starts_at)
        )
        return [_aware(value) for value in self.session.scalars(statement) if value is not None]

    def _last_success_at(self, sport_key: str) -> datetime | None:
        endpoint = ENDPOINT_KEYS.get(sport_key)
        if endpoint is None:
            return None
        row = self.session.scalar(
            select(ProviderRequestLedger)
            .where(
                ProviderRequestLedger.provider == odds_budget.PROVIDER,
                ProviderRequestLedger.endpoint_key == endpoint,
                ProviderRequestLedger.status == "SUCCEEDED",
            )
            .order_by(ProviderRequestLedger.requested_at.desc())
            .limit(1)
        )
        return _aware(row.requested_at) if row else None

    # -- planning ---------------------------------------------------------
    def plans(self, *, now: datetime | None = None) -> list[SportPlan]:
        now = _aware(now) or datetime.now(UTC)
        built: list[SportPlan] = []
        for league_code in SPORT_POLICIES:
            policy = effective_policy(league_code, self.settings)
            if policy is None:
                continue
            starts = self._open_first_pitches(league_code, now, policy.source_modes)
            built.append(build_sport_plan(policy, starts, now=now))
        built.sort(key=lambda plan: plan.policy.priority)
        return built

    def lane_budget(self, *, now: datetime | None = None):
        from app.services.odds_budget_lanes import lane_budget

        return lane_budget(
            self.session,
            monthly_cap=self.settings.odds_monthly_credit_cap,
            reserve=self.settings.odds_monthly_credit_reserve,
            daily_cap=self.settings.odds_daily_credit_budget,
            floor=self.settings.odds_quota_floor,
            now=now,
            display_timezone=self.settings.display_timezone,
        )

    def budget(self, *, now: datetime | None = None) -> odds_budget.BudgetState:
        return odds_budget.budget_state(
            self.session,
            monthly_cap=self.settings.odds_monthly_credit_cap,
            reserve=self.settings.odds_monthly_credit_reserve,
            daily_cap=self.settings.odds_daily_credit_budget,
            floor=self.settings.odds_quota_floor,
            now=now,
            display_timezone=self.settings.display_timezone,
        )

    # -- recovery ---------------------------------------------------------
    def recovery_request(self, *, now: datetime) -> DueCheckpoint | None:
        """A decision-critical MLB game that still has no usable quote.

        The scheduled cover is sized for the normal case: one observation inside
        each game's freshness window. It cannot answer for a book that went
        quiet, a payload that omitted an event, or a one-sided market. The
        ladder does: coverage is re-checked 90 minutes out, a priority attempt
        is reserved at 45, and one last controlled attempt at 25 -- all safely
        ahead of the 15-minute cutoff, after which nothing can help.

        Recovery draws on its own protected lane, so optional collection cannot
        crowd it out, and it is only requested for a game actually diagnosed
        RECOVERY_REQUIRED.
        """

        from app.services.quote_coverage import (
            RECOVERY_FINAL_MINUTES,
            RECOVERY_PRIMARY_MINUTES,
            STATE_RECOVERY,
            QuoteCoverageService,
        )

        events = QuoteCoverageService(self.session, self.settings).coverage(
            league_code="MLB", now=now,
        )
        # A game is only worth a RECOVERY credit once the scheduled cover has
        # actually had its chance. For the earliest game of a slate the 45-minute
        # rung opens BEFORE its own checkpoint, and firing then would spend the
        # protected reserve doing the scheduled request's job.
        from app.services.odds_collection_plan import (
            CHECKPOINT_GRACE_MINUTES,
            planned_covers,
        )

        mlb_plan = next((p for p in self.plans(now=now) if p.policy.league_code == "MLB"), None)
        pending = list(mlb_plan.checkpoints) if mlb_plan else []

        def scheduled_cover_exhausted(cutoff: datetime) -> bool:
            covering = [c for c in pending if planned_covers(c, cutoff)]
            if not covering:
                return True  # nothing scheduled will serve this game
            return all(
                now > c + timedelta(minutes=CHECKPOINT_GRACE_MINUTES) for c in covering
            )

        at_risk = [
            event for event in events
            if event.state == STATE_RECOVERY
            and event.cutoff - timedelta(minutes=RECOVERY_PRIMARY_MINUTES) <= now < event.cutoff
            and scheduled_cover_exhausted(event.cutoff)
        ]
        if not at_risk:
            return None
        # One league-level request serves every at-risk game at once.
        soonest = min(at_risk, key=lambda event: event.cutoff)
        last_success = self._last_success_at("baseball_mlb")
        rung = (
            RECOVERY_FINAL_MINUTES
            if soonest.cutoff - timedelta(minutes=RECOVERY_FINAL_MINUTES) <= now
            else RECOVERY_PRIMARY_MINUTES
        )
        rung_opens = soonest.cutoff - timedelta(minutes=rung)
        if last_success is not None and last_success >= rung_opens:
            return None  # this rung has already been attempted
        policy = effective_policy("MLB", self.settings)
        if policy is None:
            return None
        return DueCheckpoint(
            league_code="MLB", sport_key=policy.sport_key, checkpoint=rung_opens,
            markets=policy.markets, regions=policy.regions,
            credit_cost=policy.credit_cost, covered_cutoffs=len(at_risk),
            priority=0,  # ahead of every scheduled checkpoint
        )

    def decide(self, *, now: datetime | None = None) -> CollectionDecision:
        now = _aware(now) or datetime.now(UTC)
        plans = self.plans(now=now)
        state = self.budget(now=now)

        def decision(reason: str, detail: str, checkpoint: DueCheckpoint | None = None) -> CollectionDecision:
            return CollectionDecision(
                reason=reason, checkpoint=checkpoint, budget=state, plans=plans, detail=detail,
            )

        if not self.settings.odds_checkpoint_collection_enabled:
            return decision(
                REASON_DISABLED,
                "ODDS_CHECKPOINT_COLLECTION_ENABLED is false; no provider request is made.",
            )
        if not self.settings.the_odds_api_key:
            return decision(REASON_NO_CREDENTIAL, "THE_ODDS_API_KEY is not configured.")
        if state.floor_latched:
            return decision(
                REASON_FLOOR,
                f"Provider balance {state.provider_remaining} is at or below the floor "
                f"of {state.floor}. The floor is never lowered to make a request possible.",
            )
        if not any(plan.cutoffs for plan in plans):
            return decision(
                REASON_NO_GAMES,
                "No scheduled game has a decision cutoff still ahead; an inactive sport is "
                "never polled.",
            )

        lanes = self.lane_budget(now=now)

        # A game already diagnosed RECOVERY_REQUIRED outranks every scheduled
        # checkpoint and spends from the protected recovery lane.
        recovery = self.recovery_request(now=now)
        if recovery is not None:
            allowed, refusal = lanes.may_spend(LANE_MLB_RECOVERY, recovery.credit_cost)
            if allowed:
                return decision(
                    REASON_RECOVERY,
                    f"{recovery.covered_cutoffs} MLB game(s) have no usable quote and their "
                    f"cutoff is near; recovery attempt at {recovery.checkpoint.isoformat()} "
                    f"for {recovery.credit_cost} credit(s) from the protected reserve.",
                    recovery,
                )
            return decision(REASON_BUDGET, refusal or "Recovery reserve exhausted.", recovery)

        for plan in plans:
            if not plan.checkpoints:
                continue
            from app.services.odds_collection_plan import due_checkpoint

            due = due_checkpoint(
                plan, now=now, last_success_at=self._last_success_at(plan.policy.sport_key),
            )
            if due is None:
                continue
            lane = lane_for_request(
                league_code=plan.policy.league_code, markets=plan.policy.markets,
            )
            allowed, refusal = lanes.may_spend(lane, due.credit_cost)
            if not allowed:
                if plan.policy.priority == 1:
                    # MLB could not be served; nothing lower may slip past it.
                    return decision(REASON_BUDGET, refusal or "Budget exhausted.", due)
                continue  # an optional lane yields quietly and the loop moves on
            return decision(
                REASON_DUE,
                f"{plan.policy.league_code} checkpoint {due.checkpoint.isoformat()} covers "
                f"{due.covered_cutoffs} decision cutoff(s) for {due.credit_cost} credit(s) "
                f"from the {lane} lane.",
                due,
            )
        return decision(
            REASON_NOT_DUE,
            "Every open game already has a request covering its freshness window; a "
            "duplicate request would buy nothing.",
        )

    # -- operator view ----------------------------------------------------
    def status(self, *, now: datetime | None = None) -> dict[str, Any]:
        now = _aware(now) or datetime.now(UTC)
        decision = self.decide(now=now)
        state = decision.budget
        planned_daily = sum(plan.projected_credits for plan in decision.plans)
        mode = odds_budget.collection_mode(
            polling_enabled=self.settings.odds_checkpoint_collection_enabled, state=state,
        )
        return {
            "schema_version": "AISP_ODDS_OPERATOR_STATUS_V1",
            "generated_at_utc": now.isoformat(),
            "collection_mode": mode,
            "collection_reason": decision.reason,
            "collection_detail": decision.detail,
            "legacy_blanket_polling_enabled": self.settings.odds_polling_enabled,
            "budget": state.as_dict(),
            "projection": state.projected_exhaustion(
                planned_daily_credits=float(planned_daily) if planned_daily else None,
            ),
            "planned_credits_remaining_today": planned_daily,
            "plans": [plan.as_dict() for plan in decision.plans],
            "next_checkpoint": decision.checkpoint.as_dict() if decision.checkpoint else None,
        }


__all__ = [
    "ENDPOINT_KEYS",
    "REASON_BUDGET",
    "REASON_DISABLED",
    "REASON_DUE",
    "REASON_FLOOR",
    "REASON_NOT_DUE",
    "REASON_NO_CREDENTIAL",
    "REASON_NO_GAMES",
    "REASON_RECOVERY",
    "CollectionDecision",
    "OddsCollectionService",
    "effective_policy",
    "parse_market_overrides",
]

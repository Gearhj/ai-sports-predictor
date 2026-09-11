"""Why a game never got a usable price, counted once and only once.

THE QUESTION THIS ANSWERS
-------------------------
Fifteen games are scheduled and one position exists. Where did the other
fourteen go? Until now that could only be reconstructed by hand, and every
partial answer ("the collector ran", "we have some odds") was true and useless.

A funnel answers it, provided it obeys one rule: EVERY GAME IS COUNTED EXACTLY
ONCE, at the FIRST stage it failed. A game with no canonical event also has no
forecast and no quote and no edge; charging it to all four makes the reasons
sum to more than the slate and turns the diagnosis into noise. It is charged to
the mapping, and to nothing else.

    Scheduled -> Forecasted -> Complete quote -> Fresh through cutoff
              -> Above threshold -> Positioned

Each arrow is a gate. A game that clears every gate is positioned; a game that
stops somewhere carries the reason for exactly that stop.

WHERE THE THRESHOLD COMES FROM
------------------------------
The "above threshold" gate reads the ACTIVE EXPERIMENT'S OWN minimum edge. It
is not a constant in this file, and nothing here changes it. A second sport or
a second policy with a different minimum is compared against its own.

WHAT IT DOES NOT DO
-------------------
It reads. It makes no provider request, writes no row, creates no decision and
moves no threshold. It is a diagnosis of what already happened.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ExternalIdMapping, Forecast, Game, GameStatus, League, OfficialAspPosition,
    Season, ShadowExperimentDecision,
)
from app.services.quote_coverage import (
    CAUSE_EXPLANATIONS, MAX_QUOTE_STALENESS_MINUTES, QuoteCoverageService, decision_cutoff,
)

SCHEMA_VERSION = "AISP_PRICE_COVERAGE_FUNNEL_V1"

#: The gates, in the order a game must clear them.
STAGE_SCHEDULED = "SCHEDULED"
STAGE_FORECASTED = "FORECASTED"
STAGE_COMPLETE_QUOTE = "COMPLETE_QUOTE"
STAGE_FRESH_THROUGH_CUTOFF = "FRESH_THROUGH_CUTOFF"
STAGE_ABOVE_THRESHOLD = "ABOVE_THRESHOLD"
STAGE_POSITIONED = "POSITIONED"

STAGES = (
    STAGE_SCHEDULED, STAGE_FORECASTED, STAGE_COMPLETE_QUOTE,
    STAGE_FRESH_THROUGH_CUTOFF, STAGE_ABOVE_THRESHOLD, STAGE_POSITIONED,
)

STAGE_LABELS = {
    STAGE_SCHEDULED: "Scheduled",
    STAGE_FORECASTED: "Forecasted",
    STAGE_COMPLETE_QUOTE: "Complete quote",
    STAGE_FRESH_THROUGH_CUTOFF: "Fresh through cutoff",
    STAGE_ABOVE_THRESHOLD: "Above threshold",
    STAGE_POSITIONED: "Positioned",
}

#: First-blocking reasons. The collector's own causes are reused verbatim where
#: they already exist; the rest name gates the collector never reaches.
REASON_NO_MAPPING = "NO_CANONICAL_EVENT_MAPPING"
REASON_NO_FORECAST = "NO_MODEL_FORECAST"
REASON_PAST_CUTOFF = "PAST_CUTOFF_BEFORE_ANY_QUOTE"
REASON_ONE_SIDE = "ONLY_ONE_SIDE_AVAILABLE"
REASON_SUSPENDED = "MARKET_SUSPENDED"
REASON_EXPIRES_BEFORE_CUTOFF = "QUOTE_WOULD_EXPIRE_BEFORE_CUTOFF"
REASON_AFTER_CUTOFF = "QUOTE_ARRIVED_AFTER_CUTOFF"
REASON_FALLBACK_COOLDOWN = "FALLBACK_COOLDOWN"
REASON_BELOW_THRESHOLD = "BELOW_THRESHOLD"
REASON_RESEARCH_ONLY = "RESEARCH_ONLY_NO_WAGERING_AUTHORITY"
REASON_NOT_POSITIONED = "QUALIFIED_BUT_NO_POSITION_RECORDED"
REASON_AWAITING_CUTOFF = "QUALIFIED_AND_AWAITING_ITS_CUTOFF"
REASON_WINDOW_NOT_OPEN = "FRESHNESS_WINDOW_HAS_NOT_OPENED_YET"
REASON_OTHER = "OTHER_TECHNICAL_FAILURE"

REASON_EXPLANATIONS: dict[str, str] = {
    REASON_NO_MAPPING:
        "No canonical AISP event exists for this game, so no collector could address it "
        "and nothing downstream could run. Registering the event is the fix, not a price.",
    REASON_NO_FORECAST:
        "No model forecast was published for this game, so there was no probability to "
        "compare a price against. A price alone decides nothing.",
    REASON_PAST_CUTOFF:
        "The decision cutoff passed with no usable quote. Nothing can be recovered for this "
        "game; it is counted so the day's total stays honest.",
    REASON_ONE_SIDE:
        "One side of the moneyline was priced and the other was not. A one-sided price "
        "cannot produce a no-vig probability, so it is not a usable quote.",
    REASON_SUSPENDED:
        "Every observation in the window was suspended. A suspended price is not an "
        "offer and is never treated as one.",
    REASON_EXPIRES_BEFORE_CUTOFF:
        "A complete quote exists but would be older than the staleness limit by the time "
        "the cutoff arrives, so it cannot price a decision at the cutoff.",
    REASON_AFTER_CUTOFF:
        "The only complete quote arrived after the decision cutoff. A price observed after "
        "the deadline cannot be used for a decision taken before it.",
    REASON_FALLBACK_COOLDOWN:
        "The fallback provider was inside its cooldown, so no second request was permitted "
        "for this event in the window.",
    REASON_BELOW_THRESHOLD:
        "A fresh, complete quote existed and the adjusted edge did not reach the active "
        "policy's minimum. This is the policy working, not a coverage failure.",
    REASON_RESEARCH_ONLY:
        "This sport is research-only and cannot create a position regardless of edge.",
    REASON_NOT_POSITIONED:
        "The selection cleared the threshold, its cutoff has passed and no position row "
        "exists. This is the only reason on the list that indicates something went wrong "
        "after the decision.",
    REASON_AWAITING_CUTOFF:
        "The selection clears the threshold and its cutoff has not arrived yet. Nothing is "
        "wrong: the position is created at the cutoff, not before it.",
    REASON_WINDOW_NOT_OPEN:
        "The freshness window for this game has not opened yet, so no quote could be usable "
        "at the cutoff regardless of what has been collected. Nothing has failed; the "
        "collector has not been asked for this game yet.",
    REASON_OTHER: "A named technical failure outside the classified set.",
    **{key: value for key, value in CAUSE_EXPLANATIONS.items()},
}


@dataclass
class GameOutcome:
    """One game, the furthest gate it cleared, and the first that stopped it."""

    game_id: str
    matchup: str
    starts_at: str
    cutoff: str
    reached: str
    blocked_at: str | None
    reason: str | None
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "game_id": self.game_id, "matchup": self.matchup,
            "starts_at": self.starts_at, "cutoff": self.cutoff,
            "reached": self.reached, "blocked_at": self.blocked_at,
            "reason": self.reason,
            "explanation": REASON_EXPLANATIONS.get(self.reason or "", None),
            **self.detail,
        }


class PriceCoverageFunnel:
    """Distinct-game coverage accounting with one reason per game."""

    def __init__(self, session: Session, settings: Any) -> None:
        self.session = session
        self.settings = settings
        self.coverage_service = QuoteCoverageService(session, settings)

    def build(
        self, *, event_date: date | None = None, league_code: str = "MLB",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        from app.core.time import local_day_bounds, local_today

        now = now or datetime.now(UTC)
        event_date = event_date or local_today(self.settings.display_timezone)
        start, end = local_day_bounds(event_date, self.settings.display_timezone)

        games = list(self.session.scalars(
            select(Game)
            .join(Season, Season.id == Game.season_id)
            .join(League, League.id == Season.league_id)
            .where(League.code == league_code, Game.starts_at >= start, Game.starts_at < end)
            .order_by(Game.starts_at)
        ))
        game_ids = {game.id for game in games}
        matchups = self.coverage_service._matchups(game_ids) if game_ids else {}

        mapped = {
            mapping.entity_id for mapping in self.session.scalars(
                select(ExternalIdMapping).where(
                    ExternalIdMapping.entity_type == "game",
                    ExternalIdMapping.source == "MLB_STATS_API",
                    ExternalIdMapping.entity_id.in_(list(game_ids) or [None]),
                )
            )
        }
        forecasted = {
            value for value in self.session.scalars(
                select(Forecast.game_id).where(Forecast.game_id.in_(list(game_ids) or [None]))
            )
        }
        coverage = {entry.game_id: entry for entry in self.coverage_service.coverage(
            league_code=league_code, slate_date=event_date, now=now,
        )} if game_ids else {}

        decisions = self._decisions(game_ids)
        positioned = {
            value for value in self.session.scalars(
                select(OfficialAspPosition.forecast_id).where(
                    OfficialAspPosition.shadow_decision_id.is_not(None)
                )
            )
        }
        positioned_games = self._positioned_games(game_ids)
        minimum_edge, policy = self._active_minimum_edge()
        research_only = self._research_only(league_code)

        outcomes: list[GameOutcome] = []
        for game in games:
            outcomes.append(self._classify(
                game=game, matchup=matchups.get(game.id, str(game.id)), now=now,
                mapped=game.id in mapped, forecasted=game.id in forecasted,
                entry=coverage.get(game.id), decision=decisions.get(game.id),
                positioned=game.id in positioned_games,
                minimum_edge=minimum_edge, research_only=research_only,
            ))

        counts = {stage: 0 for stage in STAGES}
        for outcome in outcomes:
            index = STAGES.index(outcome.reached)
            for stage in STAGES[: index + 1]:
                counts[stage] += 1

        reasons = Counter(
            outcome.reason for outcome in outcomes if outcome.reason is not None
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "league": league_code,
            "event_date": event_date.isoformat(),
            "generated_at_utc": now.isoformat(),
            "minimum_edge_percentage": minimum_edge,
            "policy": policy,
            "research_only": research_only,
            "stages": [
                {"stage": stage, "label": STAGE_LABELS[stage], "games": counts[stage],
                 "dropped": counts[STAGES[index - 1]] - counts[stage] if index else 0}
                for index, stage in enumerate(STAGES)
            ],
            # Every game appears exactly once, so these sum to the games that
            # did not reach the end.
            "first_blocking_reasons": [
                {"reason": reason, "games": count,
                 "explanation": REASON_EXPLANATIONS.get(reason)}
                for reason, count in reasons.most_common()
            ],
            "reasons_sum": sum(reasons.values()),
            "games": [outcome.as_dict() for outcome in outcomes],
            "provider_calls": 0,
            "positions_created": 0,
        }

    # -- one game ----------------------------------------------------------
    def _classify(
        self, *, game: Game, matchup: str, now: datetime, mapped: bool, forecasted: bool,
        entry: Any, decision: Any, positioned: bool, minimum_edge: float, research_only: bool,
    ) -> GameOutcome:
        starts_at = game.starts_at if game.starts_at.tzinfo else game.starts_at.replace(tzinfo=UTC)
        cutoff = decision_cutoff(starts_at)
        base = {
            "game_id": str(game.id), "matchup": matchup,
            "starts_at": starts_at.isoformat(), "cutoff": cutoff.isoformat(),
        }

        # Gate 1: an event AISP can address at all.
        if not mapped:
            return GameOutcome(**base, reached=STAGE_SCHEDULED,
                               blocked_at=STAGE_FORECASTED, reason=REASON_NO_MAPPING)

        # Gate 2: something to compare a price against.
        if not forecasted:
            return GameOutcome(**base, reached=STAGE_SCHEDULED,
                               blocked_at=STAGE_FORECASTED, reason=REASON_NO_FORECAST)

        # Gate 3: a complete two-sided quote at one book.
        if entry is None or not entry.qualifying:
            reason, detail = self._quote_reason(entry, now=now, cutoff=cutoff)
            return GameOutcome(**base, reached=STAGE_FORECASTED,
                               blocked_at=STAGE_COMPLETE_QUOTE, reason=reason, detail=detail)

        # Gate 4: still fresh at the cutoff.
        if not entry.usable_through_cutoff:
            return GameOutcome(
                **base, reached=STAGE_COMPLETE_QUOTE, blocked_at=STAGE_FRESH_THROUGH_CUTOFF,
                reason=REASON_EXPIRES_BEFORE_CUTOFF,
                detail={"best_quote_age_minutes_at_cutoff": entry.age_at_cutoff},
            )

        # Gate 5: the active policy's own minimum.
        edge = self._edge_of(decision)
        if edge is None:
            return GameOutcome(
                **base, reached=STAGE_FRESH_THROUGH_CUTOFF, blocked_at=STAGE_ABOVE_THRESHOLD,
                reason=REASON_BELOW_THRESHOLD,
                detail={"adjusted_edge_percentage": None,
                        "minimum_edge_percentage": minimum_edge,
                        "note": "No priced decision row exists for this game."},
            )
        if edge < minimum_edge:
            return GameOutcome(
                **base, reached=STAGE_FRESH_THROUGH_CUTOFF, blocked_at=STAGE_ABOVE_THRESHOLD,
                reason=REASON_BELOW_THRESHOLD,
                detail={"adjusted_edge_percentage": round(edge, 4),
                        "minimum_edge_percentage": minimum_edge},
            )

        # Gate 6: a recorded position.
        if research_only:
            return GameOutcome(
                **base, reached=STAGE_ABOVE_THRESHOLD, blocked_at=STAGE_POSITIONED,
                reason=REASON_RESEARCH_ONLY,
                detail={"adjusted_edge_percentage": round(edge, 4)},
            )
        if not positioned:
            # A pregame qualifier has not failed; its position is created AT the
            # cutoff. Calling that a failure would report a healthy slate as
            # broken every afternoon.
            pending = now < cutoff
            return GameOutcome(
                **base, reached=STAGE_ABOVE_THRESHOLD, blocked_at=STAGE_POSITIONED,
                reason=REASON_AWAITING_CUTOFF if pending else REASON_NOT_POSITIONED,
                detail={"adjusted_edge_percentage": round(edge, 4),
                        "cutoff_passed": not pending},
            )
        return GameOutcome(
            **base, reached=STAGE_POSITIONED, blocked_at=None, reason=None,
            detail={"adjusted_edge_percentage": round(edge, 4)},
        )

    def _quote_reason(self, entry: Any, *, now: datetime, cutoff: datetime) -> tuple[str, dict[str, Any]]:
        """The first reason a usable quote does not exist, most specific first."""

        if entry is None:
            return REASON_OTHER, {"note": "No coverage record was derived for this game."}
        detail: dict[str, Any] = {
            "coverage_state": entry.state,
            "partial_observations_in_window": entry.partial_observations,
            "complete_quotes_out_of_window": len(entry.out_of_window),
        }
        # Nothing can be wrong before the window opens. Reporting a 7pm game as
        # a coverage failure at lunchtime would make the funnel cry wolf every
        # single afternoon, and a funnel nobody believes diagnoses nothing.
        window_start = cutoff - timedelta(minutes=MAX_QUOTE_STALENESS_MINUTES)
        if now < window_start:
            detail["window_opens_utc"] = window_start.isoformat()
            return REASON_WINDOW_NOT_OPEN, detail
        # A complete quote that exists but sits outside the window is a
        # different failure from never having one, and the side of the window
        # it falls on changes what could have been done about it.
        if entry.out_of_window:
            latest = max(quote.observed_at for quote in entry.out_of_window)
            detail["latest_complete_quote_utc"] = latest.isoformat()
            if latest > cutoff:
                return REASON_AFTER_CUTOFF, detail
            return REASON_EXPIRES_BEFORE_CUTOFF, detail
        if entry.partial_observations:
            # Prices arrived and never paired at one book.
            return REASON_ONE_SIDE, detail
        if now > cutoff:
            return REASON_PAST_CUTOFF, detail
        return (entry.cause or REASON_OTHER), detail

    # -- inputs ------------------------------------------------------------
    def _decisions(self, game_ids: set[Any]) -> dict[Any, Any]:
        """The best priced decision per game, by adjusted edge."""

        if not game_ids:
            return {}
        best: dict[Any, Any] = {}
        for decision in self.session.scalars(
            select(ShadowExperimentDecision).where(
                ShadowExperimentDecision.game_id.in_(list(game_ids))
            )
        ):
            edge = self._edge_of(decision)
            if edge is None:
                continue
            current = best.get(decision.game_id)
            if current is None or (self._edge_of(current) or -999) < edge:
                best[decision.game_id] = decision
        return best

    @staticmethod
    def _edge_of(decision: Any) -> float | None:
        if decision is None:
            return None
        evidence = getattr(decision, "evidence", None)
        if isinstance(evidence, dict):
            value = evidence.get("adjusted_edge")
            if isinstance(value, (int, float)):
                # Persisted as a fraction; the threshold is a percentage.
                return float(value) * 100 if abs(float(value)) <= 1 else float(value)
        value = getattr(decision, "adjusted_edge", None)
        if isinstance(value, (int, float)):
            return float(value) * 100 if abs(float(value)) <= 1 else float(value)
        return None

    def _positioned_games(self, game_ids: set[Any]) -> set[Any]:
        if not game_ids:
            return set()
        rows = self.session.execute(
            select(ShadowExperimentDecision.game_id)
            .join(OfficialAspPosition,
                  OfficialAspPosition.shadow_decision_id == ShadowExperimentDecision.id)
            .where(ShadowExperimentDecision.game_id.in_(list(game_ids)))
        ).all()
        return {row[0] for row in rows}

    def _active_minimum_edge(self) -> tuple[float, str | None]:
        """The ACTIVE experiment's own minimum. Never a constant in this file."""

        from app.services.shadow_experiment_service import (
            V2_DECISION_POLICY, ShadowExperimentService,
        )

        # `shadow_qualified_edge` is the official minimum this deployment
        # runs, expressed as a fraction. It is READ here and never written:
        # this module diagnoses coverage and has no business moving a gate.
        default = float(self.settings.shadow_qualified_edge) * 100
        service = ShadowExperimentService(self.session, self.settings)
        experiment = service.active_experiment(V2_DECISION_POLICY)
        if experiment is None:
            experiment = service.active_experiment()
        if experiment is None:
            return default, None
        config = getattr(experiment, "policy_config", None) or {}
        minimum = config.get("minimum_edge_percentage") if isinstance(config, dict) else None
        if not isinstance(minimum, (int, float)):
            minimum = default
        return float(minimum), experiment.decision_policy

    def _research_only(self, league_code: str) -> bool:
        return league_code.upper() != "MLB"


__all__ = [
    "PriceCoverageFunnel", "REASON_EXPLANATIONS", "SCHEMA_VERSION", "STAGES",
    "STAGE_ABOVE_THRESHOLD", "STAGE_COMPLETE_QUOTE", "STAGE_FORECASTED",
    "STAGE_FRESH_THROUGH_CUTOFF", "STAGE_LABELS", "STAGE_POSITIONED", "STAGE_SCHEDULED",
    "REASON_AFTER_CUTOFF", "REASON_BELOW_THRESHOLD", "REASON_EXPIRES_BEFORE_CUTOFF",
    "REASON_FALLBACK_COOLDOWN", "REASON_NOT_POSITIONED", "REASON_NO_FORECAST",
    "REASON_NO_MAPPING", "REASON_ONE_SIDE", "REASON_OTHER", "REASON_PAST_CUTOFF",
    "REASON_RESEARCH_ONLY", "REASON_SUSPENDED", "REASON_AWAITING_CUTOFF",
    "REASON_WINDOW_NOT_OPEN",
]

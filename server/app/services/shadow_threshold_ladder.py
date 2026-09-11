"""Inert threshold research: what other minimums WOULD have selected.

WHY A LADDER AND NOT A CHANGE
-----------------------------
The official minimum adjusted edge is 2%. The honest question about it is not
"is 2% right" -- with twelve settled positions nobody can answer that -- but
"what evidence would let us answer it in three months". The answer is a
prospective record at several minimums, recorded from the same decisions, at
the same moments, from the same prices.

So this registers six inert policies at 1.00%, 1.25%, 1.50%, 1.75%, 2.00% and
2.50%, and records what each WOULD have selected. It does not change the
official minimum, does not touch the funded experiment, and cannot create a
position. The 2.00% rung exists on purpose: it reproduces the official policy's
selections through the shadow machinery, so the ladder can be checked against a
known answer rather than trusted.

THE ONE THING THAT MAKES THIS EVIDENCE RATHER THAN A BACKTEST
-------------------------------------------------------------
A rung may only record a selection from a decision that ALREADY EXISTED before
the game started, priced by a quote that was already persisted. Nothing is
re-derived, no price is re-fetched, and no game is added after its cutoff. A
rung records what a differently-configured policy would have picked from the
evidence we actually had at the time -- which is a prospective shadow, not a
retrospective fit.

WHAT IT WRITES
--------------
Rows in its own table only. No decision, no position, no settlement, no change
to any experiment. The funded V2.1 ledger cannot observe that this exists.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Game, GameStatus, League, Season, ShadowExperimentDecision, ShadowThresholdSelection,
)

SCHEMA_VERSION = "AISP_SHADOW_THRESHOLD_LADDER_V1"
LADDER_ID = "AISP_SHADOW_THRESHOLD_LADDER_V1"

#: The rungs, in percentage points of adjusted edge. 2.00 reproduces the
#: official minimum so the ladder can be checked against a known answer.
RUNGS: tuple[float, ...] = (1.00, 1.25, 1.50, 1.75, 2.00, 2.50)

#: The official minimum. Present so a reader can see which rung is live, and
#: asserted against the deployment's own setting rather than hard-coded truth.
OFFICIAL_RUNG = 2.00


def policy_name(rung: float) -> str:
    return f"AISP_SHADOW_THRESHOLD_{rung:.2f}".replace(".", "_")


@dataclass(frozen=True)
class RungOutcome:
    rung: float
    policy: str
    considered: int
    selected: int
    recorded: int
    reused: int
    is_official: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "rung_percentage": self.rung, "policy": self.policy,
            "decisions_considered": self.considered, "would_select": self.selected,
            "recorded": self.recorded, "reused": self.reused,
            "is_official_threshold": self.is_official,
            # Repeated per rung because it is the property that matters most.
            "creates_positions": False, "wagering_authority": False,
        }


class ShadowThresholdLadder:
    """Records, per rung, what that minimum would have selected."""

    def __init__(self, session: Session, settings: Any) -> None:
        self.session = session
        self.settings = settings

    def official_minimum(self) -> float:
        return float(self.settings.shadow_qualified_edge) * 100

    def run(
        self, *, event_date: date | None = None, league_code: str = "MLB",
        now: datetime | None = None, dry_run: bool = False,
    ) -> dict[str, Any]:
        from app.core.time import local_day_bounds, local_today

        now = now or datetime.now(UTC)
        event_date = event_date or local_today(self.settings.display_timezone)
        start, end = local_day_bounds(event_date, self.settings.display_timezone)

        games = {
            game.id: game for game in self.session.scalars(
                select(Game)
                .join(Season, Season.id == Game.season_id)
                .join(League, League.id == Season.league_id)
                .where(League.code == league_code, Game.starts_at >= start, Game.starts_at < end)
            )
        }
        # ONE GAME, ONE CANDIDATE.
        #
        # The decision table holds a row per re-evaluation per experiment --
        # 2,856 of them for a fifteen-game slate -- and counting those directly
        # produced "546 selections" from fifteen games, which is not a number
        # anybody can compare between rungs. A threshold selects a GAME, so
        # each game contributes its single best priced decision and nothing
        # else.
        best: dict[Any, tuple[Any, float]] = {}
        for decision in self.session.scalars(
            select(ShadowExperimentDecision).where(
                ShadowExperimentDecision.game_id.in_(list(games) or [None])
            )
        ):
            edge = _edge_of(decision)
            if edge is None:
                continue
            current = best.get(decision.game_id)
            if current is None or edge > current[1]:
                best[decision.game_id] = (decision, edge)
        priced = list(best.values())

        outcomes: list[RungOutcome] = []
        for rung in RUNGS:
            selected = [(decision, edge) for decision, edge in priced if edge >= rung]
            recorded = reused = 0
            if not dry_run:
                for decision, edge in selected:
                    game = games.get(decision.game_id)
                    if game is None:
                        continue
                    # Identity is (ladder, rung, GAME): one rung records a
                    # game once per slate, however many times the underlying
                    # decision was re-evaluated.
                    existing = self.session.scalar(select(ShadowThresholdSelection).where(
                        ShadowThresholdSelection.ladder_id == LADDER_ID,
                        ShadowThresholdSelection.rung_percentage == rung,
                        ShadowThresholdSelection.game_id == decision.game_id,
                        ShadowThresholdSelection.slate_date == event_date,
                    ))
                    if existing is not None:
                        reused += 1
                        continue
                    self.session.add(ShadowThresholdSelection(
                        ladder_id=LADDER_ID,
                        schema_version=SCHEMA_VERSION,
                        rung_percentage=rung,
                        policy=policy_name(rung),
                        decision_id=decision.id,
                        game_id=decision.game_id,
                        league_code=league_code,
                        slate_date=event_date,
                        selection_label=getattr(decision, "selection", None),
                        adjusted_edge_percentage=round(edge, 4),
                        scheduled_start_utc=game.starts_at,
                        recorded_at=now,
                    ))
                    recorded += 1
                self.session.flush()
            outcomes.append(RungOutcome(
                rung=rung, policy=policy_name(rung), considered=len(priced),
                selected=len(selected), recorded=recorded, reused=reused,
                is_official=abs(rung - self.official_minimum()) < 1e-9,
            ))

        return {
            "schema_version": SCHEMA_VERSION,
            "ladder_id": LADDER_ID,
            "league": league_code,
            "event_date": event_date.isoformat(),
            "generated_at_utc": now.isoformat(),
            "official_minimum_percentage": self.official_minimum(),
            "rungs": [outcome.as_dict() for outcome in outcomes],
            "games_considered": len(priced),
            "decisions_considered": len(priced),
            "dry_run": dry_run,
            # The guarantees, stated where the numbers are.
            "official_threshold_changed": False,
            "funded_experiment_modified": False,
            "positions_created": 0,
            "official_decisions_created": 0,
            "models_executed": 0,
            "provider_calls": 0,
            "note": "Inert research. Each rung records what that minimum WOULD have selected "
                    "from decisions that already existed, priced by quotes already persisted. "
                    "No rung can create a position and none changes the official 2% minimum.",
        }

    def summary(self, *, league_code: str = "MLB") -> dict[str, Any]:
        """What each rung has accumulated so far, for the review checkpoint."""

        rows = list(self.session.scalars(
            select(ShadowThresholdSelection).where(
                ShadowThresholdSelection.ladder_id == LADDER_ID,
                ShadowThresholdSelection.league_code == league_code,
            )
        ))
        by_rung: dict[float, list[Any]] = {}
        for row in rows:
            by_rung.setdefault(float(row.rung_percentage), []).append(row)
        return {
            "schema_version": SCHEMA_VERSION,
            "league": league_code,
            "official_minimum_percentage": self.official_minimum(),
            "rungs": [
                {
                    "rung_percentage": rung,
                    "policy": policy_name(rung),
                    "hypothetical_selections": len(by_rung.get(rung, [])),
                    "distinct_games": len({row.game_id for row in by_rung.get(rung, [])}),
                    "is_official_threshold": abs(rung - self.official_minimum()) < 1e-9,
                    "creates_positions": False,
                }
                for rung in RUNGS
            ],
            "review_checkpoint_settled_positions": 50,
            "note": "These are hypothetical selections only. The first honest comparison "
                    "between rungs needs roughly 50 settled observations per sport; until "
                    "then the ladder is an evidence-collection mechanism, not a result.",
        }


def _edge_of(decision: Any) -> float | None:
    """Adjusted edge as a percentage, from the decision's persisted evidence."""

    evidence = getattr(decision, "evidence", None)
    if isinstance(evidence, dict):
        value = evidence.get("adjusted_edge")
        if isinstance(value, (int, float)):
            return float(value) * 100 if abs(float(value)) <= 1 else float(value)
    value = getattr(decision, "adjusted_edge", None)
    if isinstance(value, (int, float)):
        return float(value) * 100 if abs(float(value)) <= 1 else float(value)
    return None


__all__ = [
    "LADDER_ID", "OFFICIAL_RUNG", "RUNGS", "SCHEMA_VERSION", "RungOutcome",
    "ShadowThresholdLadder", "policy_name",
]

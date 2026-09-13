"""One explanation service, used by Daily Decisions and the settled surfaces.

Deliberately one service rather than two. The same wager appears on Daily
Decisions before it settles and on the experiment and research surfaces after,
and if each surface built its own sentences they would drift -- which is exactly
how a rationale ends up being quietly rewritten by hindsight. Building both from
the same decision row is what makes the before and after texts identical.

This is a pure read. It derives presentation data from retained evidence and
writes nothing: no forecast, decision, threshold, position, stake, settlement or
bankroll is touched, and no provider is called when a panel opens.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.explain.outcome import explain_outcome
from app.explain.wager import explain_wager
from app.models import (
    Game,
    GameFinalOutcome,
    GameTeam,
    OfficialAspPosition,
    PositionSettlement,
    ShadowExperimentDecision,
    ShadowForecast,
    Team,
)

SCHEMA_VERSION = "AISP_DECISION_EXPLANATION_V1"


class ExplanationService:
    def __init__(self, session: Session) -> None:
        self.session = session

    # -- public ------------------------------------------------------------

    def for_decision(self, decision_id: uuid.UUID) -> dict[str, Any] | None:
        decision = self.session.get(ShadowExperimentDecision, decision_id)
        if decision is None:
            return None
        return self._build(decision)

    def for_position(self, position_id: uuid.UUID) -> dict[str, Any] | None:
        position = self.session.get(OfficialAspPosition, position_id)
        if position is None or position.shadow_decision_id is None:
            return None
        decision = self.session.get(
            ShadowExperimentDecision, position.shadow_decision_id)
        if decision is None:
            return None
        return self._build(decision, position=position)

    #: Which decision speaks for a game, when several phases recorded one.
    #: A recorded wager always wins; otherwise the strongest recommendation, and
    #: among equals the latest. Picking arbitrarily would let a MORNING
    #: PROVISIONAL_EDGE explain a game that later qualified and was funded.
    _RECOMMENDATION_RANK = {
        "QUALIFIED_BET": 3, "PROVISIONAL_EDGE": 2, "WATCH": 1, "NO_BET": 0,
    }

    def for_slate(self, slate_date: Any) -> list[dict[str, Any]]:
        """One explanation per game on a slate, not one per phase snapshot.

        A slate records a decision row for every game at every phase -- 1,682 of
        them on 2026-09-12. Returning all of them would make the caller choose
        which one speaks for a game, and a caller choosing arbitrarily is how a
        superseded morning note ends up explaining a funded wager.
        """

        decisions = list(self.session.scalars(
            select(ShadowExperimentDecision)
            .where(ShadowExperimentDecision.slate_date == slate_date)
            .order_by(ShadowExperimentDecision.recommended_at)
        ))
        if not decisions:
            return []
        positions = {
            position.shadow_decision_id: position
            for position in self.session.scalars(
                select(OfficialAspPosition).where(
                    OfficialAspPosition.shadow_decision_id.in_(
                        [decision.id for decision in decisions])
                )
            )
        }
        chosen: dict[Any, Any] = {}
        for decision in decisions:
            current = chosen.get(decision.game_id)
            if current is None or self._outranks(decision, current, positions):
                chosen[decision.game_id] = decision
        return [
            self._build(decision, position=positions.get(decision.id))
            for decision in sorted(
                chosen.values(), key=lambda row: row.recommended_at)
        ]

    def _outranks(self, candidate: Any, incumbent: Any, positions: dict) -> bool:
        """Whether ``candidate`` should speak for the game instead."""

        candidate_funded = candidate.id in positions
        incumbent_funded = incumbent.id in positions
        if candidate_funded != incumbent_funded:
            return candidate_funded
        rank = self._RECOMMENDATION_RANK
        candidate_rank = rank.get(str(candidate.recommendation), -1)
        incumbent_rank = rank.get(str(incumbent.recommendation), -1)
        if candidate_rank != incumbent_rank:
            return candidate_rank > incumbent_rank
        return candidate.recommended_at > incumbent.recommended_at

    # -- assembly ----------------------------------------------------------

    def _build(
        self, decision: Any, *, position: Any | None = None,
    ) -> dict[str, Any]:
        if position is None:
            position = self.session.scalar(
                select(OfficialAspPosition).where(
                    OfficialAspPosition.shadow_decision_id == decision.id)
            )
        settlement = None
        if position is not None:
            settlement = self.session.scalar(
                select(PositionSettlement).where(
                    PositionSettlement.position_id == position.id)
            )

        forecast = self._bound_forecast(decision)
        matchup = self._matchup(decision.game_id)
        final = self._governed_final(decision.game_id) or _settlement_final(settlement)

        why = explain_wager(
            decision=decision, position=position, forecast=forecast, matchup=matchup)
        what = explain_outcome(
            decision=decision,
            position=position,
            settlement=settlement,
            final=final,
            forecast_distributions=(
                dict(forecast.distributions or {}) if forecast is not None else None),
            matchup=matchup,
            # No play-level or player-level rows are retained, so nothing is
            # passed here today. The parameter exists so that when that evidence
            # is collected the outcome panel uses it without a second design.
            event_evidence=self._event_evidence(decision.game_id),
        )

        return {
            "schema_version": SCHEMA_VERSION,
            "decision_id": str(decision.id),
            "position_id": str(position.id) if position is not None else None,
            "game_id": str(decision.game_id),
            "slate_date": decision.slate_date.isoformat(),
            "matchup": matchup,
            "recorded_wager": position is not None,
            "why": why,
            "what_happened": what,
            "derived_only": (
                "Derived presentation data. Nothing here modifies a forecast, "
                "decision, threshold, position, stake, settlement or bankroll."
            ),
        }

    # -- retained evidence -------------------------------------------------

    def _bound_forecast(self, decision: Any) -> ShadowForecast | None:
        """The exact forecast this decision bound, never a later one."""

        if decision.shadow_forecast_id is not None:
            return self.session.get(ShadowForecast, decision.shadow_forecast_id)
        lineage = dict((decision.evidence or {}).get("model_lineage") or {})
        identity = lineage.get("forecast_id")
        if identity:
            return self.session.scalar(
                select(ShadowForecast).where(ShadowForecast.forecast_id == identity))
        return None

    def _matchup(self, game_id: uuid.UUID) -> dict[str, Any] | None:
        rows = list(self.session.execute(
            select(GameTeam, Team)
            .join(Team, Team.id == GameTeam.team_id)
            .where(GameTeam.game_id == game_id)
        ))
        if not rows:
            return None
        home = next((t for side, t in rows if side.is_home), None)
        away = next((t for side, t in rows if not side.is_home), None)
        if home is None or away is None:
            return None
        game = self.session.get(Game, game_id)
        return {
            "home": home.abbreviation,
            "away": away.abbreviation,
            "home_name": home.name,
            "away_name": away.name,
            "label": f"{away.abbreviation} at {home.abbreviation}",
            "starts_at_utc": game.starts_at.isoformat() if game else None,
        }

    def _governed_final(self, game_id: uuid.UUID) -> dict[str, Any] | None:
        row = self.session.scalar(
            select(GameFinalOutcome)
            .where(GameFinalOutcome.game_id == game_id)
            .order_by(GameFinalOutcome.revision.desc())
            .limit(1)
        )
        if row is None or row.outcome_status != "FINAL":
            return None
        return {
            "home_score": row.home_score,
            "away_score": row.away_score,
            "revision": row.revision,
            "source_record_id": row.source_record_id,
        }

    def _event_evidence(self, game_id: uuid.UUID) -> dict[str, Any] | None:
        """Play-level evidence for this game, when any is retained.

        Returns None today: ``pitch_events`` and ``player_game_stats`` hold no
        rows, and the retained availability audit records the Statcast and
        bullpen tables at zero. The outcome panel then states that deeper event
        analysis is unavailable instead of narrating the box score it does have.
        """

        from sqlalchemy import func

        from app.models import PitchEvent, PlayerGameStat

        pitches = self.session.scalar(
            select(func.count()).select_from(PitchEvent)
            .where(PitchEvent.game_id == game_id)
        )
        stats = self.session.scalar(
            select(func.count()).select_from(PlayerGameStat)
            .where(PlayerGameStat.game_id == game_id)
        )
        if not pitches and not stats:
            return None
        return {
            "source": "AISP_RETAINED_GAME_EVIDENCE",
            "pitch_event_rows": int(pitches or 0),
            "player_stat_rows": int(stats or 0),
        }


def _settlement_final(settlement: Any) -> dict[str, Any] | None:
    """The scores the settlement itself recorded, when no governed row exists.

    Named as a distinct source rather than presented as the governed record.
    The two usually agree; where only one exists, a reader should be able to see
    which one they are looking at.
    """

    if settlement is None:
        return None
    home = getattr(settlement, "game_home_score", None)
    away = getattr(settlement, "game_away_score", None)
    if home is None or away is None:
        return None
    return {
        "home_score": int(home),
        "away_score": int(away),
        "revision": None,
        "source_record_id": None,
        "source": "POSITION_SETTLEMENT_RECORD",
    }


__all__ = ["SCHEMA_VERSION", "ExplanationService"]

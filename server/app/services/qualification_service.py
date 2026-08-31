"""Read-only qualification funnel, active-policy report, and starter-gate audit.

Everything here is point-in-time and read-only: it reuses the SAME board
evaluation the $1,000 experiment uses (ReadService._daily_opportunities), never
loosens a threshold, never creates a position, and never inspects a game outcome
to change a pregame decision. It exists to make the qualification funnel and the
exact policy transparent.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.core.time import local_day_bounds
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Game, GameTeam, StarterObservation, Team
from app.services.opportunity_service import MAX_QUOTE_STALENESS_MINUTES
from app.services.read_service import ReadService
from app.services.shadow_experiment_service import (
    FINAL_PREGAME_CUTOFF_MINUTES,
    SUPPORTED_MARKETS,
)

UTC = timezone.utc

# The ordered experiment funnel gates, each keyed to the authoritative rejection
# reason the policy code emits (read_service._qualification_reasons).
FUNNEL_GATES: list[tuple[str, str, str | None]] = [
    ("scheduled_games", "Scheduled games", None),
    ("supported_selections", "Supported selections evaluated (moneyline)", None),
    ("valid_forecast", "Valid current forecast", "Forecast unavailable"),
    ("complete_quote", "Complete two-sided quote", "No complete two-sided governed quote"),
    ("fresh_quote", "Fresh quote", "Latest quote is stale"),
    ("readiness", "Readiness ≥ 70", "Readiness below 70"),
    ("starter", "Starting pitcher confirmed", "Starting pitcher probable"),
    ("lineup", "Lineup available", "Lineup missing"),
    ("lineup_confirmed", "Lineup confirmed", "Lineup not confirmed"),
    ("availability", "No unresolved availability event", "Unresolved availability event"),
    ("not_extreme", "Not extreme disagreement", "Extreme model-market disagreement"),
    ("critical_inputs", "Critical inputs complete", "Critical inputs incomplete"),
    ("qualified", "QUALIFIED_BET selections", None),
    ("positions_created", "Positions created", None),
]


class QualificationService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    # -- exact active policy report (from code + config) -----------------
    def policy_report(self) -> dict[str, Any]:
        s = self.settings
        return {
            "policy_version": "AISP_OPPORTUNITY_POLICY_V1",
            "staking_policy_version": "AISP_QUARTER_KELLY_1_2_5_V1",
            "supported_sports": ["MLB"],
            "board_supported_market_types": ["MONEYLINE", "SPREAD", "TOTAL"],
            "experiment_supported_market_types": list(SUPPORTED_MARKETS),
            "eligible_lifecycle_stage": "pregame (final-pregame decision within cutoff)",
            "valid_forecast_required": True,
            "final_pregame_cutoff_minutes": FINAL_PREGAME_CUTOFF_MINUTES,
            "complete_two_sided_quote_required": True,
            "quote_freshness_minutes_board": MAX_QUOTE_STALENESS_MINUTES,
            "quote_freshness_experiment": "quote_observed_at <= first_pitch - 15 min (cutoff-anchored)",
            "readiness_threshold": s.opportunity_qualified_readiness,
            "publish_readiness_floor": s.forecast_min_readiness,
            "lineup_requirement": "lineup adequate (not MISSING) for full-game; full CONFIRMED not required",
            "starter_requirement": "both probable pitchers CONFIRMED",
            "availability_requirement": "no unresolved LATE_SCRATCH",
            "raw_edge_threshold": "none standalone; WATCH when no_vig_edge > watch",
            "watch_edge": s.opportunity_watch_threshold,
            "uncertainty_adjustment": "conservative = model_probability - forecast.uncertainty",
            "qualified_edge_uncertainty_adjusted": s.shadow_qualified_edge,
            "ev_threshold": "EV > 0 (strict)",
            "extreme_disagreement_threshold": s.opportunity_extreme_disagreement_threshold,
            "recommendation_rules": {
                "QUALIFIED_BET": "is_ready & EV>0 & uncertainty_adjusted_edge>=qualified_edge & fully_ready & not stale & not extreme",
                "PROVISIONAL_EDGE": "merit-qualified price but inputs incomplete or quote stale",
                "WATCH": "no_vig_edge > watch_edge",
                "NO_BET": "otherwise",
                "EXTREME_DISAGREEMENT": "no_vig_edge >= extreme threshold (review required)",
            },
            "staking": {
                "formula": "quarter_kelly: stake = bankroll * kelly_fraction(p,decimal) * 0.25",
                "kelly_fraction": s.shadow_kelly_fraction,
                "normal_unit_percentage": s.shadow_unit_percentage,
                "position_cap_percentage": s.shadow_max_position_percentage,
                "same_game_cap_percentage": s.shadow_max_same_game_percentage,
                "daily_cap_percentage": s.shadow_max_daily_percentage,
                "zero_kelly_behavior": "non-positive edge => raw stake 0 => no position",
                "starting_bankroll": s.shadow_virtual_bankroll,
            },
            "experiment_specific_restrictions": [
                "MONEYLINE market only (board also supports spread/total)",
                "position only at FINAL_PREGAME within [first_pitch-15min, first_pitch)",
                "decision.source_mode must be LIVE",
                "snapshot bytes re-verified before staking (price/outcome/observed_at match)",
            ],
        }

    # -- qualification funnel (point-in-time, read-only) -----------------
    def funnel(self, event_date: date) -> dict[str, Any]:
        read = ReadService(self.session, self.settings)
        games = read._live_games_for_date(event_date)
        cards = [read.game_card(g) for g in games]
        forecasts = [read._latest_forecast(g.id) for g in games]
        opportunities, _ = read._daily_opportunities(games, cards, forecasts)
        opp_by_game = {o.game_id: o for o in opportunities}

        counts = {key: 0 for key, _, _ in FUNNEL_GATES}
        rejection_reasons: dict[str, int] = {}
        per_game: list[dict[str, Any]] = []
        experiment_games = [g for g in games if self._sport(g) == "MLB"]
        counts["scheduled_games"] = len(experiment_games)

        for game in experiment_games:
            opp = opp_by_game.get(str(game.id))
            reasons = list(opp.recommendation_reason and opp.qualification_reasons or []) if opp else []
            state = opp.recommendation_state if opp else "NO_BET"
            counts["supported_selections"] += 1
            # Walk the ordered gates; a game survives a gate iff its keyed reason is absent.
            survived = True
            failed_gate = None
            for key, _label, reason in FUNNEL_GATES:
                if key in {"scheduled_games", "supported_selections", "qualified", "positions_created"}:
                    if key == "qualified" and survived and state == "QUALIFIED_BET":
                        counts["qualified"] += 1
                    continue
                if not survived:
                    continue
                if reason is not None and reason in reasons:
                    survived = False
                    failed_gate = key
                    rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
                    continue
                counts[key] += 1
            per_game.append({
                "game_id": str(game.id),
                "matchup": self._matchup(game),
                "starts_at": _iso(game.starts_at),
                "recommendation_state": state,
                "failed_gate": failed_gate,
                "rejection_reasons": reasons,
                "readiness": opp.readiness if opp else None,
                "uncertainty": opp.uncertainty if opp else None,
                "best_market": (opp.best.market_type if opp and opp.best else None),
                "best_selection": (opp.best.selection if opp and opp.best else None),
                "experiment_status": (opp.experiment_status if opp else "NOT_IN_EXPERIMENT"),
            })

        counts["positions_created"] = self._positions_created(event_date)
        exposure_excluded = max(0, counts["qualified"] - counts["positions_created"])
        top_reasons = sorted(rejection_reasons.items(), key=lambda kv: kv[1], reverse=True)
        steps = [{"key": k, "label": lbl, "count": counts[k]} for k, lbl, _ in FUNNEL_GATES]
        return {
            "date": event_date.isoformat(),
            "experiment_market_types": list(SUPPORTED_MARKETS),
            "steps": steps,
            "qualified_selections": counts["qualified"],
            "positions_created": counts["positions_created"],
            "exposure_cap_exclusions": exposure_excluded,
            "top_rejection_reasons": [{"reason": r, "count": c} for r, c in top_reasons],
            "games": per_game,
        }

    # -- starter-gate audit + probable-stable research comparison --------
    def starter_gate_analysis(self, event_date: date) -> dict[str, Any]:
        read = ReadService(self.session, self.settings)
        games = read._live_games_for_date(event_date)
        cards = [read.game_card(g) for g in games]
        forecasts = [read._latest_forecast(g.id) for g in games]
        opportunities, _ = read._daily_opportunities(games, cards, forecasts)
        opp_by_game = {o.game_id: o for o in opportunities}

        missing = probable = confirmed = 0
        starter_only_losses = 0  # otherwise-qualified but for the starter gate
        probable_stable_gain = 0
        current_qualified = 0
        for game in games:
            if self._sport(game) != "MLB":
                continue
            opp = opp_by_game.get(str(game.id))
            if opp is None:
                continue
            starter_state = (opp.availability.starting_pitcher_status if opp.availability else None) or "MISSING"
            if starter_state == "CONFIRMED":
                confirmed += 1
            elif starter_state == "PROBABLE":
                probable += 1
            else:
                missing += 1
            reasons = set(opp.qualification_reasons or [])
            if opp.recommendation_state == "QUALIFIED_BET":
                current_qualified += 1
            # "starter-only loss": the ONLY blocker is the confirmed-starter gate.
            if reasons and reasons == {"Starting pitcher probable"}:
                starter_only_losses += 1
                if self._probable_stable(game, event_date):
                    probable_stable_gain += 1
        return {
            "date": event_date.isoformat(),
            "policy_requires_confirmed": True,
            "starter_states": {"MISSING": missing, "PROBABLE": probable, "CONFIRMED": confirmed},
            "qualification_losses_from_starter_gate_only": starter_only_losses,
            "current_strict_qualified": current_qualified,
            "probable_stable_challenger": {
                "definition": (
                    "governed player id; same starter in >=2 consecutive polls; no competing starter; "
                    "no unresolved availability event; inside the final pregame window"
                ),
                "additional_qualified_if_adopted": probable_stable_gain,
                "note": "Research comparison only. The official policy is unchanged and this is not adopted.",
            },
            "structurally_restrictive": confirmed == 0 and (probable + missing) > 0,
        }

    def _probable_stable(self, game: Game, event_date: date) -> bool:
        """Challenger stability: the same probable pitcher observed in >=2 polls,
        no competing pitcher, for BOTH sides. Read-only; never adopted."""
        for is_home in (True, False):
            gt = self.session.scalar(select(GameTeam).where(GameTeam.game_id == game.id, GameTeam.is_home.is_(is_home)))
            if gt is None:
                return False
            obs = list(self.session.scalars(select(StarterObservation).where(
                StarterObservation.game_id == game.id, StarterObservation.team_id == gt.team_id,
            ).order_by(StarterObservation.available_as_of)))
            pitchers = {o.probable_pitcher_id for o in obs if o.probable_pitcher_id}
            if len(obs) < 2 or len(pitchers) != 1:
                return False
        return True

    # -- helpers ---------------------------------------------------------
    def _positions_created(self, event_date: date) -> int:
        from sqlalchemy import func
        from app.models import Market, MarketOutcome, OfficialAspPosition

        start, end = local_day_bounds(event_date, self.settings.display_timezone)
        return int(self.session.scalar(
            select(func.count()).select_from(OfficialAspPosition)
            .join(MarketOutcome, MarketOutcome.id == OfficialAspPosition.market_outcome_id)
            .join(Market, Market.id == MarketOutcome.market_id)
            .join(Game, Game.id == Market.game_id)
            .where(Game.starts_at >= start, Game.starts_at < end)
        ) or 0)

    def _sport(self, game: Game) -> str:
        from app.models import League

        code = self.session.scalar(select(League.code).where(League.id == game.league_id))
        return code or "UNKNOWN"

    def _matchup(self, game: Game) -> str:
        def ab(is_home: bool) -> str:
            gt = self.session.scalar(select(GameTeam).where(GameTeam.game_id == game.id, GameTeam.is_home.is_(is_home)))
            t = self.session.get(Team, gt.team_id) if gt else None
            return t.abbreviation if t else "?"
        return f"{ab(False)} @ {ab(True)}"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()


__all__ = ["QualificationService", "FUNNEL_GATES"]

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

    # -- V2 practical-policy funnel (point-in-time, read-only) ------------
    def funnel_v2(self, event_date: date, *, now: datetime | None = None) -> dict[str, Any]:
        """The challenger funnel: component-explicit V2 gates, never the blunt
        readiness composite. Read-only — no decision or position is created."""
        from datetime import UTC, timedelta

        from app.services.practical_policy import (
            MIN_ADJUSTED_EDGE,
            POLICY_VERSION,
            PracticalPolicyEvaluator,
        )
        from app.services.shadow_experiment_service import (
            FINAL_PREGAME_CUTOFF_MINUTES,
            V2_DECISION_POLICY,
            ShadowExperimentService,
        )

        now = now or datetime.now(UTC)
        read = ReadService(self.session, self.settings)
        games = read._live_games_for_date(event_date)
        cards = [read.game_card(g) for g in games]
        forecasts = [read._latest_forecast(g.id) for g in games]
        opportunities, _ = read._daily_opportunities(games, cards, forecasts)
        opp_by_game = {o.game_id: o for o in opportunities}
        evaluator = PracticalPolicyEvaluator(self.session, self.settings)

        gates = [
            ("scheduled_games", "Scheduled games"),
            ("supported_selections", "Moneyline markets supported"),
            ("valid_forecast", "Valid current forecast"),
            ("complete_quote", "Complete two-sided quote"),
            ("fresh_quote", "Fresh quote"),
            ("exact_identity", "Exact game identity"),
            ("starters_eligible", "Starter confirmed or probable-stable"),
            ("lineup_eligible", "Lineup confirmed, adequate, or pending-allowed"),
            ("no_unresolved_event", "No unresolved availability event"),
            ("model_inputs_valid", "Model-required inputs valid or governed fallback"),
            ("critical_complete", "V2.1 critical evidence complete"),
            ("adjusted_edge", f"Adjusted edge ≥ {MIN_ADJUSTED_EDGE * 100:.1f} pts"),
            ("adjusted_ev", "Positive adjusted EV"),
            ("adjusted_kelly", "Positive adjusted Kelly"),
            ("qualified", "V2.1 qualified decision"),
            ("positions_created", "Positions created"),
        ]
        counts = {key: 0 for key, _ in gates}
        rejection: dict[str, int] = {}
        diagnostics = {
            "awaiting_final_pregame_evaluation": 0,
            "blocked_only_by_starter_instability": 0,
            "blocked_only_by_lineup_conflict": 0,
            "blocked_only_by_model_required_input": 0,
            "previously_blocked_by_duplicate_feature_completeness_gate": 0,
            "unsupported_market": 0,
            "edge_ev_kelly_failure": 0,
            "portfolio_cap_failure": 0,
        }
        per_game: list[dict[str, Any]] = []
        experiment_games = [g for g in games if self._sport(g) == "MLB"]
        counts["scheduled_games"] = len(experiment_games)
        counts["supported_selections"] = len(experiment_games)

        for game in experiment_games:
            opp = opp_by_game.get(str(game.id))
            forecast = next((f for g, f in zip(games, forecasts) if g.id == game.id), None)
            views = []
            if opp is not None:
                views = [v for v in (opp.best, *opp.alternates) if v is not None]
            moneyline = next((v for v in views if (v.market_type or "").upper() == "MONEYLINE"), None)
            starts = game.starts_at if game.starts_at.tzinfo else game.starts_at.replace(tzinfo=UTC)
            cutoff = starts - timedelta(minutes=FINAL_PREGAME_CUTOFF_MINUTES)
            # The funnel reads the slate "as at the final decision cutoff": a
            # point-in-time evaluation, never a retroactive decision. Quote
            # freshness is therefore anchored to min(now, cutoff), not to the
            # moment the funnel happens to be read.
            reference = min(now, cutoff)
            if moneyline is not None and moneyline.quote_observed_at is not None:
                observed = moneyline.quote_observed_at
                observed = observed if observed.tzinfo else observed.replace(tzinfo=UTC)
                stale_at_reference = (
                    observed > cutoff
                    or observed < reference - timedelta(minutes=MAX_QUOTE_STALENESS_MINUTES)
                )
                moneyline = moneyline.model_copy(update={"is_stale": stale_at_reference})
            v2 = evaluator.evaluate(
                game, forecast, opp.availability if opp else None, moneyline,
                now=min(now, cutoff - timedelta(seconds=1)),
                cutoff=cutoff,
            )
            all_reasons = " | ".join([v2.reason, *v2.reasons]).lower()
            decomposition = v2.decomposition or {}
            model_required_missing = decomposition.get("model_required_missing", [])
            scratch_or_event = (
                "scratch" in all_reasons
                or "availability event" in all_reasons
                or "material lineup change" in all_reasons
            )
            edge_pass = v2.adjusted_edge is not None and v2.adjusted_edge >= MIN_ADJUSTED_EDGE
            ev_pass = bool(v2.adjusted_expected_value and v2.adjusted_expected_value > 0)
            passes = {
                "valid_forecast": forecast is not None,
                "complete_quote": moneyline is not None,
                "fresh_quote": moneyline is not None and not bool(getattr(moneyline, "is_stale", False)),
                "exact_identity": "identity unavailable" not in all_reasons,
                "starters_eligible": bool(v2.starter_home and v2.starter_away
                                          and v2.starter_home.eligible and v2.starter_away.eligible),
                "lineup_eligible": bool(v2.lineup and v2.lineup.eligible_for_moneyline),
                "no_unresolved_event": not scratch_or_event,
                "model_inputs_valid": forecast is not None and not model_required_missing,
                "critical_complete": v2.critical_evidence_complete,
                "adjusted_edge": edge_pass,
                "adjusted_ev": ev_pass,
                # Positive adjusted Kelly is mathematically equivalent to
                # positive adjusted EV at the same price; shown as its own gate
                # for transparency.
                "adjusted_kelly": ev_pass,
                "qualified": v2.qualified,
            }
            survived = True
            failed_gate = None
            for key, _label in gates:
                if key in {"scheduled_games", "supported_selections", "positions_created"}:
                    continue
                if not survived:
                    continue
                if passes.get(key, False):
                    counts[key] += 1
                else:
                    survived = False
                    failed_gate = key
                    for reason in (v2.reasons or [v2.reason]):
                        rejection[reason] = rejection.get(reason, 0) + 1
            # -- diagnostics -------------------------------------------------
            if v2.lifecycle in ("PENDING_FINAL_EVIDENCE", "NOT_YET_EVALUATED"):
                diagnostics["awaiting_final_pregame_evaluation"] += 1
            if failed_gate == "starters_eligible":
                diagnostics["blocked_only_by_starter_instability"] += 1
            if failed_gate == "lineup_eligible" and v2.lineup is not None and v2.lineup.state in (
                "CONFLICT", "LATE_SCRATCH_UNRESOLVED",
            ):
                diagnostics["blocked_only_by_lineup_conflict"] += 1
            if failed_gate == "model_inputs_valid":
                diagnostics["blocked_only_by_model_required_input"] += 1
            if opp is not None and moneyline is None and opp.best is not None:
                diagnostics["unsupported_market"] += 1
            if failed_gate in {"adjusted_edge", "adjusted_ev", "adjusted_kelly"}:
                diagnostics["edge_ev_kelly_failure"] += 1
            # Would the OLD composite gate have blocked this game even though
            # no model-required component is actually missing?
            fc = (forecast.readiness_components or {}).get("feature_completeness") if forecast else None
            if (
                forecast is not None and isinstance(fc, dict)
                and str(fc.get("status", "")).lower() != "ready"
                and not model_required_missing
            ):
                diagnostics["previously_blocked_by_duplicate_feature_completeness_gate"] += 1
            per_game.append({
                "game_id": str(game.id),
                "matchup": self._matchup(game),
                "starts_at": _iso(game.starts_at),
                "tier": v2.tier,
                "lifecycle": v2.lifecycle,
                "recommendation_state": v2.recommendation,
                "failed_gate": failed_gate,
                "rejection_reasons": list(v2.reasons),
                "gates_passed": list(v2.gates_passed),
                "critical_evidence_complete": v2.critical_evidence_complete,
                "starter_state_home": v2.starter_home.state if v2.starter_home else None,
                "starter_state_away": v2.starter_away.state if v2.starter_away else None,
                "starter_stable_since_home": v2.starter_home.stable_since if v2.starter_home else None,
                "starter_stable_since_away": v2.starter_away.stable_since if v2.starter_away else None,
                "lineup_state": v2.lineup.state if v2.lineup else None,
                "model_required_missing": list(model_required_missing),
                "adjusted_edge": v2.adjusted_edge,
                "readiness": opp.readiness if opp else None,
                "best_market": moneyline.market_type if moneyline else None,
                "best_selection": moneyline.selection if moneyline else None,
                "experiment_status": (opp.experiment_status if opp else "NOT_IN_EXPERIMENT"),
                "uncertainty": opp.uncertainty if opp else None,
            })

        # Positions created by the V2 experiment for this slate (0 pre-launch).
        from sqlalchemy import func

        from app.models import OfficialAspPosition, ShadowExperimentDecision

        service = ShadowExperimentService(self.session, self.settings)
        v2_experiment = service.active_experiment(V2_DECISION_POLICY)
        if v2_experiment is not None:
            counts["positions_created"] = int(self.session.scalar(
                select(func.count()).select_from(OfficialAspPosition)
                .join(ShadowExperimentDecision, ShadowExperimentDecision.id == OfficialAspPosition.shadow_decision_id)
                .where(
                    OfficialAspPosition.shadow_experiment_id == v2_experiment.id,
                    ShadowExperimentDecision.slate_date == event_date,
                )
            ) or 0)
        top = sorted(rejection.items(), key=lambda kv: kv[1], reverse=True)
        exposure_excluded = (
            max(0, counts["qualified"] - counts["positions_created"])
            if v2_experiment is not None and event_date >= v2_experiment.start_date else 0
        )
        diagnostics["portfolio_cap_failure"] = exposure_excluded
        return {
            "date": event_date.isoformat(),
            "policy": POLICY_VERSION,
            "experiment_market_types": list(SUPPORTED_MARKETS),
            "steps": [{"key": k, "label": lbl, "count": counts[k]} for k, lbl in gates],
            "qualified_selections": counts["qualified"],
            "positions_created": counts["positions_created"],
            "exposure_cap_exclusions": exposure_excluded,
            "top_rejection_reasons": [{"reason": r, "count": c} for r, c in top[:8]],
            "diagnostics": diagnostics,
            "experiment_registered": v2_experiment is not None,
            "effective_date": v2_experiment.start_date.isoformat() if v2_experiment else None,
            "games": per_game,
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

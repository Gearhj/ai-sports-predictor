from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo
from math import isfinite
from typing import Any

from sqlalchemy import case, distinct, func, or_, select
from sqlalchemy.orm import Session

from app.bankroll.service import (
    SettlementResult,
    SettledPosition,
    StakePolicy,
    StakingStrategy,
    bankroll_metrics,
    calculate_stake,
    settle_profit,
)
from app.core.config import Settings
from app.data_sources.provider_registry import (
    GOVERNED_SOURCES,
    authorised_sources_for_mode,
)
from app.forecast_exchange.pricing import ShadowPricingConfig
from app.core.time import local_date_of, local_day_bounds
from app.sports.registry import resolve_stored_identity
from app.features.lifecycle import LifecycleStage, classify_lifecycle
from app.features.readiness import (
    GATE2_WEIGHTS,
    ReadinessResult,
    ReadinessStatus,
    calculate_readiness,
)
from app.forecasting.evaluation import brier_score, calibration_bins, log_loss, mean_absolute_error
from app.markets.availability import average_probability, model_edge, observed_probability
from app.markets.odds import (
    american_to_decimal,
    classify_opportunity,
    decimal_to_american,
    no_vig_probabilities,
    probability_to_american,
)
from app.models import (
    AvailabilityEvent,
    FeatureVersion,
    Forecast,
    ForecastComponent,
    RosterAvailabilityObservation,
    ShadowExperiment,
    ShadowRescore,
    ForecastStatus,
    ForecastTrack,
    Game,
    GameFeatureSnapshot,
    GameRefreshState,
    GameStatus,
    GameTeam,
    IngestionRun,
    JobRun,
    Market,
    MarketComparison,
    MarketOutcome,
    MarketType,
    ModelEvaluation,
    ModelVersion,
    OddsSnapshot,
    OfficialAspPosition,
    Player,
    Pitcher,
    PositionResult,
    RunStatus,
    Sportsbook,
    Team,
    TeamFeature,
    TrainingDatasetManifest,
    LineupObservation,
    StarterObservation,
    Venue,
    WeatherSnapshot,
    ExternalIdMapping,
)
from app.schemas.api import (
    BankrollData,
    ExperimentDecisionMetrics,
    ComparisonMetric,
    DashboardData,
    DashboardSummary,
    ForecastGame,
    GameAnalysisData,
    HealthData,
    JobRunSchema,
    MarketBoardData,
    MarketFeedStatus,
    MarketBoardRow,
    MarketBookPrice,
    MarketPrice,
    OfflineEvaluation,
    OfflineEvaluationBaseline,
    OfflineEvaluationMetrics,
    OfflineEvaluationWindow,
    OfflinePromotionDecision,
    PerformanceData,
    PitcherSummary,
    PositionSchema,
    SourceHealth,
    TeamSummary,
)
from app.services.historical_research_read_service import HistoricalResearchReadService
from app.services.game_state_service import GameStateService, GameStateUnavailableError
from app.services.league_scope import MLB, in_league
from app.services.opportunity_service import (
    EXTREME_DISAGREEMENT,
    PROVISIONAL_EDGE,
    BookQuote,
    Candidate,
    MarketInput,
    SideInput,
    allocate_stakes,
    build_market_candidate,
    compute_opportunity_score,
)
from app.prospective.sportsdataio import read_availability_feed_status
from app.schemas.api import (
    AvailabilityContext,
    GameOpportunity,
    OpportunitySelection,
    OpportunitySummary,
)


TEAM_COLORS = {
    "NYY": "#132448", "BOS": "#BD3039", "LAD": "#005A9C", "SF": "#FD5A1E",
    "TOR": "#134A8E", "TB": "#092C5C", "ATL": "#CE1141", "NYM": "#FF5910",
    "CHC": "#0E3386", "HOU": "#002D62", "BAL": "#DF4601", "SEA": "#0C2C56",
}

OFFLINE_EVALUATION_POLICY_VERSION = "gate3-promotion-v1"
GEN1_RESEARCH_POLICY_VERSION = "MLB_GEN1_RESEARCH_POLICY_V1"
STRICT_A2_MANIFEST_NAME = "mlb_historical_a2_tier_a_strict"
ENHANCED_A2_MANIFEST_NAME = "mlb_historical_a2_tier_b_enhanced"


def _simulation_over_probability(simulation: dict[str, Any], total: float | None) -> float:
    """Return an over probability for both standard and consensus total lines."""
    if total is None:
        return 0.0

    precomputed = (simulation.get("total_probabilities") or {}).get(f"{total:g}")
    if precomputed is not None:
        return float(precomputed.get("over", 0.0))

    distribution = simulation.get("total_runs_distribution") or {}
    if not distribution:
        return 0.0

    probability = 0.0
    for runs, share in distribution.items():
        if runs == ">20":
            if total < 20:
                probability += float(share)
            continue
        if runs == "<0":
            continue
        try:
            if float(runs) > total:
                probability += float(share)
        except (TypeError, ValueError):
            continue
    return min(max(probability, 0.0), 1.0)


def _snapshot_evidence_timestamp(
    snapshot: GameFeatureSnapshot | None,
    *families: str,
) -> datetime | None:
    if snapshot is None:
        return None
    candidates: list[datetime] = []
    for item in snapshot.source_evidence or []:
        if item.get("family") not in families or not item.get("available_as_of"):
            continue
        value = item["available_as_of"]
        try:
            parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        candidates.append(_aware(parsed))
    return max(candidates, default=None)


class ReadService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        # Per-instance memo for ``_latest_market_snapshots``. A ReadService is
        # built per request/job, and the board path asks for the same game's
        # latest quotes up to three times while assembling one response.
        self._market_snapshot_cache: dict[tuple[str, datetime | None], list[Any]] = {}

    def _prefetch(self):
        """The request's set-based FunnelReadContext when a funnel attached one
        to this session; None for every other caller (legacy per-game reads)."""
        from app.services.funnel_read_context import attached_context

        return attached_context(self.session)

    def _prefetched_game(self, game_id: Any):
        ctx = self._prefetch()
        return ctx if ctx is not None and game_id in ctx.games_by_id else None

    def dashboard(self, event_date: date) -> DashboardData:
        # The public Today surface is a hard current-LIVE boundary. Demo rows
        # remain available only through the explicitly illustrative client
        # route and historical rows remain on the research surface.
        from app.services.funnel_read_context import (
            attach_context, attached_context, begin_read_snapshot, build_funnel_read_context, detach_context,
        )

        if attached_context(self.session) is None:
            begin_read_snapshot(self.session)
        games = self._live_games_for_date(event_date, MLB)
        # The Today board walks the same per-game helpers as the funnels; serve
        # them from one set-based read context (business contract unchanged).
        owns_context = attached_context(self.session) is None
        if owns_context:
            attach_context(self.session, build_funnel_read_context(self.session, self.settings, games))
        try:
            cards = [self.game_card(game) for game in games]
            forecasts = [self._latest_forecast(game.id) for game in games]
            # A game counts as forecast-complete when it has a valid current version —
            # the morning publication or a later intraday rescore that superseded it.
            complete = sum(forecast is not None for forecast in forecasts)
            return self._dashboard_from(event_date, games, cards, forecasts, complete)
        finally:
            if owns_context:
                detach_context(self.session)

    def _dashboard_from(self, event_date: date, games, cards, forecasts, complete: int) -> DashboardData:
        readiness = [card.data_readiness for card in cards if card.data_readiness is not None]
        bankroll = self.bankroll()
        last_refresh = max(
            (card.last_refresh_at for card in cards if card.last_refresh_at is not None),
            default=None,
        )
        opportunities, opportunity_summary = self._daily_opportunities(games, cards, forecasts)
        return DashboardData(
            data_mode="live" if games else "empty",
            date=event_date,
            model_status=self._model_status(),
            component_weights=self._component_weights(),
            summary=DashboardSummary(
                games=len(games),
                complete_forecasts=complete,
                qualifying_edges=sum(card.opportunity in {"STANDARD EDGE", "STRONG EDGE"} for card in cards),
                last_refresh=last_refresh,
                overall_data_health=sum(readiness) / len(readiness) if readiness else 0,
                official_bankroll=bankroll.current_bankroll,
                season_return=bankroll.roi,
            ),
            games=cards,
            opportunities=opportunities,
            opportunity_summary=opportunity_summary,
        )

    # -- daily opportunity board -----------------------------------------
    def _daily_opportunities(
        self,
        games: list[Game],
        cards: list[ForecastGame],
        forecasts: list[Forecast | None],
    ) -> tuple[list[GameOpportunity], OpportunitySummary]:
        """Price the current slate into a governed best-opportunity board.

        Reuses the shadow-pricing policy and the virtual-bankroll math verbatim;
        the read layer only adapts persisted rows into plain pricing inputs. It
        joins the latest availability events and immutable rescore evidence for
        display. This is a database-only read — it never triggers a provider call.
        """

        now = datetime.now(UTC)
        bankroll = float(self.settings.shadow_virtual_bankroll)
        policy = StakePolicy(
            strategy=StakingStrategy.FRACTIONAL_KELLY,
            kelly_fraction=self.settings.shadow_kelly_fraction,
            maximum_stake_percentage=self.settings.shadow_max_position_percentage,
        )
        same_game_cap = bankroll * self.settings.shadow_max_same_game_percentage
        daily_cap = bankroll * self.settings.shadow_max_daily_percentage
        config = ShadowPricingConfig(
            qualified_edge=self.settings.shadow_qualified_edge,
            watch_edge=self.settings.opportunity_watch_threshold,
        )
        ready_floor = self.settings.opportunity_qualified_readiness
        extreme_threshold = self.settings.opportunity_extreme_disagreement_threshold
        injury_feed_status = read_availability_feed_status(self.settings, self.session)

        candidates_by_game: dict[str, list[Candidate]] = {}
        meta_by_game: dict[str, dict[str, Any]] = {}
        availability_by_game: dict[str, AvailabilityContext] = {}
        gate_by_game: dict[str, dict[str, Any]] = {}
        qualified: list[tuple[str, Candidate]] = []
        for game, card, forecast in zip(games, cards, forecasts):
            game_id = str(game.id)
            sport = self._game_sport(game)
            availability, rescored, unresolved_scratch = self._availability_context(game, card, injury_feed_status)
            availability_by_game[game_id] = availability
            confirmation = self._input_confirmation(card)
            readiness_ok = forecast is not None and (forecast.readiness_score or 0.0) >= ready_floor
            gate_by_game[game_id] = {
                **confirmation,
                "unresolved_scratch": unresolved_scratch,
                "readiness_ok": readiness_ok,
                "production_injury_coverage": availability.production_injury_coverage,
            }
            if forecast is None:
                candidates_by_game[game_id] = []
                meta_by_game[game_id] = {"sport": sport, "readiness": None, "uncertainty_pct": None, "uncertainty_raw": None, "disagreement": None}
                continue
            home_side, home, away_side, away = self._sides(game.id)
            # Market-specific critical inputs. For full-game markets QUALIFICATION
            # needs a confirmed starter, adequate lineup evidence, no critical
            # readiness gap, and no unresolved late scratch. A confirmed lineup is
            # not required for full-game (it is for props/team totals). Absence of
            # a production injury feed is a caveat, not a hard block for ordinary
            # markets — it is surfaced as a qualification reason instead.
            base_ok = not self._has_critical_missing(forecast)
            full_game_ok = (
                base_ok
                and confirmation["starter_confirmed"]
                and confirmation["lineup_adequate"]
                and not unresolved_scratch
            )
            priced = [
                candidate
                for market in self._market_inputs(game, forecast, home, away)
                if (
                    candidate := build_market_candidate(
                        market, readiness_ok=readiness_ok, critical_inputs_ok=full_game_ok,
                        config=config, now=now, extreme_threshold=extreme_threshold,
                    )
                )
                is not None
            ]
            # Extreme model–market gaps are REVIEW REQUIRED: only a genuinely
            # qualified price (confirmed inputs + fresh quote) that has also been
            # rescored after the latest event may keep the QUALIFIED label.
            for candidate in priced:
                if candidate.extreme and candidate.recommendation in {"QUALIFIED_BET", PROVISIONAL_EDGE}:
                    if not (candidate.recommendation == "QUALIFIED_BET" and rescored):
                        candidate.recommendation = EXTREME_DISAGREEMENT
            candidates_by_game[game_id] = priced
            meta_by_game[game_id] = {
                "sport": sport,
                "readiness": forecast.readiness_score,
                "uncertainty_pct": round(forecast.uncertainty * 100, 2) if forecast.uncertainty is not None else None,
                "uncertainty_raw": forecast.uncertainty,
                "disagreement": self._component_disagreement(forecast),
            }
            qualified.extend(
                (game_id, candidate)
                for candidate in priced
                if candidate.recommendation == "QUALIFIED_BET"
            )

        daily_exposure = allocate_stakes(
            qualified,
            bankroll=bankroll,
            policy=policy,
            same_game_cap=same_game_cap,
            daily_cap=daily_cap,
        )

        # Score every candidate now that exposure is settled, then rank by the
        # single governed Opportunity Score (tier band, then blended quality).
        for game_id, priced in candidates_by_game.items():
            meta = meta_by_game[game_id]
            for candidate in priced:
                candidate.opportunity_score = compute_opportunity_score(
                    recommendation=candidate.recommendation,
                    uncertainty_adjusted_ev=candidate.uncertainty_adjusted_ev,
                    readiness=meta["readiness"],
                    quote_age_seconds=candidate.quote_age_seconds,
                    forecast_uncertainty=meta["uncertainty_raw"],
                    model_disagreement=meta["disagreement"],
                    has_virtual_position=candidate.virtual_stake > 0,
                )
            priced.sort(
                key=lambda item: (item.opportunity_score, item.uncertainty_adjusted_ev if item.uncertainty_adjusted_ev is not None else float("-inf")),
                reverse=True,
            )

        ranked_games = sorted(
            [
                (game_id, priced[0])
                for game_id, priced in candidates_by_game.items()
                if priced and priced[0].recommendation != "NO_BET"
            ],
            key=lambda item: item[1].opportunity_score,
            reverse=True,
        )
        top = ranked_games[0][1] if ranked_games else None

        experiment_by_game = self._experiment_status_map([game.id for game in games])
        opportunities: list[GameOpportunity] = []
        counts = {"QUALIFIED_BET": 0, "PROVISIONAL_EDGE": 0, "EXTREME_DISAGREEMENT": 0, "WATCH": 0, "NO_BET": 0}
        best_ev: float | None = None
        sports_available: list[str] = []
        v1_ml_top: tuple[float, Any] | None = None
        v2_top: Any = None
        v2_pending_count = 0
        for game, forecast in zip(games, forecasts):
            game_id = str(game.id)
            priced = candidates_by_game.get(game_id, [])
            meta = meta_by_game[game_id]
            if meta["sport"] not in sports_available:
                sports_available.append(meta["sport"])
            best = priced[0] if priced else None
            state = best.recommendation if best else "NO_BET"
            counts[state] = counts.get(state, 0) + 1
            reason = self._recommendation_reason(forecast, best, ready_floor)
            best_view = self._opportunity_view(best) if best else None
            if best is not None and best.recommendation != "NO_BET" and best_view.expected_value is not None:
                best_ev = best_view.expected_value if best_ev is None else max(best_ev, best_view.expected_value)
            exp_status, exp_stake = experiment_by_game.get(game_id, (None, None))
            if exp_status is None:
                ctx = self._prefetch()
                if ctx is not None:
                    experiment_active = bool(ctx.active_experiments)
                else:
                    experiment_active = self.session.scalar(
                        select(ShadowExperiment.id).where(ShadowExperiment.is_active.is_(True)).limit(1)
                    ) is not None
                if not experiment_active:
                    exp_status = "NOT_IN_EXPERIMENT"
                elif state == "QUALIFIED_BET":
                    exp_status = "ELIGIBLE"
                elif state == "NO_BET":
                    exp_status = "NO_POSITION"
                else:
                    exp_status = "PROVISIONAL"
            alternate_views = [self._opportunity_view(candidate) for candidate in priced[1:]]
            v2_view = self._v2_policy_view(
                game, forecast, availability_by_game[game_id],
                [view for view in (best_view, *alternate_views) if view is not None],
                now,
            )
            ml_view = next(
                (view for view in (best_view, *alternate_views)
                 if view is not None and (view.market_type or "").upper() == "MONEYLINE"),
                None,
            )
            if ml_view is not None:
                ml_score = ml_view.opportunity_score or 0.0
                if v1_ml_top is None or ml_score > v1_ml_top[0]:
                    v1_ml_top = (ml_score, ml_view)
            if v2_view is not None:
                if v2_view.recommendation == "QUALIFIED_BET":
                    if v2_top is None or (v2_view.adjusted_edge or -999.0) > (v2_top.adjusted_edge or -999.0):
                        v2_top = v2_view
                elif v2_view.lifecycle in ("PENDING_FINAL_EVIDENCE", "NOT_YET_EVALUATED"):
                    v2_pending_count += 1
            opportunities.append(GameOpportunity(
                game_id=game_id,
                sport=meta["sport"],
                recommendation_state=state,
                recommendation_reason=reason,
                qualification_reasons=self._qualification_reasons(
                    forecast, best, gate_by_game[game_id], ready_floor,
                ),
                review_required=state == EXTREME_DISAGREEMENT,
                opportunity_score=best.opportunity_score if best else 0.0,
                markets_available=sorted({candidate.market_label for candidate in priced}),
                readiness=meta["readiness"],
                uncertainty=meta["uncertainty_pct"],
                model_disagreement=meta["disagreement"],
                availability=availability_by_game[game_id],
                best=best_view,
                alternates=alternate_views,
                experiment_status=exp_status,
                experiment_stake=exp_stake,
                v2=v2_view,
            ))

        odds_last_refresh, odds_age, odds_quota, scheduler_running = self._odds_freshness(candidates_by_game, now)
        summary = OpportunitySummary(
            qualified=counts["QUALIFIED_BET"],
            provisional=counts["PROVISIONAL_EDGE"],
            review_required=counts["EXTREME_DISAGREEMENT"],
            watch=counts["WATCH"],
            no_bet=counts["NO_BET"],
            best_expected_value=best_ev,
            virtual_daily_exposure=daily_exposure,
            top_opportunity=self._top_opportunity_label(top) if top else None,
            markets_available=sorted({
                candidate.market_label
                for candidates in candidates_by_game.values()
                for candidate in candidates
            }),
            sports_available=sports_available,
            injury_feed_status=injury_feed_status,
            injury_feed_authoritative=injury_feed_status == "INJURY_FEED_ACTIVE",
            odds_last_refresh=odds_last_refresh,
            odds_quote_age_seconds=odds_age,
            odds_quota_remaining=odds_quota,
            odds_scheduler_running=scheduler_running,
            top_v1_moneyline=(
                f"{v1_ml_top[1].selection} "
                f"{f'{v1_ml_top[1].american_odds:+d}' if v1_ml_top[1].american_odds is not None else '—'}"
                f"{f' · {v1_ml_top[1].sportsbook}' if v1_ml_top[1].sportsbook else ''}"
                f" · {v1_ml_top[1].recommendation}"
                if v1_ml_top is not None else None
            ),
            top_v2_moneyline=(
                (
                    f"{v2_top.selection} · {v2_top.tier}"
                    f"{f' · adj {v2_top.adjusted_edge:+.1f} pts' if v2_top.adjusted_edge is not None else ''}"
                    f"{' · awaiting final evaluation' if v2_top.lifecycle == 'PROVISIONALLY_ELIGIBLE' else ''}"
                )
                if v2_top is not None
                else f"No V2 final decision yet — {v2_pending_count} candidate(s) awaiting final evidence"
                if v2_pending_count > 0
                else "No V2-qualified moneyline after final evaluation"
            ),
        )
        return opportunities, summary

    def _qualification_reasons(
        self,
        forecast: Forecast | None,
        best: Candidate | None,
        gate: dict[str, Any],
        ready_floor: float,
    ) -> list[str]:
        """The short list of blockers explaining why a game is not qualified."""

        if forecast is None:
            return ["Forecast unavailable"]
        if best is None:
            return ["No complete two-sided governed quote"]
        reasons: list[str] = []
        if best.is_stale:
            reasons.append("Latest quote is stale")
        if not gate.get("readiness_ok"):
            reasons.append(f"Readiness below {ready_floor:.0f}")
        if not gate.get("starter_confirmed"):
            reasons.append("Starting pitcher probable")
        if not gate.get("lineup_adequate"):
            reasons.append("Lineup missing")
        elif not gate.get("lineup_confirmed"):
            reasons.append("Lineup not confirmed")
        if gate.get("unresolved_scratch"):
            reasons.append("Unresolved availability event")
        if best.recommendation == EXTREME_DISAGREEMENT:
            reasons.append("Extreme model-market disagreement")
        if self._has_critical_missing(forecast):
            reasons.append("Critical inputs incomplete")
        # Absence of the deferred paid injury feed is never a blocker for ordinary
        # game markets — qualification rests on available governed evidence only.
        return list(dict.fromkeys(reasons))[:6]

    def _odds_freshness(
        self,
        candidates_by_game: dict[str, list[Candidate]],
        now: datetime,
    ) -> tuple[datetime | None, int | None, int | None, bool]:
        """Odds refresh diagnostics — never a fabricated timestamp."""

        observed = [
            candidate.observed_at
            for candidates in candidates_by_game.values()
            for candidate in candidates
            if candidate.observed_at is not None
        ]
        freshest = max(observed) if observed else None
        last_run = self.session.scalar(
            select(IngestionRun)
            .where(IngestionRun.source == "THE_ODDS_API", IngestionRun.status == RunStatus.SUCCEEDED)
            .order_by(IngestionRun.completed_at.desc().nulls_last())
            .limit(1)
        )
        last_refresh = _aware(last_run.completed_at) if last_run and last_run.completed_at else freshest
        age = int((now - _aware(freshest)).total_seconds()) if freshest else None
        # "Running" requires BOTH the configuration that arms refresh-odds AND
        # the scheduler actually started in this serving process — configuration
        # alone must never present a dead scheduler as running.
        from app.jobs.scheduler import scheduler_is_running

        scheduler_armed = bool(
            self.settings.scheduler_enabled
            and (
                self.settings.odds_polling_enabled
                or (self.settings.live_shadow_enabled and self.settings.the_odds_api_key)
            )
        )
        scheduler_running = scheduler_armed and scheduler_is_running()
        return last_refresh, age, self._odds_quota(), scheduler_running

    @staticmethod
    def _input_confirmation(card: ForecastGame) -> dict[str, bool]:
        away, home = card.away_lineup, card.home_lineup
        away_status = away.status if away else "MISSING"
        home_status = home.status if home else "MISSING"
        away_complete = bool(away and away.status == "CONFIRMED" and away.complete)
        home_complete = bool(home and home.status == "CONFIRMED" and home.complete)
        # A lineup is CONFIRMED only with the full persisted payload (complete
        # batting order); a status-only/manual confirmation is not enough.
        lineup_confirmed = away_complete and home_complete
        lineup_adequate = "MISSING" not in (away_status, home_status)
        starter_confirmed = bool(card.away_pitcher.confirmed and card.home_pitcher.confirmed)
        return {
            "lineup_confirmed": lineup_confirmed,
            "lineup_adequate": lineup_adequate,
            "lineup_complete": away_complete and home_complete,
            "starter_confirmed": starter_confirmed,
        }

    def _availability_context(
        self,
        game: Game,
        card: ForecastGame,
        injury_feed_status: str,
    ) -> tuple[AvailabilityContext, bool, bool]:
        """Join the latest availability events + immutable rescore for the board.

        Returns (context, rescored, unresolved_scratch)."""

        ctx = self._prefetched_game(game.id)
        if ctx is not None:
            events = list(ctx.events(game.id))  # newest received first
        else:
            events = list(self.session.scalars(
                select(AvailabilityEvent)
                .where(AvailabilityEvent.game_id == game.id)
                .order_by(AvailabilityEvent.received_at.desc())
            ))
        last = events[0] if events else None
        confirmation = self._input_confirmation(card)
        away_lineup = card.away_lineup.status if card.away_lineup else "MISSING"
        home_lineup = card.home_lineup.status if card.home_lineup else "MISSING"
        unresolved_scratch = any(event.event_type == "LATE_SCRATCH" for event in events)
        if unresolved_scratch or any(event.event_type == "LINEUP_CHANGED" for event in events):
            lineup_status = "CHANGED"
        elif away_lineup == "CONFIRMED" and home_lineup == "CONFIRMED":
            # A confirmed status without the full persisted lineup payload is not
            # a real confirmation — surface the details as missing.
            lineup_status = "CONFIRMED" if confirmation["lineup_complete"] else "DETAILS_MISSING"
        elif "MISSING" in (away_lineup, home_lineup):
            lineup_status = "MISSING"
        else:
            lineup_status = "PROJECTED"

        starter_status = self._starter_status(card)

        # A trial/unconfigured/errored feed is never authoritative: it must not
        # be shown as "0 unavailable" (a false healthy claim). Manual/production
        # out-events still count; otherwise the count is unknown (—).
        authoritative = injury_feed_status == "INJURY_FEED_ACTIVE"
        out_types = {"CONFIRMED_OUT", "INJURED_LIST_PLACED", "LATE_SCRATCH", "DAY_TO_DAY"}
        latest_by_player: dict[str, str] = {}
        for event in events:  # already newest-first
            key = event.player_source_id or event.player_name
            if key and key not in latest_by_player:
                latest_by_player[key] = event.event_type
        out_count = sum(1 for event_type in latest_by_player.values() if event_type in out_types)
        unavailable = out_count if (authoritative or out_count > 0) else None

        if ctx is not None:
            rescores = ctx.rescores_by_game.get(game.id, [])  # newest completed first
            rescore = rescores[0] if rescores else None
        else:
            rescore = self.session.scalar(
                select(ShadowRescore)
                .where(ShadowRescore.game_id == game.id)
                .order_by(ShadowRescore.rescore_completed_at.desc())
                .limit(1)
            )
        forecast_before = forecast_after = edge_before = edge_after = None
        book_moved: bool | None = None
        if rescore is not None:
            forecast_after = round(float(rescore.after_home_win_probability) * 100, 1)
            if rescore.before_home_win_probability is not None:
                forecast_before = round(float(rescore.before_home_win_probability) * 100, 1)
            book_moved = bool(rescore.book_moved)
            pre = rescore.pre_quote or {}
            post = rescore.post_quote or {}
            if rescore.before_home_win_probability is not None and pre.get("no_vig_home") is not None:
                edge_before = round((float(rescore.before_home_win_probability) - float(pre["no_vig_home"])) * 100, 1)
            if post.get("no_vig_home") is not None:
                edge_after = round((float(rescore.after_home_win_probability) - float(post["no_vig_home"])) * 100, 1)

        context = AvailabilityContext(
            lineup_status=lineup_status,
            lineup_complete=confirmation["lineup_complete"],
            starting_pitcher_status=starter_status,
            injury_feed_status=injury_feed_status,
            injury_feed_authoritative=authoritative,
            production_injury_coverage=authoritative,
            unavailable_players=unavailable,
            last_availability_update=_aware(last.received_at) if last else None,
            last_availability_event=last.event_type if last else None,
            rescored=rescore is not None,
            forecast_before=forecast_before,
            forecast_after=forecast_after,
            edge_before=edge_before,
            edge_after=edge_after,
            book_moved=book_moved,
        )
        return context, rescore is not None, unresolved_scratch

    @staticmethod
    def _starter_status(card: ForecastGame) -> str:
        away, home = card.away_pitcher, card.home_pitcher
        if away.confirmed and home.confirmed:
            return "CONFIRMED"
        named = all(
            pitcher.name and "unavailable" not in pitcher.name.lower()
            for pitcher in (away, home)
        )
        return "PROBABLE" if named else "MISSING"

    def _market_inputs(
        self,
        game: Game,
        forecast: Forecast,
        home: Team,
        away: Team,
    ) -> list[MarketInput]:
        """Adapt the latest persisted two-sided quotes into pricing inputs."""

        rows = self._latest_market_snapshots(game.id)
        simulation = forecast.simulation_summary or {}
        uncertainty = forecast.uncertainty or 0.0
        markets: dict[Any, dict[str, Any]] = {}
        for market, outcome, snapshot, book in rows:
            if market.market_type not in {MarketType.MONEYLINE, MarketType.TOTAL, MarketType.SPREAD}:
                continue
            entry = markets.setdefault(market.id, {"type": market.market_type, "books": defaultdict(dict)})
            entry["books"][book.id][outcome.id] = (outcome, snapshot, book)

        inputs: list[MarketInput] = []
        for entry in markets.values():
            market_type = entry["type"]
            sides: dict[str, dict[str, Any]] = {}
            for outcomes in entry["books"].values():
                # A governed price requires a complete two-sided quote at the book.
                if len(outcomes) != 2:
                    continue
                (outcome_a, snapshot_a, book_a), (outcome_b, snapshot_b, book_b) = list(outcomes.values())
                for (outcome, snapshot, book), opp_snapshot in (
                    ((outcome_a, snapshot_a, book_a), snapshot_b),
                    ((outcome_b, snapshot_b, book_b), snapshot_a),
                ):
                    if snapshot.american_odds is None or opp_snapshot.american_odds is None:
                        continue
                    role = self._resolve_side(
                        market_type,
                        outcome,
                        snapshot,
                        forecast.home_win_probability,
                        forecast.away_win_probability,
                        simulation,
                        home,
                        away,
                    )
                    if role is None:
                        continue
                    key, selection, line, model_p = role
                    accum = sides.setdefault(
                        key, {"selection": selection, "line": line, "model_p": model_p, "quotes": []}
                    )
                    accum["quotes"].append(BookQuote(
                        book=book.name,
                        sportsbook_id=str(book.id),
                        market_outcome_id=str(outcome.id),
                        odds_snapshot_id=str(snapshot.id),
                        side_american=int(snapshot.american_odds),
                        opposite_american=int(opp_snapshot.american_odds),
                        observed_at=_aware(snapshot.observed_at),
                    ))
            side_inputs = [
                SideInput(
                    selection=data["selection"],
                    line=data["line"],
                    model_probability=data["model_p"],
                    conservative_probability=(
                        min(max(data["model_p"] - uncertainty, 0.0), 1.0)
                        if data["model_p"] is not None else None
                    ),
                    quotes=tuple(data["quotes"]),
                )
                for data in sides.values()
                if data["quotes"]
            ]
            if side_inputs:
                inputs.append(MarketInput(
                    market_label=_market_label(market_type),
                    period="Full game",
                    sides=tuple(side_inputs),
                ))
        return inputs

    @staticmethod
    def _resolve_side(
        market_type: MarketType,
        outcome: MarketOutcome,
        snapshot: OddsSnapshot,
        home_win_probability: float | None,
        away_win_probability: float | None,
        simulation: dict[str, Any],
        home: Team,
        away: Team,
    ) -> tuple[str, str, float | None, float | None] | None:
        """Return (side_key, label, line, model_probability) for one outcome."""

        if market_type == MarketType.MONEYLINE:
            if outcome.participant_team_id == home.id:
                return "HOME", f"{home.abbreviation} ML", None, home_win_probability
            if outcome.participant_team_id == away.id:
                return "AWAY", f"{away.abbreviation} ML", None, away_win_probability
            return None
        if market_type == MarketType.SPREAD:
            point = snapshot.point
            if point is None:
                return None
            key = f"{point:+g}"
            if outcome.participant_team_id == home.id:
                probability = (simulation.get("home_cover_probabilities") or {}).get(key)
                return "HOME", f"{home.abbreviation} {key}", point, probability
            if outcome.participant_team_id == away.id:
                probability = (simulation.get("away_cover_probabilities") or {}).get(key)
                return "AWAY", f"{away.abbreviation} {key}", point, probability
            return None
        if market_type == MarketType.TOTAL:
            point = snapshot.point
            if point is None:
                return None
            label = (outcome.label or "").lower()
            if label not in {"over", "under"}:
                return None
            over = _simulation_over_probability(simulation, point)
            probability = over if label == "over" else max(0.0, 1.0 - over)
            return label.upper(), f"{'Over' if label == 'over' else 'Under'} {point:g}", point, probability
        return None

    def _component_rows(self, forecast: Forecast) -> list[ForecastComponent]:
        ctx = self._prefetched_game(forecast.game_id)
        if ctx is not None and forecast.id in ctx.components_by_forecast:
            return list(ctx.components_by_forecast[forecast.id])
        if ctx is not None and ctx.latest_forecast_by_game.get(forecast.game_id) is forecast:
            return []  # loaded for the slate; this forecast simply has no components
        return list(self.session.scalars(
            select(ForecastComponent).where(ForecastComponent.forecast_id == forecast.id)
        ))

    def _component_disagreement(self, forecast: Forecast) -> float | None:
        """Spread (percentage points) of ensemble component home-win probabilities."""

        probabilities = [
            float(component.output["home_win_probability"])
            for component in self._component_rows(forecast)
            if isinstance(component.output, dict) and component.output.get("home_win_probability") is not None
        ]
        if len(probabilities) < 2:
            return None
        return round((max(probabilities) - min(probabilities)) * 100, 2)

    @staticmethod
    def _has_critical_missing(forecast: Forecast) -> bool:
        """True when a component the readiness policy marks critical is absent/stale.

        Uses the persisted governed readiness components — no new threshold. A
        critical input gap (e.g. an unposted lineup or unconfirmed starter at a
        stage where it is required) blocks QUALIFIED and yields PROVISIONAL_EDGE.
        """

        for key, raw in (forecast.readiness_components or {}).items():
            if key.startswith("_") or not isinstance(raw, dict):
                continue
            if raw.get("critical") and str(raw.get("status", "")).lower() in {"missing", "stale"}:
                return True
        return False

    def _game_sport(self, game: Game) -> str:
        """League label for the board, resolved from the game's canonical league.

        Previously hardcoded to MLB. It now reads the governed identity, so a
        board row is labelled by what it actually is. Unknown identities fail
        closed to the league code rather than silently claiming MLB.
        """

        from app.models import League

        ctx = self._prefetched_game(game.id)
        if ctx is not None:
            code = ctx.league_code_by_id.get(game.league_id)
        else:
            code = self.session.scalar(select(League.code).where(League.id == game.league_id))
        if not code:
            return "UNKNOWN"
        entry = resolve_stored_identity(None, code)
        return str(entry.league_code) if entry is not None else str(code).upper()

    @staticmethod
    def _recommendation_reason(
        forecast: Forecast | None,
        best: Candidate | None,
        ready_floor: float,
    ) -> str:
        if forecast is None:
            return "Forecast unavailable — required pregame inputs are incomplete."
        if best is None:
            return "No complete two-sided governed quote is available for any market."
        edge = best.uncertainty_adjusted_edge
        if best.recommendation == "QUALIFIED_BET":
            return (
                f"Uncertainty-adjusted edge {edge * 100:+.1f} pts clears the qualified bar "
                f"with positive expected value, complete critical inputs, and a fresh quote."
            )
        if best.recommendation == EXTREME_DISAGREEMENT:
            gap = best.raw_edge * 100 if best.raw_edge is not None else 0.0
            return (
                f"Model–market gap {gap:+.1f} pts exceeds the review threshold — "
                f"EXTREME DISAGREEMENT. It requires a complete, fresh quote, confirmed "
                f"critical inputs, and a successful rescore before it can be qualified."
            )
        if best.recommendation == PROVISIONAL_EDGE:
            readiness = forecast.readiness_score or 0.0
            return (
                f"Edge {edge * 100:+.1f} pts clears the value bar, but critical configured "
                f"inputs are incomplete (readiness {readiness:.0f} < {ready_floor:.0f} or a "
                f"required lineup/starter is unconfirmed) — provisional, not a qualified bet."
            )
        if best.recommendation == "WATCH":
            raw = best.raw_edge * 100 if best.raw_edge is not None else 0.0
            return (
                f"Model–market disagreement {raw:+.1f} pts, but it does not clear the "
                f"qualified value bar (uncertainty-adjusted edge or expected value)."
            )
        if best.is_stale:
            return "The latest governed quote is stale; no qualified position is taken."
        return "No positive-expected-value edge versus the no-vig market; no position."

    @staticmethod
    def _top_opportunity_label(best: Candidate) -> str:
        price = f"{best.american_odds:+d}" if best.american_odds is not None else "—"
        book = f" · {best.book}" if best.book else ""
        return f"{best.selection} {price}{book}"

    def _v2_policy_view(self, game, forecast, availability, views, now):
        """Read-only V2 practical-policy evaluation for one game's moneyline.

        Point-in-time and side-effect free: it never creates a decision or
        position — the audited freeze job does that, and only on/after the
        challenger's prospective effective date."""
        from datetime import timedelta as _td

        from app.bankroll.service import StakePolicy, StakingStrategy, calculate_stake
        from app.schemas.api import V2PolicyView
        from app.services.practical_policy import POLICY_VERSION, PracticalPolicyEvaluator
        from app.services.pregame_evidence import pregame_evidence_summary
        from app.services.shadow_experiment_service import (
            FINAL_PREGAME_CUTOFF_MINUTES,
            V2_DECISION_POLICY,
            ShadowExperimentService,
        )

        if game.starts_at is None:
            return None
        moneyline = next(
            (view for view in views if (view.market_type or "").upper() == "MONEYLINE"), None,
        )
        cutoff = _aware(game.starts_at) - _td(minutes=FINAL_PREGAME_CUTOFF_MINUTES)
        service = ShadowExperimentService(self.session, self.settings)
        experiment = service.active_experiment(V2_DECISION_POLICY)
        slate_date = _aware(game.starts_at).astimezone(ZoneInfo(self.settings.display_timezone)).date()
        active = experiment is not None and slate_date >= experiment.start_date
        is_final = now >= cutoff
        final_decision = None
        ctx = self._prefetched_game(game.id)
        if is_final and active:
            if ctx is not None:
                final_decision = ctx.final_decision_by_experiment_game.get((experiment.id, game.id))
            else:
                from app.models import ExperimentDecisionPhase, ShadowExperimentDecision

                final_decision = self.session.scalar(
                    select(ShadowExperimentDecision).where(
                        ShadowExperimentDecision.experiment_id == experiment.id,
                        ShadowExperimentDecision.game_id == game.id,
                        ShadowExperimentDecision.phase == ExperimentDecisionPhase.FINAL_PREGAME,
                        ShadowExperimentDecision.recommended_at <= cutoff,
                    ).order_by(ShadowExperimentDecision.recommended_at.desc()).limit(1)
                )
        # Post-cutoff the view describes the AT-CUTOFF decision (point-in-time),
        # never a re-litigated later state.
        evaluation = PracticalPolicyEvaluator(self.session, self.settings).evaluate(
            game, forecast, availability, moneyline,
            now=min(now, cutoff - _td(seconds=1)), cutoff=cutoff, final=is_final,
        )
        if final_decision is not None:
            # The audited frozen decision is the authority for the final state.
            evaluation.recommendation = final_decision.recommendation
            evaluation.lifecycle = (
                "FINAL_QUALIFIED" if final_decision.recommendation == "QUALIFIED_BET" else "FINAL_NO_BET"
            )
        stake = None
        if evaluation.qualified and moneyline is not None and moneyline.decimal_odds:
            if experiment is None:
                bankroll = float(self.settings.shadow_virtual_bankroll)
            elif ctx is not None:
                # Ledger-derived value memoised once per request (one settlement
                # scan), never per qualified game.
                key = f"bankroll:{experiment.id}"
                if key not in ctx.memo:
                    ctx.memo[key] = service.current_bankroll(experiment)
                bankroll = ctx.memo[key]
            else:
                bankroll = service.current_bankroll(experiment)
            policy = StakePolicy(
                strategy=StakingStrategy.FRACTIONAL_KELLY,
                kelly_fraction=self.settings.shadow_kelly_fraction,
                maximum_stake_percentage=evaluation.position_cap_fraction
                or self.settings.shadow_max_position_percentage,
            )
            stake = round(min(
                calculate_stake(
                    bankroll, policy,
                    model_probability=float(evaluation.adjusted_probability or 0.0),
                    decimal_odds=float(moneyline.decimal_odds),
                ) * evaluation.evidence_multiplier,
                bankroll * (evaluation.position_cap_fraction or 1.0),
            ), 2)

        def pts(value):
            return round(value * 100, 2) if value is not None else None

        decomposition = evaluation.decomposition or {}
        return V2PolicyView(
            policy_version=POLICY_VERSION,
            tier=evaluation.tier,
            lifecycle=evaluation.lifecycle,
            recommendation=evaluation.recommendation,
            reason=evaluation.reason,
            reasons=list(evaluation.reasons),
            gates_passed=list(evaluation.gates_passed),
            critical_evidence_complete=evaluation.critical_evidence_complete,
            starter_state_home=evaluation.starter_home.state if evaluation.starter_home else None,
            starter_state_away=evaluation.starter_away.state if evaluation.starter_away else None,
            starter_stable_since_home=evaluation.starter_home.stable_since if evaluation.starter_home else None,
            starter_stable_since_away=evaluation.starter_away.stable_since if evaluation.starter_away else None,
            lineup_state=evaluation.lineup.state if evaluation.lineup else None,
            pregame_evidence=pregame_evidence_summary(
                evaluation, cutoff=cutoff, reference=min(now, cutoff),
            ),
            selection=moneyline.selection if moneyline else None,
            raw_edge=pts(evaluation.raw_edge),
            starter_penalty=pts(evaluation.starter_penalty) or 0.0,
            lineup_penalty=pts(evaluation.lineup_penalty) or 0.0,
            adjusted_edge=pts(evaluation.adjusted_edge),
            adjusted_expected_value=pts(evaluation.adjusted_expected_value),
            evidence_multiplier=evaluation.evidence_multiplier,
            theoretical_stake=stake,
            experiment_effective_date=(
                experiment.start_date.isoformat() if experiment is not None else None
            ),
            active=active,
            feature_decomposition={
                "version": decomposition.get("version"),
                "model_required_missing": decomposition.get("model_required_missing", []),
                "duplicate_components": len(decomposition.get("duplicate_components", [])),
                "fallback_components": len(decomposition.get("fallback_components", [])),
                "optional_components": len(decomposition.get("optional_components", [])),
            } if decomposition else None,
        )

    @staticmethod
    def _opportunity_view(candidate: Candidate) -> OpportunitySelection:
        def pct(value: float | None) -> float | None:
            return round(value * 100, 2) if value is not None else None

        return OpportunitySelection(
            recommendation=candidate.recommendation,
            market_type=candidate.market_label,
            period=candidate.period,
            selection=candidate.selection,
            line=candidate.line,
            sportsbook=candidate.book,
            sportsbook_id=candidate.sportsbook_id,
            market_outcome_id=candidate.market_outcome_id,
            odds_snapshot_id=candidate.odds_snapshot_id,
            american_odds=candidate.american_odds,
            decimal_odds=round(candidate.decimal_odds, 4) if candidate.decimal_odds is not None else None,
            model_probability=pct(candidate.model_probability),
            no_vig_probability=pct(candidate.no_vig_probability),
            consensus_probability=pct(candidate.consensus_probability),
            fair_odds=candidate.fair_odds,
            raw_edge=pct(candidate.raw_edge),
            uncertainty_adjusted_edge=pct(candidate.uncertainty_adjusted_edge),
            expected_value=pct(candidate.expected_value),
            quote_observed_at=candidate.observed_at,
            quote_age_seconds=candidate.quote_age_seconds,
            is_stale=candidate.is_stale,
            virtual_stake=candidate.virtual_stake,
            potential_profit=candidate.potential_profit,
            total_return=candidate.total_return,
            opportunity_score=candidate.opportunity_score,
            model_probability_fraction=candidate.model_probability,
            no_vig_probability_fraction=candidate.no_vig_probability,
            edge_fraction=candidate.raw_edge,
            expected_value_fraction=candidate.expected_value,
        )

    def game_card(self, game: Game) -> ForecastGame:
        home_side, home, away_side, away = self._sides(game.id)
        forecast = self._latest_forecast(game.id)
        refresh_state = self._latest_refresh_state(game.id, game.source_mode)
        feature_snapshot_id = (
            forecast.feature_snapshot_id
            if forecast and forecast.feature_snapshot_id
            else refresh_state.last_feature_snapshot_id if refresh_state else None
        )
        feature_snapshot = self.session.get(GameFeatureSnapshot, feature_snapshot_id) if feature_snapshot_id else None
        if feature_snapshot is None:
            feature_snapshot = self._latest_feature_snapshot(game.id, game.source_mode)
        snapshot_readiness = (
            self._snapshot_readiness(game, feature_snapshot)
            if forecast is None and feature_snapshot is not None
            else None
        )
        lineage_cutoff = (
            _aware(feature_snapshot.data_as_of)
            if feature_snapshot
            else _aware(forecast.available_as_of)
            if forecast
            else _aware(refresh_state.available_as_of) if refresh_state else datetime.now(UTC)
        )
        away_lineup = self._lineup_summary(
            game,
            away.id,
            lineage_cutoff,
            feature_snapshot.away_lineup_observation_id if feature_snapshot else None,
        )
        home_lineup = self._lineup_summary(
            game,
            home.id,
            lineage_cutoff,
            feature_snapshot.home_lineup_observation_id if feature_snapshot else None,
        )
        venue = self.session.get(Venue, game.venue_id) if game.venue_id else None
        market = self._market_context(game.id, home.id, away.id)
        # One selector decides which engine this surface leads with, so the
        # slate, the market board, the analysis page and the simulator cannot
        # disagree about which forecast a game "has". A V18 game shows V18's
        # probability AND a band recomputed from it with the same thresholds --
        # leaving Gen 1's band beside a V18 number would attribute a judgement
        # to a model that never made it.
        from app.services.forecast_selection import ForecastSelector

        selection = ForecastSelector(self.session).select(game)
        primary = selection.primary
        # The selected forecast is the only thing the primary fields ever show.
        # A published-but-limited forecast stays on screen with its limitation
        # stated; a forecast the controlling engine never published leaves these
        # fields EMPTY and says so.
        #
        # The removed branch fell through to Gen 1 here. It was labelled, but a
        # labelled substitution is still a substitution: the slate then read
        # "SEA 52% / 48% OAK" for a game V18 had said nothing about, and a
        # reader comparing that number against a V18 forecast elsewhere was
        # comparing two different models. Gen 1 remains reachable as an explicit
        # comparison in `forecast_selection.comparison`.
        use_primary = primary is not None and primary.home_win_probability is not None
        home_probability = primary.home_win_probability if use_primary else None
        away_probability = primary.away_win_probability if use_primary else None
        market_home = market["home_probability"]
        home_edge = model_edge(home_probability, market_home)
        away_edge = (
            model_edge(away_probability, 1.0 - market_home)
            if market_home is not None else None
        )
        if home_edge is not None and away_edge is not None and max(home_edge, away_edge) > 0:
            edge_side = "home" if home_edge >= away_edge else "away"
            edge = max(home_edge, away_edge)
        else:
            edge_side, edge = "none", None
        opportunity = classify_opportunity(
            edge,
            watch=self.settings.opportunity_watch_threshold,
            standard=self.settings.opportunity_standard_threshold,
            strong=self.settings.opportunity_strong_threshold,
        )
        last_update = max(
            [_aware(value) for value in [forecast.generated_at if forecast else None, market["last_update"]] if value],
            default=_aware(game.created_at),
        )
        status = game.status.value.upper()
        # Keyed on the SELECTED forecast, not on whether a legacy Gen 1 row
        # exists. A game the controlling engine never forecast has no primary
        # number to show even when Gen 1 published one, and the status has to
        # agree with the empty fields rather than with the other engine's row.
        if not use_primary:
            status = f"{status} · FORECAST UNAVAILABLE"
        # Which engine actually produced the numbers below. Usually the
        # selected one; when the selection has no numbers at all it is the
        # engine the fallback came from. A chip reading "V18" above a Gen 1
        # probability is the exact mislabelling this field prevents.
        selection_payload = selection.as_dict()
        selection_payload["displayed_engine_id"] = primary.engine_id if use_primary else None
        # Every forecast-derived field below comes from the DISPLAYED engine.
        # The Gen 1 row (`forecast`) feeds them only when Gen 1 is the engine on
        # screen; beside a V18 probability its projected score, fair price and
        # confidence would be another model's numbers under V18's name, and with
        # no displayed engine there is nothing to show at all.
        from app.services.forecast_selection import GEN1_ENGINE

        gen1_row = forecast if use_primary and primary.engine_id == GEN1_ENGINE else None
        if gen1_row is not None:
            projected_home = gen1_row.expected_home_runs
            projected_away = gen1_row.expected_away_runs
            fair_home, fair_away = gen1_row.fair_home_moneyline, gen1_row.fair_away_moneyline
            model_label = self._forecast_model_label(gen1_row)
        elif use_primary:
            projected_home = primary.expected_home_score
            projected_away = primary.expected_away_score
            fair_home = _fair_moneyline(home_probability)
            fair_away = _fair_moneyline(away_probability)
            model_label = primary.model_label
        else:
            projected_home = projected_away = fair_home = fair_away = model_label = None
        both_scores = projected_home is not None and projected_away is not None
        return ForecastGame(
            id=str(game.id),
            forecast_selection=selection_payload,
            league_code=_league_code_for(self.session, game),
            start_time=_aware(game.starts_at),
            status=status,
            venue=venue.name if venue else "Venue unavailable",
            away_team=self._team_summary(away, away_side),
            home_team=self._team_summary(home, home_side),
            away_pitcher=self._pitcher_summary(
                away_side,
                away.id,
                game.id,
                cutoff=lineage_cutoff,
                observation_id=(
                    feature_snapshot.away_starter_observation_id if feature_snapshot else None
                ),
                starter_era=(
                    feature_snapshot.feature_values.get("away_starter_era")
                    if feature_snapshot else None
                ),
                starter_xera=(
                    feature_snapshot.feature_values.get("away_starter_xera")
                    if feature_snapshot else None
                ),
                snapshot_bound=feature_snapshot is not None,
            ),
            home_pitcher=self._pitcher_summary(
                home_side,
                home.id,
                game.id,
                cutoff=lineage_cutoff,
                observation_id=(
                    feature_snapshot.home_starter_observation_id if feature_snapshot else None
                ),
                starter_era=(
                    feature_snapshot.feature_values.get("home_starter_era")
                    if feature_snapshot else None
                ),
                starter_xera=(
                    feature_snapshot.feature_values.get("home_starter_xera")
                    if feature_snapshot else None
                ),
                snapshot_bound=feature_snapshot is not None,
            ),
            # A Gen 1 display still needs its valid (published) row; a draft the
            # selector saw does not make a forecast available.
            forecast_available=use_primary and (primary.engine_id != GEN1_ENGINE or forecast is not None),
            forecast_generated_at=(
                _aware(gen1_row.generated_at) if gen1_row is not None
                else _aware(primary.published_utc) if use_primary and primary.published_utc
                else None
            ),
            forecast_model_label=model_label,
            market_available=bool(market["has_moneyline"]),
            projected_away_runs=projected_away,
            projected_home_runs=projected_home,
            projected_total=(
                gen1_row.expected_total_runs if gen1_row is not None
                else projected_home + projected_away if both_scores else None
            ),
            projected_run_difference=(
                gen1_row.expected_run_difference if gen1_row is not None
                else projected_home - projected_away if both_scores else None
            ),
            uncertainty=gen1_row.uncertainty if gen1_row is not None else None,
            market_total=market["total"],
            market_quote_observed_at=market["quote_observed_at"],
            market_providers=market["providers"],
            market_books=market["books"],
            away_win_probability=away_probability * 100 if away_probability is not None else None,
            home_win_probability=home_probability * 100 if home_probability is not None else None,
            fair_away_moneyline=fair_away,
            fair_home_moneyline=fair_home,
            best_away_moneyline=market["best_away"] if market["has_moneyline"] else None,
            best_home_moneyline=market["best_home"] if market["has_moneyline"] else None,
            market_home_probability=(
                market["home_probability"] * 100
                if market["home_probability"] is not None else None
            ),
            model_edge=edge * 100 if edge is not None else None,
            edge_side=edge_side,
            opportunity=opportunity,
            confidence=gen1_row.confidence if gen1_row is not None else None,
            data_readiness=(
                forecast.readiness_score
                if forecast
                else snapshot_readiness.score if snapshot_readiness else None
            ),
            data_as_of=(
                _aware(feature_snapshot.data_as_of)
                if feature_snapshot
                else _aware(forecast.available_as_of)
                if forecast
                else _aware(refresh_state.available_as_of) if refresh_state else None
            ),
            lifecycle_stage=(
                refresh_state.lifecycle_stage
                if refresh_state
                else forecast.lifecycle.value.upper() if forecast else "NOT_STARTED"
            ),
            time_to_first_pitch_seconds=refresh_state.time_to_first_pitch_seconds if refresh_state else None,
            last_refresh_at=_aware(refresh_state.last_source_refresh_at) if refresh_state and refresh_state.last_source_refresh_at else None,
            next_scheduled_refresh_at=(
                _aware(refresh_state.next_scheduled_refresh_at)
                if refresh_state and refresh_state.next_scheduled_refresh_at
                else None
            ),
            final_forecast_status=(
                refresh_state.final_forecast_status
                if refresh_state
                else "PUBLISHED" if forecast and forecast.lifecycle.value.upper() == "FINAL" else "NOT_STARTED"
            ),
            final_snapshot_frozen=refresh_state.final_snapshot_frozen if refresh_state else None,
            away_lineup=away_lineup,
            home_lineup=home_lineup,
            readiness_detail=(
                self._readiness_detail(forecast)
                if forecast
                else self._snapshot_readiness_detail(snapshot_readiness)
            ),
            books_reporting=market["book_count"],
            last_update=_aware(last_update),
        )

    def game_analysis(self, game_id: Any) -> GameAnalysisData | None:
        game = self.session.get(Game, game_id)
        if not game:
            return None
        card = self.game_card(game)
        forecast = self._latest_forecast(game.id)
        if not forecast or not card.forecast_available:
            return self._empty_game_analysis(game, card)
        analysis = self._gen1_game_analysis(game, card, forecast)
        displayed = (card.forecast_selection or {}).get("displayed_engine_id")
        from app.services.forecast_selection import GEN1_ENGINE

        if displayed == GEN1_ENGINE:
            return analysis
        # The page leads with another engine (V18). The Gen 1 row still supplies
        # the AISP input panels -- weather, starters, offense, bullpen, prices,
        # data audit -- but its probabilities, components, drivers, simulated
        # distributions and version history belong to Gen 1 and are withheld
        # rather than shown under the displayed engine's name. That engine's
        # own distribution lives on the simulation page.
        primary = (card.forecast_selection or {}).get("primary") or {}
        comparison = [{"label": card.forecast_model_label or "Displayed model",
                       "home": card.home_win_probability, "away": card.away_win_probability}]
        comparison += [row for row in analysis.probability_comparison if row["label"] == "No-vig market"]
        version = (
            f"{primary.get('snapshot_type') or 'FORECAST'} · v{primary['snapshot_version']}"
            if primary.get("snapshot_version") is not None else primary.get("snapshot_type") or "PUBLISHED"
        )
        return analysis.model_copy(update={
            "model_version": " · ".join(
                part for part in (card.forecast_model_label, primary.get("engine_version")) if part
            ),
            "forecast_version": version,
            "probability_comparison": comparison,
            "run_distribution": [], "total_run_distribution": [], "run_difference_distribution": [],
            "model_components": [], "drivers": [], "forecast_history": [],
            "simulation": {"iterations": 0, "home_cover_probability": 0, "over_probability": 0,
                           "most_likely_score": "Unavailable"},
        })

    def _gen1_game_analysis(self, game: Game, card: ForecastGame, forecast: Forecast) -> GameAnalysisData:
        home_side, home, away_side, away = self._sides(game.id)
        feature_snapshot = (
            self.session.get(GameFeatureSnapshot, forecast.feature_snapshot_id)
            if forecast.feature_snapshot_id else None
        )
        lineage_cutoff = (
            _aware(feature_snapshot.data_as_of)
            if feature_snapshot else _aware(forecast.available_as_of)
        )
        snapshot_values = feature_snapshot.feature_values if feature_snapshot else {}
        snapshot_missing = set(feature_snapshot.missing_features or []) if feature_snapshot else set()

        def snapshot_value(key: str) -> Any:
            return None if key in snapshot_missing else snapshot_values.get(key)

        home_feature = self._latest_feature(home.id, lineage_cutoff, game.source_mode)
        away_feature = self._latest_feature(away.id, lineage_cutoff, game.source_mode)
        home_values, away_values = (home_feature.values if home_feature else {}), (away_feature.values if away_feature else {})
        weather = (
            self.session.get(WeatherSnapshot, feature_snapshot.weather_snapshot_id)
            if feature_snapshot and feature_snapshot.weather_snapshot_id
            else self.session.scalar(select(WeatherSnapshot).where(
                WeatherSnapshot.game_id == game.id,
                WeatherSnapshot.available_as_of <= lineage_cutoff,
                WeatherSnapshot.source_mode == game.source_mode,
            ).order_by(WeatherSnapshot.available_as_of.desc()).limit(1))
        )
        market = self._market_context(game.id, home.id, away.id, cutoff=lineage_cutoff)
        model_version = self.session.get(ModelVersion, forecast.model_version_id)
        components = list(self.session.scalars(select(ForecastComponent).where(ForecastComponent.forecast_id == forecast.id)))
        # History shows every immutable version — the morning publication plus any
        # intraday rescores — each rendered with the input state known at its OWN
        # cutoff. Newer versions never overwrite older ones.
        history = list(self.session.scalars(select(Forecast).where(
            Forecast.game_id == game.id,
            Forecast.forecast_track == ForecastTrack.CURRENT_DEVELOPMENT,
            Forecast.source_mode == game.source_mode,
            self._valid_current_versions(game.id),
        ).order_by(Forecast.generated_at)))
        rescore_reasons = self._rescore_reasons(game.id)
        simulation = forecast.simulation_summary or {}
        home_dist = simulation.get("home_runs_distribution") or {}
        away_dist = simulation.get("away_runs_distribution") or {}
        run_distribution = [
            {"runs": runs, "away": round(float(away_dist.get(str(runs), 0)) * 100, 2), "home": round(float(home_dist.get(str(runs), 0)) * 100, 2)}
            for runs in range(10)
        ]
        baseline = next((
            component.output
            for component in components
            if component.component_name == "record_strength_baseline"
        ), {})
        feature_updated = max(
            (_aware(item.available_as_of) for item in (home_feature, away_feature) if item),
            default=_aware(forecast.available_as_of),
        )
        if feature_snapshot:
            starter_updated = _snapshot_evidence_timestamp(feature_snapshot, "home_starter", "away_starter")
            audit_updates = {
                "schedule": _snapshot_evidence_timestamp(feature_snapshot, "game_state"),
                "probable_pitchers": starter_updated,
                "pitcher_confirmation": starter_updated,
                "lineups": _snapshot_evidence_timestamp(feature_snapshot, "home_lineup", "away_lineup"),
                "roster": _snapshot_evidence_timestamp(feature_snapshot, "roster"),
                "team_stats": _snapshot_evidence_timestamp(feature_snapshot, "home_team_feature", "away_team_feature"),
                "statcast": _snapshot_evidence_timestamp(feature_snapshot, "statcast", "talent"),
                "bullpen": _snapshot_evidence_timestamp(feature_snapshot, "bullpen"),
                "odds": market["last_update"],
                "sportsbook_count": market["last_update"],
                "weather": _snapshot_evidence_timestamp(feature_snapshot, "weather"),
                "feature_completeness": _aware(feature_snapshot.generated_at),
            }
        else:
            audit_updates = {
                "schedule": _aware(game.created_at),
                "probable_pitchers": _aware(home_side.pitcher_status_observed_at or forecast.available_as_of),
                "pitcher_confirmation": _aware(home_side.pitcher_status_observed_at or forecast.available_as_of),
                "team_stats": feature_updated,
                "odds": market["last_update"],
                "sportsbook_count": market["last_update"],
                "weather": _aware(weather.available_as_of) if weather else None,
                "feature_completeness": feature_updated,
            }
        data_audit = [
            {
                "source": key.replace("_", " ").title(),
                "status": _audit_status(value.get("status", "missing")),
                "updated": audit_updates.get(key).isoformat() if audit_updates.get(key) else "Never",
                "detail": value.get("detail", "No detail supplied"),
            }
            for key, value in (forecast.readiness_components or {}).items()
            if not key.startswith("_")
        ]
        most_home = _distribution_mode(home_dist)
        most_away = _distribution_mode(away_dist)
        probability_comparison = [{
            "label": "ASP ensemble",
            "home": forecast.home_win_probability * 100,
            "away": forecast.away_win_probability * 100,
        }]
        if baseline.get("home_win_probability") is not None:
            baseline_probability = float(baseline["home_win_probability"])
            probability_comparison.append({
                "label": "Record strength baseline",
                "home": baseline_probability * 100,
                "away": (1 - baseline_probability) * 100,
            })
        if market["home_probability"] is not None:
            probability_comparison.insert(1, {
                "label": "No-vig market",
                "home": market["home_probability"] * 100,
                "away": (1 - market["home_probability"]) * 100,
            })
        return GameAnalysisData(
            data_mode=self._game_data_mode(game.id),
            game=card,
            data_as_of=lineage_cutoff,
            model_version=f"{model_version.name} v{model_version.version}" if model_version else "Unknown model version",
            forecast_version=f"{forecast.lifecycle.value.upper()} · v{forecast.version}",
            weather={
                "temperature_f": weather.temperature_f if weather else None,
                "description": (weather.raw_payload or {}).get("description", "Weather unavailable") if weather else "Weather unavailable",
                "wind_mph": weather.wind_mph if weather else None,
                "wind_direction": weather.wind_direction or "Unavailable" if weather else "Unavailable",
                "humidity": (weather.raw_payload or {}).get("humidity_percent") if weather else None,
                "run_effect": None,
            },
            probability_comparison=probability_comparison,
            run_distribution=run_distribution,
            total_run_distribution=_distribution_points(simulation.get("total_runs_distribution") or {}),
            run_difference_distribution=_distribution_points(simulation.get("run_differential_distribution") or {}),
            market_prices=market["market_prices"],
            pitcher_metrics=[
                self._metric("ERA", away_values.get("starter_era"), home_values.get("starter_era"), lower=True, digits=2),
                self._metric(
                    "xERA",
                    snapshot_value("away_starter_xera"),
                    snapshot_value("home_starter_xera"),
                    lower=True,
                    digits=2,
                ),
                self._metric("Confirmation", "Confirmed" if away_side.pitcher_confirmed else "Probable", "Confirmed" if home_side.pitcher_confirmed else "Probable"),
            ],
            offense_metrics=[
                self._metric("Runs / game", away_values.get("runs_per_game"), home_values.get("runs_per_game"), digits=2),
                self._metric("OPS", away_values.get("ops"), home_values.get("ops"), digits=3),
                self._metric("OBP", away_values.get("obp"), home_values.get("obp"), digits=3),
                self._metric("SLG", away_values.get("slg"), home_values.get("slg"), digits=3),
                self._metric("Strikeout rate", away_values.get("strikeout_rate"), home_values.get("strikeout_rate"), lower=True, percent=True),
                self._metric("Walk rate", away_values.get("walk_rate"), home_values.get("walk_rate"), percent=True),
            ],
            bullpen_metrics=[
                self._metric("Season ERA", away_values.get("bullpen_era"), home_values.get("bullpen_era"), lower=True, digits=2),
                self._metric(
                    "Workload availability",
                    snapshot_value("away_bullpen_availability"),
                    snapshot_value("home_bullpen_availability"),
                    percent=True,
                ),
            ],
            statcast_metrics=[
                self._metric("Lineup xwOBA", snapshot_value("away_lineup_xwoba"), snapshot_value("home_lineup_xwoba"), digits=3),
                self._metric("Statcast xwOBA", snapshot_value("away_statcast_xwoba"), snapshot_value("home_statcast_xwoba"), digits=3),
                self._metric("Barrel rate", snapshot_value("away_statcast_barrel_rate"), snapshot_value("home_statcast_barrel_rate"), percent=True),
                self._metric("Hard-hit rate", snapshot_value("away_statcast_hard_hit_rate"), snapshot_value("home_statcast_hard_hit_rate"), percent=True),
                self._metric("Average exit velocity", snapshot_value("away_statcast_average_exit_velocity"), snapshot_value("home_statcast_average_exit_velocity"), digits=1),
            ],
            model_components=[{
                "model": component.component_name,
                "home_probability": float(component.output.get("home_win_probability", 0)) * 100,
                "expected_margin": float(component.output.get("expected_home_runs", 0)) - float(component.output.get("expected_away_runs", 0)),
                "weight": (component.weight or 0) * 100,
                "trained_artifact": bool(component.output.get("trained_artifact")),
                "model_type": component.output.get("model_type"),
            } for component in components],
            drivers=[{
                "label": driver.get("label", "Model driver"),
                "impact": float(driver.get("impact", 0)) * 100,
                "detail": driver.get("detail", "Associated with the forecast; not a causal claim"),
            } for driver in (forecast.model_drivers or [])],
            forecast_history=[{
                "time": item.generated_at.isoformat(),
                "version": f"{item.lifecycle.value.upper()} · v{item.version}",
                "home_probability": item.home_win_probability * 100,
                "market_probability": (
                    self._forecast_market_probability(item.id, home.id) * 100
                    if self._forecast_market_probability(item.id, home.id) is not None
                    else None
                ),
                # Point-in-time facets of THIS version (its own cutoff): the lineup
                # state it was built on, its expected score, and — for an intraday
                # rescore — the availability change that triggered it.
                "lineup_status": self._version_lineup_state(item),
                "expected_home_runs": item.expected_home_runs,
                "expected_away_runs": item.expected_away_runs,
                "rescore_reason": rescore_reasons.get(item.id),
            } for item in history],
            data_audit=data_audit,
            simulation={
                "iterations": int(simulation.get("simulations", 0)),
                "home_cover_probability": float((simulation.get("home_cover_probabilities") or {}).get("-1.5", 0)) * 100,
                "over_probability": _simulation_over_probability(simulation, market["total"]) * 100,
                "most_likely_score": f"{home.abbreviation} {most_home} – {away.abbreviation} {most_away}",
            },
        )

    def market_board(self, event_date: date) -> MarketBoardData:
        rows: list[MarketBoardRow] = []
        all_books: set[str] = set()
        for game in self._games_for_date(event_date, MLB):
            home_side, home, away_side, away = self._sides(game.id)
            forecast = self._latest_forecast(game.id)
            snapshots = self._latest_market_snapshots(game.id)
            grouped: dict[Any, list[tuple[Market, MarketOutcome, OddsSnapshot, Sportsbook]]] = defaultdict(list)
            paired: dict[tuple[Any, Any], list[tuple[Market, MarketOutcome, OddsSnapshot, Sportsbook]]] = defaultdict(list)
            for market, outcome, snapshot, book in snapshots:
                grouped[outcome.id].append((market, outcome, snapshot, book))
                paired[(market.id, book.id)].append((market, outcome, snapshot, book))
                all_books.add(book.name)
            for outcome_rows in grouped.values():
                market, outcome = outcome_rows[0][0], outcome_rows[0][1]
                quotes = [row[2] for row in outcome_rows]
                books = [MarketBookPrice(book=row[3].name, price=row[2].american_odds or 0) for row in outcome_rows]
                if not books:
                    continue
                best_index = max(range(len(quotes)), key=lambda index: quotes[index].decimal_odds)
                books[best_index].best = True
                asp_probability = 0.0
                if forecast and market.market_type == MarketType.MONEYLINE:
                    asp_probability = forecast.home_win_probability if outcome.participant_team_id == home.id else forecast.away_win_probability
                elif forecast and market.market_type == MarketType.TOTAL and (outcome.label.lower() == "over"):
                    total_probs = (forecast.simulation_summary or {}).get("total_probabilities") or {}
                    asp_probability = float((total_probs.get(f"{quotes[0].point:g}") or {}).get("over", 0)) if quotes[0].point is not None else 0
                elif forecast and market.market_type == MarketType.TOTAL:
                    total_probs = (forecast.simulation_summary or {}).get("total_probabilities") or {}
                    asp_probability = float((total_probs.get(f"{quotes[0].point:g}") or {}).get("under", 0)) if quotes[0].point is not None else 0
                elif forecast and market.market_type == MarketType.SPREAD:
                    covers = (forecast.simulation_summary or {}).get("home_cover_probabilities") if outcome.participant_team_id == home.id else (forecast.simulation_summary or {}).get("away_cover_probabilities")
                    asp_probability = float((covers or {}).get(f"{quotes[0].point:+g}", 0)) if quotes[0].point is not None else 0
                no_vig_values: list[float] = []
                for _, selected_outcome, selected_snapshot, selected_book in outcome_rows:
                    same_book = paired[(market.id, selected_book.id)]
                    if len(same_book) < 2:
                        continue
                    result = no_vig_probabilities(
                        {str(row[1].id): row[2].decimal_odds for row in same_book},
                        american=False,
                    )
                    selected_probability = result.probabilities.get(str(selected_outcome.id))
                    if selected_probability is not None:
                        no_vig_values.append(selected_probability)
                # Consensus is the mean of the probabilities that actually
                # qualify. A book without a usable implied probability is
                # excluded, never counted as zero, so one missing quote cannot
                # drag the consensus toward 0.
                implied = average_probability(no_vig_values) or average_probability(
                    quote.implied_probability for quote in quotes
                )
                consensus_price = (
                    probability_to_american(implied) if implied is not None
                    else decimal_to_american(
                        sum(quote.decimal_odds for quote in quotes) / len(quotes)
                    )
                )
                fair_price = decimal_to_american(1 / asp_probability) if 0 < asp_probability < 1 else None
                rows.append(MarketBoardRow(
                    id=str(outcome.id), game_id=str(game.id), matchup=f"{away.abbreviation} @ {home.abbreviation}",
                    start_time=_aware(game.starts_at), market_type=_market_label(market.market_type), selection=_selection_label(outcome, quotes[0].point),
                    line=quotes[0].point, asp_fair=fair_price, consensus=consensus_price,
                    # An edge needs both sides; with either missing there is
                    # nothing to compare and the row reports no edge.
                    edge=(
                        edge_value * 100
                        if (edge_value := model_edge(asp_probability, implied)) is not None
                        else None
                    ),
                    books=books, observed_at=max(_aware(quote.observed_at) for quote in quotes),
                ))
        quota = self._odds_quota()
        return MarketBoardData(
            data_mode=self._market_data_mode(event_date, bool(rows)),
            date=event_date,
            books=sorted(all_books),
            quota_remaining=quota,
            rows=rows,
            feed_status=self._market_feed_status(event_date),
        )

    def _market_feed_status(self, event_date: date) -> MarketFeedStatus:
        """The honest state of the sportsbook feed for this slate."""

        from app.jobs.odds_quota_recovery import missing_market_reason, quota_state

        state = quota_state(
            self.session,
            floor=self.settings.odds_quota_floor,
            probe_after_hours=self.settings.odds_quota_probe_after_hours,
            probe_cooldown_hours=self.settings.odds_quota_probe_cooldown_hours,
            renewal_days=self.settings.odds_quota_renewal_days,
        )
        # Scoped: an unqualified MAX over the whole table would let a single
        # research-fallback or demo row make the public feed look fresh while
        # the governed provider is dark.
        latest = _aware(self.session.scalar(
            select(func.max(OddsSnapshot.observed_at)).where(
                OddsSnapshot.source.in_(GOVERNED_SOURCES),
                OddsSnapshot.source_mode == "LIVE",
            )
        ))
        # "Historical" is decided by the observation's own date against the
        # slate being viewed, not by a staleness heuristic.
        historical = bool(
            latest is not None
            and local_date_of(latest, self.settings.display_timezone) < event_date
        )
        return MarketFeedStatus(
            ingestion_blocked=state.latched,
            reason=missing_market_reason(state, latest),
            provider="THE_ODDS_API",
            quota_remaining=state.remaining,
            quota_floor=state.floor,
            quota_observed_at=state.observed_at,
            latest_observation_at=latest,
            latest_observation_is_historical=historical,
            recovery_probe_available=state.probe_allowed,
            recovery_blocked_reason=state.probe_blocked_reason,
        )

    def performance(self) -> PerformanceData:
        historical_dataset = HistoricalResearchReadService(
            self.session
        ).performance_summary()
        experiment_started_at = self.session.scalar(
            select(ShadowExperiment.started_at)
            .where(ShadowExperiment.is_active.is_(True))
            .order_by(ShadowExperiment.version.desc())
            .limit(1)
        )
        performance_query = (
            select(Forecast, Game)
            .join(Game, Game.id == Forecast.game_id)
            .join(GameFeatureSnapshot, GameFeatureSnapshot.id == Forecast.feature_snapshot_id)
            .where(
                Forecast.status == ForecastStatus.PUBLISHED,
                Forecast.forecast_track == ForecastTrack.CURRENT_DEVELOPMENT,
                Forecast.generated_at < Game.starts_at,
                Forecast.published_at.is_not(None),
                Forecast.published_at < Game.starts_at,
                Forecast.source_mode == "LIVE",
                Game.source_mode == "LIVE",
                GameFeatureSnapshot.data_as_of < Game.starts_at,
                GameFeatureSnapshot.source_mode == "LIVE",
                Game.status == GameStatus.FINAL,
                Game.home_score.is_not(None),
                Game.away_score.is_not(None),
            ).order_by(Game.starts_at, Forecast.version.desc())
        )
        if experiment_started_at is not None:
            performance_query = performance_query.where(Game.starts_at >= experiment_started_at)
        all_rows = list(self.session.execute(performance_query))
        rows: list[tuple[Forecast, Game]] = []
        seen_games: set[Any] = set()
        for forecast, game in all_rows:
            if game.id not in seen_games:
                # Public evaluation policy: the latest published pregame
                # forecast for each completed game, never every intraday version.
                rows.append((forecast, game))
                seen_games.add(game.id)
        forecasts = [row[0] for row in rows]
        games = [row[1] for row in rows]
        model = self._forward_model(forecasts)
        offline_evaluation = self._offline_evaluation()
        if not rows:
            return PerformanceData(
                data_mode="empty",
                evaluation_available=False,
                market_benchmark_available=False,
                model_version=f"{model.name} v{model.version} · awaiting settled sample" if model else "No registered model",
                model_status=self._model_status(),
                component_weights=self._component_weights(),
                evaluated_games=0, brier_score=None, log_loss=None, accuracy=None, run_mae=None, total_mae=None,
                versus_market_brier=None, calibration=[], rolling=[],
                model_components=[],
                by_confidence=[],
                offline_evaluation=offline_evaluation,
                historical_dataset=historical_dataset,
                betting=self._experiment_betting(),
            )
        probabilities = [forecast.home_win_probability for forecast in forecasts]
        outcomes = [int((game.home_score or 0) > (game.away_score or 0)) for game in games]
        run_errors_actual = [(game.home_score or 0) - (game.away_score or 0) for game in games]
        run_errors_pred = [forecast.expected_run_difference or 0 for forecast in forecasts]
        total_actual = [(game.home_score or 0) + (game.away_score or 0) for game in games]
        total_pred = [forecast.expected_total_runs or 0 for forecast in forecasts]
        bins = calibration_bins(probabilities, outcomes, 10)
        asp_brier = brier_score(probabilities, outcomes)
        asp_log_loss = log_loss(probabilities, outcomes)
        market_evidence: dict[Any, float] = {}
        for forecast, game in rows:
            home_side = self.session.scalar(select(GameTeam).where(
                GameTeam.game_id == game.id,
                GameTeam.is_home.is_(True),
            ))
            if not home_side:
                continue
            comparison = self.session.execute(
                select(MarketComparison, MarketOutcome)
                .join(MarketOutcome, MarketOutcome.id == MarketComparison.market_outcome_id)
                .where(
                    MarketComparison.forecast_id == forecast.id,
                    MarketComparison.market_no_vig_probability.is_not(None),
                    MarketOutcome.participant_team_id == home_side.team_id,
                )
                .order_by(MarketComparison.observed_at.desc())
                .limit(1)
            ).first()
            if comparison and _aware(comparison[0].observed_at) < _aware(game.starts_at):
                market_evidence[forecast.id] = float(comparison[0].market_no_vig_probability)

        market_rows = [
            (forecast, game, market_evidence[forecast.id])
            for forecast, game in rows
            if forecast.id in market_evidence
        ]
        market_probabilities = [item[2] for item in market_rows]
        market_outcomes = [int((item[1].home_score or 0) > (item[1].away_score or 0)) for item in market_rows]
        market_brier = brier_score(market_probabilities, market_outcomes) if market_rows else None
        asp_market_subset_brier = brier_score(
            [item[0].home_win_probability for item in market_rows],
            market_outcomes,
        ) if market_rows else None

        rolling_groups: dict[str, list[tuple[Forecast, Game]]] = defaultdict(list)
        for forecast, game in rows:
            rolling_groups[_aware(game.starts_at).strftime("%Y-%m")].append((forecast, game))
        rolling = []
        for month, month_rows in sorted(rolling_groups.items()):
            month_probabilities = [forecast.home_win_probability for forecast, _ in month_rows]
            month_outcomes = [int((game.home_score or 0) > (game.away_score or 0)) for _, game in month_rows]
            month_market = [
                (market_evidence[forecast.id], outcome)
                for (forecast, _), outcome in zip(month_rows, month_outcomes)
                if forecast.id in market_evidence
            ]
            rolling.append({
                "date": month,
                "asp_brier": brier_score(month_probabilities, month_outcomes),
                "market_brier": brier_score(
                    [item[0] for item in month_market],
                    [item[1] for item in month_market],
                ) if month_market else None,
                "accuracy": sum((probability >= .5) == bool(outcome) for probability, outcome in zip(month_probabilities, month_outcomes)) / len(month_rows) * 100,
            })

        confidence_ranges = [
            (0.0, 50.0, "Under 50"),
            (50.0, 65.0, "50–64"),
            (65.0, 80.0, "65–79"),
            (80.0, 100.000001, "80–100"),
        ]
        by_confidence = []
        for lower, upper, label in confidence_ranges:
            members = [
                (forecast.home_win_probability, outcome)
                for forecast, outcome in zip(forecasts, outcomes)
                if forecast.confidence is not None and lower <= float(forecast.confidence) < upper
            ]
            if members:
                member_probabilities = [item[0] for item in members]
                member_outcomes = [item[1] for item in members]
                by_confidence.append({
                    "bucket": label,
                    "forecasts": len(members),
                    "accuracy": sum((probability >= .5) == bool(outcome) for probability, outcome in members) / len(members) * 100,
                    "brier": brier_score(member_probabilities, member_outcomes),
                })

        component_rows: dict[str, list[tuple[float, float | None, int, float]]] = defaultdict(list)
        for forecast, outcome, actual_margin in zip(forecasts, outcomes, run_errors_actual):
            components = self.session.scalars(select(ForecastComponent).where(ForecastComponent.forecast_id == forecast.id))
            for component in components:
                probability = component.output.get("home_win_probability")
                if probability is None:
                    continue
                expected_home = component.output.get("expected_home_runs")
                expected_away = component.output.get("expected_away_runs")
                expected_margin = (
                    float(expected_home) - float(expected_away)
                    if expected_home is not None and expected_away is not None
                    else None
                )
                component_rows[component.component_name].append((float(probability), expected_margin, outcome, actual_margin))
        model_components = [{
            "model": "ASP Ensemble",
            "brier": asp_brier,
            "log_loss": asp_log_loss,
            "run_mae": mean_absolute_error(run_errors_actual, run_errors_pred),
            "status": "DEVELOPMENT",
        }]
        for name, items in sorted(component_rows.items()):
            component_probabilities = [item[0] for item in items]
            component_outcomes = [item[2] for item in items]
            margin_pairs = [(item[3], item[1]) for item in items if item[1] is not None]
            model_components.append({
                "model": name,
                "brier": brier_score(component_probabilities, component_outcomes),
                "log_loss": log_loss(component_probabilities, component_outcomes),
                "run_mae": mean_absolute_error(
                    [item[0] for item in margin_pairs],
                    [item[1] for item in margin_pairs],
                ) if margin_pairs else None,
                "status": "COMPONENT",
            })
        return PerformanceData(
            data_mode=self._forecast_data_mode(forecasts),
            evaluation_available=True,
            market_benchmark_available=bool(market_rows),
            model_version=f"{model.name} v{model.version}" if model else "Unknown",
            model_status=self._model_status(),
            component_weights=self._component_weights(),
            evaluated_games=len(rows), brier_score=asp_brier,
            log_loss=asp_log_loss, accuracy=sum((p >= .5) == bool(o) for p, o in zip(probabilities, outcomes)) / len(rows) * 100,
            run_mae=mean_absolute_error(run_errors_actual, run_errors_pred), total_mae=mean_absolute_error(total_actual, total_pred),
            # The comparison must use the identical game subset for ASP and
            # market probabilities; otherwise missing market evidence biases it.
            versus_market_brier=(
                asp_market_subset_brier - market_brier
                if asp_market_subset_brier is not None and market_brier is not None
                else None
            ),
            calibration=[{"predicted": (item.mean_probability or 0) * 100, "observed": (item.observed_rate or 0) * 100, "count": item.count} for item in bins if item.count],
            rolling=rolling, model_components=model_components,
            by_confidence=by_confidence,
            offline_evaluation=offline_evaluation,
            historical_dataset=historical_dataset,
            betting=self._experiment_betting(),
        )

    def _experiment_status_map(self, game_ids: list[Any]) -> dict[str, tuple[str, float | None]]:
        """Per-game experiment membership for the board: FROZEN / SETTLED / (none).

        PROVISIONAL is derived by the caller from the live recommendation state."""
        from app.models import OfficialAspPosition, PositionSettlement
        from app.services.shadow_experiment_service import ShadowExperimentService

        # Scoped to the V1 strict control — a registered V2 challenger has its
        # own ledger and must never repaint the V1 board badges.
        experiment = ShadowExperimentService(self.session, self.settings).active_experiment()
        if experiment is None or not game_ids:
            return {}
        settled_ids = set(self.session.scalars(select(PositionSettlement.position_id)))
        result: dict[str, tuple[str, float | None]] = {}
        for position, game_id in self.session.execute(
            select(OfficialAspPosition, Market.game_id)
            .join(MarketOutcome, MarketOutcome.id == OfficialAspPosition.market_outcome_id)
            .join(Market, Market.id == MarketOutcome.market_id)
            .where(
                OfficialAspPosition.shadow_experiment_id == experiment.id,
                Market.game_id.in_(game_ids),
            )
        ):
            status = "SETTLED" if position.id in settled_ids else "FROZEN"
            result[str(game_id)] = (status, float(position.stake))
        return result

    def _experiment_betting(self) -> Any:
        """The forward experiment's betting performance for the scorecard (or None)."""
        from app.schemas.api import BettingPerformance
        from app.services.shadow_experiment_service import ShadowExperimentService

        service = ShadowExperimentService(self.session, self.settings)
        experiment = service.active_experiment()
        if experiment is None:
            return None
        return BettingPerformance(**service.ledger_view(experiment)["betting"])

    def _offline_evaluation(self) -> OfflineEvaluation:
        evaluation = self.session.scalar(
            select(ModelEvaluation)
            .where(ModelEvaluation.source_mode == "LIVE")
            .order_by(
                ModelEvaluation.evaluated_at.desc(),
                ModelEvaluation.created_at.desc(),
                ModelEvaluation.id.desc(),
            )
            .limit(1)
        )
        if evaluation is not None:
            model = self.session.get(ModelVersion, evaluation.model_version_id)
            manifest = self.session.get(TrainingDatasetManifest, evaluation.training_manifest_id)
            feature_version = self._offline_feature_version(manifest)
            policy_version = self._offline_policy_version(evaluation, manifest)
            raw_status = str(evaluation.status or "").upper()
            integrity_errors: list[str] = []
            if raw_status in {"FAILED", "ERROR", "ABORTED"}:
                status = "FAILED"
                status_reason = evaluation.notes or "The latest governed offline evaluation failed. Partial results are not published."
            elif raw_status == "INSUFFICIENT_DATA":
                status = "INSUFFICIENT_DATA"
                status_reason = evaluation.notes or "The governed evaluation sample did not meet the published policy."
            elif raw_status == "COMPLETED":
                integrity_errors = self._offline_evidence_integrity_errors(
                    evaluation,
                    model,
                    manifest,
                    policy_version,
                )
                if integrity_errors:
                    status = "FAILED"
                    status_reason = (
                        "The latest completed offline evaluation failed evidence-integrity checks: "
                        + "; ".join(integrity_errors)
                        + ". Metrics and promotion claims are blocked."
                    )
                else:
                    status = "AVAILABLE"
                    status_reason = evaluation.notes or (
                        "A governed offline evaluation completed. These research "
                        "results do not imply operational promotion."
                    )
            else:
                status = "NOT_RUN"
                status_reason = (
                    f"The latest governed evaluation status is {raw_status or 'not completed'}, not COMPLETED; "
                    "no terminal offline results are published."
                )

            metrics = evaluation.metrics if isinstance(evaluation.metrics, dict) else {}
            validation_metrics = self._offline_metrics(
                self._first_mapping(metrics, "validation", "rolling_validation", "out_of_fold", "oof")
            )
            holdout_metrics = self._offline_metrics(
                self._first_mapping(metrics, "holdout", "locked_holdout", "test")
            )
            # A FAILED or unfinished record may contain diagnostic partials.
            # Fail closed: only a completed immutable evaluation publishes them.
            if status != "AVAILABLE":
                validation_metrics = None
                holdout_metrics = None

            return OfflineEvaluation(
                status=status,
                status_reason=status_reason,
                model_version=self._offline_model_version(model),
                manifest_version=manifest.version if manifest else None,
                feature_version=feature_version,
                dataset_checksum=manifest.identity_checksum if manifest else None,
                feature_set_checksum=self._offline_feature_set_checksum(model, manifest),
                research_decision=(
                    self._offline_research_decision(evaluation)
                    if status == "AVAILABLE"
                    else None
                ),
                evaluation_policy_version=policy_version,
                rolling_origin_folds=self._offline_fold_count(evaluation, manifest),
                train_window=self._offline_window(model.training_window if model else None),
                validation_window=self._offline_window(evaluation.validation_window),
                holdout_window=self._offline_window(evaluation.holdout_window),
                validation_metrics=validation_metrics,
                holdout_metrics=holdout_metrics,
                baselines=self._offline_baselines(evaluation.baseline_metrics) if status == "AVAILABLE" else [],
                promotion=(
                    self._offline_promotion(model, evaluation, policy_version)
                    if status == "AVAILABLE"
                    else OfflinePromotionDecision(
                        decision="PENDING" if status == "NOT_RUN" else "BLOCKED",
                        reason=status_reason,
                        criteria_version=policy_version,
                    )
                ),
            )

        manifests = list(self.session.scalars(
            select(TrainingDatasetManifest)
            .where(TrainingDatasetManifest.source_mode == "LIVE")
            .order_by(
                TrainingDatasetManifest.built_at.desc(),
                TrainingDatasetManifest.created_at.desc(),
                TrainingDatasetManifest.id.desc(),
            )
            .limit(50)
        ))
        manifest = next(
            (
                item
                for item in manifests
                if not self._research_only_historical_manifest(item)
            ),
            None,
        )
        if manifest is None:
            return OfflineEvaluation(
                status="NOT_RUN",
                status_reason="No governed offline evaluation or training-dataset manifest has been published.",
                model_version=None,
                manifest_version=None,
                feature_version=None,
                dataset_checksum=None,
                feature_set_checksum=None,
                research_decision=None,
                evaluation_policy_version=OFFLINE_EVALUATION_POLICY_VERSION,
                rolling_origin_folds=0,
                promotion=OfflinePromotionDecision(
                    decision="PENDING",
                    reason="No promotion decision exists because offline evaluation has not run.",
                    criteria_version=OFFLINE_EVALUATION_POLICY_VERSION,
                ),
            )

        model = self.session.scalar(
            select(ModelVersion)
            .where(ModelVersion.training_manifest_id == manifest.id)
            .order_by(ModelVersion.created_at.desc(), ModelVersion.id.desc())
            .limit(1)
        )
        policy_version = self._offline_policy_version(None, manifest)
        sufficiency_status, sufficiency_reason = self._offline_training_sufficiency(manifest)
        if sufficiency_status == "INSUFFICIENT_DATA":
            reason = sufficiency_reason or (
                "The governed training manifest records an insufficient temporal or outcome sample."
            )
            status = "INSUFFICIENT_DATA"
            promotion = OfflinePromotionDecision(
                decision="BLOCKED",
                reason=reason,
                criteria_version=policy_version,
            )
        elif sufficiency_status == "NOT_READY":
            reason = sufficiency_reason or (
                "The governed historical dataset did not pass its audit-only "
                "readiness contract."
            )
            status = "NOT_RUN"
            promotion = OfflinePromotionDecision(
                decision="BLOCKED",
                reason=reason,
                criteria_version=policy_version,
            )
        elif manifest.row_count < self.settings.gate3_min_training_rows:
            reason = (
                f"Only {manifest.row_count} eligible LIVE games were available; "
                f"the governed policy requires at least {self.settings.gate3_min_training_rows}."
            )
            status = "INSUFFICIENT_DATA"
            promotion = OfflinePromotionDecision(
                decision="BLOCKED",
                reason=reason,
                criteria_version=policy_version,
            )
        else:
            reason = "A governed training manifest exists, but no immutable offline evaluation has completed."
            status = "NOT_RUN"
            promotion = OfflinePromotionDecision(
                decision="PENDING",
                reason="Promotion remains pending until a governed evaluation is published.",
                criteria_version=policy_version,
            )
        return OfflineEvaluation(
            status=status,
            status_reason=reason,
            model_version=self._offline_model_version(model),
            manifest_version=manifest.version,
            feature_version=self._offline_feature_version(manifest),
            dataset_checksum=manifest.identity_checksum,
            feature_set_checksum=self._offline_feature_set_checksum(model, manifest),
            research_decision=None,
            evaluation_policy_version=policy_version,
            rolling_origin_folds=0,
            promotion=promotion,
        )

    def _offline_feature_version(self, manifest: TrainingDatasetManifest | None) -> str | None:
        if manifest is None:
            return None
        feature = self.session.get(FeatureVersion, manifest.feature_version_id)
        return f"{feature.name}/{feature.version}" if feature else manifest.schema_version

    def _offline_feature_set_checksum(
        self,
        model: ModelVersion | None,
        manifest: TrainingDatasetManifest | None,
    ) -> str | None:
        """Return the schema identity actually consumed by the evaluated model."""

        feature_id = (
            model.feature_version_id
            if model is not None and model.feature_version_id is not None
            else manifest.feature_version_id if manifest is not None else None
        )
        feature = self.session.get(FeatureVersion, feature_id) if feature_id else None
        return feature.schema_hash if feature is not None else None

    @staticmethod
    def _offline_research_decision(
        evaluation: ModelEvaluation,
    ) -> str | None:
        allowed = {
            "RESEARCH_CANDIDATE",
            "RESEARCH_CHALLENGER",
            "REJECTED_GEN1",
        }
        for payload in (evaluation.metrics, evaluation.slice_metrics):
            if isinstance(payload, dict):
                value = payload.get("research_decision")
                if isinstance(value, str) and value in allowed:
                    return value
        if "GEN1" not in str(evaluation.evaluation_type or "").upper():
            return None
        if evaluation.promotion_recommendation is True:
            return "RESEARCH_CANDIDATE"
        if evaluation.promotion_recommendation is False:
            return "REJECTED_GEN1"
        return None

    @staticmethod
    def _offline_model_version(model: ModelVersion | None) -> str | None:
        return f"{model.name} v{model.version}" if model else None

    @staticmethod
    def _offline_training_sufficiency(
        manifest: TrainingDatasetManifest,
    ) -> tuple[str | None, str | None]:
        """Read the canonical, immutable training preflight result when present."""

        quality = manifest.quality_summary if isinstance(manifest.quality_summary, dict) else {}
        readiness = quality.get("readiness")
        if isinstance(readiness, dict):
            readiness_status = str(readiness.get("status") or "").upper()
            if readiness_status == "READY_FOR_GATE_3B_MODEL_DEVELOPMENT":
                return "SUFFICIENT", None
            if readiness_status == "NOT_READY_FOR_GATE_3B_MODEL_DEVELOPMENT":
                checks = readiness.get("checks")
                failed = sorted(
                    str(name)
                    for name, result in checks.items()
                    if not isinstance(result, dict) or result.get("passed") is not True
                ) if isinstance(checks, dict) else []
                suffix = f" Failed checks: {', '.join(failed)}." if failed else ""
                return (
                    "NOT_READY",
                    "The audit-only historical dataset readiness status is "
                    "NOT_READY_FOR_GATE_3B_MODEL_DEVELOPMENT."
                    + suffix
                    + " No model evaluation was run as part of the immutable A2 "
                    "materialization/readiness audit.",
                )
        sufficiency = quality.get("training_sufficiency")
        if not isinstance(sufficiency, dict):
            return None, None
        status = str(sufficiency.get("status") or "").upper()
        reason_value = sufficiency.get("reason")
        reason = reason_value.strip() if isinstance(reason_value, str) and reason_value.strip() else None
        if status == "INSUFFICIENT_DATA":
            return status, reason
        if status == "SUFFICIENT":
            return status, reason
        # An unrecognized structure cannot establish that a training run was
        # sufficient, but it also must not invent an insufficient-data result.
        return None, None

    @staticmethod
    def _research_only_historical_manifest(
        manifest: TrainingDatasetManifest,
    ) -> bool:
        quality = manifest.quality_summary if isinstance(manifest.quality_summary, dict) else {}
        return (
            quality.get("stage") == "GATE_3A_A2"
            and quality.get("population") == "TIER_B_ENHANCED"
        )

    def _offline_evidence_integrity_errors(
        self,
        evaluation: ModelEvaluation,
        model: ModelVersion | None,
        manifest: TrainingDatasetManifest | None,
        policy_version: str,
    ) -> list[str]:
        """Return public-safe reasons a completed evaluation cannot be published."""

        errors: list[str] = []
        if model is None:
            errors.append("the referenced model version does not exist")
        if manifest is None:
            errors.append("the referenced training manifest does not exist")
        if model is None or manifest is None:
            return errors

        if model.training_manifest_id != manifest.id:
            errors.append("model and evaluation training-manifest lineage do not match")
        is_gen1 = self._is_gen1_research_evaluation(
            evaluation,
            model,
            policy_version,
        )
        if is_gen1:
            errors.extend(
                self._gen1_research_lineage_errors(
                    model,
                    manifest,
                    policy_version,
                )
            )
        elif model.feature_version_id is None or model.feature_version_id != manifest.feature_version_id:
            errors.append("model and manifest feature-version lineage do not match")
        if manifest.source_mode != "LIVE":
            errors.append("the referenced training manifest is not LIVE evidence")
        if model.code_revision != evaluation.code_revision:
            errors.append("model and evaluation code revisions do not match")
        if model.validation_window != evaluation.validation_window:
            errors.append("model and evaluation validation windows do not match")
        if model.holdout_window != evaluation.holdout_window:
            errors.append("model and evaluation holdout windows do not match")

        quality = manifest.quality_summary if isinstance(manifest.quality_summary, dict) else {}
        if (
            manifest.name == ENHANCED_A2_MANIFEST_NAME
            or quality.get("population") == "TIER_B_ENHANCED"
        ):
            errors.append("research-only Tier B evidence cannot publish model evaluation metrics")
        leakage_checks = quality.get("leakage_checks")
        if (
            not isinstance(leakage_checks, dict)
            or not leakage_checks
            or any(result is not True for result in leakage_checks.values())
        ):
            errors.append("manifest leakage checks are missing or did not all pass")

        required_references = (
            ("training-manifest artifact", manifest.artifact_uri, manifest.artifact_checksum),
            ("model artifact", model.artifact_uri, model.artifact_checksum),
            ("calibrator artifact", model.calibrator_uri, model.calibrator_checksum),
            (
                "evaluation prediction artifact",
                evaluation.prediction_artifact_uri,
                evaluation.prediction_artifact_checksum,
            ),
        )
        for label, uri, checksum in required_references:
            if not self._offline_complete_artifact_reference(uri, checksum):
                errors.append(f"{label} URI/checksum reference is incomplete or invalid")

        required_identities = (
            ("training-manifest", manifest.identity_checksum),
            ("model", model.identity_checksum),
            ("evaluation", evaluation.identity_checksum),
        )
        for label, checksum in required_identities:
            if not self._offline_sha256(checksum):
                errors.append(f"{label} identity checksum is missing or invalid")
        return errors

    @staticmethod
    def _is_gen1_research_evaluation(
        evaluation: ModelEvaluation,
        model: ModelVersion,
        policy_version: str,
    ) -> bool:
        parameters = model.parameters if isinstance(model.parameters, dict) else {}
        return (
            policy_version == GEN1_RESEARCH_POLICY_VERSION
            or parameters.get("research_policy_version")
            == GEN1_RESEARCH_POLICY_VERSION
            or "GEN1" in str(evaluation.evaluation_type or "").upper()
        )

    def _gen1_research_lineage_errors(
        self,
        model: ModelVersion,
        manifest: TrainingDatasetManifest,
        policy_version: str,
    ) -> list[str]:
        """Validate the strict A2 source and the separately derived Gen-1 schema."""

        errors: list[str] = []
        quality = manifest.quality_summary if isinstance(manifest.quality_summary, dict) else {}
        if policy_version != GEN1_RESEARCH_POLICY_VERSION:
            errors.append("Gen-1 evaluation policy identity is missing or inconsistent")
        if manifest.name != STRICT_A2_MANIFEST_NAME:
            errors.append("Gen-1 evaluation is not bound to the strict A2 manifest")
        if quality.get("stage") != "GATE_3A_A2":
            errors.append("Gen-1 source manifest is not a Gate 3A A2 dataset")
        if quality.get("population") != "TIER_A_STRICT":
            errors.append("Gen-1 source population is not Tier A strict")
        if quality.get("quality_tiers") != ["A"]:
            errors.append("Gen-1 source manifest contains a non-Tier-A population")
        if manifest.seasons != [2025]:
            errors.append("Gen-1 source manifest is not the governed 2025 season")
        cutoff_policy = manifest.cutoff_policy if isinstance(manifest.cutoff_policy, dict) else {}
        if cutoff_policy.get("policy_version") != "mlb_t_minus_6h_v1":
            errors.append("Gen-1 source cutoff policy is missing or inconsistent")

        source_feature = self.session.get(FeatureVersion, manifest.feature_version_id)
        derived_feature = (
            self.session.get(FeatureVersion, model.feature_version_id)
            if model.feature_version_id is not None
            else None
        )
        if source_feature is None:
            errors.append("Gen-1 source feature version does not resolve")
            return errors
        if quality.get("schema_hash") != source_feature.schema_hash:
            errors.append("Gen-1 source feature schema hash disagrees with the manifest")
        if derived_feature is None:
            errors.append("Gen-1 derived feature version does not resolve")
            return errors
        if derived_feature.id == source_feature.id:
            errors.append("Gen-1 model is not bound to a separately derived feature schema")

        expected_source = {
            "research_policy_version": GEN1_RESEARCH_POLICY_VERSION,
            "source_feature_version_id": str(source_feature.id),
            "source_feature_schema_version": manifest.schema_version,
            "source_feature_schema_hash": source_feature.schema_hash,
        }
        definition = (
            derived_feature.definition
            if isinstance(derived_feature.definition, dict)
            else {}
        )
        for key, expected in expected_source.items():
            if definition.get(key) != expected:
                errors.append(f"Gen-1 derived feature definition has invalid {key}")

        derived_schema_version = definition.get("derived_feature_schema_version")
        if not isinstance(derived_schema_version, str) or not derived_schema_version:
            errors.append(
                "Gen-1 derived feature definition has invalid "
                "derived_feature_schema_version"
            )
        if definition.get("derived_feature_schema_hash") != derived_feature.schema_hash:
            errors.append(
                "Gen-1 derived feature definition has invalid "
                "derived_feature_schema_hash"
            )

        expected_parameters = {
            **expected_source,
            "derived_feature_schema_version": derived_schema_version,
            "derived_feature_schema_hash": derived_feature.schema_hash,
        }
        parameters = model.parameters if isinstance(model.parameters, dict) else {}
        for key, expected in expected_parameters.items():
            if parameters.get(key) != expected:
                errors.append(f"Gen-1 model parameters have invalid {key}")
        return errors

    @classmethod
    def _offline_complete_artifact_reference(cls, uri: Any, checksum: Any) -> bool:
        return isinstance(uri, str) and bool(uri.strip()) and cls._offline_sha256(checksum)

    @staticmethod
    def _offline_sha256(value: Any) -> bool:
        return (
            isinstance(value, str)
            and len(value) == 64
            and all(character in "0123456789abcdefABCDEF" for character in value)
        )

    @staticmethod
    def _first_mapping(value: dict[str, Any], *keys: str) -> dict[str, Any] | None:
        for key in keys:
            candidate = value.get(key)
            if isinstance(candidate, dict):
                return candidate
        return None

    @staticmethod
    def _offline_number(value: Any) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        result = float(value)
        return result if isfinite(result) else None

    @classmethod
    def _offline_metrics(cls, value: dict[str, Any] | None) -> OfflineEvaluationMetrics | None:
        if not isinstance(value, dict):
            return None
        return OfflineEvaluationMetrics(
            brier_score=cls._offline_number(value.get("brier_score")),
            log_loss=cls._offline_number(value.get("log_loss")),
            expected_calibration_error=cls._offline_number(
                value.get("expected_calibration_error", value.get("ece"))
            ),
            run_mae=cls._offline_number(value.get("run_mae")),
        )

    @classmethod
    def _offline_baselines(cls, value: Any) -> list[OfflineEvaluationBaseline]:
        rows: list[tuple[str, dict[str, Any]]] = []
        if isinstance(value, dict):
            rows = [(str(name), metrics) for name, metrics in value.items() if isinstance(metrics, dict)]
        elif isinstance(value, list):
            rows = [
                (str(metrics.get("name") or "Baseline"), metrics)
                for metrics in value
                if isinstance(metrics, dict)
            ]
        result: list[OfflineEvaluationBaseline] = []
        for name, metrics in sorted(rows, key=lambda item: item[0].lower()):
            sample = metrics.get("sample_size", metrics.get("observations", metrics.get("games", 0)))
            sample_size = int(sample) if isinstance(sample, (int, float)) and not isinstance(sample, bool) and sample >= 0 else 0
            result.append(OfflineEvaluationBaseline(
                name=str(metrics.get("name") or name),
                sample_size=sample_size,
                brier_score=cls._offline_number(metrics.get("brier_score")),
                log_loss=cls._offline_number(metrics.get("log_loss")),
                expected_calibration_error=cls._offline_number(
                    metrics.get("expected_calibration_error", metrics.get("ece"))
                ),
                run_mae=cls._offline_number(metrics.get("run_mae")),
            ))
        return result

    @staticmethod
    def _offline_window(value: Any) -> OfflineEvaluationWindow | None:
        if not isinstance(value, dict):
            return None
        start = value.get("start_date", value.get("start", value.get("date_start")))
        end = value.get("end_date", value.get("end", value.get("date_end")))
        games = value.get("games", value.get("row_count", value.get("observations", value.get("count"))))
        if start is None or end is None or isinstance(games, bool) or not isinstance(games, (int, float)):
            return None
        return OfflineEvaluationWindow(
            start_date=_offline_date_text(start),
            end_date=_offline_date_text(end),
            games=max(0, int(games)),
        )

    def _offline_fold_count(
        self,
        evaluation: ModelEvaluation,
        manifest: TrainingDatasetManifest | None,
    ) -> int:
        candidates: list[Any] = []
        if isinstance(evaluation.metrics, dict):
            candidates.extend([
                evaluation.metrics.get("rolling_origin_folds"),
                evaluation.metrics.get("fold_count"),
            ])
            folds = evaluation.metrics.get("folds")
            if isinstance(folds, list):
                candidates.append(len(folds))
        if manifest is not None and isinstance(manifest.split_policy, dict):
            candidates.extend([
                manifest.split_policy.get("rolling_origin_folds"),
                manifest.split_policy.get("folds"),
            ])
        for candidate in candidates:
            if isinstance(candidate, (int, float)) and not isinstance(candidate, bool) and candidate >= 0:
                return int(candidate)
        return 0

    @staticmethod
    def _offline_policy_version(
        evaluation: ModelEvaluation | None,
        manifest: TrainingDatasetManifest | None,
    ) -> str:
        if evaluation is not None and isinstance(evaluation.slice_metrics, dict):
            for key in ("evaluation_policy_version", "policy_version", "criteria_version"):
                if evaluation.slice_metrics.get(key):
                    return str(evaluation.slice_metrics[key])
        if manifest is not None and isinstance(manifest.split_policy, dict):
            for key in ("evaluation_policy_version", "policy_version", "version"):
                if manifest.split_policy.get(key):
                    return str(manifest.split_policy[key])
        return OFFLINE_EVALUATION_POLICY_VERSION

    @staticmethod
    def _offline_promotion(
        model: ModelVersion | None,
        evaluation: ModelEvaluation,
        policy_version: str,
    ) -> OfflinePromotionDecision:
        raw = str(model.promotion_decision if model and model.promotion_decision else "PENDING").upper()
        if raw in {"PROMOTED", "APPROVED", "CHAMPION"}:
            decision = "PROMOTED"
        elif raw in {"NOT_PROMOTED", "REJECTED", "CHALLENGER_READY"}:
            decision = "NOT_PROMOTED"
        elif raw in {"BLOCKED", "NO_PROMOTION", "INSUFFICIENT_DATA"}:
            decision = "BLOCKED"
        else:
            decision = "PENDING"
        return OfflinePromotionDecision(
            decision=decision,
            reason=(model.promotion_reason if model else None) or evaluation.notes,
            criteria_version=policy_version,
        )

    def bankroll(self, experiment_key: str = "v1") -> BankrollData:
        from app.services.shadow_experiment_service import (
            V2_DECISION_POLICY,
            V2_EXPERIMENT_NAME,
            ShadowExperimentService,
        )
        from app.services.practical_policy import POLICY_VERSION as V2_POLICY

        service = ShadowExperimentService(self.session, self.settings)
        if experiment_key == "v2":
            experiment = service.active_experiment(V2_DECISION_POLICY)
            if experiment is not None:
                return self._experiment_bankroll(service, experiment)
            # Honest pre-registration state: the challenger exists as policy
            # only; no bankroll, decisions, or positions exist yet.
            return BankrollData(
                data_mode="empty", experiment_status="NOT_STARTED",
                starting_bankroll=float(self.settings.shadow_virtual_bankroll),
                current_bankroll=float(self.settings.shadow_virtual_bankroll),
                peak_bankroll=float(self.settings.shadow_virtual_bankroll),
                total_return=0.0, roi=0.0, max_drawdown=0.0, record="0–0–0",
                positions_count=0, policy=V2_POLICY,
                policy_detail="Practical pregame challenger — not yet registered.",
                equity_curve=[], monthly=[], positions=[],
                experiment_name=V2_EXPERIMENT_NAME,
            )
        experiment = service.active_experiment()
        if experiment is not None:
            return self._experiment_bankroll(service, experiment)
        return self._official_bankroll()

    def _experiment_bankroll(self, service: Any, experiment: Any) -> BankrollData:
        from app.core.time import local_today
        from app.schemas.api import BettingPerformance

        ledger = service.ledger_view(experiment)
        betting = BettingPerformance(**ledger["betting"])
        positions = [PositionSchema(**row) for row in ledger["positions"]]
        scheduled = experiment.start_date > local_today(self.settings.display_timezone)
        return BankrollData(
            data_mode="live",
            experiment_status="SCHEDULED" if scheduled else "ACTIVE",
            starting_bankroll=ledger["starting"], current_bankroll=ledger["current"],
            peak_bankroll=ledger["peak"], lowest_bankroll=ledger.get("lowest_bankroll"),
            total_return=ledger["net"], roi=ledger["roi"],
            bankroll_return_pct=ledger.get("bankroll_return_pct"),
            return_on_settled_stake_pct=ledger.get("return_on_settled_stake_pct"),
            settled_stake=ledger.get("settled_stake"), open_exposure=ledger.get("open_exposure"),
            max_drawdown=-ledger["max_drawdown"] / ledger["starting"] * 100 if ledger["starting"] else 0.0,
            record=ledger["record"], positions_count=len(positions),
            policy=experiment.decision_policy,
            policy_detail=(
                "Quarter-Kelly sizing capped at 1% per position, 2% same-game, 5% daily gross of the "
                "current virtual bankroll. Virtual research only — no real-money wagering."
            ),
            policy_max_stake_percentage=experiment.max_position_percentage * 100,
            policy_kelly_fraction=experiment.kelly_fraction,
            policy_edge_threshold_percentage=self.settings.shadow_qualified_edge * 100,
            equity_curve=ledger["equity"], monthly=ledger["monthly"], daily=ledger["daily"],
            positions=list(reversed(positions)),
            experiment_id=str(experiment.id),
            experiment_name=experiment.name,
            experiment_start=experiment.started_at.isoformat(),
            controlling_engine=experiment.controlling_engine,
            code_commit=experiment.code_commit,
            supported_markets=list(experiment.supported_markets or []),
            open_positions=ledger["open_positions"], settled_positions=ledger["settled_positions"],
            no_bet_days=ledger["no_bet_days"],
            current_drawdown=ledger["current_drawdown"],
            closing_quote_coverage=ledger["closing_quote_coverage"],
            betting=betting,
            **self._experiment_identity(experiment),
            decision_metrics=ExperimentDecisionMetrics(**self._decision_metrics(experiment)),
            staking_policy=experiment.staking_policy_version,
            policy_checksum=(experiment.policy_config or {}).get("checksum"),
            experiment_start_date=experiment.start_date.isoformat(),
        )

    _V1_POLICY = "AISP_OPPORTUNITY_POLICY_V1"

    def _experiment_identity(self, experiment: Any) -> dict[str, Any]:
        """Which model actually produced this experiment's decisions.

        Read from the forecasts the decisions REFERENCED, never from a label --
        and from both places a decision can reference one. A Gen 1 decision
        binds ``forecast_id`` into ``forecasts``; a V18-era decision binds
        ``shadow_forecast_id`` into ``shadow_forecasts`` and leaves the Gen 1
        column null.

        Only the first join existed. Every V18-era decision therefore resolved
        to no model at all, the ledger reported "no decision references a
        forecast yet" while holding seven V18 positions, and the only model
        name on the page came from the Gen 1 comparison table beside it -- so the
        page read as though asp_mlb_ensemble had placed V18's positions.
        """
        from app.models import ModelVersion, ShadowExperimentDecision, ShadowForecast

        is_v1 = experiment.decision_policy == self._V1_POLICY

        # Gen 1: decisions -> forecasts -> model_versions
        versions = list(self.session.execute(
            select(ModelVersion.name, ModelVersion.version, ModelVersion.status, func.count())
            .select_from(ShadowExperimentDecision)
            .join(Forecast, Forecast.id == ShadowExperimentDecision.forecast_id)
            .join(ModelVersion, ModelVersion.id == Forecast.model_version_id)
            .where(ShadowExperimentDecision.experiment_id == experiment.id)
            .group_by(ModelVersion.name, ModelVersion.version, ModelVersion.status)
            .order_by(func.count().desc())
        ).all())
        # Research-shadow engines: decisions -> shadow_forecasts
        engines = list(self.session.execute(
            select(ShadowForecast.engine_id, ShadowForecast.engine_version, func.count())
            .select_from(ShadowExperimentDecision)
            .join(ShadowForecast, ShadowForecast.id == ShadowExperimentDecision.shadow_forecast_id)
            .where(ShadowExperimentDecision.experiment_id == experiment.id)
            .group_by(ShadowForecast.engine_id, ShadowForecast.engine_version)
            .order_by(func.count().desc())
        ).all())

        gen1_decisions = sum(row[3] for row in versions)
        shadow_decisions = sum(row[2] for row in engines)
        distinct = len(versions) + len(engines)

        if shadow_decisions >= gen1_decisions and engines:
            engine_id, engine_version, _ = engines[0]
            name, version, status_value = engine_id, engine_version, "RESEARCH_SHADOW"
            source = "decisions→shadow_forecasts"
        elif versions:
            name, version, status, _ = versions[0]
            status_value = status.value if hasattr(status, "value") else str(status)
            source = "decisions→forecasts→model_versions"
        else:
            name = version = status_value = None
            source = "no decision references a forecast yet"
        if distinct > 1:
            source += f" ({distinct} model identities referenced; most frequent shown)"

        policy_version = experiment.decision_policy
        label = ("Strict control — V1" if is_v1
                 else f"Practical pregame policy challenger — V{policy_version.rsplit('_V', 1)[-1].replace('_', '.')}")
        # Derived from the engines actually observed, not asserted. The old
        # constant claimed "SAME MODEL · DIFFERENT DECISION POLICY" for every
        # non-V1 experiment, which is exactly backwards for the V18 era: that
        # era runs a DIFFERENT model under the SAME decision policy.
        if is_v1:
            relationship = "Reference control policy"
        elif engines and not versions:
            relationship = (
                f"DIFFERENT FORECASTING MODEL ({engines[0][0]}) · SAME DECISION POLICY "
                f"as the predecessor era"
            )
        elif engines and versions:
            relationship = (
                "MIXED MODEL IDENTITIES · this experiment's decisions reference more "
                "than one forecasting model; see each position's own disclosure"
            )
        else:
            relationship = "SAME MODEL · DIFFERENT DECISION POLICY — not a new forecasting model"
        return {
            "experiment_key": "v1" if is_v1 else "v2",
            "experiment_role": "STRICT_CONTROL" if is_v1 else "POLICY_CHALLENGER",
            "policy_label": label,
            "policy_relationship": relationship,
            "model_name": name,
            "model_version": version,
            "model_status": status_value,
            "model_identity_source": source,
        }

    def _decision_metrics(self, experiment: Any) -> dict[str, Any]:
        from app.models import OfficialAspPosition, PositionSettlement, ShadowExperimentDecision

        decisions = list(self.session.execute(select(
            ShadowExperimentDecision.game_id, ShadowExperimentDecision.selection,
            ShadowExperimentDecision.recommendation,
        ).where(ShadowExperimentDecision.experiment_id == experiment.id)).all())
        qualified = [(g, sel) for g, sel, rec in decisions if rec == "QUALIFIED_BET"]
        positions = int(self.session.scalar(select(func.count()).select_from(OfficialAspPosition).where(
            OfficialAspPosition.shadow_experiment_id == experiment.id)) or 0)
        settled = int(self.session.scalar(
            select(func.count()).select_from(PositionSettlement)
            .join(OfficialAspPosition, OfficialAspPosition.id == PositionSettlement.position_id)
            .where(OfficialAspPosition.shadow_experiment_id == experiment.id)) or 0)
        if settled == 0 and positions == 0:
            stage, note = "NO_EVIDENCE", "No position has been created; nothing can be concluded."
        elif settled < 30:
            stage, note = "EARLY_EVIDENCE", (f"{settled} settled position(s) — far too few to distinguish skill from noise; "
                                             "no performance claim is made.")
        elif settled < 200:
            stage, note = "ACCUMULATING", f"{settled} settled positions — directional only; confidence intervals remain wide."
        else:
            stage, note = "MATURE", f"{settled} settled positions."
        return {
            "candidate_evaluations": len(decisions),
            "games_evaluated": len({g for g, _, _ in decisions}),
            "qualified_evaluations": len(qualified),
            "qualified_selections": len(set(qualified)),
            "frozen_positions": positions,
            "settled_positions": settled,
            "evidence_stage": stage,
            "evidence_note": note,
        }

    def experiment_comparison(self) -> dict[str, Any]:
        """V1-vs-V2 side-by-side aggregates. Read-only; separate ledgers are
        never pooled, and result/CLV fields appear only after legitimate
        settlement."""
        from app.models import OfficialAspPosition, PositionSettlement, ShadowExperimentDecision
        from app.core.time import local_today
        from app.services.shadow_experiment_service import ShadowExperimentService

        service = ShadowExperimentService(self.session, self.settings)
        out: dict[str, Any] = {"experiments": [], "superseded": [
            {
                "name": row.name,
                "policy": row.decision_policy,
                "status": status,
                "effective_date": row.start_date.isoformat(),
                "decisions": 0,
                "positions": 0,
                "note": "Preserved immutably; superseded before activation with zero decisions and zero positions.",
            }
            for row, status in service.superseded_experiments()
        ]}
        for experiment in service.active_experiments():
            # Everything this page needs from the decision history is an
            # aggregate: five numbers over what is now ~24,000 rows, each
            # carrying a ~2 KB `evidence` blob. Loading the rows to reduce them
            # in Python meant decoding roughly 45,000 JSON documents per
            # request. `adjusted_edge` is the one value read out of `evidence`,
            # and JSON extraction in SQL treats a JSON null the same way the
            # Python did -- as absent, not as zero.
            adjusted_edge = ShadowExperimentDecision.evidence["adjusted_edge"].as_float()
            decision_count, games_evaluated, qualified_count, raw_edge_sum, raw_edge_n, adjusted_sum, adjusted_n = (
                self.session.execute(
                    select(
                        func.count(),
                        func.count(distinct(ShadowExperimentDecision.game_id)),
                        func.sum(case(
                            (ShadowExperimentDecision.recommendation == "QUALIFIED_BET", 1), else_=0,
                        )),
                        func.sum(ShadowExperimentDecision.edge),
                        func.count(ShadowExperimentDecision.edge),
                        func.sum(adjusted_edge),
                        func.count(adjusted_edge),
                    ).where(ShadowExperimentDecision.experiment_id == experiment.id)
                ).one()
            )
            positions = list(self.session.scalars(select(OfficialAspPosition).where(
                OfficialAspPosition.shadow_experiment_id == experiment.id,
            )))
            settlements = list(self.session.scalars(
                select(PositionSettlement)
                .join(OfficialAspPosition, OfficialAspPosition.id == PositionSettlement.position_id)
                .where(OfficialAspPosition.shadow_experiment_id == experiment.id)
            ))
            clvs = [float(s.closing_line_value) for s in settlements if s.closing_line_value is not None]
            profit = sum(float(s.profit_loss) for s in settlements)
            wins = sum(1 for s in settlements if s.result.value.upper() == "WIN")
            losses = sum(1 for s in settlements if s.result.value.upper() == "LOSS")
            scheduled = experiment.start_date > local_today(self.settings.display_timezone)
            identity = self._experiment_identity(experiment)
            out["experiments"].append({
                "key": identity["experiment_key"],
                "name": experiment.name,
                "policy": experiment.decision_policy,
                # Honest identity + distinct decision metrics (see ExperimentDecisionMetrics).
                "role": identity["experiment_role"],
                "policy_label": identity["policy_label"],
                "policy_relationship": identity["policy_relationship"],
                "model_name": identity["model_name"],
                "model_version": identity["model_version"],
                "model_status": identity["model_status"],
                "decision_metrics": self._decision_metrics(experiment),
                "staking_policy": experiment.staking_policy_version,
                "status": "SCHEDULED" if scheduled else "ACTIVE",
                "effective_date": experiment.start_date.isoformat(),
                "starting_bankroll": float(experiment.starting_bankroll),
                "current_bankroll": round(service.current_bankroll(experiment), 2),
                "games_evaluated": int(games_evaluated or 0),
                "decisions": int(decision_count or 0),
                "qualified_decisions": int(qualified_count or 0),
                "positions": len(positions),
                "average_raw_edge": (
                    round(float(raw_edge_sum) / int(raw_edge_n) * 100, 2) if raw_edge_n else None
                ),
                "average_adjusted_edge": (
                    round(float(adjusted_sum) / int(adjusted_n) * 100, 2) if adjusted_n else None
                ),
                "average_stake": (
                    round(sum(float(p.stake) for p in positions) / len(positions), 2) if positions else None
                ),
                "settled": len(settlements),
                "record": f"{wins}–{losses}" if settlements else None,
                "profit_loss": round(profit, 2) if settlements else None,
                "average_clv": round(sum(clvs) / len(clvs), 3) if clvs else None,
                "clv_sample_size": len(clvs),
                "total_risked": round(sum(float(p.stake) for p in positions), 2),
                "started_at": experiment.started_at.isoformat(),
                "policy_checksum": (experiment.policy_config or {}).get("checksum"),
            })
        return out

    def _official_bankroll(self) -> BankrollData:
        rows = list(self.session.execute(
            select(OfficialAspPosition, MarketOutcome, Market, Game)
            .join(Forecast, Forecast.id == OfficialAspPosition.forecast_id)
            .join(MarketOutcome, MarketOutcome.id == OfficialAspPosition.market_outcome_id)
            .join(Market, Market.id == MarketOutcome.market_id)
            .join(Game, Game.id == Market.game_id)
            .where(
                Forecast.forecast_track == ForecastTrack.CURRENT_DEVELOPMENT,
                Forecast.source_mode == Game.source_mode,
            )
            .order_by(OfficialAspPosition.placed_at)
        ))
        settled = [SettledPosition(float(position.stake), position.decimal_odds, SettlementResult(position.result.value)) for position, _, _, _ in rows if position.result.value in {"win", "loss", "push", "void"}]
        metrics = bankroll_metrics(settled, 1000.0)
        balance = 1000.0
        equity = [{"date": "Start", "bankroll": balance, "benchmark": 1000.0}]
        positions: list[PositionSchema] = []
        monthly: dict[str, dict[str, Any]] = {}
        for position, outcome, market, game in rows:
            result = position.result.value
            profit = settle_profit(float(position.stake), position.decimal_odds, SettlementResult(result)) if result != "pending" else 0.0
            balance += profit
            placed_local = local_date_of(position.placed_at, self.settings.display_timezone)
            equity.append({"date": placed_local.isoformat(), "bankroll": round(balance, 2), "benchmark": 1000.0})
            month = placed_local.strftime("%Y-%m")
            bucket = monthly.setdefault(month, {"month": month, "positions": 0, "wins": 0, "losses": 0, "pushes": 0, "profit_loss": 0.0, "stake": 0.0})
            bucket["positions"] += 1
            bucket["stake"] += float(position.stake)
            bucket[f"{result}es" if result == "push" else f"{result}s"] = bucket.get(f"{result}es" if result == "push" else f"{result}s", 0) + 1
            bucket["profit_loss"] += profit
            positions.append(PositionSchema(
                id=str(position.id), date=placed_local, game_id=str(game.id), selection=outcome.label,
                market=_market_label(market.market_type), price=decimal_to_american(position.decimal_odds), stake=float(position.stake),
                result="OPEN" if result == "pending" else result.upper(), profit_loss=profit,
                closing_price=decimal_to_american(position.closing_decimal_odds) if position.closing_decimal_odds else None,
                clv=(position.decimal_odds / position.closing_decimal_odds - 1) * 100 if position.closing_decimal_odds else None,
            ))
        monthly_rows = [{
            "month": item["month"], "positions": item["positions"],
            "record": f"{item.get('wins', 0)}–{item.get('losses', 0)}–{item.get('pushes', 0)}",
            "profit_loss": round(item["profit_loss"], 2), "roi": item["profit_loss"] / item["stake"] * 100 if item["stake"] else 0,
        } for item in monthly.values()]
        return BankrollData(
            data_mode=self._position_data_mode(rows),
            experiment_status="ACTIVE" if rows else "NOT_STARTED",
            starting_bankroll=metrics.starting_bankroll, current_bankroll=metrics.current_bankroll,
            peak_bankroll=metrics.peak_bankroll, total_return=metrics.profit_loss, roi=metrics.roi * 100,
            max_drawdown=-metrics.maximum_drawdown * 100, record=f"{metrics.wins}–{metrics.losses}–{metrics.pushes}",
            positions_count=len(rows), policy="PROVISIONAL PHASE 1 BANKROLL POLICY",
            policy_detail="Quarter Kelly sizing, capped at 2.5% of the current virtual bankroll. Simulation only—not real-money wagering advice.",
            policy_max_stake_percentage=self.settings.official_max_stake_percentage * 100,
            policy_kelly_fraction=0.25,
            policy_edge_threshold_percentage=self.settings.opportunity_standard_threshold * 100,
            equity_curve=equity, monthly=monthly_rows, positions=list(reversed(positions)),
        )

    def health(self, event_date: date) -> HealthData:
        now = datetime.now(UTC)
        games = self._games_for_date(event_date, MLB)
        game_ids = [game.id for game in games]
        latest_forecast = self.session.scalar(
            select(Forecast)
            .join(Game, Game.id == Forecast.game_id)
            .where(
                Forecast.game_id.in_(game_ids),
                Forecast.forecast_track == ForecastTrack.CURRENT_DEVELOPMENT,
                Forecast.source_mode == Game.source_mode,
            )
            .order_by(Forecast.generated_at.desc())
            .limit(1)
        ) if game_ids else None
        feature_count = self.session.scalar(select(func.count()).select_from(TeamFeature)) or 0
        odds_count = self.session.scalar(select(func.count()).select_from(OddsSnapshot).join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id).join(Market, Market.id == MarketOutcome.market_id).where(Market.game_id.in_(game_ids))) if game_ids else 0
        weather_count = self.session.scalar(select(func.count()).select_from(WeatherSnapshot).where(WeatherSnapshot.game_id.in_(game_ids))) if game_ids else 0
        forecast_count = self.session.scalar(
            select(func.count()).select_from(Forecast).join(
                Game, Game.id == Forecast.game_id
            ).where(
                Forecast.game_id.in_(game_ids),
                Forecast.forecast_track == ForecastTrack.CURRENT_DEVELOPMENT,
                Forecast.source_mode == Game.source_mode,
            )
        ) if game_ids else 0
        statcast_run = self.session.scalar(select(IngestionRun).where(
            IngestionRun.source == "BASEBALL_SAVANT_STATCAST",
            IngestionRun.job_name == "refresh-statcast",
        ).order_by(IngestionRun.completed_at.desc()).limit(1))
        sports_sources = [
            self._source_run_health("mlb", "MLB schedule & game feed", len(games), "refresh-schedule", "Canonical schedule records discovered"),
            self._source_run_health("mlb-data", "MLB team statistics", feature_count, "refresh-mlb-data", "Canonical pregame team feature snapshots"),
            self._source_run_health("statcast", "Baseball Savant / Statcast", statcast_run.records_processed if statcast_run else 0, "refresh-statcast", "Immutable raw + Parquet Statcast acquisition" if statcast_run else "Disabled by configuration; no synthetic Statcast values are presented", disabled=not self.settings.statcast_enabled),
            self._source_run_health("odds", "The Odds API / demo fixture", odds_count or 0, "refresh-odds", "Odds observations are append-only"),
            self._source_run_health("weather", "Open-Meteo weather", weather_count or 0, "refresh-weather", "Latest hourly venue forecasts"),
        ]
        sports_records = feature_count + odds_count + weather_count + forecast_count + len(games)
        sports_data_status = "available" if sports_records else "empty"
        database_source = self._source_health(
            "database",
            "Canonical database",
            sports_records,
            now,
            "Database connection ready; no sports records are stored" if sports_data_status == "empty" else "Database connection ready",
        )
        sources = [database_source, *sports_sources]
        jobs = list(self.session.scalars(select(JobRun).order_by(JobRun.started_at.desc()).limit(10)))
        score = (
            sum(100 if source.status == "ready" else 60 if source.status in {"watch", "stale"} else 0 for source in sports_sources)
            / len(sports_sources)
            if sports_data_status == "available"
            else 0.0
        )
        return HealthData(
            data_mode=self._data_mode(event_date),
            database_status="ready",
            sports_data_status=sports_data_status,
            provider_evidence_status=self._provider_evidence_status(),
            overall_score=score,
            model_version=self._model_label(latest_forecast),
            model_status=self._model_status(),
            component_weights=self._component_weights(),
            latest_forecast=latest_forecast.generated_at.isoformat() if latest_forecast else "No forecast generated",
            odds_quota_remaining=self._odds_quota(), odds_polling_enabled=self.settings.odds_polling_enabled,
            games_discovered=len(games), missing_games=None,
            sources=sources,
            recent_jobs=[JobRunSchema(
                id=str(job.id), job=job.job_name,
                status="success" if job.status == RunStatus.SUCCEEDED else "running" if job.status == RunStatus.RUNNING else "failed",
                started_at=job.started_at.isoformat() if job.started_at else "Not started",
                duration=_duration(job.started_at, job.completed_at), records=int((job.result or {}).get("records", 0)),
            ) for job in jobs],
            historical_warehouse=HistoricalResearchReadService(
                self.session
            ).health_summary(),
        )

    def _empty_game_analysis(self, game: Game, card: ForecastGame) -> GameAnalysisData:
        now = datetime.now(UTC)
        return GameAnalysisData(
            data_mode=self._game_data_mode(game.id),
            game=card,
            data_as_of=card.data_as_of or card.last_refresh_at or _aware(game.created_at),
            model_version=(card.forecast_model_label or "Displayed model") if card.forecast_available
            else "No forecast available",
            forecast_version="PUBLISHED" if card.forecast_available else "UNPUBLISHED",
            weather={"temperature_f": None, "description": "Unavailable", "wind_mph": None, "wind_direction": "Unavailable", "humidity": None, "run_effect": None},
            probability_comparison=[], run_distribution=[], total_run_distribution=[], run_difference_distribution=[], market_prices=[], pitcher_metrics=[], offense_metrics=[], bullpen_metrics=[], statcast_metrics=[], model_components=[], drivers=[], forecast_history=[],
            data_audit=[] if card.forecast_available else [{
                "source": "Forecast", "status": "missing", "updated": now.isoformat(),
                "detail": (card.forecast_selection or {}).get("selection_note")
                or "No forecast from the controlling engine is available; ASP did not substitute one",
            }],
            simulation={"iterations": 0, "home_cover_probability": 0, "over_probability": 0, "most_likely_score": "Unavailable"},
        )

    def _games_for_date(self, event_date: date, league_code: str) -> list[Game]:
        """One league's slate: its LIVE games, else (demo mode only) its DEMO fixtures.

        Another league's game never appears, whatever its source mode, and a
        date with none of this league's games is an empty slate.
        """
        start, end = local_day_bounds(event_date, self.settings.display_timezone)
        games = list(self.session.scalars(select(Game).where(
            Game.starts_at >= start, Game.starts_at < end, in_league(league_code),
        ).order_by(Game.starts_at)))
        live = [game for game in games if game.source_mode == "LIVE"]
        if live:
            return live
        return [game for game in games if game.source_mode == "DEMO"] if self.settings.demo_mode else []

    def _live_games_for_date(self, event_date: date, league_code: str) -> list[Game]:
        start, end = local_day_bounds(event_date, self.settings.display_timezone)
        return list(self.session.scalars(
            select(Game)
            .where(
                Game.starts_at >= start,
                Game.starts_at < end,
                Game.source_mode == "LIVE",
                in_league(league_code),
            )
            .order_by(Game.starts_at)
        ))

    def _valid_current_versions(self, game_id: Any):
        """Filter for versions eligible to be the displayed 'current' forecast.

        A version is eligible when it is either a published forecast or the
        recorded `after` forecast of a governed intraday rescore (a real
        lineup/starter change). This lets a legitimate intraday rescore supersede
        the morning forecast for display while every version keeps its own
        point-in-time cutoff — an unready one-off DRAFT never qualifies."""

        rescore_versions = select(ShadowRescore.after_forecast_id).where(
            ShadowRescore.game_id == game_id
        )
        return or_(
            Forecast.status == ForecastStatus.PUBLISHED,
            Forecast.id.in_(rescore_versions),
        )

    def _forecast_model_label(self, forecast: Forecast | None) -> str | None:
        """Human model identity behind a forecast, or None when unrecorded.

        Reported so a displayed probability always names the model that
        produced it. Never falls back to a different model's identity.
        """

        if forecast is None:
            return None
        version = self.session.get(ModelVersion, forecast.model_version_id)
        if version is None:
            return None
        name = getattr(version, "name", None)
        tag = getattr(version, "version", None)
        if name and tag:
            return f"{name} {tag}"
        return name or tag

    def _latest_forecast(self, game_id: Any) -> Forecast | None:
        ctx = self._prefetched_game(game_id)
        if ctx is not None:
            return ctx.latest_forecast_by_game.get(game_id)
        game = self.session.get(Game, game_id)
        if game is None:
            return None
        # The displayed forecast is the NEWEST valid version on the operational
        # track: the morning publication, or a later intraday rescore triggered by
        # a confirmed lineup / starter change. The rescore carries its own cutoff,
        # so surfacing it never grafts today's lineup onto an older forecast.
        return self.session.scalar(select(Forecast).where(
            Forecast.game_id == game_id,
            Forecast.source_mode == game.source_mode,
            Forecast.forecast_track == ForecastTrack.CURRENT_DEVELOPMENT,
            self._valid_current_versions(game_id),
        ).order_by(Forecast.version.desc()).limit(1))

    def _rescore_reasons(self, game_id: Any) -> dict[Any, str]:
        """Map each rescored forecast version to the availability change that
        produced it (e.g. 'LINEUP_CONFIRMED'), for the history 'reason' column."""

        rows = self.session.execute(
            select(
                ShadowRescore.after_forecast_id,
                AvailabilityEvent.description,
                AvailabilityEvent.event_type,
            )
            .join(AvailabilityEvent, AvailabilityEvent.id == ShadowRescore.availability_event_id)
            .where(ShadowRescore.game_id == game_id)
        ).all()
        return {row[0]: (row[1] or row[2]) for row in rows}

    def _version_lineup_state(self, forecast: Forecast) -> str:
        """Lineup state of the exact snapshot THIS version was built on — a
        point-in-time fact of the version, never today's newest lineup."""

        snapshot = (
            self.session.get(GameFeatureSnapshot, forecast.feature_snapshot_id)
            if forecast.feature_snapshot_id else None
        )
        if snapshot is None:
            return "MISSING"
        observations = [
            self.session.get(LineupObservation, obs_id)
            for obs_id in (snapshot.home_lineup_observation_id, snapshot.away_lineup_observation_id)
            if obs_id
        ]
        if len(observations) == 2 and all(
            obs is not None and obs.lineup_status == "CONFIRMED" and obs.is_complete
            for obs in observations
        ):
            return "CONFIRMED"
        if any(obs is not None for obs in observations):
            return "PARTIAL"
        return "MISSING"

    def _latest_refresh_state(self, game_id: Any, source_mode: str) -> GameRefreshState | None:
        ctx = self._prefetched_game(game_id)
        if ctx is not None:
            state = ctx.refresh_state_by_game.get(game_id)
            return state if state is not None and state.source_mode == source_mode else None
        return self.session.scalar(
            select(GameRefreshState)
            .where(
                GameRefreshState.game_id == game_id,
                GameRefreshState.source_mode == source_mode,
            )
            .order_by(GameRefreshState.available_as_of.desc(), GameRefreshState.evaluated_at.desc())
            .limit(1)
        )

    def _latest_feature_snapshot(
        self,
        game_id: Any,
        source_mode: str,
    ) -> GameFeatureSnapshot | None:
        ctx = self._prefetched_game(game_id)
        if ctx is not None:
            snap = ctx.latest_feature_snapshot_by_game.get(game_id)
            return snap if snap is not None and snap.source_mode == source_mode else None
        return self.session.scalar(
            select(GameFeatureSnapshot)
            .where(
                GameFeatureSnapshot.game_id == game_id,
                GameFeatureSnapshot.source_mode == source_mode,
            )
            .order_by(
                GameFeatureSnapshot.data_as_of.desc(),
                GameFeatureSnapshot.generated_at.desc(),
                GameFeatureSnapshot.created_at.desc(),
                GameFeatureSnapshot.id.desc(),
            )
            .limit(1)
        )

    def _snapshot_readiness(
        self,
        game: Game,
        snapshot: GameFeatureSnapshot,
    ) -> ReadinessResult | None:
        """Reconstruct failed-publication readiness from persisted PIT evidence."""

        cutoff = _aware(snapshot.data_as_of)
        try:
            state = GameStateService(self.session).get_game_state(game.id, cutoff)
        except (GameStateUnavailableError, LookupError):
            return None

        odds = list(state.odds)
        books = len({row.sportsbook_id for row in odds})
        home_team_values = state.home.team_feature.values if state.home.team_feature else {}
        away_team_values = state.away.team_feature.values if state.away.team_feature else {}
        core_features = all(
            values.get(key) is not None
            for values in (home_team_values, away_team_values)
            for key in ("runs_per_game", "ops", "era")
        )
        team_stats_fresh = all(
            feature is not None
            and cutoff - _aware(feature.available_as_of) <= timedelta(hours=36)
            for feature in (state.home.team_feature, state.away.team_feature)
        )
        statcast_rows = (*state.home.statcast, *state.away.statcast)
        statcast_present = bool(state.home.statcast and state.away.statcast)
        bayesian_metadata = snapshot.bayesian_metadata or {}
        statcast_coverage = (
            float((bayesian_metadata.get("home_statcast") or {}).get("player_coverage") or 0),
            float((bayesian_metadata.get("away_statcast") or {}).get("player_coverage") or 0),
        )
        statcast_usable = statcast_present and min(statcast_coverage) >= 0.70
        statcast_fresh = statcast_present and all(
            cutoff - _aware(row.available_as_of) <= timedelta(hours=72)
            for row in statcast_rows
        )
        odds_age = max(
            (cutoff - _aware(row.available_as_of) for row in odds),
            default=None,
        )
        weather_age = cutoff - _aware(state.weather.available_as_of) if state.weather else None
        starters = (state.home.starter, state.away.starter)
        starter_ids = tuple(
            (row.confirmed_pitcher_id or row.probable_pitcher_id) if row else None
            for row in starters
        )
        starters_confirmed = all(
            row is not None
            and row.confirmation_state == "CONFIRMED"
            and row.confirmed_pitcher_id is not None
            for row in starters
        )
        lineups = (state.home.lineup, state.away.lineup)
        lineups_confirmed = all(
            row is not None and row.lineup_status == "CONFIRMED" and row.is_complete
            for row in lineups
        )
        lineups_present = all(row is not None for row in lineups)
        roster_present = bool(state.home.roster and state.away.roster)
        bullpen_coverage = (
            float((bayesian_metadata.get("home_bullpen") or {}).get("coverage") or 0),
            float((bayesian_metadata.get("away_bullpen") or {}).get("coverage") or 0),
        )
        bullpen_present = bool(state.home.bullpen and state.away.bullpen)
        bullpen_usable = (
            bullpen_present
            and min(bullpen_coverage) >= 0.70
            and snapshot.feature_values.get("home_bullpen_availability") is not None
            and snapshot.feature_values.get("away_bullpen_availability") is not None
        )
        schedule_age = cutoff - _aware(state.game_observation.available_as_of)
        statuses = {
            "schedule": ReadinessStatus.READY if schedule_age <= timedelta(hours=1) else ReadinessStatus.STALE,
            "probable_pitchers": ReadinessStatus.READY if all(starter_ids) else ReadinessStatus.MISSING,
            "pitcher_confirmation": (
                ReadinessStatus.READY if starters_confirmed
                else ReadinessStatus.STALE if all(starter_ids)
                else ReadinessStatus.MISSING
            ),
            "lineups": (
                ReadinessStatus.READY if lineups_confirmed
                else ReadinessStatus.STALE if lineups_present
                else ReadinessStatus.MISSING
            ),
            "roster": (
                ReadinessStatus.READY if roster_present
                else ReadinessStatus.STALE if state.home.roster or state.away.roster
                else ReadinessStatus.MISSING
            ),
            "team_stats": (
                ReadinessStatus.READY if core_features and team_stats_fresh
                else ReadinessStatus.STALE if core_features
                else ReadinessStatus.MISSING
            ),
            "statcast": (
                ReadinessStatus.READY if statcast_usable and statcast_fresh
                else ReadinessStatus.STALE if statcast_usable
                else ReadinessStatus.MISSING
            ),
            "bullpen": ReadinessStatus.READY if bullpen_usable else ReadinessStatus.MISSING,
            "odds": (
                ReadinessStatus.READY if odds_age is not None and odds_age <= timedelta(minutes=30)
                else ReadinessStatus.STALE if odds
                else ReadinessStatus.MISSING
            ),
            "sportsbook_count": (
                ReadinessStatus.READY if books >= 3
                else ReadinessStatus.STALE if books
                else ReadinessStatus.MISSING
            ),
            "weather": (
                ReadinessStatus.READY if weather_age is not None and weather_age <= timedelta(hours=3)
                else ReadinessStatus.STALE if state.weather
                else ReadinessStatus.MISSING
            ),
            "feature_completeness": (
                ReadinessStatus.READY if snapshot.feature_completeness >= 0.60
                else ReadinessStatus.STALE if snapshot.feature_completeness > 0
                else ReadinessStatus.MISSING
            ),
        }
        details = {
            "schedule": (
                "Point-in-time schedule evidence age: "
                f"{int(schedule_age.total_seconds() // 60)} minutes"
            ),
            "pitcher_confirmation": (
                "Both starters confirmed"
                if starters_confirmed else "Club confirmation not observed for both starters"
            ),
            "lineups": (
                "Both complete lineups confirmed"
                if lineups_confirmed else "Lineup evidence is projected, partial, or missing"
            ),
            "roster": f"{len(state.home.roster)} home and {len(state.away.roster)} away observations",
            "sportsbook_count": f"{books} books reporting",
            "statcast": (
                "Canonical Statcast lineup/player coverage: "
                f"home {statcast_coverage[0]:.0%}, away {statcast_coverage[1]:.0%}"
                if statcast_present else "Optional Statcast evidence unavailable"
            ),
            "bullpen": (
                "Usable reliever workload coverage: "
                f"home {bullpen_coverage[0]:.0%}, away {bullpen_coverage[1]:.0%}"
                if bullpen_present else "Bullpen workload evidence unavailable"
            ),
            "feature_completeness": (
                f"{snapshot.feature_completeness:.0%} measured coverage; "
                f"{len(snapshot.missing_features)} missing, {len(snapshot.stale_features)} stale, "
                f"{len(snapshot.provisional_features)} provisional"
            ),
        }
        stage = classify_lifecycle(state.scheduled_at, cutoff)
        critical = {"schedule", "probable_pitchers", "team_stats", "feature_completeness"}
        if stage in {LifecycleStage.T_MINUS_10M, LifecycleStage.FINAL}:
            critical.update({"pitcher_confirmation", "lineups", "weather"})
        return calculate_readiness(
            statuses,
            details,
            weights=GATE2_WEIGHTS,
            critical=frozenset(critical),
            minimum_publish_score=self.settings.forecast_min_readiness,
            block_stale_critical=stage in {LifecycleStage.T_MINUS_10M, LifecycleStage.FINAL},
        )

    def _snapshot_readiness_detail(
        self,
        readiness: ReadinessResult | None,
    ) -> list[dict[str, Any]]:
        if readiness is None:
            return []
        return [
            {
                "key": component.key,
                "label": component.label,
                "status": component.status.value,
                "score": component.score,
                "weight": component.weight,
                "detail": component.detail,
                "critical": component.critical,
            }
            for component in readiness.components
        ]

    def _lineup_summary(
        self,
        game: Game,
        team_id: Any,
        cutoff: datetime,
        observation_id: Any | None,
    ) -> dict[str, Any]:
        observation = self.session.get(LineupObservation, observation_id) if observation_id else None
        if observation is not None and (observation.game_id != game.id or observation.team_id != team_id):
            observation = None
        if observation is None:
            ctx = self._prefetched_game(game.id)
            if ctx is not None:
                cutoff_aware = _aware(cutoff)
                candidates = [
                    o for o in ctx.lineup_rows(game.id, team_id)
                    if _aware(o.available_as_of) <= cutoff_aware and o.source_mode == game.source_mode
                ]
            else:
                candidates = list(self.session.scalars(
                    select(LineupObservation).where(
                        LineupObservation.game_id == game.id,
                        LineupObservation.team_id == team_id,
                        LineupObservation.available_as_of <= cutoff,
                        LineupObservation.source_mode == game.source_mode,
                    )
                ))
            status_rank = {"PROJECTED": 1, "EXPECTED": 2, "CONFIRMED": 3}
            observation = max(
                candidates,
                key=lambda item: (
                    status_rank.get(item.lineup_status.upper(), 0),
                    _aware(item.available_as_of),
                    _aware(item.observed_at),
                ),
                default=None,
            )
        if observation is None:
            return {
                "status": "MISSING",
                "complete": False,
                "observed_at": None,
                "available_as_of": None,
                "source": None,
            }
        return {
            "status": observation.lineup_status.upper(),
            "complete": observation.is_complete,
            "observed_at": _aware(observation.observed_at),
            "available_as_of": _aware(observation.available_as_of),
            "source": observation.source,
        }

    def _readiness_detail(self, forecast: Forecast | None) -> list[dict[str, Any]]:
        if forecast is None:
            return []
        details: list[dict[str, Any]] = []
        for key, raw in (forecast.readiness_components or {}).items():
            if key.startswith("_"):
                continue
            value = raw if isinstance(raw, dict) else {}
            status = str(value.get("status", "missing"))
            details.append({
                "key": key,
                "label": key.replace("_", " ").title(),
                "status": status,
                "score": _optional_float(value.get("score")),
                "weight": _optional_float(value.get("weight")),
                "detail": str(value.get("detail", "No readiness detail recorded")),
                "critical": bool(value.get("critical", False)),
                # Forecasts persisted before reasons existed carry none, so
                # fall back to the status itself rather than claiming to know.
                "unavailable_reason": (
                    value.get("unavailable_reason")
                    or _fallback_unavailable_reason(status)
                ),
            })
        return details

    def _sides(self, game_id: Any) -> tuple[GameTeam, Team, GameTeam, Team]:
        ctx = self._prefetched_game(game_id)
        if ctx is not None:
            rows = list(ctx.sides_by_game.get(game_id, []))
        else:
            rows = list(self.session.execute(select(GameTeam, Team).join(Team, Team.id == GameTeam.team_id).where(GameTeam.game_id == game_id)))
        home_side, home = next(row for row in rows if row[0].is_home)
        away_side, away = next(row for row in rows if not row[0].is_home)
        return home_side, home, away_side, away

    def _team_summary(self, team: Team, side: GameTeam) -> TeamSummary:
        wins, losses = side.record_wins, side.record_losses
        return TeamSummary(
            id=str(team.id), abbreviation=team.abbreviation, name=team.name, city=team.city or "",
            record=f"{wins}–{losses}" if wins is not None and losses is not None else "Record unavailable",
            color=TEAM_COLORS.get(team.abbreviation, "#47758f"),
        )

    def _pitcher_summary(
        self,
        side: GameTeam,
        team_id: Any,
        game_id: Any,
        *,
        cutoff: datetime,
        observation_id: Any | None,
        starter_era: Any,
        starter_xera: Any,
        snapshot_bound: bool,
    ) -> PitcherSummary:
        game = self.session.get(Game, game_id)
        observation = self.session.get(StarterObservation, observation_id) if observation_id else None
        if observation is not None and (
            observation.game_id != game_id or observation.team_id != team_id
        ):
            observation = None
        ctx = self._prefetched_game(game_id)
        if observation is None and not snapshot_bound and game is not None:
            if ctx is not None:
                cutoff_aware = _aware(cutoff)
                eligible = [
                    o for o in ctx.starter_rows(game_id, team_id)
                    if _aware(o.available_as_of) <= cutoff_aware and o.source_mode == game.source_mode
                ]
                observation = eligible[-1] if eligible else None
            else:
                observation = self.session.scalar(
                    select(StarterObservation)
                    .where(
                        StarterObservation.game_id == game_id,
                        StarterObservation.team_id == team_id,
                        StarterObservation.available_as_of <= cutoff,
                        StarterObservation.source_mode == game.source_mode,
                    )
                    .order_by(StarterObservation.available_as_of.desc())
                    .limit(1)
                )
        player_id = (
            observation.confirmed_pitcher_id or observation.probable_pitcher_id
            if observation is not None else side.probable_pitcher_id
        )
        if ctx is not None and player_id and player_id in ctx.players_by_id:
            player = ctx.players_by_id[player_id]
        else:
            player = self.session.get(Player, player_id) if player_id else None
        if snapshot_bound:
            values = {"starter_era": starter_era, "starter_xera": starter_xera}
        else:
            feature = self._latest_feature(team_id, cutoff, game.source_mode if game else None)
            values = feature.values if feature else {}
        if ctx is not None and player is not None:
            pitcher = ctx.pitcher_by_player_id.get(player.id)
        else:
            pitcher = self.session.scalar(select(Pitcher).where(Pitcher.player_id == player.id)) if player else None
        throwing_hand = str(pitcher.throws).upper() if pitcher and pitcher.throws else None
        hand = "LHP" if throwing_hand == "L" else "RHP" if throwing_hand == "R" else None
        return PitcherSummary(
            name=player.full_name if player else "Probable pitcher unavailable", hand=hand,
            confirmed=(
                observation is not None
                and observation.confirmation_state == "CONFIRMED"
                and observation.confirmed_pitcher_id is not None
            ) if observation is not None else side.pitcher_confirmed,
            era=float(values["starter_era"]) if values.get("starter_era") is not None else None,
            xera=float(values["starter_xera"]) if values.get("starter_xera") is not None else None,
        )

    def _latest_feature(self, team_id: Any, cutoff: datetime, source_mode: str | None = None) -> TeamFeature | None:
        ctx = self._prefetch()
        if ctx is not None and team_id in ctx.team_features_by_team:
            cutoff_aware = _aware(cutoff)
            for tf in ctx.team_features_by_team[team_id]:  # newest first
                if _aware(tf.available_as_of) <= cutoff_aware and (not source_mode or tf.source_mode == source_mode):
                    return tf
            return None
        query = select(TeamFeature).where(
            TeamFeature.team_id == team_id,
            TeamFeature.available_as_of <= cutoff,
        )
        if source_mode:
            query = query.where(TeamFeature.source_mode == source_mode)
        return self.session.scalar(query.order_by(TeamFeature.available_as_of.desc()).limit(1))

    def _latest_market_snapshots(
        self,
        game_id: Any,
        *,
        cutoff: datetime | None = None,
    ) -> list[tuple[Market, MarketOutcome, OddsSnapshot, Sportsbook]]:
        """Latest persisted quote batch per (market, sportsbook) for one game.

        Semantics (unchanged): for every (market, sportsbook) pair take the
        snapshots observed at that pair's most recent ``observed_at`` (subject
        to ``cutoff`` on ``available_as_of`` and the game's ``source_mode``),
        one row per (outcome, sportsbook), ordered newest first.

        The previous implementation loaded EVERY snapshot ever recorded for the
        game (≈2,000+ rows × 4 ORM entities per game, ~260k instances for a
        15-game slate) and reduced them in Python; profiling put 8 of the ~9
        seconds of a Today request here. The reduction is now expressed in SQL
        so only the winning rows are materialised.
        """
        from sqlalchemy import and_

        cutoff_aware = _aware(cutoff) if cutoff is not None else None
        cache_key = (str(game_id), cutoff_aware)
        cached = self._market_snapshot_cache.get(cache_key)
        if cached is not None:
            return list(cached)
        ctx = self._prefetched_game(game_id)
        if ctx is not None and cutoff_aware is None:
            # Set-based read model: the same (market, sportsbook) latest-batch
            # reduction was executed once for the whole slate.
            rows = list(ctx.market_rows_by_game.get(game_id, []))
            self._market_snapshot_cache[cache_key] = list(rows)
            return rows

        game = self.session.get(Game, game_id)

        def _scoped(statement):
            statement = statement.where(Market.game_id == game_id)
            if cutoff_aware is not None:
                statement = statement.where(OddsSnapshot.available_as_of <= cutoff_aware)
            if game is not None:
                statement = statement.where(OddsSnapshot.source_mode == game.source_mode)
            # Governed pricing reads governed providers only. Without this a
            # research-fallback row could supply one side of a same-book
            # two-sided quote and reach a V2.1 gate. The restriction applies to
            # LIVE rows, which are the only ones that can reach a decision.
            authorised = authorised_sources_for_mode(
                game.source_mode if game is not None else "LIVE"
            )
            if authorised is not None:
                statement = statement.where(OddsSnapshot.source.in_(authorised))
            return statement

        latest_per_market_book = _scoped(
            select(
                Market.id.label("market_id"),
                OddsSnapshot.sportsbook_id.label("sportsbook_id"),
                func.max(OddsSnapshot.observed_at).label("latest_observed_at"),
            )
            .join(MarketOutcome, MarketOutcome.market_id == Market.id)
            .join(OddsSnapshot, OddsSnapshot.market_outcome_id == MarketOutcome.id)
        ).group_by(Market.id, OddsSnapshot.sportsbook_id).subquery("latest_market_book")

        query = _scoped(
            select(Market, MarketOutcome, OddsSnapshot, Sportsbook)
            .join(MarketOutcome, MarketOutcome.market_id == Market.id)
            .join(OddsSnapshot, OddsSnapshot.market_outcome_id == MarketOutcome.id)
            .join(Sportsbook, Sportsbook.id == OddsSnapshot.sportsbook_id)
            .join(
                latest_per_market_book,
                and_(
                    latest_per_market_book.c.market_id == Market.id,
                    latest_per_market_book.c.sportsbook_id == OddsSnapshot.sportsbook_id,
                    latest_per_market_book.c.latest_observed_at == OddsSnapshot.observed_at,
                ),
            )
        ).order_by(
            # Deterministic: rows sharing one observed_at (alternate lines quoted
            # in the same batch) previously came back in unspecified physical
            # order, which silently decided score ties between equal candidates.
            OddsSnapshot.observed_at.desc(), Market.id, MarketOutcome.id, Sportsbook.id,
        )

        seen: set[tuple[Any, Any]] = set()
        latest: list[tuple[Market, MarketOutcome, OddsSnapshot, Sportsbook]] = []
        for row in self.session.execute(query):
            market, outcome, snapshot, book = row
            outcome_key = (outcome.id, book.id)
            if outcome_key in seen:
                continue
            seen.add(outcome_key)
            latest.append(row)
        # The temporary fallback is a fallback, not a ninth bookmaker. Applied
        # PER MARKET so a fallback price can serve a market the primary never
        # covered while never displacing one the primary did -- including when
        # the fallback row is newer, which is exactly when it would otherwise
        # win the latest-per-book reduction.
        latest = _prefer_primary_per_market(latest)
        self._market_snapshot_cache[cache_key] = list(latest)
        return latest

    def _market_context(
        self,
        game_id: Any,
        home_team_id: Any,
        away_team_id: Any,
        *,
        cutoff: datetime | None = None,
    ) -> dict[str, Any]:
        rows = self._latest_market_snapshots(game_id, cutoff=cutoff)
        by_book: dict[Any, dict[Any, tuple[OddsSnapshot, Sportsbook]]] = defaultdict(dict)
        total_lines: list[float] = []
        for market, outcome, snapshot, book in rows:
            if market.market_type == MarketType.MONEYLINE and outcome.participant_team_id:
                by_book[book.id][outcome.participant_team_id] = (snapshot, book)
            elif market.market_type == MarketType.TOTAL and snapshot.point is not None:
                total_lines.append(snapshot.point)
        market_probabilities: list[float] = []
        prices: list[MarketPrice] = []
        best_home = best_away = 0
        best_home_decimal = best_away_decimal = 0.0
        last_updates: list[datetime] = []
        # Price time of each usable pair is its OLDER side: a pair is only as
        # fresh as the stalest price in it. Retrieval time is never substituted.
        pair_times: list[datetime] = []
        providers: set[str] = set()
        book_names: set[str] = set()
        for outcomes in by_book.values():
            if home_team_id not in outcomes or away_team_id not in outcomes:
                continue
            home_snapshot, book = outcomes[home_team_id]
            away_snapshot, _ = outcomes[away_team_id]
            pair_times.append(min(_aware(home_snapshot.observed_at), _aware(away_snapshot.observed_at)))
            providers.update(str(s.source) for s in (home_snapshot, away_snapshot) if s.source)
            book_names.add(book.name)
            result = no_vig_probabilities({"home": home_snapshot.decimal_odds, "away": away_snapshot.decimal_odds}, american=False)
            market_probabilities.append(result.probabilities["home"])
            if home_snapshot.decimal_odds > best_home_decimal:
                best_home_decimal, best_home = home_snapshot.decimal_odds, home_snapshot.american_odds or 0
            if away_snapshot.decimal_odds > best_away_decimal:
                best_away_decimal, best_away = away_snapshot.decimal_odds, away_snapshot.american_odds or 0
            observed = max(_aware(home_snapshot.observed_at), _aware(away_snapshot.observed_at))
            last_updates.append(observed)
            prices.append(MarketPrice(book=book.name, away=away_snapshot.american_odds or 0, home=home_snapshot.american_odds or 0, observed_at=observed))
        for item in prices:
            item.is_best_home = item.home == best_home
            item.is_best_away = item.away == best_away
        # Absence is None, never 0.0. A consumer that reads the number without
        # the flag must get "no observation", not a confident zero probability.
        home_probability = observed_probability(
            average_probability(market_probabilities),
            has_two_sided_quote=bool(market_probabilities),
        )
        return {
            "has_moneyline": home_probability is not None,
            "home_probability": home_probability,
            "best_home": best_home, "best_away": best_away,
            "book_count": len(prices), "market_prices": prices,
            "total": sum(total_lines) / len(total_lines) if total_lines else None,
            "last_update": max(last_updates) if last_updates else None,
            "quote_observed_at": max(pair_times) if pair_times else None,
            "providers": sorted(providers),
            "books": sorted(book_names),
        }

    def _metric(
        self,
        label: str,
        away: Any,
        home: Any,
        lower: bool = False,
        *,
        digits: int | None = None,
        percent: bool = False,
    ) -> ComparisonMetric:
        def display(value: Any) -> Any:
            if value is None:
                return "Unavailable"
            if isinstance(value, (int, float)):
                if percent:
                    return f"{value * 100:.1f}%"
                if digits is not None:
                    return f"{value:.{digits}f}"
            return value

        away_value = display(away)
        home_value = display(home)
        preferred = "neutral"
        if isinstance(away, (int, float)) and isinstance(home, (int, float)) and away != home:
            home_better = home < away if lower else home > away
            preferred = "home" if home_better else "away"
        return ComparisonMetric(metric=label, away=away_value, home=home_value, preferred=preferred)

    def _source_health(self, source_id: str, name: str, records: int, updated: datetime | None, detail: str, missing: bool = False) -> SourceHealth:
        if missing or not updated:
            return SourceHealth(id=source_id, name=name, status="missing", last_success="Never", last_failure=None, records=records, detail=detail, freshness_minutes=0)
        updated = _aware(updated)
        minutes = max(0, (datetime.now(UTC) - updated).total_seconds() / 60)
        return SourceHealth(id=source_id, name=name, status="ready" if minutes <= 180 else "stale", last_success=updated.isoformat(), last_failure=None, records=records, detail=detail, freshness_minutes=minutes)

    def _source_run_health(
        self,
        source_id: str,
        name: str,
        records: int,
        job_name: str,
        detail: str,
        *,
        disabled: bool = False,
    ) -> SourceHealth:
        latest_success = self.session.scalar(select(IngestionRun).where(
            IngestionRun.job_name == job_name,
            IngestionRun.status == RunStatus.SUCCEEDED,
        ).order_by(IngestionRun.completed_at.desc()).limit(1))
        latest_failed_job = self.session.scalar(select(JobRun).where(
            JobRun.job_name == job_name,
            JobRun.status == RunStatus.FAILED,
        ).order_by(JobRun.completed_at.desc()).limit(1))
        if disabled and not latest_success:
            return SourceHealth(
                id=source_id, name=name, status="watch", last_success="Never",
                last_failure=latest_failed_job.completed_at.isoformat() if latest_failed_job and latest_failed_job.completed_at else None,
                records=records, detail=detail, freshness_minutes=0,
            )
        updated = latest_success.completed_at if latest_success else None
        base = self._source_health(source_id, name, records, updated, detail, missing=not latest_success)
        if latest_success and latest_success.warnings:
            base.status = "watch"
            base.detail = f"Completed with {len(latest_success.warnings)} warning(s): {latest_success.warnings[0]}"
        base.last_failure = latest_failed_job.completed_at.isoformat() if latest_failed_job and latest_failed_job.completed_at else None
        if latest_failed_job and latest_failed_job.completed_at and (not latest_success or _aware(latest_failed_job.completed_at) > _aware(latest_success.completed_at)):
            base.status = "missing" if not latest_success else "stale"
            base.detail = f"Latest refresh failed: {latest_failed_job.error_message or 'unknown error'}"
        return base

    def _odds_quota(self) -> int | None:
        run = self.session.scalar(select(IngestionRun).where(IngestionRun.source == "THE_ODDS_API").order_by(IngestionRun.completed_at.desc()).limit(1))
        value = (run.source_metadata or {}).get("requests_remaining") if run else None
        return int(value) if value is not None else None

    def _forward_model(self, forecasts: list[Forecast]) -> ModelVersion | None:
        if forecasts:
            latest = max(
                forecasts,
                key=lambda item: (
                    _aware(item.published_at or item.generated_at),
                    item.version,
                    str(item.id),
                ),
            )
            return self.session.get(ModelVersion, latest.model_version_id)

        # An offline research model is not operational merely because it was
        # registered most recently. Prefer the model actually used by the
        # latest published LIVE forecast; otherwise use the latest legacy
        # operational/untrained registry row.
        latest_model_id = self.session.scalar(
            select(Forecast.model_version_id)
            .where(
                Forecast.source_mode == "LIVE",
                Forecast.status == ForecastStatus.PUBLISHED,
                Forecast.forecast_track == ForecastTrack.CURRENT_DEVELOPMENT,
            )
            .order_by(
                Forecast.published_at.desc().nulls_last(),
                Forecast.generated_at.desc(),
                Forecast.version.desc(),
            )
            .limit(1)
        )
        if latest_model_id is not None:
            return self.session.get(ModelVersion, latest_model_id)
        return self.session.scalar(
            select(ModelVersion)
            .where(
                ModelVersion.training_manifest_id.is_(None),
                ModelVersion.name == "asp_mlb_ensemble",
            )
            .order_by(ModelVersion.created_at.desc(), ModelVersion.id.desc())
            .limit(1)
        )

    def _model_label(self, forecast: Forecast | None = None) -> str:
        model = (
            self.session.get(ModelVersion, forecast.model_version_id)
            if forecast is not None
            else self._forward_model([])
        )
        return f"{model.name} v{model.version}" if model else "No model registered"

    def _model_status(self) -> str:
        # Public promotion status stays DEVELOPMENT until a governed promotion
        # record and approval workflow exist. Internal ModelVersion state alone
        # is not sufficient evidence for a public CHAMPION claim.
        return "DEVELOPMENT"

    def _component_weights(self) -> dict[str, float]:
        weights = {
            "baseline": self.settings.ensemble_baseline_weight,
            "run": self.settings.ensemble_run_weight,
            "boosting": self.settings.ensemble_boosting_weight,
        }
        total = sum(weights.values())
        if total <= 0:
            return {key: 0.0 for key in weights}
        return {key: round(value / total * 100, 6) for key, value in weights.items()}

    def _provider_evidence_status(self) -> str:
        sources = set(self.session.scalars(select(IngestionRun.source).where(IngestionRun.status == RunStatus.SUCCEEDED)))
        if not sources:
            return "none"
        has_demo = "ASP_DEMO_FIXTURE" in sources
        has_live = any(source != "ASP_DEMO_FIXTURE" for source in sources)
        if has_demo and has_live:
            return "mixed"
        return "demo" if has_demo else "live"

    def _forecast_market_probability(self, forecast_id: Any, home_team_id: Any) -> float | None:
        value = self.session.scalar(
            select(MarketComparison.market_no_vig_probability)
            .join(MarketOutcome, MarketOutcome.id == MarketComparison.market_outcome_id)
            .where(
                MarketComparison.forecast_id == forecast_id,
                MarketOutcome.participant_team_id == home_team_id,
            )
            .limit(1)
        )
        return float(value) if value is not None else None

    def _game_data_mode(self, game_id: Any) -> str:
        game = self.session.get(Game, game_id)
        if not game:
            return "empty"
        forecast_mode = self.session.scalar(select(Forecast.source_mode).where(
            Forecast.game_id == game_id,
            Forecast.status == ForecastStatus.PUBLISHED,
            Forecast.forecast_track == ForecastTrack.CURRENT_DEVELOPMENT,
        ).order_by(Forecast.version.desc()).limit(1))
        current_market_modes = {row[2].source_mode for row in self._latest_market_snapshots(game_id)}
        if game.source_mode == "DEMO":
            return "demo"
        if forecast_mode is not None:
            return "demo" if forecast_mode == "DEMO" else "live"
        return "demo" if "DEMO" in current_market_modes else "live"

    def _data_mode(self, event_date: date) -> str:
        games = self._games_for_date(event_date, MLB)
        if not games:
            return "empty"
        return "demo" if any(self._game_data_mode(game.id) == "demo" for game in games) else "live"

    def _market_data_mode(self, event_date: date, has_rows: bool) -> str:
        if not has_rows:
            return "empty"
        modes = {
            snapshot.source_mode
            for game in self._games_for_date(event_date, MLB)
            for _, _, snapshot, _ in self._latest_market_snapshots(game.id)
        }
        return "demo" if "DEMO" in modes else "live"

    @staticmethod
    def _forecast_data_mode(forecasts: list[Forecast]) -> str:
        if not forecasts:
            return "empty"
        if any(forecast.source_mode == "DEMO" for forecast in forecasts):
            return "demo"
        return "live"

    def _position_data_mode(self, rows: list[tuple[OfficialAspPosition, MarketOutcome, Market, Game]]) -> str:
        if not rows:
            return "empty"
        modes = {
            forecast.source_mode
            for position, _, _, _ in rows
            if position.forecast_id and (forecast := self.session.get(Forecast, position.forecast_id)) is not None
        }
        modes.update(game.source_mode for _, _, _, game in rows)
        return "demo" if "DEMO" in modes else "live"


def _market_label(value: MarketType) -> str:
    return {MarketType.MONEYLINE: "Moneyline", MarketType.SPREAD: "Run line", MarketType.TOTAL: "Total"}[value]


def _selection_label(outcome: MarketOutcome, point: float | None) -> str:
    return f"{outcome.label} {point:+g}" if point is not None and outcome.label.lower() not in {"over", "under"} else f"{outcome.label} {point:g}" if point is not None else outcome.label


def _distribution_mode(distribution: dict[str, Any]) -> int:
    numeric = [(int(key), float(value)) for key, value in distribution.items() if key.lstrip("-").isdigit()]
    return max(numeric, key=lambda item: item[1])[0] if numeric else 0


def _distribution_points(distribution: dict[str, Any]) -> list[dict[str, float | int]]:
    numeric = sorted(
        (int(key), float(value))
        for key, value in distribution.items()
        if str(key).lstrip("-").isdigit()
    )
    return [{"value": value, "probability": round(probability * 100, 3)} for value, probability in numeric]


def _audit_status(value: str) -> str:
    return {"ready": "ready", "stale": "stale", "missing": "missing", "not_applicable": "ready"}.get(value, "watch")


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _fair_moneyline(probability: float | None) -> int | None:
    """The no-vig American price implied by a model probability, or None."""
    if probability is None or not isfinite(probability) or not 0 < probability < 1:
        return None
    return probability_to_american(probability)


def _offline_date_text(value: Any) -> str:
    if isinstance(value, datetime):
        return _aware(value).date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value)
    return text[:10] if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-" else text


def _league_code_for(session: Any, game: Any) -> str | None:
    """The persisted league code for one game, or None when not recorded."""

    from app.models import League

    league_id = getattr(game, "league_id", None)
    if league_id is None:
        return None
    league = session.get(League, league_id)
    return getattr(league, "code", None)


def _fallback_unavailable_reason(status: str) -> str | None:
    """Reason for a component persisted before reasons were recorded.

    Says only what the stored status already proves. It never asserts that a
    source is missing when the record cannot show that.
    """

    from app.features.readiness import REASON_EVIDENCE_MISSING, REASON_EVIDENCE_STALE

    if status == "stale":
        return REASON_EVIDENCE_STALE
    if status == "missing":
        return REASON_EVIDENCE_MISSING
    return None


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _duration(started: datetime | None, completed: datetime | None) -> str:
    if not started:
        return "0s"
    seconds = int((_aware(completed or datetime.now(UTC)) - _aware(started)).total_seconds())
    return f"{seconds // 60}m {seconds % 60}s" if seconds >= 60 else f"{seconds}s"


def _primary_can_price(primary_rows: list[tuple[Any, Any, Any, Any]]) -> bool:
    """Could these governed rows form a quote a decision could actually use?

    Two conditions, both from the decision contract rather than invented here:
    some single bookmaker must carry BOTH outcomes, and that pair must still be
    inside the freshness window. A one-sided or long-stale governed row prices
    nothing, so it must not be allowed to suppress a fallback pair that does.
    """
    from app.services.quote_coverage import MAX_QUOTE_STALENESS_MINUTES

    now = datetime.now(UTC)
    by_book: dict[Any, set[Any]] = defaultdict(set)
    freshest: dict[Any, datetime] = {}
    for _market, outcome, snapshot, book in primary_rows:
        by_book[book.id].add(outcome.id)
        observed = _aware(snapshot.observed_at)
        if book.id not in freshest or observed > freshest[book.id]:
            freshest[book.id] = observed
    for book_id, outcomes in by_book.items():
        if len(outcomes) < 2:
            continue
        age = (now - freshest[book_id]).total_seconds() / 60.0
        if age <= MAX_QUOTE_STALENESS_MINUTES:
            return True
    return False


def _prefer_primary_per_market(
    rows: list[tuple[Any, Any, Any, Any]],
) -> list[tuple[Any, Any, Any, Any]]:
    """Drop fallback rows for any market the governed primary can actually price."""
    from app.data_sources.provider_registry import prefer_primary

    by_market: dict[Any, list[tuple[Any, Any, Any, Any]]] = defaultdict(list)
    for row in rows:
        by_market[row[0].id].append(row)
    kept: list[tuple[Any, Any, Any, Any]] = []
    for market_rows in by_market.values():
        kept.extend(prefer_primary(
            market_rows, lambda row: row[2].source, primary_usable=_primary_can_price,
        ))
    # Preserve the deterministic order the query established.
    order = {id(row): index for index, row in enumerate(rows)}
    return sorted(kept, key=lambda row: order[id(row)])

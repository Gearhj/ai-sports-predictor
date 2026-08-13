from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from math import isfinite
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.bankroll.service import SettlementResult, SettledPosition, bankroll_metrics, settle_profit
from app.core.config import Settings
from app.core.time import local_day_bounds
from app.forecasting.evaluation import brier_score, calibration_bins, log_loss, mean_absolute_error
from app.markets.odds import (
    american_to_decimal,
    classify_opportunity,
    decimal_to_american,
    no_vig_probabilities,
    probability_to_american,
)
from app.models import (
    FeatureVersion,
    Forecast,
    ForecastComponent,
    ForecastStatus,
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
    ComparisonMetric,
    DashboardData,
    DashboardSummary,
    ForecastGame,
    GameAnalysisData,
    HealthData,
    JobRunSchema,
    MarketBoardData,
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


TEAM_COLORS = {
    "NYY": "#132448", "BOS": "#BD3039", "LAD": "#005A9C", "SF": "#FD5A1E",
    "TOR": "#134A8E", "TB": "#092C5C", "ATL": "#CE1141", "NYM": "#FF5910",
    "CHC": "#0E3386", "HOU": "#002D62", "BAL": "#DF4601", "SEA": "#0C2C56",
}

OFFLINE_EVALUATION_POLICY_VERSION = "gate3-promotion-v1"


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

    def dashboard(self, event_date: date) -> DashboardData:
        games = self._games_for_date(event_date)
        cards = [self.game_card(game) for game in games]
        forecasts = [self._latest_forecast(game.id) for game in games]
        complete = sum(forecast is not None and forecast.status == ForecastStatus.PUBLISHED for forecast in forecasts)
        readiness = [card.data_readiness for card in cards if card.data_readiness is not None]
        bankroll = self.bankroll()
        last_refresh = max(
            (card.last_refresh_at for card in cards if card.last_refresh_at is not None),
            default=None,
        )
        return DashboardData(
            data_mode=self._data_mode(event_date),
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
        home_probability = forecast.home_win_probability if forecast else None
        away_probability = forecast.away_win_probability if forecast else None
        home_edge = home_probability - market["home_probability"] if home_probability is not None and market["has_moneyline"] else None
        away_edge = away_probability - (1 - market["home_probability"]) if away_probability is not None and market["has_moneyline"] else None
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
        if not forecast:
            status = f"{status} · FORECAST UNAVAILABLE"
        return ForecastGame(
            id=str(game.id),
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
            forecast_available=forecast is not None,
            market_available=bool(market["has_moneyline"]),
            projected_away_runs=forecast.expected_away_runs if forecast else None,
            projected_home_runs=forecast.expected_home_runs if forecast else None,
            projected_total=forecast.expected_total_runs if forecast else None,
            projected_run_difference=forecast.expected_run_difference if forecast else None,
            uncertainty=forecast.uncertainty if forecast else None,
            market_total=market["total"] if market["total"] else None,
            away_win_probability=away_probability * 100 if away_probability is not None else None,
            home_win_probability=home_probability * 100 if home_probability is not None else None,
            fair_away_moneyline=forecast.fair_away_moneyline if forecast else None,
            fair_home_moneyline=forecast.fair_home_moneyline if forecast else None,
            best_away_moneyline=market["best_away"] if market["has_moneyline"] else None,
            best_home_moneyline=market["best_home"] if market["has_moneyline"] else None,
            market_home_probability=market["home_probability"] * 100 if market["has_moneyline"] else None,
            model_edge=edge * 100 if edge is not None else None,
            edge_side=edge_side,
            opportunity=opportunity,
            confidence=forecast.confidence if forecast else None,
            data_readiness=forecast.readiness_score if forecast else None,
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
            readiness_detail=self._readiness_detail(forecast),
            books_reporting=market["book_count"],
            last_update=_aware(last_update),
        )

    def game_analysis(self, game_id: Any) -> GameAnalysisData | None:
        game = self.session.get(Game, game_id)
        if not game:
            return None
        card = self.game_card(game)
        forecast = self._latest_forecast(game.id)
        if not forecast:
            return self._empty_game_analysis(game, card)
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
        history = list(self.session.scalars(select(Forecast).where(
            Forecast.game_id == game.id,
            Forecast.status == ForecastStatus.PUBLISHED,
        ).order_by(Forecast.generated_at)))
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
        if market["has_moneyline"]:
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
        for game in self._games_for_date(event_date):
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
                implied = sum(no_vig_values) / len(no_vig_values) if no_vig_values else sum(quote.implied_probability or 0 for quote in quotes) / len(quotes)
                consensus_price = probability_to_american(implied) if 0 < implied < 1 else decimal_to_american(sum(quote.decimal_odds for quote in quotes) / len(quotes))
                fair_price = decimal_to_american(1 / asp_probability) if 0 < asp_probability < 1 else None
                rows.append(MarketBoardRow(
                    id=str(outcome.id), game_id=str(game.id), matchup=f"{away.abbreviation} @ {home.abbreviation}",
                    start_time=_aware(game.starts_at), market_type=_market_label(market.market_type), selection=_selection_label(outcome, quotes[0].point),
                    line=quotes[0].point, asp_fair=fair_price, consensus=consensus_price,
                    edge=(asp_probability - implied) * 100 if asp_probability else None,
                    books=books, observed_at=max(_aware(quote.observed_at) for quote in quotes),
                ))
        quota = self._odds_quota()
        return MarketBoardData(
            data_mode=self._market_data_mode(event_date, bool(rows)),
            date=event_date,
            books=sorted(all_books),
            quota_remaining=quota,
            rows=rows,
        )

    def performance(self) -> PerformanceData:
        all_rows = list(self.session.execute(
            select(Forecast, Game)
            .join(Game, Game.id == Forecast.game_id)
            .join(GameFeatureSnapshot, GameFeatureSnapshot.id == Forecast.feature_snapshot_id)
            .where(
                Forecast.status == ForecastStatus.PUBLISHED,
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
        ))
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
        model = self.session.scalar(select(ModelVersion).order_by(ModelVersion.created_at.desc()).limit(1))
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
        )

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
                integrity_errors = self._offline_evidence_integrity_errors(evaluation, model, manifest)
                if integrity_errors:
                    status = "FAILED"
                    status_reason = (
                        "The latest completed offline evaluation failed evidence-integrity checks: "
                        + "; ".join(integrity_errors)
                        + ". Metrics and promotion claims are blocked."
                    )
                else:
                    status = "AVAILABLE"
                    status_reason = None
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

        manifest = self.session.scalar(
            select(TrainingDatasetManifest)
            .where(TrainingDatasetManifest.source_mode == "LIVE")
            .order_by(
                TrainingDatasetManifest.built_at.desc(),
                TrainingDatasetManifest.created_at.desc(),
                TrainingDatasetManifest.id.desc(),
            )
            .limit(1)
        )
        if manifest is None:
            return OfflineEvaluation(
                status="NOT_RUN",
                status_reason="No governed offline evaluation or training-dataset manifest has been published.",
                model_version=None,
                manifest_version=None,
                feature_version=None,
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
            evaluation_policy_version=policy_version,
            rolling_origin_folds=0,
            promotion=promotion,
        )

    def _offline_feature_version(self, manifest: TrainingDatasetManifest | None) -> str | None:
        if manifest is None:
            return None
        feature = self.session.get(FeatureVersion, manifest.feature_version_id)
        return f"{feature.name}/{feature.version}" if feature else manifest.schema_version

    @staticmethod
    def _offline_model_version(model: ModelVersion | None) -> str | None:
        return f"{model.name} v{model.version}" if model else None

    @staticmethod
    def _offline_training_sufficiency(
        manifest: TrainingDatasetManifest,
    ) -> tuple[str | None, str | None]:
        """Read the canonical, immutable training preflight result when present."""

        quality = manifest.quality_summary if isinstance(manifest.quality_summary, dict) else {}
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

    @classmethod
    def _offline_evidence_integrity_errors(
        cls,
        evaluation: ModelEvaluation,
        model: ModelVersion | None,
        manifest: TrainingDatasetManifest | None,
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
        if model.feature_version_id is None or model.feature_version_id != manifest.feature_version_id:
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
            if not cls._offline_complete_artifact_reference(uri, checksum):
                errors.append(f"{label} URI/checksum reference is incomplete or invalid")

        required_identities = (
            ("training-manifest", manifest.identity_checksum),
            ("model", model.identity_checksum),
            ("evaluation", evaluation.identity_checksum),
        )
        for label, checksum in required_identities:
            if not cls._offline_sha256(checksum):
                errors.append(f"{label} identity checksum is missing or invalid")
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

    def bankroll(self) -> BankrollData:
        rows = list(self.session.execute(
            select(OfficialAspPosition, MarketOutcome, Market, Game)
            .join(MarketOutcome, MarketOutcome.id == OfficialAspPosition.market_outcome_id)
            .join(Market, Market.id == MarketOutcome.market_id)
            .join(Game, Game.id == Market.game_id)
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
            equity.append({"date": position.placed_at.date().isoformat(), "bankroll": round(balance, 2), "benchmark": 1000.0})
            month = position.placed_at.strftime("%Y-%m")
            bucket = monthly.setdefault(month, {"month": month, "positions": 0, "wins": 0, "losses": 0, "pushes": 0, "profit_loss": 0.0, "stake": 0.0})
            bucket["positions"] += 1
            bucket["stake"] += float(position.stake)
            bucket[f"{result}es" if result == "push" else f"{result}s"] = bucket.get(f"{result}es" if result == "push" else f"{result}s", 0) + 1
            bucket["profit_loss"] += profit
            positions.append(PositionSchema(
                id=str(position.id), date=position.placed_at.date(), game_id=str(game.id), selection=outcome.label,
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
        games = self._games_for_date(event_date)
        game_ids = [game.id for game in games]
        latest_forecast = self.session.scalar(
            select(Forecast)
            .where(Forecast.game_id.in_(game_ids))
            .order_by(Forecast.generated_at.desc())
            .limit(1)
        ) if game_ids else None
        feature_count = self.session.scalar(select(func.count()).select_from(TeamFeature)) or 0
        odds_count = self.session.scalar(select(func.count()).select_from(OddsSnapshot).join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id).join(Market, Market.id == MarketOutcome.market_id).where(Market.game_id.in_(game_ids))) if game_ids else 0
        weather_count = self.session.scalar(select(func.count()).select_from(WeatherSnapshot).where(WeatherSnapshot.game_id.in_(game_ids))) if game_ids else 0
        forecast_count = self.session.scalar(select(func.count()).select_from(Forecast).where(Forecast.game_id.in_(game_ids))) if game_ids else 0
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
            model_version=self._model_label(),
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
        )

    def _empty_game_analysis(self, game: Game, card: ForecastGame) -> GameAnalysisData:
        now = datetime.now(UTC)
        return GameAnalysisData(
            data_mode=self._game_data_mode(game.id),
            game=card,
            data_as_of=card.data_as_of or card.last_refresh_at or _aware(game.created_at),
            model_version="No forecast available",
            forecast_version="UNPUBLISHED",
            weather={"temperature_f": None, "description": "Unavailable", "wind_mph": None, "wind_direction": "Unavailable", "humidity": None, "run_effect": None},
            probability_comparison=[], run_distribution=[], total_run_distribution=[], run_difference_distribution=[], market_prices=[], pitcher_metrics=[], offense_metrics=[], bullpen_metrics=[], statcast_metrics=[], model_components=[], drivers=[], forecast_history=[],
            data_audit=[{"source": "Forecast", "status": "missing", "updated": now.isoformat(), "detail": "Critical pregame inputs are incomplete; ASP did not fabricate a forecast"}],
            simulation={"iterations": 0, "home_cover_probability": 0, "over_probability": 0, "most_likely_score": "Unavailable"},
        )

    def _games_for_date(self, event_date: date) -> list[Game]:
        start, end = local_day_bounds(event_date, self.settings.display_timezone)
        games = list(self.session.scalars(select(Game).where(Game.starts_at >= start, Game.starts_at < end).order_by(Game.starts_at)))
        live = [game for game in games if game.source_mode == "LIVE"]
        if live:
            return live
        return games if self.settings.demo_mode else []

    def _latest_forecast(self, game_id: Any) -> Forecast | None:
        return self.session.scalar(select(Forecast).where(
            Forecast.game_id == game_id,
            Forecast.status == ForecastStatus.PUBLISHED,
        ).order_by(Forecast.version.desc()).limit(1))

    def _latest_refresh_state(self, game_id: Any, source_mode: str) -> GameRefreshState | None:
        return self.session.scalar(
            select(GameRefreshState)
            .where(
                GameRefreshState.game_id == game_id,
                GameRefreshState.source_mode == source_mode,
            )
            .order_by(GameRefreshState.available_as_of.desc(), GameRefreshState.evaluated_at.desc())
            .limit(1)
        )

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
            details.append({
                "key": key,
                "label": key.replace("_", " ").title(),
                "status": str(value.get("status", "missing")),
                "score": _optional_float(value.get("score")),
                "weight": _optional_float(value.get("weight")),
                "detail": str(value.get("detail", "No readiness detail recorded")),
                "critical": bool(value.get("critical", False)),
            })
        return details

    def _sides(self, game_id: Any) -> tuple[GameTeam, Team, GameTeam, Team]:
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
        if observation is None and not snapshot_bound and game is not None:
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
        player = self.session.get(Player, player_id) if player_id else None
        if snapshot_bound:
            values = {"starter_era": starter_era, "starter_xera": starter_xera}
        else:
            feature = self._latest_feature(team_id, cutoff, game.source_mode if game else None)
            values = feature.values if feature else {}
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
        query = (
            select(Market, MarketOutcome, OddsSnapshot, Sportsbook)
            .join(MarketOutcome, MarketOutcome.market_id == Market.id)
            .join(OddsSnapshot, OddsSnapshot.market_outcome_id == MarketOutcome.id)
            .join(Sportsbook, Sportsbook.id == OddsSnapshot.sportsbook_id)
            .where(Market.game_id == game_id)
            .order_by(OddsSnapshot.observed_at.desc())
        )
        if cutoff is not None:
            query = query.where(OddsSnapshot.available_as_of <= _aware(cutoff))
        rows = list(self.session.execute(query))
        game = self.session.get(Game, game_id)
        if game:
            rows = [row for row in rows if row[2].source_mode == game.source_mode]
        latest_market_book: dict[tuple[Any, Any], datetime] = {}
        for market, _, snapshot, book in rows:
            key = (market.id, book.id)
            observed = _aware(snapshot.observed_at)
            if key not in latest_market_book or observed > latest_market_book[key]:
                latest_market_book[key] = observed
        seen: set[tuple[Any, Any]] = set()
        latest = []
        for row in rows:
            market, outcome, snapshot, book = row
            key = (market.id, book.id)
            outcome_key = (outcome.id, book.id)
            if _aware(snapshot.observed_at) == latest_market_book[key] and outcome_key not in seen:
                latest.append(row)
                seen.add(outcome_key)
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
        for outcomes in by_book.values():
            if home_team_id not in outcomes or away_team_id not in outcomes:
                continue
            home_snapshot, book = outcomes[home_team_id]
            away_snapshot, _ = outcomes[away_team_id]
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
        return {
            "has_moneyline": bool(market_probabilities),
            "home_probability": sum(market_probabilities) / len(market_probabilities) if market_probabilities else 0.0,
            "best_home": best_home, "best_away": best_away,
            "book_count": len(prices), "market_prices": prices,
            "total": sum(total_lines) / len(total_lines) if total_lines else 0.0,
            "last_update": max(last_updates) if last_updates else None,
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

    def _model_label(self) -> str:
        model = self.session.scalar(select(ModelVersion).order_by(ModelVersion.created_at.desc()).limit(1))
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
            Forecast.game_id == game_id, Forecast.status == ForecastStatus.PUBLISHED
        ).order_by(Forecast.version.desc()).limit(1))
        current_market_modes = {row[2].source_mode for row in self._latest_market_snapshots(game_id)}
        if game.source_mode == "DEMO":
            return "demo"
        if forecast_mode is not None:
            return "demo" if forecast_mode == "DEMO" else "live"
        return "demo" if "DEMO" in current_market_modes else "live"

    def _data_mode(self, event_date: date) -> str:
        games = self._games_for_date(event_date)
        if not games:
            return "empty"
        return "demo" if any(self._game_data_mode(game.id) == "demo" for game in games) else "live"

    def _market_data_mode(self, event_date: date, has_rows: bool) -> str:
        if not has_rows:
            return "empty"
        modes = {
            snapshot.source_mode
            for game in self._games_for_date(event_date)
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


def _offline_date_text(value: Any) -> str:
    if isinstance(value, datetime):
        return _aware(value).date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value)
    return text[:10] if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-" else text


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

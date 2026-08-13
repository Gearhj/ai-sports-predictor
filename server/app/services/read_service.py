from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
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
    Forecast,
    ForecastComponent,
    ForecastStatus,
    Game,
    GameStatus,
    GameTeam,
    IngestionRun,
    JobRun,
    Market,
    MarketComparison,
    MarketOutcome,
    MarketType,
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
        last_refresh = max((card.last_update for card in cards), default=None)
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
            away_pitcher=self._pitcher_summary(away_side, away.id, game.id),
            home_pitcher=self._pitcher_summary(home_side, home.id, game.id),
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
        home_feature = self._latest_feature(home.id, forecast.available_as_of, game.source_mode)
        away_feature = self._latest_feature(away.id, forecast.available_as_of, game.source_mode)
        home_values, away_values = (home_feature.values if home_feature else {}), (away_feature.values if away_feature else {})
        weather = self.session.scalar(select(WeatherSnapshot).where(
            WeatherSnapshot.game_id == game.id,
            WeatherSnapshot.available_as_of <= forecast.available_as_of,
        ).order_by(WeatherSnapshot.available_as_of.desc()).limit(1))
        market = self._market_context(game.id, home.id, away.id)
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
        baseline = next((component.output for component in components if component.component_name == "elo_strength_baseline"), {})
        feature_updated = max(
            (_aware(item.available_as_of) for item in (home_feature, away_feature) if item),
            default=_aware(forecast.available_as_of),
        )
        audit_updates = {
            "schedule": _aware(game.created_at),
            "probable_pitchers": _aware(home_side.pitcher_status_observed_at or forecast.available_as_of),
            "pitcher_confirmation": _aware(home_side.pitcher_status_observed_at or forecast.available_as_of),
            "team_stats": feature_updated,
            "statcast": feature_updated,
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
        ]
        most_home = _distribution_mode(home_dist)
        most_away = _distribution_mode(away_dist)
        probability_comparison = [
            {"label": "ASP ensemble", "home": forecast.home_win_probability * 100, "away": forecast.away_win_probability * 100},
            {"label": "Baseline rating", "home": float(baseline.get("home_win_probability", 0)) * 100, "away": (1 - float(baseline.get("home_win_probability", 0))) * 100},
        ]
        if market["has_moneyline"]:
            probability_comparison.insert(1, {
                "label": "No-vig market",
                "home": market["home_probability"] * 100,
                "away": (1 - market["home_probability"]) * 100,
            })
        return GameAnalysisData(
            data_mode=self._game_data_mode(game.id),
            game=card,
            data_as_of=_aware(forecast.available_as_of),
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
                self._metric("xERA", away_values.get("starter_xera"), home_values.get("starter_xera"), lower=True, digits=2),
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
                ComparisonMetric(metric="Workload", away="Unavailable", home="Unavailable", preferred="neutral", context="No reliable reliever-availability feed configured"),
            ],
            statcast_metrics=[
                self._metric("Team xwOBA", away_values.get("xwoba"), home_values.get("xwoba"), digits=3),
                self._metric("Barrel rate", away_values.get("barrel_rate"), home_values.get("barrel_rate"), percent=True),
                self._metric("Hard-hit rate", away_values.get("hard_hit_rate"), home_values.get("hard_hit_rate"), percent=True),
                self._metric("Average exit velocity", away_values.get("average_exit_velocity"), home_values.get("average_exit_velocity"), digits=1),
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
                "over_probability": float((simulation.get("total_probabilities") or {}).get(f"{market['total']:g}", {}).get("over", 0)) * 100 if market["total"] else 0,
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
            select(Forecast, Game).join(Game, Game.id == Forecast.game_id).where(
                Forecast.status == ForecastStatus.PUBLISHED,
                Forecast.generated_at < Game.starts_at,
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
            versus_market_brier=asp_brier - market_brier if market_brier is not None else None,
            calibration=[{"predicted": (item.mean_probability or 0) * 100, "observed": (item.observed_rate or 0) * 100, "count": item.count} for item in bins if item.count],
            rolling=rolling, model_components=model_components,
            by_confidence=by_confidence,
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
            game=card, data_as_of=now, model_version="No forecast available", forecast_version="UNPUBLISHED",
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

    def _pitcher_summary(self, side: GameTeam, team_id: Any, game_id: Any) -> PitcherSummary:
        player = self.session.get(Player, side.probable_pitcher_id) if side.probable_pitcher_id else None
        game = self.session.get(Game, game_id)
        feature = self._latest_feature(team_id, datetime.now(UTC), game.source_mode if game else None)
        values = feature.values if feature else {}
        pitcher = self.session.scalar(select(Pitcher).where(Pitcher.player_id == player.id)) if player else None
        throwing_hand = str(pitcher.throws).upper() if pitcher and pitcher.throws else None
        hand = "LHP" if throwing_hand == "L" else "RHP" if throwing_hand == "R" else None
        return PitcherSummary(
            name=player.full_name if player else "Probable pitcher unavailable", hand=hand,
            confirmed=side.pitcher_confirmed,
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

    def _latest_market_snapshots(self, game_id: Any) -> list[tuple[Market, MarketOutcome, OddsSnapshot, Sportsbook]]:
        rows = list(self.session.execute(
            select(Market, MarketOutcome, OddsSnapshot, Sportsbook)
            .join(MarketOutcome, MarketOutcome.market_id == Market.id)
            .join(OddsSnapshot, OddsSnapshot.market_outcome_id == MarketOutcome.id)
            .join(Sportsbook, Sportsbook.id == OddsSnapshot.sportsbook_id)
            .where(Market.game_id == game_id)
            .order_by(OddsSnapshot.observed_at.desc())
        ))
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

    def _market_context(self, game_id: Any, home_team_id: Any, away_team_id: Any) -> dict[str, Any]:
        rows = self._latest_market_snapshots(game_id)
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


def _duration(started: datetime | None, completed: datetime | None) -> str:
    if not started:
        return "0s"
    seconds = int((_aware(completed or datetime.now(UTC)) - _aware(started)).total_seconds())
    return f"{seconds // 60}m {seconds % 60}s" if seconds >= 60 else f"{seconds}s"

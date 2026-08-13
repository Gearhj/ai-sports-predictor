from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.features.readiness import ReadinessStatus, calculate_readiness
from app.forecasting.ensemble import GameFeatures, MLBForecastEnsemble
from app.markets.odds import no_vig_probabilities
from app.models import (
    FeatureVersion,
    Forecast,
    ForecastComponent,
    ForecastLifecycle,
    ForecastStatus,
    Game,
    GameTeam,
    Market,
    MarketComparison,
    MarketOutcome,
    MarketType,
    ModelStatus,
    ModelVersion,
    OddsSnapshot,
    TeamFeature,
    WeatherSnapshot,
)


class ForecastNotReadyError(RuntimeError):
    def __init__(self, score: float, missing: tuple[str, ...]) -> None:
        super().__init__(f"Forecast inputs not ready ({score:.1f}/100); missing: {', '.join(missing)}")
        self.score = score
        self.missing = missing


class ForecastWindowClosedError(RuntimeError):
    pass


class ForecastService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def generate_for_game(
        self,
        game: Game,
        *,
        lifecycle: ForecastLifecycle = ForecastLifecycle.EARLY,
        data_as_of: datetime | None = None,
        force_draft: bool = False,
    ) -> Forecast:
        data_as_of = data_as_of or datetime.now(UTC)
        if _aware(data_as_of) >= _aware(game.starts_at):
            raise ForecastWindowClosedError("Pregame forecast generation is closed after first pitch")
        home_side, away_side = self._game_sides(game.id)
        home_feature = self._latest_team_feature(home_side.team_id, game.starts_at, data_as_of, game.source_mode)
        away_feature = self._latest_team_feature(away_side.team_id, game.starts_at, data_as_of, game.source_mode)
        weather = self.session.scalar(
            select(WeatherSnapshot)
            .where(
                WeatherSnapshot.game_id == game.id,
                WeatherSnapshot.available_as_of <= data_as_of,
                WeatherSnapshot.source_mode == game.source_mode,
            )
            .order_by(WeatherSnapshot.available_as_of.desc())
            .limit(1)
        )
        odds_history = list(self.session.scalars(
            select(OddsSnapshot)
            .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
            .join(Market, Market.id == MarketOutcome.market_id)
            .where(
                Market.game_id == game.id,
                OddsSnapshot.available_as_of <= data_as_of,
                OddsSnapshot.source_mode == game.source_mode,
            )
            .order_by(OddsSnapshot.observed_at.desc())
        ))
        # A line move creates new point-specific outcomes. Select the newest
        # complete market/book observation first, then retain only outcomes
        # observed at that instant; otherwise stale lines leak into forecasts.
        market_cache: dict[Any, Market | None] = {}
        latest_market_book: dict[tuple[Any, Any], datetime] = {}
        for snapshot in odds_history:
            outcome = self.session.get(MarketOutcome, snapshot.market_outcome_id)
            market = market_cache.setdefault(
                outcome.market_id if outcome else None,
                self.session.get(Market, outcome.market_id) if outcome else None,
            )
            if not market:
                continue
            key = (market.id, snapshot.sportsbook_id)
            observed = _aware(snapshot.observed_at)
            if key not in latest_market_book or observed > latest_market_book[key]:
                latest_market_book[key] = observed
        odds = []
        seen_odds: set[tuple[Any, Any]] = set()
        for snapshot in odds_history:
            outcome = self.session.get(MarketOutcome, snapshot.market_outcome_id)
            market = market_cache.get(outcome.market_id if outcome else None)
            if not market:
                continue
            key = (market.id, snapshot.sportsbook_id)
            outcome_key = (snapshot.market_outcome_id, snapshot.sportsbook_id)
            if _aware(snapshot.observed_at) == latest_market_book[key] and outcome_key not in seen_odds:
                odds.append(snapshot)
                seen_odds.add(outcome_key)
        books = len({snapshot.sportsbook_id for snapshot in odds})
        core_features = _core_feature_complete(home_feature) and _core_feature_complete(away_feature)
        extended_features = _feature_complete(home_feature) and _feature_complete(away_feature)
        now = _aware(data_as_of)
        team_stats_fresh = all(
            feature is not None and now - _aware(feature.available_as_of) <= timedelta(hours=36)
            for feature in (home_feature, away_feature)
        )
        statcast_present = _has_statcast(home_feature) and _has_statcast(away_feature)
        statcast_fresh = statcast_present and team_stats_fresh
        odds_age = min(
            (now - _aware(snapshot.available_as_of) for snapshot in odds),
            default=None,
        )
        weather_age = now - _aware(weather.available_as_of) if weather else None
        statuses = {
            "schedule": ReadinessStatus.READY if now - _aware(game.created_at) <= timedelta(hours=24) else ReadinessStatus.STALE,
            "probable_pitchers": ReadinessStatus.READY if home_side.probable_pitcher_id and away_side.probable_pitcher_id else ReadinessStatus.MISSING,
            "pitcher_confirmation": ReadinessStatus.READY if home_side.pitcher_confirmed and away_side.pitcher_confirmed else ReadinessStatus.STALE,
            "team_stats": ReadinessStatus.READY if core_features and team_stats_fresh else (ReadinessStatus.STALE if core_features else ReadinessStatus.MISSING),
            "statcast": ReadinessStatus.READY if statcast_fresh else (ReadinessStatus.STALE if statcast_present else ReadinessStatus.MISSING),
            "odds": ReadinessStatus.READY if odds_age is not None and odds_age <= timedelta(minutes=30) else (ReadinessStatus.STALE if odds else ReadinessStatus.MISSING),
            "sportsbook_count": ReadinessStatus.READY if books >= 3 else (ReadinessStatus.STALE if books else ReadinessStatus.MISSING),
            "weather": ReadinessStatus.READY if weather_age is not None and weather_age <= timedelta(hours=6) else (ReadinessStatus.STALE if weather else ReadinessStatus.MISSING),
            "feature_completeness": ReadinessStatus.READY if extended_features else (ReadinessStatus.STALE if core_features else ReadinessStatus.MISSING),
        }
        details = {
            "pitcher_confirmation": "Both starters confirmed" if home_side.pitcher_confirmed and away_side.pitcher_confirmed else "Probable starters available; club confirmation not yet observed",
            "sportsbook_count": f"{books} books reporting",
            "statcast": "Statcast-derived fields present" if statuses["statcast"] == ReadinessStatus.READY else "Statcast-derived fields unavailable",
            "feature_completeness": "Core live team fields present" if extended_features else "Core team fields present; optional starter/bullpen inputs use documented league-neutral defaults" if core_features else "Critical team fields missing",
        }
        readiness = calculate_readiness(
            statuses,
            details,
            minimum_publish_score=self.settings.forecast_min_readiness,
        )
        if not readiness.ready_to_publish and not force_draft:
            raise ForecastNotReadyError(readiness.score, readiness.critical_missing)
        feature_values = self._features(home_feature, away_feature, weather)
        ensemble = MLBForecastEnsemble(
            baseline_weight=self.settings.ensemble_baseline_weight,
            run_weight=self.settings.ensemble_run_weight,
            boosting_weight=self.settings.ensemble_boosting_weight,
            simulation_draws=self.settings.simulation_draws,
            simulation_seed=self.settings.simulation_seed,
        ).predict(feature_values, data_readiness=readiness.score)
        model_version, feature_version = self._versions()
        next_version = (self.session.scalar(select(func.max(Forecast.version)).where(Forecast.game_id == game.id)) or 0) + 1
        published = readiness.ready_to_publish and not force_draft
        forecast = Forecast(
            game_id=game.id,
            model_version_id=model_version.id,
            feature_version_id=feature_version.id,
            version=next_version,
            generated_at=data_as_of,
            available_as_of=data_as_of,
            status=ForecastStatus.PUBLISHED if published else ForecastStatus.DRAFT,
            lifecycle=lifecycle,
            home_win_probability=ensemble.home_win_probability,
            away_win_probability=ensemble.away_win_probability,
            expected_home_runs=ensemble.expected_home_runs,
            expected_away_runs=ensemble.expected_away_runs,
            expected_total_runs=ensemble.expected_total_runs,
            expected_run_difference=ensemble.expected_run_difference,
            fair_home_moneyline=ensemble.fair_home_moneyline,
            fair_away_moneyline=ensemble.fair_away_moneyline,
            uncertainty=ensemble.uncertainty,
            confidence=ensemble.confidence,
            readiness_score=readiness.score,
            readiness_components={component.key: {
                "status": component.status.value,
                "score": component.score,
                "weight": component.weight,
                "detail": component.detail,
                "critical": component.critical,
            } for component in readiness.components},
            model_drivers=[{
                "label": driver.feature,
                "impact": driver.impact,
                "direction": driver.direction,
                "detail": driver.explanation,
            } for driver in ensemble.drivers],
            simulation_summary=_simulation_json(ensemble.simulation),
            feature_snapshot_reference=f"team_features:{getattr(home_feature, 'id', None)}:{getattr(away_feature, 'id', None)}",
            market_snapshot_reference=",".join(str(snapshot.id) for snapshot in odds)[:500] or None,
            source_mode="DEMO" if any(
                value == "DEMO"
                for value in [
                    getattr(home_feature, "source_mode", "LIVE"),
                    getattr(away_feature, "source_mode", "LIVE"),
                    getattr(weather, "source_mode", "LIVE"),
                    *(snapshot.source_mode for snapshot in odds),
                ]
            ) else "LIVE",
            published_at=data_as_of if published else None,
        )
        self.session.add(forecast)
        self.session.flush()
        for component in ensemble.components:
            self.session.add(ForecastComponent(
                forecast_id=forecast.id,
                component_name=component.name,
                weight=component.weight,
                output={
                    "model_type": component.model_type,
                    "version": component.version,
                    "trained_artifact": component.trained_artifact,
                    "home_win_probability": component.home_win_probability,
                    "expected_home_runs": component.expected_home_runs,
                    "expected_away_runs": component.expected_away_runs,
                    "details": component.details,
                },
            ))
        self._persist_market_comparisons(forecast, odds)
        self.session.flush()
        return forecast

    def _game_sides(self, game_id: Any) -> tuple[GameTeam, GameTeam]:
        sides = list(self.session.scalars(select(GameTeam).where(GameTeam.game_id == game_id)))
        home = next((side for side in sides if side.is_home), None)
        away = next((side for side in sides if not side.is_home), None)
        if not home or not away:
            raise ValueError("Canonical game must have one home and one away team")
        return home, away

    def _latest_team_feature(self, team_id: Any, first_pitch: datetime, data_as_of: datetime, source_mode: str) -> TeamFeature | None:
        cutoff = min(_aware(first_pitch), _aware(data_as_of))
        return self.session.scalar(
            select(TeamFeature)
            .where(
                TeamFeature.team_id == team_id,
                TeamFeature.available_as_of <= cutoff,
                TeamFeature.source_mode == ("LIVE" if source_mode == "LIVE" else "DEMO"),
            )
            .order_by(TeamFeature.available_as_of.desc())
            .limit(1)
        )

    def _features(self, home: TeamFeature | None, away: TeamFeature | None, weather: WeatherSnapshot | None) -> GameFeatures:
        home_values = home.values if home else {}
        away_values = away.values if away else {}
        measured_fields = (
            "runs_per_game", "ops", "obp", "slg", "strikeout_rate", "walk_rate",
            "era", "whip", "recent_runs", "starter_era", "starter_xera", "bullpen_era",
            "xwoba", "barrel_rate", "hard_hit_rate", "average_exit_velocity",
        )
        measured = sum(
            values.get(key) is not None
            for values in (home_values, away_values)
            for key in measured_fields
        )
        feature_completeness = measured / (2 * len(measured_fields))
        sample_sizes = [
            float(values["games_played"])
            for values in (home_values, away_values)
            if values.get("games_played") is not None
        ]
        sample_reliability = min(1.0, min(sample_sizes) / 30.0) if len(sample_sizes) == 2 else 0.0
        return GameFeatures(
            home_rating=float(home_values.get("rating", 1500)),
            away_rating=float(away_values.get("rating", 1500)),
            home_runs_per_game=float(home_values.get("runs_per_game") or 4.4),
            away_runs_per_game=float(away_values.get("runs_per_game") or 4.4),
            home_runs_allowed_per_game=float(home_values.get("runs_allowed_per_game") or home_values.get("era") or 4.4),
            away_runs_allowed_per_game=float(away_values.get("runs_allowed_per_game") or away_values.get("era") or 4.4),
            home_recent_runs=_float_or_none(home_values.get("recent_runs")),
            away_recent_runs=_float_or_none(away_values.get("recent_runs")),
            home_starter_era=_float_or_none(home_values.get("starter_era")),
            away_starter_era=_float_or_none(away_values.get("starter_era")),
            home_bullpen_era=_float_or_none(home_values.get("bullpen_era") or home_values.get("era")),
            away_bullpen_era=_float_or_none(away_values.get("bullpen_era") or away_values.get("era")),
            home_ops=_float_or_none(home_values.get("ops")),
            away_ops=_float_or_none(away_values.get("ops")),
            temperature_f=weather.temperature_f if weather else None,
            # Wind direction cannot be converted to "out" without a verified
            # park-orientation reference, so raw speed is display-only for now.
            wind_out_mph=None,
            park_run_factor=float(home_values.get("park_run_factor", 1.0)),
            feature_completeness=feature_completeness,
            sample_reliability=sample_reliability,
        )

    def _versions(self) -> tuple[ModelVersion, FeatureVersion]:
        feature = self.session.scalar(select(FeatureVersion).where(FeatureVersion.name == "mlb_pregame", FeatureVersion.version == "1.0.0"))
        if feature is None:
            raise RuntimeError("MLB feature version is not initialized")
        model = self.session.scalar(select(ModelVersion).where(ModelVersion.name == "asp_mlb_ensemble", ModelVersion.version == "1.0.0"))
        if not model:
            model = ModelVersion(
                name="asp_mlb_ensemble",
                version="1.0.0",
                feature_version_id=feature.id,
                algorithm="weighted ensemble: rating + Poisson scoring + development challenger",
                status=ModelStatus.DEVELOPMENT,
                training_window={"status": "not trained; deterministic Phase 1 baseline"},
                hyperparameters={"weights": self.settings.model_dump(include={"ensemble_baseline_weight", "ensemble_run_weight", "ensemble_boosting_weight"})},
                parameters={"simulation_draws": self.settings.simulation_draws},
                metrics={"status": "not yet evaluated on a production holdout"},
            )
            self.session.add(model)
            self.session.flush()
        return model, feature

    def _persist_market_comparisons(self, forecast: Forecast, odds: list[OddsSnapshot]) -> None:
        outcomes: dict[Any, MarketOutcome] = {}
        moneyline_snapshots: list[OddsSnapshot] = []
        for snapshot in odds:
            outcome = outcomes.setdefault(snapshot.market_outcome_id, self.session.get(MarketOutcome, snapshot.market_outcome_id))
            market = self.session.get(Market, outcome.market_id) if outcome else None
            if market and market.market_type == MarketType.MONEYLINE:
                moneyline_snapshots.append(snapshot)
        by_book: dict[Any, list[OddsSnapshot]] = {}
        for snapshot in moneyline_snapshots:
            by_book.setdefault(snapshot.sportsbook_id, []).append(snapshot)
        no_vig_by_outcome: dict[Any, list[float]] = {}
        for book_snapshots in by_book.values():
            unique = {snapshot.market_outcome_id: snapshot for snapshot in book_snapshots}
            if len(unique) != 2:
                continue
            result = no_vig_probabilities(
                {str(outcome_id): snapshot.decimal_odds for outcome_id, snapshot in unique.items()},
                american=False,
            )
            for outcome_id, probability in result.probabilities.items():
                no_vig_by_outcome.setdefault(outcome_id, []).append(probability)
        grouped: dict[Any, list[OddsSnapshot]] = {}
        for snapshot in moneyline_snapshots:
            grouped.setdefault(snapshot.market_outcome_id, []).append(snapshot)
        for outcome_id, snapshots in grouped.items():
            outcome = outcomes[outcome_id]
            probabilities = no_vig_by_outcome.get(str(outcome_id), [])
            if not probabilities:
                continue
            market = self.session.get(Market, outcome.market_id)
            game_team = self.session.scalar(select(GameTeam).where(GameTeam.game_id == market.game_id, GameTeam.team_id == outcome.participant_team_id))
            asp_probability = forecast.home_win_probability if game_team and game_team.is_home else forecast.away_win_probability
            market_probability = sum(probabilities) / len(probabilities)
            self.session.add(MarketComparison(
                forecast_id=forecast.id,
                market_outcome_id=outcome.id,
                observed_at=max(snapshot.observed_at for snapshot in snapshots),
                asp_probability=asp_probability,
                market_no_vig_probability=market_probability,
                edge=asp_probability - market_probability,
                best_decimal_odds=max(snapshot.decimal_odds for snapshot in snapshots),
                consensus_decimal_odds=sum(snapshot.decimal_odds for snapshot in snapshots) / len(snapshots),
            ))


def _has_statcast(feature: TeamFeature | None) -> bool:
    if not feature:
        return False
    return any(
        feature.values.get(key) is not None
        for key in ("xwoba", "barrel_rate", "hard_hit_rate", "average_exit_velocity")
    )


def _feature_complete(feature: TeamFeature | None) -> bool:
    if not feature:
        return False
    return all(feature.values.get(key) is not None for key in ("runs_per_game", "ops", "era", "starter_era", "bullpen_era"))


def _core_feature_complete(feature: TeamFeature | None) -> bool:
    if not feature:
        return False
    return all(feature.values.get(key) is not None for key in ("runs_per_game", "ops", "era"))


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _simulation_json(result: Any) -> dict[str, Any]:
    from dataclasses import asdict
    return asdict(result)

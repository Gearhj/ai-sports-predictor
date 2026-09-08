from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.features.lifecycle import LifecycleStage, classify_lifecycle
from app.features.readiness import GATE2_WEIGHTS, ReadinessStatus, calculate_readiness
from app.forecasting.ensemble import MLBForecastEnsemble
from app.markets.odds import no_vig_probabilities
from app.models import (
    FeatureVersion,
    Forecast,
    ForecastComponent,
    ForecastLifecycle,
    ForecastMovementEvidence,
    ForecastStatus,
    ForecastTrack,
    Game,
    GameTeam,
    Market,
    MarketComparison,
    MarketOutcome,
    MarketType,
    ModelStatus,
    ModelVersion,
    OddsSnapshot,
    GameFeatureSnapshot,
)
from app.services.feature_snapshot_service import FeatureSnapshotService, game_features_from_snapshot
from app.services.game_state_service import GameStateService


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
        return self._generate_point_in_time(
            game,
            requested_lifecycle=lifecycle,
            data_as_of=data_as_of,
            force_draft=force_draft,
        )

    def _generate_point_in_time(
        self,
        game: Game,
        *,
        requested_lifecycle: ForecastLifecycle,
        data_as_of: datetime | None,
        force_draft: bool,
    ) -> Forecast:
        cutoff = _aware(data_as_of or datetime.now(UTC))
        computed_at = datetime.now(UTC)
        if cutoff > computed_at + timedelta(seconds=30):
            raise ValueError("data_as_of cannot be later than the actual computation time")
        state = GameStateService(self.session).get_game_state(game.id, cutoff)
        if cutoff >= state.scheduled_at:
            raise ForecastWindowClosedError("Pregame forecast generation is closed after first pitch")
        if state.source_mode == "LIVE" and computed_at >= state.scheduled_at and not force_draft:
            raise ForecastWindowClosedError(
                "Historical live replays must be generated explicitly as DRAFT after first pitch"
            )
        stage = (
            LifecycleStage.FINAL
            if requested_lifecycle == ForecastLifecycle.FINAL
            else classify_lifecycle(state.scheduled_at, cutoff)
        )
        model_version, feature_version = self._versions()
        snapshot = FeatureSnapshotService(self.session).build_snapshot(
            state,
            feature_version,
            generated_at=computed_at,
        )
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
            feature is not None and cutoff - _aware(feature.available_as_of) <= timedelta(hours=36)
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
            "bullpen": (
                ReadinessStatus.READY if bullpen_usable
                else ReadinessStatus.MISSING
            ),
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
            "schedule": f"Point-in-time schedule evidence age: {int(schedule_age.total_seconds() // 60)} minutes",
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
                f"Canonical Statcast lineup/player coverage: home {statcast_coverage[0]:.0%}, "
                f"away {statcast_coverage[1]:.0%}"
                if statcast_present else "Optional Statcast evidence unavailable"
            ),
            "bullpen": (
                f"Usable reliever workload coverage: home {bullpen_coverage[0]:.0%}, "
                f"away {bullpen_coverage[1]:.0%}"
                if bullpen_present else "Bullpen workload evidence unavailable"
            ),
            "feature_completeness": (
                f"{snapshot.feature_completeness:.0%} measured coverage; "
                f"{len(snapshot.missing_features)} missing, {len(snapshot.stale_features)} stale, "
                f"{len(snapshot.provisional_features)} provisional"
            ),
        }
        critical = {"schedule", "probable_pitchers", "team_stats", "feature_completeness"}
        if stage in {LifecycleStage.T_MINUS_10M, LifecycleStage.FINAL}:
            critical.update({"pitcher_confirmation", "lineups", "weather"})
        readiness = calculate_readiness(
            statuses,
            details,
            weights=GATE2_WEIGHTS,
            critical=frozenset(critical),
            minimum_publish_score=self.settings.forecast_min_readiness,
            block_stale_critical=stage in {LifecycleStage.T_MINUS_10M, LifecycleStage.FINAL},
        )
        if not readiness.ready_to_publish and not force_draft:
            raise ForecastNotReadyError(readiness.score, readiness.critical_missing)
        ensemble = MLBForecastEnsemble(
            baseline_weight=self.settings.ensemble_baseline_weight,
            run_weight=self.settings.ensemble_run_weight,
            boosting_weight=self.settings.ensemble_boosting_weight,
            simulation_draws=self.settings.simulation_draws,
            simulation_seed=self.settings.simulation_seed,
        ).predict(game_features_from_snapshot(snapshot), data_readiness=readiness.score)
        next_version = (
            self.session.scalar(
                select(func.max(Forecast.version)).where(
                    Forecast.game_id == game.id,
                    Forecast.forecast_track == ForecastTrack.CURRENT_DEVELOPMENT,
                )
            )
            or 0
        ) + 1
        published = (
            readiness.ready_to_publish
            and not force_draft
            and (state.source_mode == "DEMO" or computed_at < state.scheduled_at)
        )
        forecast = Forecast(
            game_id=game.id,
            model_version_id=model_version.id,
            feature_version_id=feature_version.id,
            feature_snapshot_id=snapshot.id,
            forecast_track=ForecastTrack.CURRENT_DEVELOPMENT,
            version=next_version,
            generated_at=computed_at,
            available_as_of=computed_at,
            status=ForecastStatus.PUBLISHED if published else ForecastStatus.DRAFT,
            lifecycle=_forecast_lifecycle(stage),
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
            readiness_components={
                **{
                    component.key: {
                        "status": component.status.value,
                        "score": component.score,
                        "weight": component.weight,
                        "detail": component.detail,
                        "critical": component.critical,
                    }
                    for component in readiness.components
                },
                "_gate2": {
                    "lifecycle_stage": stage.value,
                    "data_as_of": cutoff.isoformat(),
                    "computed_at": computed_at.isoformat(),
                    "historical_replay": state.source_mode == "LIVE" and computed_at >= state.scheduled_at,
                    "missing_features": snapshot.missing_features,
                    "stale_features": snapshot.stale_features,
                    "provisional_features": snapshot.provisional_features,
                    "critical_blockers": list(readiness.critical_missing),
                },
            },
            model_drivers=[{
                "label": driver.feature,
                "impact": driver.impact,
                "direction": driver.direction,
                "detail": driver.explanation,
            } for driver in ensemble.drivers],
            simulation_summary=_simulation_json(ensemble.simulation),
            feature_snapshot_reference=f"sha256:{snapshot.checksum}",
            market_snapshot_reference=_market_snapshot_reference(odds),
            source_mode=state.source_mode,
            published_at=computed_at if published else None,
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
        self._persist_movement(forecast, snapshot, computed_at)
        self.session.flush()
        return forecast

    def _versions(self) -> tuple[ModelVersion, FeatureVersion]:
        feature = self.session.scalar(select(FeatureVersion).where(
            FeatureVersion.name == "mlb_pregame",
            FeatureVersion.version == "2.0.0",
        ))
        if feature is None:
            raise RuntimeError("Gate 2 MLB feature version is not initialized")
        settings_payload = {
            "ensemble_baseline_weight": self.settings.ensemble_baseline_weight,
            "ensemble_run_weight": self.settings.ensemble_run_weight,
            "ensemble_boosting_weight": self.settings.ensemble_boosting_weight,
            "simulation_draws": self.settings.simulation_draws,
            "simulation_seed": self.settings.simulation_seed,
            "feature_schema_hash": feature.schema_hash,
        }
        settings_hash = sha256(
            json.dumps(settings_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        version = f"2.0.0-gate2-{settings_hash[:12]}"
        model = self.session.scalar(select(ModelVersion).where(
            ModelVersion.name == "asp_mlb_ensemble",
            ModelVersion.version == version,
        ))
        if not model:
            model = ModelVersion(
                name="asp_mlb_ensemble",
                version=version,
                feature_version_id=feature.id,
                algorithm="point-in-time weighted ensemble: rating + Poisson scoring + development challenger",
                status=ModelStatus.DEVELOPMENT,
                training_window={"status": "not trained; deterministic Gate 2 development model"},
                hyperparameters={
                    "weights": {
                        "baseline": self.settings.ensemble_baseline_weight,
                        "run": self.settings.ensemble_run_weight,
                        "boosting": self.settings.ensemble_boosting_weight,
                    },
                },
                parameters={
                    "simulation_draws": self.settings.simulation_draws,
                    "simulation_seed": self.settings.simulation_seed,
                    "settings_hash": settings_hash,
                    "knowledge_time_contract": "GameStateService.get_game_state",
                },
                metrics={
                    "status": "not yet evaluated on a production holdout",
                    "superiority_claim": False,
                },
            )
            self.session.add(model)
            self.session.flush()
        return model, feature

    def _persist_movement(
        self,
        forecast: Forecast,
        snapshot: GameFeatureSnapshot,
        available_as_of: datetime,
    ) -> None:
        previous = self.session.scalar(
            select(Forecast)
            .where(
                Forecast.game_id == forecast.game_id,
                Forecast.forecast_track == forecast.forecast_track,
                Forecast.id != forecast.id,
                Forecast.version < forecast.version,
                Forecast.feature_snapshot_id.is_not(None),
            )
            .order_by(Forecast.version.desc())
            .limit(1)
        )
        previous_snapshot = (
            self.session.get(GameFeatureSnapshot, previous.feature_snapshot_id)
            if previous and previous.feature_snapshot_id else None
        )
        current_families = _movement_families(snapshot)
        previous_families = _movement_families(previous_snapshot) if previous_snapshot else {}
        if previous_snapshot is None:
            current_families = {"initial_snapshot": {
                "checksum": snapshot.checksum,
                "feature_completeness": snapshot.feature_completeness,
            }}
        for family, current_value in current_families.items():
            prior_value = previous_families.get(family)
            if previous_snapshot is not None and prior_value == current_value:
                continue
            associated = [
                row for row in snapshot.source_evidence
                if _evidence_matches_family(str(row.get("family", "")), family)
            ]
            payload = {
                "game_id": str(forecast.game_id),
                "previous_forecast_id": str(previous.id) if previous else None,
                "current_forecast_id": str(forecast.id),
                "family": family,
                "previous": prior_value,
                "current": current_value,
                "associated_input_updates": associated,
            }
            self.session.add(ForecastMovementEvidence(
                game_id=forecast.game_id,
                previous_forecast_id=previous.id if previous else None,
                current_forecast_id=forecast.id,
                previous_feature_snapshot_id=previous_snapshot.id if previous_snapshot else None,
                current_feature_snapshot_id=snapshot.id,
                feature_family=family,
                observed_at=available_as_of,
                available_as_of=available_as_of,
                previous_value=prior_value,
                current_value=current_value,
                delta=_numeric_delta(prior_value, current_value),
                associated_input_updates=associated,
                description=(
                    "Initial point-in-time feature snapshot"
                    if previous_snapshot is None else f"{family.replace('_', ' ').title()} evidence changed"
                ),
                checksum=_json_checksum(payload),
                source_mode=forecast.source_mode,
            ))

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


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _market_snapshot_reference(odds: list[OddsSnapshot]) -> str | None:
    """Bind a forecast to the complete deterministic set of market observations it used."""

    snapshot_ids = sorted({str(row.id) for row in odds})
    return ",".join(snapshot_ids) or None


def _simulation_json(result: Any) -> dict[str, Any]:
    """Serialise the simulation aggregates for the persisted forecast artifact.

    ``draws`` is deliberately dropped: it is an opt-in, read-only convenience
    for replay consumers and never belongs in the stored artifact, which must
    stay exactly the aggregate summary it has always been.
    """

    from dataclasses import asdict
    payload = asdict(result)
    payload.pop("draws", None)
    return payload


def _forecast_lifecycle(stage: LifecycleStage) -> ForecastLifecycle:
    if stage == LifecycleStage.MORNING:
        return ForecastLifecycle.MORNING
    if stage in {LifecycleStage.T_MINUS_6H, LifecycleStage.T_MINUS_3H}:
        return ForecastLifecycle.EARLY
    if stage in {
        LifecycleStage.T_MINUS_60M,
        LifecycleStage.T_MINUS_30M,
        LifecycleStage.T_MINUS_10M,
    }:
        return ForecastLifecycle.PREGAME
    return ForecastLifecycle.FINAL


def _movement_families(snapshot: GameFeatureSnapshot | None) -> dict[str, dict[str, Any]]:
    if snapshot is None:
        return {}
    values = snapshot.feature_values
    return {
        "schedule": {"game_state_observation_id": str(snapshot.game_state_observation_id)},
        "lineup": {
            "home_observation_id": str(snapshot.home_lineup_observation_id) if snapshot.home_lineup_observation_id else None,
            "away_observation_id": str(snapshot.away_lineup_observation_id) if snapshot.away_lineup_observation_id else None,
            "home_status": values.get("home_lineup_status"),
            "away_status": values.get("away_lineup_status"),
        },
        "starter": {
            "home_observation_id": str(snapshot.home_starter_observation_id) if snapshot.home_starter_observation_id else None,
            "away_observation_id": str(snapshot.away_starter_observation_id) if snapshot.away_starter_observation_id else None,
            "home_player_id": values.get("home_starter_player_id"),
            "away_player_id": values.get("away_starter_player_id"),
        },
        "roster": {"observation_ids": snapshot.roster_observation_ids},
        "bullpen": {
            "observation_ids": snapshot.bullpen_observation_ids,
            "home_availability": values.get("home_bullpen_availability"),
            "away_availability": values.get("away_bullpen_availability"),
        },
        "statcast": {
            "observation_ids": snapshot.statcast_observation_ids,
            "home_xwoba": values.get("home_statcast_xwoba"),
            "away_xwoba": values.get("away_statcast_xwoba"),
        },
        "weather": {
            "observation_id": str(snapshot.weather_snapshot_id) if snapshot.weather_snapshot_id else None,
            "temperature_f": values.get("temperature_f"),
            "precipitation_probability": values.get("precipitation_probability"),
        },
        "park": {
            "version_id": str(snapshot.park_factor_version_id) if snapshot.park_factor_version_id else None,
            "run_factor": values.get("park_run_factor"),
        },
        "rating": {
            "home_observation_id": str(snapshot.home_rating_observation_id) if snapshot.home_rating_observation_id else None,
            "away_observation_id": str(snapshot.away_rating_observation_id) if snapshot.away_rating_observation_id else None,
            "home_rating": values.get("home_rating"),
            "away_rating": values.get("away_rating"),
        },
        "market": {"snapshot_ids": snapshot.market_snapshot_ids},
        "team_form": {
            key: values.get(key)
            for key in (
                "home_runs_per_game", "away_runs_per_game",
                "home_recent_runs", "away_recent_runs",
                "home_ops", "away_ops",
            )
        },
    }


def _evidence_matches_family(evidence_family: str, movement_family: str) -> bool:
    aliases = {
        "lineup": {"home_lineup", "away_lineup"},
        "starter": {"home_starter", "away_starter"},
        "rating": {"home_rating", "away_rating"},
        "schedule": {"game_state"},
    }
    return evidence_family == movement_family or evidence_family in aliases.get(movement_family, set())


def _numeric_delta(previous: Any, current: Any) -> dict[str, Any] | None:
    if not isinstance(previous, dict) or not isinstance(current, dict):
        return None
    result: dict[str, float] = {}
    for key in current.keys() & previous.keys():
        before, after = previous[key], current[key]
        if isinstance(before, (int, float)) and isinstance(after, (int, float)):
            result[key] = after - before
    return result or None


def _json_checksum(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(canonical.encode("utf-8")).hexdigest()

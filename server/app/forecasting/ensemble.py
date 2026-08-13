from __future__ import annotations

from dataclasses import asdict, dataclass, field
from math import exp, log, sqrt
from statistics import pstdev
from typing import Mapping

from app.forecasting.simulation import SimulationResult, simulate_game
from app.markets.odds import probability_to_american


@dataclass(frozen=True)
class GameFeatures:
    home_rating: float = 1500.0
    away_rating: float = 1500.0
    home_runs_per_game: float = 4.4
    away_runs_per_game: float = 4.4
    home_runs_allowed_per_game: float = 4.4
    away_runs_allowed_per_game: float = 4.4
    home_recent_runs: float | None = None
    away_recent_runs: float | None = None
    home_starter_era: float | None = None
    away_starter_era: float | None = None
    home_bullpen_era: float | None = None
    away_bullpen_era: float | None = None
    home_ops: float | None = None
    away_ops: float | None = None
    temperature_f: float | None = None
    wind_out_mph: float | None = None
    park_run_factor: float = 1.0
    feature_completeness: float = 1.0
    sample_reliability: float = 0.0


@dataclass(frozen=True)
class ComponentForecast:
    name: str
    model_type: str
    home_win_probability: float
    expected_home_runs: float
    expected_away_runs: float
    weight: float
    version: str
    trained_artifact: bool
    details: dict[str, float | str] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelDriver:
    feature: str
    impact: float
    direction: str
    explanation: str


@dataclass(frozen=True)
class EnsembleForecast:
    expected_home_runs: float
    expected_away_runs: float
    expected_total_runs: float
    expected_run_difference: float
    home_win_probability: float
    away_win_probability: float
    fair_home_moneyline: int
    fair_away_moneyline: int
    uncertainty: float
    confidence: float
    components: tuple[ComponentForecast, ...]
    drivers: tuple[ModelDriver, ...]
    simulation: SimulationResult
    methodology_label: str

    def as_dict(self) -> dict:
        return asdict(self)


class BaselineRatingModel:
    name = "elo_strength_baseline"
    version = "1.0.0"

    def predict(self, features: GameFeatures, weight: float) -> ComponentForecast:
        home_advantage = 24.0
        probability = 1.0 / (1.0 + 10 ** (-(features.home_rating + home_advantage - features.away_rating) / 400.0))
        shift = (probability - 0.5) * 1.7
        base = (features.home_runs_per_game + features.away_runs_per_game) / 2
        return ComponentForecast(
            name=self.name,
            model_type="interpretable rating model",
            home_win_probability=_clip(probability, 0.05, 0.95),
            expected_home_runs=_clip(base + 0.15 + shift, 1.5, 8.5),
            expected_away_runs=_clip(base - shift, 1.5, 8.5),
            weight=weight,
            version=self.version,
            trained_artifact=False,
            details={"home_advantage_elo": home_advantage, "rating_gap": features.home_rating - features.away_rating},
        )


class StatisticalRunModel:
    name = "poisson_run_expectation"
    version = "1.0.0"

    def predict(self, features: GameFeatures, weight: float) -> ComponentForecast:
        home_attack = _blend(features.home_runs_per_game, features.home_recent_runs)
        away_attack = _blend(features.away_runs_per_game, features.away_recent_runs)
        home_pitch = _pitch_adjustment(features.away_starter_era, features.away_bullpen_era)
        away_pitch = _pitch_adjustment(features.home_starter_era, features.home_bullpen_era)
        weather_factor = 1.0
        if features.temperature_f is not None:
            weather_factor *= _clip(1 + (features.temperature_f - 70) * 0.0022, 0.93, 1.08)
        if features.wind_out_mph is not None:
            weather_factor *= _clip(1 + features.wind_out_mph * 0.0025, 0.95, 1.08)
        home_runs = _clip((0.52 * home_attack + 0.48 * features.away_runs_allowed_per_game) * home_pitch * features.park_run_factor * weather_factor * 1.025, 1.25, 9.0)
        away_runs = _clip((0.52 * away_attack + 0.48 * features.home_runs_allowed_per_game) * away_pitch * features.park_run_factor * weather_factor, 1.25, 9.0)
        probability = _logistic((home_runs - away_runs) / 1.55)
        return ComponentForecast(
            name=self.name,
            model_type="statistical scoring model",
            home_win_probability=probability,
            expected_home_runs=home_runs,
            expected_away_runs=away_runs,
            weight=weight,
            version=self.version,
            trained_artifact=False,
            details={"park_factor": features.park_run_factor, "weather_run_factor": weather_factor},
        )


class GradientBoostingCompatibleModel:
    """Feature-contract-compatible challenger with optional trained estimator.

    The deterministic fallback is deliberately identified as untrained. A fitted
    scikit-learn estimator can be injected; no UI or registry may claim it is a
    trained gradient model until ``trained_artifact`` is true.
    """

    name = "gradient_boosting_challenger"
    version = "1.0.0-dev"

    def __init__(self, estimator: object | None = None) -> None:
        self.estimator = estimator

    def predict(self, features: GameFeatures, weight: float) -> ComponentForecast:
        vector = feature_vector(features)
        trained = self.estimator is not None
        if trained:
            probability = float(self.estimator.predict_proba([vector])[0][1])  # type: ignore[attr-defined]
            label = "trained gradient boosting classifier"
        else:
            # Transparent development challenger, not represented as trained ML.
            coefficients = [0.0018, -0.0018, 0.34, -0.34, -0.18, 0.18, 0.55, -0.55, 0.12]
            probability = _logistic(-0.05 + sum(value * coefficient for value, coefficient in zip(vector, coefficients)))
            label = "deterministic development challenger (untrained fallback)"
        run_difference = log(probability / (1 - probability)) * 1.35
        total = _clip(features.home_runs_per_game + features.away_runs_per_game, 5.0, 13.0)
        return ComponentForecast(
            name=self.name,
            model_type=label,
            home_win_probability=_clip(probability, 0.05, 0.95),
            expected_home_runs=_clip(total / 2 + run_difference / 2, 1.25, 9.0),
            expected_away_runs=_clip(total / 2 - run_difference / 2, 1.25, 9.0),
            weight=weight,
            version=self.version,
            trained_artifact=trained,
            details={"feature_count": float(len(vector))},
        )


def feature_vector(features: GameFeatures) -> list[float]:
    return [
        features.home_rating - 1500.0,
        features.away_rating - 1500.0,
        features.home_runs_per_game - 4.4,
        features.away_runs_per_game - 4.4,
        (features.home_starter_era or 4.2) - 4.2,
        (features.away_starter_era or 4.2) - 4.2,
        (features.home_ops or 0.720) - 0.720,
        (features.away_ops or 0.720) - 0.720,
        features.park_run_factor - 1.0,
    ]


class MLBForecastEnsemble:
    def __init__(
        self,
        *,
        baseline_weight: float = 0.30,
        run_weight: float = 0.45,
        boosting_weight: float = 0.25,
        simulation_draws: int = 20_000,
        simulation_seed: int = 20250601,
        boosting_estimator: object | None = None,
    ) -> None:
        weight_total = baseline_weight + run_weight + boosting_weight
        if weight_total <= 0:
            raise ValueError("Ensemble weights must total more than zero")
        self.weights = {
            "baseline": baseline_weight / weight_total,
            "run": run_weight / weight_total,
            "boosting": boosting_weight / weight_total,
        }
        self.simulation_draws = simulation_draws
        self.simulation_seed = simulation_seed
        self.baseline = BaselineRatingModel()
        self.run_model = StatisticalRunModel()
        self.boosting = GradientBoostingCompatibleModel(boosting_estimator)

    def predict(
        self,
        features: GameFeatures,
        *,
        data_readiness: float,
        historical_calibration: float | None = None,
        seed: int | None = None,
    ) -> EnsembleForecast:
        components = (
            self.baseline.predict(features, self.weights["baseline"]),
            self.run_model.predict(features, self.weights["run"]),
            self.boosting.predict(features, self.weights["boosting"]),
        )
        home_runs = sum(component.expected_home_runs * component.weight for component in components)
        away_runs = sum(component.expected_away_runs * component.weight for component in components)
        component_probability = sum(component.home_win_probability * component.weight for component in components)
        simulation = simulate_game(
            home_runs,
            away_runs,
            simulations=self.simulation_draws,
            seed=self.simulation_seed if seed is None else seed,
        )
        home_probability = _clip(0.60 * component_probability + 0.40 * simulation.home_win_probability, 0.02, 0.98)
        disagreement = pstdev(component.home_win_probability for component in components)
        uncertainty = _clip(sqrt(disagreement**2 + home_probability * (1 - home_probability) / self.simulation_draws), 0, 0.5)
        agreement_score = _clip(1 - disagreement / 0.18, 0, 1)
        readiness_score = _clip(data_readiness / 100, 0, 1)
        sample_score = _clip(features.sample_reliability, 0, 1)
        if historical_calibration is None:
            # Until a real chronological evaluation exists, confidence uses
            # only measurable current signals and does not invent a calibration score.
            confidence = 100 * (0.45 * agreement_score + 0.40 * readiness_score + 0.15 * sample_score)
        else:
            confidence = 100 * (
                0.35 * agreement_score
                + 0.30 * readiness_score
                + 0.15 * sample_score
                + 0.20 * _clip(historical_calibration, 0, 1)
            )
        drivers = _model_drivers(features)
        return EnsembleForecast(
            expected_home_runs=home_runs,
            expected_away_runs=away_runs,
            expected_total_runs=home_runs + away_runs,
            expected_run_difference=home_runs - away_runs,
            home_win_probability=home_probability,
            away_win_probability=1 - home_probability,
            fair_home_moneyline=probability_to_american(home_probability),
            fair_away_moneyline=probability_to_american(1 - home_probability),
            uncertainty=uncertainty,
            confidence=round(confidence, 1),
            components=components,
            drivers=drivers,
            simulation=simulation,
            methodology_label="ASP MLB ensemble v1 — quantitative, reproducible, non-LLM forecast",
        )


def _model_drivers(features: GameFeatures) -> tuple[ModelDriver, ...]:
    candidates = [
        ("Team strength", (features.home_rating - features.away_rating) / 400, "Relative rating before first pitch"),
        ("Season offense", (features.home_runs_per_game - features.away_runs_per_game) / 3, "Season-to-date scoring rate difference"),
        ("Starting pitching", ((features.away_starter_era or 4.2) - (features.home_starter_era or 4.2)) / 4, "Starter run-prevention comparison"),
        ("Bullpen", ((features.away_bullpen_era or 4.2) - (features.home_bullpen_era or 4.2)) / 5, "Bullpen effectiveness comparison"),
        ("Park and weather", (features.park_run_factor - 1) + ((features.temperature_f or 70) - 70) / 300, "Run environment adjustment; affects scoring more than side"),
    ]
    ranked = sorted(candidates, key=lambda item: abs(item[1]), reverse=True)
    return tuple(
        ModelDriver(
            feature=name,
            impact=impact,
            direction="home" if impact >= 0 else "away",
            explanation=explanation,
        )
        for name, impact, explanation in ranked
    )


def _blend(season_value: float, recent_value: float | None) -> float:
    return season_value if recent_value is None else 0.72 * season_value + 0.28 * recent_value


def _pitch_adjustment(starter_era: float | None, bullpen_era: float | None) -> float:
    starter = starter_era if starter_era is not None else 4.2
    bullpen = bullpen_era if bullpen_era is not None else 4.2
    combined = 0.60 * starter + 0.40 * bullpen
    return _clip(combined / 4.2, 0.72, 1.32)


def _logistic(value: float) -> float:
    return 1.0 / (1.0 + exp(-value))


def _clip(value: float, minimum: float, maximum: float) -> float:
    return min(max(value, minimum), maximum)

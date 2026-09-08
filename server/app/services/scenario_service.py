"""What-If Scenario Lab — hypothetical recomputation through the real pipeline.

DESIGN RULE
-----------
A scenario control exists only when changing it flows through the *actual*
model path the official forecast used:

    persisted GameFeatureSnapshot
      -> game_features_from_snapshot()      (the same mapping)
      -> dataclasses.replace(...)           (the only scenario edit)
      -> MLBForecastEnsemble(...).predict() (the same ensemble and weights)
      -> simulate_game(...)                 (the same Monte Carlo, same seed)

Nothing here nudges a probability, applies a hand-authored adjustment, or asks
a language model for a number. If a control cannot be recomputed that way it is
reported UNSUPPORTED with the missing capability named, and the UI disables it.

WHAT GEN 1 ACTUALLY SUPPORTS TODAY
----------------------------------
The ensemble consumes a flat numeric feature vector. Player identity reaches it
only through aggregates: the starter through one xERA number, the bullpen
through a participation-weighted xERA, the lineup through an aggregated xwOBA.
Those aggregates come from ``player_talent_estimates``, which is EMPTY in this
deployment, so every player-level feature on a live forecast is None and listed
in ``missing_features``. A pitcher swap would therefore be None -> None: it
could not move the forecast without inventing a number. Those controls are
consequently disabled and reported, not faked.

``temperature_f`` and ``park_run_factor`` are populated and genuinely drive
expected runs, so they are enabled.

This module performs NO database write, NO provider call, and never touches an
official forecast, decision, position or ledger row.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.forecasting.ensemble import MLBForecastEnsemble
from app.models.entities import Forecast, GameFeatureSnapshot, ModelVersion
from app.services.feature_snapshot_service import game_features_from_snapshot

# Baseline reproduction must be exact; this guards a future regression rather
# than absorbing an expected error.
BASELINE_TOLERANCE = 1e-9

# Bounded, baseball-realistic ranges. A value outside the observed evidence
# range is still allowed but flagged out-of-domain rather than silently used.
CONTROL_BOUNDS = {
    "temperature_f": {"min": 25.0, "max": 115.0, "step": 1.0, "domain_min": 40.0, "domain_max": 100.0, "unit": "°F"},
    "park_run_factor": {"min": 0.85, "max": 1.20, "step": 0.01, "domain_min": 0.90, "domain_max": 1.12, "unit": "×"},
}


def _identity_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _metrics(ensemble: Any) -> dict[str, Any]:
    """The comparable outputs of one ensemble run."""
    simulation = ensemble.simulation
    return {
        "home_win_probability": ensemble.home_win_probability,
        "away_win_probability": ensemble.away_win_probability,
        "expected_home_runs": ensemble.expected_home_runs,
        "expected_away_runs": ensemble.expected_away_runs,
        "expected_total_runs": ensemble.expected_total_runs,
        "expected_run_difference": ensemble.expected_run_difference,
        "simulation_home_win_probability": simulation.home_win_probability,
        "home_cover_probabilities": simulation.home_cover_probabilities,
        "away_cover_probabilities": simulation.away_cover_probabilities,
        "total_probabilities": simulation.total_probabilities,
        "total_runs_distribution": simulation.total_runs_distribution,
        "home_runs_distribution": simulation.home_runs_distribution,
        "away_runs_distribution": simulation.away_runs_distribution,
        "fair_home_moneyline": ensemble.fair_home_moneyline,
        "fair_away_moneyline": ensemble.fair_away_moneyline,
    }


def _run(settings: Settings, features: Any, readiness: float) -> Any:
    """The identical ensemble invocation the official forecast performs."""
    return MLBForecastEnsemble(
        baseline_weight=settings.ensemble_baseline_weight,
        run_weight=settings.ensemble_run_weight,
        boosting_weight=settings.ensemble_boosting_weight,
        simulation_draws=settings.simulation_draws,
        simulation_seed=settings.simulation_seed,
    ).predict(features, data_readiness=readiness)


def _load(session: Session, game_id: Any) -> tuple[Forecast, GameFeatureSnapshot] | None:
    forecast = session.scalars(
        select(Forecast).where(Forecast.game_id == game_id)
        .order_by(Forecast.generated_at.desc()).limit(1)
    ).first()
    if forecast is None or forecast.feature_snapshot_id is None:
        return None
    snapshot = session.get(GameFeatureSnapshot, forecast.feature_snapshot_id)
    if snapshot is None:
        return None
    return forecast, snapshot


def scenario_capabilities(session: Session, game_id: Any) -> dict[str, Any] | None:
    """Which scenario controls this game can honestly support, and why not."""
    loaded = _load(session, game_id)
    if loaded is None:
        return None
    forecast, snapshot = loaded
    values = snapshot.feature_values or {}
    missing = set(snapshot.missing_features or [])
    features = game_features_from_snapshot(snapshot)

    def player_level(key: str, label: str, feature_keys: tuple[str, ...]) -> dict[str, Any]:
        absent = [k for k in feature_keys if values.get(k) is None]
        return {
            "key": key,
            "label": label,
            "status": "UNSUPPORTED",
            "model_path": (
                "Reaches the ensemble only as an aggregate numeric feature "
                f"({', '.join(feature_keys)})."
            ),
            "reason": (
                "This deployment has no player talent estimates, so "
                f"{', '.join(absent)} is unavailable on the official forecast. "
                "Substituting a player would have to invent the number the model "
                "consumes, which would not be a recomputation."
            ),
            "required_capability": (
                "A forecast bundle supplying per-player xERA / xwOBA talent "
                "estimates with evidence cutoffs, plus roster and lineup identity."
            ),
            "current_value": None,
        }

    controls: list[dict[str, Any]] = []

    for key, label, feature_name, current in (
        ("temperature_f", "Temperature", "temperature_f", features.temperature_f),
        ("park_run_factor", "Park run factor", "park_run_factor", features.park_run_factor),
    ):
        supported = current is not None
        bounds = CONTROL_BOUNDS[key]
        controls.append({
            "key": key,
            "label": label,
            "status": "SUPPORTED" if supported else "UNSUPPORTED",
            "model_path": (
                "Multiplies both teams' expected runs in the statistical run "
                "component, and park additionally feeds the boosting component "
                "and the published drivers."
            ),
            "reason": None if supported else (
                f"The official forecast has no observed {label.lower()} for this game."
            ),
            "required_capability": None if supported else "Observed value in the feature snapshot.",
            "current_value": current,
            **bounds,
        })

    controls.append({
        "key": "wind_out_mph",
        "label": "Wind",
        "status": "UNSUPPORTED",
        "model_path": "The run component would use wind, but the feature mapping sets it to None.",
        "reason": (
            "game_features_from_snapshot hardcodes wind_out_mph to None, so the official "
            "forecast never has an observed wind value to compare a scenario against."
        ),
        "required_capability": "Wind ingestion into the feature snapshot and mapping.",
        "current_value": None,
    })

    controls.append(player_level(
        "starting_pitcher", "Starting pitcher",
        ("home_starter_xera", "away_starter_xera"),
    ))
    controls.append(player_level(
        "lineup", "Lineup / injury replacement",
        ("home_lineup_xwoba", "away_lineup_xwoba"),
    ))
    controls.append(player_level(
        "bullpen", "Bullpen availability",
        ("home_bullpen_xera", "away_bullpen_xera"),
    ))
    controls.append({
        "key": "starter_workload",
        "label": "Starter innings / pitch limit",
        "status": "UNSUPPORTED",
        "model_path": "None. The feature vector has no starter-duration or batters-faced term.",
        "reason": (
            "Gen 1 draws final scores from two Poisson rates and models no innings or "
            "pitcher usage, so a workload limit has nothing to change."
        ),
        "required_capability": "A Gen 2 plate-appearance simulator with starter and bullpen usage.",
        "current_value": None,
    })

    return {
        "data_mode": "live",
        "game_id": str(game_id),
        "baseline_forecast_id": str(forecast.id),
        "baseline_model_version_id": str(forecast.model_version_id),
        "baseline_evidence_cutoff": snapshot.frozen_at,
        "feature_snapshot_id": str(snapshot.id),
        "missing_features": sorted(missing),
        "controls": controls,
        "supported_count": sum(1 for c in controls if c["status"] == "SUPPORTED"),
        "notice": (
            "Hypothetical research scenario. This does not change ASP's official forecast "
            "or recorded decision."
        ),
    }


def run_scenario(
    session: Session,
    game_id: Any,
    overrides: dict[str, float],
    settings: Settings,
) -> dict[str, Any] | None:
    """Recompute the forecast under hypothetical assumptions. Read-only."""
    loaded = _load(session, game_id)
    if loaded is None:
        return None
    forecast, snapshot = loaded

    capabilities = scenario_capabilities(session, game_id)
    assert capabilities is not None
    allowed = {
        control["key"] for control in capabilities["controls"]
        if control["status"] == "SUPPORTED"
    }
    rejected = {key: "unsupported for this game" for key in overrides if key not in allowed}
    applied = {key: float(value) for key, value in overrides.items() if key in allowed}

    base_features = game_features_from_snapshot(snapshot)
    readiness = float(forecast.readiness_score or 0.0)

    baseline = _run(settings, base_features, readiness)
    # The scenario is only meaningful if the untouched pipeline reproduces the
    # published forecast. Verified here rather than assumed.
    baseline_delta = max(
        abs(baseline.home_win_probability - forecast.home_win_probability),
        abs(baseline.expected_home_runs - forecast.expected_home_runs),
        abs(baseline.expected_away_runs - forecast.expected_away_runs),
    )

    out_of_domain: list[str] = []
    for key, value in applied.items():
        bounds = CONTROL_BOUNDS[key]
        if value < bounds["domain_min"] or value > bounds["domain_max"]:
            out_of_domain.append(key)

    scenario_features = replace(base_features, **applied) if applied else base_features
    scenario = _run(settings, scenario_features, readiness)

    identity = {
        "baseline_forecast_id": str(forecast.id),
        "baseline_model_version_id": str(forecast.model_version_id),
        "feature_snapshot_id": str(snapshot.id),
        "evidence_cutoff": snapshot.frozen_at,
        "simulations": settings.simulation_draws,
        "seed": settings.simulation_seed,
        "overrides": dict(sorted(applied.items())),
    }
    model = session.get(ModelVersion, forecast.model_version_id)

    return {
        "data_mode": "live",
        "status": "HYPOTHETICAL",
        "notice": (
            "Hypothetical research scenario. This does not change ASP's official forecast "
            "or recorded decision, and it never enters the $1,000 experiment."
        ),
        "identity": {
            **identity,
            "scenario_hash": _identity_hash(identity),
            "generated_at": datetime.now(UTC),
            "baseline_model_name": getattr(model, "name", None),
            "baseline_model_version": getattr(model, "version", None),
            "baseline_model_status": getattr(model, "status", None) and str(model.status),
        },
        "applied_overrides": applied,
        "rejected_overrides": rejected,
        "out_of_domain": out_of_domain,
        "baseline_reproduction": {
            "exact": baseline_delta <= BASELINE_TOLERANCE,
            "max_absolute_delta": baseline_delta,
            "tolerance": BASELINE_TOLERANCE,
            "note": (
                "The scenario pipeline re-run with no overrides is compared against the "
                "persisted official forecast before any assumption is applied."
            ),
        },
        "official": _metrics(baseline),
        "scenario": _metrics(scenario),
        "deltas": _deltas(_metrics(baseline), _metrics(scenario)),
        "scenario_joint": _scenario_joint(scenario),
        "interpretation": _interpretation(
            _metrics(baseline), _metrics(scenario), applied, capabilities,
        ),
        "sensitivity": _sensitivity(settings, base_features, readiness, applied),
    }


def _deltas(official: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    """Percentage-point changes for probabilities, absolute for run counts."""
    def points(key: str) -> float:
        return (scenario[key] - official[key]) * 100

    def runs(key: str) -> float:
        return scenario[key] - official[key]

    return {
        "home_win_probability_points": points("home_win_probability"),
        "away_win_probability_points": points("away_win_probability"),
        "expected_home_runs": runs("expected_home_runs"),
        "expected_away_runs": runs("expected_away_runs"),
        "expected_total_runs": runs("expected_total_runs"),
        "expected_run_difference": runs("expected_run_difference"),
        "home_cover_points": {
            line: (scenario["home_cover_probabilities"][line]
                   - official["home_cover_probabilities"][line]) * 100
            for line in official["home_cover_probabilities"]
        },
        "total_over_points": {
            line: (scenario["total_probabilities"][line]["over"]
                   - official["total_probabilities"][line]["over"]) * 100
            for line in official["total_probabilities"]
        },
    }


def _scenario_joint(ensemble: Any) -> dict[str, Any]:
    """Joint score matrix for the scenario, from its own simulation draws."""
    from app.services.simulation_replay_service import _joint_matrix

    draws = ensemble.simulation.draws
    if draws is None:
        return {}
    return _joint_matrix(draws.home, draws.away, draws.regulation_tie)


def _interpretation(
    official: dict[str, Any],
    scenario: dict[str, Any],
    applied: dict[str, float],
    capabilities: dict[str, Any],
) -> str:
    """One sentence generated arithmetically from the computed deltas."""
    if not applied:
        return "No supported assumption is active, so the scenario equals the official forecast."
    labels = {c["key"]: c["label"] for c in capabilities["controls"]}
    changed = ", ".join(f"{labels.get(k, k)} to {v:g}" for k, v in sorted(applied.items()))
    points = (scenario["home_win_probability"] - official["home_win_probability"]) * 100
    total = scenario["expected_total_runs"] - official["expected_total_runs"]
    side = "toward the home team" if points > 0 else "toward the away team"
    if abs(points) < 0.05 and abs(total) < 0.02:
        return f"Changing {changed} leaves the outcome distribution essentially unchanged."
    return (
        f"Changing {changed} shifts {abs(points):.1f} percentage points of win probability "
        f"{side} and changes expected total runs by {total:+.2f}."
    )


def _sensitivity(
    settings: Settings,
    base_features: Any,
    readiness: float,
    applied: dict[str, float],
) -> list[dict[str, Any]]:
    """Isolated effect of each active assumption, each recomputed alone.

    Combined effects need not equal the sum of the isolated effects, and the UI
    says so; these are computed independently, never apportioned.
    """
    if len(applied) < 2:
        return []
    baseline = _run(settings, base_features, readiness)
    rows: list[dict[str, Any]] = []
    for key, value in sorted(applied.items()):
        single = _run(settings, replace(base_features, **{key: value}), readiness)
        rows.append({
            "key": key,
            "home_win_probability_points": (
                single.home_win_probability - baseline.home_win_probability) * 100,
            "expected_total_runs": single.expected_total_runs - baseline.expected_total_runs,
        })
    rows.sort(key=lambda row: abs(row["home_win_probability_points"]), reverse=True)
    return rows

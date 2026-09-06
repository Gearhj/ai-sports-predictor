"""Deterministic, read-only replay of an official MLB forecast simulation.

WHY THIS EXISTS
---------------
The persisted forecast artifact stores only *aggregates*: marginal run
histograms, cover/total probabilities and means. Two things a simulation
product needs are therefore absent from storage:

* the **joint** final-score distribution (the marginals cannot be multiplied
  back into a joint, because the extra-inning tie break moves mass off the
  diagonal), and
* any **individual simulated outcome** to show the user.

Both are recoverable exactly, because the official simulator is pure and the
forecast persists every input it needs: ``expected_home_runs``,
``expected_away_runs``, ``simulations`` and ``seed``. Replaying those inputs
through the same ``simulate_game`` reproduces the official run bit-for-bit,
which :func:`replay_official_simulation` asserts on every call rather than
assuming.

WHAT THIS DOES NOT DO
---------------------
It does not invent a play sequence. The Gen 1 simulator draws two independent
Poisson final scores; it has no innings, plate appearances, base-out states,
batters or pitchers. There is no sequence to recover, so none is produced and
``play_by_play`` reports why. Deriving one from final scores would fabricate
game events that no model produced.

This module performs no writes, no provider calls, and no forecast mutation.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.forecasting.simulation import simulate_game
from app.models.entities import Forecast, ModelVersion

# Matches the marginal histogram cap already used by the simulator, so the
# joint matrix and the persisted marginals describe the same support.
SCORE_CAP = 12

# The replay must reproduce the official aggregates exactly. This tolerance is
# a guard against a future non-determinism regression, not an expected error;
# observed delta on live forecasts is 0.0.
REPLAY_TOLERANCE = 1e-9

REPRESENTATIVE_KEYS = (
    "typical",
    "home_win",
    "away_win",
    "high_scoring",
    "low_scoring",
    "extra_innings",
)


@dataclass(frozen=True)
class ReplayValidation:
    """How closely the replay reproduced the persisted official aggregates."""

    exact: bool
    max_absolute_delta: float
    compared_fields: int
    tolerance: float


def _modal_cell(home: np.ndarray, away: np.ndarray, mask: np.ndarray) -> tuple[int, int] | None:
    """Most frequent exact (home, away) score among the masked draws.

    Ties between equally frequent scores are broken by the lowest home score
    then the lowest away score, so the choice is deterministic and independent
    of dictionary or array ordering.
    """
    if not mask.any():
        return None
    pairs, counts = np.unique(np.stack([home[mask], away[mask]], axis=1), axis=0, return_counts=True)
    best = int(counts.max())
    candidates = [tuple(int(v) for v in pair) for pair, c in zip(pairs, counts) if int(c) == best]
    return min(candidates)


def _first_index(
    home: np.ndarray,
    away: np.ndarray,
    cell: tuple[int, int],
    mask: np.ndarray,
) -> int:
    """Lowest draw index producing this exact score *within the criterion*.

    The mask matters: an extra-inning outcome must cite a draw that actually
    went to extra innings, not merely the first draw anywhere with the same
    final score. Without it the citation would contradict its own label.
    """
    hits = np.flatnonzero((home == cell[0]) & (away == cell[1]) & mask)
    return int(hits[0])


def _representative(
    home: np.ndarray,
    away: np.ndarray,
    regulation_tie: np.ndarray,
) -> list[dict[str, Any]]:
    """Six representative outcomes chosen from ACTUAL draws.

    Every entry is a real simulated result with its draw index, not a scripted
    story. Selection rules are fixed and documented so the same forecast always
    yields the same six outcomes:

    * ``typical``       modal score across all draws
    * ``home_win``      modal score among draws the home team won
    * ``away_win``      modal score among draws the away team won
    * ``high_scoring``  modal score among draws in the top total-runs decile
    * ``low_scoring``   modal score among draws in the bottom total-runs decile
    * ``extra_innings`` modal score among draws that were level in regulation
    """
    total = home + away
    n = len(home)
    high_cut = float(np.quantile(total, 0.90))
    low_cut = float(np.quantile(total, 0.10))

    masks = {
        "typical": np.ones(n, dtype=bool),
        "home_win": home > away,
        "away_win": away > home,
        "high_scoring": total >= high_cut,
        "low_scoring": total <= low_cut,
        "extra_innings": regulation_tie,
    }
    criteria = {
        "typical": "Most frequent exact score across all simulations",
        "home_win": "Most frequent exact score among simulations the home team won",
        "away_win": "Most frequent exact score among simulations the away team won",
        "high_scoring": f"Most frequent score among the highest-scoring 10% (total ≥ {high_cut:g})",
        "low_scoring": f"Most frequent score among the lowest-scoring 10% (total ≤ {low_cut:g})",
        "extra_innings": "Most frequent score among simulations level after regulation",
    }

    outcomes: list[dict[str, Any]] = []
    for key in REPRESENTATIVE_KEYS:
        mask = masks[key]
        cell = _modal_cell(home, away, mask)
        if cell is None:
            # e.g. a forecast with no regulation ties at all. Report it as
            # unavailable rather than substituting a different outcome.
            outcomes.append({
                "key": key, "available": False, "criterion": criteria[key],
                "reason": "No simulation in this run matched this criterion.",
                "home_runs": None, "away_runs": None, "total_runs": None,
                "winner": None, "went_extra_innings": None,
                "draw_index": None, "share_of_simulations": 0.0,
                "matching_simulations": 0, "share_within_criterion": 0.0,
                "criterion_simulations": 0,
            })
            continue
        index = _first_index(home, away, cell, mask)
        matches = int(np.sum((home == cell[0]) & (away == cell[1])))
        within = int(np.sum((home == cell[0]) & (away == cell[1]) & mask))
        outcomes.append({
            "key": key,
            "available": True,
            "criterion": criteria[key],
            "reason": None,
            "home_runs": cell[0],
            "away_runs": cell[1],
            "total_runs": cell[0] + cell[1],
            "winner": "home" if cell[0] > cell[1] else "away",
            "went_extra_innings": bool(regulation_tie[index]),
            "draw_index": index,
            # Overall frequency of this exact final score across the whole run,
            # and the frequency within the selection criterion. Both are given
            # so neither number can be mistaken for the other.
            "share_of_simulations": matches / n,
            "matching_simulations": matches,
            "share_within_criterion": within / max(int(mask.sum()), 1),
            "criterion_simulations": int(mask.sum()),
        })
    return outcomes


def _joint_matrix(home: np.ndarray, away: np.ndarray) -> dict[str, Any]:
    """Joint final-score distribution, capped with an explicit overflow bucket.

    Scores above the cap are accumulated into a labelled overflow row/column
    rather than discarded, so the matrix still sums to 1.
    """
    n = len(home)
    size = SCORE_CAP + 2  # 0..SCORE_CAP plus one overflow bucket
    hi = np.minimum(home, SCORE_CAP + 1)
    ai = np.minimum(away, SCORE_CAP + 1)
    counts = np.zeros((size, size), dtype=np.int64)
    np.add.at(counts, (hi, ai), 1)
    labels = [str(i) for i in range(SCORE_CAP + 1)] + [f">{SCORE_CAP}"]
    return {
        "home_labels": labels,
        "away_labels": labels,
        "probabilities": (counts / n).round(8).tolist(),
        "counts": counts.tolist(),
        "cap": SCORE_CAP,
        "note": (
            "Joint probability of each exact final score, from the official "
            "simulation's own draws. Rows are home runs, columns away runs."
        ),
    }


@lru_cache(maxsize=64)
def _replay(lambda_home: float, lambda_away: float, simulations: int, seed: int) -> dict[str, Any]:
    """Pure, cached replay. Bounded to 64 forecasts so memory stays flat.

    Cached on the exact simulator inputs, so a regenerated forecast with
    different expected runs produces a different entry rather than a stale hit.
    """
    result = simulate_game(lambda_home, lambda_away, simulations=simulations, seed=seed, return_draws=True)
    draws = result.draws
    assert draws is not None
    home, away, tie = draws.home, draws.away, draws.regulation_tie
    return {
        "result": result,
        "joint_score_distribution": _joint_matrix(home, away),
        "representative_outcomes": _representative(home, away, tie),
        "extra_inning_probability": float(np.mean(tie)),
    }


def _validate(persisted: dict[str, Any], result: Any) -> ReplayValidation:
    """Compare replay against the persisted official aggregates, field by field."""
    deltas: list[float] = []
    for field in (
        "home_win_probability", "away_win_probability", "mean_home_runs",
        "mean_away_runs", "mean_total_runs", "mean_run_difference",
    ):
        if field in persisted:
            deltas.append(abs(float(persisted[field]) - float(getattr(result, field))))
    for field in (
        "home_runs_distribution", "away_runs_distribution",
        "total_runs_distribution", "run_difference_distribution",
    ):
        stored = persisted.get(field) or {}
        live = getattr(result, field)
        for key, value in live.items():
            if key in stored:
                deltas.append(abs(float(stored[key]) - float(value)))
    worst = max(deltas) if deltas else 0.0
    return ReplayValidation(
        exact=worst <= REPLAY_TOLERANCE,
        max_absolute_delta=worst,
        compared_fields=len(deltas),
        tolerance=REPLAY_TOLERANCE,
    )


def replay_official_simulation(session: Session, game_id: Any) -> dict[str, Any] | None:
    """Build the read-only simulation payload for one game's official forecast.

    Returns ``None`` when no forecast exists. Returns a payload with
    ``simulation_available = False`` when a forecast exists but its persisted
    artifact cannot support a replay — never a fabricated substitute.
    """
    forecast = session.scalars(
        select(Forecast).where(Forecast.game_id == game_id)
        .order_by(Forecast.generated_at.desc()).limit(1)
    ).first()
    if forecast is None:
        return None

    model = session.get(ModelVersion, forecast.model_version_id)
    identity = {
        "forecast_id": str(forecast.id),
        "forecast_version": forecast.version,
        "forecast_track": str(forecast.forecast_track),
        "lifecycle": str(forecast.lifecycle),
        "generated_at": forecast.generated_at,
        "available_as_of": forecast.available_as_of,
        "model_name": getattr(model, "name", None),
        "model_version": getattr(model, "version", None),
        "model_status": getattr(model, "status", None) and str(model.status),
        "feature_snapshot_reference": forecast.feature_snapshot_reference,
        "development_model": True,
        "development_note": (
            "Gen 1 is a development model. It has no proven betting edge and is "
            "not investment advice."
        ),
    }

    summary = forecast.simulation_summary or {}
    unsupported = {
        # Provenance: this is persisted live evidence, never demo or illustrative.
        "data_mode": "live",
        "simulation_available": False,
        "identity": identity,
        "play_by_play": _play_by_play_state(),
        "official_win_probability": {
            "home": forecast.home_win_probability,
            "away": forecast.away_win_probability,
        },
    }
    if not summary or "seed" not in summary or "simulations" not in summary:
        return {**unsupported, "unavailable_reason":
                "This forecast has no persisted simulation summary, so its simulation "
                "cannot be replayed."}
    if not forecast.expected_home_runs or not forecast.expected_away_runs:
        return {**unsupported, "unavailable_reason":
                "This forecast does not persist the expected-run inputs the simulator "
                "requires, so its simulation cannot be replayed."}

    replay = _replay(
        float(forecast.expected_home_runs),
        float(forecast.expected_away_runs),
        int(summary["simulations"]),
        int(summary["seed"]),
    )
    result = replay["result"]
    validation = _validate(summary, result)

    return {
        "data_mode": "live",
        "simulation_available": True,
        "unavailable_reason": None,
        "identity": identity,
        "inputs": {
            "expected_home_runs": float(forecast.expected_home_runs),
            "expected_away_runs": float(forecast.expected_away_runs),
            "simulations": int(summary["simulations"]),
            "seed": int(summary["seed"]),
            "simulator": "independent-Poisson final score with extra-inning tie break",
        },
        "replay_validation": {
            "exact": validation.exact,
            "max_absolute_delta": validation.max_absolute_delta,
            "compared_fields": validation.compared_fields,
            "tolerance": validation.tolerance,
            "note": (
                "The replay is compared field-by-field against the persisted official "
                "forecast. Values shown come from that verified replay."
            ),
        },
        # The official published probability is a governed blend of the model
        # components and the simulation; it is NOT the raw simulation rate, and
        # the two are reported separately so neither is misread as the other.
        "official_win_probability": {
            "home": forecast.home_win_probability,
            "away": forecast.away_win_probability,
        },
        "simulation_win_probability": {
            "home": result.home_win_probability,
            "away": result.away_win_probability,
        },
        "means": {
            "home_runs": result.mean_home_runs,
            "away_runs": result.mean_away_runs,
            "total_runs": result.mean_total_runs,
            "run_difference": result.mean_run_difference,
        },
        "home_runs_distribution": result.home_runs_distribution,
        "away_runs_distribution": result.away_runs_distribution,
        "total_runs_distribution": result.total_runs_distribution,
        "run_difference_distribution": result.run_difference_distribution,
        "home_cover_probabilities": result.home_cover_probabilities,
        "away_cover_probabilities": result.away_cover_probabilities,
        "total_probabilities": result.total_probabilities,
        "joint_score_distribution": replay["joint_score_distribution"],
        "representative_outcomes": replay["representative_outcomes"],
        "extra_inning_probability": replay["extra_inning_probability"],
        "sampling_uncertainty": _sampling_uncertainty(
            result.home_win_probability, int(summary["simulations"])
        ),
        "play_by_play": _play_by_play_state(),
    }


def _sampling_uncertainty(probability: float, simulations: int) -> dict[str, Any]:
    """Monte Carlo standard error of the win rate, with a 95% interval.

    This is the uncertainty of the *sampling*, not of the model itself, and is
    labelled that way so it is not read as a confidence interval on reality.
    """
    se = float(np.sqrt(max(probability * (1 - probability), 0.0) / simulations))
    return {
        "standard_error": se,
        "confidence_95_low": max(0.0, probability - 1.96 * se),
        "confidence_95_high": min(1.0, probability + 1.96 * se),
        "simulations": simulations,
        "note": (
            "Monte Carlo sampling error only: how much this win rate would move if "
            "the same model were resampled. It is not model or forecast uncertainty."
        ),
    }


def _play_by_play_state() -> dict[str, Any]:
    """Honest, fixed statement of why no play sequence is shown."""
    return {
        "available": False,
        "reason": (
            "Play-by-play simulation is unavailable for this forecast. The official "
            "Gen 1 simulator draws final scores directly and models no innings, plate "
            "appearances, base-out states, batters or pitchers, so no play sequence "
            "exists to replay."
        ),
        "what_is_shown_instead": (
            "Representative final outcomes selected from the official simulation's own "
            "draws, and the full joint distribution of simulated final scores."
        ),
    }

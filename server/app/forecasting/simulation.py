from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

import numpy as np


@dataclass(frozen=True)
class SimulationDraws:
    """The raw per-draw outcomes behind a :class:`SimulationResult`.

    Exposed so a read-only consumer can render the *joint* score distribution
    and pick representative outcomes from actual draws, instead of guessing a
    joint from the marginal histograms. ``regulation_tie`` marks draws that
    finished level in regulation and were resolved by the extra-inning
    Bernoulli, which is the only extra-inning evidence this simulator has.

    ``home``/``away`` are post-tie-break final scores, so they always disagree.
    """

    home: np.ndarray
    away: np.ndarray
    regulation_tie: np.ndarray


@dataclass(frozen=True)
class SimulationResult:
    simulations: int
    seed: int
    home_win_probability: float
    away_win_probability: float
    mean_home_runs: float
    mean_away_runs: float
    mean_total_runs: float
    mean_run_difference: float
    home_cover_probabilities: dict[str, float]
    away_cover_probabilities: dict[str, float]
    total_probabilities: dict[str, dict[str, float]]
    home_runs_distribution: dict[str, float]
    away_runs_distribution: dict[str, float]
    total_runs_distribution: dict[str, float]
    run_difference_distribution: dict[str, float]
    # Populated only when the caller opts in. Kept off the default path so the
    # ordinary forecast write never carries 20,000 integers into JSON.
    draws: SimulationDraws | None = None


def simulate_game(
    expected_home_runs: float,
    expected_away_runs: float,
    *,
    simulations: int = 20_000,
    seed: int = 20250601,
    run_lines: tuple[float, ...] = (-1.5, 1.5),
    totals: tuple[float, ...] = (7.5, 8.0, 8.5, 9.0, 9.5),
    return_draws: bool = False,
) -> SimulationResult:
    """Seeded independent-Poisson game simulation with an explicit tie breaker.

    Regulation ties are resolved as an extra-inning Bernoulli using the relative
    run intensities. This ensures baseball outcomes sum to one without pretending
    that a final game can tie.
    """
    for value in (expected_home_runs, expected_away_runs):
        if not isfinite(value) or value <= 0:
            raise ValueError("Expected runs must be positive finite values")
    if simulations < 100:
        raise ValueError("At least 100 simulations are required")
    rng = np.random.default_rng(seed)
    home = rng.poisson(expected_home_runs, simulations)
    away = rng.poisson(expected_away_runs, simulations)
    ties = home == away
    # Snapshot before the tie break mutates the scores: this is the only
    # record of which draws needed extra innings.
    regulation_tie = ties.copy()
    tie_count = int(ties.sum())
    if tie_count:
        home_extra_probability = expected_home_runs / (expected_home_runs + expected_away_runs)
        home_wins_tie = rng.random(tie_count) < home_extra_probability
        tie_indices = np.flatnonzero(ties)
        home[tie_indices[home_wins_tie]] += 1
        away[tie_indices[~home_wins_tie]] += 1

    difference = home - away
    total = home + away
    home_wins = home > away
    home_cover = {f"{line:+g}": float(np.mean(difference + line > 0)) for line in run_lines}
    away_cover = {f"{line:+g}": float(np.mean(-difference + line > 0)) for line in run_lines}
    total_probabilities: dict[str, dict[str, float]] = {}
    for line in totals:
        total_probabilities[f"{line:g}"] = {
            "over": float(np.mean(total > line)),
            "under": float(np.mean(total < line)),
            "push": float(np.mean(total == line)),
        }
    return SimulationResult(
        simulations=simulations,
        seed=seed,
        home_win_probability=float(np.mean(home_wins)),
        away_win_probability=float(np.mean(~home_wins)),
        mean_home_runs=float(np.mean(home)),
        mean_away_runs=float(np.mean(away)),
        mean_total_runs=float(np.mean(total)),
        mean_run_difference=float(np.mean(difference)),
        home_cover_probabilities=home_cover,
        away_cover_probabilities=away_cover,
        total_probabilities=total_probabilities,
        home_runs_distribution=_histogram(home, 0, 12),
        away_runs_distribution=_histogram(away, 0, 12),
        total_runs_distribution=_histogram(total, 0, 20),
        run_difference_distribution=_histogram(difference, -10, 10),
        draws=(
            SimulationDraws(home=home, away=away, regulation_tie=regulation_tie)
            if return_draws else None
        ),
    )


def _histogram(values: np.ndarray, minimum: int, maximum: int) -> dict[str, float]:
    total = len(values)
    result = {str(value): float(np.sum(values == value) / total) for value in range(minimum, maximum + 1)}
    result[f"<{minimum}"] = float(np.sum(values < minimum) / total)
    result[f">{maximum}"] = float(np.sum(values > maximum) / total)
    return result

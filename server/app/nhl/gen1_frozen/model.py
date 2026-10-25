"""A pandas-free port of the frozen NHL Gen1 regulation-Poisson model's INFERENCE path.

SOURCE
------
Model Studio ``nhl-gen1-overnight-20261001-v1/code/nhl_gen1.py`` (sha256
``d69af26729a4cbc4f001264ed7a80e13eabda6d851756dfe0c30c2b04dd9d467``): only
``GoalModel``, ``design()`` and ``predict_goal()`` are ported. Nothing that fits,
evaluates or reads an outcome is ported; the fitted state is the vendored
``selected_model.json`` and is never refitted here.

WHAT IS IDENTICAL
-----------------
The design matrix (intercept, one common home-ice term, offence and defence
team indicators, the capped rest difference and the two back-to-back flags),
``mu = exp(x @ theta)``, the Skellam regulation three-way and the pooled tie
split ``p_final = p_home + p_tie * tiedRegHomeAllocation``. Both of the
original's assertions are kept and raise.

WHAT IS DIFFERENT
-----------------
Rows are plain mappings instead of a DataFrame, and the result is a list of
dicts instead of a DataFrame. No arithmetic changes. The reproduction test
(``tests/test_nhl_inprocess_generator.py``) holds this port to the producer's
own prepared forecasts.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.stats import skellam

SOURCE_MODEL_CODE_SHA256 = "d69af26729a4cbc4f001264ed7a80e13eabda6d851756dfe0c30c2b04dd9d467"


@dataclass
class GoalModel:
    team_ids: list
    theta: np.ndarray
    half_life_days: float
    ridge: float
    cutoff: str
    tied_reg_home_allocation: float
    fitting_games: int
    converged: bool
    max_gradient: float


def design(rows: Sequence[Mapping[str, Any]], teams: Sequence[str]) -> np.ndarray:
    """``nhl_gen1.design``: one row per (game, side); home rows first, then away rows."""

    lookup = {v: i for i, v in enumerate(teams)}
    n = len(rows)
    t = len(teams)
    x = np.zeros((2 * n, 2 * t + 5), dtype=float)
    x[:, 0] = 1.0
    # One common home-ice log effect. Opposing-team defense is a goal allowance effect.
    x[:n, 1] = 1.0
    for j, row in enumerate(rows):
        home, away = row["home_id"], row["away_id"]
        home_rest, away_rest = row["home_rest_days"], row["away_rest_days"]
        for i, off, deff, ownrest, opprest in [(j, home, away, home_rest, away_rest),
                                               (n + j, away, home, away_rest, home_rest)]:
            if off in lookup:
                x[i, 2 + lookup[off]] = 1.0
            if deff in lookup:
                x[i, 2 + t + lookup[deff]] = 1.0
            x[i, 2 + 2 * t] = (min(ownrest, 4) - min(opprest, 4)) / 3.0
            x[i, 3 + 2 * t] = float(ownrest == 1)
            x[i, 4 + 2 * t] = float(opprest == 1)
    return x


def predict_goal(model: GoalModel, rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """``nhl_gen1.predict_goal``: regulation Poisson means, Skellam three-way, pooled-tie final."""

    x = design(rows, model.team_ids)
    mu = np.exp(x @ model.theta)
    n = len(rows)
    h = mu[:n]
    a = mu[n:]
    p_tie = skellam.pmf(0, h, a)
    p_away = skellam.cdf(-1, h, a)
    p_home = skellam.sf(0, h, a)
    if not np.allclose(p_home + p_tie + p_away, 1, atol=1e-12):
        raise AssertionError("REGULATION_MASS_NOT_ONE")
    p_final = p_home + p_tie * model.tied_reg_home_allocation
    if not np.all((p_final > 0) & (p_final < 1)):
        raise AssertionError("FINAL_PROBABILITY_OUT_OF_RANGE")
    return [{
        "game_id": row["game_id"],
        "goal_home_mean_regulation": float(h[j]),
        "goal_away_mean_regulation": float(a[j]),
        "reg_home_win": float(p_home[j]),
        "reg_tie": float(p_tie[j]),
        "reg_away_win": float(p_away[j]),
        "tied_reg_home_allocation": model.tied_reg_home_allocation,
        "goal_home_win_final": float(p_final[j]),
        "fit_cutoff": model.cutoff,
        "fit_games": model.fitting_games,
    } for j, row in enumerate(rows)]


def model_from_state(state: Mapping[str, Any]) -> GoalModel:
    """The frozen state exactly as the producer's preparer builds it (no refit)."""

    return GoalModel(state["teamIds"], np.array(state["theta"]), state["halfLifeDays"], state["ridge"],
                     state["fitCutoff"], state["tiedRegHomeAllocation"], state["fittingGames"], state["converged"],
                     state["maxGradient"])


__all__ = ["GoalModel", "SOURCE_MODEL_CODE_SHA256", "design", "model_from_state", "predict_goal"]

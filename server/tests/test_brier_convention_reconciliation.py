"""Two Brier conventions live in this codebase, and they differ by a factor of two.

AISP persists ``research_forecast_evaluations.brier_score`` as the THREE-CLASS
SUM, because the same scorer grades NFL forecasts that publish a tie
probability. Model Studio reports the binary ``(p - y)^2``. For MLB, where the
published tie probability is 0.0, the first is exactly twice the second -- which
is why a five-game cohort read 0.4472 here and "about half that" there.

Neither value is wrong and neither is rewritten. What matters is that the
relationship is exact, that it holds only under the stated condition, and that
converting between them cannot reorder two arms.
"""
from __future__ import annotations

import math

import pytest

from app.services.forecast_scoring import (
    OutcomeProbabilities,
    brier_score,
    log_loss,
    two_way_brier,
    two_way_log_loss,
)

# The five real (home win probability, outcome) pairs from the September 12
# transition cohort, candidate arm, with the stored three-class sums.
COHORT = (
    ("822927", 0.5538, "HOME", 0.398188880),
    ("823009", 0.4632, "AWAY", 0.429108480),
    ("823737", 0.6380, "HOME", 0.262088000),
    ("824870", 0.4808, "HOME", 0.539137280),
    ("825035", 0.5572, "AWAY", 0.620943680),
)


def _probabilities(home: float) -> OutcomeProbabilities:
    return OutcomeProbabilities(home=home, away=1.0 - home, tie=0.0)


@pytest.mark.parametrize("game,home_probability,outcome,stored", COHORT)
def test_the_stored_brier_is_exactly_twice_the_binary_one(
    game: str, home_probability: float, outcome: str, stored: float,
) -> None:
    probabilities = _probabilities(home_probability)

    three_class = brier_score(probabilities, outcome)
    binary = two_way_brier(home_probability, outcome)

    assert three_class == pytest.approx(stored, abs=1e-9), game
    assert three_class == pytest.approx(2.0 * binary, rel=1e-12), game
    assert 0.0 <= binary <= 1.0
    assert 0.0 <= three_class <= 2.0


@pytest.mark.parametrize("game,home_probability,outcome,stored", COHORT)
def test_log_loss_needs_no_conversion(
    game: str, home_probability: float, outcome: str, stored: float,
) -> None:
    """The two definitions coincide once the tie class carries no mass."""

    assert log_loss(_probabilities(home_probability), outcome) == pytest.approx(
        two_way_log_loss(home_probability, outcome), rel=1e-12), game


def test_the_factor_of_two_depends_on_an_empty_tie_class() -> None:
    """State the condition, so nobody applies the halving to a tie sport.

    An NFL forecast publishes real tie mass, and there the stored sum is NOT
    twice the binary score -- the tie term contributes on its own.
    """

    with_tie = OutcomeProbabilities(home=0.55, away=0.40, tie=0.05)

    three_class = brier_score(with_tie, "HOME")
    binary = two_way_brier(0.55, "HOME")

    assert not math.isclose(three_class, 2.0 * binary, rel_tol=1e-6)


def test_converting_conventions_cannot_reorder_the_two_arms() -> None:
    """Halving both arms is monotone, so the ranking is convention-independent."""

    candidate = [(p, outcome) for _, p, outcome, _ in COHORT]
    baseline = [(0.5536, "HOME"), (0.4602, "AWAY"), (0.6346, "HOME"),
                (0.4850, "HOME"), (0.5552, "AWAY")]

    def mean(pairs, scorer):
        return sum(scorer(p, outcome) for p, outcome in pairs) / len(pairs)

    stored_gap = (mean(candidate, lambda p, o: brier_score(_probabilities(p), o))
                  - mean(baseline, lambda p, o: brier_score(_probabilities(p), o)))
    binary_gap = (mean(candidate, two_way_brier) - mean(baseline, two_way_brier))

    assert stored_gap > 0 and binary_gap > 0, "baseline scored lower under both"
    assert stored_gap == pytest.approx(2.0 * binary_gap, rel=1e-12)

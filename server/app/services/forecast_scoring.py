"""Scoring rules for a prospective forecast against a final result.

WHY THIS IS A SEPARATE, PURE MODULE
-----------------------------------
Every number on the results page comes from here, and every one of them is a
formula somebody can check by hand. Keeping them out of the database layer
means the arithmetic can be tested against a worked example rather than
against whatever the last query returned -- which is the only way to notice
that a Brier score has silently become a two-class one, or that a margin error
lost its sign.

WHAT IS AND IS NOT A DISTRIBUTION
---------------------------------
Two of these metrics need a distribution and the rest do not. A forecast that
publishes only a mean and three outcome probabilities can be scored for log
loss, Brier, margin error and total error, and CANNOT be scored for CRPS,
energy score or interval coverage. When that happens the metric is reported
unavailable with the reason. Nothing here manufactures draws from a summary:
a Normal fitted to a published mean and standard error would produce a CRPS
that looks exactly like a real one and measures nothing.

The distributions this module does accept are probability MASS functions over
integer scores -- the form the simulator actually publishes, from a stated
number of draws. CRPS over a discrete CDF and the energy score over a discrete
joint are both exact sums, not approximations, so no resampling is involved.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import log, sqrt
from typing import Iterable, Sequence

#: The three mutually exclusive results of a game.
HOME = "HOME"
AWAY = "AWAY"
TIE = "TIE"
CLASSES = (HOME, AWAY, TIE)

#: Probabilities are clipped before a logarithm. A published 0 for the outcome
#: that happened would otherwise make the log loss infinite and take the whole
#: aggregate with it; the clip is stated so the floor is visible rather than
#: being an invisible property of the arithmetic.
LOG_LOSS_EPSILON = 1e-15


class ScoringError(ValueError):
    """The inputs cannot be scored as supplied."""


@dataclass(frozen=True)
class OutcomeProbabilities:
    home: float
    away: float
    tie: float

    def normalised(self) -> "OutcomeProbabilities":
        """Renormalise a published triple that does not quite sum to one.

        The producer publishes rounded probabilities, so 0.5736 + 0.3966 +
        0.0298 is 1.0000 only by luck. Scoring rules assume a proper
        distribution, so the triple is normalised and the adjustment is
        reported by ``mass_before_normalising`` rather than hidden.
        """

        total = self.home + self.away + self.tie
        if total <= 0:
            raise ScoringError("outcome probabilities sum to zero")
        return OutcomeProbabilities(self.home / total, self.away / total, self.tie / total)

    @property
    def mass(self) -> float:
        return self.home + self.away + self.tie

    def of(self, outcome: str) -> float:
        return {HOME: self.home, AWAY: self.away, TIE: self.tie}[outcome]

    @property
    def predicted_class(self) -> str:
        """The argmax. A tie in the argmax resolves in CLASSES order, stated."""

        return max(CLASSES, key=lambda name: (self.of(name), -CLASSES.index(name)))


def actual_class(home_score: int, away_score: int) -> str:
    if home_score > away_score:
        return HOME
    if away_score > home_score:
        return AWAY
    return TIE


def log_loss(probabilities: OutcomeProbabilities, outcome: str) -> float:
    """Negative log of the probability given to what actually happened."""

    p = probabilities.normalised().of(outcome)
    return -log(max(p, LOG_LOSS_EPSILON))


def brier_score(probabilities: OutcomeProbabilities, outcome: str) -> float:
    """Sum of squared errors over ALL THREE classes.

    Not the two-class version: a football forecast publishes a tie probability
    and dropping it would quietly reward a model for ignoring the outcome it
    is least sure about.
    """

    normalised = probabilities.normalised()
    return sum((normalised.of(name) - (1.0 if name == outcome else 0.0)) ** 2 for name in CLASSES)


@dataclass(frozen=True)
class PointErrors:
    predicted: float
    actual: float

    @property
    def signed(self) -> float:
        """Predicted minus actual. Positive means the forecast was too high."""

        return self.predicted - self.actual

    @property
    def absolute(self) -> float:
        return abs(self.signed)


def mean_absolute_team_score_error(home: PointErrors, away: PointErrors) -> float:
    return (home.absolute + away.absolute) / 2.0


# -- distribution scoring ---------------------------------------------------

@dataclass(frozen=True)
class MassFunction:
    """A probability mass function over integer values, from real draws."""

    values: tuple[int, ...]
    probabilities: tuple[float, ...]
    draws: int | None = None

    @classmethod
    def from_pairs(cls, pairs: Iterable[tuple[int, float]], *, draws: int | None = None) -> "MassFunction":
        merged: dict[int, float] = {}
        for value, probability in pairs:
            merged[int(value)] = merged.get(int(value), 0.0) + float(probability)
        if not merged:
            raise ScoringError("mass function is empty")
        total = sum(merged.values())
        if total <= 0:
            raise ScoringError("mass function sums to zero")
        ordered = sorted(merged)
        return cls(
            values=tuple(ordered),
            probabilities=tuple(merged[value] / total for value in ordered),
            draws=draws,
        )

    def cdf_at(self, value: int) -> float:
        total = 0.0
        for candidate, probability in zip(self.values, self.probabilities, strict=True):
            if candidate > value:
                break
            total += probability
        return total

    def quantile(self, fraction: float) -> int:
        """Smallest value whose CDF reaches ``fraction``."""

        accumulated = 0.0
        for value, probability in zip(self.values, self.probabilities, strict=True):
            accumulated += probability
            if accumulated >= fraction - 1e-12:
                return value
        return self.values[-1]

    def central_interval(self, level: float) -> tuple[int, int]:
        """Equal-tailed interval. Discrete support makes it conservative."""

        if not 0 < level < 1:
            raise ScoringError("interval level must be between 0 and 1")
        tail = (1 - level) / 2
        return self.quantile(tail), self.quantile(1 - tail)

    @property
    def mean(self) -> float:
        return sum(v * p for v, p in zip(self.values, self.probabilities, strict=True))


def crps(distribution: MassFunction, observed: int) -> float:
    """Continuous ranked probability score for a discrete predictive CDF.

    CRPS = integral over x of (F(x) - 1{x >= y})^2 dx. On integer support with
    unit spacing that integral is an exact sum over the steps, so this is the
    real thing rather than a Monte Carlo estimate of it.
    """

    values = distribution.values
    low = min(values[0], observed) - 1
    high = max(values[-1], observed) + 1
    total = 0.0
    cumulative = 0.0
    index = 0
    for x in range(low, high + 1):
        while index < len(values) and values[index] <= x:
            cumulative += distribution.probabilities[index]
            index += 1
        indicator = 1.0 if x >= observed else 0.0
        total += (cumulative - indicator) ** 2
    return total


@dataclass(frozen=True)
class JointMass:
    """A probability mass function over (home, away) integer score pairs."""

    cells: tuple[tuple[int, int, float], ...]
    draws: int | None = None

    @classmethod
    def from_cells(
        cls, rows: Iterable[tuple[int, int, float]], *, draws: int | None = None,
    ) -> "JointMass":
        merged: dict[tuple[int, int], float] = {}
        for home, away, probability in rows:
            key = (int(home), int(away))
            merged[key] = merged.get(key, 0.0) + float(probability)
        if not merged:
            raise ScoringError("joint mass function is empty")
        total = sum(merged.values())
        if total <= 0:
            raise ScoringError("joint mass function sums to zero")
        return cls(
            cells=tuple((home, away, probability / total)
                        for (home, away), probability in sorted(merged.items())),
            draws=draws,
        )

    def marginal_home(self) -> MassFunction:
        return MassFunction.from_pairs(((home, p) for home, _, p in self.cells), draws=self.draws)

    def marginal_away(self) -> MassFunction:
        return MassFunction.from_pairs(((away, p) for _, away, p in self.cells), draws=self.draws)

    def margin(self) -> MassFunction:
        return MassFunction.from_pairs(
            ((home - away, p) for home, away, p in self.cells), draws=self.draws)

    def total(self) -> MassFunction:
        return MassFunction.from_pairs(
            ((home + away, p) for home, away, p in self.cells), draws=self.draws)


def energy_score(joint: JointMass, observed: tuple[int, int]) -> float:
    """Two-dimensional energy score: E||X - y|| - 0.5 E||X - X'||.

    Computed exactly from the mass function rather than sampled from it. The
    second term is the double sum over cell pairs, which is what makes this a
    proper score rather than a distance to the mean.
    """

    home_y, away_y = observed
    first = sum(
        p * sqrt((home - home_y) ** 2 + (away - away_y) ** 2)
        for home, away, p in joint.cells
    )
    second = 0.0
    for index, (home_a, away_a, p_a) in enumerate(joint.cells):
        for home_b, away_b, p_b in joint.cells[index + 1:]:
            second += 2 * p_a * p_b * sqrt((home_a - home_b) ** 2 + (away_a - away_b) ** 2)
    return first - 0.5 * second


def interval_coverage(
    distribution: MassFunction, observed: int, levels: Sequence[float] = (0.5, 0.8, 0.9),
) -> dict[str, dict[str, float | int | bool]]:
    """Per-level interval and whether the observation fell inside it.

    For ONE game this is a hit or a miss, not a coverage rate. The aggregate is
    the rate; the caller is responsible for saying which it is showing.
    """

    out: dict[str, dict[str, float | int | bool]] = {}
    for level in levels:
        low, high = distribution.central_interval(level)
        out[f"{int(level * 100)}"] = {
            "level": level, "lower": low, "upper": high,
            "contains": bool(low <= observed <= high),
            "width": high - low,
        }
    return out


# -- market scoring ---------------------------------------------------------

def implied_probability(american: int) -> float:
    if american == 0:
        raise ScoringError("an American price of zero has no implied probability")
    return 100 / (american + 100) if american > 0 else -american / (-american + 100)


def no_vig_two_way(home_american: int, away_american: int) -> tuple[float, float]:
    """Proportionally remove the margin from a complete two-sided quote.

    Two-way only, and deliberately so: a three-way NFL market including the tie
    is a different quote, and pushing a two-way price through a three-way
    scoring rule would assign the tie a probability the book never offered.
    """

    home = implied_probability(home_american)
    away = implied_probability(away_american)
    total = home + away
    if total <= 0:
        raise ScoringError("implied probabilities sum to zero")
    return home / total, away / total


def two_way_log_loss(home_probability: float, outcome: str) -> float:
    """Log loss of a two-way market against a settled two-way result.

    A tie has no two-way answer, so the caller must not reach here with one.
    """

    if outcome == TIE:
        raise ScoringError("a two-way market cannot be scored against a tie")
    p = home_probability if outcome == HOME else 1 - home_probability
    return -log(max(p, LOG_LOSS_EPSILON))


def two_way_brier(home_probability: float, outcome: str) -> float:
    if outcome == TIE:
        raise ScoringError("a two-way market cannot be scored against a tie")
    actual = 1.0 if outcome == HOME else 0.0
    return (home_probability - actual) ** 2


def spread_grade(home_margin: int, home_handicap: float) -> str:
    """Did the home side cover its own handicap? PUSH is its own answer."""

    adjusted = home_margin + home_handicap
    if adjusted > 0:
        return "HOME_COVER"
    if adjusted < 0:
        return "AWAY_COVER"
    return "PUSH"


def total_grade(actual_total: int, line: float) -> str:
    if actual_total > line:
        return "OVER"
    if actual_total < line:
        return "UNDER"
    return "PUSH"


__all__ = [
    "AWAY", "CLASSES", "HOME", "JointMass", "LOG_LOSS_EPSILON", "MassFunction",
    "OutcomeProbabilities", "PointErrors", "ScoringError", "TIE", "actual_class",
    "brier_score", "crps", "energy_score", "implied_probability", "interval_coverage",
    "log_loss", "mean_absolute_team_score_error", "no_vig_two_way", "spread_grade",
    "total_grade", "two_way_brier", "two_way_log_loss",
]

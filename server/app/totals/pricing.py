"""Over, Under and Push at a line, from V18's retained total-runs mass function.

WHERE THE PROBABILITY COMES FROM, AND WHERE IT DOES NOT
------------------------------------------------------
V18 stores ``distributions.totalRunDistribution``: a mass function over integer
total runs, taken from the paired score draws of one simulation run. That is the
only admissible source here, and three plausible alternatives are refused:

*   **Convolving the home and away marginals.** Those are stored too, and
    combining them would be easy and wrong -- it assumes the two scores are
    independent. They are not: the marginals convolve to a distribution roughly
    0.08-0.10 total-variation away from the paired total, and they put half a
    percent of mass on a 0-0 final, which cannot happen in MLB.
*   **Comparing the mean total with the line.** A mean is not a probability, and
    an 8.6-run mean against a 9.0 line says nothing about how often the game
    goes over without the shape around it.
*   **A score-limited candidate population.** That configuration conditions on
    games staying inside published scoring limits, so it answers a narrower
    question. It stays a research comparison and never prices a market.

WHAT A LINE MEANS
-----------------
For Over at line L: win when total > L, push when total == L, loss when
total < L. Under reverses win and loss and keeps the same push. A push is only
reachable on an integer line, and integer lines are common in MLB, so the push
branch is computed rather than assumed away -- treating a push as a loss would
overstate the cost of an 8.0 total by several points.

UNITS
-----
Every probability this module returns is a **percent in [0, 100]**, and every
structure says so in its own ``probability_units`` field. The mass function on
the way in is a fraction in [0, 1]. The conversion happens once, here, because
the alternative -- percent and fraction flowing through the same call graph --
is how a page ends up rendering "6024.0%".

MONEY
-----
Expected net return per unit stake at decimal odds ``d`` is
``p_win * (d - 1) - p_loss``, with probabilities as fractions. A push
contributes zero: the stake comes back, so it neither wins nor loses. This is a
**model-implied estimate**, not an offer and not a projection of realised
profit, and callers are expected to label it that way.

No calibration adjustment is applied. In particular the moneyline starter
penalty is NOT applied to totals: it was fitted against moneyline outcomes and
carrying it across markets would present an unvalidated adjustment as an
established one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

#: Probabilities crossing this module's boundary are percents in [0, 100].
PROBABILITY_UNITS = "PERCENT"

#: A stored mass function must sum to 1 within this much to be usable. V18's own
#: distributions sum to 1.000000 exactly; the tolerance exists so a rounding
#: convention in a future producer build is not read as a corrupt population.
MASS_TOLERANCE = 1e-6

#: The distribution is the retained draws, so its resolution is 1/n. A summary
#: reported to more precision than that would imply evidence that is not there.
PROBABILITY_DECIMALS = 4


class TotalsPricingError(ValueError):
    """The stored evidence cannot price a total, and no estimate is invented."""


@dataclass(frozen=True)
class TotalsDistribution:
    """One forecast's mass function over integer total runs.

    Frozen and validated on construction. An unusable distribution raises here
    rather than producing a probability that looks fine, because the failure
    mode this prevents -- a distribution missing its tail, silently renormalised
    into a confident-looking Over number -- is invisible downstream.
    """

    #: total runs -> probability as a FRACTION in [0, 1]
    mass: dict[int, float]
    #: How many simulated games the mass function counts.
    simulation_count: int | None
    #: The forecast this came from, carried so a price can name its evidence.
    forecast_id: str
    engine_id: str
    engine_version: str | None

    @property
    def expected_total_runs(self) -> float:
        return sum(runs * probability for runs, probability in self.mass.items())

    @property
    def support(self) -> tuple[int, int]:
        keys = self.mass.keys()
        return min(keys), max(keys)

    @classmethod
    def from_stored(
        cls,
        raw: Any,
        *,
        forecast_id: str,
        engine_id: str,
        engine_version: str | None = None,
        simulation_count: int | None = None,
    ) -> TotalsDistribution:
        """Build from ``distributions.totalRunDistribution`` as persisted."""

        if not isinstance(raw, dict) or not raw:
            raise TotalsPricingError(
                "no total-runs distribution is stored for this forecast"
            )
        mass: dict[int, float] = {}
        for key, value in raw.items():
            try:
                runs = int(key)
                probability = float(value)
            except (TypeError, ValueError) as exc:
                raise TotalsPricingError(
                    f"total-runs distribution has a non-numeric entry {key!r}: {value!r}"
                ) from exc
            if runs < 0:
                raise TotalsPricingError(f"total runs cannot be negative, got {runs}")
            if not math.isfinite(probability) or probability < 0:
                raise TotalsPricingError(
                    f"probability for {runs} runs is not a usable mass: {probability!r}"
                )
            mass[runs] = mass.get(runs, 0.0) + probability
        total = sum(mass.values())
        if abs(total - 1.0) > MASS_TOLERANCE:
            # Not renormalised. A mass function that does not sum to one is
            # either truncated or double-counted, and scaling it would hide
            # which, while still reporting a probability.
            raise TotalsPricingError(
                f"total-runs distribution sums to {total:.8f}, not 1; it is "
                "truncated or double-counted and cannot price a line"
            )
        return cls(
            mass=mass,
            simulation_count=simulation_count,
            forecast_id=forecast_id,
            engine_id=engine_id,
            engine_version=engine_version,
        )


@dataclass(frozen=True)
class TotalsProbabilities:
    """Over / Under / Push at one line. Percent units, stated on the object."""

    line: float
    over_pct: float
    under_pct: float
    push_pct: float
    #: True when the line is a whole number, so a push is reachable at all.
    push_possible: bool
    probability_units: str = PROBABILITY_UNITS

    def as_dict(self) -> dict[str, Any]:
        return {
            "line": self.line,
            "over_probability_pct": self.over_pct,
            "under_probability_pct": self.under_pct,
            "push_probability_pct": self.push_pct,
            "push_possible": self.push_possible,
            "probability_units": self.probability_units,
        }

    def fraction(self, side: str) -> tuple[float, float, float]:
        """(win, push, loss) as fractions for one side, for money arithmetic."""

        upper = side.upper()
        if upper == "OVER":
            return self.over_pct / 100.0, self.push_pct / 100.0, self.under_pct / 100.0
        if upper == "UNDER":
            return self.under_pct / 100.0, self.push_pct / 100.0, self.over_pct / 100.0
        raise TotalsPricingError(f"side must be OVER or UNDER, got {side!r}")


def price_line(distribution: TotalsDistribution, line: float) -> TotalsProbabilities:
    """Over, Under and Push at ``line``, read straight off the mass function.

    No interpolation and no continuity correction. Total runs are integers and
    the line is either an integer or a half, so every case is decided exactly by
    comparison -- there is nothing to smooth, and smoothing would invent mass
    between values the game cannot produce.
    """

    value = float(line)
    if not math.isfinite(value):
        raise TotalsPricingError(f"line must be a finite number, got {line!r}")
    if value < 0:
        raise TotalsPricingError(f"line cannot be negative, got {line!r}")

    push_possible = float(value).is_integer()
    over = under = push = 0.0
    for runs, probability in distribution.mass.items():
        if runs > value:
            over += probability
        elif runs < value:
            under += probability
        else:
            push += probability

    return TotalsProbabilities(
        line=value,
        over_pct=round(over * 100.0, PROBABILITY_DECIMALS),
        under_pct=round(under * 100.0, PROBABILITY_DECIMALS),
        push_pct=round(push * 100.0, PROBABILITY_DECIMALS),
        push_possible=push_possible,
    )


def expected_net_return(p_win: float, p_loss: float, decimal_odds: float) -> float:
    """Model-implied net return per unit staked. Fractions in, fraction out.

    ``p_win * (d - 1) - p_loss``. The push term is absent because a push returns
    the stake: it contributes nothing to net return, and adding it as either a
    win or a loss is the most common way this number goes wrong.
    """

    if decimal_odds is None or not math.isfinite(decimal_odds) or decimal_odds <= 1.0:
        raise TotalsPricingError(
            f"decimal odds must be greater than 1 to price a return, got {decimal_odds!r}"
        )
    for name, value in (("p_win", p_win), ("p_loss", p_loss)):
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise TotalsPricingError(f"{name} must be a fraction in [0, 1], got {value!r}")
    return p_win * (decimal_odds - 1.0) - p_loss


def fair_decimal_odds(p_win: float, p_push: float = 0.0) -> float | None:
    """The break-even price for a side, pushes taken out of the pool.

    A push returns the stake, so it is neither staked nor at risk in the sense
    that matters for a fair price: the fair odds are on the conditional
    win-given-a-decision. Returning None rather than infinity when a side cannot
    win keeps an unpriceable case out of arithmetic downstream.
    """

    decided = 1.0 - p_push
    if decided <= 0:
        return None
    conditional = p_win / decided
    if conditional <= 0:
        return None
    return 1.0 / conditional


def american_from_decimal(decimal_odds: float | None) -> int | None:
    if decimal_odds is None or not math.isfinite(decimal_odds) or decimal_odds <= 1.0:
        return None
    if decimal_odds >= 2.0:
        return int(round((decimal_odds - 1.0) * 100))
    return int(round(-100.0 / (decimal_odds - 1.0)))


__all__ = [
    "MASS_TOLERANCE",
    "PROBABILITY_DECIMALS",
    "PROBABILITY_UNITS",
    "TotalsDistribution",
    "TotalsPricingError",
    "TotalsProbabilities",
    "american_from_decimal",
    "expected_net_return",
    "fair_decimal_odds",
    "price_line",
]

"""One semantic rule for whether a sportsbook probability exists at all.

THE RULE
--------
A sportsbook probability is available only when a legitimate, complete,
two-sided sportsbook observation exists AND the normalized probability derived
from it lies strictly inside (0, 1). Anything else is absence, and absence is
reported as absence -- never as 0, never as an edge input, never as a consensus
member, never as a market average.

WHY IT IS ONE FUNCTION
----------------------
The failure this replaces was a dictionary that returned ``0.0`` for "no
two-sided quote" beside a separate ``has_moneyline`` flag. Every consumer that
remembered to read the flag was correct; the one that read the number alone
rendered a confident "Sportsbook 0.0%" for a game with zero reporting books.
Making absence representable as ``None`` removes the class of bug rather than
the instance.

SCOPE
-----
This is deliberately NOT a global "zero means missing" rule. It applies to
probability fields whose valid domain is the open interval (0, 1) and which
require supporting quote evidence. A genuine zero elsewhere -- zero reporting
books, zero virtual stake, zero provider calls -- is a real measurement and is
displayed as such.
"""

from __future__ import annotations

from collections.abc import Iterable

#: Text used wherever an absent sportsbook probability is surfaced to a reader.
UNAVAILABLE_LABEL = "Not available"


def is_valid_probability(value: object) -> bool:
    """True only for a real number strictly inside (0, 1)."""

    # Deliberately strict about type as well as range. Every persisted
    # probability is a Float column; a string that merely looks numeric is a
    # contract violation upstream, not a probability to be salvaged here.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    # NaN fails every comparison, which is the behaviour we want here.
    return 0.0 < float(value) < 1.0


def observed_probability(value: object, *, has_two_sided_quote: bool) -> float | None:
    """The sportsbook probability, or None when the rule is not satisfied.

    ``has_two_sided_quote`` is the evidence half of the rule: a probability
    computed from a one-sided or absent quote is not an observation at all,
    however plausible the number looks.
    """

    if not has_two_sided_quote:
        return None
    return float(value) if is_valid_probability(value) else None  # type: ignore[arg-type]


def average_probability(values: Iterable[object]) -> float | None:
    """Mean of the valid probabilities only, or None when none qualify.

    An invalid member is excluded from the average rather than counted as zero,
    so one missing book can never drag a consensus toward 0.
    """

    valid = [float(item) for item in values if is_valid_probability(item)]  # type: ignore[arg-type]
    return sum(valid) / len(valid) if valid else None


def model_edge(
    model_probability: float | None,
    market_probability: float | None,
) -> float | None:
    """Model minus market, only when BOTH sides genuinely exist.

    Returns None rather than an edge computed against a fabricated market. An
    edge is a comparison; with one side missing there is nothing to compare.
    """

    if model_probability is None or not is_valid_probability(market_probability):
        return None
    if not is_valid_probability(model_probability):
        return None
    return float(model_probability) - float(market_probability)  # type: ignore[arg-type]


__all__ = [
    "UNAVAILABLE_LABEL",
    "average_probability",
    "is_valid_probability",
    "model_edge",
    "observed_probability",
]

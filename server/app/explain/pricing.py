"""The three probabilities a wager explanation must never let blur together.

There are three different numbers in every moneyline decision, and a reader who
conflates any two of them will misread the whole thing:

``model``       what AISP thinks the selection's chance is.
``break_even``  what the OFFERED price requires before the bet is worth making.
``no_vig``      what the market thinks, with the bookmaker's margin removed.

The distinction that matters most is between the first two. A selection can be
unlikely to win and still be worth backing: Kansas City at +195 needed only
33.90% to break even, and the model gave it 36.46% after adjustment. That is a
genuine reason to take the price and NOT a claim that Kansas City was the likely
winner. Saying "we liked Kansas City" would be false; saying "the price was
generous relative to our estimate" is the actual argument.

The third is a separate question again. Beating the break-even is about whether
the bet pays; disagreeing with the no-vig market is about whether AISP sees
something the market does not. The stored policy gates on one of them, and the
explanation says which rather than implying both were required.

Everything here is computed at full precision. Rounding happens once, at the
display boundary, because rounding early and then comparing is how a 33.8983%
break-even and a 36.46% estimate turn into a claim about "36% versus 34%" that
is off by enough to matter on the margin.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


def decimal_from_american(american: int | None) -> float | None:
    """Decimal odds from an American price, or None when it is not a price."""

    if american is None or american == 0:
        return None
    if -100 < american < 100:
        # American odds do not exist in this band; refuse rather than coerce.
        return None
    if american > 0:
        return 1.0 + american / 100.0
    return 1.0 + 100.0 / abs(american)


def break_even_probability(decimal_odds: float | None) -> float | None:
    """The chance the OFFERED price requires to be worth taking.

    ``1 / decimal_odds``. This is the price's own requirement and has nothing to
    do with what the market collectively believes -- a book can offer a price
    whose break-even sits well below the no-vig consensus, which is exactly the
    situation a value selection is looking for.
    """

    if decimal_odds is None or not math.isfinite(decimal_odds) or decimal_odds <= 1.0:
        return None
    return 1.0 / decimal_odds


def expected_net_return(p_win: float, p_loss: float, decimal_odds: float) -> float:
    """``p_win * (d - 1) - p_loss`` per unit staked. A push contributes nothing."""

    return p_win * (decimal_odds - 1.0) - p_loss


@dataclass(frozen=True)
class PriceComparison:
    """One selection's model estimate set against the two market quantities."""

    american_odds: int | None
    decimal_odds: float | None
    #: The model's probability after any recorded adjustment, as a fraction.
    model_probability: float | None
    #: Before adjustment, when the decision recorded both.
    raw_model_probability: float | None
    break_even: float | None
    no_vig_probability: float | None

    @property
    def clears_break_even(self) -> bool | None:
        if self.model_probability is None or self.break_even is None:
            return None
        return self.model_probability > self.break_even

    @property
    def margin_over_break_even(self) -> float | None:
        """Model probability minus what the price requires. Full precision."""

        if self.model_probability is None or self.break_even is None:
            return None
        return self.model_probability - self.break_even

    @property
    def disagreement_with_market(self) -> float | None:
        """Model probability minus the no-vig market probability."""

        if self.model_probability is None or self.no_vig_probability is None:
            return None
        return self.model_probability - self.no_vig_probability

    @property
    def model_makes_it_favoured(self) -> bool | None:
        """Whether the MODEL thinks this selection is more likely than not.

        Deliberately separate from everything else on this object. It is the
        only field that speaks to "likely to win", and it is false for most
        value selections.
        """

        if self.model_probability is None:
            return None
        return self.model_probability > 0.5

    @property
    def priced_as_underdog(self) -> bool | None:
        """Whether the BOOK priced it as an underdog."""

        if self.american_odds is None:
            return None
        return self.american_odds > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "american_odds": self.american_odds,
            "decimal_odds": _round(self.decimal_odds, 4),
            "model_probability_pct": _pct(self.model_probability),
            "raw_model_probability_pct": _pct(self.raw_model_probability),
            "break_even_probability_pct": _pct(self.break_even),
            "no_vig_probability_pct": _pct(self.no_vig_probability),
            "margin_over_break_even_pts": _pts(self.margin_over_break_even),
            "disagreement_with_market_pts": _pts(self.disagreement_with_market),
            "clears_break_even": self.clears_break_even,
            "model_makes_it_favoured": self.model_makes_it_favoured,
            "priced_as_underdog": self.priced_as_underdog,
            "probability_units": "PERCENT",
            "definitions": {
                "break_even_probability_pct": (
                    "What the offered price itself requires before the wager is "
                    "worth making. Not a forecast and not the market's opinion."
                ),
                "no_vig_probability_pct": (
                    "What the market implies once the bookmaker's margin is "
                    "removed. Disagreeing with it is a different claim from "
                    "beating the price."
                ),
                "model_makes_it_favoured": (
                    "Whether the model makes this selection more likely than not "
                    "to win. Often false for a selection taken on price."
                ),
            },
        }


def compare(
    *,
    american_odds: int | None,
    decimal_odds: float | None,
    model_probability: float | None,
    raw_model_probability: float | None = None,
    no_vig_probability: float | None = None,
) -> PriceComparison:
    """Build the comparison, deriving decimal odds only when they are absent."""

    resolved = decimal_odds if decimal_odds else decimal_from_american(american_odds)
    return PriceComparison(
        american_odds=american_odds,
        decimal_odds=resolved,
        model_probability=model_probability,
        raw_model_probability=raw_model_probability,
        break_even=break_even_probability(resolved),
        no_vig_probability=no_vig_probability,
    )


def _pct(value: float | None, places: int = 2) -> float | None:
    """Fraction -> percent, rounded once, at the display boundary."""

    return None if value is None else round(value * 100.0, places)


def _pts(value: float | None, places: int = 2) -> float | None:
    """A difference of two fractions, shown in percentage points."""

    return None if value is None else round(value * 100.0, places)


def _round(value: float | None, places: int) -> float | None:
    return None if value is None else round(value, places)


__all__ = [
    "PriceComparison",
    "break_even_probability",
    "compare",
    "decimal_from_american",
    "expected_net_return",
]

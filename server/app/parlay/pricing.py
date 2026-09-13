"""What a multi-leg ticket is worth, with pushes kept and independence declared.

THE APPROXIMATION, STATED ONCE
------------------------------
Legs from different games are combined as if independent. That is an
approximation, not a property AISP has measured. Real slates share weather
fronts, umpires, and league-wide scoring conditions, and a slate of Overs is
correlated in a way this arithmetic does not see.

What is NOT done, and is worth naming because it would look rigorous: pairing
simulation index *i* of one game with index *i* of another and calling the
result a joint slate simulation. Those indices share nothing but their position
in a list. The pairing would be arbitrary, and averaging over it would dress the
same independence assumption up as a simulation result.

So: independence is assumed, the payload says so on every ticket, and nothing
here claims a measured joint distribution.

PUSHES ARE NOT LOSSES, AND "WINS" IS NOT "PAYS"
-----------------------------------------------
A pushed leg is removed and the ticket reprices over the legs that remain. Two
different questions follow, and a page that reports one as the other is
misleading in the direction that flatters the ticket:

*   **Every leg wins** -- product of the per-leg win probabilities.
*   **The ticket pays more than the stake** -- no leg loses, and at least one
    wins. Strictly larger than the first whenever any leg can push.

Both are computed and both are labelled. A third, **stake returned**, covers the
case where every leg pushes and the ticket is a scratch.

THE ARITHMETIC
--------------
Give each leg a payout multiple: ``d`` if it wins, ``1`` if it pushes (it drops
out of the ticket), ``0`` if it loses (the ticket is dead). The ticket's multiple
is the product. Under independence the expectation factorises exactly:

    E[multiple] = prod_i (p_win_i * d_i + p_push_i)

so the expected net return per unit stake is ``E[multiple] - 1``. That is exact
given independence -- no enumeration, no simulation, and no Monte Carlo error of
its own.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from app.totals.pricing import PROBABILITY_UNITS, fair_decimal_odds

#: How a combined price was arrived at. These are three different objects and
#: the distinction survives into settlement and performance reporting.
PRICE_RECORDED_COMBINED = "RECORDED_SPORTSBOOK_COMBINED_QUOTE"
PRICE_ESTIMATED_FROM_LEGS = "ESTIMATED_FROM_SAME_BOOK_LEG_ODDS"
PRICE_MODEL_FAIR = "MODEL_FAIR_ODDS"

INDEPENDENCE_NOTE = (
    "Legs are combined as if the games were independent. AISP has not measured "
    "cross-game correlation, and shared conditions (weather, umpires, league-wide "
    "scoring) would move same-side legs together. Treat the combined figures as "
    "an approximation."
)

ESTIMATED_PRICE_NOTE = (
    "No sportsbook parlay quote is recorded for this combination. The combined "
    "price is the product of that book's individual leg prices. Books apply their "
    "own parlay rules and may not offer this price, so this is a research "
    "estimate, not an available offer."
)


class ParlayPricingError(ValueError):
    """A ticket cannot be priced from the evidence given."""


@dataclass(frozen=True)
class Leg:
    """One selection on a ticket, with its own probabilities and price.

    Probabilities are FRACTIONS here. The percent-facing boundary is the read
    service; arithmetic stays in fractions so nothing has to remember which
    scale it is on mid-multiplication.
    """

    game_id: str
    market: str          # MONEYLINE | TOTAL
    selection: str       # e.g. "NYY ML", "OVER 8.5"
    p_win: float
    p_push: float
    p_loss: float
    decimal_odds: float
    sportsbook: str
    #: Which forecast produced p_win. A leg with no named forecast is refused.
    forecast_id: str
    engine_id: str
    #: When the price was observed, and when the game starts.
    quote_observed_at: Any = None
    starts_at: Any = None
    line: float | None = None
    american_odds: int | None = None
    odds_snapshot_id: str | None = None
    market_outcome_id: str | None = None

    def __post_init__(self) -> None:
        total = self.p_win + self.p_push + self.p_loss
        if abs(total - 1.0) > 1e-6:
            raise ParlayPricingError(
                f"leg {self.selection!r} probabilities sum to {total:.8f}, not 1"
            )
        for name in ("p_win", "p_push", "p_loss"):
            value = getattr(self, name)
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ParlayPricingError(
                    f"leg {self.selection!r} {name} is not a fraction: {value!r}"
                )
        if not math.isfinite(self.decimal_odds) or self.decimal_odds <= 1.0:
            raise ParlayPricingError(
                f"leg {self.selection!r} decimal odds must exceed 1, got {self.decimal_odds!r}"
            )
        if not self.forecast_id:
            raise ParlayPricingError(
                f"leg {self.selection!r} names no forecast; an unattributed "
                "probability is not usable evidence"
            )

    @property
    def push_possible(self) -> bool:
        return self.p_push > 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "game_id": self.game_id,
            "market": self.market,
            "selection": self.selection,
            "line": self.line,
            "win_probability_pct": round(self.p_win * 100.0, 4),
            "push_probability_pct": round(self.p_push * 100.0, 4),
            "loss_probability_pct": round(self.p_loss * 100.0, 4),
            "probability_units": PROBABILITY_UNITS,
            "decimal_odds": round(self.decimal_odds, 4),
            "american_odds": self.american_odds,
            "sportsbook": self.sportsbook,
            "forecast_id": self.forecast_id,
            "engine_id": self.engine_id,
            "quote_observed_at": self.quote_observed_at,
            "starts_at": self.starts_at,
            "odds_snapshot_id": self.odds_snapshot_id,
            "market_outcome_id": self.market_outcome_id,
            "push_possible": self.push_possible,
        }


@dataclass(frozen=True)
class TicketPrice:
    """Every number a ticket carries, each one labelled with what it is."""

    leg_count: int
    #: Product of leg win probabilities: every leg wins outright.
    all_legs_win_pct: float
    #: No leg loses AND at least one wins: the ticket returns more than stake.
    ticket_pays_pct: float
    #: Every leg pushes: the stake comes back and nothing is won.
    stake_returned_pct: float
    #: At least one leg loses: the ticket is dead.
    ticket_loses_pct: float
    #: Product of the same book's leg prices. An estimate unless a real
    #: combined quote was recorded.
    combined_decimal_odds: float
    price_provenance: str
    #: Model fair price, from the push-adjusted per-leg fair odds.
    model_fair_decimal_odds: float | None
    #: E[payout multiple] - 1, per unit staked. Pushes handled exactly.
    expected_net_return_per_unit: float
    #: What one unit returns if every leg wins, stake included.
    payout_multiple_if_all_win: float
    push_possible: bool
    probability_units: str = PROBABILITY_UNITS
    independence_note: str = INDEPENDENCE_NOTE

    def as_dict(self) -> dict[str, Any]:
        return {
            "leg_count": self.leg_count,
            "all_legs_win_probability_pct": self.all_legs_win_pct,
            "ticket_pays_probability_pct": self.ticket_pays_pct,
            "stake_returned_probability_pct": self.stake_returned_pct,
            "ticket_loses_probability_pct": self.ticket_loses_pct,
            "probability_units": self.probability_units,
            "combined_decimal_odds": self.combined_decimal_odds,
            "price_provenance": self.price_provenance,
            "model_fair_decimal_odds": self.model_fair_decimal_odds,
            "estimated_net_return_per_unit": self.expected_net_return_per_unit,
            "payout_multiple_if_all_win": self.payout_multiple_if_all_win,
            "push_possible": self.push_possible,
            "independence_note": self.independence_note,
            "probability_definitions": {
                "all_legs_win_probability_pct":
                    "Every leg wins outright. No leg pushes.",
                "ticket_pays_probability_pct":
                    "No leg loses and at least one wins, so the ticket returns "
                    "more than the stake. A pushed leg is removed and the ticket "
                    "reprices over the rest.",
                "stake_returned_probability_pct":
                    "Every leg pushes. The stake is returned and nothing is won.",
            },
        }


def price_ticket(
    legs: list[Leg],
    *,
    recorded_combined_decimal_odds: float | None = None,
) -> TicketPrice:
    """Price one ticket. Pushes are preserved; independence is assumed."""

    if len(legs) < 2:
        raise ParlayPricingError("a ticket needs at least two legs")
    games = [leg.game_id for leg in legs]
    if len(set(games)) != len(games):
        # Two selections from one game are correlated by construction -- an
        # Over and that game's favourite are the same event seen twice -- and
        # the independent product would be simply wrong rather than approximate.
        raise ParlayPricingError(
            "a ticket may not take two selections from the same game in this release"
        )
    books = {leg.sportsbook for leg in legs}
    if len(books) != 1:
        raise ParlayPricingError(
            f"a priced ticket must use one sportsbook; got {sorted(books)}"
        )

    all_win = 1.0
    no_loss = 1.0
    all_push = 1.0
    expected_multiple = 1.0
    estimated_odds = 1.0
    fair_product: float | None = 1.0
    for leg in legs:
        all_win *= leg.p_win
        no_loss *= leg.p_win + leg.p_push
        all_push *= leg.p_push
        # d if it wins, 1 if it pushes (it drops out), 0 if it loses.
        expected_multiple *= leg.p_win * leg.decimal_odds + leg.p_push
        estimated_odds *= leg.decimal_odds
        leg_fair = fair_decimal_odds(leg.p_win, leg.p_push)
        if leg_fair is None:
            fair_product = None
        elif fair_product is not None:
            fair_product *= leg_fair

    combined = (
        recorded_combined_decimal_odds
        if recorded_combined_decimal_odds is not None else estimated_odds
    )
    provenance = (
        PRICE_RECORDED_COMBINED if recorded_combined_decimal_odds is not None
        else PRICE_ESTIMATED_FROM_LEGS
    )
    if recorded_combined_decimal_odds is not None:
        # The recorded quote is the price of the ticket as offered. The expected
        # return has to be computed at THAT price, not at the product of the
        # legs, or the headline number describes a ticket nobody is offering.
        scale = recorded_combined_decimal_odds / estimated_odds
        expected_multiple *= scale

    return TicketPrice(
        leg_count=len(legs),
        all_legs_win_pct=round(all_win * 100.0, 4),
        ticket_pays_pct=round((no_loss - all_push) * 100.0, 4),
        stake_returned_pct=round(all_push * 100.0, 4),
        ticket_loses_pct=round((1.0 - no_loss) * 100.0, 4),
        combined_decimal_odds=round(combined, 4),
        price_provenance=provenance,
        model_fair_decimal_odds=(
            round(fair_product, 4) if fair_product is not None else None),
        expected_net_return_per_unit=round(expected_multiple - 1.0, 6),
        payout_multiple_if_all_win=round(combined, 4),
        push_possible=any(leg.push_possible for leg in legs),
    )


__all__ = [
    "ESTIMATED_PRICE_NOTE",
    "INDEPENDENCE_NOTE",
    "PRICE_ESTIMATED_FROM_LEGS",
    "PRICE_MODEL_FAIR",
    "PRICE_RECORDED_COMBINED",
    "Leg",
    "ParlayPricingError",
    "TicketPrice",
    "price_ticket",
]

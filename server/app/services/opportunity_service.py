"""Governed daily-opportunity pricing for the public Today board.

This module contains only *pure* pricing/selection logic. It never touches the
database, the network, or a provider. Every governed number is produced by the
existing shadow-pricing policy (``app.forecast_exchange.pricing.price_selection``)
and the existing virtual-bankroll math (``app.bankroll.service.calculate_stake``);
nothing is duplicated or re-derived here. A selection is priced only when a real,
complete, fresh two-sided governed quote exists — otherwise it is ``NO_BET`` with
a zero virtual stake. Real-money execution is impossible by construction.

``ReadService`` adapts persisted ORM rows into the plain inputs below, calls the
functions here, and maps the results onto the API schema.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from statistics import fmean

from app.bankroll.service import StakePolicy, calculate_stake
from app.forecast_exchange.pricing import (
    ModelProbability,
    Recommendation,
    ShadowPricingConfig,
    american_to_decimal,
    expected_value,
    implied_probability,
    no_vig_two_sided,
    price_selection,
)
from app.markets.odds import probability_to_american


# The public Today board refreshes odds on a minute cadence; a governed quote is
# only actionable while fresh. This mirrors the shadow board's staleness policy.
MAX_QUOTE_STALENESS_MINUTES = 240

_REC_RANK = {
    Recommendation.QUALIFIED_BET.value: 3,
    Recommendation.WATCH.value: 2,
    Recommendation.NO_BET.value: 1,
}


@dataclass(frozen=True)
class BookQuote:
    """One book's real, complete two-sided price for a single selection."""

    book: str
    side_american: int
    opposite_american: int
    observed_at: datetime


@dataclass(frozen=True)
class SideInput:
    """A single bettable selection with its model probability and live quotes."""

    selection: str
    line: float | None
    model_probability: float | None
    conservative_probability: float | None
    quotes: tuple[BookQuote, ...]


@dataclass(frozen=True)
class MarketInput:
    """A market (moneyline / run line / total) with its two sides."""

    market_label: str
    period: str
    sides: tuple[SideInput, ...]


@dataclass
class Candidate:
    """A fully priced selection ready to surface. Mutated only to assign stake."""

    market_label: str
    period: str
    selection: str
    line: float | None
    recommendation: str
    model_probability: float | None
    no_vig_probability: float | None
    consensus_probability: float | None
    raw_edge: float | None
    uncertainty_adjusted_edge: float | None
    expected_value: float | None
    uncertainty_adjusted_ev: float | None
    fair_odds: int | None
    american_odds: int | None
    decimal_odds: float | None
    book: str | None
    observed_at: datetime | None
    quote_age_seconds: int | None
    is_stale: bool
    is_ready: bool
    virtual_stake: float = 0.0
    potential_profit: float = 0.0
    total_return: float = 0.0


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _is_stale(observed_at: datetime, now: datetime, max_staleness_minutes: int) -> bool:
    return _aware(observed_at) < now - timedelta(minutes=max_staleness_minutes)


def _consensus_no_vig(quotes: tuple[BookQuote, ...]) -> float | None:
    values = [
        no_vig_two_sided(
            implied_probability(quote.side_american),
            implied_probability(quote.opposite_american),
        )
        for quote in quotes
    ]
    return fmean(values) if values else None


def _sort_key(candidate: Candidate) -> tuple[int, float, float]:
    return (
        _REC_RANK.get(candidate.recommendation, 0),
        candidate.uncertainty_adjusted_ev if candidate.uncertainty_adjusted_ev is not None else float("-inf"),
        _aware(candidate.observed_at).timestamp() if candidate.observed_at else 0.0,
    )


def price_side(
    side: SideInput,
    *,
    market_label: str,
    period: str,
    is_ready: bool,
    config: ShadowPricingConfig,
    now: datetime,
    max_staleness_minutes: int = MAX_QUOTE_STALENESS_MINUTES,
) -> Candidate | None:
    """Price one selection against its best real two-sided quote.

    Returns ``None`` when no model probability or no complete quote exists — the
    honest "no governed price" case, which the caller renders as missing.
    """

    if side.model_probability is None or not side.quotes:
        return None

    # The best book for this side maximizes the bettor's decimal return; its own
    # paired opposite forms the complete two-sided quote the policy prices from.
    best = max(side.quotes, key=lambda quote: american_to_decimal(quote.side_american))
    conservative = (
        side.conservative_probability
        if side.conservative_probability is not None
        else side.model_probability
    )
    priced = price_selection(
        model=ModelProbability(side.model_probability, conservative, None),
        side_american=best.side_american,
        opposite_american=best.opposite_american,
        best_american=best.side_american,
        is_ready=is_ready,
        config=config,
    )

    stale = _is_stale(best.observed_at, now, max_staleness_minutes)
    recommendation = Recommendation.NO_BET.value if stale else priced.recommendation.value
    uae_ev = expected_value(conservative, priced.best_decimal_odds)
    fair_odds = (
        probability_to_american(side.model_probability)
        if 0.0 < side.model_probability < 1.0
        else None
    )
    observed = _aware(best.observed_at)
    return Candidate(
        market_label=market_label,
        period=period,
        selection=side.selection,
        line=side.line,
        recommendation=recommendation,
        model_probability=side.model_probability,
        no_vig_probability=priced.no_vig_probability,
        consensus_probability=_consensus_no_vig(side.quotes),
        raw_edge=priced.raw_edge,
        uncertainty_adjusted_edge=priced.uncertainty_adjusted_edge,
        expected_value=priced.expected_value,
        uncertainty_adjusted_ev=uae_ev,
        fair_odds=fair_odds,
        american_odds=priced.best_american_odds,
        decimal_odds=priced.best_decimal_odds,
        book=best.book,
        observed_at=observed,
        quote_age_seconds=max(0, int((now - observed).total_seconds())),
        is_stale=stale,
        is_ready=is_ready,
    )


def build_market_candidate(
    market: MarketInput,
    *,
    is_ready: bool,
    config: ShadowPricingConfig,
    now: datetime,
    max_staleness_minutes: int = MAX_QUOTE_STALENESS_MINUTES,
) -> Candidate | None:
    """Return the single better side of a market (the exact recommended bet)."""

    priced = [
        candidate
        for side in market.sides
        if (
            candidate := price_side(
                side,
                market_label=market.market_label,
                period=market.period,
                is_ready=is_ready,
                config=config,
                now=now,
                max_staleness_minutes=max_staleness_minutes,
            )
        )
        is not None
    ]
    if not priced:
        return None
    return max(priced, key=_sort_key)


def rank_candidates(candidates: list[Candidate]) -> list[Candidate]:
    """Order a game's market candidates: best recommendation, then adjusted EV."""

    return sorted(candidates, key=_sort_key, reverse=True)


def allocate_stakes(
    qualified: list[tuple[str, Candidate]],
    *,
    bankroll: float,
    policy: StakePolicy,
    same_game_cap: float,
    daily_cap: float,
) -> float:
    """Assign governed virtual stakes across the slate under exposure caps.

    Mirrors ``ShadowBoardService.evaluate_board``: quarter-Kelly sizing capped at
    the per-position, same-game, and daily limits. Mutates each candidate with
    its stake/profit/return and returns the total virtual daily exposure.
    """

    per_game: dict[str, float] = defaultdict(float)
    daily = 0.0
    ordered = sorted(
        qualified,
        key=lambda item: (
            item[1].uncertainty_adjusted_ev if item[1].uncertainty_adjusted_ev is not None else float("-inf"),
            _aware(item[1].observed_at).timestamp() if item[1].observed_at else 0.0,
        ),
        reverse=True,
    )
    for game_id, candidate in ordered:
        if candidate.model_probability is None or candidate.decimal_odds is None:
            continue
        raw = calculate_stake(
            bankroll,
            policy,
            model_probability=candidate.model_probability,
            decimal_odds=candidate.decimal_odds,
        )
        remaining_game = max(0.0, same_game_cap - per_game[game_id])
        remaining_day = max(0.0, daily_cap - daily)
        stake = round(max(0.0, min(raw, remaining_game, remaining_day)), 2)
        candidate.virtual_stake = stake
        candidate.potential_profit = round(stake * (candidate.decimal_odds - 1.0), 2)
        candidate.total_return = round(stake * candidate.decimal_odds, 2)
        per_game[game_id] += stake
        daily += stake
    return round(daily, 2)


__all__ = [
    "MAX_QUOTE_STALENESS_MINUTES",
    "BookQuote",
    "SideInput",
    "MarketInput",
    "Candidate",
    "price_side",
    "build_market_candidate",
    "rank_candidates",
    "allocate_stakes",
]

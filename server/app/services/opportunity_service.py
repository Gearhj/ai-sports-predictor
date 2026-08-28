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

# An opportunity whose price clears the qualified bar on merit but whose critical
# configured inputs (readiness, lineups, starters) are incomplete is a governed
# PROVISIONAL_EDGE — never a QUALIFIED_BET. A very large model–market gap is a
# governed EXTREME_DISAGREEMENT — REVIEW REQUIRED that can never be QUALIFIED
# without confirmed inputs, a fresh quote, and a successful rescore. Both labels
# live only in the public opportunity layer; the shadow-pricing enum is untouched.
PROVISIONAL_EDGE = "PROVISIONAL_EDGE"
EXTREME_DISAGREEMENT = "EXTREME_DISAGREEMENT"

_REC_RANK = {
    Recommendation.QUALIFIED_BET.value: 5,
    PROVISIONAL_EDGE: 4,
    EXTREME_DISAGREEMENT: 3,
    Recommendation.WATCH.value: 2,
    Recommendation.NO_BET.value: 1,
}

# Tiered Opportunity Score: the governed tier sets the band, quality (0..1)
# differentiates within it. Ranking by this single score therefore respects the
# recommendation tier first, then blends the value/reliability signals — never
# raw edge alone. Bands are gapped so a tier is never outranked by a lower one.
# EXTREME_DISAGREEMENT sits below actionable tiers: a flagged review item is not
# a top pick, but its real signal ranks above a plain WATCH.
_TIER_BASE = {
    Recommendation.QUALIFIED_BET.value: 75.0,
    PROVISIONAL_EDGE: 52.0,
    EXTREME_DISAGREEMENT: 44.0,
    Recommendation.WATCH.value: 28.0,
    Recommendation.NO_BET.value: 6.0,
}


@dataclass(frozen=True)
class BookQuote:
    """One book's real, complete two-sided price for a single selection."""

    book: str
    side_american: int
    opposite_american: int
    observed_at: datetime
    sportsbook_id: str | None = None
    market_outcome_id: str | None = None
    odds_snapshot_id: str | None = None


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
    sportsbook_id: str | None
    market_outcome_id: str | None
    odds_snapshot_id: str | None
    observed_at: datetime | None
    quote_age_seconds: int | None
    is_stale: bool
    is_ready: bool
    extreme: bool = False
    virtual_stake: float = 0.0
    potential_profit: float = 0.0
    total_return: float = 0.0
    opportunity_score: float = 0.0


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
    readiness_ok: bool,
    critical_inputs_ok: bool,
    config: ShadowPricingConfig,
    now: datetime,
    extreme_threshold: float = 1.0,
    max_staleness_minutes: int = MAX_QUOTE_STALENESS_MINUTES,
) -> Candidate | None:
    """Price one selection against its best real two-sided quote.

    Returns ``None`` when no model probability or no complete quote exists — the
    honest "no governed price" case, which the caller renders as missing.

    The governed edge/EV bar is evaluated on merit (``is_ready=True``); a merit
    pass is then labelled QUALIFIED_BET only when both the readiness policy and
    the critical configured inputs pass. When the price is attractive but those
    inputs are incomplete it is a PROVISIONAL_EDGE, never a qualified bet.
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
        is_ready=True,
        config=config,
    )

    stale = _is_stale(best.observed_at, now, max_staleness_minutes)
    fully_ready = readiness_ok and critical_inputs_ok
    # A merit-qualified price is QUALIFIED only when every critical input is
    # confirmed AND the quote is fresh; otherwise it is a PROVISIONAL_EDGE. A
    # stale/unconfirmed input never becomes NO_BET when the edge is real.
    if priced.recommendation == Recommendation.QUALIFIED_BET:
        recommendation = (
            Recommendation.QUALIFIED_BET.value
            if (fully_ready and not stale)
            else PROVISIONAL_EDGE
        )
    else:
        recommendation = priced.recommendation.value
    extreme = priced.no_vig_edge >= extreme_threshold
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
        sportsbook_id=best.sportsbook_id,
        market_outcome_id=best.market_outcome_id,
        odds_snapshot_id=best.odds_snapshot_id,
        observed_at=observed,
        quote_age_seconds=max(0, int((now - observed).total_seconds())),
        is_stale=stale,
        is_ready=fully_ready and not stale,
        extreme=extreme,
    )


def build_market_candidate(
    market: MarketInput,
    *,
    readiness_ok: bool,
    critical_inputs_ok: bool,
    config: ShadowPricingConfig,
    now: datetime,
    extreme_threshold: float = 1.0,
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
                readiness_ok=readiness_ok,
                critical_inputs_ok=critical_inputs_ok,
                config=config,
                now=now,
                extreme_threshold=extreme_threshold,
                max_staleness_minutes=max_staleness_minutes,
            )
        )
        is not None
    ]
    if not priced:
        return None
    return max(priced, key=_sort_key)


def compute_opportunity_score(
    *,
    recommendation: str,
    uncertainty_adjusted_ev: float | None,
    readiness: float | None,
    quote_age_seconds: int | None,
    forecast_uncertainty: float | None,
    model_disagreement: float | None,
    has_virtual_position: bool,
    max_staleness_minutes: int = MAX_QUOTE_STALENESS_MINUTES,
) -> float:
    """One governed cross-sport score: tier band + blended quality (0..1).

    Blends the existing signals — uncertainty-adjusted EV, readiness, quote
    freshness, forecast uncertainty, model disagreement, and whether exposure
    controls left a virtual position — so ranking never rests on raw edge alone.
    """

    def clamp(value: float) -> float:
        return max(0.0, min(1.0, value))

    ev_norm = clamp((uncertainty_adjusted_ev or 0.0) / 0.5)          # +50% EV → 1.0
    readiness_f = clamp((readiness or 0.0) / 100.0)
    max_age = max_staleness_minutes * 60
    age = quote_age_seconds if quote_age_seconds is not None else max_age
    fresh_f = clamp(1.0 - age / max_age) if max_age else 0.0
    uncertainty_f = clamp(1.0 - (forecast_uncertainty or 0.0) * 4.0)  # dispersion 0..0.25 → 1..0
    disagreement_f = clamp(1.0 - (model_disagreement or 0.0) / 40.0)  # 0..40 pts → 1..0
    exposure_f = 1.0 if has_virtual_position else 0.6
    quality = clamp(
        0.40 * ev_norm
        + 0.18 * readiness_f
        + 0.12 * fresh_f
        + 0.16 * uncertainty_f
        + 0.08 * disagreement_f
        + 0.06 * exposure_f
    )
    return round(_TIER_BASE.get(recommendation, 6.0) + 15.0 * quality, 1)


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
    "PROVISIONAL_EDGE",
    "EXTREME_DISAGREEMENT",
    "BookQuote",
    "SideInput",
    "MarketInput",
    "Candidate",
    "price_side",
    "build_market_candidate",
    "compute_opportunity_score",
    "rank_candidates",
    "allocate_stakes",
]

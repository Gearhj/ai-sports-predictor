from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from math import isfinite
from statistics import fmean
from typing import Iterable, Mapping


class InvalidOddsError(ValueError):
    pass


def american_to_decimal(american_odds: int | float) -> float:
    odds = float(american_odds)
    if not isfinite(odds) or odds == 0 or -100 < odds < 100:
        raise InvalidOddsError("American odds must be <= -100 or >= +100")
    return 1.0 + (odds / 100.0 if odds > 0 else 100.0 / abs(odds))


def decimal_to_american(decimal_odds: float) -> int:
    odds = float(decimal_odds)
    if not isfinite(odds) or odds <= 1:
        raise InvalidOddsError("Decimal odds must be greater than 1")
    raw = (odds - 1.0) * 100.0 if odds >= 2.0 else -100.0 / (odds - 1.0)
    return int(round(raw))


def american_implied_probability(american_odds: int | float) -> float:
    return 1.0 / american_to_decimal(american_odds)


def decimal_implied_probability(decimal_odds: float) -> float:
    if not isfinite(decimal_odds) or decimal_odds <= 1:
        raise InvalidOddsError("Decimal odds must be greater than 1")
    return 1.0 / decimal_odds


def probability_to_american(probability: float) -> int:
    if not isfinite(probability) or probability <= 0 or probability >= 1:
        raise InvalidOddsError("Probability must be strictly between 0 and 1")
    if probability >= 0.5:
        return int(round(-100 * probability / (1 - probability)))
    return int(round(100 * (1 - probability) / probability))


@dataclass(frozen=True)
class NoVigResult:
    probabilities: dict[str, float]
    raw_probabilities: dict[str, float]
    overround: float


def no_vig_probabilities(prices: Mapping[str, int | float], *, american: bool = True) -> NoVigResult:
    if len(prices) < 2:
        raise InvalidOddsError("At least two outcomes are required")
    implied = {
        selection: american_implied_probability(price) if american else decimal_implied_probability(float(price))
        for selection, price in prices.items()
    }
    total = sum(implied.values())
    if total <= 0:
        raise InvalidOddsError("Market implied probability total must be positive")
    return NoVigResult(
        probabilities={selection: probability / total for selection, probability in implied.items()},
        raw_probabilities=implied,
        overround=total - 1.0,
    )


@dataclass(frozen=True)
class ArbitrageResult:
    exists: bool
    inverse_sum: float
    margin: float
    stakes: dict[str, float]
    guaranteed_profit: float


def detect_arbitrage(best_decimal_prices: Mapping[str, float], bankroll: float = 100.0) -> ArbitrageResult:
    if len(best_decimal_prices) < 2:
        raise InvalidOddsError("At least two prices are required")
    if bankroll <= 0:
        raise ValueError("Bankroll must be positive")
    for price in best_decimal_prices.values():
        if not isfinite(price) or price <= 1:
            raise InvalidOddsError("All decimal prices must be greater than 1")
    inverse_sum = sum(1.0 / price for price in best_decimal_prices.values())
    exists = inverse_sum < 1.0
    if not exists:
        return ArbitrageResult(False, inverse_sum, 1.0 - inverse_sum, {}, 0.0)
    stakes = {
        selection: bankroll * (1.0 / price) / inverse_sum
        for selection, price in best_decimal_prices.items()
    }
    payout = bankroll / inverse_sum
    return ArbitrageResult(True, inverse_sum, 1.0 - inverse_sum, stakes, payout - bankroll)


@dataclass(frozen=True)
class PriceQuote:
    selection: str
    sportsbook: str
    american_odds: int
    observed_at: datetime
    point: float | None = None

    @property
    def decimal_odds(self) -> float:
        return american_to_decimal(self.american_odds)


@dataclass(frozen=True)
class ConsensusPrice:
    selection: str
    best_sportsbook: str
    best_american_odds: int
    worst_american_odds: int
    consensus_implied_probability: float
    book_count: int
    last_update: datetime


def market_consensus(quotes: Iterable[PriceQuote]) -> dict[str, ConsensusPrice]:
    groups: dict[str, list[PriceQuote]] = {}
    for quote in quotes:
        groups.setdefault(quote.selection, []).append(quote)
    result: dict[str, ConsensusPrice] = {}
    for selection, selection_quotes in groups.items():
        best = max(selection_quotes, key=lambda quote: quote.decimal_odds)
        worst = min(selection_quotes, key=lambda quote: quote.decimal_odds)
        result[selection] = ConsensusPrice(
            selection=selection,
            best_sportsbook=best.sportsbook,
            best_american_odds=best.american_odds,
            worst_american_odds=worst.american_odds,
            consensus_implied_probability=fmean(
                american_implied_probability(quote.american_odds) for quote in selection_quotes
            ),
            book_count=len({quote.sportsbook for quote in selection_quotes}),
            last_update=max(quote.observed_at for quote in selection_quotes),
        )
    return result


def normalize_team_name(name: str) -> str:
    aliases = {
        "ny yankees": "new york yankees",
        "la dodgers": "los angeles dodgers",
        "sf giants": "san francisco giants",
        "kc royals": "kansas city royals",
        "tb rays": "tampa bay rays",
    }
    normalized = " ".join("".join(char.lower() if char.isalnum() else " " for char in name).split())
    return aliases.get(normalized, normalized)


def team_name_match(left: str, right: str, threshold: float = 0.86) -> bool:
    first, second = normalize_team_name(left), normalize_team_name(right)
    return first == second or SequenceMatcher(None, first, second).ratio() >= threshold


def game_match(
    home_team: str,
    away_team: str,
    candidate_home: str,
    candidate_away: str,
    *,
    threshold: float = 0.86,
) -> bool:
    return team_name_match(home_team, candidate_home, threshold) and team_name_match(
        away_team, candidate_away, threshold
    )


def classify_opportunity(
    edge: float | None,
    *,
    watch: float = 0.02,
    standard: float = 0.04,
    strong: float = 0.07,
) -> str:
    if edge is None or edge < watch:
        return "NO BET"
    if edge < standard:
        return "WATCH"
    if edge < strong:
        return "STANDARD EDGE"
    return "STRONG EDGE"

"""Pure market-probability queries over an immutable paired-score population.

This module deliberately has no provider, filesystem, database, exchange, model,
or scheduler dependency.  It calculates empirical probability mass only.  It
does not decide whether a market is eligible, attractive, actionable, or sized.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import itertools
import json
import math
from typing import Any, Mapping, Sequence


CONTRACT_VERSION = "ASP_MARKET_PROBABILITY_CONTRACT_V1"
PROBABILITY_UNITS = "FRACTION_0_TO_1"
SUPPORTED_SPORTS = frozenset({"MLB", "NFL", "NBA", "CFB"})
SETTLEMENT_STATES = ("WIN", "LOSS", "PUSH", "VOID")


class MarketProbabilityError(ValueError):
    """Fail-closed contract error with a stable consumer-facing code."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise MarketProbabilityError("INVALID_NUMBER", f"{field} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise MarketProbabilityError("INVALID_NUMBER", f"{field} must be numeric") from error
    if not math.isfinite(result):
        raise MarketProbabilityError("INVALID_NUMBER", f"{field} must be finite")
    return result


def _nonnegative_integer(value: Any, field: str) -> int:
    number = _finite_number(value, field)
    if number < 0 or number != math.floor(number):
        raise MarketProbabilityError("INVALID_SCORE_POPULATION", f"{field} must be a nonnegative integer")
    return int(number)


@dataclass(frozen=True)
class ScoreCell:
    away_score: int
    home_score: int
    count: int

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ScoreCell":
        return cls(
            _nonnegative_integer(value.get("awayScore"), "awayScore"),
            _nonnegative_integer(value.get("homeScore"), "homeScore"),
            _nonnegative_integer(value.get("count"), "count"),
        )

    def canonical(self) -> dict[str, int]:
        return {"awayScore": self.away_score, "homeScore": self.home_score, "count": self.count}


@dataclass(frozen=True)
class JointScorePopulation:
    sport: str
    game_id: str
    forecast_id: str
    model_id: str
    configuration_id: str
    package_sha256: str
    cells: tuple[ScoreCell, ...]
    draw_count: int
    population_sha256: str

    @classmethod
    def from_cells(
        cls,
        *,
        sport: str,
        game_id: str,
        forecast_id: str,
        model_id: str,
        configuration_id: str,
        package_sha256: str,
        cells: Sequence[Mapping[str, Any]],
        draw_count: int,
        expected_population_sha256: str | None = None,
    ) -> "JointScorePopulation":
        normalized_sport = str(sport).upper()
        if normalized_sport not in SUPPORTED_SPORTS:
            raise MarketProbabilityError("SPORT_UNSUPPORTED", f"unsupported sport {sport!r}")
        identities = {
            "gameId": game_id,
            "forecastId": forecast_id,
            "modelId": model_id,
            "configurationId": configuration_id,
            "packageSha256": package_sha256,
        }
        if any(not isinstance(value, str) or not value for value in identities.values()):
            raise MarketProbabilityError("IDENTITY_INCOMPLETE", "all population identities are required")
        if len(package_sha256) != 64 or any(c not in "0123456789abcdef" for c in package_sha256.lower()):
            raise MarketProbabilityError("IDENTITY_INVALID", "packageSha256 must be a hexadecimal SHA-256")
        expected_count = _nonnegative_integer(draw_count, "drawCount")
        if expected_count == 0:
            raise MarketProbabilityError(
                "PAIRED_POPULATION_UNAVAILABLE",
                "point-only forecasts cannot answer spread, total, or joint probability queries",
            )
        parsed = tuple(ScoreCell.from_mapping(row) for row in cells)
        if not parsed or any(cell.count == 0 for cell in parsed):
            raise MarketProbabilityError("INVALID_SCORE_POPULATION", "population cells must have positive count")
        if len({(cell.away_score, cell.home_score) for cell in parsed}) != len(parsed):
            raise MarketProbabilityError("DUPLICATE_SCORE_CELL", "each paired score cell must be unique")
        ordered = tuple(sorted(parsed, key=lambda cell: (cell.away_score, cell.home_score)))
        actual_count = sum(cell.count for cell in ordered)
        if actual_count != expected_count:
            raise MarketProbabilityError(
                "DRAW_COUNT_MISMATCH", f"cell counts sum to {actual_count}, declared drawCount is {expected_count}"
            )
        canonical_cells = [cell.canonical() for cell in ordered]
        population_sha = _sha256(canonical_json_bytes(canonical_cells))
        if expected_population_sha256 is not None and population_sha != expected_population_sha256:
            raise MarketProbabilityError("POPULATION_HASH_MISMATCH", "paired score population hash differs")
        return cls(
            normalized_sport,
            game_id,
            forecast_id,
            model_id,
            configuration_id,
            package_sha256.lower(),
            ordered,
            expected_count,
            population_sha,
        )

    @property
    def binding(self) -> dict[str, Any]:
        return {
            "sport": self.sport,
            "gameId": self.game_id,
            "forecastId": self.forecast_id,
            "modelId": self.model_id,
            "configurationId": self.configuration_id,
            "packageSha256": self.package_sha256,
            "populationSha256": self.population_sha256,
            "drawCount": self.draw_count,
        }


def _normalized_leg(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise MarketProbabilityError("LEG_INVALID", "each leg must be an object")
    market = str(value.get("market", "")).upper()
    if market == "MONEYLINE":
        selection = str(value.get("selection", "")).upper()
        if selection not in {"HOME", "AWAY", "TIE"}:
            raise MarketProbabilityError("SELECTION_INVALID", "moneyline selection must be HOME, AWAY, or TIE")
        tie = str(value.get("tieSettlement", "")).upper()
        if selection != "TIE" and tie not in {"PUSH", "LOSS", "VOID"}:
            raise MarketProbabilityError(
                "TIE_SETTLEMENT_REQUIRED", "HOME/AWAY moneyline requires PUSH, LOSS, or VOID tie settlement"
            )
        return {"market": market, "selection": selection, "tieSettlement": tie or "LOSS"}
    if market == "SPREAD":
        selection = str(value.get("selection", "")).upper()
        if selection not in {"HOME", "AWAY"}:
            raise MarketProbabilityError("SELECTION_INVALID", "spread selection must be HOME or AWAY")
        return {"market": market, "selection": selection, "line": _finite_number(value.get("line"), "line")}
    if market == "TOTAL":
        selection = str(value.get("selection", "")).upper()
        if selection not in {"OVER", "UNDER"}:
            raise MarketProbabilityError("SELECTION_INVALID", "total selection must be OVER or UNDER")
        return {"market": market, "selection": selection, "line": _finite_number(value.get("line"), "line")}
    raise MarketProbabilityError("MARKET_UNSUPPORTED", f"unsupported market {market!r}")


def _settle(leg: Mapping[str, Any], away: int, home: int) -> str:
    market = leg["market"]
    if market == "MONEYLINE":
        selection = leg["selection"]
        if away == home:
            return "WIN" if selection == "TIE" else leg["tieSettlement"]
        if selection == "TIE":
            return "LOSS"
        winner = "HOME" if home > away else "AWAY"
        return "WIN" if selection == winner else "LOSS"
    if market == "SPREAD":
        selected = home if leg["selection"] == "HOME" else away
        opponent = away if leg["selection"] == "HOME" else home
        adjusted = selected + leg["line"] - opponent
    else:
        adjusted = away + home - leg["line"]
        if leg["selection"] == "UNDER":
            adjusted = -adjusted
    return "WIN" if adjusted > 0 else "LOSS" if adjusted < 0 else "PUSH"


def _fair_price(probabilities: Mapping[str, float]) -> dict[str, Any]:
    p_win = probabilities["win"]
    p_loss = probabilities["loss"]
    return {
        "pWin": p_win,
        "pLoss": p_loss,
        "pPush": probabilities["push"],
        "pVoid": probabilities["void"],
        "pushAwareFairDecimalOdds": None if p_win == 0 else 1.0 + p_loss / p_win,
        "definition": "1 + pLoss / pWin; push and void mass are refunded and excluded from net gain/loss",
    }


def _result(population: JointScorePopulation, legs: Sequence[dict[str, Any]], counts: Mapping[str, int], *, method: str) -> dict[str, Any]:
    n = population.draw_count
    probabilities = {state.lower(): counts[state] / n for state in SETTLEMENT_STATES}
    if not math.isclose(sum(probabilities.values()), 1.0, rel_tol=0.0, abs_tol=1e-15):
        raise MarketProbabilityError("PROBABILITY_MASS_MISMATCH", "settlement mass does not equal one")
    p_win = probabilities["win"]
    return {
        "schemaVersion": CONTRACT_VERSION,
        "status": "SUPPORTED_DIRECT_FROM_PAIRED_POPULATION",
        "method": method,
        "probabilityUnits": PROBABILITY_UNITS,
        "population": population.binding,
        "legs": list(legs),
        "counts": {state.lower(): counts[state] for state in SETTLEMENT_STATES},
        "probabilities": probabilities,
        "effectiveDrawCount": n,
        "winProbabilityMonteCarloStandardError": math.sqrt(p_win * (1.0 - p_win) / n),
        "monteCarloQualification": "sampling precision for this empirical population, not model or input uncertainty",
        "fairPriceComponents": _fair_price(probabilities),
        "authority": {
            "probabilityCalculationOnly": True,
            "bookSelection": False,
            "edgeDetermination": False,
            "decisionEligibility": False,
            "stakeSelection": False,
        },
    }


def evaluate_market(population: JointScorePopulation, leg: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate one moneyline, spread, or total leg."""
    normalized = _normalized_leg(leg)
    counts = {state: 0 for state in SETTLEMENT_STATES}
    for cell in population.cells:
        counts[_settle(normalized, cell.away_score, cell.home_score)] += cell.count
    return _result(population, [normalized], counts, method="EMPIRICAL_PAIRED_SCORE_ENUMERATION")


def evaluate_same_game_joint(population: JointScorePopulation, legs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Intersect leg outcomes within each paired score draw/cell.

    A loss dominates.  If there is no loss, all wins produce WIN, any void
    produces VOID, and otherwise a push produces PUSH.  Consumer settlement
    rules may price reduced-leg parlays differently; this result is the raw
    model-state breakdown, not a sportsbook settlement engine.
    """
    normalized = [_normalized_leg(leg) for leg in legs]
    if len(normalized) < 2:
        raise MarketProbabilityError("JOINT_REQUIRES_MULTIPLE_LEGS", "same-game joint requires at least two legs")
    counts = {state: 0 for state in SETTLEMENT_STATES}
    for cell in population.cells:
        states = [_settle(leg, cell.away_score, cell.home_score) for leg in normalized]
        state = "LOSS" if "LOSS" in states else "WIN" if all(x == "WIN" for x in states) else "VOID" if "VOID" in states else "PUSH"
        counts[state] += cell.count
    result = _result(population, normalized, counts, method="PER_DRAW_SAME_GAME_INTERSECTION")
    result["settlementPrecedence"] = ["LOSS", "ALL_WIN", "VOID", "PUSH"]
    return result


def evaluate_cross_game(
    results: Sequence[Mapping[str, Any]], *, allow_independence_assumption: bool = False
) -> dict[str, Any]:
    """Fail closed by default; optionally convolve marginal settlement states."""
    if len(results) < 2:
        raise MarketProbabilityError("CROSS_GAME_REQUIRES_MULTIPLE_GAMES", "at least two game results are required")
    game_ids = [str(result.get("population", {}).get("gameId", "")) for result in results]
    if len(set(game_ids)) != len(game_ids) or any(not value for value in game_ids):
        raise MarketProbabilityError("CROSS_GAME_IDENTITY_INVALID", "cross-game inputs require distinct game identities")
    if not allow_independence_assumption:
        raise MarketProbabilityError(
            "CROSS_GAME_JOINT_UNSUPPORTED",
            "no cross-game joint population exists; explicit independence research mode is required",
        )
    distributions = []
    for result in results:
        probabilities = result.get("probabilities")
        if not isinstance(probabilities, Mapping) or any(state.lower() not in probabilities for state in SETTLEMENT_STATES):
            raise MarketProbabilityError("CROSS_GAME_INPUT_INVALID", "each input needs a complete settlement distribution")
        distribution = {state: _finite_number(probabilities[state.lower()], state.lower()) for state in SETTLEMENT_STATES}
        if any(value < 0.0 or value > 1.0 for value in distribution.values()) or not math.isclose(
            sum(distribution.values()), 1.0, rel_tol=0.0, abs_tol=1e-12
        ):
            raise MarketProbabilityError("CROSS_GAME_INPUT_INVALID", "each settlement distribution must normalize to one")
        distributions.append(distribution)
    totals = {state: 0.0 for state in SETTLEMENT_STATES}
    for states in itertools.product(SETTLEMENT_STATES, repeat=len(distributions)):
        mass = math.prod(distributions[index][state] for index, state in enumerate(states))
        outcome = "LOSS" if "LOSS" in states else "WIN" if all(x == "WIN" for x in states) else "VOID" if "VOID" in states else "PUSH"
        totals[outcome] += mass
    if not math.isclose(sum(totals.values()), 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise MarketProbabilityError("PROBABILITY_MASS_MISMATCH", "independence convolution mass does not equal one")
    return {
        "schemaVersion": CONTRACT_VERSION,
        "status": "APPROXIMATE_RESEARCH_ONLY",
        "method": "INDEPENDENCE_ASSUMPTION",
        "probabilityUnits": PROBABILITY_UNITS,
        "gameIds": game_ids,
        "probabilities": {state.lower(): totals[state] for state in SETTLEMENT_STATES},
        "effectiveDrawCount": None,
        "winProbabilityMonteCarloStandardError": None,
        "unsupportedReason": "No retained cross-game joint simulation population; dependence is unmodeled.",
        "fairPriceComponents": _fair_price({state.lower(): totals[state] for state in SETTLEMENT_STATES}),
        "authority": {
            "probabilityCalculationOnly": True,
            "bookSelection": False,
            "edgeDetermination": False,
            "decisionEligibility": False,
            "stakeSelection": False,
        },
    }


def unsupported(code: str, message: str, *, binding: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Structured fail-closed response for adapters that cannot build a population."""
    return {
        "schemaVersion": CONTRACT_VERSION,
        "status": "UNSUPPORTED",
        "error": {"code": code, "message": message},
        "binding": dict(binding or {}),
        "probabilityUnits": PROBABILITY_UNITS,
    }

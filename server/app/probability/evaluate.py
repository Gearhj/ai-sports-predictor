"""Consumer evaluation over a verified population, and AISP's parlay-price gate.

The request mirrors ``ASP_MARKET_PROBABILITY_REQUEST_V1`` (validated field by
field; unknown fields are refused). One leg is a single-market evaluation;
two or more legs are a same-game paired-draw intersection over the same
population. There is no cross-game path: any cross-game mode other than
``REJECT`` fails closed with ``CROSS_GAME_CALIBRATION_NOT_VALIDATED``.

Edge and expected return for a parlay are assessed only against an actual
recorded sportsbook combined quote. No such quote is stored today, and a price
multiplied from individual leg prices is never accepted.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.probability import adapter
from app.probability.adapter import ProbabilityUnavailable
from app.probability.identity import (
    CROSS_GAME_CALIBRATION_NOT_VALIDATED,
    CROSS_GAME_MESSAGE,
    PARLAY_PRICE_UNAVAILABLE,
    RECORDED_COMBINED_QUOTE,
    SAME_GAME_DISPLAY_METHOD,
)
from app.probability.market_probabilities import (
    MarketProbabilityError,
    evaluate_market,
    evaluate_same_game_joint,
)

REQUEST_SCHEMA = "ASP_MARKET_PROBABILITY_REQUEST_V1"
RESULT_SCHEMA = "AISP_PROBABILITY_RESULT_V1"
_SHA = r"^[0-9a-f]{64}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PopulationBinding(_Strict):
    sport: Literal["MLB", "NFL", "NBA", "CFB"]
    gameId: str = Field(min_length=1)
    forecastId: str = Field(min_length=1)
    modelId: str = Field(min_length=1)
    configurationId: str = Field(min_length=1)
    packageSha256: str = Field(pattern=_SHA)
    populationSha256: str = Field(pattern=_SHA)
    drawCount: int = Field(ge=1)


class MoneylineLeg(_Strict):
    market: Literal["MONEYLINE"]
    selection: Literal["HOME", "AWAY", "TIE"]
    tieSettlement: Literal["PUSH", "LOSS", "VOID"] | None = None


class SpreadLeg(_Strict):
    market: Literal["SPREAD"]
    selection: Literal["HOME", "AWAY"]
    line: float | int


class TotalLeg(_Strict):
    market: Literal["TOTAL"]
    selection: Literal["OVER", "UNDER"]
    line: float | int


class ProbabilityRequest(_Strict):
    schemaVersion: Literal["ASP_MARKET_PROBABILITY_REQUEST_V1"]
    populationBinding: PopulationBinding
    legs: list[dict[str, Any]] = Field(min_length=1)
    crossGameMode: Literal["REJECT", "INDEPENDENCE_ASSUMPTION"] | None = None


_LEG_MODELS = {"MONEYLINE": MoneylineLeg, "SPREAD": SpreadLeg, "TOTAL": TotalLeg}


def cross_game_refusal() -> ProbabilityUnavailable:
    return ProbabilityUnavailable(CROSS_GAME_CALIBRATION_NOT_VALIDATED, CROSS_GAME_MESSAGE)


def parse_request(body: Any) -> tuple[ProbabilityRequest, list[dict[str, Any]]]:
    if isinstance(body, Mapping) and body.get("crossGameMode") not in (None, "REJECT"):
        raise cross_game_refusal()
    try:
        request = ProbabilityRequest.model_validate(body)
        legs = []
        for leg in request.legs:
            model = _LEG_MODELS.get(str(leg.get("market")))
            if model is None:
                raise ProbabilityUnavailable("MARKET_UNSUPPORTED", "leg market must be MONEYLINE, SPREAD or TOTAL")
            legs.append(model.model_validate(leg).model_dump(exclude_none=True))
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first.get("loc", ()))
        raise ProbabilityUnavailable("REQUEST_INVALID", f"{where}: {first.get('msg')}") from exc
    return request, legs


def _american(decimal_odds: float | None) -> int | None:
    if decimal_odds is None or decimal_odds <= 1:
        return None
    return round((decimal_odds - 1) * 100) if decimal_odds >= 2 else round(-100 / (decimal_odds - 1))


def parlay_assessment(result: Mapping[str, Any], quote: Mapping[str, Any] | None) -> dict[str, Any]:
    """Implied probability, edge and expected return -- only from a recorded combined quote."""

    unavailable = {
        "quote_status": "SPORTSBOOK_PARLAY_PRICE_UNAVAILABLE",
        "message": PARLAY_PRICE_UNAVAILABLE,
        "quote": None, "implied_probability": None, "edge": None, "expected_return": None,
    }
    if not quote or quote.get("provenance") != RECORDED_COMBINED_QUOTE:
        return unavailable
    decimal = quote.get("decimal")
    if not isinstance(decimal, (int, float)) or not math.isfinite(decimal) or decimal <= 1:
        return unavailable
    p = result["probabilities"]
    settled = p["win"] + p["loss"]
    conditional = p["win"] / settled if settled else None
    implied = 1 / decimal
    return {
        "quote_status": "RECORDED_SPORTSBOOK_PARLAY_PRICE",
        "message": "Research comparison against an actual sportsbook parlay price. Not a governed decision.",
        "quote": {k: quote.get(k) for k in ("sportsbook", "decimal", "american", "observed_at", "provenance")},
        "implied_probability": implied,
        "edge": None if conditional is None else conditional - implied,
        # Per unit staked; push and void mass are refunds.
        "expected_return": p["win"] * (decimal - 1) - p["loss"],
    }


def run(channel: adapter.VerifiedChannel, legs: list[dict[str, Any]],
        *, parlay_quote: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Evaluate legs against one verified population and wrap the contract result."""

    try:
        if len(legs) == 1:
            result = evaluate_market(channel.population, legs[0])
        else:
            result = evaluate_same_game_joint(channel.population, legs)
    except MarketProbabilityError as exc:
        raise ProbabilityUnavailable(exc.code, exc.message) from exc
    fair = result["fairPriceComponents"]["pushAwareFairDecimalOdds"]
    envelope: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA,
        "contract": result,
        "kind": "SINGLE_MARKET" if len(legs) == 1 else "SAME_GAME_PARLAY",
        "display_method": SAME_GAME_DISPLAY_METHOD if len(legs) > 1 else "PAIRED_POPULATION_ENUMERATION",
        "authority": channel.authority,
        "authority_label": channel.authority_label,
        "tie_policy": channel.tie_policy,
        "forecast_created_at": adapter._iso(channel.forecast_created_at),
        "fair_american": _american(fair),
        "research_only": True,
        "creates_decision": False,
        "creates_position": False,
        "provider_calls_made": 0,
    }
    if len(legs) > 1:
        envelope["parlay"] = parlay_assessment(result, parlay_quote)
    return envelope


def evaluate_request(session: Session, settings: Settings, body: Any) -> dict[str, Any]:
    request, legs = parse_request(body)
    claimed = request.populationBinding.model_dump()
    channel = adapter.bind(session, settings, adapter.parse_game_id(claimed["gameId"]))
    adapter.require_binding(channel, claimed)
    return run(channel, legs)


def leg_masses(channel: adapter.VerifiedChannel, leg: dict[str, Any]) -> dict[str, Any]:
    """Win/loss/push/void and push-aware fair price for one quoted side."""

    result = evaluate_market(channel.population, leg)
    fair = result["fairPriceComponents"]["pushAwareFairDecimalOdds"]
    p = result["probabilities"]
    settled = p["win"] + p["loss"]
    return {
        "win": p["win"], "loss": p["loss"], "push": p["push"], "void": p["void"],
        "conditional": (p["win"] / settled) if settled else None,
        "mcse": result["winProbabilityMonteCarloStandardError"],
        "fair_decimal": fair, "fair_american": _american(fair),
    }

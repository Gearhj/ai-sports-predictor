"""Player-prop quote contract: supported markets, outcomes, wording and authority.

Quote display and a user's own virtual pick are the only permitted uses.
Model Studio's producer disposition is recorded here verbatim in substance and
served with every player-prop payload.
"""

from __future__ import annotations

import math
from typing import Any

CONTRACT = "AISP_PLAYER_PROP_QUOTE_V1"
PROVIDER = "THE_ODDS_API"
SPORT_KEY = "baseball_mlb"
QUOTE_FRESHNESS_MINUTES = 240
NO_ASSESSMENT_LABEL = "Sportsbook market — no AISP assessment"

#: provider market key -> accepted outcome names and wording.
SUPPORTED_MARKETS: dict[str, dict[str, Any]] = {
    "batter_hits": {"label": "Hits", "unit": ("hit", "hits"), "outcomes": {"Over", "Under"}, "role": "BATTER"},
    "batter_total_bases": {"label": "Total bases", "unit": ("total base", "total bases"),
                           "outcomes": {"Over", "Under"}, "role": "BATTER"},
    "batter_home_runs": {"label": "Home runs", "unit": ("home run", "home runs"),
                         "outcomes": {"Over", "Under", "Yes", "No"}, "role": "BATTER"},
    "pitcher_strikeouts": {"label": "Strikeouts", "unit": ("strikeout", "strikeouts"),
                           "outcomes": {"Over", "Under"}, "role": "PITCHER"},
}

OUTCOME_SIDE = {"Over": "OVER", "Under": "UNDER", "Yes": "YES", "No": "NO"}

#: Model Studio's recorded disposition for player props (producer handoff).
PRODUCER_DISPOSITION: dict[str, Any] = {
    "producer": "Model Studio",
    "passive_player_counters": "AVAILABLE",
    "player_prop_probabilities": "INSUFFICIENT_EVIDENCE",
    "supported_in_aisp": ["QUOTE_DISPLAY", "USER_VIRTUAL_SELECTION"],
    "not_supported_in_aisp": ["AISP_PROBABILITY", "FAIR_PRICE", "EDGE", "EXPECTED_RETURN",
                              "DECISION", "POSITION", "CONFIDENCE"],
    "future_assessed_path": "Requires prospective Model Studio calibration evidence before any assessment.",
}

# Quarantine reasons.
GAME_UNRESOLVED = "GAME_UNRESOLVED"
GAME_NOT_MLB = "GAME_NOT_MLB"
PARTICIPANT_CONFLICT = "EVENT_PARTICIPANT_CONFLICT"
PLAYER_UNKNOWN = "PLAYER_UNKNOWN"
PLAYER_NO_EVIDENCE = "PLAYER_NO_GOVERNED_EVIDENCE"
PLAYER_AMBIGUOUS = "PLAYER_AMBIGUOUS"
PLAYER_WRONG_TEAM = "PLAYER_WRONG_TEAM"
MARKET_UNSUPPORTED = "MARKET_UNSUPPORTED"
OUTCOME_UNRECOGNIZED = "OUTCOME_UNRECOGNIZED"
ORIENTATION_CONFLICT = "ORIENTATION_CONFLICT"
LINE_MISSING = "LINE_MISSING"
PRICE_MISSING = "PRICE_MISSING"


def decimal_from_american(american: int | None) -> float | None:
    if american is None or american == 0 or -100 < american < 100:
        return None
    return round(1 + (american / 100 if american > 0 else 100 / -american), 6)


def describe(market_key: str, outcome_name: str, point: float | None) -> str:
    """User-facing wording. The provider's market, outcome and line stay alongside it.

    ``batter_hits`` Over 0.5 reads "1+ hit"; half-point Over lines read "N+ unit";
    everything else keeps the provider's own Over/Under wording.
    """

    spec = SUPPORTED_MARKETS.get(market_key)
    if spec is None:
        return f"{outcome_name} {point}" if point is not None else outcome_name
    one, many = spec["unit"]
    if outcome_name == "Yes":
        return f"1+ {one}"
    if outcome_name == "No":
        return f"No {one}"
    if point is None:
        return f"{outcome_name} {many}"
    whole = point - math.floor(point) == 0.5
    if outcome_name == "Over" and whole:
        n = math.ceil(point)
        return f"{n}+ {one if n == 1 else many}"
    if outcome_name == "Under" and whole and point == 0.5:
        return f"No {one}"
    return f"{outcome_name} {point:g} {many}"

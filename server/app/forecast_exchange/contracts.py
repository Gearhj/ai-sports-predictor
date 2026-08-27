"""Exact, closed contract models for the AISP daily forecast exchange.

Every model forbids unknown fields (``extra="forbid"``) so a producer schema
drift or an injected field is rejected rather than silently ignored. Field
names are snake_case in Python and camelCase on the wire via a shared alias
generator; the four ``schema_version`` values are pinned with ``Literal``.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


GAME_FORECAST_CONTRACT = "AISP_GAME_FORECAST_EXPORT_V1"
MARKET_QUOTE_CONTRACT = "AISP_MARKET_QUOTE_V1"
BET_CANDIDATE_CONTRACT = "AISP_BET_CANDIDATE_V1"
DAILY_BUNDLE_CONTRACT = "AISP_DAILY_FORECAST_BUNDLE_V1"

# The four exact contract versions the consumer supports.
CONTRACT_VERSIONS = {
    "gameForecast": GAME_FORECAST_CONTRACT,
    "marketQuote": MARKET_QUOTE_CONTRACT,
    "betCandidate": BET_CANDIDATE_CONTRACT,
    "dailyForecastBundle": DAILY_BUNDLE_CONTRACT,
}

# The earliest event season this consumer refuses (research-shadow evidence is
# historical only; 2025+ live data is out of scope for the shadow board).
REJECTED_SEASON_FLOOR = 2025


class _Contract(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class TeamRef(_Contract):
    display_name: str | None
    team_id: str


class Engine(_Contract):
    candidate_id: str
    class_order: list[str]
    engine_id: str
    engine_version: str
    input_data_sha256: str
    model_artifact_sha256: str


class Explanation(_Contract):
    code: str
    detail: str
    summary: str


class Readiness(_Contract):
    is_ready: bool
    missing_inputs: list[str]
    reason_codes: list[str]
    score: float
    threshold: float


class Simulator(_Contract):
    completed_simulations: int
    illegal_state_count: int
    nontermination_count: int
    seed: int
    simulation_count: int
    simulator_id: str
    simulator_version: str


class Uncertainty(_Contract):
    away_win_probability_lower: float
    away_win_probability_upper: float
    confidence_level: float
    expected_total_score_std_error: float
    first_five_expected_total_score_std_error: float
    first_five_home_win_probability_lower: float
    first_five_home_win_probability_upper: float
    home_win_probability_lower: float
    home_win_probability_upper: float
    max_batch_probability_delta: float
    method: str


class JointScoreCell(_Contract):
    away_score: int
    home_score: int
    probability: float


class MarginCell(_Contract):
    home_minus_away: int
    probability: float


class TotalCell(_Contract):
    probability: float
    total_score: int


class GameSplit(_Contract):
    """A full-game or first-five simulation summary and distributions."""

    available: bool
    away_win_probability: float
    completed_simulations: int
    completion_rate: float
    expected_away_score: float
    expected_home_score: float
    expected_total_score: float
    home_win_probability: float
    joint_score_distribution: list[JointScoreCell]
    missing_reason: str | None
    score_margin_distribution: list[MarginCell]
    tie_probability: float
    total_score_distribution: list[TotalCell]


class GameForecastExportV1(_Contract):
    away_team: TeamRef
    engine: Engine
    event_date: date
    event_id: str
    execution_allowed: bool
    explanations: list[Explanation]
    first_five: GameSplit
    forecast_cutoff_utc: datetime | None
    forecast_id: str
    full_game: GameSplit
    home_team: TeamRef
    league: str
    model_promotion_allowed: bool
    readiness: Readiness
    research_shadow_only: bool
    scheduled_start_utc: datetime | None
    schema_version: Literal["AISP_GAME_FORECAST_EXPORT_V1"]
    season: int
    simulator: Simulator
    source_event_id: str
    sport: str
    status: str
    uncertainty: Uncertainty


class MarketQuoteV1(_Contract):
    """Bundle-carried market quote. Empty in historical research bundles.

    Modeled defensively so any future producer-supplied quote is validated
    rather than silently accepted; the shadow board otherwise sources live
    quotes from the existing Odds API integration, never from fabrication.
    """

    schema_version: Literal["AISP_MARKET_QUOTE_V1"]
    event_id: str
    market: str
    selection: str
    book: str
    american_price: int
    observed_at_utc: datetime
    line: float | None = None


class BetCandidateV1(_Contract):
    """Bundle-carried bet candidate. Empty in historical research bundles."""

    schema_version: Literal["AISP_BET_CANDIDATE_V1"]
    event_id: str
    engine_id: str
    market: str
    selection: str
    model_probability: float
    recommendation: str


class DailyForecastBundleV1(_Contract):
    bet_candidates: list[BetCandidateV1]
    bundle_id: str
    execution_allowed: bool
    explanations: list[Explanation]
    forecast_cutoff_utc: datetime | None
    forecasts: list[GameForecastExportV1]
    generated_at_utc: datetime
    league: str
    market_quotes: list[MarketQuoteV1]
    model_promotion_allowed: bool
    parlays_allowed: bool
    readiness: Readiness
    research_shadow_only: bool
    schema_version: Literal["AISP_DAILY_FORECAST_BUNDLE_V1"]
    slate_date: date
    sport: str
    status: str


class ExpectedRecordCounts(_Contract):
    bet_candidates: int
    forecasts: int
    market_quotes: int


class ConsumerImportFixtureV1(_Contract):
    bundle_logical_uri: str
    bundle_sha256: str
    execution_allowed: bool
    expected_contract_versions: dict[str, str]
    expected_record_counts: ExpectedRecordCounts
    fixture_id: str
    research_shadow_only: bool
    schema_version: Literal["AISP_CONSUMER_IMPORT_FIXTURE_V1"]
    validation_instructions_sha256: str


__all__ = [
    "BET_CANDIDATE_CONTRACT",
    "CONTRACT_VERSIONS",
    "DAILY_BUNDLE_CONTRACT",
    "GAME_FORECAST_CONTRACT",
    "MARKET_QUOTE_CONTRACT",
    "REJECTED_SEASON_FLOOR",
    "BetCandidateV1",
    "ConsumerImportFixtureV1",
    "DailyForecastBundleV1",
    "Engine",
    "Explanation",
    "GameForecastExportV1",
    "GameSplit",
    "MarketQuoteV1",
    "Readiness",
    "Simulator",
    "TeamRef",
    "Uncertainty",
]

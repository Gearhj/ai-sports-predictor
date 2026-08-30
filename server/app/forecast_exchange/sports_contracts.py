"""Closed sport-neutral prospective forecast-exchange contracts.

This schema is additive.  The historical MLB ``AISP_DAILY_FORECAST_BUNDLE_V1``
contract remains byte-for-byte compatible in :mod:`contracts`; prospective NFL
research-shadow bundles use the explicitly separate contract family below.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
import math
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from app.gen2.exchange.canonical_json import canonical_sha256


SPORTS_BUNDLE_CONTRACT = "ASP_SPORTS_FORECAST_EXCHANGE_V1"
SPORTS_FORECAST_CONTRACT = "ASP_SPORTS_GAME_FORECAST_V1"
SPORTS_FIXTURE_CONTRACT = "ASP_SPORTS_FORECAST_IMPORT_FIXTURE_V1"
NFL_SHADOW_INDEX_CONTRACT = "ASP_NFL_2026_RESEARCH_SHADOW_INDEX_V1"
NFL_SHADOW_INDEX_V2_CONTRACT = "ASP_NFL_2026_RESEARCH_SHADOW_INDEX_V2"
SPORTS_CONTRACT_VERSIONS = {
    "sportsForecastExchange": SPORTS_BUNDLE_CONTRACT,
    "sportsGameForecast": SPORTS_FORECAST_CONTRACT,
}

NFL_ENGINE_ID = "NFL_M1_XGBOOST"
NFL_MODEL_MANIFEST_SHA256 = "ab9c066052b288f3f6162d8f8558e673988fb52172c72fe00aa855aa800a45e9"
NFL_MODEL_BINARY_SHA256 = "7bec06646fd5035380107501d07e139425ac483a47e02e0bbbd56301124fb8fd"
NFL_FEATURE_CONTRACT_SHA256 = "ef5c4f9ce8c3dd04a473e7ecb2ea3a255154f65a1bb3fda9d2ebc19bf1cb8f4d"
NFL_TRAINING_CUTOFF = date(2026, 2, 8)


class _Contract(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class SportsTeamRef(_Contract):
    team_id: str = Field(min_length=1, max_length=120)
    source: str = Field(min_length=1, max_length=80)
    source_team_id: str = Field(min_length=1, max_length=120)
    abbreviation: str = Field(min_length=1, max_length=12)
    identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    display_name: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def identity_binding(self) -> "SportsTeamRef":
        projection = {
            "abbreviation": self.abbreviation,
            "source": self.source,
            "sourceTeamId": self.source_team_id,
            "teamId": self.team_id,
        }
        if canonical_sha256(projection) != self.identity_sha256:
            raise ValueError("team identity hash does not bind exact provider/canonical IDs")
        return self


class SportsEngine(_Contract):
    engine_id: Literal["NFL_M1_XGBOOST"]
    engine_version: str = Field(min_length=1, max_length=80)
    candidate_id: str = Field(min_length=1, max_length=160)
    model_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_binary_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    training_dataset_version: str = Field(min_length=1, max_length=160)
    training_dataset_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    feature_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    producer_revision: str = Field(min_length=7, max_length=120)
    feature_tier: Literal["LONG_HISTORY_CORE"]
    training_cutoff: date


class SportsReadiness(_Contract):
    is_ready: bool
    score: float = Field(ge=0, le=100)
    threshold: float = Field(ge=0, le=100)
    missing_inputs: list[str]
    reason_codes: list[str]


class SportsFeatureEvidence(_Contract):
    feature_count: int = Field(gt=0)
    feature_vector_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    missing_feature_count: int = Field(ge=0)
    missing_features: list[str]
    missing_reasons: dict[str, str]
    readiness: SportsReadiness

    @model_validator(mode="after")
    def complete_missingness_accounting(self) -> "SportsFeatureEvidence":
        if self.missing_feature_count != len(self.missing_features):
            raise ValueError("missing feature count does not reconcile")
        if set(self.missing_reasons) != set(self.missing_features):
            raise ValueError("missing reasons do not cover the exact missing-feature set")
        if self.missing_feature_count > self.feature_count:
            raise ValueError("missing feature count exceeds feature contract")
        return self


class SportsSimulator(_Contract):
    simulator_id: str = Field(min_length=1, max_length=80)
    simulator_version: str = Field(min_length=1, max_length=80)
    seed: int
    simulation_count: int = Field(gt=0)
    completed_simulations: int = Field(gt=0)

    @model_validator(mode="after")
    def completed_not_above_requested(self) -> "SportsSimulator":
        if self.completed_simulations != self.simulation_count:
            raise ValueError("completed simulations differ from requested simulations")
        return self


class SportsUncertainty(_Contract):
    method: str = Field(min_length=1)
    confidence_level: float = Field(gt=0, lt=1)
    home_win_probability_lower: float = Field(ge=0, le=1)
    home_win_probability_upper: float = Field(ge=0, le=1)
    expected_total_score_std_error: float = Field(ge=0)
    max_batch_probability_delta: float = Field(ge=0)

    @model_validator(mode="after")
    def ordered_interval(self) -> "SportsUncertainty":
        if self.home_win_probability_lower > self.home_win_probability_upper:
            raise ValueError("home-win uncertainty interval is reversed")
        return self


class SportsPredictionInterval(_Contract):
    confidence_level: float = Field(gt=0, lt=1)
    home_score_lower: float
    home_score_upper: float
    away_score_lower: float
    away_score_upper: float
    total_lower: float
    total_upper: float
    margin_lower: float
    margin_upper: float

    @model_validator(mode="after")
    def ordered_intervals(self) -> "SportsPredictionInterval":
        values = (
            self.home_score_lower, self.home_score_upper, self.away_score_lower,
            self.away_score_upper, self.total_lower, self.total_upper,
            self.margin_lower, self.margin_upper,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("prediction intervals must be finite")
        if (
            self.home_score_lower > self.home_score_upper
            or self.away_score_lower > self.away_score_upper
            or self.total_lower > self.total_upper
            or self.margin_lower > self.margin_upper
        ):
            raise ValueError("prediction interval is reversed")
        if any(value < 0 for value in (
            self.home_score_lower, self.home_score_upper,
            self.away_score_lower, self.away_score_upper,
            self.total_lower, self.total_upper,
        )):
            raise ValueError("score/total prediction intervals must be nonnegative")
        return self


class SportsJointScoreCell(_Contract):
    home_score: int = Field(ge=0)
    away_score: int = Field(ge=0)
    probability: float = Field(ge=0, le=1)


class SportsMarginCell(_Contract):
    home_minus_away: int
    probability: float = Field(ge=0, le=1)


class SportsTotalCell(_Contract):
    total_score: int = Field(ge=0)
    probability: float = Field(ge=0, le=1)


class SportsSpreadProbability(_Contract):
    line: float
    home_cover_probability: float = Field(ge=0, le=1)
    away_cover_probability: float = Field(ge=0, le=1)
    push_probability: float = Field(ge=0, le=1)


class SportsTotalProbability(_Contract):
    line: float
    over_probability: float = Field(ge=0, le=1)
    under_probability: float = Field(ge=0, le=1)
    push_probability: float = Field(ge=0, le=1)


class SportsLikelyScore(_Contract):
    home_score: int = Field(ge=0)
    away_score: int = Field(ge=0)
    probability: float = Field(ge=0, le=1)


class SportsGameForecastV1(_Contract):
    schema_version: Literal["ASP_SPORTS_GAME_FORECAST_V1"]
    forecast_id: str
    snapshot_type: Literal["EARLY", "CURRENT", "FINAL_PREGAME"]
    sport: Literal["NFL"]
    league: Literal["NFL"]
    season: Literal[2026]
    season_type: Literal["REG", "POST"]
    week: int = Field(ge=1, le=25)
    event_date: date
    event_id: str = Field(min_length=1, max_length=120)
    source_event_id: str = Field(min_length=1, max_length=120)
    scheduled_start_utc: datetime
    schedule_as_of_utc: datetime
    forecast_cutoff_utc: datetime
    source_cutoff_utc: datetime
    available_as_of_utc: datetime
    snapshot_version: int = Field(gt=0)
    home_team: SportsTeamRef
    away_team: SportsTeamRef
    engine: SportsEngine
    features: SportsFeatureEvidence
    readiness: SportsReadiness
    status: Literal["RESEARCH_SHADOW"]
    promotion_status: Literal["NOT_PROMOTED"]
    execution_status: Literal["NOT_EXECUTABLE"]
    research_shadow_only: Literal[True]
    model_promotion_allowed: Literal[False]
    execution_allowed: Literal[False]
    expected_home_score: float = Field(ge=0)
    expected_away_score: float = Field(ge=0)
    home_win_probability: float = Field(ge=0, le=1)
    away_win_probability: float = Field(ge=0, le=1)
    tie_probability: float = Field(ge=0, le=1)
    joint_score_distribution: list[SportsJointScoreCell] = Field(min_length=1)
    score_margin_distribution: list[SportsMarginCell] = Field(min_length=1)
    total_score_distribution: list[SportsTotalCell] = Field(min_length=1)
    spread_cover_probabilities: list[SportsSpreadProbability]
    over_under_probabilities: list[SportsTotalProbability]
    most_likely_scores: list[SportsLikelyScore] = Field(min_length=1)
    prediction_interval: SportsPredictionInterval
    uncertainty: SportsUncertainty
    simulator: SportsSimulator

    @model_validator(mode="after")
    def nfl_shadow_invariants(self) -> "SportsGameForecastV1":
        if self.engine.model_manifest_sha256 != NFL_MODEL_MANIFEST_SHA256:
            raise ValueError("NFL M1 model manifest is not the frozen authority")
        if self.engine.model_binary_sha256 != NFL_MODEL_BINARY_SHA256:
            raise ValueError("NFL M1 model binary is not the frozen authority")
        if self.engine.training_cutoff != NFL_TRAINING_CUTOFF:
            raise ValueError("NFL M1 training cutoff changed")
        if not date(2026, 8, 1) <= self.event_date <= date(2027, 2, 28):
            raise ValueError("event date is outside the governed 2026 NFL season horizon")
        eastern_date = self.scheduled_start_utc.astimezone(ZoneInfo("America/New_York")).date()
        if eastern_date != self.event_date:
            raise ValueError("event date does not reconcile to scheduled kickoff Eastern date")
        if self.features.readiness != self.readiness:
            raise ValueError("feature readiness differs from forecast readiness")
        if self.home_team.team_id == self.away_team.team_id:
            raise ValueError("home and away canonical team identities collide")
        if self.home_team.source_team_id == self.away_team.source_team_id:
            raise ValueError("home and away source team identities collide")
        if self.schedule_as_of_utc > self.source_cutoff_utc:
            raise ValueError("schedule evidence is later than the source cutoff")
        if self.source_cutoff_utc > self.forecast_cutoff_utc:
            raise ValueError("source cutoff cannot be after the forecast cutoff")
        if self.forecast_cutoff_utc > self.available_as_of_utc:
            raise ValueError("forecast cannot be available before its cutoff")
        if self.available_as_of_utc >= self.scheduled_start_utc:
            raise ValueError("pregame forecast availability must precede kickoff")
        if self.forecast_cutoff_utc >= self.scheduled_start_utc:
            raise ValueError("pregame forecast cutoff must precede kickoff")
        if (
            self.snapshot_type == "FINAL_PREGAME"
            and self.forecast_cutoff_utc > self.scheduled_start_utc - timedelta(minutes=15)
        ):
            raise ValueError("FINAL_PREGAME cutoff must be no later than 15 minutes before kickoff")
        probability_groups = (
            self.joint_score_distribution,
            self.score_margin_distribution,
            self.total_score_distribution,
        )
        for cells in probability_groups:
            if abs(sum(cell.probability for cell in cells) - 1.0) > 1e-6:
                raise ValueError("forecast distribution does not sum to one")
        if abs(self.home_win_probability + self.away_win_probability + self.tie_probability - 1.0) > 1e-6:
            raise ValueError("win/tie probabilities do not sum to one")
        for row in self.spread_cover_probabilities:
            if abs(row.home_cover_probability + row.away_cover_probability + row.push_probability - 1.0) > 1e-6:
                raise ValueError("spread probabilities do not sum to one")
        for row in self.over_under_probabilities:
            if abs(row.over_probability + row.under_probability + row.push_probability - 1.0) > 1e-6:
                raise ValueError("total probabilities do not sum to one")
        joint_home = sum(
            cell.probability for cell in self.joint_score_distribution
            if cell.home_score > cell.away_score
        )
        joint_away = sum(
            cell.probability for cell in self.joint_score_distribution
            if cell.away_score > cell.home_score
        )
        joint_tie = sum(
            cell.probability for cell in self.joint_score_distribution
            if cell.away_score == cell.home_score
        )
        expected_home = sum(cell.home_score * cell.probability for cell in self.joint_score_distribution)
        expected_away = sum(cell.away_score * cell.probability for cell in self.joint_score_distribution)
        reconciliations = (
            (joint_home, self.home_win_probability, "home-win"),
            (joint_away, self.away_win_probability, "away-win"),
            (joint_tie, self.tie_probability, "tie"),
            (expected_home, self.expected_home_score, "expected home score"),
            (expected_away, self.expected_away_score, "expected away score"),
        )
        for derived, declared, label in reconciliations:
            if abs(derived - declared) > 1e-6:
                raise ValueError(f"{label} does not reconcile to the joint distribution")
        joint_margins: dict[int, float] = {}
        joint_totals: dict[int, float] = {}
        for cell in self.joint_score_distribution:
            margin = cell.home_score - cell.away_score
            total = cell.home_score + cell.away_score
            joint_margins[margin] = joint_margins.get(margin, 0.0) + cell.probability
            joint_totals[total] = joint_totals.get(total, 0.0) + cell.probability
        declared_margins = {cell.home_minus_away: cell.probability for cell in self.score_margin_distribution}
        declared_totals = {cell.total_score: cell.probability for cell in self.total_score_distribution}
        if set(declared_margins) != set(joint_margins) or any(
            abs(declared_margins[key] - value) > 1e-6 for key, value in joint_margins.items()
        ):
            raise ValueError("margin distribution does not reconcile to the joint distribution")
        if set(declared_totals) != set(joint_totals) or any(
            abs(declared_totals[key] - value) > 1e-6 for key, value in joint_totals.items()
        ):
            raise ValueError("total distribution does not reconcile to the joint distribution")
        for row in self.spread_cover_probabilities:
            expected_home_cover = sum(
                probability for margin, probability in joint_margins.items()
                if margin + row.line > 0
            )
            expected_away_cover = sum(
                probability for margin, probability in joint_margins.items()
                if margin + row.line < 0
            )
            expected_push = sum(
                probability for margin, probability in joint_margins.items()
                if margin + row.line == 0
            )
            if any(abs(actual - expected) > 1e-6 for actual, expected in (
                (row.home_cover_probability, expected_home_cover),
                (row.away_cover_probability, expected_away_cover),
                (row.push_probability, expected_push),
            )):
                raise ValueError("spread probabilities do not reconcile to the joint distribution")
        for row in self.over_under_probabilities:
            expected_over = sum(
                probability for total, probability in joint_totals.items()
                if total - row.line > 0
            )
            expected_under = sum(
                probability for total, probability in joint_totals.items()
                if total - row.line < 0
            )
            expected_push = sum(
                probability for total, probability in joint_totals.items()
                if total - row.line == 0
            )
            if any(abs(actual - expected) > 1e-6 for actual, expected in (
                (row.over_probability, expected_over),
                (row.under_probability, expected_under),
                (row.push_probability, expected_push),
            )):
                raise ValueError("total probabilities do not reconcile to the joint distribution")
        expected_most = sorted(
            self.joint_score_distribution,
            key=lambda row: (-row.probability, row.home_score, row.away_score),
        )[: len(self.most_likely_scores)]
        if len(self.most_likely_scores) != min(5, len(self.joint_score_distribution)):
            raise ValueError("most-likely score count differs from the joint distribution")
        if any(
            actual.home_score != expected.home_score
            or actual.away_score != expected.away_score
            or abs(actual.probability - expected.probability) > 1e-6
            for actual, expected in zip(self.most_likely_scores, expected_most, strict=True)
        ):
            raise ValueError("most-likely scores do not match the top joint cells")
        return self


class SportsForecastBundleV1(_Contract):
    schema_version: Literal["ASP_SPORTS_FORECAST_EXCHANGE_V1"]
    bundle_id: str
    sport: Literal["NFL"]
    league: Literal["NFL"]
    season: Literal[2026]
    slate_date: date
    generated_at_utc: datetime
    status: Literal["RESEARCH_SHADOW"]
    research_shadow_only: Literal[True]
    model_promotion_allowed: Literal[False]
    execution_allowed: Literal[False]
    parlays_allowed: Literal[False]
    forecasts: list[SportsGameForecastV1] = Field(min_length=1)

    @model_validator(mode="after")
    def consistent_children(self) -> "SportsForecastBundleV1":
        identities: set[tuple[str, str]] = set()
        for forecast in self.forecasts:
            if forecast.event_date != self.slate_date:
                raise ValueError("forecast event date differs from bundle slate date")
            if self.generated_at_utc < forecast.available_as_of_utc:
                raise ValueError("bundle generation predates child availability")
            if self.generated_at_utc >= forecast.scheduled_start_utc:
                raise ValueError("bundle was published at or after kickoff")
            if (
                forecast.snapshot_type == "FINAL_PREGAME"
                and (
                    forecast.available_as_of_utc
                    > forecast.scheduled_start_utc - timedelta(minutes=15)
                    or self.generated_at_utc
                    > forecast.scheduled_start_utc - timedelta(minutes=15)
                )
            ):
                raise ValueError(
                    "FINAL_PREGAME availability/publication must be no later than 15 minutes before kickoff"
                )
            identity = (forecast.event_id, forecast.snapshot_type)
            if identity in identities:
                raise ValueError("duplicate event/snapshot forecast in bundle")
            identities.add(identity)
        return self


class SportsExpectedRecordCounts(_Contract):
    forecasts: int = Field(ge=0)


class SportsConsumerImportFixtureV1(_Contract):
    schema_version: Literal["ASP_SPORTS_FORECAST_IMPORT_FIXTURE_V1"]
    bundle_logical_uri: str
    bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fixture_id: str
    research_shadow_only: Literal[True]
    execution_allowed: Literal[False]
    expected_contract_versions: dict[str, str]
    expected_record_counts: SportsExpectedRecordCounts
    validation_instructions_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class NFLShadowBundleIndexV1(_Contract):
    """Strict mutable locator; each addressed bundle remains independently authoritative."""

    schema_version: Literal["ASP_NFL_2026_RESEARCH_SHADOW_INDEX_V1"]
    sport: Literal["NFL"]
    league: Literal["NFL"]
    season: Literal[2026]
    status: Literal["RESEARCH_SHADOW"]
    research_shadow_only: Literal[True]
    bundle_contract_version: Literal["ASP_SPORTS_FORECAST_EXCHANGE_V1"]
    forecast_contract_version: Literal["ASP_SPORTS_GAME_FORECAST_V1"]
    model_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_binary_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    feature_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    updated_at_utc: datetime
    bundle_count: int = Field(gt=0)
    bundle_sha256s: list[str] = Field(min_length=1, alias="bundleSha256s")
    bundle_list_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def exact_navigation_authority(self) -> "NFLShadowBundleIndexV1":
        if self.model_manifest_sha256 != NFL_MODEL_MANIFEST_SHA256:
            raise ValueError("NFL index model manifest differs from frozen authority")
        if self.model_binary_sha256 != NFL_MODEL_BINARY_SHA256:
            raise ValueError("NFL index model binary differs from frozen authority")
        if self.feature_contract_sha256 != NFL_FEATURE_CONTRACT_SHA256:
            raise ValueError("NFL index feature contract differs from frozen authority")
        if self.bundle_count != len(self.bundle_sha256s):
            raise ValueError("NFL index bundle count does not reconcile")
        if self.bundle_sha256s != sorted(set(self.bundle_sha256s)):
            raise ValueError("NFL index bundle addresses must be sorted and unique")
        if any(len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value)
               for value in self.bundle_sha256s):
            raise ValueError("NFL index contains a malformed bundle address")
        if canonical_sha256(self.bundle_sha256s) != self.bundle_list_sha256:
            raise ValueError("NFL index list hash does not bind the ordered bundle addresses")
        return self

    @property
    def current_addresses(self) -> tuple[str, ...]:
        return tuple(self.bundle_sha256s)

    @property
    def historical_addresses(self) -> tuple[str, ...]:
        return ()


class NFLShadowBundleIndexV2(_Contract):
    """Unambiguous mutable locator separating current and superseded containers."""

    schema_version: Literal["ASP_NFL_2026_RESEARCH_SHADOW_INDEX_V2"]
    sport: Literal["NFL"]
    league: Literal["NFL"]
    season: Literal[2026]
    status: Literal["RESEARCH_SHADOW"]
    research_shadow_only: Literal[True]
    bundle_contract_version: Literal["ASP_SPORTS_FORECAST_EXCHANGE_V1"]
    forecast_contract_version: Literal["ASP_SPORTS_GAME_FORECAST_V1"]
    model_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_binary_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    feature_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    updated_at_utc: datetime
    current_bundle_count: int = Field(gt=0)
    current_bundle_sha256s: list[str] = Field(min_length=1, alias="currentBundleSha256s")
    current_bundle_list_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    historical_bundle_count: int = Field(ge=0)
    historical_bundle_sha256s: list[str] = Field(alias="historicalBundleSha256s")
    historical_bundle_list_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def exact_navigation_authority(self) -> "NFLShadowBundleIndexV2":
        if self.model_manifest_sha256 != NFL_MODEL_MANIFEST_SHA256:
            raise ValueError("NFL index model manifest differs from frozen authority")
        if self.model_binary_sha256 != NFL_MODEL_BINARY_SHA256:
            raise ValueError("NFL index model binary differs from frozen authority")
        if self.feature_contract_sha256 != NFL_FEATURE_CONTRACT_SHA256:
            raise ValueError("NFL index feature contract differs from frozen authority")
        for label, declared_count, addresses, declared_sha in (
            ("current", self.current_bundle_count, self.current_bundle_sha256s,
             self.current_bundle_list_sha256),
            ("historical", self.historical_bundle_count, self.historical_bundle_sha256s,
             self.historical_bundle_list_sha256),
        ):
            if declared_count != len(addresses):
                raise ValueError(f"NFL {label} bundle count does not reconcile")
            if addresses != sorted(set(addresses)):
                raise ValueError(f"NFL {label} bundle addresses must be sorted and unique")
            if any(len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value)
                   for value in addresses):
                raise ValueError(f"NFL {label} index contains a malformed bundle address")
            if canonical_sha256(addresses) != declared_sha:
                raise ValueError(f"NFL {label} list hash does not bind its ordered addresses")
        if set(self.current_bundle_sha256s) & set(self.historical_bundle_sha256s):
            raise ValueError("NFL current and historical bundle authorities overlap")
        return self

    @property
    def current_addresses(self) -> tuple[str, ...]:
        return tuple(self.current_bundle_sha256s)

    @property
    def historical_addresses(self) -> tuple[str, ...]:
        return tuple(self.historical_bundle_sha256s)


__all__ = [
    "NFL_ENGINE_ID", "NFL_FEATURE_CONTRACT_SHA256", "NFL_MODEL_BINARY_SHA256",
    "NFL_MODEL_MANIFEST_SHA256", "NFL_SHADOW_INDEX_CONTRACT",
    "NFL_SHADOW_INDEX_V2_CONTRACT",
    "NFL_TRAINING_CUTOFF", "SPORTS_BUNDLE_CONTRACT", "SPORTS_CONTRACT_VERSIONS",
    "SPORTS_FIXTURE_CONTRACT", "SPORTS_FORECAST_CONTRACT",
    "NFLShadowBundleIndexV1", "NFLShadowBundleIndexV2", "SportsConsumerImportFixtureV1",
    "SportsForecastBundleV1", "SportsGameForecastV1",
]

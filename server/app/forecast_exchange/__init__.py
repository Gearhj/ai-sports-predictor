"""Consumer side of the Model Studio daily forecast exchange.

This package imports immutable, research-shadow-only forecast bundles produced
by ASP Model Studio. It never fits a model, never grants promotion or execution
authority, and never places a wager. Odds are joined separately from the
existing live provider; this package fabricates no price, edge, or ROI.
"""

from app.forecast_exchange.contracts import (
    CONTRACT_VERSIONS,
    BetCandidateV1,
    ConsumerImportFixtureV1,
    DailyForecastBundleV1,
    GameForecastExportV1,
    MarketQuoteV1,
)
from app.forecast_exchange.importer import (
    ForecastBundleImportError,
    ImportedForecastBundle,
    ImportedSportsForecastBundle,
    SportsIdentityInconsistencyError,
    VerifiedNFLShadowBundleIndex,
    verify_and_load_any_bundle,
    verify_and_load_bundle,
    verify_and_load_sports_bundle,
    verify_nfl_shadow_bundle_index,
)
from app.forecast_exchange.sports_contracts import (
    SPORTS_CONTRACT_VERSIONS,
    SportsConsumerImportFixtureV1,
    SportsForecastBundleV1,
    SportsGameForecastV1,
    NFLShadowBundleIndexV1,
    NFLShadowBundleIndexV2,
)

__all__ = [
    "CONTRACT_VERSIONS",
    "BetCandidateV1",
    "ConsumerImportFixtureV1",
    "DailyForecastBundleV1",
    "ForecastBundleImportError",
    "GameForecastExportV1",
    "ImportedForecastBundle",
    "ImportedSportsForecastBundle",
    "VerifiedNFLShadowBundleIndex",
    "MarketQuoteV1",
    "SPORTS_CONTRACT_VERSIONS",
    "SportsConsumerImportFixtureV1",
    "SportsForecastBundleV1",
    "SportsGameForecastV1",
    "SportsIdentityInconsistencyError",
    "NFLShadowBundleIndexV1",
    "NFLShadowBundleIndexV2",
    "verify_and_load_any_bundle",
    "verify_and_load_bundle",
    "verify_and_load_sports_bundle",
    "verify_nfl_shadow_bundle_index",
]

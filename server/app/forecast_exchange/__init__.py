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
    verify_and_load_bundle,
)

__all__ = [
    "CONTRACT_VERSIONS",
    "BetCandidateV1",
    "ConsumerImportFixtureV1",
    "DailyForecastBundleV1",
    "ForecastBundleImportError",
    "GameForecastExportV1",
    "ImportedForecastBundle",
    "MarketQuoteV1",
    "verify_and_load_bundle",
]

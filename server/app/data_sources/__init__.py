"""Replaceable external source adapters and explicit demo fixtures."""

from .base import SourceError
from .fixtures import DemoSportsSource
from .mlb import MLBStatsAPIAdapter
from .odds import TheOddsAPIAdapter
from .request_policy import (
    CircuitOpenError,
    ConservativeRequestExecutor,
    ConservativeRequestPolicy,
    RequestAccounting,
    RequestCapExceeded,
)
from .statcast import BaseballSavantStatcastAdapter
from .weather import OpenMeteoAdapter

__all__ = [
    "DemoSportsSource",
    "MLBStatsAPIAdapter",
    "OpenMeteoAdapter",
    "BaseballSavantStatcastAdapter",
    "SourceError",
    "TheOddsAPIAdapter",
    "CircuitOpenError",
    "ConservativeRequestExecutor",
    "ConservativeRequestPolicy",
    "RequestAccounting",
    "RequestCapExceeded",
]

"""Frozen, named feature contracts for Gate 3 model development.

The extractor never silently drops missing values.  Every numeric feature has a
documented neutral value and a paired missingness indicator in the output
vector.  The ordered output names and feature metadata are checksum-addressed so
training and inference can fail closed on schema drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from math import isfinite
from typing import Mapping, Sequence

import numpy as np


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    family: str
    neutral_value: float
    missing_indicator: bool = True
    recent_window_days: int | None = None


@dataclass(frozen=True)
class ExtractedFeatures:
    names: tuple[str, ...]
    values: tuple[float, ...]
    missing: tuple[str, ...]
    families: tuple[str, ...]

    def as_array(self) -> np.ndarray:
        return np.asarray(self.values, dtype=float)


@dataclass(frozen=True)
class FrozenFeatureSchema:
    version: str
    features: tuple[FeatureSpec, ...]

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("Feature schema version is required")
        names = [feature.name for feature in self.features]
        if not names or len(set(names)) != len(names):
            raise ValueError("Feature names must be non-empty and unique")
        if any(not item.family.strip() for item in self.features):
            raise ValueError("Every feature must have a family")
        if any(not isfinite(float(item.neutral_value)) for item in self.features):
            raise ValueError("Neutral values must be finite")

    @property
    def output_names(self) -> tuple[str, ...]:
        names: list[str] = []
        for feature in self.features:
            names.append(feature.name)
            if feature.missing_indicator:
                names.append(f"{feature.name}__missing")
        return tuple(names)

    @property
    def output_families(self) -> tuple[str, ...]:
        families: list[str] = []
        for feature in self.features:
            families.append(feature.family)
            if feature.missing_indicator:
                families.append(feature.family)
        return tuple(families)

    @property
    def schema_hash(self) -> str:
        payload = {
            "version": self.version,
            "features": [
                {
                    "name": item.name,
                    "family": item.family,
                    "neutral_value": item.neutral_value,
                    "missing_indicator": item.missing_indicator,
                    "recent_window_days": item.recent_window_days,
                }
                for item in self.features
            ],
            "output_names": self.output_names,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return sha256(canonical.encode("utf-8")).hexdigest()

    def extract(self, values: Mapping[str, object]) -> ExtractedFeatures:
        output: list[float] = []
        missing: list[str] = []
        for feature in self.features:
            value = _finite_float(values.get(feature.name))
            is_missing = value is None
            if is_missing:
                value = float(feature.neutral_value)
                missing.append(feature.name)
            output.append(value)
            if feature.missing_indicator:
                output.append(float(is_missing))
        return ExtractedFeatures(
            names=self.output_names,
            values=tuple(output),
            missing=tuple(missing),
            families=self.output_families,
        )

    def matrix(self, rows: Sequence[Mapping[str, object]]) -> np.ndarray:
        if not rows:
            raise ValueError("At least one feature row is required")
        return np.asarray([self.extract(row).values for row in rows], dtype=float)

    def without(
        self,
        *,
        families: Sequence[str] = (),
        names: Sequence[str] = (),
        version_suffix: str = "ablation",
    ) -> FrozenFeatureSchema:
        excluded_families = frozenset(families)
        excluded_names = frozenset(names)
        kept = tuple(
            feature
            for feature in self.features
            if feature.family not in excluded_families and feature.name not in excluded_names
        )
        if not kept:
            raise ValueError("An ablation cannot remove every feature")
        return FrozenFeatureSchema(f"{self.version}+{version_suffix}", kept)


def _finite_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return None
    return converted if isfinite(converted) else None


GATE3_FEATURE_SCHEMA = FrozenFeatureSchema(
    version="mlb_pregame_gate3_v1",
    features=(
        FeatureSpec("home_rating", "rating", 1500.0),
        FeatureSpec("away_rating", "rating", 1500.0),
        FeatureSpec("home_runs_per_game", "team", 4.4),
        FeatureSpec("away_runs_per_game", "team", 4.4),
        FeatureSpec("home_runs_allowed_per_game", "team", 4.4),
        FeatureSpec("away_runs_allowed_per_game", "team", 4.4),
        FeatureSpec("home_ops", "team", 0.720),
        FeatureSpec("away_ops", "team", 0.720),
        FeatureSpec("home_recent_runs", "recent", 4.4, recent_window_days=14),
        FeatureSpec("away_recent_runs", "recent", 4.4, recent_window_days=14),
        FeatureSpec("home_starter_era", "starter", 4.2),
        FeatureSpec("away_starter_era", "starter", 4.2),
        FeatureSpec("home_starter_xera", "starter", 4.2),
        FeatureSpec("away_starter_xera", "starter", 4.2),
        FeatureSpec("home_bullpen_era", "bullpen", 4.2),
        FeatureSpec("away_bullpen_era", "bullpen", 4.2),
        FeatureSpec("home_bullpen_xera", "bullpen", 4.2),
        FeatureSpec("away_bullpen_xera", "bullpen", 4.2),
        FeatureSpec("home_bullpen_availability", "bullpen", 0.5),
        FeatureSpec("away_bullpen_availability", "bullpen", 0.5),
        FeatureSpec("home_lineup_xwoba", "lineup", 0.320),
        FeatureSpec("away_lineup_xwoba", "lineup", 0.320),
        FeatureSpec("home_lineup_talent_coverage", "lineup", 0.0),
        FeatureSpec("away_lineup_talent_coverage", "lineup", 0.0),
        FeatureSpec("home_statcast_xwoba", "statcast", 0.320),
        FeatureSpec("away_statcast_xwoba", "statcast", 0.320),
        FeatureSpec("home_statcast_barrel_rate", "statcast", 0.080),
        FeatureSpec("away_statcast_barrel_rate", "statcast", 0.080),
        FeatureSpec("home_statcast_hard_hit_rate", "statcast", 0.380),
        FeatureSpec("away_statcast_hard_hit_rate", "statcast", 0.380),
        FeatureSpec("home_statcast_average_exit_velocity", "statcast", 88.0),
        FeatureSpec("away_statcast_average_exit_velocity", "statcast", 88.0),
        FeatureSpec("temperature_f", "context", 70.0),
        FeatureSpec("wind_speed_mph", "context", 0.0),
        FeatureSpec("wind_out_mph", "context", 0.0),
        FeatureSpec("precipitation_probability", "context", 0.0),
        FeatureSpec("park_run_factor", "context", 1.0),
        FeatureSpec("home_days_since_previous_game", "schedule", 1.0),
        FeatureSpec("away_days_since_previous_game", "schedule", 1.0),
        FeatureSpec("home_travel_distance_miles", "schedule", 0.0),
        FeatureSpec("away_travel_distance_miles", "schedule", 0.0),
        FeatureSpec("doubleheader_game_number", "schedule", 1.0),
        FeatureSpec("feature_completeness", "readiness", 0.0),
        FeatureSpec("sample_reliability", "readiness", 0.0),
        FeatureSpec("minutes_to_first_pitch", "lifecycle", 720.0),
        FeatureSpec("lineups_confirmed", "lifecycle", 0.0),
        FeatureSpec("starters_confirmed", "lifecycle", 0.0),
    ),
)

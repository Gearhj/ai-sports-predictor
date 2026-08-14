"""Frozen, content-addressed feature contract for Gate 3A Stage A2.

The A2 contract deliberately lives outside the training schema.  Reconstruction
emits nullable, human-auditable values in this order; a later governed adapter
may neutral-fill them and append the paired missingness indicators described by
``output_names``.  Keeping the contract pure makes it safe to import from
offline reconstruction and migration tooling without constructing a provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from math import isfinite


A2_SCHEMA_VERSION = "mlb_historical_pregame_a2_v2"


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@dataclass(frozen=True, slots=True)
class A2FeatureSpec:
    """One ordered A2 value and its governed missingness behavior."""

    name: str
    family: str
    neutral_value: float
    recent_window_days: int | None = None
    tier_b_only: bool = False

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.family.strip():
            raise ValueError("A2 feature name and family are required")
        if not isfinite(float(self.neutral_value)):
            raise ValueError("A2 feature neutral_value must be finite")
        if self.recent_window_days is not None and self.recent_window_days <= 0:
            raise ValueError("A2 recent windows must be positive")

    @property
    def missing_indicator_name(self) -> str:
        return f"{self.name}__missing"

    def definition(self) -> dict[str, object]:
        return {
            "family": self.family,
            "name": self.name,
            "neutral_value": float(self.neutral_value),
            "recent_window_days": self.recent_window_days,
            "tier_b_only": self.tier_b_only,
        }


@dataclass(frozen=True, slots=True)
class A2FrozenFeatureSchema:
    """Immutable ordered A2 schema with a reproducible content identity."""

    version: str
    features: tuple[A2FeatureSpec, ...]

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("A2 schema version is required")
        names = tuple(item.name for item in self.features)
        if not names or len(names) != len(set(names)):
            raise ValueError("A2 feature names must be non-empty and unique")

    @property
    def value_names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.features)

    @property
    def output_names(self) -> tuple[str, ...]:
        return tuple(
            name
            for item in self.features
            for name in (item.name, item.missing_indicator_name)
        )

    @property
    def families(self) -> tuple[str, ...]:
        return tuple(sorted({item.family for item in self.features}))

    def definition(self) -> dict[str, object]:
        return {
            "features": [item.definition() for item in self.features],
            "output_names": list(self.output_names),
            "version": self.version,
        }

    @property
    def schema_hash(self) -> str:
        return sha256(_canonical_json(self.definition()).encode("utf-8")).hexdigest()


def _side_features(side: str) -> tuple[A2FeatureSpec, ...]:
    team = (
        A2FeatureSpec(f"{side}_runs_per_game_season", "team", 4.4),
        A2FeatureSpec(f"{side}_runs_allowed_per_game_season", "team", 4.4),
        A2FeatureSpec(f"{side}_team_sample_season", "team", 0.0),
        A2FeatureSpec(f"{side}_runs_per_game_14d", "recent", 4.4, 14),
        A2FeatureSpec(f"{side}_runs_allowed_per_game_14d", "recent", 4.4, 14),
        A2FeatureSpec(f"{side}_team_sample_14d", "recent", 0.0, 14),
        A2FeatureSpec(f"{side}_runs_per_game_30d", "recent", 4.4, 30),
        A2FeatureSpec(f"{side}_runs_allowed_per_game_30d", "recent", 4.4, 30),
        A2FeatureSpec(f"{side}_team_sample_30d", "recent", 0.0, 30),
        A2FeatureSpec(f"{side}_offense_true_talent", "talent", 4.4),
        A2FeatureSpec(f"{side}_run_prevention_true_talent", "talent", 4.4),
        A2FeatureSpec(f"{side}_offense_recent_delta_14d", "talent", 0.0, 14),
        A2FeatureSpec(f"{side}_offense_recent_delta_30d", "talent", 0.0, 30),
        A2FeatureSpec(f"{side}_run_prevention_recent_delta_14d", "talent", 0.0, 14),
        A2FeatureSpec(f"{side}_run_prevention_recent_delta_30d", "talent", 0.0, 30),
        A2FeatureSpec(f"{side}_team_reliability", "talent", 0.0),
        A2FeatureSpec(f"{side}_elo_rating", "rating", 1500.0),
        A2FeatureSpec(f"{side}_elo_games", "rating", 0.0),
    )
    player_form = tuple(
        feature
        for label, days in (("season", None), ("14d", 14), ("30d", 30))
        for feature in (
            A2FeatureSpec(
                f"{side}_batting_pa_{label}", "hitting", 0.0, days
            ),
            A2FeatureSpec(
                f"{side}_batting_average_{label}", "hitting", 0.248, days
            ),
            A2FeatureSpec(
                f"{side}_batting_on_base_rate_{label}", "hitting", 0.320, days
            ),
            A2FeatureSpec(
                f"{side}_batting_slugging_{label}", "hitting", 0.400, days
            ),
            A2FeatureSpec(
                f"{side}_batting_walk_rate_{label}", "hitting", 0.085, days
            ),
            A2FeatureSpec(
                f"{side}_batting_strikeout_rate_{label}", "hitting", 0.225, days
            ),
            A2FeatureSpec(
                f"{side}_batting_home_run_rate_{label}", "hitting", 0.030, days
            ),
            A2FeatureSpec(
                f"{side}_batting_average_posterior_{label}", "hitting", 0.248, days
            ),
            A2FeatureSpec(
                f"{side}_batting_on_base_rate_posterior_{label}",
                "hitting",
                0.320,
                days,
            ),
            A2FeatureSpec(
                f"{side}_batting_slugging_posterior_{label}",
                "hitting",
                0.400,
                days,
            ),
            A2FeatureSpec(
                f"{side}_batting_walk_rate_posterior_{label}",
                "hitting",
                0.085,
                days,
            ),
            A2FeatureSpec(
                f"{side}_batting_strikeout_rate_posterior_{label}",
                "hitting",
                0.225,
                days,
            ),
            A2FeatureSpec(
                f"{side}_batting_home_run_rate_posterior_{label}",
                "hitting",
                0.030,
                days,
            ),
            A2FeatureSpec(
                f"{side}_batting_rate_reliability_{label}", "hitting", 0.0, days
            ),
            A2FeatureSpec(
                f"{side}_pitching_bf_{label}", "pitching", 0.0, days
            ),
            A2FeatureSpec(
                f"{side}_pitching_era_{label}", "pitching", 4.2, days
            ),
            A2FeatureSpec(
                f"{side}_pitching_hit_rate_allowed_{label}",
                "pitching",
                0.225,
                days,
            ),
            A2FeatureSpec(
                f"{side}_pitching_walk_rate_allowed_{label}",
                "pitching",
                0.085,
                days,
            ),
            A2FeatureSpec(
                f"{side}_pitching_strikeout_rate_{label}",
                "pitching",
                0.225,
                days,
            ),
            A2FeatureSpec(
                f"{side}_pitching_home_run_rate_allowed_{label}",
                "pitching",
                0.030,
                days,
            ),
            A2FeatureSpec(
                f"{side}_pitching_era_posterior_{label}",
                "pitching",
                4.2,
                days,
            ),
            A2FeatureSpec(
                f"{side}_pitching_hit_rate_allowed_posterior_{label}",
                "pitching",
                0.225,
                days,
            ),
            A2FeatureSpec(
                f"{side}_pitching_walk_rate_allowed_posterior_{label}",
                "pitching",
                0.085,
                days,
            ),
            A2FeatureSpec(
                f"{side}_pitching_strikeout_rate_posterior_{label}",
                "pitching",
                0.225,
                days,
            ),
            A2FeatureSpec(
                f"{side}_pitching_home_run_rate_allowed_posterior_{label}",
                "pitching",
                0.030,
                days,
            ),
            A2FeatureSpec(
                f"{side}_pitching_rate_reliability_{label}",
                "pitching",
                0.0,
                days,
            ),
        )
    )
    bullpen = tuple(
        A2FeatureSpec(f"{side}_bullpen_{metric}_{days}d", "bullpen", neutral, days)
        for days in (1, 2, 3)
        for metric, neutral in (("pitches", 0.0), ("outs", 0.0), ("innings", 0.0))
    ) + (
        A2FeatureSpec(f"{side}_bullpen_consecutive_relievers", "bullpen", 0.0),
        A2FeatureSpec(f"{side}_bullpen_pool_count", "bullpen", 0.0),
        A2FeatureSpec(f"{side}_bullpen_fatigue", "bullpen", 0.0),
        A2FeatureSpec(f"{side}_bullpen_quality_posterior", "bullpen", 4.2),
        A2FeatureSpec(f"{side}_bullpen_quality_reliability", "bullpen", 0.0),
    )
    schedule = (
        A2FeatureSpec(f"{side}_hours_since_previous_game", "schedule", 24.0),
        A2FeatureSpec(f"{side}_days_since_previous_game", "schedule", 1.0),
        A2FeatureSpec(f"{side}_travel_miles", "schedule", 0.0),
        A2FeatureSpec(f"{side}_previous_game_innings", "schedule", 9.0),
    )
    context = (
        A2FeatureSpec(f"{side}_starter_prior_era", "target_context", 4.2, tier_b_only=True),
        A2FeatureSpec(
            f"{side}_starter_quality_posterior", "target_context", 4.2, tier_b_only=True
        ),
        A2FeatureSpec(
            f"{side}_starter_quality_reliability",
            "target_context",
            0.0,
            tier_b_only=True,
        ),
        A2FeatureSpec(
            f"{side}_starter_prior_appearance_count",
            "target_context",
            0.0,
            tier_b_only=True,
        ),
        A2FeatureSpec(
            f"{side}_starter_days_since_previous_start",
            "target_context",
            5.0,
            tier_b_only=True,
        ),
        A2FeatureSpec(
            f"{side}_starter_previous_start_pitches",
            "target_context",
            90.0,
            tier_b_only=True,
        ),
        A2FeatureSpec(
            f"{side}_starter_previous_start_innings",
            "target_context",
            5.0,
            tier_b_only=True,
        ),
        A2FeatureSpec(
            f"{side}_starter_pitches_7d",
            "target_context",
            0.0,
            7,
            tier_b_only=True,
        ),
        A2FeatureSpec(
            f"{side}_starter_innings_14d",
            "target_context",
            0.0,
            14,
            tier_b_only=True,
        ),
        A2FeatureSpec(
            f"{side}_starter_appearances_14d",
            "target_context",
            0.0,
            14,
            tier_b_only=True,
        ),
        A2FeatureSpec(
            f"{side}_starter_workload_trend_7d_vs_prior_7d",
            "target_context",
            0.0,
            14,
            tier_b_only=True,
        ),
    ) + tuple(
        A2FeatureSpec(
            f"{side}_starter_{metric}_{days}d",
            "target_context",
            0.0,
            days,
            tier_b_only=True,
        )
        for days in (1, 3, 5)
        for metric in ("pitches", "outs")
    ) + (
        A2FeatureSpec(
            f"{side}_lineup_member_count", "target_context", 0.0, tier_b_only=True
        ),
        A2FeatureSpec(
            f"{side}_lineup_prior_appearance_coverage",
            "target_context",
            0.0,
            tier_b_only=True,
        ),
    )
    return team + player_form + bullpen + schedule + context


A2_SCHEMA = A2FrozenFeatureSchema(
    version=A2_SCHEMA_VERSION,
    features=(
        *_side_features("home"),
        *_side_features("away"),
        A2FeatureSpec("elo_expected_home_probability", "rating", 0.5),
        A2FeatureSpec("doubleheader_game_number", "schedule", 1.0),
        A2FeatureSpec("minutes_to_first_pitch", "lifecycle", 360.0),
        A2FeatureSpec("cutoff_offset_hours", "lifecycle", 6.0),
        A2FeatureSpec("starters_confirmed", "lifecycle", 0.0),
        A2FeatureSpec("lineups_confirmed", "lifecycle", 0.0),
    ),
)
A2_SCHEMA_HASH = A2_SCHEMA.schema_hash


__all__ = [
    "A2FeatureSpec",
    "A2FrozenFeatureSchema",
    "A2_SCHEMA",
    "A2_SCHEMA_HASH",
    "A2_SCHEMA_VERSION",
]

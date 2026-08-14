"""Prior-period park factors and provenance-safe weather orientation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
import json
from math import cos, isfinite, pi, sqrt
from typing import Iterable


PARK_FACTOR_VERSION = "mlb_gen2_park_factor_v1"
WEATHER_CONTEXT_VERSION = "mlb_gen2_weather_orientation_v1"


class ContextError(ValueError):
    """Raised when park or weather context violates point-in-time rules."""


def _utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContextError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _checksum(value: object) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def _sha(value: str, field: str) -> str:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value.lower()):
        raise ContextError(f"{field} must be SHA-256")
    return value.lower()


@dataclass(frozen=True)
class ParkGameObservation:
    game_id: str
    venue_id: str
    season: int
    official_date: date
    completed_at: datetime
    eligible_at: datetime
    total_runs: int
    home_runs: int
    evidence_checksum: str

    def __post_init__(self) -> None:
        if not self.game_id or not self.venue_id:
            raise ContextError("game and venue identities are required")
        if self.season < 1876:
            raise ContextError("season is invalid")
        completed = _utc(self.completed_at, "completed_at")
        eligible = _utc(self.eligible_at, "eligible_at")
        if eligible < completed:
            raise ContextError("park observation cannot be eligible before completion")
        if self.total_runs < 0 or self.home_runs < 0:
            raise ContextError("park run counts must be nonnegative")
        _sha(self.evidence_checksum, "evidence_checksum")


@dataclass(frozen=True)
class ParkFactor:
    version: str
    venue_id: str
    target_season: int
    computed_at: datetime
    valid_from: date
    valid_through: date
    prior_seasons: tuple[int, ...]
    games: int
    run_factor: float
    home_run_factor: float
    run_factor_standard_error: float
    home_run_factor_standard_error: float
    prior_strength_games: float
    source_game_ids: tuple[str, ...]
    source_evidence_checksum: str
    identity_checksum: str


def fit_prior_period_park_factors(
    observations: Iterable[ParkGameObservation],
    *,
    target_season: int,
    computed_at: datetime,
    prior_strength_games: float = 50.0,
    minimum_venue_games: int = 10,
) -> tuple[ParkFactor, ...]:
    """Fit target-season factors strictly from earlier seasons and eligible results."""

    boundary = _utc(computed_at, "computed_at")
    if target_season < 1876:
        raise ContextError("target_season is invalid")
    if not isfinite(prior_strength_games) or prior_strength_games <= 0:
        raise ContextError("prior_strength_games must be finite and positive")
    if minimum_venue_games <= 0:
        raise ContextError("minimum_venue_games must be positive")
    semantic: dict[str, ParkGameObservation] = {}
    for row in observations:
        if row.season >= target_season:
            continue
        if _utc(row.completed_at, "completed_at") >= boundary:
            continue
        if _utc(row.eligible_at, "eligible_at") > boundary:
            continue
        prior = semantic.get(row.game_id)
        if prior is not None and prior != row:
            raise ContextError("conflicting duplicate park game")
        semantic[row.game_id] = row
    rows = tuple(sorted(semantic.values(), key=lambda row: (row.official_date, row.game_id)))
    if not rows:
        raise ContextError("no eligible prior-period park observations")
    league_games = len(rows)
    league_run_rate = sum(row.total_runs for row in rows) / league_games
    league_hr_rate = sum(row.home_runs for row in rows) / league_games
    if league_run_rate <= 0 or league_hr_rate <= 0:
        raise ContextError("league park priors require positive run and home-run rates")
    venues: dict[str, list[ParkGameObservation]] = {}
    for row in rows:
        venues.setdefault(row.venue_id, []).append(row)
    factors: list[ParkFactor] = []
    for venue_id, venue_rows in sorted(venues.items()):
        if len(venue_rows) < minimum_venue_games:
            continue
        games = len(venue_rows)
        shrunk_run_rate = (
            sum(row.total_runs for row in venue_rows) + prior_strength_games * league_run_rate
        ) / (games + prior_strength_games)
        shrunk_hr_rate = (
            sum(row.home_runs for row in venue_rows) + prior_strength_games * league_hr_rate
        ) / (games + prior_strength_games)
        run_values = [row.total_runs for row in venue_rows]
        hr_values = [row.home_runs for row in venue_rows]
        run_mean = sum(run_values) / games
        hr_mean = sum(hr_values) / games
        run_variance = sum((value - run_mean) ** 2 for value in run_values) / max(1, games - 1)
        hr_variance = sum((value - hr_mean) ** 2 for value in hr_values) / max(1, games - 1)
        source_ids = tuple(row.game_id for row in venue_rows)
        evidence = _checksum(
            [
                {
                    "game_id": row.game_id,
                    "venue_id": row.venue_id,
                    "season": row.season,
                    "official_date": row.official_date.isoformat(),
                    "completed_at": _utc(row.completed_at, "completed_at").isoformat(),
                    "eligible_at": _utc(row.eligible_at, "eligible_at").isoformat(),
                    "total_runs": row.total_runs,
                    "home_runs": row.home_runs,
                    "evidence_checksum": row.evidence_checksum,
                }
                for row in venue_rows
            ]
        )
        valid_from = date(target_season, 1, 1)
        valid_through = date(target_season, 12, 31)
        prior_seasons = tuple(sorted({row.season for row in venue_rows}))
        run_factor = shrunk_run_rate / league_run_rate
        home_run_factor = shrunk_hr_rate / league_hr_rate
        run_standard_error = sqrt(run_variance / games) / league_run_rate
        home_run_standard_error = sqrt(hr_variance / games) / league_hr_rate
        payload = {
            "version": PARK_FACTOR_VERSION,
            "venue_id": venue_id,
            "target_season": target_season,
            "computed_at": boundary.isoformat(),
            "valid_from": valid_from.isoformat(),
            "valid_through": valid_through.isoformat(),
            "prior_seasons": prior_seasons,
            "games": games,
            "run_factor": run_factor,
            "home_run_factor": home_run_factor,
            "run_factor_standard_error": run_standard_error,
            "home_run_factor_standard_error": home_run_standard_error,
            "prior_strength_games": prior_strength_games,
            "source_game_ids": source_ids,
            "source_evidence_checksum": evidence,
        }
        factors.append(
            ParkFactor(
                version=PARK_FACTOR_VERSION,
                venue_id=venue_id,
                target_season=target_season,
                computed_at=boundary,
                valid_from=valid_from,
                valid_through=valid_through,
                prior_seasons=prior_seasons,
                games=games,
                run_factor=run_factor,
                home_run_factor=home_run_factor,
                run_factor_standard_error=run_standard_error,
                home_run_factor_standard_error=home_run_standard_error,
                prior_strength_games=prior_strength_games,
                source_game_ids=source_ids,
                source_evidence_checksum=evidence,
                identity_checksum=_checksum(payload),
            )
        )
    if not factors:
        raise ContextError("no venue met the governed minimum sample")
    return tuple(factors)


@dataclass(frozen=True)
class WeatherObservation:
    observation_id: str
    game_id: str
    venue_id: str
    observed_at: datetime
    eligible_at: datetime
    temperature_f: float | None
    humidity_pct: float | None
    pressure_hpa: float | None
    precipitation_probability: float | None
    wind_speed_mph: float | None
    wind_from_degrees: float | None
    roof_closed: bool | None
    altitude_ft: float | None
    evidence_checksum: str

    def __post_init__(self) -> None:
        if not self.observation_id or not self.game_id or not self.venue_id:
            raise ContextError("weather identities are required")
        _utc(self.observed_at, "observed_at")
        _utc(self.eligible_at, "eligible_at")
        if _utc(self.eligible_at, "eligible_at") < _utc(self.observed_at, "observed_at"):
            raise ContextError("weather evidence cannot be eligible before observation")
        for field, value in (
            ("temperature_f", self.temperature_f),
            ("humidity_pct", self.humidity_pct),
            ("pressure_hpa", self.pressure_hpa),
            ("precipitation_probability", self.precipitation_probability),
            ("wind_speed_mph", self.wind_speed_mph),
            ("wind_from_degrees", self.wind_from_degrees),
            ("altitude_ft", self.altitude_ft),
        ):
            if value is not None and not isfinite(value):
                raise ContextError(f"{field} must be finite when present")
        if self.humidity_pct is not None and not 0 <= self.humidity_pct <= 100:
            raise ContextError("humidity_pct must be in 0..100")
        if self.precipitation_probability is not None and not 0 <= self.precipitation_probability <= 1:
            raise ContextError("precipitation_probability must be in 0..1")
        if self.wind_speed_mph is not None and self.wind_speed_mph < 0:
            raise ContextError("wind_speed_mph must be nonnegative")
        if self.wind_from_degrees is not None and not 0 <= self.wind_from_degrees < 360:
            raise ContextError("wind_from_degrees must be in [0,360)")
        _sha(self.evidence_checksum, "evidence_checksum")


@dataclass(frozen=True)
class WeatherContext:
    version: str
    observation_id: str
    game_id: str
    venue_id: str
    park_orientation_bearing: float | None
    directional_wind_mph: float | None
    directional_wind_basis: str
    evidence_checksum: str
    identity_checksum: str


@dataclass(frozen=True)
class ParkOrientationEvidence:
    venue_id: str
    home_to_center_bearing: float
    evidence_cutoff: datetime
    evidence_checksum: str
    source_evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.venue_id:
            raise ContextError("park orientation venue identity is required")
        if not isfinite(self.home_to_center_bearing) or not 0 <= self.home_to_center_bearing < 360:
            raise ContextError("park orientation bearing must be in [0,360)")
        _utc(self.evidence_cutoff, "park orientation evidence_cutoff")
        _sha(self.evidence_checksum, "park orientation evidence_checksum")
        if (
            not self.source_evidence_ids
            or any(not identity for identity in self.source_evidence_ids)
            or len(set(self.source_evidence_ids)) != len(self.source_evidence_ids)
        ):
            raise ContextError("park orientation source identities must be nonempty and unique")


def build_weather_context(
    observation: WeatherObservation,
    *,
    cutoff_at: datetime,
    park_orientation: ParkOrientationEvidence | None,
) -> WeatherContext:
    cutoff = _utc(cutoff_at, "cutoff_at")
    if _utc(observation.observed_at, "observed_at") > cutoff:
        raise ContextError("weather observation is after the feature cutoff")
    if _utc(observation.eligible_at, "eligible_at") > cutoff:
        raise ContextError("weather observation is not eligible by the feature cutoff")
    if park_orientation is not None:
        if park_orientation.venue_id != observation.venue_id:
            raise ContextError("park orientation venue does not match weather evidence")
        if _utc(park_orientation.evidence_cutoff, "park orientation evidence_cutoff") > cutoff:
            raise ContextError("park orientation is not eligible by the feature cutoff")
    bearing = park_orientation.home_to_center_bearing if park_orientation else None
    directional: float | None = None
    basis = "UNAVAILABLE_NO_GOVERNED_ORIENTATION_OR_WIND"
    if observation.roof_closed is True:
        directional = 0.0
        basis = "ROOF_CLOSED_ZERO_DIRECTIONAL_WIND"
    elif (
        bearing is not None
        and observation.wind_speed_mph is not None
        and observation.wind_from_degrees is not None
    ):
        wind_to = (observation.wind_from_degrees + 180.0) % 360.0
        directional = observation.wind_speed_mph * cos((wind_to - bearing) * pi / 180.0)
        basis = "GOVERNED_HOME_TO_CENTER_BEARING_PROJECTION"
    observation_payload = {
        "observation_id": observation.observation_id,
        "game_id": observation.game_id,
        "venue_id": observation.venue_id,
        "observed_at": _utc(observation.observed_at, "observed_at").isoformat(),
        "eligible_at": _utc(observation.eligible_at, "eligible_at").isoformat(),
        "temperature_f": observation.temperature_f,
        "humidity_pct": observation.humidity_pct,
        "pressure_hpa": observation.pressure_hpa,
        "precipitation_probability": observation.precipitation_probability,
        "wind_speed_mph": observation.wind_speed_mph,
        "wind_from_degrees": observation.wind_from_degrees,
        "roof_closed": observation.roof_closed,
        "altitude_ft": observation.altitude_ft,
        "evidence_checksum": observation.evidence_checksum,
    }
    orientation_payload = (
        {
            "venue_id": park_orientation.venue_id,
            "home_to_center_bearing": park_orientation.home_to_center_bearing,
            "evidence_cutoff": _utc(
                park_orientation.evidence_cutoff,
                "park orientation evidence_cutoff",
            ).isoformat(),
            "evidence_checksum": park_orientation.evidence_checksum,
            "source_evidence_ids": tuple(sorted(park_orientation.source_evidence_ids)),
        }
        if park_orientation
        else None
    )
    combined_evidence_checksum = _checksum(
        {"weather": observation_payload, "park_orientation": orientation_payload}
    )
    payload = {
        "version": WEATHER_CONTEXT_VERSION,
        "weather": observation_payload,
        "park_orientation": orientation_payload,
        "cutoff_at": cutoff.isoformat(),
        "park_orientation_bearing": bearing,
        "directional_wind_mph": directional,
        "basis": basis,
        "evidence_checksum": combined_evidence_checksum,
    }
    return WeatherContext(
        version=WEATHER_CONTEXT_VERSION,
        observation_id=observation.observation_id,
        game_id=observation.game_id,
        venue_id=observation.venue_id,
        park_orientation_bearing=bearing,
        directional_wind_mph=directional,
        directional_wind_basis=basis,
        evidence_checksum=combined_evidence_checksum,
        identity_checksum=_checksum(payload),
    )

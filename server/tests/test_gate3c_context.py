from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from app.gen2.context import (
    ContextError,
    ParkGameObservation,
    ParkOrientationEvidence,
    WeatherObservation,
    build_weather_context,
    fit_prior_period_park_factors,
)


BOUNDARY = datetime(2025, 3, 1, tzinfo=UTC)


def _park_rows() -> tuple[ParkGameObservation, ...]:
    rows = []
    for index in range(60):
        venue = "hitter-park" if index % 2 == 0 else "pitcher-park"
        runs = 11 if venue == "hitter-park" else 7
        homers = 3 if venue == "hitter-park" else 1
        completed = datetime(2024, 4, 1, tzinfo=UTC) + timedelta(days=index)
        rows.append(
            ParkGameObservation(
                game_id=f"game-{index}",
                venue_id=venue,
                season=2024,
                official_date=completed.date(),
                completed_at=completed,
                eligible_at=completed,
                total_runs=runs,
                home_runs=homers,
                evidence_checksum=f"{index + 1:064x}"[-64:],
            )
        )
    return tuple(rows)


def test_prior_period_park_factor_never_uses_target_season() -> None:
    rows = _park_rows() + (
        ParkGameObservation(
            game_id="future-target-season",
            venue_id="pitcher-park",
            season=2025,
            official_date=date(2025, 4, 1),
            completed_at=datetime(2025, 4, 2, tzinfo=UTC),
            eligible_at=datetime(2025, 4, 2, tzinfo=UTC),
            total_runs=99,
            home_runs=20,
            evidence_checksum="f" * 64,
        ),
    )
    factors = fit_prior_period_park_factors(
        rows,
        target_season=2025,
        computed_at=BOUNDARY,
        minimum_venue_games=20,
    )
    by_venue = {factor.venue_id: factor for factor in factors}
    assert by_venue["hitter-park"].run_factor > 1
    assert by_venue["pitcher-park"].run_factor < 1
    assert all(factor.prior_seasons == (2024,) for factor in factors)
    assert all("future-target-season" not in factor.source_game_ids for factor in factors)
    assert all(len(factor.identity_checksum) == 64 for factor in factors)


def _weather(**overrides) -> WeatherObservation:
    values = {
        "observation_id": "weather-1",
        "game_id": "game-1",
        "venue_id": "venue-1",
        "observed_at": BOUNDARY - timedelta(hours=1),
        "eligible_at": BOUNDARY - timedelta(hours=1),
        "temperature_f": 80.0,
        "humidity_pct": 50.0,
        "pressure_hpa": 1012.0,
        "precipitation_probability": 0.1,
        "wind_speed_mph": 10.0,
        "wind_from_degrees": 180.0,
        "roof_closed": False,
        "altitude_ft": 20.0,
        "evidence_checksum": "a" * 64,
    }
    values.update(overrides)
    return WeatherObservation(**values)


def _orientation(**overrides) -> ParkOrientationEvidence:
    values = {
        "venue_id": "venue-1",
        "home_to_center_bearing": 0.0,
        "evidence_cutoff": BOUNDARY - timedelta(days=1),
        "evidence_checksum": "b" * 64,
        "source_evidence_ids": ("venue-survey-1",),
    }
    values.update(overrides)
    return ParkOrientationEvidence(**values)


def test_directional_wind_requires_governed_orientation_and_respects_roof() -> None:
    unavailable = build_weather_context(
        _weather(), cutoff_at=BOUNDARY, park_orientation=None
    )
    assert unavailable.directional_wind_mph is None
    assert unavailable.directional_wind_basis.startswith("UNAVAILABLE")
    toward_center = build_weather_context(
        _weather(), cutoff_at=BOUNDARY, park_orientation=_orientation()
    )
    assert toward_center.directional_wind_mph == pytest.approx(10.0)
    closed = build_weather_context(
        _weather(roof_closed=True), cutoff_at=BOUNDARY, park_orientation=_orientation()
    )
    assert closed.directional_wind_mph == 0.0
    assert closed.directional_wind_basis == "ROOF_CLOSED_ZERO_DIRECTIONAL_WIND"


def test_weather_after_cutoff_and_invalid_bearing_fail_closed() -> None:
    with pytest.raises(ContextError, match="after the feature cutoff"):
        build_weather_context(
            _weather(
                observed_at=BOUNDARY + timedelta(seconds=1),
                eligible_at=BOUNDARY + timedelta(seconds=1),
            ),
            cutoff_at=BOUNDARY,
            park_orientation=_orientation(),
        )
    with pytest.raises(ContextError, match="bearing"):
        _orientation(home_to_center_bearing=360.0)


def test_context_identity_binds_full_park_and_orientation_lineage() -> None:
    first = fit_prior_period_park_factors(
        _park_rows(),
        target_season=2025,
        computed_at=BOUNDARY,
        minimum_venue_games=20,
    )[0]
    changed = fit_prior_period_park_factors(
        _park_rows(),
        target_season=2025,
        computed_at=BOUNDARY + timedelta(seconds=1),
        minimum_venue_games=20,
    )[0]
    assert first.identity_checksum != changed.identity_checksum

    weather = _weather()
    first_weather = build_weather_context(
        weather,
        cutoff_at=BOUNDARY,
        park_orientation=_orientation(),
    )
    changed_orientation = build_weather_context(
        weather,
        cutoff_at=BOUNDARY,
        park_orientation=_orientation(
            evidence_checksum="c" * 64,
            source_evidence_ids=("venue-survey-2",),
        ),
    )
    assert first_weather.identity_checksum != changed_orientation.identity_checksum
    with pytest.raises(ContextError, match="not eligible"):
        build_weather_context(
            weather,
            cutoff_at=BOUNDARY,
            park_orientation=_orientation(evidence_cutoff=BOUNDARY + timedelta(seconds=1)),
        )

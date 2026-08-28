from __future__ import annotations

import logging
from datetime import UTC, date, datetime

import httpx
import pytest

from app.core.logging import configure_logging
from app.data_sources import MLBStatsAPIAdapter, OpenMeteoAdapter, SourceError, TheOddsAPIAdapter


def test_mlb_team_normalization_preserves_provider_abbreviation() -> None:
    team = MLBStatsAPIAdapter._team({
        "team": {
            "id": 118,
            "name": "Kansas City Royals",
            "abbreviation": "KC",
        },
        "leagueRecord": {"wins": 70, "losses": 61},
    })

    assert team.source_id == "118"
    assert team.name == "Kansas City Royals"
    assert team.abbreviation == "KC"


@pytest.mark.asyncio
async def test_mlb_schedule_normalization_keeps_probable_unconfirmed() -> None:
    payload = {
        "dates": [{"games": [{
            "gamePk": 123, "gameDate": "2026-08-12T23:05:00Z",
            "status": {"detailedState": "Scheduled"}, "venue": {"id": 1, "name": "Fenway Park"},
            "teams": {
                "away": {"team": {"id": 147, "name": "New York Yankees"}, "leagueRecord": {"wins": 60, "losses": 50}, "probablePitcher": {"id": 9, "fullName": "Away Starter"}},
                "home": {"team": {"id": 111, "name": "Boston Red Sox"}, "leagueRecord": {"wins": 58, "losses": 52}, "probablePitcher": {"id": 8, "fullName": "Home Starter"}},
            },
        }]}],
    }
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/people"):
            return httpx.Response(200, json={"people": [
                {"id": 9, "pitchHand": {"code": "L"}},
                {"id": 8, "pitchHand": {"code": "R"}},
            ]})
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await MLBStatsAPIAdapter(client=client).get_schedule(date(2026, 8, 12))
    game = result.envelope.data[0]
    assert game["away_pitcher"]["confirmed"] is False
    assert game["home_pitcher"]["confirmed"] is False
    assert game["away_pitcher"]["throws"] == "L"
    assert game["home_pitcher"]["throws"] == "R"


@pytest.mark.asyncio
async def test_mlb_lineup_batting_order_maps_to_nine_slots() -> None:
    # MLB encodes the batting slot as battingOrder "100".."900" (slot x100).
    # The parser must map these to slots 1..9 — otherwise every starter clamps to
    # slot 9, dedup keeps one entry, and the lineup is wrongly marked incomplete.
    ids = list(range(101, 110))
    players = {
        f"ID{pid}": {
            "person": {"fullName": f"Batter {k}", "batSide": {"code": "R"}},
            "position": {"abbreviation": "OF"},
            "battingOrder": f"{k}00",
        }
        for k, pid in enumerate(ids, start=1)
    }
    boxscore = {"teams": {
        "away": {"team": {"id": 147}, "players": players, "battingOrder": ids},
        "home": {"team": {"id": 111}, "players": {}, "battingOrder": []},
    }}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=boxscore))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await MLBStatsAPIAdapter(client=client).get_game_lineups("777")

    lineups = result.envelope.data
    assert len(lineups) == 1  # only the away side has a batting order
    entries = lineups[0]["entries"]
    assert sorted(entry["batting_order"] for entry in entries) == list(range(1, 10))
    assert lineups[0]["status"] == "CONFIRMED"


@pytest.mark.asyncio
async def test_odds_missing_key_fails_without_network() -> None:
    with pytest.raises(SourceError) as caught:
        await TheOddsAPIAdapter("").get_market_snapshot()
    assert caught.value.retryable is False


@pytest.mark.asyncio
async def test_odds_api_key_is_redacted_from_transport_logs(caplog: pytest.LogCaptureFixture) -> None:
    configure_logging()
    sentinel_key = "test-sentinel-odds-key-never-log"
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[]))

    with caplog.at_level(logging.INFO):
        async with httpx.AsyncClient(transport=transport) as client:
            await TheOddsAPIAdapter(api_key=sentinel_key, client=client).get_market_snapshot()

    # Defense in depth: even a warning record containing a provider URL is
    # redacted. Exercise the installed filter directly so the assertion remains
    # independent of pytest's process-wide handler order.
    warning_record = logging.LogRecord(
        name="httpx",
        level=logging.WARNING,
        pathname=__file__,
        lineno=0,
        msg="provider request failed: https://example.test/odds?apiKey=%s&regions=us",
        args=(sentinel_key,),
        exc_info=None,
    )
    for log_filter in logging.getLogger("httpx").filters:
        log_filter.filter(warning_record)

    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
    assert sentinel_key not in caplog.text
    assert sentinel_key not in warning_record.getMessage()
    assert "apiKey=<redacted>" in warning_record.getMessage()


@pytest.mark.asyncio
async def test_source_http_error_is_typed() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(503))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(SourceError) as caught:
            await MLBStatsAPIAdapter(client=client).get_schedule(date(2026, 8, 12))
    assert caught.value.source == "MLB_STATS_API"


@pytest.mark.asyncio
async def test_weather_selects_nearest_hour() -> None:
    payload = {"hourly": {
        "time": ["2026-08-12T22:00", "2026-08-12T23:00"],
        "temperature_2m": [70, 74], "relative_humidity_2m": [60, 55],
        "precipitation_probability": [20, 10], "precipitation": [0.1, 0],
        "weather_code": [61, 1], "pressure_msl": [1010, 1012],
        "wind_speed_10m": [5, 8], "wind_direction_10m": [180, 225],
    }}
    weather = OpenMeteoAdapter._nearest_hour(payload, datetime(2026, 8, 12, 22, 50, tzinfo=UTC))
    assert weather.temperature_f == 74
    assert weather.description == "Mainly clear"

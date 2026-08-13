from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from .base import AdapterResponse, SourceEnvelope, SourceError, WeatherDTO


WEATHER_DESCRIPTIONS = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Rime fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    80: "Rain showers",
    81: "Rain showers",
    82: "Heavy rain showers",
    95: "Thunderstorm",
}


class OpenMeteoAdapter:
    source = "OPEN_METEO"
    endpoint = "https://api.open-meteo.com/v1/forecast"

    def __init__(self, client: httpx.AsyncClient | None = None, timeout: float = 15.0) -> None:
        self._client = client
        self.timeout = timeout

    async def get_weather(self, latitude: float, longitude: float, at: datetime) -> AdapterResponse:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": (
                "temperature_2m,relative_humidity_2m,precipitation_probability,"
                "precipitation,weather_code,pressure_msl,wind_speed_10m,wind_direction_10m"
            ),
            "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph",
            "precipitation_unit": "inch",
            "timezone": "UTC",
            "start_date": at.date().isoformat(),
            "end_date": at.date().isoformat(),
        }
        try:
            response = await client.get(self.endpoint, params=params)
            response.raise_for_status()
            raw = response.json()
            weather = self._nearest_hour(raw, at)
            return AdapterResponse(
                envelope=SourceEnvelope(
                    source=self.source,
                    fetched_at=weather.available_as_of,
                    data=weather.model_dump(mode="json"),
                    metadata={"latitude": latitude, "longitude": longitude},
                ),
                raw_payload=raw,
            )
        except SourceError:
            raise
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise SourceError(self.source, f"Weather request failed: {type(exc).__name__}") from exc
        finally:
            if owns_client:
                await client.aclose()

    @staticmethod
    def _nearest_hour(raw: dict[str, Any], at: datetime) -> WeatherDTO:
        hourly = raw["hourly"]
        times = [datetime.fromisoformat(value).replace(tzinfo=UTC) for value in hourly["time"]]
        target = at.astimezone(UTC) if at.tzinfo else at.replace(tzinfo=UTC)
        index = min(range(len(times)), key=lambda idx: abs((times[idx] - target).total_seconds()))

        def item(key: str) -> Any:
            values = hourly.get(key) or []
            return values[index] if index < len(values) else None

        code = item("weather_code")
        now = datetime.now(UTC)
        return WeatherDTO(
            available_as_of=now,
            forecast_for=times[index],
            temperature_f=item("temperature_2m"),
            wind_speed_mph=item("wind_speed_10m"),
            wind_direction_degrees=item("wind_direction_10m"),
            precipitation_inches=item("precipitation"),
            precipitation_probability=item("precipitation_probability"),
            humidity_percent=item("relative_humidity_2m"),
            pressure_hpa=item("pressure_msl"),
            weather_code=code,
            description=WEATHER_DESCRIPTIONS.get(code, "Unknown") if code is not None else None,
        )

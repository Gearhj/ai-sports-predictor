from __future__ import annotations

from datetime import date, datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


class SourceError(RuntimeError):
    def __init__(self, source: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.source = source
        self.retryable = retryable


class SourceEnvelope(BaseModel):
    source: str
    source_mode: str = "LIVE"
    fetched_at: datetime
    source_event_time: datetime | None = None
    data: Any
    metadata: dict[str, Any] = Field(default_factory=dict)


class TeamDTO(BaseModel):
    source_id: str
    name: str
    abbreviation: str | None = None
    record_wins: int | None = None
    record_losses: int | None = None


class PitcherDTO(BaseModel):
    source_id: str | None = None
    full_name: str | None = None
    throws: str | None = None
    confirmed: bool = False


class VenueDTO(BaseModel):
    source_id: str | None = None
    name: str
    latitude: float | None = None
    longitude: float | None = None


class ScheduleGameDTO(BaseModel):
    source_game_id: str
    scheduled_at: datetime
    event_date: date
    status: str
    away_team: TeamDTO
    home_team: TeamDTO
    away_pitcher: PitcherDTO | None = None
    home_pitcher: PitcherDTO | None = None
    venue: VenueDTO
    source_mode: str = "LIVE"
    away_score: int | None = None
    home_score: int | None = None
    final_at: datetime | None = None


class TeamStatsDTO(BaseModel):
    source_team_id: str
    available_as_of: datetime
    games_played: int | None = None
    runs_per_game: float | None = None
    ops: float | None = None
    obp: float | None = None
    slg: float | None = None
    strikeout_rate: float | None = None
    walk_rate: float | None = None
    era: float | None = None
    whip: float | None = None
    recent_runs: float | None = None
    starter_era: float | None = None
    starter_xera: float | None = None
    bullpen_era: float | None = None
    xwoba: float | None = None
    barrel_rate: float | None = None
    hard_hit_rate: float | None = None
    average_exit_velocity: float | None = None
    raw_metrics: dict[str, Any] = Field(default_factory=dict)


class WeatherDTO(BaseModel):
    available_as_of: datetime
    forecast_for: datetime
    temperature_f: float | None = None
    wind_speed_mph: float | None = None
    wind_direction_degrees: float | None = None
    precipitation_inches: float | None = None
    precipitation_probability: float | None = None
    humidity_percent: float | None = None
    pressure_hpa: float | None = None
    weather_code: int | None = None
    description: str | None = None
    source_mode: str = "LIVE"


class OddsOutcomeDTO(BaseModel):
    name: str
    price: int
    point: float | None = None


class BookMarketDTO(BaseModel):
    key: str
    last_update: datetime
    outcomes: list[OddsOutcomeDTO]


class BookmakerDTO(BaseModel):
    key: str
    title: str
    last_update: datetime
    markets: list[BookMarketDTO]


class OddsEventDTO(BaseModel):
    source_event_id: str
    commence_time: datetime
    home_team: str
    away_team: str
    bookmakers: list[BookmakerDTO]
    source_mode: str = "LIVE"


class ScheduleSource(Protocol):
    async def get_schedule(self, event_date: date) -> SourceEnvelope: ...


class StatsSource(Protocol):
    async def get_team_stats(self, source_team_id: str, season: int) -> SourceEnvelope: ...


class OddsSource(Protocol):
    async def get_market_snapshot(self) -> SourceEnvelope: ...


class WeatherSource(Protocol):
    async def get_weather(self, latitude: float, longitude: float, at: datetime) -> SourceEnvelope: ...


class StatcastSource(Protocol):
    async def get_batting_leaderboard(self, season: int, minimum_plate_appearances: int = 50) -> "AdapterResponse": ...


class AdapterResponse(BaseModel):
    """Raw and normalized response used by ingestion without provider coupling."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    envelope: SourceEnvelope
    raw_payload: dict[str, Any] | list[Any]

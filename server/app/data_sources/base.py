from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class SourceError(RuntimeError):
    def __init__(self, source: str, message: str, *, retryable: bool = True, status_code: int | None = None) -> None:
        super().__init__(message)
        self.source = source
        self.retryable = retryable
        # HTTP status when the failure was an HTTP error (None for transport errors).
        self.status_code = status_code


SourceMode = Literal["LIVE", "DEMO"]
HistoricalEvidenceClass = Literal[
    "RECONSTRUCTABLE_POINT_IN_TIME",
    "PARTIALLY_RECONSTRUCTABLE",
    "FORWARD_ONLY",
]
ResearchProvenance = Literal["HISTORICAL_RECONSTRUCTION"]
HistoricalCompletionTimeBasis = Literal[
    "AUTHORITATIVE_FINAL_AT",
    "DERIVED_LAST_EVENT_COMPLETION_PROXY",
]
HistoricalStarterProvenance = Literal["ACTUAL_STARTER_HISTORICAL_RECONSTRUCTION"]
HistoricalLineupProvenance = Literal["HISTORICAL_ACTUAL_LINEUP"]


class SourceEnvelope(BaseModel):
    source: str
    source_mode: SourceMode = "LIVE"
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
    timezone: str | None = None


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
    source_mode: SourceMode = "LIVE"
    away_score: int | None = None
    home_score: int | None = None
    final_at: datetime | None = None
    doubleheader_status: str | None = None
    game_number: int | None = None
    scheduled_innings: int | None = 9
    resume_at: datetime | None = None
    rescheduled_from: datetime | None = None
    resumed_from_date: date | None = None


class HistoricalFinalDTO(BaseModel):
    source_game_id: str
    official_date: date
    status: str
    abstract_state: str
    home_score: int
    away_score: int
    innings_played: int | None = None
    event_completed_at: datetime | None = None
    completion_time_basis: HistoricalCompletionTimeBasis | None = None
    completion_time_source_field: str | None = None
    completion_time_method: str | None = None
    completion_time_limitation: str | None = None
    source_published_at: datetime | None = None
    acquired_at: datetime
    research_provenance: ResearchProvenance = "HISTORICAL_RECONSTRUCTION"
    evidence_class: HistoricalEvidenceClass = "RECONSTRUCTABLE_POINT_IN_TIME"


class HistoricalScheduleGameDTO(BaseModel):
    game: ScheduleGameDTO
    season: int
    source_schedule_date: date | None = None
    official_date: date
    game_type: str | None = None
    abstract_state: str | None = None
    coded_state: str | None = None
    acquired_at: datetime
    final: HistoricalFinalDTO | None = None
    research_provenance: ResearchProvenance = "HISTORICAL_RECONSTRUCTION"
    evidence_class: HistoricalEvidenceClass = "RECONSTRUCTABLE_POINT_IN_TIME"


class HistoricalPlayerGameEvidenceDTO(BaseModel):
    source_game_id: str
    source_player_id: str
    source_team_id: str
    full_name: str | None = None
    is_home: bool
    batting_order: int | None = None
    appeared_as_pitcher: bool = False
    actual_starting_pitcher: bool = False
    starter_provenance: HistoricalStarterProvenance | None = None
    lineup_provenance: HistoricalLineupProvenance | None = None
    position: str | None = None
    batting: dict[str, Any] | None = None
    pitching: dict[str, Any] | None = None
    acquired_at: datetime
    research_provenance: ResearchProvenance = "HISTORICAL_RECONSTRUCTION"
    evidence_class: HistoricalEvidenceClass = "RECONSTRUCTABLE_POINT_IN_TIME"


class HistoricalTeamGameEvidenceDTO(BaseModel):
    source_game_id: str
    source_team_id: str
    is_home: bool
    team_stats: dict[str, Any]
    acquired_at: datetime
    research_provenance: ResearchProvenance = "HISTORICAL_RECONSTRUCTION"
    evidence_class: HistoricalEvidenceClass = "RECONSTRUCTABLE_POINT_IN_TIME"


class HistoricalBoxscoreDTO(BaseModel):
    source_game_id: str
    acquired_at: datetime
    players: list[HistoricalPlayerGameEvidenceDTO]
    teams: list[HistoricalTeamGameEvidenceDTO]
    research_provenance: ResearchProvenance = "HISTORICAL_RECONSTRUCTION"
    evidence_class: HistoricalEvidenceClass = "RECONSTRUCTABLE_POINT_IN_TIME"


class HistoricalGameEvidenceDTO(BaseModel):
    """Final, play-completion, and boxscore facts from one immutable game feed."""

    source_game_id: str
    acquired_at: datetime
    final: HistoricalFinalDTO
    boxscore: HistoricalBoxscoreDTO
    research_provenance: ResearchProvenance = "HISTORICAL_RECONSTRUCTION"
    evidence_class: HistoricalEvidenceClass = "RECONSTRUCTABLE_POINT_IN_TIME"


class LineupEntryDTO(BaseModel):
    source_player_id: str
    full_name: str
    batting_order: int
    defensive_position: str | None = None
    bats: str | None = None
    starter: bool = True
    expected_plate_appearance_weight: float | None = None


class LineupDTO(BaseModel):
    source_game_id: str
    source_team_id: str
    available_as_of: datetime
    status: str
    entries: list[LineupEntryDTO]
    source_mode: SourceMode = "LIVE"


class RosterAvailabilityDTO(BaseModel):
    source_team_id: str
    source_player_id: str
    full_name: str
    available_as_of: datetime
    effective_at: datetime
    status: str
    position: str | None = None
    bats: str | None = None
    throws: str | None = None
    participation_probability: float | None = None
    replacement_source_player_id: str | None = None
    source_mode: SourceMode = "LIVE"


class BullpenAvailabilityDTO(BaseModel):
    """Provider evidence for one reliever's pregame workload.

    ``participation_probability`` is a workload-only score when the evidence is
    complete. It is not an official club availability declaration. The raw
    inputs, formula, and missing fields remain in ``workload`` so downstream
    consumers can distinguish an estimate from reported availability.
    """

    source_team_id: str
    source_player_id: str
    full_name: str
    available_as_of: datetime
    evidence_start_date: date
    evidence_end_date: date
    role: str | None = None
    throws: str | None = None
    pitches_previous_day: int | None = Field(default=None, ge=0)
    appearances_last_three_days: int | None = Field(default=None, ge=0)
    innings_last_three_days: float | None = Field(default=None, ge=0)
    used_consecutive_days: bool | None = None
    expected_available: bool | None = None
    participation_probability: float | None = Field(default=None, ge=0, le=1)
    workload: dict[str, Any] = Field(default_factory=dict)
    source_mode: SourceMode = "LIVE"


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
    source_mode: SourceMode = "LIVE"


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
    source_mode: SourceMode = "LIVE"


class OddsScoreTeamDTO(BaseModel):
    name: str
    score: int = Field(ge=0)


class OddsScoreEventDTO(BaseModel):
    source_event_id: str
    commence_time: datetime
    completed: bool
    home_team: str
    away_team: str
    scores: list[OddsScoreTeamDTO]
    last_update: datetime | None = None
    source_mode: SourceMode = "LIVE"


class ScheduleSource(Protocol):
    async def get_schedule(self, event_date: date) -> "AdapterResponse": ...


class StatsSource(Protocol):
    async def get_team_stats(self, source_team_id: str, season: int) -> "AdapterResponse": ...


class OddsSource(Protocol):
    async def get_market_snapshot(self) -> "AdapterResponse": ...


class WeatherSource(Protocol):
    async def get_weather(self, latitude: float, longitude: float, at: datetime) -> "AdapterResponse": ...


class StatcastSource(Protocol):
    async def get_batting_leaderboard(self, season: int, minimum_plate_appearances: int = 50) -> "AdapterResponse": ...


class AdapterResponse(BaseModel):
    """Raw and normalized response used by ingestion without provider coupling."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    envelope: SourceEnvelope
    raw_payload: dict[str, Any] | list[Any]

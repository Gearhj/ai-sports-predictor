"""Canonical, provider-neutral SQLAlchemy entities for AI Sports Predictor."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, Float, ForeignKey, Index, Integer, JSON, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.db.base import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class StrEnum(str, enum.Enum):
    pass


class UserRole(StrEnum):
    ADMIN = "admin"
    MEMBER = "member"


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    PENDING = "pending"


class GameStatus(StrEnum):
    SCHEDULED = "scheduled"
    LIVE = "live"
    FINAL = "final"
    POSTPONED = "postponed"
    CANCELED = "canceled"


class MarketType(StrEnum):
    MONEYLINE = "h2h"
    SPREAD = "spreads"
    TOTAL = "totals"


class OddsStatus(StrEnum):
    OPEN = "open"
    SUSPENDED = "suspended"
    CLOSED = "closed"


class ForecastStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    SUPERSEDED = "superseded"


class ForecastLifecycle(StrEnum):
    MORNING = "morning"
    EARLY = "early"
    PREGAME = "pregame"
    FINAL = "final"


class ModelStatus(StrEnum):
    DEVELOPMENT = "development"
    CHALLENGER = "challenger"
    CHAMPION = "champion"
    RETIRED = "retired"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class PositionStatus(StrEnum):
    OPEN = "open"
    SETTLED = "settled"
    VOID = "void"


class PositionResult(StrEnum):
    PENDING = "pending"
    WIN = "win"
    LOSS = "loss"
    PUSH = "push"
    VOID = "void"


class EntitlementKind(StrEnum):
    MEMBER = "member"
    ADMIN = "admin"
    PREMIUM = "premium"


class CommunityPostStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    HIDDEN = "hidden"


class AnalystTier(StrEnum):
    UNRATED = "unrated"
    PROVISIONAL = "provisional"
    ESTABLISHED = "established"


def enum_type(value: type[StrEnum]) -> SAEnum:
    """Use varchar-backed enums so migrations work the same on SQLite and PostgreSQL."""
    return SAEnum(value, native_enum=False, create_constraint=True, validate_strings=True)


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=_uuid)


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class User(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[UserRole] = mapped_column(enum_type(UserRole), nullable=False, default=UserRole.MEMBER)
    status: Mapped[UserStatus] = mapped_column(enum_type(UserStatus), nullable=False, default=UserStatus.ACTIVE)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class Entitlement(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "entitlements"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    kind: Mapped[EntitlementKind] = mapped_column(enum_type(EntitlementKind), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(80), nullable=False, default="local")
    subscription_provider: Mapped[str | None] = mapped_column(String(80))
    external_membership_id: Mapped[str | None] = mapped_column(String(255))
    entitlement_status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    __table_args__ = (UniqueConstraint("user_id", "kind", name="uq_entitlement_user_kind"),)


class Sport(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "sports"
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class League(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "leagues"
    sport_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sports.id"), index=True, nullable=False)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    country_code: Mapped[str | None] = mapped_column(String(2))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Season(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "seasons"
    league_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leagues.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (UniqueConstraint("league_id", "name", name="uq_season_league_name"),)


class Team(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "teams"
    league_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leagues.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    abbreviation: Mapped[str] = mapped_column(String(12), nullable=False)
    city: Mapped[str | None] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    __table_args__ = (UniqueConstraint("league_id", "abbreviation", name="uq_team_league_abbreviation"),)


class Player(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "players"
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id"), index=True)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    position: Mapped[str | None] = mapped_column(String(32))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Roster(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "rosters"
    season_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("seasons.id"), index=True, nullable=False)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id"), index=True, nullable=False)
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), index=True, nullable=False)
    available_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="active")
    __table_args__ = (UniqueConstraint("season_id", "team_id", "player_id", "available_as_of", name="uq_roster_asof"),)


class PlayerTeamHistory(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "player_team_history"
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), index=True, nullable=False)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id"), index=True, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Pitcher(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "pitchers"
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), unique=True, nullable=False)
    throws: Mapped[str | None] = mapped_column(String(8))


class Batter(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "batters"
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), unique=True, nullable=False)
    bats: Mapped[str | None] = mapped_column(String(8))


class Venue(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "venues"
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    city: Mapped[str | None] = mapped_column(String(120))
    region: Mapped[str | None] = mapped_column(String(80))
    country_code: Mapped[str | None] = mapped_column(String(2))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    timezone: Mapped[str | None] = mapped_column(String(64))


class Game(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "games"
    league_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leagues.id"), index=True, nullable=False)
    season_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("seasons.id"), index=True)
    venue_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("venues.id"), index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    status: Mapped[GameStatus] = mapped_column(enum_type(GameStatus), nullable=False, default=GameStatus.SCHEDULED)
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    scheduled_innings: Mapped[int | None] = mapped_column(Integer)
    final_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="LIVE")


class GameTeam(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "game_teams"
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), nullable=False)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id"), nullable=False)
    is_home: Mapped[bool] = mapped_column(Boolean, nullable=False)
    score: Mapped[int | None] = mapped_column(Integer)
    probable_pitcher_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("players.id"))
    pitcher_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pitcher_status_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    record_wins: Mapped[int | None] = mapped_column(Integer)
    record_losses: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (UniqueConstraint("game_id", "team_id", name="uq_game_team"), UniqueConstraint("game_id", "is_home", name="uq_game_home_away"))


class TeamGameStat(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "team_game_stats"
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), index=True, nullable=False)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id"), index=True, nullable=False)
    available_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    values: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    __table_args__ = (UniqueConstraint("game_id", "team_id", "available_as_of", name="uq_team_game_stats_asof"),)


class PlayerGameStat(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "player_game_stats"
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), index=True, nullable=False)
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), index=True, nullable=False)
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id"), index=True)
    available_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    values: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    __table_args__ = (UniqueConstraint("game_id", "player_id", "available_as_of", name="uq_player_game_stats_asof"),)


class PitchEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "pitch_events"
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), index=True, nullable=False)
    pitcher_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("players.id"), index=True)
    batter_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("players.id"), index=True)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    values: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    __table_args__ = (UniqueConstraint("game_id", "sequence_number", name="uq_pitch_event_sequence"),)


class ExternalIdMapping(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "external_id_mappings"
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    __table_args__ = (UniqueConstraint("entity_type", "source", "external_id", name="uq_external_source_id"), Index("ix_external_entity", "entity_type", "entity_id"))


class WeatherSnapshot(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "weather_snapshots"
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), index=True, nullable=False)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    available_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    temperature_f: Mapped[float | None] = mapped_column(Float)
    wind_mph: Mapped[float | None] = mapped_column(Float)
    wind_direction: Mapped[str | None] = mapped_column(String(32))
    precipitation_probability: Mapped[float | None] = mapped_column(Float)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    payload_checksum: Mapped[str | None] = mapped_column(String(64))
    source_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="LIVE")


class Sportsbook(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "sportsbooks"
    key: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    region: Mapped[str | None] = mapped_column(String(20))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Market(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "markets"
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), index=True, nullable=False)
    market_type: Mapped[MarketType] = mapped_column(enum_type(MarketType), nullable=False)
    period: Mapped[str] = mapped_column(String(32), nullable=False, default="full_game")
    __table_args__ = (UniqueConstraint("game_id", "market_type", "period", name="uq_game_market_period"),)


class MarketOutcome(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "market_outcomes"
    market_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("markets.id"), index=True, nullable=False)
    outcome_key: Mapped[str] = mapped_column(String(80), nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    participant_team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id"))
    __table_args__ = (UniqueConstraint("market_id", "outcome_key", name="uq_market_outcome_key"),)


class OddsSnapshot(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "odds_snapshots"
    market_outcome_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("market_outcomes.id"), index=True, nullable=False)
    sportsbook_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sportsbooks.id"), index=True, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    available_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    american_odds: Mapped[int | None] = mapped_column(Integer)
    decimal_odds: Mapped[float] = mapped_column(Float, nullable=False)
    point: Mapped[float | None] = mapped_column(Float)
    implied_probability: Mapped[float | None] = mapped_column(Float)
    status: Mapped[OddsStatus] = mapped_column(enum_type(OddsStatus), nullable=False, default=OddsStatus.OPEN)
    is_closing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    payload_checksum: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(80), nullable=False, default="unknown")
    source_event_id: Mapped[str | None] = mapped_column(String(255), index=True)
    source_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="LIVE")


class ModelVersion(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "model_versions"
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[str] = mapped_column(String(80), nullable=False)
    sport_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sports.id"), index=True)
    feature_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("feature_versions.id"), index=True)
    algorithm: Mapped[str] = mapped_column(String(160), nullable=False, default="unspecified")
    status: Mapped[ModelStatus] = mapped_column(enum_type(ModelStatus), nullable=False, default=ModelStatus.DEVELOPMENT)
    training_window: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    hyperparameters: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    artifact_uri: Mapped[str | None] = mapped_column(String(500))
    artifact_checksum: Mapped[str | None] = mapped_column(String(64))
    parameters: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    trained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("name", "version", name="uq_model_name_version"),)


class FeatureVersion(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "feature_versions"
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[str] = mapped_column(String(80), nullable=False)
    schema_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    definition: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("name", "version", name="uq_feature_name_version"),)


class Forecast(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "forecasts"
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), index=True, nullable=False)
    model_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("model_versions.id"), nullable=False)
    feature_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("feature_versions.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    available_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[ForecastStatus] = mapped_column(enum_type(ForecastStatus), nullable=False, default=ForecastStatus.DRAFT)
    lifecycle: Mapped[ForecastLifecycle] = mapped_column(enum_type(ForecastLifecycle), nullable=False, default=ForecastLifecycle.EARLY)
    home_win_probability: Mapped[float] = mapped_column(Float, nullable=False)
    away_win_probability: Mapped[float] = mapped_column(Float, nullable=False)
    expected_home_runs: Mapped[float | None] = mapped_column(Float)
    expected_away_runs: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    readiness_score: Mapped[float | None] = mapped_column(Float)
    fair_home_moneyline: Mapped[int | None] = mapped_column(Integer)
    fair_away_moneyline: Mapped[int | None] = mapped_column(Integer)
    expected_total_runs: Mapped[float | None] = mapped_column(Float)
    expected_run_difference: Mapped[float | None] = mapped_column(Float)
    uncertainty: Mapped[float | None] = mapped_column(Float)
    readiness_components: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    model_drivers: Mapped[list[Any] | None] = mapped_column(JSON)
    feature_snapshot_reference: Mapped[str | None] = mapped_column(String(500))
    market_snapshot_reference: Mapped[str | None] = mapped_column(String(500))
    source_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="LIVE")
    simulation_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("game_id", "version", name="uq_forecast_game_version"),)


class ForecastComponent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "forecast_components"
    forecast_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("forecasts.id"), index=True, nullable=False)
    component_name: Mapped[str] = mapped_column(String(120), nullable=False)
    weight: Mapped[float | None] = mapped_column(Float)
    output: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    __table_args__ = (UniqueConstraint("forecast_id", "component_name", name="uq_forecast_component"),)


class MarketComparison(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "market_comparisons"
    forecast_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("forecasts.id"), index=True, nullable=False)
    market_outcome_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("market_outcomes.id"), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    asp_probability: Mapped[float] = mapped_column(Float, nullable=False)
    market_no_vig_probability: Mapped[float | None] = mapped_column(Float)
    edge: Mapped[float | None] = mapped_column(Float)
    best_decimal_odds: Mapped[float | None] = mapped_column(Float)
    consensus_decimal_odds: Mapped[float | None] = mapped_column(Float)


class OfficialAspPosition(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "official_asp_positions"
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    forecast_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("forecasts.id"), index=True)
    market_outcome_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("market_outcomes.id"), nullable=False)
    odds_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("odds_snapshots.id"), index=True)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stake: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    decimal_odds: Mapped[float] = mapped_column(Float, nullable=False)
    result: Mapped[PositionResult] = mapped_column(enum_type(PositionResult), nullable=False, default=PositionResult.PENDING)
    status: Mapped[PositionStatus] = mapped_column(enum_type(PositionStatus), nullable=False, default=PositionStatus.OPEN)
    payout: Mapped[float | None] = mapped_column(Numeric(12, 2))
    closing_decimal_odds: Mapped[float | None] = mapped_column(Float)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False, default="flat")


class PositionSettlement(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "position_results"
    position_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("official_asp_positions.id"), index=True, nullable=False)
    result: Mapped[PositionResult] = mapped_column(enum_type(PositionResult), nullable=False)
    settled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    profit_loss: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    game_home_score: Mapped[int | None] = mapped_column(Integer)
    game_away_score: Mapped[int | None] = mapped_column(Integer)
    settlement_note: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("position_id", name="uq_position_result"),)


class IngestionRun(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "ingestion_runs"
    source: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    job_name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[RunStatus] = mapped_column(enum_type(RunStatus), nullable=False, default=RunStatus.QUEUED)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    records_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warnings: Mapped[list[Any] | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    raw_reference: Mapped[str | None] = mapped_column(String(500))
    schema_version: Mapped[str | None] = mapped_column(String(80))
    source_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class DataQualityCheck(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "data_quality_checks"
    ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ingestion_runs.id"), index=True)
    check_name: Mapped[str] = mapped_column(String(160), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="warning")
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AuditEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "audit_events"
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(160), index=True, nullable=False)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    ip_address: Mapped[str | None] = mapped_column(String(64))


class JobRun(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "job_runs"
    job_name: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    status: Mapped[RunStatus] = mapped_column(enum_type(RunStatus), nullable=False, default=RunStatus.QUEUED)
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parameters: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)


class _FeatureSnapshot(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __abstract__ = True
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), index=True, nullable=False)
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id"), index=True)
    feature_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("feature_versions.id"))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    values: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source: Mapped[str] = mapped_column(String(80), nullable=False, default="unknown")
    source_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="LIVE")


class TeamFeature(_FeatureSnapshot):
    __tablename__ = "team_features"


class BattingFeature(_FeatureSnapshot):
    __tablename__ = "batting_features"


class PitchingFeature(_FeatureSnapshot):
    __tablename__ = "pitching_features"
    player_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("players.id"), index=True)


class BullpenFeature(_FeatureSnapshot):
    __tablename__ = "bullpen_features"


class GameContext(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "game_contexts"
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), index=True, nullable=False)
    feature_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("feature_versions.id"))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    values: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


# Community/leaderboard tables are intentionally small scaffolding for post-Phase-1 features.
class CommunityPost(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "community_posts"
    author_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    game_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("games.id"), index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[CommunityPostStatus] = mapped_column(enum_type(CommunityPostStatus), nullable=False, default=CommunityPostStatus.DRAFT)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CommunityReaction(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "community_reactions"
    post_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("community_posts.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    reaction: Mapped[str] = mapped_column(String(32), nullable=False, default="like")
    __table_args__ = (UniqueConstraint("post_id", "user_id", "reaction", name="uq_community_reaction"),)


class LeaderboardEntry(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "leaderboard_entries"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    season_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("seasons.id"), index=True)
    metric: Mapped[str] = mapped_column(String(80), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (UniqueConstraint("user_id", "season_id", "metric", "calculated_at", name="uq_leaderboard_measure"),)


class UserVirtualBankroll(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "user_virtual_bankrolls"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    starting_balance: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=1000)
    current_balance: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=1000)
    peak_balance: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=1000)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class VirtualPosition(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "virtual_positions"
    bankroll_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_virtual_bankrolls.id"), index=True, nullable=False)
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), index=True, nullable=False)
    market_outcome_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("market_outcomes.id"), index=True)
    selection: Mapped[str] = mapped_column(String(160), nullable=False)
    stake: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    decimal_odds: Mapped[float] = mapped_column(Float, nullable=False)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[PositionResult] = mapped_column(enum_type(PositionResult), nullable=False, default=PositionResult.PENDING)
    profit_loss: Mapped[float | None] = mapped_column(Numeric(12, 2))


class AnalystRating(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "analyst_ratings"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    tier: Mapped[AnalystTier] = mapped_column(enum_type(AnalystTier), nullable=False, default=AnalystTier.UNRATED)
    rating: Mapped[float | None] = mapped_column(Float)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dimensions: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LeaderboardPeriod(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "leaderboard_periods"
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="planned")


class LeaderboardSnapshot(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "leaderboard_snapshots"
    period_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leaderboard_periods.id"), index=True, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    entries: Mapped[list[Any]] = mapped_column(JSON, nullable=False)


class PrivateLeague(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "private_leagues"
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    join_code_hash: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class PrivateLeagueMember(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "private_league_members"
    league_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("private_leagues.id"), index=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="member")
    __table_args__ = (UniqueConstraint("league_id", "user_id", name="uq_private_league_member"),)

"""Canonical, provider-neutral SQLAlchemy entities for AI Sports Predictor."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, Enum as SAEnum, Float, ForeignKey, Index, Integer, JSON, Numeric, String, Text, UniqueConstraint, func
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
    CANDIDATE = "candidate"
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


class _PointInTimeObservation(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Shared knowledge-time fields for append-only evidence.

    ``observed_at`` is the source/event observation time. ``available_as_of`` is
    the first instant ASP could use the record. Historical queries must always
    constrain the latter, even when the source timestamp is older.
    """

    __abstract__ = True
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    source_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="LIVE")
    source_record_id: Mapped[str | None] = mapped_column(String(255))
    payload_checksum: Mapped[str | None] = mapped_column(String(64))
    ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ingestion_runs.id"))


class GameStateObservation(_PointInTimeObservation):
    __tablename__ = "game_state_observations"
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), nullable=False)
    home_team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id"))
    away_team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id"))
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    is_postponed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    postponement_reason: Mapped[str | None] = mapped_column(Text)
    venue_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("venues.id"))
    doubleheader_status: Mapped[str | None] = mapped_column(String(40))
    game_number: Mapped[int | None] = mapped_column(Integer)
    scheduled_innings: Mapped[int | None] = mapped_column(Integer)
    home_probable_pitcher_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("players.id"))
    away_probable_pitcher_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("players.id"))
    home_starter_confirmation: Mapped[str] = mapped_column(String(24), nullable=False, default="UNKNOWN")
    away_starter_confirmation: Mapped[str] = mapped_column(String(24), nullable=False, default="UNKNOWN")
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    final_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint(
            "game_id", "source", "source_mode", "available_as_of", "payload_checksum",
            name="uq_game_state_obs_evidence",
        ),
        CheckConstraint("game_number IS NULL OR game_number > 0", name="ck_game_state_game_number"),
        Index("ix_game_state_obs_game_asof", "game_id", "available_as_of"),
        Index("ix_game_state_obs_home_team", "home_team_id"),
        Index("ix_game_state_obs_away_team", "away_team_id"),
    )


class LineupObservation(_PointInTimeObservation):
    __tablename__ = "lineup_observations"
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), nullable=False)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id"), nullable=False)
    lineup_status: Mapped[str] = mapped_column(String(24), nullable=False)
    is_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    __table_args__ = (
        UniqueConstraint(
            "game_id", "team_id", "source", "source_mode", "available_as_of", "payload_checksum",
            name="uq_lineup_obs_evidence",
        ),
        CheckConstraint("lineup_status IN ('PROJECTED', 'EXPECTED', 'CONFIRMED')", name="ck_lineup_obs_status"),
        Index("ix_lineup_obs_game_team_asof", "game_id", "team_id", "available_as_of"),
    )


class LineupEntry(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "lineup_entries"
    lineup_observation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("lineup_observations.id"), nullable=False)
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), nullable=False)
    batting_order: Mapped[int | None] = mapped_column(Integer)
    defensive_position: Mapped[str | None] = mapped_column(String(32))
    bats: Mapped[str | None] = mapped_column(String(8))
    is_starter: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    expected_plate_appearances: Mapped[float | None] = mapped_column(Float)
    expected_plate_appearance_weight: Mapped[float | None] = mapped_column(Float)
    __table_args__ = (
        UniqueConstraint("lineup_observation_id", "player_id", name="uq_lineup_entry_player"),
        UniqueConstraint("lineup_observation_id", "batting_order", name="uq_lineup_entry_order"),
        CheckConstraint("batting_order IS NULL OR (batting_order >= 1 AND batting_order <= 9)", name="ck_lineup_entry_order"),
        CheckConstraint("expected_plate_appearances IS NULL OR expected_plate_appearances >= 0", name="ck_lineup_entry_pa"),
        CheckConstraint(
            "expected_plate_appearance_weight IS NULL OR (expected_plate_appearance_weight >= 0 AND expected_plate_appearance_weight <= 1)",
            name="ck_lineup_entry_weight",
        ),
        Index("ix_lineup_entries_observation", "lineup_observation_id"),
    )


class StarterObservation(_PointInTimeObservation):
    __tablename__ = "starter_observations"
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), nullable=False)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id"), nullable=False)
    probable_pitcher_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("players.id"))
    confirmed_pitcher_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("players.id"))
    confirmation_state: Mapped[str] = mapped_column(String(24), nullable=False, default="UNKNOWN")
    throws: Mapped[str | None] = mapped_column(String(8))
    days_rest: Mapped[int | None] = mapped_column(Integer)
    pitches_previous_day: Mapped[int | None] = mapped_column(Integer)
    pitches_last_start: Mapped[int | None] = mapped_column(Integer)
    workload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    __table_args__ = (
        UniqueConstraint(
            "game_id", "team_id", "source", "source_mode", "available_as_of", "payload_checksum",
            name="uq_starter_obs_evidence",
        ),
        CheckConstraint("confirmation_state IN ('UNKNOWN', 'PROBABLE', 'EXPECTED', 'CONFIRMED')", name="ck_starter_obs_confirmation"),
        CheckConstraint("days_rest IS NULL OR days_rest >= 0", name="ck_starter_obs_rest"),
        Index("ix_starter_obs_game_team_asof", "game_id", "team_id", "available_as_of"),
    )


class RosterAvailabilityObservation(_PointInTimeObservation):
    __tablename__ = "roster_availability_observations"
    season_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("seasons.id"))
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id"))
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), nullable=False)
    availability_status: Mapped[str] = mapped_column(String(32), nullable=False)
    transaction_status: Mapped[str | None] = mapped_column(String(32))
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    participation_probability: Mapped[float | None] = mapped_column(Float)
    replacement_player_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("players.id"))
    active_roster: Mapped[bool | None] = mapped_column(Boolean)
    reason: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint(
            "player_id", "team_id", "source", "source_mode", "available_as_of", "payload_checksum",
            name="uq_roster_avail_obs_evidence",
        ),
        CheckConstraint(
            "participation_probability IS NULL OR (participation_probability >= 0 AND participation_probability <= 1)",
            name="ck_roster_avail_probability",
        ),
        Index("ix_roster_avail_player_asof", "player_id", "available_as_of"),
        Index("ix_roster_avail_team_asof", "team_id", "available_as_of"),
        Index("ix_roster_avail_effective", "effective_at"),
    )


class PlayerTalentEstimate(_PointInTimeObservation):
    __tablename__ = "player_talent_estimates"
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    metric_name: Mapped[str] = mapped_column(String(80), nullable=False)
    metric_class: Mapped[str] = mapped_column(String(32), nullable=False, default="UNDERLYING_SKILL")
    prior_value: Mapped[float | None] = mapped_column(Float)
    prior_sample_size: Mapped[float | None] = mapped_column(Float)
    season_value: Mapped[float | None] = mapped_column(Float)
    season_sample_size: Mapped[float | None] = mapped_column(Float)
    recent_value: Mapped[float | None] = mapped_column(Float)
    recent_sample_size: Mapped[float | None] = mapped_column(Float)
    posterior_value: Mapped[float] = mapped_column(Float, nullable=False)
    posterior_delta: Mapped[float | None] = mapped_column(Float)
    reliability: Mapped[float | None] = mapped_column(Float)
    evidence_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    method: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    parameters: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    evidence_references: Mapped[list[Any] | None] = mapped_column(JSON)
    __table_args__ = (
        UniqueConstraint(
            "player_id", "role", "metric_name", "version", "source", "source_mode", "available_as_of",
            name="uq_player_talent_estimate",
        ),
        CheckConstraint("reliability IS NULL OR (reliability >= 0 AND reliability <= 1)", name="ck_player_talent_reliability"),
        Index("ix_player_talent_player_metric_asof", "player_id", "metric_name", "available_as_of"),
        Index("ix_player_talent_evidence_cutoff", "evidence_cutoff"),
    )


class BullpenAvailabilityObservation(_PointInTimeObservation):
    __tablename__ = "bullpen_availability_observations"
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), nullable=False)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id"), nullable=False)
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), nullable=False)
    true_talent_estimate_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("player_talent_estimates.id"))
    role: Mapped[str | None] = mapped_column(String(40))
    throws: Mapped[str | None] = mapped_column(String(8))
    pitches_previous_day: Mapped[int | None] = mapped_column(Integer)
    appearances_last_three_days: Mapped[int | None] = mapped_column(Integer)
    innings_last_three_days: Mapped[float | None] = mapped_column(Float)
    used_consecutive_days: Mapped[bool | None] = mapped_column(Boolean)
    expected_available: Mapped[bool | None] = mapped_column(Boolean)
    participation_probability: Mapped[float | None] = mapped_column(Float)
    workload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    __table_args__ = (
        UniqueConstraint(
            "game_id", "team_id", "player_id", "source", "source_mode", "available_as_of",
            name="uq_bullpen_avail_obs",
        ),
        CheckConstraint(
            "participation_probability IS NULL OR (participation_probability >= 0 AND participation_probability <= 1)",
            name="ck_bullpen_avail_probability",
        ),
        Index("ix_bullpen_avail_game_team_asof", "game_id", "team_id", "available_as_of"),
    )


class StatcastPlayerObservation(_PointInTimeObservation):
    __tablename__ = "statcast_player_observations"
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), nullable=False)
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id"))
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    evidence_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sample_size: Mapped[float | None] = mapped_column(Float)
    feature_schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    values: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    raw_reference: Mapped[str | None] = mapped_column(String(500))
    __table_args__ = (
        UniqueConstraint(
            "player_id", "role", "season", "source", "source_mode", "available_as_of", "payload_checksum",
            name="uq_statcast_player_obs",
        ),
        CheckConstraint("sample_size IS NULL OR sample_size >= 0", name="ck_statcast_player_sample"),
        Index("ix_statcast_player_role_asof", "player_id", "role", "available_as_of"),
        Index("ix_statcast_player_cutoff", "evidence_cutoff"),
    )


class ParkFactorVersion(_PointInTimeObservation):
    __tablename__ = "park_factor_versions"
    venue_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("venues.id"), nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    overall_run_factor: Mapped[float | None] = mapped_column(Float)
    home_run_factor: Mapped[float | None] = mapped_column(Float)
    left_handed_run_factor: Mapped[float | None] = mapped_column(Float)
    right_handed_run_factor: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    sample_size: Mapped[float | None] = mapped_column(Float)
    methodology: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint(
            "venue_id", "season", "version", "source", "source_mode",
            name="uq_park_factor_version",
        ),
        CheckConstraint("overall_run_factor IS NULL OR overall_run_factor > 0", name="ck_park_factor_run"),
        CheckConstraint("home_run_factor IS NULL OR home_run_factor > 0", name="ck_park_factor_hr"),
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_park_factor_confidence"),
        Index("ix_park_factor_venue_asof", "venue_id", "available_as_of"),
    )


class TeamRatingObservation(_PointInTimeObservation):
    __tablename__ = "team_rating_observations"
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id"), nullable=False)
    rating: Mapped[float] = mapped_column(Float, nullable=False)
    method: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    evidence_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sample_size: Mapped[float | None] = mapped_column(Float)
    components: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    __table_args__ = (
        UniqueConstraint(
            "team_id", "method", "version", "source", "source_mode", "available_as_of",
            name="uq_team_rating_observation",
        ),
        Index("ix_team_rating_team_asof", "team_id", "available_as_of"),
        Index("ix_team_rating_cutoff", "evidence_cutoff"),
    )


class SourceFreshnessPolicy(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "source_freshness_policies"
    source_key: Mapped[str] = mapped_column(String(80), nullable=False)
    lifecycle_stage: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    fresh_threshold_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    stale_threshold_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    critical_threshold_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    blocks_final: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    available_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    __table_args__ = (
        UniqueConstraint("source_key", "lifecycle_stage", "version", name="uq_source_freshness_policy"),
        CheckConstraint("fresh_threshold_minutes >= 0", name="ck_freshness_fresh_nonnegative"),
        CheckConstraint("stale_threshold_minutes >= fresh_threshold_minutes", name="ck_freshness_stale_order"),
        CheckConstraint("critical_threshold_minutes >= stale_threshold_minutes", name="ck_freshness_critical_order"),
        Index("ix_source_freshness_lookup", "source_key", "lifecycle_stage", "available_as_of"),
    )


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


class TrainingDatasetManifest(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Immutable identity and provenance for one materialized training dataset."""

    __tablename__ = "training_dataset_manifests"
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[str] = mapped_column(String(80), nullable=False)
    sport_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sports.id"), index=True)
    feature_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("feature_versions.id"), index=True, nullable=False)
    source_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="LIVE")
    schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    target: Mapped[str] = mapped_column(String(80), nullable=False)
    target_definition: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    cutoff_policy: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    split_policy: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    feature_names: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    seasons: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    event_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    game_count: Mapped[int] = mapped_column(Integer, nullable=False)
    excluded_row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    artifact_uri: Mapped[str] = mapped_column(String(500), nullable=False)
    artifact_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    source_evidence: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    quality_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    code_revision: Mapped[str] = mapped_column(String(120), nullable=False)
    identity_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    built_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_training_dataset_name_version"),
        UniqueConstraint("identity_checksum", name="uq_training_dataset_identity_checksum"),
        CheckConstraint("source_mode IN ('LIVE', 'DEMO')", name="ck_training_dataset_source_mode"),
        CheckConstraint("event_end_at >= event_start_at", name="ck_training_dataset_event_window"),
        CheckConstraint("row_count >= 0", name="ck_training_dataset_row_count"),
        CheckConstraint("game_count >= 0", name="ck_training_dataset_game_count"),
        CheckConstraint("excluded_row_count >= 0", name="ck_training_dataset_excluded_count"),
        Index("ix_training_dataset_event_window", "event_start_at", "event_end_at"),
    )


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
    target: Mapped[str | None] = mapped_column(String(80))
    training_manifest_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("training_dataset_manifests.id", name="fk_model_versions_training_manifest"),
        index=True,
    )
    validation_window: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    holdout_window: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    calibrator_uri: Mapped[str | None] = mapped_column(String(500))
    calibrator_checksum: Mapped[str | None] = mapped_column(String(64))
    ensemble_weights: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    code_revision: Mapped[str | None] = mapped_column(String(120))
    identity_checksum: Mapped[str | None] = mapped_column(String(64))
    feature_names: Mapped[list[Any] | None] = mapped_column(JSON)
    seed_policy: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    target_definition: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    promotion_decision: Mapped[str | None] = mapped_column(String(32))
    promotion_reason: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_model_name_version"),
        UniqueConstraint("identity_checksum", name="uq_model_version_identity_checksum"),
        CheckConstraint(
            "calibrator_checksum IS NULL OR calibrator_uri IS NOT NULL",
            name="ck_model_version_calibrator_reference",
        ),
    )


class ModelEvaluation(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Immutable terminal result for a reproducible offline model evaluation."""

    __tablename__ = "model_evaluations"
    model_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("model_versions.id"), nullable=False)
    training_manifest_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("training_dataset_manifests.id"), nullable=False)
    baseline_model_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("model_versions.id"))
    evaluation_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    validation_window: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    holdout_window: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    baseline_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    slice_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    calibration: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    prediction_artifact_uri: Mapped[str | None] = mapped_column(String(500))
    prediction_artifact_checksum: Mapped[str | None] = mapped_column(String(64))
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="LIVE")
    code_revision: Mapped[str] = mapped_column(String(120), nullable=False)
    identity_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    superiority_claim: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    promotion_recommendation: Mapped[bool | None] = mapped_column(Boolean)
    notes: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint("identity_checksum", name="uq_model_evaluation_identity_checksum"),
        CheckConstraint("source_mode IN ('LIVE', 'DEMO')", name="ck_model_evaluation_source_mode"),
        CheckConstraint(
            "prediction_artifact_checksum IS NULL OR prediction_artifact_uri IS NOT NULL",
            name="ck_model_evaluation_prediction_reference",
        ),
        Index("ix_model_evaluation_model_evaluated", "model_version_id", "evaluated_at"),
        Index("ix_model_evaluation_manifest_evaluated", "training_manifest_id", "evaluated_at"),
        Index("ix_model_evaluation_status", "status"),
    )


class FeatureVersion(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "feature_versions"
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[str] = mapped_column(String(80), nullable=False)
    schema_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    definition: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("name", "version", name="uq_feature_name_version"),)


class GameFeatureSnapshot(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Immutable, checksum-addressed inputs used for one game forecast state."""

    __tablename__ = "game_feature_snapshots"
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    feature_schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    feature_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("feature_versions.id"))
    game_state_observation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("game_state_observations.id"))
    home_lineup_observation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("lineup_observations.id"))
    away_lineup_observation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("lineup_observations.id"))
    home_starter_observation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("starter_observations.id"))
    away_starter_observation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("starter_observations.id"))
    weather_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("weather_snapshots.id"))
    park_factor_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("park_factor_versions.id"))
    home_rating_observation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("team_rating_observations.id"))
    away_rating_observation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("team_rating_observations.id"))
    bullpen_observation_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    roster_observation_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    statcast_observation_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    market_snapshot_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    source_evidence: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    feature_values: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    feature_completeness: Mapped[float] = mapped_column(Float, nullable=False)
    missing_features: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    stale_features: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    provisional_features: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    bayesian_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    source_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="LIVE")
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("feature_completeness >= 0 AND feature_completeness <= 1", name="ck_game_feature_completeness"),
        CheckConstraint("data_as_of <= generated_at", name="ck_game_feature_data_asof"),
        Index("ix_game_feature_snapshot_game_asof", "game_id", "data_as_of"),
        Index("ix_game_feature_snapshot_generated", "generated_at"),
    )


class Forecast(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "forecasts"
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), index=True, nullable=False)
    model_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("model_versions.id"), nullable=False)
    feature_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("feature_versions.id"), nullable=False)
    feature_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("game_feature_snapshots.id"), index=True)
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


class ForecastMovementEvidence(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "forecast_movement_evidence"
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), nullable=False)
    previous_forecast_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("forecasts.id"))
    current_forecast_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("forecasts.id"), nullable=False)
    previous_feature_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("game_feature_snapshots.id"))
    current_feature_snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("game_feature_snapshots.id"), nullable=False)
    feature_family: Mapped[str] = mapped_column(String(80), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    previous_value: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    current_value: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    delta: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    associated_input_updates: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    description: Mapped[str | None] = mapped_column(Text)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    source_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="LIVE")
    __table_args__ = (
        UniqueConstraint("current_forecast_id", "feature_family", name="uq_forecast_movement_family"),
        Index("ix_forecast_movement_game_asof", "game_id", "available_as_of"),
    )


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


class ProviderRequestLedger(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Secret-free append-only provider quota and request accounting."""

    __tablename__ = "provider_request_ledger"
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    endpoint_key: Mapped[str] = mapped_column(String(120), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    quota_cost: Mapped[int | None] = mapped_column(Integer)
    quota_remaining: Mapped[int | None] = mapped_column(Integer)
    records_received: Mapped[int | None] = mapped_column(Integer)
    controlled_live_request: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="LIVE")
    job_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("job_runs.id"))
    reservation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("provider_request_ledger.id"))
    error_type: Mapped[str | None] = mapped_column(String(120))
    response_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    __table_args__ = (
        CheckConstraint("quota_cost IS NULL OR quota_cost >= 0", name="ck_provider_ledger_quota_cost"),
        CheckConstraint("quota_remaining IS NULL OR quota_remaining >= 0", name="ck_provider_ledger_quota_remaining"),
        CheckConstraint("records_received IS NULL OR records_received >= 0", name="ck_provider_ledger_records"),
        CheckConstraint("status IN ('RESERVED', 'SUCCEEDED', 'FAILED')", name="ck_provider_ledger_status"),
        CheckConstraint(
            "(status = 'RESERVED' AND reservation_id IS NULL AND completed_at IS NULL) OR "
            "(status IN ('SUCCEEDED', 'FAILED') AND reservation_id IS NOT NULL AND completed_at IS NOT NULL)",
            name="ck_provider_ledger_lifecycle",
        ),
        UniqueConstraint("reservation_id", name="uq_provider_ledger_terminal_reservation"),
        UniqueConstraint(
            "provider", "endpoint_key", "request_fingerprint", "status",
            name="uq_provider_ledger_request_status",
        ),
        Index("ix_provider_ledger_provider_requested", "provider", "requested_at"),
        Index("ix_provider_ledger_job", "job_run_id"),
        Index("ix_provider_ledger_reservation", "reservation_id"),
    )


class GameRefreshState(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only evaluation of a game's event-relative refresh lifecycle."""

    __tablename__ = "game_refresh_states"
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), nullable=False)
    lifecycle_stage: Mapped[str] = mapped_column(String(32), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    first_pitch_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    time_to_first_pitch_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    last_source_refresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_feature_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("game_feature_snapshots.id"))
    last_forecast_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("forecasts.id"))
    next_scheduled_refresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    final_forecast_status: Mapped[str] = mapped_column(String(32), nullable=False, default="NOT_STARTED")
    final_snapshot_frozen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    freeze_reason: Mapped[str | None] = mapped_column(Text)
    source_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="LIVE")
    __table_args__ = (
        UniqueConstraint(
            "game_id", "evaluated_at", "lifecycle_stage", "source_mode",
            name="uq_game_refresh_state",
        ),
        Index("ix_game_refresh_state_game_asof", "game_id", "available_as_of"),
        Index("ix_game_refresh_state_next", "next_scheduled_refresh_at"),
    )


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

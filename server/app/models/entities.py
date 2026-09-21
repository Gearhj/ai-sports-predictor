"""Canonical, provider-neutral SQLAlchemy entities for AI Sports Predictor."""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, Enum as SAEnum, Float, ForeignKey, ForeignKeyConstraint, Index, Integer, JSON, Numeric, String, Text, UniqueConstraint, false, func, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.db.base import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _insert_parameter(context: Any, name: str) -> Any:
    """Copy a sibling INSERT parameter for legacy direct ORM constructors."""

    return context.get_current_parameters()[name]


def _home_win_default(context: Any) -> bool:
    values = context.get_current_parameters()
    return int(values["home_score"]) > int(values["away_score"])


def _run_differential_default(context: Any) -> int:
    values = context.get_current_parameters()
    return int(values["home_score"]) - int(values["away_score"])


def _total_runs_default(context: Any) -> int:
    values = context.get_current_parameters()
    return int(values["home_score"]) + int(values["away_score"])


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


class ForecastTrack(StrEnum):
    """Mutually exclusive operational and research forecast namespaces."""

    CURRENT_DEVELOPMENT = "CURRENT_DEVELOPMENT"
    GEN2_RESEARCH_CHALLENGER = "GEN2_RESEARCH_CHALLENGER"


class ExperimentDecisionPhase(StrEnum):
    """The three game-day snapshots the $1,000 shadow experiment may freeze.

    FINAL_PREGAME is the only phase that may create the primary virtual position.
    """

    MORNING = "MORNING"
    LINEUP_CONFIRMED = "LINEUP_CONFIRMED"
    FINAL_PREGAME = "FINAL_PREGAME"


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
    #: PostgreSQL now(): the START of the inserting transaction. A job that opens
    #: its transaction, fetches, then inserts stamps rows earlier than the fetch's
    #: own observed/available clocks (94 of 124 V18 starter/lineup inputs on
    #: 2026-09-18). It is not the insert time and not the commit time; a row is
    #: visible to other sessions only after its transaction commits.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class User(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[UserRole] = mapped_column(enum_type(UserRole), nullable=False, default=UserRole.MEMBER)
    status: Mapped[UserStatus] = mapped_column(enum_type(UserStatus), nullable=False, default=UserStatus.ACTIVE)
    # Monotonic session-generation counter. Every issued access token binds the
    # value current at issue time; incrementing it revokes all prior tokens
    # before their natural expiry (logout, password/security-sensitive change).
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
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


class HistoricalSourceVersion(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Immutable authority, eligibility, and timing policy for one source release."""

    __tablename__ = "historical_source_versions"
    source_key: Mapped[str] = mapped_column(String(80), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(80), nullable=False)
    provider_name: Mapped[str] = mapped_column(String(120), nullable=False)
    source_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="LIVE")
    source_classification: Mapped[str] = mapped_column(String(32), nullable=False)
    temporal_semantics: Mapped[str] = mapped_column(
        String(40), nullable=False, default="RETROSPECTIVE_RECONSTRUCTION"
    )
    quality_tier: Mapped[str] = mapped_column(String(16), nullable=False)
    training_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    evaluation_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    availability_policy: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(40), nullable=False)
    governance: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    documentation_uri: Mapped[str | None] = mapped_column(String(500))
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("historical_source_versions.id")
    )
    identity_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "source_key", "dataset_version", "source_mode",
            name="uq_hist_source_key_version_mode",
        ),
        UniqueConstraint("identity_checksum", name="uq_hist_source_identity"),
        CheckConstraint("source_mode IN ('LIVE', 'DEMO')", name="ck_hist_source_mode"),
        CheckConstraint(
            "source_classification IN ('OFFICIAL_PRIMARY', 'LICENSED_MARKET', "
            "'PUBLIC_SECONDARY', 'DERIVED_INTERNAL', 'SYNTHETIC')",
            name="ck_hist_source_classification",
        ),
        CheckConstraint(
            "temporal_semantics = 'RETROSPECTIVE_RECONSTRUCTION'",
            name="ck_hist_source_temporal_semantics",
        ),
        CheckConstraint(
            "quality_tier IN ('A', 'B', 'C', 'EXCLUDED')",
            name="ck_hist_source_quality_tier",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_hist_source_effective_window",
        ),
        CheckConstraint(
            "training_eligible = false OR evaluation_eligible = true",
            name="ck_hist_source_training_requires_eval",
        ),
        CheckConstraint(
            "(training_eligible = false AND evaluation_eligible = false) OR "
            "(source_mode = 'LIVE' AND source_classification <> 'SYNTHETIC' "
            "AND quality_tier <> 'EXCLUDED')",
            name="ck_hist_source_eligibility",
        ),
        CheckConstraint(
            "length(identity_checksum) = 64",
            name="ck_hist_source_identity_length",
        ),
        Index("ix_hist_source_lookup", "source_key", "source_mode", "effective_from"),
        Index("ix_hist_source_eligibility", "training_eligible", "quality_tier"),
    )


class HistoryBackfillCheckpoint(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only terminal result for one resumable historical request chunk."""

    __tablename__ = "history_backfill_checkpoints"
    ingestion_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ingestion_runs.id"), nullable=False
    )
    source_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("historical_source_versions.id"), nullable=False
    )
    parent_checkpoint_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("history_backfill_checkpoints.id")
    )
    stream_key: Mapped[str] = mapped_column(String(120), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    season: Mapped[int | None] = mapped_column(Integer)
    event_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cursor_before: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    cursor_after: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    query_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_received: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_accepted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    raw_references: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    raw_checksums: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    next_resume_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    code_revision: Mapped[str] = mapped_column(String(120), nullable=False)
    quality_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_type: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    source_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="LIVE")
    temporal_semantics: Mapped[str] = mapped_column(
        String(40), nullable=False, default="RETROSPECTIVE_RECONSTRUCTION"
    )
    identity_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "ingestion_run_id", "stream_key", "sequence",
            name="uq_hist_checkpoint_run_stream_sequence",
        ),
        UniqueConstraint("identity_checksum", name="uq_hist_checkpoint_identity"),
        CheckConstraint("source_mode IN ('LIVE', 'DEMO')", name="ck_hist_checkpoint_mode"),
        CheckConstraint(
            "temporal_semantics = 'RETROSPECTIVE_RECONSTRUCTION'",
            name="ck_hist_checkpoint_temporal_semantics",
        ),
        CheckConstraint(
            "status IN ('SUCCEEDED', 'FAILED', 'SKIPPED', 'BLOCKED')",
            name="ck_hist_checkpoint_status",
        ),
        CheckConstraint("sequence >= 0", name="ck_hist_checkpoint_sequence"),
        CheckConstraint(
            "season IS NULL OR season >= 1876",
            name="ck_hist_checkpoint_season",
        ),
        CheckConstraint(
            "(event_start_at IS NULL AND event_end_at IS NULL) OR "
            "(event_start_at IS NOT NULL AND event_end_at IS NOT NULL "
            "AND event_end_at >= event_start_at)",
            name="ck_hist_checkpoint_event_window",
        ),
        CheckConstraint(
            "completed_at >= requested_at",
            name="ck_hist_checkpoint_completion",
        ),
        CheckConstraint(
            "attempt_count >= 1 AND retry_count >= 0 AND retry_count < attempt_count",
            name="ck_hist_checkpoint_attempts",
        ),
        CheckConstraint(
            "records_received >= 0 AND records_accepted >= 0 AND "
            "records_rejected >= 0 AND duplicate_count >= 0",
            name="ck_hist_checkpoint_counts",
        ),
        CheckConstraint(
            "records_accepted + records_rejected + duplicate_count <= records_received",
            name="ck_hist_checkpoint_count_balance",
        ),
        CheckConstraint(
            "length(query_fingerprint) = 64 AND length(identity_checksum) = 64",
            name="ck_hist_checkpoint_checksum_lengths",
        ),
        Index("ix_hist_checkpoint_source_stream", "source_version_id", "stream_key", "sequence"),
        Index("ix_hist_checkpoint_event_window", "event_start_at", "event_end_at"),
        Index("ix_hist_checkpoint_status_completed", "status", "completed_at"),
    )


class GameFinalOutcome(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Immutable, revisioned target truth kept separate from pregame evidence."""

    __tablename__ = "game_final_outcomes"
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), nullable=False)
    season_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("seasons.id"), nullable=False)
    home_team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id"), nullable=False)
    away_team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id"), nullable=False)
    venue_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("venues.id"))
    game_state_observation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("game_state_observations.id")
    )
    source_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("historical_source_versions.id"), nullable=False
    )
    checkpoint_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("history_backfill_checkpoints.id"), nullable=False
    )
    # Nullable only for honest migration of A0 rows, whose schema discarded
    # MLB's officialDate. All A1 canonical writes require the provider value.
    official_date: Mapped[date | None] = mapped_column(Date)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    final_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completion_time_basis: Mapped[str] = mapped_column(
        String(48), nullable=False, default="AUTHORITATIVE_FINAL_AT"
    )
    completion_time_source_field: Mapped[str] = mapped_column(
        String(255), nullable=False, default="legacy:event_completed_at"
    )
    completion_time_method: Mapped[str] = mapped_column(
        Text, nullable=False, default="legacy A0 explicit event_completed_at"
    )
    completion_time_limitation: Mapped[str] = mapped_column(
        Text, nullable=False, default="legacy direct ORM row predates A1 metadata"
    )
    evidence_classification: Mapped[str] = mapped_column(
        String(40), nullable=False, default="RECONSTRUCTABLE_POINT_IN_TIME"
    )
    quality_tier: Mapped[str] = mapped_column(String(16), nullable=False, default="A")
    reconstructed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda context: _insert_parameter(context, "retrieved_at"),
    )
    scheduled_innings: Mapped[int | None] = mapped_column(Integer)
    innings_played: Mapped[int | None] = mapped_column(Integer)
    home_score: Mapped[int] = mapped_column(Integer, nullable=False)
    away_score: Mapped[int] = mapped_column(Integer, nullable=False)
    home_win: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=_home_win_default
    )
    run_differential: Mapped[int] = mapped_column(
        Integer, nullable=False, default=_run_differential_default
    )
    total_runs: Mapped[int] = mapped_column(
        Integer, nullable=False, default=_total_runs_default
    )
    outcome_status: Mapped[str] = mapped_column(String(16), nullable=False, default="FINAL")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    supersedes_outcome_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("game_final_outcomes.id")
    )
    official: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source_record_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    label_available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    availability_basis: Mapped[str] = mapped_column(String(80), nullable=False)
    payload_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    source_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="LIVE")
    temporal_semantics: Mapped[str] = mapped_column(
        String(40), nullable=False, default="RETROSPECTIVE_RECONSTRUCTION"
    )
    identity_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "game_id", "source_version_id", "source_mode", "revision",
            name="uq_game_final_source_revision",
        ),
        UniqueConstraint("identity_checksum", name="uq_game_final_identity"),
        CheckConstraint("source_mode IN ('LIVE', 'DEMO')", name="ck_game_final_mode"),
        CheckConstraint(
            "temporal_semantics = 'RETROSPECTIVE_RECONSTRUCTION'",
            name="ck_game_final_temporal_semantics",
        ),
        CheckConstraint("outcome_status = 'FINAL'", name="ck_game_final_status"),
        CheckConstraint(
            "completion_time_basis IN ('AUTHORITATIVE_FINAL_AT', "
            "'DERIVED_LAST_EVENT_COMPLETION_PROXY')",
            name="ck_game_final_completion_basis",
        ),
        CheckConstraint(
            "evidence_classification IN ('RECONSTRUCTABLE_POINT_IN_TIME', "
            "'PARTIALLY_RECONSTRUCTABLE')",
            name="ck_game_final_evidence_class",
        ),
        CheckConstraint(
            "quality_tier IN ('A', 'B')",
            name="ck_game_final_quality_tier",
        ),
        CheckConstraint(
            "(evidence_classification = 'RECONSTRUCTABLE_POINT_IN_TIME' "
            "AND quality_tier = 'A') OR "
            "(evidence_classification = 'PARTIALLY_RECONSTRUCTABLE' "
            "AND quality_tier = 'B')",
            name="ck_game_final_class_tier",
        ),
        CheckConstraint(
            "home_team_id <> away_team_id",
            name="ck_game_final_distinct_sides",
        ),
        CheckConstraint(
            "home_score >= 0 AND away_score >= 0",
            name="ck_game_final_scores",
        ),
        CheckConstraint(
            "home_score <> away_score",
            name="ck_game_final_no_ties",
        ),
        CheckConstraint(
            "run_differential = home_score - away_score AND "
            "total_runs = home_score + away_score",
            name="ck_game_final_derived_scores",
        ),
        CheckConstraint(
            "(home_win = true AND home_score > away_score) OR "
            "(home_win = false AND home_score < away_score)",
            name="ck_game_final_home_win",
        ),
        CheckConstraint(
            "scheduled_innings IS NULL OR scheduled_innings > 0",
            name="ck_game_final_scheduled_innings",
        ),
        CheckConstraint(
            "innings_played IS NULL OR innings_played > 0",
            name="ck_game_final_innings_played",
        ),
        CheckConstraint("final_at >= scheduled_at", name="ck_game_final_time_order"),
        CheckConstraint(
            "reconstructed_at >= retrieved_at",
            name="ck_game_final_reconstruction_time",
        ),
        CheckConstraint(
            "retrieved_at >= final_at AND label_available_at >= final_at",
            name="ck_game_final_availability",
        ),
        CheckConstraint("revision >= 1", name="ck_game_final_revision"),
        CheckConstraint(
            "supersedes_outcome_id IS NULL OR supersedes_outcome_id <> id",
            name="ck_game_final_not_self_superseding",
        ),
        CheckConstraint(
            "length(payload_checksum) = 64 AND length(identity_checksum) = 64",
            name="ck_game_final_checksum_lengths",
        ),
        Index("ix_game_final_game_available", "game_id", "label_available_at"),
        Index("ix_game_final_season_scheduled", "season_id", "scheduled_at"),
        Index("ix_game_final_official_date", "official_date", "game_id", "revision"),
        Index("ix_game_final_checkpoint", "checkpoint_id"),
    )


class TeamGameHistoryObservation(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Governed team-game fact with explicit event and replay eligibility times."""

    __tablename__ = "team_game_history_observations"
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), nullable=False)
    final_outcome_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("game_final_outcomes.id"), nullable=False
    )
    season_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("seasons.id"), nullable=False)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id"), nullable=False)
    opponent_team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id"), nullable=False)
    is_home: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("historical_source_versions.id"), nullable=False
    )
    checkpoint_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("history_backfill_checkpoints.id"), nullable=False
    )
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    eligible_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    availability_basis: Mapped[str] = mapped_column(String(80), nullable=False)
    evidence_classification: Mapped[str] = mapped_column(String(40), nullable=False)
    quality_tier: Mapped[str] = mapped_column(String(16), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(255), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    supersedes_observation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("team_game_history_observations.id")
    )
    stat_schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    values: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    payload_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    source_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="LIVE")
    temporal_semantics: Mapped[str] = mapped_column(
        String(40), nullable=False, default="RETROSPECTIVE_RECONSTRUCTION"
    )
    identity_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "game_id", "team_id", "source_version_id", "source_mode",
            "source_record_id", "revision",
            name="uq_team_game_history_revision",
        ),
        UniqueConstraint("identity_checksum", name="uq_team_game_history_identity"),
        CheckConstraint("source_mode IN ('LIVE', 'DEMO')", name="ck_team_game_history_mode"),
        CheckConstraint(
            "temporal_semantics = 'RETROSPECTIVE_RECONSTRUCTION'",
            name="ck_team_game_history_temporal",
        ),
        CheckConstraint(
            "evidence_classification IN ('RECONSTRUCTABLE_POINT_IN_TIME', "
            "'PARTIALLY_RECONSTRUCTABLE', 'FORWARD_ONLY')",
            name="ck_team_game_history_class",
        ),
        CheckConstraint(
            "quality_tier IN ('A', 'B', 'C', 'EXCLUDED')",
            name="ck_team_game_history_tier",
        ),
        CheckConstraint("team_id <> opponent_team_id", name="ck_team_game_history_teams"),
        CheckConstraint("revision >= 1", name="ck_team_game_history_revision"),
        CheckConstraint(
            "supersedes_observation_id IS NULL OR supersedes_observation_id <> id",
            name="ck_team_game_history_not_self_superseding",
        ),
        CheckConstraint(
            "evidence_end_at <= event_at AND eligible_at >= evidence_end_at "
            "AND retrieved_at >= evidence_end_at",
            name="ck_team_game_history_times",
        ),
        CheckConstraint(
            "length(payload_checksum) = 64 AND length(identity_checksum) = 64",
            name="ck_team_game_history_checksums",
        ),
        Index("ix_team_game_history_game", "game_id", "event_at"),
        Index("ix_team_game_history_team_eligible", "team_id", "eligible_at"),
        Index(
            "ix_team_game_history_record_revision",
            "game_id", "team_id", "source_version_id", "source_record_id", "revision",
        ),
        Index("ix_team_game_history_checkpoint", "checkpoint_id"),
    )


class PlayerGameHistoryObservation(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Governed player-game participation/stat fact for chronological replay."""

    __tablename__ = "player_game_history_observations"
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), nullable=False)
    final_outcome_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("game_final_outcomes.id"), nullable=False
    )
    season_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("seasons.id"), nullable=False)
    player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("players.id"), nullable=False)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id"), nullable=False)
    opponent_team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    started: Mapped[bool | None] = mapped_column(Boolean)
    source_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("historical_source_versions.id"), nullable=False
    )
    checkpoint_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("history_backfill_checkpoints.id"), nullable=False
    )
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    eligible_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    availability_basis: Mapped[str] = mapped_column(String(80), nullable=False)
    evidence_classification: Mapped[str] = mapped_column(String(40), nullable=False)
    quality_tier: Mapped[str] = mapped_column(String(16), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(255), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    supersedes_observation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("player_game_history_observations.id")
    )
    stat_schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    values: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    payload_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    source_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="LIVE")
    temporal_semantics: Mapped[str] = mapped_column(
        String(40), nullable=False, default="RETROSPECTIVE_RECONSTRUCTION"
    )
    identity_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "game_id", "player_id", "team_id", "role", "source_version_id",
            "source_mode", "source_record_id", "revision",
            name="uq_player_game_history_revision",
        ),
        UniqueConstraint("identity_checksum", name="uq_player_game_history_identity"),
        CheckConstraint("source_mode IN ('LIVE', 'DEMO')", name="ck_player_game_history_mode"),
        CheckConstraint(
            "temporal_semantics = 'RETROSPECTIVE_RECONSTRUCTION'",
            name="ck_player_game_history_temporal",
        ),
        CheckConstraint(
            "evidence_classification IN ('RECONSTRUCTABLE_POINT_IN_TIME', "
            "'PARTIALLY_RECONSTRUCTABLE', 'FORWARD_ONLY')",
            name="ck_player_game_history_class",
        ),
        CheckConstraint(
            "quality_tier IN ('A', 'B', 'C', 'EXCLUDED')",
            name="ck_player_game_history_tier",
        ),
        CheckConstraint("team_id <> opponent_team_id", name="ck_player_game_history_teams"),
        CheckConstraint("revision >= 1", name="ck_player_game_history_revision"),
        CheckConstraint(
            "supersedes_observation_id IS NULL OR supersedes_observation_id <> id",
            name="ck_player_game_history_not_self_superseding",
        ),
        CheckConstraint(
            "evidence_end_at <= event_at AND eligible_at >= evidence_end_at "
            "AND retrieved_at >= evidence_end_at",
            name="ck_player_game_history_times",
        ),
        CheckConstraint(
            "length(payload_checksum) = 64 AND length(identity_checksum) = 64",
            name="ck_player_game_history_checksums",
        ),
        Index("ix_player_game_history_game", "game_id", "event_at"),
        Index("ix_player_game_history_player_eligible", "player_id", "eligible_at"),
        Index("ix_player_game_history_team_eligible", "team_id", "eligible_at"),
        Index(
            "ix_player_game_history_record_revision",
            "game_id", "player_id", "team_id", "role", "source_version_id",
            "source_record_id", "revision",
        ),
        Index("ix_player_game_history_checkpoint", "checkpoint_id"),
    )


class HistoricalFeatureMaterialization(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Governed bridge from reconstructed evidence to an immutable feature snapshot."""

    __tablename__ = "historical_feature_materializations"
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), nullable=False)
    game_feature_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("game_feature_snapshots.id"), nullable=False, unique=True
    )
    feature_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("feature_versions.id"), nullable=False
    )
    game_final_outcome_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("game_final_outcomes.id")
    )
    checkpoint_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("history_backfill_checkpoints.id"), nullable=False
    )
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cutoff_policy_version: Mapped[str] = mapped_column(String(40), nullable=False)
    cutoff_policy: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source_bundle: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    source_bundle_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_manifest: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    evidence_manifest_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    quality_tier: Mapped[str] = mapped_column(String(16), nullable=False)
    quality_score: Mapped[float] = mapped_column(Float, nullable=False)
    quality_components: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    materializer_version: Mapped[str] = mapped_column(String(80), nullable=False)
    code_revision: Mapped[str] = mapped_column(String(120), nullable=False)
    eligibility_status: Mapped[str] = mapped_column(String(24), nullable=False)
    eligibility_reasons: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    training_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="LIVE")
    temporal_semantics: Mapped[str] = mapped_column(
        String(40), nullable=False, default="RETROSPECTIVE_RECONSTRUCTION"
    )
    identity_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (
        UniqueConstraint("identity_checksum", name="uq_hist_materialization_identity"),
        UniqueConstraint(
            "game_id", "cutoff_at", "feature_version_id", "cutoff_policy_version",
            "source_mode", "materializer_version", "evidence_manifest_checksum",
            name="uq_hist_materialization_semantic",
        ),
        CheckConstraint("source_mode IN ('LIVE', 'DEMO')", name="ck_hist_materialization_mode"),
        CheckConstraint(
            "temporal_semantics = 'RETROSPECTIVE_RECONSTRUCTION'",
            name="ck_hist_materialization_temporal",
        ),
        CheckConstraint(
            "quality_tier IN ('A', 'B', 'C', 'EXCLUDED')",
            name="ck_hist_materialization_tier",
        ),
        CheckConstraint(
            "quality_score >= 0 AND quality_score <= 100",
            name="ck_hist_materialization_quality",
        ),
        CheckConstraint(
            "eligibility_status IN ('ELIGIBLE', 'INELIGIBLE', 'INSUFFICIENT_DATA')",
            name="ck_hist_materialization_status",
        ),
        CheckConstraint(
            "cutoff_at < scheduled_at AND generated_at >= cutoff_at",
            name="ck_hist_materialization_times",
        ),
        CheckConstraint(
            "training_eligible = false OR (eligibility_status = 'ELIGIBLE' "
            "AND source_mode = 'LIVE' AND quality_tier IN ('A', 'B'))",
            name="ck_hist_materialization_training",
        ),
        CheckConstraint(
            "length(source_bundle_checksum) = 64 "
            "AND length(evidence_manifest_checksum) = 64 "
            "AND length(identity_checksum) = 64",
            name="ck_hist_materialization_checksums",
        ),
        Index("ix_hist_materialization_game_cutoff", "game_id", "cutoff_at"),
        Index("ix_hist_materialization_checkpoint", "checkpoint_id"),
        Index(
            "ix_hist_materialization_eligibility",
            "training_eligible", "quality_tier", "scheduled_at",
        ),
    )


class HistoricalEvidenceStateAttestation(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Compact, append-only proof of the canonical evidence state at a cutoff."""

    __tablename__ = "historical_evidence_state_attestations"
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), nullable=False)
    season_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("seasons.id"), nullable=False)
    feature_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("feature_versions.id"), nullable=False
    )
    checkpoint_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("history_backfill_checkpoints.id"), nullable=False
    )
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state_version: Mapped[str] = mapped_column(String(80), nullable=False)
    population: Mapped[str] = mapped_column(String(40), nullable=False)
    outcome_history_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    team_history_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    player_history_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    statcast_event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    evidence_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evidence_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    eligible_max_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    family_counts: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    family_checksums: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    source_bundle: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    source_bundle_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_set_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    feature_values_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    target_excluded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    temporal_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="LIVE")
    temporal_semantics: Mapped[str] = mapped_column(
        String(40), nullable=False, default="RETROSPECTIVE_RECONSTRUCTION"
    )
    identity_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "game_id",
            "cutoff_at",
            "feature_version_id",
            "state_version",
            "population",
            "source_mode",
            "canonical_set_checksum",
            name="uq_hist_evidence_state_semantic",
        ),
        UniqueConstraint(
            "identity_checksum",
            name="uq_hist_evidence_state_identity",
        ),
        CheckConstraint(
            "outcome_history_count >= 0 AND team_history_count >= 0 "
            "AND player_history_count >= 0 "
            "AND statcast_event_count >= 0",
            name="ck_hist_evidence_state_counts",
        ),
        CheckConstraint(
            "generated_at >= cutoff_at",
            name="ck_hist_evidence_state_generation",
        ),
        CheckConstraint(
            "((outcome_history_count + team_history_count + player_history_count "
            "+ statcast_event_count = 0 "
            "AND evidence_start_at IS NULL AND evidence_end_at IS NULL "
            "AND eligible_max_at IS NULL) OR "
            "(outcome_history_count + team_history_count + player_history_count "
            "+ statcast_event_count > 0 "
            "AND evidence_start_at IS NOT NULL AND evidence_end_at IS NOT NULL "
            "AND eligible_max_at IS NOT NULL))",
            name="ck_hist_evidence_state_bounds_presence",
        ),
        CheckConstraint(
            "evidence_start_at IS NULL OR "
            "(evidence_end_at >= evidence_start_at AND evidence_end_at < cutoff_at "
            "AND eligible_max_at >= evidence_end_at "
            "AND eligible_max_at <= cutoff_at)",
            name="ck_hist_evidence_state_bounds_order",
        ),
        CheckConstraint(
            "population IN ('TIER_A_STRICT', 'TIER_B_ENHANCED')",
            name="ck_hist_evidence_state_population",
        ),
        CheckConstraint(
            "source_mode IN ('LIVE', 'DEMO')",
            name="ck_hist_evidence_state_mode",
        ),
        CheckConstraint(
            "temporal_semantics = 'RETROSPECTIVE_RECONSTRUCTION'",
            name="ck_hist_evidence_state_temporal",
        ),
        CheckConstraint(
            "length(source_bundle_checksum) = 64 "
            "AND length(canonical_set_checksum) = 64 "
            "AND length(feature_values_checksum) = 64 "
            "AND length(identity_checksum) = 64",
            name="ck_hist_evidence_state_checksums",
        ),
        Index("ix_hist_evidence_state_game_cutoff", "game_id", "cutoff_at"),
        Index("ix_hist_evidence_state_checkpoint", "checkpoint_id"),
        Index(
            "ix_hist_evidence_state_season_population",
            "season_id",
            "population",
            "generated_at",
        ),
    )


class SourceOwnerApproval(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Immutable project-owner decision for one historical source and use scope."""

    __tablename__ = "source_owner_approvals"
    source_key: Mapped[str] = mapped_column(String(80), nullable=False)
    approval_scope: Mapped[str] = mapped_column(
        String(40), nullable=False, default="LOCAL_PRIVATE_RESEARCH",
        server_default="LOCAL_PRIVATE_RESEARCH",
    )
    decision: Mapped[str] = mapped_column(
        String(16), nullable=False, default="DENIED", server_default="DENIED"
    )
    approved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    decided_by: Mapped[str] = mapped_column(String(160), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    authorization_reference: Mapped[str | None] = mapped_column(String(500))
    conditions: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    identity_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (
        UniqueConstraint("identity_checksum", name="uq_source_owner_approval_identity"),
        CheckConstraint(
            "approval_scope = 'LOCAL_PRIVATE_RESEARCH'",
            name="ck_source_owner_approval_scope",
        ),
        CheckConstraint(
            "decision IN ('APPROVED', 'DENIED')",
            name="ck_source_owner_approval_decision",
        ),
        CheckConstraint(
            "(approved = true AND decision = 'APPROVED') OR "
            "(approved = false AND decision = 'DENIED')",
            name="ck_source_owner_approval_consistency",
        ),
        CheckConstraint(
            "expires_at IS NULL OR expires_at > decided_at",
            name="ck_source_owner_approval_expiry",
        ),
        CheckConstraint(
            "length(identity_checksum) = 64",
            name="ck_source_owner_approval_identity_length",
        ),
        Index("ix_source_owner_approval_source_decided", "source_key", "decided_at"),
    )


class ProviderAccessAuthorization(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Immutable, default-denied decision governing new provider access."""

    __tablename__ = "provider_access_authorizations"
    owner_approval_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_owner_approvals.id"), nullable=False
    )
    source_key: Mapped[str] = mapped_column(String(80), nullable=False)
    provider_name: Mapped[str] = mapped_column(String(120), nullable=False)
    access_method: Mapped[str] = mapped_column(String(80), nullable=False)
    decision: Mapped[str] = mapped_column(
        String(16), nullable=False, default="DENIED", server_default="DENIED"
    )
    authorized: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    decided_by: Mapped[str] = mapped_column(String(160), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terms_reference: Mapped[str | None] = mapped_column(String(500))
    terms_checksum: Mapped[str | None] = mapped_column(String(64))
    decision_basis: Mapped[str] = mapped_column(Text, nullable=False)
    identity_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "identity_checksum", name="uq_provider_access_authorization_identity"
        ),
        CheckConstraint(
            "decision IN ('AUTHORIZED', 'DENIED', 'UNRESOLVED')",
            name="ck_provider_access_authorization_decision",
        ),
        CheckConstraint(
            "(authorized = true AND decision = 'AUTHORIZED') OR "
            "(authorized = false AND decision IN ('DENIED', 'UNRESOLVED'))",
            name="ck_provider_access_authorization_consistency",
        ),
        CheckConstraint(
            "expires_at IS NULL OR expires_at > decided_at",
            name="ck_provider_access_authorization_expiry",
        ),
        CheckConstraint(
            "terms_checksum IS NULL OR length(terms_checksum) = 64",
            name="ck_provider_access_authorization_terms_checksum",
        ),
        CheckConstraint(
            "length(identity_checksum) = 64",
            name="ck_provider_access_authorization_identity_length",
        ),
        Index(
            "ix_provider_access_authorization_source_decided",
            "source_key",
            "decided_at",
        ),
        Index(
            "ix_provider_access_authorization_owner",
            "owner_approval_id",
        ),
    )


class ExistingEvidenceDerivedUseDecision(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Immutable decision for local derivation from evidence already on disk."""

    __tablename__ = "existing_evidence_derived_use_decisions"
    owner_approval_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_owner_approvals.id"), nullable=False
    )
    source_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("historical_source_versions.id")
    )
    source_key: Mapped[str] = mapped_column(String(80), nullable=False)
    evidence_manifest_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    use_scope: Mapped[str] = mapped_column(
        String(40), nullable=False, default="LOCAL_PRIVATE_RESEARCH",
        server_default="LOCAL_PRIVATE_RESEARCH",
    )
    decision: Mapped[str] = mapped_column(
        String(16), nullable=False, default="DENIED", server_default="DENIED"
    )
    allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    decided_by: Mapped[str] = mapped_column(String(160), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_basis: Mapped[str] = mapped_column(Text, nullable=False)
    identity_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "identity_checksum", name="uq_existing_evidence_use_identity"
        ),
        CheckConstraint(
            "use_scope = 'LOCAL_PRIVATE_RESEARCH'",
            name="ck_existing_evidence_use_scope",
        ),
        CheckConstraint(
            "decision IN ('ALLOWED', 'DENIED', 'UNRESOLVED')",
            name="ck_existing_evidence_use_decision",
        ),
        CheckConstraint(
            "(allowed = true AND decision = 'ALLOWED') OR "
            "(allowed = false AND decision IN ('DENIED', 'UNRESOLVED'))",
            name="ck_existing_evidence_use_consistency",
        ),
        CheckConstraint(
            "expires_at IS NULL OR expires_at > decided_at",
            name="ck_existing_evidence_use_expiry",
        ),
        CheckConstraint(
            "length(evidence_manifest_checksum) = 64 "
            "AND length(identity_checksum) = 64",
            name="ck_existing_evidence_use_checksum_lengths",
        ),
        Index("ix_existing_evidence_use_source_decided", "source_key", "decided_at"),
        Index("ix_existing_evidence_use_source_version", "source_version_id"),
    )


class HistoricalAcquisitionStage(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only state record for one bounded, resumable acquisition stage."""

    __tablename__ = "historical_acquisition_stages"
    source_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("historical_source_versions.id"), nullable=False
    )
    owner_approval_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_owner_approvals.id"), nullable=False
    )
    provider_authorization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("provider_access_authorizations.id")
    )
    supersedes_stage_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("historical_acquisition_stages.id")
    )
    stage_key: Mapped[str] = mapped_column(String(40), nullable=False)
    stage_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    target_family: Mapped[str] = mapped_column(String(40), nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int | None] = mapped_column(Integer)
    event_start_date: Mapped[date | None] = mapped_column(Date)
    event_end_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checkpoint_identity: Mapped[str | None] = mapped_column(String(64))
    next_resume_point: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    physical_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rate_limit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_received: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    bytes_received: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    elapsed_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    identity_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "source_version_id",
            "stage_key",
            "stage_revision",
            name="uq_historical_acquisition_stage_revision",
        ),
        UniqueConstraint(
            "identity_checksum", name="uq_historical_acquisition_stage_identity"
        ),
        CheckConstraint("stage_revision >= 1", name="ck_historical_acquisition_stage_revision"),
        CheckConstraint("season >= 1876", name="ck_historical_acquisition_stage_season"),
        CheckConstraint(
            "month IS NULL OR (month >= 1 AND month <= 12)",
            name="ck_historical_acquisition_stage_month",
        ),
        CheckConstraint(
            "(event_start_date IS NULL AND event_end_date IS NULL) OR "
            "(event_start_date IS NOT NULL AND event_end_date IS NOT NULL "
            "AND event_end_date >= event_start_date)",
            name="ck_historical_acquisition_stage_window",
        ),
        CheckConstraint(
            "target_family IN ('MLB_CORE', 'STATCAST')",
            name="ck_historical_acquisition_stage_family",
        ),
        CheckConstraint(
            "status IN ('PLANNED', 'BLOCKED', 'AUTHORIZED', 'RUNNING', "
            "'SUCCEEDED', 'FAILED', 'SKIPPED')",
            name="ck_historical_acquisition_stage_status",
        ),
        CheckConstraint(
            "started_at IS NULL OR started_at <= recorded_at",
            name="ck_historical_acquisition_stage_start",
        ),
        CheckConstraint(
            "completed_at IS NULL OR (started_at IS NOT NULL "
            "AND completed_at >= started_at AND completed_at <= recorded_at)",
            name="ck_historical_acquisition_stage_completion",
        ),
        CheckConstraint(
            "physical_attempt_count >= 0 AND success_count >= 0 "
            "AND failure_count >= 0 AND retry_count >= 0 AND rate_limit_count >= 0 "
            "AND success_count + failure_count + rate_limit_count <= physical_attempt_count",
            name="ck_historical_acquisition_stage_attempt_counts",
        ),
        CheckConstraint(
            "records_received >= 0 AND bytes_received >= 0 AND elapsed_ms >= 0",
            name="ck_historical_acquisition_stage_volume",
        ),
        CheckConstraint(
            "checkpoint_identity IS NULL OR length(checkpoint_identity) = 64",
            name="ck_historical_acquisition_stage_checkpoint",
        ),
        CheckConstraint(
            "length(identity_checksum) = 64",
            name="ck_historical_acquisition_stage_identity_length",
        ),
        Index("ix_historical_acquisition_stage_status", "stage_key", "status"),
        Index("ix_historical_acquisition_stage_source", "source_version_id", "recorded_at"),
    )


class HistoricalRequestAttempt(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Secret-free append-only accounting for one physical acquisition attempt."""

    __tablename__ = "historical_request_attempts"
    acquisition_stage_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("historical_acquisition_stages.id"), nullable=False
    )
    checkpoint_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("history_backfill_checkpoints.id")
    )
    provider_ledger_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("provider_request_ledger.id")
    )
    endpoint_key: Mapped[str] = mapped_column(String(120), nullable=False)
    attempt_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    http_status_code: Mapped[int | None] = mapped_column(Integer)
    records_received: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    bytes_received: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    elapsed_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    retry_after_seconds: Mapped[int | None] = mapped_column(Integer)
    error_type: Mapped[str | None] = mapped_column(String(120))
    error_message: Mapped[str | None] = mapped_column(Text)
    identity_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "acquisition_stage_id",
            "attempt_sequence",
            name="uq_historical_request_attempt_sequence",
        ),
        UniqueConstraint(
            "identity_checksum", name="uq_historical_request_attempt_identity"
        ),
        CheckConstraint("attempt_sequence >= 1", name="ck_historical_request_attempt_sequence"),
        CheckConstraint(
            "status IN ('SUCCEEDED', 'FAILED', 'RATE_LIMITED')",
            name="ck_historical_request_attempt_status",
        ),
        CheckConstraint(
            "completed_at >= requested_at",
            name="ck_historical_request_attempt_completion",
        ),
        CheckConstraint(
            "http_status_code IS NULL OR (http_status_code >= 100 AND http_status_code <= 599)",
            name="ck_historical_request_attempt_http_status",
        ),
        CheckConstraint(
            "records_received >= 0 AND bytes_received >= 0 AND elapsed_ms >= 0 "
            "AND (retry_after_seconds IS NULL OR retry_after_seconds >= 0)",
            name="ck_historical_request_attempt_volume",
        ),
        CheckConstraint(
            "length(request_fingerprint) = 64 AND length(identity_checksum) = 64",
            name="ck_historical_request_attempt_checksum_lengths",
        ),
        Index("ix_historical_request_attempt_stage", "acquisition_stage_id", "attempt_sequence"),
        Index("ix_historical_request_attempt_status", "status", "requested_at"),
    )


class HistoricalEventPartition(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only metadata for one governed Gen-2 analytical Parquet file."""

    __tablename__ = "historical_event_partitions"
    source_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("historical_source_versions.id"), nullable=False
    )
    checkpoint_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("history_backfill_checkpoints.id"), nullable=False
    )
    event_family: Mapped[str] = mapped_column(String(32), nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    logical_uri: Mapped[str] = mapped_column(String(1000), nullable=False)
    row_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    game_count: Mapped[int] = mapped_column(Integer, nullable=False)
    plate_appearance_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    player_count: Mapped[int] = mapped_column(Integer, nullable=False)
    min_event_date: Mapped[date | None] = mapped_column(Date)
    max_event_date: Mapped[date | None] = mapped_column(Date)
    min_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    storage_format: Mapped[str] = mapped_column(String(16), nullable=False, default="PARQUET")
    compression: Mapped[str] = mapped_column(String(16), nullable=False, default="ZSTD")
    schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    schema_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    raw_contributor_manifest: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    raw_contributor_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    code_revision: Mapped[str] = mapped_column(String(120), nullable=False)
    evidence_tier: Mapped[str] = mapped_column(String(16), nullable=False)
    timing_classification: Mapped[str] = mapped_column(String(80), nullable=False)
    identity_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (
        UniqueConstraint("logical_uri", name="uq_historical_event_partition_uri"),
        UniqueConstraint(
            "identity_checksum", name="uq_historical_event_partition_identity"
        ),
        CheckConstraint(
            "event_family IN ('pitch', 'plate_appearance', 'play_transition', "
            "'pitch_sequence', 'player_game_batting', 'player_game_pitching', "
            "'player_game_fielding', 'starter_usage', 'bullpen_usage', "
            "'weather', 'player_prior')",
            name="ck_historical_event_partition_family",
        ),
        CheckConstraint("season >= 1876", name="ck_historical_event_partition_season"),
        CheckConstraint(
            "month >= 1 AND month <= 12",
            name="ck_historical_event_partition_month",
        ),
        CheckConstraint(
            "logical_uri LIKE 'historical-events/%' "
            "AND logical_uri NOT LIKE '/%' "
            "AND logical_uri NOT LIKE '%..%' "
            "AND replace(logical_uri, '\\', '') = logical_uri "
            "AND logical_uri LIKE '%.parquet'",
            name="ck_historical_event_partition_logical_uri",
        ),
        CheckConstraint(
            "row_count > 0 AND game_count >= 0 "
            "AND plate_appearance_count >= 0 AND player_count >= 0 "
            "AND (event_family NOT IN ('pitch', 'plate_appearance', "
            "'play_transition', 'pitch_sequence') OR "
            "(game_count > 0 AND plate_appearance_count > 0 AND player_count > 0)) "
            "AND (event_family NOT IN ('player_game_batting', "
            "'player_game_pitching', 'player_game_fielding', 'starter_usage', "
            "'bullpen_usage') OR (game_count > 0 AND player_count > 0)) "
            "AND (event_family <> 'weather' OR game_count > 0) "
            "AND (event_family <> 'player_prior' OR player_count > 0)",
            name="ck_historical_event_partition_counts",
        ),
        CheckConstraint(
            "(min_event_date IS NULL AND max_event_date IS NULL) OR "
            "(min_event_date IS NOT NULL AND max_event_date IS NOT NULL "
            "AND max_event_date >= min_event_date)",
            name="ck_historical_event_partition_date_bounds",
        ),
        CheckConstraint(
            "(min_event_at IS NULL AND max_event_at IS NULL) OR "
            "(min_event_at IS NOT NULL AND max_event_at IS NOT NULL "
            "AND max_event_at >= min_event_at)",
            name="ck_historical_event_partition_time_bounds",
        ),
        CheckConstraint(
            "storage_format = 'PARQUET' AND compression = 'ZSTD'",
            name="ck_historical_event_partition_storage",
        ),
        CheckConstraint(
            "file_size_bytes > 0",
            name="ck_historical_event_partition_file_size",
        ),
        CheckConstraint(
            "evidence_tier IN ('A', 'B', 'C', 'EXCLUDED')",
            name="ck_historical_event_partition_tier",
        ),
        CheckConstraint(
            "length(schema_hash) = 64 AND length(file_sha256) = 64 "
            "AND length(raw_contributor_checksum) = 64 "
            "AND length(identity_checksum) = 64",
            name="ck_historical_event_partition_checksums",
        ),
        Index(
            "ix_historical_event_partition_source",
            "source_version_id",
            "event_family",
            "season",
            "month",
        ),
        Index("ix_historical_event_partition_checkpoint", "checkpoint_id"),
        Index("ix_historical_event_partition_events", "min_event_at", "max_event_at"),
    )


class OpenDataDownloadManifest(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only physical-download evidence for one official open-data object."""

    __tablename__ = "open_data_download_manifests"
    source_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("historical_source_versions.id"), nullable=False
    )
    source_key: Mapped[str] = mapped_column(String(80), nullable=False)
    object_key: Mapped[str] = mapped_column(String(120), nullable=False)
    source_release: Mapped[str] = mapped_column(String(120), nullable=False)
    upstream_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    official_download_uri: Mapped[str] = mapped_column(String(1000), nullable=False)
    downloaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    http_status: Mapped[int] = mapped_column(Integer, nullable=False)
    content_length: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(160), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    archive_sha256: Mapped[str | None] = mapped_column(String(64))
    license_identity: Mapped[str] = mapped_column(String(160), nullable=False)
    attribution_identity: Mapped[str] = mapped_column(String(64), nullable=False)
    physical_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    raw_logical_uri: Mapped[str] = mapped_column(String(1000), nullable=False)
    semantic_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    identity_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "source_version_id",
            "object_key",
            "upstream_revision",
            name="uq_open_data_download_object_revision",
        ),
        UniqueConstraint(
            "identity_checksum", name="uq_open_data_download_identity"
        ),
        CheckConstraint(
            "upstream_revision >= 1 AND content_length > 0 "
            "AND physical_attempt_count >= 1 AND retry_count >= 0 "
            "AND retry_count < physical_attempt_count",
            name="ck_open_data_download_counts",
        ),
        CheckConstraint(
            "http_status >= 200 AND http_status <= 299",
            name="ck_open_data_download_http_status",
        ),
        CheckConstraint(
            "official_download_uri LIKE 'https://%' "
            "AND raw_logical_uri NOT LIKE '/%' "
            "AND raw_logical_uri NOT LIKE '%..%' "
            "AND replace(raw_logical_uri, '\\', '') = raw_logical_uri",
            name="ck_open_data_download_uris",
        ),
        CheckConstraint(
            "length(payload_sha256) = 64 "
            "AND (archive_sha256 IS NULL OR length(archive_sha256) = 64) "
            "AND length(attribution_identity) = 64 "
            "AND length(semantic_fingerprint) = 64 "
            "AND length(identity_checksum) = 64",
            name="ck_open_data_download_checksums",
        ),
        Index("ix_open_data_download_source", "source_key", "source_release"),
        Index("ix_open_data_download_time", "downloaded_at"),
    )


class OpenDataIdentityMapping(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only, row-bound crosswalk evidence from an open-data release."""

    __tablename__ = "open_data_identity_mappings"
    source_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("historical_source_versions.id"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_namespace: Mapped[str] = mapped_column(String(80), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_namespace: Mapped[str] = mapped_column(String(80), nullable=False)
    canonical_id: Mapped[str | None] = mapped_column(String(255))
    source_row_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_row_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[str] = mapped_column(String(24), nullable=False)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_through: Mapped[date | None] = mapped_column(Date)
    evidence_logical_uri: Mapped[str] = mapped_column(String(1000), nullable=False)
    identity_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "identity_checksum", name="uq_open_data_identity_mapping_identity"
        ),
        UniqueConstraint(
            "source_version_id",
            "entity_type",
            "source_namespace",
            "external_id",
            "source_row_number",
            name="uq_open_data_identity_mapping_natural",
        ),
        CheckConstraint(
            "entity_type IN ('person', 'team', 'franchise', 'park', 'game', 'umpire')",
            name="ck_open_data_identity_mapping_entity",
        ),
        CheckConstraint(
            "confidence IN ('EXACT', 'HIGH', 'UNRESOLVED', 'CONFLICT')",
            name="ck_open_data_identity_mapping_confidence",
        ),
        CheckConstraint(
            "source_row_number >= 1",
            name="ck_open_data_identity_mapping_row",
        ),
        CheckConstraint(
            "(confidence IN ('EXACT', 'HIGH') AND canonical_id IS NOT NULL) "
            "OR (confidence IN ('UNRESOLVED', 'CONFLICT'))",
            name="ck_open_data_identity_mapping_resolution",
        ),
        CheckConstraint(
            "valid_through IS NULL OR valid_from IS NULL OR valid_through >= valid_from",
            name="ck_open_data_identity_mapping_window",
        ),
        CheckConstraint(
            "evidence_logical_uri NOT LIKE '/%' "
            "AND evidence_logical_uri NOT LIKE '%..%' "
            "AND replace(evidence_logical_uri, '\\', '') = evidence_logical_uri",
            name="ck_open_data_identity_mapping_uri",
        ),
        CheckConstraint(
            "length(source_row_checksum) = 64 AND length(identity_checksum) = 64",
            name="ck_open_data_identity_mapping_checksums",
        ),
        Index(
            "ix_open_data_identity_external",
            "source_namespace",
            "external_id",
        ),
        Index(
            "ix_open_data_identity_canonical",
            "canonical_namespace",
            "canonical_id",
        ),
    )


class ProspectiveEvaluationPlan(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only identity for one frozen Gen-2 prospective evaluation window."""

    __tablename__ = "prospective_evaluation_plans"
    plan_id: Mapped[str] = mapped_column(String(120), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(100), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(100), nullable=False)
    policy_identity_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    research_policy_version: Mapped[str] = mapped_column(String(100), nullable=False)
    evaluation_starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    target_season: Mapped[int] = mapped_column(Integer, nullable=False)
    lifecycle: Mapped[str] = mapped_column(String(40), nullable=False)
    forecast_lifecycle: Mapped[str] = mapped_column(String(20), nullable=False)
    forecast_track: Mapped[str] = mapped_column(String(40), nullable=False)
    forecast_family: Mapped[str] = mapped_column(String(40), nullable=False)
    registration_status: Mapped[str] = mapped_column(String(60), nullable=False)
    interim_completed_games: Mapped[int] = mapped_column(Integer, nullable=False)
    final_completed_games: Mapped[int] = mapped_column(Integer, nullable=False)
    metrics: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    baselines: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    policy_manifest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    code_revision: Mapped[str] = mapped_column(String(120), nullable=False)
    code_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_bundle_version: Mapped[str] = mapped_column(String(120), nullable=False)
    schema_bundle_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    component_identities: Mapped[list[dict[str, str]]] = mapped_column(
        JSON, nullable=False
    )
    plan_identity_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (
        UniqueConstraint("plan_id", name="uq_prospective_plan_id"),
        UniqueConstraint(
            "plan_identity_checksum", name="uq_prospective_plan_identity"
        ),
        UniqueConstraint(
            "id",
            "plan_identity_checksum",
            name="uq_prospective_plan_row_identity",
        ),
        CheckConstraint(
            "schema_version = 'mlb_gen2_prospective_evaluation_plan_v1' "
            "AND policy_version = 'mlb_gen2_prospective_policy_v1' "
            "AND research_policy_version = 'MLB_GEN2_RESEARCH_POLICY_V1'",
            name="ck_prospective_plan_versions",
        ),
        CheckConstraint(
            "target_season = 2026",
            name="ck_prospective_plan_season",
        ),
        CheckConstraint(
            "lifecycle = 'FINAL_T_MINUS_10' AND forecast_lifecycle = 'T-10'",
            name="ck_prospective_plan_lifecycle",
        ),
        CheckConstraint(
            "forecast_track = 'GEN2_RESEARCH_CHALLENGER' "
            "AND forecast_family = 'ASP_INDEPENDENT' "
            "AND registration_status = 'GEN2_PROSPECTIVE_RESEARCH_CHALLENGER'",
            name="ck_prospective_plan_research_track",
        ),
        CheckConstraint(
            "interim_completed_games = 300 AND final_completed_games = 500",
            name="ck_prospective_plan_thresholds",
        ),
        CheckConstraint(
            "evaluation_starts_at >= created_at",
            name="ck_prospective_plan_window",
        ),
        CheckConstraint(
            "length(policy_identity_checksum) = 64 "
            "AND length(code_checksum) = 64 "
            "AND length(schema_bundle_checksum) = 64 "
            "AND length(plan_identity_checksum) = 64",
            name="ck_prospective_plan_checksums",
        ),
        Index("ix_prospective_plan_window", "target_season", "evaluation_starts_at"),
        Index(
            "ix_prospective_plan_registration",
            "forecast_track",
            "registration_status",
        ),
    )


class ProspectiveEvaluationClaim(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only, globally single-use claim for one plan/checkpoint key."""

    __tablename__ = "prospective_evaluation_claims"
    evaluation_plan_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False
    )
    plan_identity_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(60), nullable=False)
    checkpoint: Mapped[str] = mapped_column(String(60), nullable=False)
    minimum_completed_games: Mapped[int] = mapped_column(Integer, nullable=False)
    eligible_game_count: Mapped[int] = mapped_column(Integer, nullable=False)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    eligible_set_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    code_revision: Mapped[str] = mapped_column(String(120), nullable=False)
    code_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_bundle_version: Mapped[str] = mapped_column(String(120), nullable=False)
    schema_bundle_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    global_claim_identity_checksum: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    claim_identity_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "plan_identity_checksum",
            "checkpoint",
            name="uq_prospective_claim_plan_checkpoint",
        ),
        UniqueConstraint(
            "global_claim_identity_checksum",
            name="uq_prospective_claim_global_identity",
        ),
        UniqueConstraint(
            "claim_identity_checksum", name="uq_prospective_claim_identity"
        ),
        UniqueConstraint(
            "id",
            "evaluation_plan_id",
            "plan_identity_checksum",
            "checkpoint",
            name="uq_prospective_claim_row_plan_checkpoint",
        ),
        UniqueConstraint(
            "id",
            "plan_identity_checksum",
            "global_claim_identity_checksum",
            "claim_identity_checksum",
            name="uq_prospective_claim_row_identities",
        ),
        CheckConstraint(
            "schema_version = 'mlb_gen2_prospective_evaluation_claim_v1' "
            "AND status = 'PROSPECTIVE_LABEL_ACCESS_AUTHORIZED'",
            name="ck_prospective_claim_version_status",
        ),
        CheckConstraint(
            "checkpoint IN ('INTERIM_300_COMPLETED_ELIGIBLE_GAMES', "
            "'FINAL_500_COMPLETED_ELIGIBLE_GAMES')",
            name="ck_prospective_claim_checkpoint",
        ),
        CheckConstraint(
            "(checkpoint = 'INTERIM_300_COMPLETED_ELIGIBLE_GAMES' "
            "AND minimum_completed_games = 300 AND eligible_game_count >= 300) OR "
            "(checkpoint = 'FINAL_500_COMPLETED_ELIGIBLE_GAMES' "
            "AND minimum_completed_games = 500 AND eligible_game_count >= 500)",
            name="ck_prospective_claim_threshold",
        ),
        CheckConstraint(
            "length(eligible_set_checksum) = 64 "
            "AND length(plan_identity_checksum) = 64 "
            "AND length(code_checksum) = 64 "
            "AND length(schema_bundle_checksum) = 64 "
            "AND length(global_claim_identity_checksum) = 64 "
            "AND length(claim_identity_checksum) = 64",
            name="ck_prospective_claim_checksums",
        ),
        ForeignKeyConstraint(
            ["evaluation_plan_id", "plan_identity_checksum"],
            [
                "prospective_evaluation_plans.id",
                "prospective_evaluation_plans.plan_identity_checksum",
            ],
            name="fk_prospective_claim_plan_identity",
        ),
        Index(
            "ix_prospective_claim_plan_time", "evaluation_plan_id", "claimed_at"
        ),
        Index("ix_prospective_claim_checkpoint", "checkpoint", "claimed_at"),
    )


class ProspectiveEvaluationGameAssignment(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Global occurrence-to-plan assignment preventing cross-window evidence reuse."""

    __tablename__ = "prospective_evaluation_game_assignments"
    evaluation_plan_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    plan_identity_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    game_id: Mapped[str] = mapped_column(String(120), nullable=False)
    occurrence_id: Mapped[str] = mapped_column(String(160), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    assignment_identity_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "game_id",
            "occurrence_id",
            name="uq_prospective_assignment_global_occurrence",
        ),
        UniqueConstraint(
            "assignment_identity_checksum",
            name="uq_prospective_assignment_identity",
        ),
        UniqueConstraint(
            "id",
            "evaluation_plan_id",
            "plan_identity_checksum",
            "game_id",
            "occurrence_id",
            name="uq_prospective_assignment_row_identity",
        ),
        CheckConstraint(
            "length(plan_identity_checksum) = 64 "
            "AND length(assignment_identity_checksum) = 64",
            name="ck_prospective_assignment_checksums",
        ),
        ForeignKeyConstraint(
            ["evaluation_plan_id", "plan_identity_checksum"],
            [
                "prospective_evaluation_plans.id",
                "prospective_evaluation_plans.plan_identity_checksum",
            ],
            name="fk_prospective_assignment_plan_identity",
        ),
        Index("ix_prospective_assignment_plan", "evaluation_plan_id", "assigned_at"),
    )


class ProspectiveEvaluationClaimBinding(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Normalized immutable forecast/target binding for one claimed game."""

    __tablename__ = "prospective_evaluation_claim_bindings"
    evaluation_claim_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    evaluation_plan_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    plan_identity_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    checkpoint: Mapped[str] = mapped_column(String(60), nullable=False)
    game_assignment_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    binding_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    game_id: Mapped[str] = mapped_column(String(120), nullable=False)
    occurrence_id: Mapped[str] = mapped_column(String(160), nullable=False)
    forecast_id: Mapped[str] = mapped_column(String(160), nullable=False)
    forecast_identity_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    forecast_evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    eligibility_policy_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    eligibility_proof_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    target_revision_id: Mapped[str] = mapped_column(String(160), nullable=False)
    target_revision_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    target_revision_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    target_available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    target_status: Mapped[str] = mapped_column(String(24), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(100), nullable=False)
    binding_identity_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "evaluation_claim_id",
            "game_assignment_id",
            name="uq_prospective_binding_claim_game",
        ),
        UniqueConstraint(
            "evaluation_claim_id",
            "binding_ordinal",
            name="uq_prospective_binding_claim_ordinal",
        ),
        UniqueConstraint(
            "evaluation_claim_id",
            "forecast_id",
            name="uq_prospective_binding_claim_forecast",
        ),
        UniqueConstraint(
            "evaluation_claim_id",
            "binding_identity_checksum",
            name="uq_prospective_binding_claim_identity",
        ),
        CheckConstraint(
            "checkpoint IN ('INTERIM_300_COMPLETED_ELIGIBLE_GAMES', "
            "'FINAL_500_COMPLETED_ELIGIBLE_GAMES')",
            name="ck_prospective_binding_checkpoint",
        ),
        CheckConstraint(
            "binding_ordinal >= 1 AND target_revision_sequence >= 0",
            name="ck_prospective_binding_sequences",
        ),
        CheckConstraint(
            "target_status = 'OFFICIAL_FINAL' "
            "AND schema_version = 'mlb_gen2_prospective_evaluation_claim_binding_v1'",
            name="ck_prospective_binding_target_schema",
        ),
        CheckConstraint(
            "length(plan_identity_checksum) = 64 "
            "AND length(forecast_identity_checksum) = 64 "
            "AND length(eligibility_policy_checksum) = 64 "
            "AND length(eligibility_proof_checksum) = 64 "
            "AND length(target_revision_checksum) = 64 "
            "AND length(binding_identity_checksum) = 64",
            name="ck_prospective_binding_checksums",
        ),
        ForeignKeyConstraint(
            [
                "evaluation_claim_id",
                "evaluation_plan_id",
                "plan_identity_checksum",
                "checkpoint",
            ],
            [
                "prospective_evaluation_claims.id",
                "prospective_evaluation_claims.evaluation_plan_id",
                "prospective_evaluation_claims.plan_identity_checksum",
                "prospective_evaluation_claims.checkpoint",
            ],
            name="fk_prospective_binding_claim_identity",
        ),
        ForeignKeyConstraint(
            [
                "game_assignment_id",
                "evaluation_plan_id",
                "plan_identity_checksum",
                "game_id",
                "occurrence_id",
            ],
            [
                "prospective_evaluation_game_assignments.id",
                "prospective_evaluation_game_assignments.evaluation_plan_id",
                "prospective_evaluation_game_assignments.plan_identity_checksum",
                "prospective_evaluation_game_assignments.game_id",
                "prospective_evaluation_game_assignments.occurrence_id",
            ],
            name="fk_prospective_binding_assignment_identity",
        ),
        Index("ix_prospective_binding_claim", "evaluation_claim_id", "binding_ordinal"),
        Index("ix_prospective_binding_game", "game_id", "occurrence_id"),
    )


class ProspectiveEvaluationLabelAccess(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only reservation consumed before a claim's labels are loaded."""

    __tablename__ = "prospective_evaluation_label_accesses"
    evaluation_claim_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    plan_identity_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    global_claim_identity_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    claim_identity_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    consumed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    code_revision: Mapped[str] = mapped_column(String(120), nullable=False)
    code_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_bundle_version: Mapped[str] = mapped_column(String(120), nullable=False)
    schema_bundle_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(60), nullable=False)
    access_identity_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "evaluation_claim_id", name="uq_prospective_label_access_claim"
        ),
        UniqueConstraint(
            "global_claim_identity_checksum",
            name="uq_prospective_label_access_global_claim",
        ),
        UniqueConstraint(
            "claim_identity_checksum", name="uq_prospective_label_access_claim_identity"
        ),
        UniqueConstraint(
            "access_identity_checksum", name="uq_prospective_label_access_identity"
        ),
        CheckConstraint(
            "schema_version = 'mlb_gen2_prospective_label_access_v1' "
            "AND status = 'PROSPECTIVE_LABEL_ACCESS_CONSUMED'",
            name="ck_prospective_label_access_version_status",
        ),
        CheckConstraint(
            "length(plan_identity_checksum) = 64 "
            "AND length(global_claim_identity_checksum) = 64 "
            "AND length(claim_identity_checksum) = 64 "
            "AND length(code_checksum) = 64 "
            "AND length(schema_bundle_checksum) = 64 "
            "AND length(access_identity_checksum) = 64",
            name="ck_prospective_label_access_checksums",
        ),
        ForeignKeyConstraint(
            [
                "evaluation_claim_id",
                "plan_identity_checksum",
                "global_claim_identity_checksum",
                "claim_identity_checksum",
            ],
            [
                "prospective_evaluation_claims.id",
                "prospective_evaluation_claims.plan_identity_checksum",
                "prospective_evaluation_claims.global_claim_identity_checksum",
                "prospective_evaluation_claims.claim_identity_checksum",
            ],
            name="fk_prospective_label_access_claim_identity",
        ),
        Index("ix_prospective_label_access_time", "consumed_at"),
    )


# Compatibility aliases keep policy terminology explicit without mapping duplicate tables.
HistoricalSourceOwnerApproval = SourceOwnerApproval
HistoricalProviderAccessAuthorization = ProviderAccessAuthorization
ExistingEvidenceUseDecision = ExistingEvidenceDerivedUseDecision
DerivedEvidenceUseDecision = ExistingEvidenceDerivedUseDecision


class Forecast(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "forecasts"
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), index=True, nullable=False)
    model_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("model_versions.id"), nullable=False)
    feature_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("feature_versions.id"), nullable=False)
    feature_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("game_feature_snapshots.id"), index=True)
    forecast_track: Mapped[ForecastTrack] = mapped_column(
        enum_type(ForecastTrack),
        nullable=False,
        default=ForecastTrack.CURRENT_DEVELOPMENT,
        server_default=ForecastTrack.CURRENT_DEVELOPMENT.value,
    )
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
    market_snapshot_reference: Mapped[str | None] = mapped_column(Text)
    source_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="LIVE")
    simulation_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint(
            "game_id",
            "forecast_track",
            "version",
            name="uq_forecast_game_track_version",
        ),
        Index(
            "ix_forecast_game_track_status_version",
            "game_id",
            "forecast_track",
            "status",
            "version",
        ),
    )


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
    #: The producer forecast behind a model-era position. Mirrors the decision.
    shadow_forecast_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("shadow_forecasts.id"), index=True
    )
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
    # The prospective experiment reuses this canonical virtual-position ledger.
    # These fields are null for legacy positions.
    shadow_experiment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("shadow_experiments.id"), index=True
    )
    shadow_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("shadow_experiment_decisions.id"), unique=True, index=True
    )
    bankroll_before_position: Mapped[float | None] = mapped_column(Numeric(12, 2))
    recommendation_policy_version: Mapped[str | None] = mapped_column(String(80))
    staking_policy_version: Mapped[str | None] = mapped_column(String(80))
    __table_args__ = (
        UniqueConstraint(
            "shadow_experiment_id", "market_outcome_id",
            name="uq_official_position_experiment_outcome",
        ),
    )


class PositionSettlement(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "position_results"
    position_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("official_asp_positions.id"), index=True, nullable=False)
    result: Mapped[PositionResult] = mapped_column(enum_type(PositionResult), nullable=False)
    settled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    profit_loss: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    game_home_score: Mapped[int | None] = mapped_column(Integer)
    game_away_score: Mapped[int | None] = mapped_column(Integer)
    settlement_note: Mapped[str | None] = mapped_column(Text)
    bankroll_before: Mapped[float | None] = mapped_column(Numeric(12, 2))
    bankroll_after: Mapped[float | None] = mapped_column(Numeric(12, 2))
    closing_odds_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("odds_snapshots.id"))
    closing_american_odds: Mapped[int | None] = mapped_column(Integer)
    closing_decimal_odds: Mapped[float | None] = mapped_column(Float)
    implied_probability_movement: Mapped[float | None] = mapped_column(Float)
    closing_line_value: Mapped[float | None] = mapped_column(Float)
    beat_close: Mapped[bool | None] = mapped_column(Boolean)
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


# ---------------------------------------------------------------------------
# Model Studio forecast-exchange shadow board (research-shadow, never executable)
# ---------------------------------------------------------------------------


class ShadowForecastBundle(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Immutable identity of one imported Model Studio daily forecast bundle."""

    __tablename__ = "shadow_forecast_bundles"
    bundle_sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    contract_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default="AISP_DAILY_FORECAST_BUNDLE_V1",
        server_default="AISP_DAILY_FORECAST_BUNDLE_V1",
    )
    bundle_id: Mapped[str] = mapped_column(String(160), nullable=False)
    logical_uri: Mapped[str] = mapped_column(Text, nullable=False)
    source_fixture_id: Mapped[str] = mapped_column(String(200), nullable=False)
    slate_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    league: Mapped[str] = mapped_column(String(16), nullable=False)
    sport: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(48), nullable=False)
    research_shadow_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    forecast_count: Mapped[int] = mapped_column(Integer, nullable=False)
    quote_count: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # Immutable consumer-side evidence for imported bundles. Daily V18 slates
    # use this to retain publisher-declared skips without inventing forecasts.
    evidence: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ShadowForecast(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One imported research-shadow forecast; the engine id is its forecast track."""

    __tablename__ = "shadow_forecasts"
    bundle_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shadow_forecast_bundles.id"), index=True, nullable=False)
    canonical_game_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("games.id"), index=True)
    forecast_id: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)
    forecast_schema_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default="AISP_GAME_FORECAST_EXPORT_V1",
        server_default="AISP_GAME_FORECAST_EXPORT_V1",
    )
    snapshot_type: Mapped[str] = mapped_column(String(24), nullable=False, default="HISTORICAL", server_default="HISTORICAL")
    snapshot_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    engine_id: Mapped[str] = mapped_column(String(32), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    model_manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    feature_vector_sha256: Mapped[str | None] = mapped_column(String(64))
    simulator_id: Mapped[str] = mapped_column(String(64), nullable=False)
    simulator_version: Mapped[str] = mapped_column(String(64), nullable=False)
    simulator_seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_id: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(120), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    season_type: Mapped[str | None] = mapped_column(String(16))
    week: Mapped[int | None] = mapped_column(Integer)
    home_team_id: Mapped[str] = mapped_column(String(120), nullable=False)
    away_team_id: Mapped[str] = mapped_column(String(120), nullable=False)
    is_ready: Mapped[bool] = mapped_column(Boolean, nullable=False)
    readiness_score: Mapped[float] = mapped_column(Float, nullable=False)
    readiness_threshold: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(48), nullable=False)
    scheduled_start_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    schedule_as_of_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_cutoff_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    forecast_cutoff_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_as_of_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    full_home_win_probability: Mapped[float] = mapped_column(Float, nullable=False)
    full_away_win_probability: Mapped[float] = mapped_column(Float, nullable=False)
    full_expected_home_score: Mapped[float] = mapped_column(Float, nullable=False)
    full_expected_away_score: Mapped[float] = mapped_column(Float, nullable=False)
    f5_home_win_probability: Mapped[float | None] = mapped_column(Float)
    f5_away_win_probability: Mapped[float | None] = mapped_column(Float)
    f5_expected_home_score: Mapped[float | None] = mapped_column(Float)
    f5_expected_away_score: Mapped[float | None] = mapped_column(Float)
    distributions: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    uncertainty: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    readiness_detail: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    identity_evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
    lineage_detail: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
    __table_args__ = (
        Index("ix_shadow_forecasts_event_engine", "event_id", "engine_id"),
    )


class ShadowBoardDecision(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Immutable evaluated shadow decision. A missing quote is a real NO_BET."""

    __tablename__ = "shadow_board_decisions"
    shadow_forecast_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shadow_forecasts.id"), index=True, nullable=False)
    bundle_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shadow_forecast_bundles.id"), index=True, nullable=False)
    slate_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    engine_id: Mapped[str] = mapped_column(String(32), nullable=False)
    event_id: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    market: Mapped[str] = mapped_column(String(40), nullable=False)
    selection: Mapped[str] = mapped_column(String(40), nullable=False)
    # Null only for a line-dependent market (total/team total) with no governed
    # line: no line is ever fabricated, so no probability is stated.
    model_probability: Mapped[float | None] = mapped_column(Float)
    push_probability: Mapped[float | None] = mapped_column(Float)
    has_quote: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    odds_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("odds_snapshots.id"), index=True)
    opposite_odds_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("odds_snapshots.id"), index=True)
    source_quote_event_id: Mapped[str | None] = mapped_column(String(255))
    quote_available_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    book: Mapped[str | None] = mapped_column(String(80))
    line: Mapped[float | None] = mapped_column(Float)
    side_american: Mapped[int | None] = mapped_column(Integer)
    opposite_american: Mapped[int | None] = mapped_column(Integer)
    best_american: Mapped[int | None] = mapped_column(Integer)
    best_decimal: Mapped[float | None] = mapped_column(Float)
    quote_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    break_even_probability: Mapped[float | None] = mapped_column(Float)
    no_vig_probability: Mapped[float | None] = mapped_column(Float)
    raw_edge: Mapped[float | None] = mapped_column(Float)
    no_vig_edge: Mapped[float | None] = mapped_column(Float)
    expected_value: Mapped[float | None] = mapped_column(Float)
    uncertainty_adjusted_edge: Mapped[float | None] = mapped_column(Float)
    recommendation: Mapped[str] = mapped_column(String(16), nullable=False)
    virtual_stake: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    quote_signature: Mapped[str] = mapped_column(String(80), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        UniqueConstraint(
            "shadow_forecast_id", "market", "selection", "quote_signature",
            name="uq_shadow_decision_idempotent",
        ),
    )


class ShadowDecisionSettlement(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only settlement of a shadow decision. Virtual profit only."""

    __tablename__ = "shadow_decision_settlements"
    decision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shadow_board_decisions.id"), unique=True, index=True, nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    closing_american: Mapped[int | None] = mapped_column(Integer)
    closing_decimal: Mapped[float | None] = mapped_column(Float)
    closing_line_value: Mapped[float | None] = mapped_column(Float)
    virtual_profit_loss: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    settlement_note: Mapped[str | None] = mapped_column(String(200))
    performance_detail: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
    settled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


# ---------------------------------------------------------------------------
# Prospective live shadow pilot: immutable availability events + rescores
# ---------------------------------------------------------------------------


class AvailabilityEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Immutable availability-change evidence (AISP_AVAILABILITY_EVENT_V1)."""

    __tablename__ = "availability_events"
    schema_version: Mapped[str] = mapped_column(String(48), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    source_event_key: Mapped[str] = mapped_column(String(200), nullable=False)
    sport: Mapped[str] = mapped_column(String(24), nullable=False, default="BASEBALL")
    league: Mapped[str] = mapped_column(String(16), nullable=False, default="MLB")
    game_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("games.id"), index=True)
    source_game_id: Mapped[str | None] = mapped_column(String(120))
    team_source_id: Mapped[str | None] = mapped_column(String(120))
    player_source_id: Mapped[str | None] = mapped_column(String(120))
    player_name: Mapped[str | None] = mapped_column(String(160))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    __table_args__ = (
        UniqueConstraint("source", "source_event_key", name="uq_availability_event_source_key"),
    )


class ShadowRescore(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Immutable before/after evidence of one availability-triggered rescore.

    Virtual and non-executable. Quotes are snapshots of persisted sportsbook
    observations; no market price is fabricated.
    """

    __tablename__ = "shadow_rescores"
    availability_event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("availability_events.id"), index=True, nullable=False)
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), index=True, nullable=False)
    slate_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    engine: Mapped[str] = mapped_column(String(32), nullable=False, default="AISP_BASELINE")
    market: Mapped[str] = mapped_column(String(40), nullable=False, default="FULL_GAME_MONEYLINE")
    before_forecast_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("forecasts.id"))
    after_forecast_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("forecasts.id"), nullable=False)
    before_home_win_probability: Mapped[float | None] = mapped_column(Float)
    after_home_win_probability: Mapped[float] = mapped_column(Float, nullable=False)
    before_readiness: Mapped[float | None] = mapped_column(Float)
    after_readiness: Mapped[float] = mapped_column(Float, nullable=False)
    before_uncertainty: Mapped[float | None] = mapped_column(Float)
    after_uncertainty: Mapped[float | None] = mapped_column(Float)
    pre_quote: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    post_quote: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    book_moved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    decisions: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    recommendation: Mapped[str] = mapped_column(String(16), nullable=False)
    virtual_stake: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    source_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    aisp_received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rescore_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rescore_completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    pre_event_quote_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    post_event_quote_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quote_timeline: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    __table_args__ = (
        UniqueConstraint("availability_event_id", "game_id", name="uq_shadow_rescore_event_game"),
    )


class ShadowQuoteCapture(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only later-slot quote capture for a rescore's timeline.

    Keeps the ShadowRescore row fully immutable while the 15s/60s/5m/closing
    quotes are appended as real, separately-timestamped observations. A null
    quote records that no governed price existed at that slot (never fabricated).
    """

    __tablename__ = "shadow_quote_captures"
    rescore_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shadow_rescores.id"), index=True, nullable=False)
    slot: Mapped[str] = mapped_column(String(20), nullable=False)
    quote: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        UniqueConstraint("rescore_id", "slot", name="uq_shadow_quote_capture_slot"),
    )


class LiveShadowPollState(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Mutable per-game polling bookkeeping for the armed live shadow operator.

    Operational state only (cadence + last-observed availability values used for
    change detection). It is deliberately not evidence and is therefore mutable;
    the immutable evidence lives in availability_events / shadow_rescores.
    """

    __tablename__ = "live_shadow_poll_state"
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), unique=True, index=True, nullable=False)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    poll_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_probable_pitcher: Mapped[str | None] = mapped_column(String(120))
    last_home_lineup_status: Mapped[str | None] = mapped_column(String(24))
    last_away_lineup_status: Mapped[str | None] = mapped_column(String(24))
    last_source_payload_id: Mapped[str | None] = mapped_column(String(120))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class ShadowExperiment(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Immutable start authority for the forward $1,000 virtual-bankroll experiment.

    Virtual research only; never executable. A policy change creates a NEW version
    rather than rewriting an existing experiment, so the original start date,
    bankroll and policy are preserved.
    """

    __tablename__ = "shadow_experiments"
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    starting_bankroll: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    sport: Mapped[str] = mapped_column(String(24), nullable=False)
    recommendation_policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    staking_policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    decision_policy: Mapped[str] = mapped_column(String(80), nullable=False)
    controlling_engine: Mapped[str] = mapped_column(String(40), nullable=False)
    supported_markets: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    kelly_fraction: Mapped[float] = mapped_column(Float, nullable=False)
    unit_percentage: Mapped[float] = mapped_column(Float, nullable=False)
    max_position_percentage: Mapped[float] = mapped_column(Float, nullable=False)
    max_same_game_percentage: Mapped[float] = mapped_column(Float, nullable=False)
    max_daily_percentage: Mapped[float] = mapped_column(Float, nullable=False)
    code_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    code_identity: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Frozen challenger-policy calibration (evidence penalties, sample sizes,
    # checksum, evidence multiplier, caps). NULL for the V1 strict control.
    policy_config: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    #: Immutable model-era identity when a producer supplement created this
    #: experiment at an exact future boundary (e.g. the V18 conditional
    #: champion). NULL for the original Gen1 experiments.
    model_era_id: Mapped[str | None] = mapped_column(String(80), unique=True)
    #: The experiment this one succeeds. Its open positions and settlements
    #: stay where they are; only reconciled capital carries forward.
    predecessor_experiment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("shadow_experiments.id"), index=True
    )
    #: The atomic capital snapshot taken AT the boundary: realized bankroll,
    #: reserved open stakes, what carried forward, and the predecessor's last
    #: decision. Written once; a fabricated opening balance is never accepted.
    boundary_capital: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class ShadowExperimentDecision(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One frozen game-day decision snapshot bound to the exact recommendation price.

    Distinct rows are preserved per phase (MORNING / LINEUP_CONFIRMED /
    FINAL_PREGAME). This table is evidence only: an exact cutoff-safe
    QUALIFIED_BET may be linked from the canonical OfficialAspPosition ledger.
    A row without a governed quote remains an honest NO_BET observation.
    """

    __tablename__ = "shadow_experiment_decisions"
    experiment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shadow_experiments.id"), index=True, nullable=False)
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), index=True, nullable=False)
    slate_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    phase: Mapped[ExperimentDecisionPhase] = mapped_column(enum_type(ExperimentDecisionPhase), nullable=False)
    sport: Mapped[str] = mapped_column(String(24), nullable=False, default="BASEBALL_MLB")
    market: Mapped[str] = mapped_column(String(40), nullable=False)
    period: Mapped[str] = mapped_column(String(40), nullable=False, default="Full game")
    selection: Mapped[str] = mapped_column(String(80), nullable=False)
    line: Mapped[float | None] = mapped_column(Float)
    sportsbook: Mapped[str | None] = mapped_column(String(80))
    american_odds: Mapped[int | None] = mapped_column(Integer)
    decimal_odds: Mapped[float | None] = mapped_column(Float)
    market_outcome_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("market_outcomes.id"))
    odds_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("odds_snapshots.id"))
    quote_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    forecast_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("forecasts.id"))
    #: Set instead of ``forecast_id`` when the controlling engine is a producer
    #: engine whose saved forecasts live in ``shadow_forecasts``. Exactly one of
    #: the two is populated, so a decision always names the forecast it used.
    shadow_forecast_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("shadow_forecasts.id"), index=True
    )
    #: College Football forecasts live in their own research table; a CFB paper
    #: decision names the exact imported row here instead.
    cfb_research_forecast_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cfb_research_forecasts.id"), index=True
    )
    #: The NBA Gen1 production package an NBA paper decision read. Same rule:
    #: a decision names the exact imported row it used.
    nba_gen1_forecast_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("nba_gen1_forecasts.id"), index=True
    )
    forecast_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    forecast_cutoff: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    forecast_version: Mapped[str | None] = mapped_column(String(40))
    recommended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    model_probability: Mapped[float | None] = mapped_column(Float)
    no_vig_probability: Mapped[float | None] = mapped_column(Float)
    edge: Mapped[float | None] = mapped_column(Float)
    expected_value: Mapped[float | None] = mapped_column(Float)
    readiness: Mapped[float | None] = mapped_column(Float)
    uncertainty: Mapped[float | None] = mapped_column(Float)
    recommendation: Mapped[str] = mapped_column(String(24), nullable=False)
    recommendation_reason: Mapped[str] = mapped_column(Text, nullable=False)
    qualification_reasons: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    evidence_fingerprint: Mapped[str | None] = mapped_column(String(64), unique=True)
    source_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="LIVE")
    # Exact challenger evidence breakdown (starter/lineup states, tier, applied
    # penalties, adjusted probability/edge, raw Kelly, evidence multiplier,
    # final stake, policy version). NULL for V1 strict-control decisions.
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    # -- what was decided, and what was then executed (D2) -------------------
    #
    # `recommendation` above is the POST-staking disposition: what AISP actually
    # did. These two pairs keep the two questions apart, because conflating them
    # is what let three exposure-capped games persist as QUALIFIED_BET on
    # 2026-09-20 while the job receipt recorded DECLINED / daily_cap.
    qualification_outcome: Mapped[str | None] = mapped_column(String(40))
    qualification_reason: Mapped[str | None] = mapped_column(String(80))
    execution_outcome: Mapped[str | None] = mapped_column(String(40))
    execution_reason: Mapped[str | None] = mapped_column(String(80))
    #: Binding limits, bound_by, stake, and the analytical recommended-Kelly and
    #: constrained-bankroll fields. Durable, not telemetry-only.
    staking_detail: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    # -- which forecast actually controlled this decision (D3) ---------------
    #
    # `forecast_imported_at` is the IMPORT COMPLETION, deliberately not the
    # forecast row's created_at: in Postgres that is the transaction-start
    # stamp, and on 2026-09-20 eight FINAL rows carried created_at 12:41:07
    # while their transaction did not commit until 12:43:32.
    forecast_snapshot_phase: Mapped[str | None] = mapped_column(String(40))
    forecast_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    forecast_imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    eligibility_supplement_id: Mapped[str | None] = mapped_column(String(200))
    #: The content-addressed identity of the CONTROLLING forecast. The uuid
    #: `forecast_id` above is a foreign key to `forecasts`; an NFL forecast
    #: lives in `shadow_forecasts` (see `shadow_forecast_id`), so the identity
    #: gets its own column rather than being forced into the wrong key.
    controlling_forecast_identity: Mapped[str | None] = mapped_column(String(200))

    # -- the authority behind the decision (D6) ------------------------------
    eligibility_binding: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    __table_args__ = (
        Index("ix_experiment_decision_experiment_slate", "experiment_id", "slate_date"),
    )


class ModelChampionAuthority(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One imported producer supplement granting PROSPECTIVE forecasting authority.

    WHAT THIS IS AND IS NOT
    -----------------------
    It records that a named, frozen, externally fitted model is the primary
    prospective forecasting champion for one sport from one exact future
    boundary. It grants nothing else. It does not authorize execution inside
    AISP, does not authorize real money, does not repair the model's historical
    validation, and does not relabel a single existing forecast: rows already in
    ``forecasts`` and ``shadow_forecasts`` keep the provenance they were
    imported with.

    WHY AUTHORITY CANNOT ARRIVE IN A FORECAST
    -----------------------------------------
    An ordinary forecast payload is data from a producer. If a forecast could
    carry the field that makes its own engine the champion, then anything able
    to write a forecast could promote itself. Authority therefore arrives only
    through this separately checksummed supplement, addressed by the digest of
    the exact bytes that granted it.
    """

    __tablename__ = "model_champion_authorities"
    #: sha256 of the exact handoff bytes. The import is idempotent on this.
    handoff_sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    sport: Mapped[str] = mapped_column(String(24), index=True, nullable=False)
    #: The engine whose saved forecasts this authority covers.
    engine_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    #: Frozen fitted-model identity, exactly as the producer named it.
    model_name: Mapped[str] = mapped_column(String(80), nullable=False)
    model_version: Mapped[str] = mapped_column(String(40), nullable=False)
    model_artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    champion_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    frozen_bindings_sha256: Mapped[str | None] = mapped_column(String(64))
    parent_bundle_id: Mapped[str | None] = mapped_column(String(120))
    training_run_id: Mapped[str | None] = mapped_column(String(120))
    #: The model-era identity this authority creates, and when it takes effect.
    model_era_id: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Disclosed conditions. Activation does not clear them.
    historical_validation_under_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    historical_integrity_note: Mapped[str | None] = mapped_column(Text)
    #: Hard refusals, persisted so a later reader cannot assume otherwise.
    execution_allowed_in_aisp: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    real_money_execution_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    gen1_fallback_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    position_backfill_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    minimum_adjusted_edge: Mapped[float] = mapped_column(Float, nullable=False)
    recommendation_policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    staking_policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    #: ARMED until the boundary arrives, ACTIVE afterwards. Never backdated.
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="ARMED")
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: The experiment this authority created, once it exists.
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("shadow_experiments.id"), index=True
    )
    #: Verification evidence: which files, which digests, which byte counts.
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class ResearchSupplement(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One immutable, checksum-verified research supplement (AISP_RESEARCH_SUPPLEMENT_V1).

    A supplement is evidence that arrives SEPARATELY from a forecast bundle:
    a joint score simulation, a drive-level simulation, a precomputed scenario
    comparison, or a machine-readable model benchmark study. It is bound to its
    event/engine/forecast by declared identifiers, never by inference, and
    carries no execution, promotion or wagering authority. Rows are append-only.
    """

    __tablename__ = "research_supplements"
    supplement_sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    supplement_id: Mapped[str] = mapped_column(String(200), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(64), nullable=False)
    #: JOINT_SCORE_SIMULATION | DRIVE_SIMULATION | SCENARIO_COMPARISON | MODEL_BENCHMARK_STUDY
    kind: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    sport: Mapped[str] = mapped_column(String(24), index=True, nullable=False)
    #: Null only for a study that is not bound to one event.
    event_id: Mapped[str | None] = mapped_column(String(120), index=True)
    engine_id: Mapped[str | None] = mapped_column(String(64), index=True)
    forecast_id: Mapped[str | None] = mapped_column(String(120), index=True)
    shadow_forecast_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("shadow_forecasts.id"), index=True)
    artifact_version: Mapped[str] = mapped_column(String(64), nullable=False)
    #: EXACT_DISTRIBUTION | MONTE_CARLO | DETERMINISTIC | NONE
    simulation_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    simulation_count: Mapped[int | None] = mapped_column(Integer)
    simulation_seed: Mapped[int | None] = mapped_column(BigInteger)
    source_cutoff_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    status: Mapped[str] = mapped_column(String(48), nullable=False, default="RESEARCH_SHADOW", server_default="RESEARCH_SHADOW")
    research_shadow_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Summaries and small paths the pages render. Verified before it is read.
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    #: Importer-side verification evidence: which files, which digests, which bindings.
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
    __table_args__ = (
        Index("ix_research_supplements_event_kind", "event_id", "kind"),
    )


class ResearchForecastEvaluation(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One prospective forecast, scored against one final result. Append-only.

    WHY THIS IS NOT A POSITION SETTLEMENT
    -------------------------------------
    A settlement answers "what happened to money we staked". This answers
    "how good was the forecast", for every game -- including the overwhelming
    majority where nothing was staked and no decision was ever taken. Sharing
    the settlement table would make a research metric look like a financial
    record and would tie evaluation coverage to wagering activity, which is
    exactly backwards: the forecasts we never bet on are the ones the sample
    most needs.

    THE IDENTITY, AND WHY EACH PART OF IT IS THERE
    ----------------------------------------------
    ``(event_id, engine_id, engine_version, forecast_identity, result_identity)``

      event + engine + version   one score per model per game, so ten stored
                                 snapshots of one forecast are one row
      forecast_identity          the exact snapshot that was frozen, so a
                                 re-freeze against a different eligible
                                 forecast is a NEW row rather than a silent
                                 overwrite of the old verdict
      result_identity            a corrected final produces a new evaluation
                                 beside the old one; the first score is still
                                 what we said at the time

    Re-running the job writes nothing when all five match.
    """

    __tablename__ = "research_forecast_evaluations"

    sport: Mapped[str] = mapped_column(String(24), index=True, nullable=False)
    league_code: Mapped[str] = mapped_column(String(24), index=True, nullable=False)
    event_id: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    canonical_game_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("games.id"), index=True)
    engine_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    engine_version: Mapped[str] = mapped_column(String(120), nullable=False)
    model_label: Mapped[str | None] = mapped_column(String(160))

    #: The frozen forecast. `forecast_identity` is the producer's own id.
    forecast_identity: Mapped[str] = mapped_column(String(200), nullable=False)
    shadow_forecast_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("shadow_forecasts.id"), index=True
    )
    snapshot_type: Mapped[str | None] = mapped_column(String(32))
    snapshot_version: Mapped[int | None] = mapped_column(Integer)
    forecast_published_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    forecast_evidence_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    forecast_imported_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    kickoff_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Seconds between publication and kickoff. Negative is impossible here and
    #: is what the eligibility filter exists to prevent.
    published_before_kickoff_seconds: Mapped[int | None] = mapped_column(Integer)

    #: The result this was scored against, and where it came from.
    result_identity: Mapped[str] = mapped_column(String(200), nullable=False)
    result_source: Mapped[str] = mapped_column(String(64), nullable=False)
    result_observed_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Provenance rank of ``result_source``; lower is stronger. See
    #: ``app.services.result_provenance``.
    result_tier: Mapped[int] = mapped_column(Integer, nullable=False, default=9, server_default="9")
    home_score: Mapped[int] = mapped_column(Integer, nullable=False)
    away_score: Mapped[int] = mapped_column(Integer, nullable=False)

    #: EXACTLY ONE row per (event, engine, version, forecast identity) is
    #: current, and only current rows are counted or displayed.
    #:
    #: Rows are never deleted or rewritten, because the verdict we published on
    #: Wednesday is a fact about Wednesday. But when the same game's result
    #: arrives again from a better-provenanced source, or arrives corrected,
    #: the older evaluation stops being the answer -- it becomes history. A
    #: product that counted both would report two games where one was played,
    #: which is the specific failure this column exists to make impossible.
    is_current: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true", index=True
    )
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("research_forecast_evaluations.id")
    )
    #: PROVENANCE_UPGRADE (same score, better source) or RESULT_CORRECTED
    #: (the score itself changed). The distinction matters: the first says our
    #: verdict was right and is now better evidenced, the second says the thing
    #: we scored against was wrong.
    supersede_reason: Mapped[str | None] = mapped_column(String(48))

    #: Outcome scoring.
    actual_outcome: Mapped[str] = mapped_column(String(8), nullable=False)
    predicted_outcome: Mapped[str] = mapped_column(String(8), nullable=False)
    winner_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    probability_of_actual: Mapped[float] = mapped_column(Float, nullable=False)
    log_loss: Mapped[float] = mapped_column(Float, nullable=False)
    brier_score: Mapped[float] = mapped_column(Float, nullable=False)

    #: Point scoring. Signed errors are predicted minus actual throughout.
    predicted_home_score: Mapped[float | None] = mapped_column(Float)
    predicted_away_score: Mapped[float | None] = mapped_column(Float)
    home_score_error: Mapped[float | None] = mapped_column(Float)
    away_score_error: Mapped[float | None] = mapped_column(Float)
    team_score_mae: Mapped[float | None] = mapped_column(Float)
    predicted_margin: Mapped[float | None] = mapped_column(Float)
    actual_margin: Mapped[int] = mapped_column(Integer, nullable=False)
    margin_error: Mapped[float | None] = mapped_column(Float)
    predicted_total: Mapped[float | None] = mapped_column(Float)
    actual_total: Mapped[int] = mapped_column(Integer, nullable=False)
    total_error: Mapped[float | None] = mapped_column(Float)

    #: Distribution scoring. Null with a stated reason when no authentic
    #: saved draws exist; never estimated from a summary.
    distribution_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    distribution_unavailable_reason: Mapped[str | None] = mapped_column(Text)
    distribution_draws: Mapped[int | None] = mapped_column(Integer)
    crps_home: Mapped[float | None] = mapped_column(Float)
    crps_away: Mapped[float | None] = mapped_column(Float)
    crps_margin: Mapped[float | None] = mapped_column(Float)
    crps_total: Mapped[float | None] = mapped_column(Float)
    energy_score: Mapped[float | None] = mapped_column(Float)
    interval_coverage: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    #: Market comparison. Null with a stated reason when no eligible
    #: pre-kickoff two-sided quote exists.
    market_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    market_unavailable_reason: Mapped[str | None] = mapped_column(Text)
    market_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    #: The full computation, kept so a number on the page can be traced.
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    scoring_version: Mapped[str] = mapped_column(String(48), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "event_id", "engine_id", "engine_version", "forecast_identity", "result_identity",
            name="uq_research_forecast_evaluation_identity",
        ),
        Index("ix_research_forecast_eval_league_engine", "league_code", "engine_id"),
        # The structural guarantee behind "n cannot be inflated by provenance".
        # Audit rows may pile up for one game; only one of them can be current,
        # and the database refuses the alternative rather than trusting the
        # reconciliation code to be correct forever.
        Index(
            "uq_research_forecast_eval_current",
            "event_id", "engine_id", "engine_version", "forecast_identity",
            unique=True, postgresql_where=text("is_current"), sqlite_where=text("is_current"),
        ),
        CheckConstraint(
            "published_before_kickoff_seconds IS NULL OR published_before_kickoff_seconds >= 0",
            name="ck_research_forecast_eval_prospective",
        ),
        CheckConstraint(
            "(is_current AND superseded_at IS NULL AND superseded_by_id IS NULL)"
            " OR (NOT is_current AND superseded_at IS NOT NULL)",
            name="ck_research_forecast_eval_supersession",
        ),
    )


class NflFinalObservation(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One retrieval of one NFL game's final, with its football-specific facts.

    WHY A SEPARATE TABLE FROM ``game_state_observations``
    ------------------------------------------------------
    ``GameStateObservation`` stays the canonical, sport-neutral result identity
    -- every evaluation still keys on its checksum, and this table points at
    the row that carries it. But football has facts baseball's shape cannot
    hold: overtime, week, game type. Bolting ``overtime`` onto the shared
    observation table would put football vocabulary in the one place this
    codebase has been careful to keep sport-neutral, and would leave a column
    that is NULL for every MLB row ever written.

    Append-only, one row per (game, source, result checksum). The checksum
    covers the RESULT FACTS ONLY -- never the retrieval time and never the
    feed's own last-updated stamp -- so refetching an unchanged game reuses
    this row instead of minting a new "correction" every hour.
    """

    __tablename__ = "nfl_final_observations"

    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), index=True, nullable=False)
    #: The nflverse game id, which is also AISP's own NFL event id.
    source_event_id: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    #: The sport-neutral observation this record elaborates.
    game_state_observation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("game_state_observations.id"), index=True
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="RESEARCH_SHADOW")
    result_tier: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    overtime: Mapped[bool | None] = mapped_column(Boolean)
    season: Mapped[int | None] = mapped_column(Integer)
    game_type: Mapped[str | None] = mapped_column(String(16))
    week: Mapped[int | None] = mapped_column(Integer)
    #: When the source says it last refreshed, and when we asked. Both are
    #: outside the checksum on purpose.
    source_last_updated: Mapped[str | None] = mapped_column(String(64))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Checksum of the result facts. This IS the result identity suffix.
    payload_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Checksum of the whole downloaded payload, for archive cross-reference.
    feed_checksum: Mapped[str | None] = mapped_column(String(64))
    raw_file_reference: Mapped[str | None] = mapped_column(String(500))
    raw_row: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint(
            "game_id", "source", "payload_checksum", name="uq_nfl_final_observation_identity",
        ),
        Index("ix_nfl_final_obs_source_event", "source", "source_event_id"),
    )


class ShadowThresholdSelection(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One hypothetical selection a non-official threshold would have made.

    Append-only research evidence. A row here records that a decision which
    already existed, priced by a quote already persisted, would have cleared a
    DIFFERENT minimum edge than the official one. It is not a decision, it
    cannot become a position, and nothing in the funded experiment can see it.

    Identity is (ladder, rung, decision): one rung records a given decision
    once, so re-running the ladder over the same slate reuses rather than
    duplicating.
    """

    __tablename__ = "shadow_threshold_selections"

    ladder_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Percentage points of adjusted edge, e.g. 1.25.
    rung_percentage: Mapped[float] = mapped_column(Float, index=True, nullable=False)
    policy: Mapped[str] = mapped_column(String(80), nullable=False)
    decision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shadow_experiment_decisions.id"), index=True, nullable=False
    )
    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), index=True, nullable=False)
    league_code: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    slate_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    selection_label: Mapped[str | None] = mapped_column(String(120))
    adjusted_edge_percentage: Mapped[float] = mapped_column(Float, nullable=False)
    scheduled_start_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        # One rung records a GAME once per slate. The decision table holds a
        # row per re-evaluation, so keying on the decision would record the
        # same game dozens of times and make the rungs incomparable.
        UniqueConstraint("ladder_id", "rung_percentage", "game_id", "slate_date",
                         name="uq_shadow_threshold_selection"),
        Index("ix_shadow_threshold_rung_slate", "rung_percentage", "slate_date"),
    )


class RffReadingSnapshot(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One prospectively captured RFF Core reading, or the receipt of a miss.

    APPEND-ONLY. There is no update and no delete path: an accepted row is the
    authoritative record of what the index said at a registered offset, and a
    later recomputation is a diagnostic comparison, never a correction.

    A reading is admissible only when it is captured within
    ``MAX_CAPTURE_LAG_SECONDS`` of its target instant. Past that, the row is a
    ``MISSED_CAPTURE_WINDOW`` receipt carrying no index values, and it is never
    backfilled -- a reading assembled after the fact is not the reading that
    existed, which is the whole point of capturing prospectively.

    Research and display only. Nothing here is read by a forecast, a decision,
    a stake, a position or a settlement.
    """

    __tablename__ = "rff_reading_snapshots"

    game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("games.id"), index=True, nullable=False)
    league_code: Mapped[str] = mapped_column(String(16), index=True, nullable=False)

    #: One of the four registered offsets: T-6h, T-3h, T-90m, T-30m.
    target_offset: Mapped[str] = mapped_column(String(8), index=True, nullable=False)
    target_instant_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    captured_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: The instant handed to the calculation. Equals captured_at_utc: the
    #: reading is what the index said then, not a reconstruction of the target.
    as_of_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    capture_lag_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    #: ACCEPTED or MISSED_CAPTURE_WINDOW.
    status: Mapped[str] = mapped_column(String(24), index=True, nullable=False)
    missed_reason: Mapped[str | None] = mapped_column(String(200))

    # -- calculation identity --------------------------------------------
    calculation_version: Mapped[str | None] = mapped_column(String(40))
    calculation_git_sha: Mapped[str | None] = mapped_column(String(40))
    #: SHA-256 over the physical source files that determine the number.
    calculation_source_sha256: Mapped[str | None] = mapped_column(String(64))
    weights_sha256: Mapped[str | None] = mapped_column(String(64))
    policy_sha256: Mapped[str | None] = mapped_column(String(64))
    calculation_identity: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    # -- the reading ------------------------------------------------------
    components: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    known_contribution: Mapped[float | None] = mapped_column(Float)
    evidence_coverage: Mapped[float | None] = mapped_column(Float)
    lower_bound: Mapped[float | None] = mapped_column(Float)
    upper_bound: Mapped[float | None] = mapped_column(Float)
    interval_width: Mapped[float | None] = mapped_column(Float)
    publication_tier: Mapped[str | None] = mapped_column(String(24), index=True)
    band_key: Mapped[str | None] = mapped_column(String(32))
    band_label: Mapped[str | None] = mapped_column(String(40))
    emphasize_point: Mapped[bool | None] = mapped_column(Boolean)
    rankable: Mapped[bool | None] = mapped_column(Boolean)
    available_components: Mapped[int | None] = mapped_column(Integer)

    # -- provenance -------------------------------------------------------
    evidence_health: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    source_hashes: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    source_row_identities: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    __table_args__ = (
        # One accepted reading per game per offset, enforced by the database
        # and not only by the service: a second process, a retry or a replayed
        # job can never produce a competing reading for the same instant.
        UniqueConstraint("game_id", "target_offset", name="uq_rff_snapshot_game_offset"),
        Index("ix_rff_snapshot_offset_status", "target_offset", "status"),
    )

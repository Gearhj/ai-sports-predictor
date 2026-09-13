"""NBA intake tables, deliberately separate from the MLB/NFL canonical ones.

WHY SEPARATE TABLES
-------------------
Two reasons, and the second is the important one.

First, NBA identity is its own namespace: provider team and player ids collide
with nothing here, and joining a basketball game into ``games`` would put a
sport with different period structure and no probable-pitcher concept into rows
that the MLB pipeline reads.

Second, and the reason this is not negotiable: until somebody establishes what
the provider account permits, everything this path can produce is FIXTURE data.
Fixture rows must be incapable of reaching a customer-facing surface or a model
input by accident. Every row here carries ``data_mode``, every read filters on
it, and no MLB or NFL table is touched at all.

``data_mode`` values come from ``app.nba.entitlement.DataMode``: REAL, SCRAMBLED,
FIXTURE, UNKNOWN. A row is what it says it is; nothing infers REAL from the
presence of plausible numbers.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Integer,
    JSON, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.entities import CreatedAtMixin, UUIDPrimaryKeyMixin

#: Applied to every table here. A row that cannot say what kind of data it is
#: does not get written.
_DATA_MODE = "data_mode IN ('REAL', 'SCRAMBLED', 'FIXTURE', 'UNKNOWN')"


class NbaSourcePayload(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One original provider response, kept byte-exact where retention allows.

    ``retrieved_at`` is when AISP received the bytes. ``provider_updated_at`` is
    the provider's own update stamp when it supplies one. Neither is the event
    time, and the three are never collapsed.
    """

    __tablename__ = "nba_source_payloads"

    source_identity: Mapped[str] = mapped_column(String(64), nullable=False)
    endpoint_key: Mapped[str] = mapped_column(String(64), nullable=False)
    url_template: Mapped[str] = mapped_column(Text, nullable=False)
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    payload_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_count: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Null when retention of original bytes is not permitted; the checksum and
    #: byte count are still kept so a later copy can be proven identical.
    payload: Mapped[bytes | None] = mapped_column(nullable=True)
    retention_permitted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provider_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ingestion_runs.id")
    )
    notes: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        # The same bytes from the same endpoint and parameters are one payload.
        UniqueConstraint("payload_checksum", "endpoint_key",
                         name="uq_nba_payload_identity"),
        CheckConstraint(_DATA_MODE, name="ck_nba_payload_data_mode"),
        CheckConstraint("byte_count >= 0", name="ck_nba_payload_bytes"),
    )


class NbaTeam(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Provider team identity, crosswalked to a stable AISP id."""

    __tablename__ = "nba_teams"

    provider_team_id: Mapped[int] = mapped_column(Integer, nullable=False)
    team_key: Mapped[str] = mapped_column(String(8), nullable=False)
    city: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str | None] = mapped_column(String(64))
    active: Mapped[bool | None] = mapped_column(Boolean)
    data_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    source_payload_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("nba_source_payloads.id")
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("provider_team_id", "data_mode", name="uq_nba_team_identity"),
        CheckConstraint(_DATA_MODE, name="ck_nba_team_data_mode"),
    )


class NbaPlayer(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "nba_players"

    provider_player_id: Mapped[int] = mapped_column(Integer, nullable=False)
    first_name: Mapped[str | None] = mapped_column(String(64))
    last_name: Mapped[str | None] = mapped_column(String(64))
    position: Mapped[str | None] = mapped_column(String(16))
    data_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    source_payload_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("nba_source_payloads.id")
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("provider_player_id", "data_mode", name="uq_nba_player_identity"),
        CheckConstraint(_DATA_MODE, name="ck_nba_player_data_mode"),
    )


class NbaRosterMembership(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """CURRENT roster state, which is not game participation.

    A roster row says where a player is listed now. It says nothing about who
    appeared in a past game -- that is ``NbaPlayerGameStat``. Keeping them apart
    is what stops a traded player's current club being back-projected onto games
    they played for someone else.
    """

    __tablename__ = "nba_roster_memberships"

    nba_team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nba_teams.id"), nullable=False)
    nba_player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nba_players.id"), nullable=False)
    jersey: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(String(32))
    position: Mapped[str | None] = mapped_column(String(16))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    source_payload_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("nba_source_payloads.id")
    )

    __table_args__ = (
        UniqueConstraint("nba_team_id", "nba_player_id", "observed_at",
                         name="uq_nba_roster_observation"),
        CheckConstraint(_DATA_MODE, name="ck_nba_roster_data_mode"),
    )


class NbaGame(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Schedule and final score, revisioned.

    ``revision`` and ``supersedes_game_id`` carry provider corrections forward
    without editing what was previously recorded. A rescheduled game keeps its
    provider id, so the mapping survives a date change -- games are never joined
    by team names and a date.
    """

    __tablename__ = "nba_games"

    provider_game_id: Mapped[int] = mapped_column(Integer, nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    season_type: Mapped[int] = mapped_column(Integer, nullable=False)
    season_type_label: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str | None] = mapped_column(String(24))
    home_nba_team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nba_teams.id"), nullable=False)
    away_nba_team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nba_teams.id"), nullable=False)
    game_day: Mapped[date | None] = mapped_column(Date)
    #: Provider local wall clock, US Eastern, as supplied and WITHOUT an offset.
    scheduled_local: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    #: Only populated when the provider supplies an explicit UTC field.
    scheduled_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    period_scores: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    is_final: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    provider_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    supersedes_game_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("nba_games.id"))
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    identity_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    data_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    source_payload_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("nba_source_payloads.id")
    )

    __table_args__ = (
        UniqueConstraint("provider_game_id", "data_mode", "revision",
                         name="uq_nba_game_revision"),
        UniqueConstraint("identity_checksum", name="uq_nba_game_identity"),
        CheckConstraint(_DATA_MODE, name="ck_nba_game_data_mode"),
        CheckConstraint("revision >= 1", name="ck_nba_game_revision"),
        CheckConstraint("home_nba_team_id <> away_nba_team_id",
                        name="ck_nba_game_distinct_sides"),
    )


class NbaTeamGameStat(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "nba_team_game_stats"

    nba_game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nba_games.id"), nullable=False)
    nba_team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nba_teams.id"), nullable=False)
    opponent_nba_team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("nba_teams.id"))
    is_home: Mapped[bool | None] = mapped_column(Boolean)
    points: Mapped[int | None] = mapped_column(Integer)
    stats: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    data_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    source_payload_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("nba_source_payloads.id")
    )
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("nba_game_id", "nba_team_id", name="uq_nba_team_game"),
        CheckConstraint(_DATA_MODE, name="ck_nba_team_game_data_mode"),
    )


class NbaPlayerGameStat(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Historical game participation. Not current roster state."""

    __tablename__ = "nba_player_game_stats"

    nba_game_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nba_games.id"), nullable=False)
    nba_player_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nba_players.id"), nullable=False)
    #: The club the player appeared FOR in this game.
    nba_team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("nba_teams.id"))
    opponent_nba_team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("nba_teams.id"))
    provider_stat_id: Mapped[int | None] = mapped_column(BigInteger)
    started: Mapped[bool | None] = mapped_column(Boolean)
    minutes: Mapped[int | None] = mapped_column(Integer)
    points: Mapped[int | None] = mapped_column(Integer)
    stats: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    data_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    source_payload_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("nba_source_payloads.id")
    )
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("nba_game_id", "nba_player_id", name="uq_nba_player_game"),
        CheckConstraint(_DATA_MODE, name="ck_nba_player_game_data_mode"),
    )


__all__ = [
    "NbaGame", "NbaPlayer", "NbaPlayerGameStat", "NbaRosterMembership",
    "NbaSourcePayload", "NbaTeam", "NbaTeamGameStat",
]

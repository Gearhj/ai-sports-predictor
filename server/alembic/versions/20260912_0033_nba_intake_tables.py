"""NBA intake tables, isolated from the MLB/NFL canonical path.

Revision ID: 20260912_0033
Revises: 20260911_0032
Create Date: 2026-09-12

Seven tables in their own namespace. Every row carries ``data_mode`` with a
CHECK constraint, so a FIXTURE row is self-identifying and cannot be mistaken
for provider data by any reader. No existing table is altered.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260912_0033"
down_revision = "20260911_0032"
branch_labels = None
depends_on = None

_MODE = "data_mode IN ('REAL', 'SCRAMBLED', 'FIXTURE', 'UNKNOWN')"


def upgrade() -> None:
    op.create_table(
        "nba_source_payloads",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("source_identity", sa.String(64), nullable=False),
        sa.Column("endpoint_key", sa.String(64), nullable=False),
        sa.Column("url_template", sa.Text(), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("payload_checksum", sa.String(64), nullable=False),
        sa.Column("byte_count", sa.Integer(), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=True),
        sa.Column("retention_permitted", sa.Boolean(), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_mode", sa.String(16), nullable=False),
        sa.Column("ingestion_run_id", sa.Uuid(), sa.ForeignKey("ingestion_runs.id"),
                  nullable=True),
        sa.Column("notes", sa.JSON(), nullable=False),
        sa.UniqueConstraint("payload_checksum", "endpoint_key",
                            name="uq_nba_payload_identity"),
        sa.CheckConstraint(_MODE, name="ck_nba_payload_data_mode"),
        sa.CheckConstraint("byte_count >= 0", name="ck_nba_payload_bytes"),
    )
    op.create_table(
        "nba_teams",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("provider_team_id", sa.Integer(), nullable=False),
        sa.Column("team_key", sa.String(8), nullable=False),
        sa.Column("city", sa.String(64), nullable=True),
        sa.Column("name", sa.String(64), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=True),
        sa.Column("data_mode", sa.String(16), nullable=False),
        sa.Column("source_payload_id", sa.Uuid(),
                  sa.ForeignKey("nba_source_payloads.id"), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider_team_id", "data_mode",
                            name="uq_nba_team_identity"),
        sa.CheckConstraint(_MODE, name="ck_nba_team_data_mode"),
    )
    op.create_table(
        "nba_players",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("provider_player_id", sa.Integer(), nullable=False),
        sa.Column("first_name", sa.String(64), nullable=True),
        sa.Column("last_name", sa.String(64), nullable=True),
        sa.Column("position", sa.String(16), nullable=True),
        sa.Column("data_mode", sa.String(16), nullable=False),
        sa.Column("source_payload_id", sa.Uuid(),
                  sa.ForeignKey("nba_source_payloads.id"), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider_player_id", "data_mode",
                            name="uq_nba_player_identity"),
        sa.CheckConstraint(_MODE, name="ck_nba_player_data_mode"),
    )
    op.create_table(
        "nba_games",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("provider_game_id", sa.Integer(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("season_type", sa.Integer(), nullable=False),
        sa.Column("season_type_label", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), nullable=True),
        sa.Column("home_nba_team_id", sa.Uuid(), sa.ForeignKey("nba_teams.id"),
                  nullable=False),
        sa.Column("away_nba_team_id", sa.Uuid(), sa.ForeignKey("nba_teams.id"),
                  nullable=False),
        sa.Column("game_day", sa.Date(), nullable=True),
        sa.Column("scheduled_local", sa.DateTime(timezone=False), nullable=True),
        sa.Column("scheduled_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("home_score", sa.Integer(), nullable=True),
        sa.Column("away_score", sa.Integer(), nullable=True),
        sa.Column("period_scores", sa.JSON(), nullable=False),
        sa.Column("is_final", sa.Boolean(), nullable=False),
        sa.Column("provider_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("supersedes_game_id", sa.Uuid(), sa.ForeignKey("nba_games.id"),
                  nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("identity_checksum", sa.String(64), nullable=False),
        sa.Column("data_mode", sa.String(16), nullable=False),
        sa.Column("source_payload_id", sa.Uuid(),
                  sa.ForeignKey("nba_source_payloads.id"), nullable=True),
        sa.UniqueConstraint("provider_game_id", "data_mode", "revision",
                            name="uq_nba_game_revision"),
        sa.UniqueConstraint("identity_checksum", name="uq_nba_game_identity"),
        sa.CheckConstraint(_MODE, name="ck_nba_game_data_mode"),
        sa.CheckConstraint("revision >= 1", name="ck_nba_game_revision"),
        sa.CheckConstraint("home_nba_team_id <> away_nba_team_id",
                           name="ck_nba_game_distinct_sides"),
    )
    op.create_table(
        "nba_roster_memberships",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("nba_team_id", sa.Uuid(), sa.ForeignKey("nba_teams.id"),
                  nullable=False),
        sa.Column("nba_player_id", sa.Uuid(), sa.ForeignKey("nba_players.id"),
                  nullable=False),
        sa.Column("jersey", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("position", sa.String(16), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_mode", sa.String(16), nullable=False),
        sa.Column("source_payload_id", sa.Uuid(),
                  sa.ForeignKey("nba_source_payloads.id"), nullable=True),
        sa.UniqueConstraint("nba_team_id", "nba_player_id", "observed_at",
                            name="uq_nba_roster_observation"),
        sa.CheckConstraint(_MODE, name="ck_nba_roster_data_mode"),
    )
    op.create_table(
        "nba_team_game_stats",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("nba_game_id", sa.Uuid(), sa.ForeignKey("nba_games.id"),
                  nullable=False),
        sa.Column("nba_team_id", sa.Uuid(), sa.ForeignKey("nba_teams.id"),
                  nullable=False),
        sa.Column("opponent_nba_team_id", sa.Uuid(), sa.ForeignKey("nba_teams.id"),
                  nullable=True),
        sa.Column("is_home", sa.Boolean(), nullable=True),
        sa.Column("points", sa.Integer(), nullable=True),
        sa.Column("stats", sa.JSON(), nullable=False),
        sa.Column("data_mode", sa.String(16), nullable=False),
        sa.Column("source_payload_id", sa.Uuid(),
                  sa.ForeignKey("nba_source_payloads.id"), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("nba_game_id", "nba_team_id", name="uq_nba_team_game"),
        sa.CheckConstraint(_MODE, name="ck_nba_team_game_data_mode"),
    )
    op.create_table(
        "nba_player_game_stats",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("nba_game_id", sa.Uuid(), sa.ForeignKey("nba_games.id"),
                  nullable=False),
        sa.Column("nba_player_id", sa.Uuid(), sa.ForeignKey("nba_players.id"),
                  nullable=False),
        sa.Column("nba_team_id", sa.Uuid(), sa.ForeignKey("nba_teams.id"),
                  nullable=True),
        sa.Column("opponent_nba_team_id", sa.Uuid(), sa.ForeignKey("nba_teams.id"),
                  nullable=True),
        sa.Column("provider_stat_id", sa.BigInteger(), nullable=True),
        sa.Column("started", sa.Boolean(), nullable=True),
        sa.Column("minutes", sa.Integer(), nullable=True),
        sa.Column("points", sa.Integer(), nullable=True),
        sa.Column("stats", sa.JSON(), nullable=False),
        sa.Column("data_mode", sa.String(16), nullable=False),
        sa.Column("source_payload_id", sa.Uuid(),
                  sa.ForeignKey("nba_source_payloads.id"), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("nba_game_id", "nba_player_id", name="uq_nba_player_game"),
        sa.CheckConstraint(_MODE, name="ck_nba_player_game_data_mode"),
    )


def downgrade() -> None:
    for table in (
        "nba_player_game_stats", "nba_team_game_stats", "nba_roster_memberships",
        "nba_games", "nba_players", "nba_teams", "nba_source_payloads",
    ):
        op.drop_table(table)

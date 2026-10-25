"""Authentic sportsbook player-prop quotes

One new table for provider player-prop outcomes, bound to a canonical MLB game
and player or held QUARANTINED. No existing table is altered, and nothing in the
decision, staking, bankroll, settlement or performance code reads it.

Downgrade drops only this table.

Revision ID: 20260917_0044
Revises: 20260915_0043
Create Date: 2026-09-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260917_0044"
down_revision = "20260915_0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "player_prop_quotes",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("provider_event_id", sa.String(120), nullable=False),
        sa.Column("provider_sport_key", sa.String(60), nullable=False),
        sa.Column("provider_market_key", sa.String(60), nullable=False),
        sa.Column("provider_outcome_name", sa.String(40), nullable=False),
        sa.Column("provider_player_name", sa.String(160), nullable=False),
        sa.Column("provider_point", sa.Float()),
        sa.Column("sportsbook_key", sa.String(80), nullable=False),
        sa.Column("sportsbook_title", sa.String(120)),
        sa.Column("american_price", sa.Integer()),
        sa.Column("decimal_price", sa.Float()),
        sa.Column("market_status", sa.String(24), nullable=False),
        sa.Column("provider_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_mode", sa.String(24), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("request_ledger_id", sa.Uuid(as_uuid=True), sa.ForeignKey("provider_request_ledger.id")),
        sa.Column("game_id", sa.Uuid(as_uuid=True), sa.ForeignKey("games.id")),
        sa.Column("mlb_game_ref", sa.String(40)),
        sa.Column("player_id", sa.Uuid(as_uuid=True), sa.ForeignKey("players.id")),
        sa.Column("team_id", sa.Uuid(as_uuid=True), sa.ForeignKey("teams.id")),
        sa.Column("outcome_side", sa.String(8)),
        sa.Column("line", sa.Float()),
        sa.Column("identity_status", sa.String(16), nullable=False),
        sa.Column("identity_evidence", sa.String(40)),
        sa.Column("quarantine_reason", sa.String(60)),
        sa.Column("selectable", sa.Boolean(), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "provider", "provider_event_id", "sportsbook_key", "provider_market_key",
            "provider_player_name", "provider_outcome_name", "provider_point", "provider_observed_at",
            name="uq_player_prop_quote_observation",
        ),
        sa.CheckConstraint("identity_status IN ('RESOLVED', 'QUARANTINED')", name="ck_player_prop_identity_status"),
        sa.CheckConstraint(
            "(identity_status = 'RESOLVED' AND game_id IS NOT NULL AND player_id IS NOT NULL "
            "AND team_id IS NOT NULL AND outcome_side IS NOT NULL AND quarantine_reason IS NULL) "
            "OR (identity_status = 'QUARANTINED' AND selectable = false AND quarantine_reason IS NOT NULL)",
            name="ck_player_prop_binding",
        ),
    )
    op.create_index("ix_player_prop_quotes_game_id", "player_prop_quotes", ["game_id"])


def downgrade() -> None:
    op.drop_index("ix_player_prop_quotes_game_id", table_name="player_prop_quotes")
    op.drop_table("player_prop_quotes")

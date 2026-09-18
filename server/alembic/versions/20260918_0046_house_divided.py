"""A House Divided -- virtual competition participants, accounts and wagers

Three new tables. No existing table, column, index or constraint is altered,
and the $1,000 Experiment's accounts, positions and settlements are untouched:
competition accounts are new rows with their own balances.

Seeds the three participants and a Not-started $1,000 account for each of them
in each supported sport. Nothing is started here -- a sport's start is recorded
separately, so nothing earlier can ever be imported.

Downgrade drops only these three tables.

Revision ID: 20260918_0046
Revises: 20260917_0045
Create Date: 2026-09-18
"""
from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

revision = "20260918_0046"
down_revision = "20260917_0045"
branch_labels = None
depends_on = None

PARTICIPANTS = (
    ("JAMES", "James — The Models", "#2f7de1", True, 0),
    ("SCOTTY", "Scotty", "#e0772f", False, 1),
    ("RED_LOU", "Red Lou — Mom", "#c9304a", False, 2),
)
SPORTS = ("MLB", "NFL", "NBA", "CFB")


def upgrade() -> None:
    participants = op.create_table(
        "competition_participants",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("key", sa.String(24), nullable=False, unique=True),
        sa.Column("display_name", sa.String(80), nullable=False),
        sa.Column("color", sa.String(16), nullable=False),
        sa.Column("is_model", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("user_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    accounts = op.create_table(
        "competition_accounts",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("participant_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("competition_participants.id"), nullable=False),
        sa.Column("sport", sa.String(8), nullable=False),
        sa.Column("starting_balance", sa.Numeric(12, 2), nullable=False, server_default="1000.00"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("started_by_user_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id")),
        sa.UniqueConstraint("participant_id", "sport", name="uq_competition_account_sport"),
        sa.CheckConstraint("starting_balance > 0", name="ck_competition_account_positive"),
    )
    op.create_index("ix_competition_accounts_participant_id", "competition_accounts", ["participant_id"])
    op.create_index("ix_competition_accounts_sport", "competition_accounts", ["sport"])

    op.create_table(
        "competition_wagers",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("account_id", sa.Uuid(as_uuid=True), sa.ForeignKey("competition_accounts.id"), nullable=False),
        sa.Column("participant_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("competition_participants.id"), nullable=False),
        sa.Column("sport", sa.String(8), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("game_id", sa.Uuid(as_uuid=True), sa.ForeignKey("games.id"), nullable=False),
        sa.Column("market_type", sa.String(16)),
        sa.Column("market_outcome_id", sa.Uuid(as_uuid=True), sa.ForeignKey("market_outcomes.id")),
        sa.Column("selection_label", sa.String(80)),
        sa.Column("line", sa.Float()),
        sa.Column("sportsbook_id", sa.Uuid(as_uuid=True), sa.ForeignKey("sportsbooks.id")),
        sa.Column("sportsbook_key", sa.String(80)),
        sa.Column("odds_snapshot_id", sa.Uuid(as_uuid=True), sa.ForeignKey("odds_snapshots.id")),
        sa.Column("american_odds", sa.Integer()),
        sa.Column("decimal_odds", sa.Float()),
        sa.Column("quote_observed_at", sa.DateTime(timezone=True)),
        sa.Column("quote_available_as_of", sa.DateTime(timezone=True)),
        sa.Column("stake", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("entered_by_user_id", sa.Uuid(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("source_decision_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("shadow_experiment_decisions.id"), unique=True),
        sa.Column("source_experiment_id", sa.Uuid(as_uuid=True), sa.ForeignKey("shadow_experiments.id")),
        sa.Column("model_version", sa.String(120)),
        sa.Column("decision_recommended_at", sa.DateTime(timezone=True)),
        sa.Column("staking_detail", sa.JSON()),
        sa.Column("unfunded_reason", sa.String(200)),
        sa.Column("result", sa.String(8)),
        sa.Column("profit_loss", sa.Numeric(12, 2)),
        sa.Column("payout", sa.Numeric(12, 2)),
        sa.Column("settled_at", sa.DateTime(timezone=True)),
        sa.Column("settlement_note", sa.String(200)),
        sa.Column("game_home_score", sa.Integer()),
        sa.Column("game_away_score", sa.Integer()),
        sa.CheckConstraint("stake >= 0", name="ck_competition_wager_stake_nonnegative"),
        sa.CheckConstraint("status IN ('DRAFT','OPEN','SETTLED','UNFUNDED','DISCARDED')",
                           name="ck_competition_wager_status"),
        sa.CheckConstraint("source IN ('MODEL_MIRROR','HUMAN')", name="ck_competition_wager_source"),
    )
    for column in ("account_id", "participant_id", "sport", "status", "game_id"):
        op.create_index(f"ix_competition_wagers_{column}", "competition_wagers", [column])

    # Seed: three participants, and a Not-started $1,000 account in every sport.
    participant_rows = []
    for key, name, color, is_model, order in PARTICIPANTS:
        participant_rows.append({"id": uuid.uuid4(), "key": key, "display_name": name,
                                 "color": color, "is_model": is_model, "sort_order": order})
    op.bulk_insert(participants, participant_rows)
    op.bulk_insert(accounts, [
        {"id": uuid.uuid4(), "participant_id": row["id"], "sport": sport,
         "starting_balance": 1000}
        for row in participant_rows for sport in SPORTS
    ])


def downgrade() -> None:
    for column in ("game_id", "status", "sport", "participant_id", "account_id"):
        op.drop_index(f"ix_competition_wagers_{column}", table_name="competition_wagers")
    op.drop_table("competition_wagers")
    op.drop_index("ix_competition_accounts_sport", table_name="competition_accounts")
    op.drop_index("ix_competition_accounts_participant_id", table_name="competition_accounts")
    op.drop_table("competition_accounts")
    op.drop_table("competition_participants")

"""Recorded virtual parlay tickets and their frozen legs.

Purely additive: two new tables, no change to ``official_asp_positions``,
``position_results``, ``experiment_accounts`` or any append-only evidence table.
Parlay exposure is a separate, separately tagged object read alongside the
straight-bet ledger -- not written into it, which would inflate straight-bet
counts and staked totals with money never staked that way.

``parlay_tickets.ticket_sha256`` is unique, so recording the same frozen ticket
twice is refused by the schema rather than by a caller remembering to check.
``parlay_ticket_legs`` is unique on ``(ticket_id, game_id)``, which is how the
first-release rule "one selection per game" is enforced in the store and not
only in the enumerator.

Revision ID: 20260913_0035
Revises: 20260912_0034
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260913_0035"
down_revision = "20260912_0034"
branch_labels = None
depends_on = None

POSITION_STATUS = ("OPEN", "SETTLED", "VOID")
POSITION_RESULT = ("PENDING", "WIN", "LOSS", "PUSH", "VOID")


def upgrade() -> None:
    op.create_table(
        "parlay_tickets",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("ticket_sha256", sa.String(length=64), nullable=False),
        sa.Column("ticket_key", sa.Text(), nullable=False),
        sa.Column("league", sa.String(length=16), nullable=False,
                  server_default="MLB"),
        sa.Column("sportsbook", sa.String(length=120), nullable=False),
        sa.Column("leg_count", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("experiment_accounts.id"), nullable=True),
        sa.Column("funding", sa.String(length=32), nullable=False,
                  server_default="RESEARCH_UNFUNDED"),
        sa.Column("stake", sa.Numeric(12, 2), nullable=False),
        sa.Column("combined_decimal_odds", sa.Float(), nullable=False),
        sa.Column("price_provenance", sa.String(length=48), nullable=False),
        sa.Column("probability_snapshot", sa.JSON(), nullable=False),
        sa.Column("settlement_rules", sa.JSON(), nullable=False),
        sa.Column("recorded_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("earliest_cutoff_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Enum(*POSITION_STATUS, name="positionstatus",
                                    native_enum=False, create_constraint=True),
                  nullable=False, server_default="OPEN"),
        sa.Column("result", sa.Enum(*POSITION_RESULT, name="positionresult",
                                    native_enum=False, create_constraint=True),
                  nullable=False, server_default="PENDING"),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payout", sa.Numeric(12, 2), nullable=True),
        sa.Column("profit_loss", sa.Numeric(12, 2), nullable=True),
        sa.Column("settlement_note", sa.Text(), nullable=True),
        sa.Column("settled_decimal_odds", sa.Float(), nullable=True),
        sa.CheckConstraint("stake > 0", name="ck_parlay_ticket_stake_positive"),
        sa.CheckConstraint("leg_count >= 2", name="ck_parlay_ticket_min_legs"),
    )
    op.create_index("ix_parlay_tickets_ticket_sha256", "parlay_tickets",
                    ["ticket_sha256"], unique=True)
    op.create_index("ix_parlay_tickets_account_id", "parlay_tickets", ["account_id"])
    op.create_index("ix_parlay_ticket_status_cutoff", "parlay_tickets",
                    ["status", "earliest_cutoff_utc"])

    op.create_table(
        "parlay_ticket_legs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("parlay_tickets.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Uuid(as_uuid=True), sa.ForeignKey("games.id"),
                  nullable=False),
        sa.Column("market_outcome_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("market_outcomes.id"), nullable=True),
        sa.Column("odds_snapshot_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("odds_snapshots.id"), nullable=True),
        sa.Column("market", sa.String(length=24), nullable=False),
        sa.Column("selection", sa.String(length=160), nullable=False),
        sa.Column("line", sa.Float(), nullable=True),
        sa.Column("decimal_odds", sa.Float(), nullable=False),
        sa.Column("american_odds", sa.Integer(), nullable=True),
        sa.Column("sportsbook", sa.String(length=120), nullable=False),
        sa.Column("quote_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("forecast_identity", sa.String(length=120), nullable=False),
        sa.Column("engine_id", sa.String(length=32), nullable=False),
        sa.Column("win_probability_pct", sa.Float(), nullable=False),
        sa.Column("push_probability_pct", sa.Float(), nullable=False),
        sa.Column("loss_probability_pct", sa.Float(), nullable=False),
        sa.Column("result", sa.Enum(*POSITION_RESULT, name="positionresult",
                                    native_enum=False, create_constraint=True),
                  nullable=False, server_default="PENDING"),
        sa.Column("settled_home_score", sa.Integer(), nullable=True),
        sa.Column("settled_away_score", sa.Integer(), nullable=True),
        sa.Column("settled_note", sa.Text(), nullable=True),
        sa.Column("voided", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.UniqueConstraint("ticket_id", "ordinal", name="uq_parlay_leg_ordinal"),
        sa.UniqueConstraint("ticket_id", "game_id",
                            name="uq_parlay_leg_one_per_game"),
    )
    op.create_index("ix_parlay_ticket_legs_ticket_id", "parlay_ticket_legs",
                    ["ticket_id"])
    op.create_index("ix_parlay_ticket_legs_game_id", "parlay_ticket_legs", ["game_id"])
    op.create_index("ix_parlay_ticket_legs_market_outcome_id", "parlay_ticket_legs",
                    ["market_outcome_id"])
    op.create_index("ix_parlay_ticket_legs_odds_snapshot_id", "parlay_ticket_legs",
                    ["odds_snapshot_id"])


def downgrade() -> None:
    op.drop_table("parlay_ticket_legs")
    op.drop_table("parlay_tickets")

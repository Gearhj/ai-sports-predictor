"""Season-scoped paper-bankroll accounts for the $1,000 experiment program.

Purely additive. Three new tables and no change to ``shadow_experiments``,
``official_asp_positions``, ``shadow_experiment_decisions`` or
``position_results`` — the funded MLB ledger is read by the new accounting, not
rewritten by it. An era is linked to its season account through a membership
row rather than a backfilled column, so append-only evidence stays untouched.

Revision ID: 20260912_0034
Revises: 20260912_0033
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260912_0034"
down_revision = "20260912_0033"
branch_labels = None
depends_on = None


ACCOUNT_STATUS = ("PLANNED", "ACTIVE", "CLOSED_FOR_NEW_POSITIONS", "ARCHIVED")
MEMBERSHIP_ROLE = ("FUNDED_ERA", "UNFUNDED_CONTROL", "RESEARCH_CANDIDATE")
FUNDING_KIND = ("SEASON_OPENING",)


def upgrade() -> None:
    op.create_table(
        "experiment_accounts",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("sport", sa.String(24), nullable=False),
        sa.Column("season_id", sa.String(16), nullable=False),
        sa.Column("season_label", sa.String(120), nullable=False),
        sa.Column("season_opens_on", sa.Date(), nullable=False),
        sa.Column("season_closes_on", sa.Date(), nullable=False),
        sa.Column("boundary_basis", sa.Text(), nullable=False),
        sa.Column("boundary_convention", sa.Text()),
        sa.Column("status", sa.Enum(*ACCOUNT_STATUS, name="accountstatus"), nullable=False),
        sa.Column("planned_contribution", sa.Numeric(12, 2), nullable=False),
        sa.Column("tracking_started_on", sa.Date()),
        sa.Column("partial_season", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status_note", sa.Text()),
        sa.UniqueConstraint("sport", "season_id", name="uq_experiment_account_sport_season"),
        sa.CheckConstraint("planned_contribution >= 0", name="ck_experiment_account_contribution"),
    )
    op.create_index("ix_experiment_accounts_sport", "experiment_accounts", ["sport"])

    op.create_table(
        "experiment_account_fundings",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("account_id", sa.UUID(as_uuid=True),
                  sa.ForeignKey("experiment_accounts.id"), nullable=False),
        sa.Column("kind", sa.Enum(*FUNDING_KIND, name="fundingkind"), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("funded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_on", sa.Date(), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("job_run_id", sa.UUID(as_uuid=True), sa.ForeignKey("job_runs.id")),
        sa.Column("executed_by", sa.String(80), nullable=False),
        sa.Column("note", sa.Text()),
        # Two independent guards against a second opening contribution: the
        # natural key (one opening per account) and the job's own key.
        sa.UniqueConstraint("account_id", "kind", name="uq_account_funding_once"),
        sa.UniqueConstraint("idempotency_key", name="uq_account_funding_idempotency"),
        sa.CheckConstraint("amount > 0", name="ck_account_funding_amount_positive"),
    )
    op.create_index(
        "ix_experiment_account_fundings_account_id",
        "experiment_account_fundings", ["account_id"],
    )

    op.create_table(
        "experiment_account_memberships",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("account_id", sa.UUID(as_uuid=True),
                  sa.ForeignKey("experiment_accounts.id"), nullable=False),
        sa.Column("experiment_id", sa.UUID(as_uuid=True),
                  sa.ForeignKey("shadow_experiments.id"), nullable=False),
        sa.Column("role", sa.Enum(*MEMBERSHIP_ROLE, name="membershiprole"), nullable=False),
        sa.Column("role_basis", sa.Text(), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        # One era belongs to one account. This is what makes "count each
        # position and settlement exactly once" a schema guarantee rather than
        # a property of the aggregation query.
        sa.UniqueConstraint("experiment_id", name="uq_account_membership_experiment"),
    )
    op.create_index(
        "ix_experiment_account_memberships_account_id",
        "experiment_account_memberships", ["account_id"],
    )
    op.create_index(
        "ix_account_membership_account_role",
        "experiment_account_memberships", ["account_id", "role"],
    )


def downgrade() -> None:
    op.drop_index("ix_account_membership_account_role", table_name="experiment_account_memberships")
    op.drop_index("ix_experiment_account_memberships_account_id",
                  table_name="experiment_account_memberships")
    op.drop_table("experiment_account_memberships")
    op.drop_index("ix_experiment_account_fundings_account_id",
                  table_name="experiment_account_fundings")
    op.drop_table("experiment_account_fundings")
    op.drop_index("ix_experiment_accounts_sport", table_name="experiment_accounts")
    op.drop_table("experiment_accounts")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for name in ("accountstatus", "fundingkind", "membershiprole"):
            sa.Enum(name=name).drop(bind, checkfirst=True)

"""Observed scoreboard state: score, period breakdown, and honest freshness.

Purely additive: one new table. Deliberately NOT columns on
``game_state_observations``, which the research scorer, the label gate and
forecast eligibility all read. A two-minute display refresh writing into that
append-only stream would multiply it fifteenfold and put presentation data
inside the evidence that decides which forecasts are scoreable.

Three timestamps, because they answer different questions and the UI has to be
able to tell them apart: ``source_observed_at`` (when the SOURCE says the state
held -- NULL for both current sources, which supply no score timestamp),
``retrieved_at`` (when AISP received the bytes), and ``checked_at`` (the latest
successful check that confirmed this same state). ``checked_at`` advances in
place so an unchanged payload proves freshness rather than looking stale; it is
the only field here that is ever updated, and it records an observation about
the check, not about the game.

Unique on (game_id, source, payload_checksum), so one distinct state is one row
however many times it is observed.

Revision ID: 20260914_0037
Revises: 20260913_0036
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260914_0037"
down_revision = "20260913_0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "game_scoreboard_observations",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("game_id", sa.Uuid(as_uuid=True),
                  sa.ForeignKey("games.id"), nullable=False),
        sa.Column("league_code", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("source_mode", sa.String(length=16), nullable=False,
                  server_default="LIVE"),
        sa.Column("source_game_id", sa.String(length=120)),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("status_detail", sa.String(length=120)),
        sa.Column("home_score", sa.Integer()),
        sa.Column("away_score", sa.Integer()),
        sa.Column("period_number", sa.Integer()),
        sa.Column("period_label", sa.String(length=24)),
        sa.Column("period_state", sa.String(length=24)),
        sa.Column("in_break", sa.Boolean()),
        sa.Column("clock_seconds_remaining", sa.Integer()),
        sa.Column("outs", sa.Integer()),
        sa.Column("balls", sa.Integer()),
        sa.Column("strikes", sa.Integer()),
        sa.Column("bases", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("periods", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("periods_complete", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("periods_reconcile", sa.Boolean()),
        sa.Column("source_observed_at", sa.DateTime(timezone=True)),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_checksum", sa.String(length=64), nullable=False),
        sa.UniqueConstraint("game_id", "source", "payload_checksum",
                            name="uq_scoreboard_obs_state"),
        sa.CheckConstraint(
            "status IN ('SCHEDULED','IN_PROGRESS','DELAYED','SUSPENDED',"
            "'POSTPONED','CANCELLED','FINAL','UNKNOWN')",
            name="ck_scoreboard_obs_status",
        ),
        sa.CheckConstraint("home_score IS NULL OR home_score >= 0",
                           name="ck_scoreboard_obs_home"),
        sa.CheckConstraint("away_score IS NULL OR away_score >= 0",
                           name="ck_scoreboard_obs_away"),
        sa.CheckConstraint("outs IS NULL OR (outs >= 0 AND outs <= 3)",
                           name="ck_scoreboard_obs_outs"),
    )
    op.create_index("ix_scoreboard_obs_game_checked",
                    "game_scoreboard_observations", ["game_id", "checked_at"])
    op.create_index("ix_scoreboard_obs_league_checked",
                    "game_scoreboard_observations", ["league_code", "checked_at"])


def downgrade() -> None:
    op.drop_index("ix_scoreboard_obs_league_checked",
                  table_name="game_scoreboard_observations")
    op.drop_index("ix_scoreboard_obs_game_checked",
                  table_name="game_scoreboard_observations")
    op.drop_table("game_scoreboard_observations")

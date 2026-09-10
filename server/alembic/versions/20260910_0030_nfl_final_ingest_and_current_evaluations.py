"""NFL final ingestion, result provenance tiers, and current-evaluation selection.

Two things land together because they are one idea: results can now arrive
automatically from more than one source, so the evaluation table needs a way to
say which of several honest evaluations of the same game is the answer.

Backfill: every existing evaluation is marked current and given the provenance
tier of its recorded source. The NE @ SEA operator-entered row therefore keeps
its place until an automatic source confirms the same game, at which point the
reconciler moves the flag without deleting anything.

Revision ID: 20260910_0030
Revises: 20260910_0029
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260910_0030"
down_revision = "20260910_0029"
branch_labels = None
depends_on = None

#: Mirrors app.services.result_provenance.RESULT_TIERS. Duplicated rather than
#: imported so this migration keeps meaning if the module later changes.
TIER_BACKFILL = {"THE_ODDS_API": 1, "NFLVERSE": 2, "OPERATOR_ENTERED": 3}
UNCLASSIFIED = 9


def upgrade() -> None:
    op.create_table(
        "nfl_final_observations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("game_id", sa.Uuid(), sa.ForeignKey("games.id"), nullable=False),
        sa.Column("source_event_id", sa.String(length=120), nullable=False),
        sa.Column("game_state_observation_id", sa.Uuid(),
                  sa.ForeignKey("game_state_observations.id")),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_mode", sa.String(length=16), nullable=False,
                  server_default="RESEARCH_SHADOW"),
        sa.Column("result_tier", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("home_score", sa.Integer()),
        sa.Column("away_score", sa.Integer()),
        sa.Column("overtime", sa.Boolean()),
        sa.Column("season", sa.Integer()),
        sa.Column("game_type", sa.String(length=16)),
        sa.Column("week", sa.Integer()),
        sa.Column("source_last_updated", sa.String(length=64)),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_checksum", sa.String(length=64), nullable=False),
        sa.Column("feed_checksum", sa.String(length=64)),
        sa.Column("raw_file_reference", sa.String(length=500)),
        sa.Column("raw_row", sa.JSON(), nullable=False, server_default="{}"),
        sa.UniqueConstraint("game_id", "source", "payload_checksum",
                            name="uq_nfl_final_observation_identity"),
    )
    op.create_index("ix_nfl_final_observations_game_id", "nfl_final_observations", ["game_id"])
    op.create_index("ix_nfl_final_observations_source_event_id", "nfl_final_observations",
                    ["source_event_id"])
    op.create_index("ix_nfl_final_observations_game_state_observation_id",
                    "nfl_final_observations", ["game_state_observation_id"])
    op.create_index("ix_nfl_final_obs_source_event", "nfl_final_observations",
                    ["source", "source_event_id"])

    op.add_column("research_forecast_evaluations",
                  sa.Column("result_tier", sa.Integer(), nullable=False, server_default="9"))
    op.add_column("research_forecast_evaluations",
                  sa.Column("is_current", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("research_forecast_evaluations",
                  sa.Column("superseded_at", sa.DateTime(timezone=True)))
    op.add_column("research_forecast_evaluations",
                  sa.Column("superseded_by_id", sa.Uuid(),
                            sa.ForeignKey("research_forecast_evaluations.id")))
    op.add_column("research_forecast_evaluations",
                  sa.Column("supersede_reason", sa.String(length=48)))

    for source, tier in TIER_BACKFILL.items():
        op.execute(sa.text(
            "UPDATE research_forecast_evaluations SET result_tier = :tier "
            "WHERE upper(result_source) = :source"
        ).bindparams(tier=tier, source=source))
    op.execute(sa.text(
        "UPDATE research_forecast_evaluations SET result_tier = :tier "
        "WHERE upper(result_source) NOT IN ('THE_ODDS_API', 'NFLVERSE', 'OPERATOR_ENTERED')"
    ).bindparams(tier=UNCLASSIFIED))

    op.create_index("ix_research_forecast_evaluations_is_current",
                    "research_forecast_evaluations", ["is_current"])
    # Structural guarantee: audit rows may accumulate for one game, but the
    # database itself refuses to hold two current evaluations of one forecast.
    op.create_index(
        "uq_research_forecast_eval_current", "research_forecast_evaluations",
        ["event_id", "engine_id", "engine_version", "forecast_identity"],
        unique=True, postgresql_where=sa.text("is_current"),
    )
    op.create_check_constraint(
        "ck_research_forecast_eval_supersession", "research_forecast_evaluations",
        "(is_current AND superseded_at IS NULL AND superseded_by_id IS NULL)"
        " OR (NOT is_current AND superseded_at IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_research_forecast_eval_supersession",
                       "research_forecast_evaluations", type_="check")
    op.drop_index("uq_research_forecast_eval_current", table_name="research_forecast_evaluations")
    op.drop_index("ix_research_forecast_evaluations_is_current",
                  table_name="research_forecast_evaluations")
    op.drop_column("research_forecast_evaluations", "supersede_reason")
    op.drop_column("research_forecast_evaluations", "superseded_by_id")
    op.drop_column("research_forecast_evaluations", "superseded_at")
    op.drop_column("research_forecast_evaluations", "is_current")
    op.drop_column("research_forecast_evaluations", "result_tier")
    op.drop_index("ix_nfl_final_obs_source_event", table_name="nfl_final_observations")
    op.drop_index("ix_nfl_final_observations_game_state_observation_id",
                  table_name="nfl_final_observations")
    op.drop_index("ix_nfl_final_observations_source_event_id", table_name="nfl_final_observations")
    op.drop_index("ix_nfl_final_observations_game_id", table_name="nfl_final_observations")
    op.drop_table("nfl_final_observations")

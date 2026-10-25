"""NHL Gen1 research consumer: forecasts, results, import receipts (integration candidate)

Additive only: three NEW tables (nhl_research_forecasts, nhl_game_results,
nhl_import_receipts) with their indexes, unique and check constraints. No
existing table, column, index or row is touched and nothing is back-filled.
Foreign keys point FROM the new tables TO games.id; creating them takes a SHARE
ROW EXCLUSIVE lock on games only for the instant of constraint creation on an
empty table (no scan of games rows is needed beyond FK validation of zero rows).

CHAIN: descends from 20260925_0053 (MLB_V18_FUNDING_PAUSE_V1), the production
head confirmed read-only on 2026-10-01 and the head at the release base 52b2ac1.
If another migration lands first, re-point down_revision before release.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261001_0054"
down_revision = "20260925_0053"
branch_labels = None
depends_on = None

_TS = sa.DateTime(timezone=True)
_REGULATION = ("regulation_home_probability", "regulation_tie_probability", "regulation_away_probability")
_FINAL = ("final_home_probability", "final_away_probability")
_FRACTIONS = _REGULATION + _FINAL


def upgrade() -> None:
    op.create_table(
        "nhl_research_forecasts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("forecast_id", sa.String(120), nullable=False),
        sa.Column("object_sha256", sa.String(64), nullable=False),
        sa.Column("object_bytes", sa.Integer(), nullable=False),
        sa.Column("manifest_sha256", sa.String(64)),
        sa.Column("forecast_schema", sa.String(60), nullable=False),
        sa.Column("supersedes_forecast_id", sa.String(120)),
        sa.Column("model_id", sa.String(120), nullable=False),
        sa.Column("model_version", sa.String(60), nullable=False),
        sa.Column("configuration_sha256", sa.String(64), nullable=False),
        sa.Column("canonical_game_ref", sa.String(40), nullable=False),
        sa.Column("source_game_id", sa.String(16), nullable=False),
        sa.Column("game_id", sa.Uuid(), sa.ForeignKey("games.id")),
        sa.Column("season", sa.String(9), nullable=False),
        sa.Column("game_type", sa.String(12), nullable=False),
        sa.Column("scheduled_start_utc", _TS, nullable=False),
        sa.Column("home_canonical_team_ref", sa.String(20), nullable=False),
        sa.Column("away_canonical_team_ref", sa.String(20), nullable=False),
        *[sa.Column(name, sa.Float()) for name in _REGULATION],
        *[sa.Column(name, sa.Float(), nullable=False) for name in _FINAL],
        sa.Column("overtime_probability", sa.Float()),
        sa.Column("shootout_probability", sa.Float()),
        sa.Column("regulation_expected_home_goals", sa.Float()),
        sa.Column("regulation_expected_away_goals", sa.Float()),
        sa.Column("final_expected_home_goals", sa.Float()),
        sa.Column("final_expected_away_goals", sa.Float()),
        sa.Column("home_goalie_status", sa.String(12), nullable=False),
        sa.Column("away_goalie_status", sa.String(12), nullable=False),
        sa.Column("goalies", sa.JSON(), nullable=False),
        sa.Column("missing_inputs", sa.JSON(), nullable=False),
        sa.Column("evidence_mode", sa.String(48), nullable=False),
        sa.Column("goalie_feature_mode", sa.String(20), nullable=False),
        sa.Column("producer_evidence_ref", sa.JSON(), nullable=False),
        sa.Column("producer_evidence_status", sa.String(32), nullable=False),
        sa.Column("record_class", sa.String(20), nullable=False),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.Column("source_cutoff_utc", _TS, nullable=False),
        sa.Column("source_retrieved_utc", _TS, nullable=False),
        sa.Column("forecast_created_utc", _TS, nullable=False),
        sa.Column("published_utc", _TS),
        sa.Column("publication_clock_source", sa.String(40), nullable=False),
        sa.Column("available_as_of", _TS, nullable=False),
        sa.Column("imported_at", _TS, nullable=False),
        sa.Column("research_only", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("forecast_id", name="uq_nhl_research_forecast_id"),
        sa.CheckConstraint("research_only", name="ck_nhl_research_only"),
        sa.CheckConstraint("game_type IN ('PRESEASON','REGULAR','PLAYOFF')", name="ck_nhl_forecast_game_type"),
        sa.CheckConstraint("record_class IN ('FORECAST','PREVIEW_FIXTURE','SYNTHETIC_FIXTURE')",
                           name="ck_nhl_forecast_record_class"),
        sa.CheckConstraint(
            "(regulation_home_probability IS NULL) = (regulation_tie_probability IS NULL) AND "
            "(regulation_home_probability IS NULL) = (regulation_away_probability IS NULL)",
            name="ck_nhl_regulation_all_or_none"),
        *[sa.CheckConstraint(f"{n} >= 0 AND {n} <= 1", name=f"ck_nhl_{n}_fraction") for n in _FRACTIONS],
    )
    op.create_index("ix_nhl_research_forecasts_canonical_game_ref", "nhl_research_forecasts", ["canonical_game_ref"])
    op.create_index("ix_nhl_research_forecasts_game_id", "nhl_research_forecasts", ["game_id"])
    op.create_index("ix_nhl_forecast_game_published", "nhl_research_forecasts", ["canonical_game_ref", "published_utc"])

    op.create_table(
        "nhl_game_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("result_sha256", sa.String(64), nullable=False),
        sa.Column("object_sha256", sa.String(64)),
        sa.Column("supersedes_result_sha256", sa.String(64)),
        sa.Column("canonical_game_ref", sa.String(40), nullable=False),
        sa.Column("game_id", sa.Uuid(), sa.ForeignKey("games.id")),
        sa.Column("game_type", sa.String(12), nullable=False),
        sa.Column("regulation_home_goals", sa.Integer(), nullable=False),
        sa.Column("regulation_away_goals", sa.Integer(), nullable=False),
        sa.Column("regulation_result", sa.String(4), nullable=False),
        sa.Column("regulation_source", sa.String(60), nullable=False),
        sa.Column("regulation_observed_utc", _TS, nullable=False),
        sa.Column("regulation_retrieved_utc", _TS, nullable=False),
        sa.Column("decided_in", sa.String(10), nullable=False),
        sa.Column("overtime_home_goals", sa.Integer()),
        sa.Column("overtime_away_goals", sa.Integer()),
        sa.Column("shootout_home_scored", sa.Integer()),
        sa.Column("shootout_away_scored", sa.Integer()),
        sa.Column("final_home_score", sa.Integer(), nullable=False),
        sa.Column("final_away_score", sa.Integer(), nullable=False),
        sa.Column("final_winner", sa.String(4), nullable=False),
        sa.Column("final_official", sa.Boolean(), nullable=False),
        sa.Column("final_source", sa.String(60), nullable=False),
        sa.Column("final_observed_utc", _TS, nullable=False),
        sa.Column("final_retrieved_utc", _TS, nullable=False),
        sa.Column("final_completed_utc", _TS),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.Column("available_as_of", _TS, nullable=False),
        sa.Column("imported_at", _TS, nullable=False),
        sa.UniqueConstraint("result_sha256", name="uq_nhl_game_result_sha256"),
        sa.CheckConstraint("regulation_result IN ('HOME','AWAY','TIE')", name="ck_nhl_regulation_result"),
        sa.CheckConstraint("decided_in IN ('REGULATION','OVERTIME','SHOOTOUT')", name="ck_nhl_decided_in"),
        sa.CheckConstraint("final_winner IN ('HOME','AWAY')", name="ck_nhl_final_never_tied"),
        sa.CheckConstraint("final_home_score <> final_away_score", name="ck_nhl_final_score_not_tied"),
        sa.CheckConstraint("(decided_in = 'REGULATION') = (regulation_result <> 'TIE')",
                           name="ck_nhl_regulation_tie_iff_extra_time"),
    )
    op.create_index("ix_nhl_game_results_canonical_game_ref", "nhl_game_results", ["canonical_game_ref"])
    op.create_index("ix_nhl_game_results_game_id", "nhl_game_results", ["game_id"])

    op.create_table(
        "nhl_import_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("disposition", sa.String(20), nullable=False),
        sa.Column("identity", sa.String(120)),
        sa.Column("object_sha256", sa.String(64)),
        sa.Column("manifest_sha256", sa.String(64)),
        sa.Column("canonical_game_ref", sa.String(40)),
        sa.Column("game_bound", sa.Boolean(), nullable=False),
        sa.Column("problems", sa.JSON(), nullable=False),
        sa.Column("source_label", sa.String(300)),
        sa.Column("received_at", _TS, nullable=False),
    )
    op.create_index("ix_nhl_import_receipts_run_id", "nhl_import_receipts", ["run_id"])


def downgrade() -> None:
    op.drop_table("nhl_import_receipts")
    op.drop_table("nhl_game_results")
    op.drop_table("nhl_research_forecasts")

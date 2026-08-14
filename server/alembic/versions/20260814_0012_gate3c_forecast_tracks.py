"""Separate operational and Generation 2 research forecast tracks.

Revision ID: 20260814_0012
Revises: 20260814_0011
Create Date: 2026-08-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260814_0012"
down_revision = "20260814_0011"
branch_labels = None
depends_on = None


CURRENT_TRACK = "CURRENT_DEVELOPMENT"
RESEARCH_TRACK = "GEN2_RESEARCH_CHALLENGER"


def upgrade() -> None:
    # The server default both backfills every pre-Gate-3C row and keeps direct
    # database inserts fail-safe on the public operational track. The CHECK is
    # explicit so the boundary is enforced outside SQLAlchemy as well.
    with op.batch_alter_table("forecasts") as batch_op:
        batch_op.add_column(
            sa.Column(
                "forecast_track",
                # Match SQLAlchemy's non-native ForecastTrack enum storage
                # width so schema autogeneration remains drift-free.
                sa.String(length=24),
                server_default=sa.text(f"'{CURRENT_TRACK}'"),
                nullable=False,
            )
        )
        batch_op.drop_constraint("uq_forecast_game_version", type_="unique")
        batch_op.create_check_constraint(
            "forecasttrack",
            f"forecast_track IN ('{CURRENT_TRACK}', '{RESEARCH_TRACK}')",
        )
        batch_op.create_unique_constraint(
            "uq_forecast_game_track_version",
            ["game_id", "forecast_track", "version"],
        )
        batch_op.create_index(
            "ix_forecast_game_track_status_version",
            ["game_id", "forecast_track", "status", "version"],
            unique=False,
        )


def downgrade() -> None:
    # The pre-0012 schema has no way to preserve a research-only identity. Even
    # a non-colliding challenger version could become the old application's
    # public "latest" row after this column disappears, so fail closed whenever
    # any non-current row exists. Archival/removal requires a separate,
    # explicitly authorized migration policy.
    connection = op.get_bind()
    research_row = connection.execute(
        sa.text(
            "SELECT 1 FROM forecasts WHERE forecast_track <> :current_track LIMIT 1"
        ),
        {"current_track": CURRENT_TRACK},
    ).first()
    if research_row is not None:
        raise RuntimeError(
            "Cannot downgrade forecast tracks while any non-current forecast exists; "
            "archive or remove research rows under an explicit migration policy first"
        )

    with op.batch_alter_table("forecasts") as batch_op:
        batch_op.drop_index("ix_forecast_game_track_status_version")
        batch_op.drop_constraint("uq_forecast_game_track_version", type_="unique")
        batch_op.drop_constraint("forecasttrack", type_="check")
        batch_op.create_unique_constraint(
            "uq_forecast_game_version",
            ["game_id", "version"],
        )
        batch_op.drop_column("forecast_track")

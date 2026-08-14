"""Add A1 completion evidence and correction lineage.

Revision ID: 20260814_0006
Revises: 20260814_0005
Create Date: 2026-08-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260814_0006"
down_revision = "20260814_0005"
branch_labels = None
depends_on = None


def _recreate() -> str:
    return "always" if op.get_bind().dialect.name == "sqlite" else "auto"


def _populate_observation_revisions(
    table_name: str,
    semantic_columns: tuple[str, ...],
) -> None:
    """Convert valid A0 checksum variants into a deterministic revision chain.

    A0 allowed more than one observation for the same semantic source record
    when the payload checksum differed.  A1 moves that distinction to an
    explicit revision/supersedes contract, so assigning every legacy row the
    temporary default revision ``1`` would collide when the new unique
    constraint is installed.  Ordering by retrieval, creation, and UUID is
    deterministic on both supported databases and preserves the most useful
    correction chronology available in the A0 schema.
    """

    partition = ", ".join(semantic_columns)
    order = "retrieved_at, created_at, id"
    ranked = f"""
        SELECT
            id,
            ROW_NUMBER() OVER (
                PARTITION BY {partition}
                ORDER BY {order}
            ) AS assigned_revision,
            LAG(id) OVER (
                PARTITION BY {partition}
                ORDER BY {order}
            ) AS assigned_supersedes_id
        FROM {table_name}
    """
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(sa.text(f"""
            WITH ranked AS ({ranked})
            UPDATE {table_name} AS observations
            SET revision = ranked.assigned_revision,
                supersedes_observation_id = ranked.assigned_supersedes_id
            FROM ranked
            WHERE observations.id = ranked.id
        """))
        return
    if dialect == "sqlite":
        op.execute(sa.text(f"""
            WITH ranked AS ({ranked})
            UPDATE {table_name}
            SET revision = (
                    SELECT assigned_revision
                    FROM ranked
                    WHERE ranked.id = {table_name}.id
                ),
                supersedes_observation_id = (
                    SELECT assigned_supersedes_id
                    FROM ranked
                    WHERE ranked.id = {table_name}.id
                )
            WHERE id IN (SELECT id FROM ranked)
        """))
        return
    raise RuntimeError(
        "Gate 3A revision migration supports only PostgreSQL and SQLite"
    )


def upgrade() -> None:
    # Add nullable first so any A0 rows can be labelled honestly before the A1
    # non-null evidence contract and derived-value checks are installed.
    with op.batch_alter_table("game_final_outcomes") as batch:
        batch.add_column(sa.Column("completion_time_basis", sa.String(48)))
        batch.add_column(sa.Column("completion_time_source_field", sa.String(255)))
        batch.add_column(sa.Column("completion_time_method", sa.Text()))
        batch.add_column(sa.Column("completion_time_limitation", sa.Text()))
        batch.add_column(sa.Column("evidence_classification", sa.String(40)))
        batch.add_column(sa.Column("quality_tier", sa.String(16)))
        batch.add_column(sa.Column("reconstructed_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("home_win", sa.Boolean()))
        batch.add_column(sa.Column("run_differential", sa.Integer()))
        batch.add_column(sa.Column("total_runs", sa.Integer()))

    outcomes = sa.table(
        "game_final_outcomes",
        sa.column("completion_time_basis", sa.String()),
        sa.column("completion_time_source_field", sa.String()),
        sa.column("completion_time_method", sa.Text()),
        sa.column("completion_time_limitation", sa.Text()),
        sa.column("evidence_classification", sa.String()),
        sa.column("quality_tier", sa.String()),
        sa.column("reconstructed_at", sa.DateTime(timezone=True)),
        sa.column("retrieved_at", sa.DateTime(timezone=True)),
        sa.column("home_win", sa.Boolean()),
        sa.column("home_score", sa.Integer()),
        sa.column("away_score", sa.Integer()),
        sa.column("run_differential", sa.Integer()),
        sa.column("total_runs", sa.Integer()),
    )
    op.execute(
        outcomes.update().values(
            completion_time_basis="AUTHORITATIVE_FINAL_AT",
            completion_time_source_field="legacy:event_completed_at",
            completion_time_method="legacy A0 explicit event_completed_at",
            completion_time_limitation=(
                "legacy row predates A1 completion-evidence metadata"
            ),
            evidence_classification="RECONSTRUCTABLE_POINT_IN_TIME",
            quality_tier="A",
            reconstructed_at=outcomes.c.retrieved_at,
            home_win=sa.case(
                (outcomes.c.home_score > outcomes.c.away_score, True),
                else_=False,
            ),
            run_differential=outcomes.c.home_score - outcomes.c.away_score,
            total_runs=outcomes.c.home_score + outcomes.c.away_score,
        )
    )

    with op.batch_alter_table("game_final_outcomes", recreate=_recreate()) as batch:
        for name, type_ in (
            ("completion_time_basis", sa.String(48)),
            ("completion_time_source_field", sa.String(255)),
            ("completion_time_method", sa.Text()),
            ("completion_time_limitation", sa.Text()),
            ("evidence_classification", sa.String(40)),
            ("quality_tier", sa.String(16)),
            ("reconstructed_at", sa.DateTime(timezone=True)),
            ("home_win", sa.Boolean()),
            ("run_differential", sa.Integer()),
            ("total_runs", sa.Integer()),
        ):
            batch.alter_column(name, existing_type=type_, nullable=False)
        batch.create_check_constraint(
            "ck_game_final_completion_basis",
            "completion_time_basis IN ('AUTHORITATIVE_FINAL_AT', "
            "'DERIVED_LAST_EVENT_COMPLETION_PROXY')",
        )
        batch.create_check_constraint(
            "ck_game_final_evidence_class",
            "evidence_classification IN ('RECONSTRUCTABLE_POINT_IN_TIME', "
            "'PARTIALLY_RECONSTRUCTABLE')",
        )
        batch.create_check_constraint(
            "ck_game_final_quality_tier", "quality_tier IN ('A', 'B')"
        )
        batch.create_check_constraint(
            "ck_game_final_class_tier",
            "(evidence_classification = 'RECONSTRUCTABLE_POINT_IN_TIME' "
            "AND quality_tier = 'A') OR "
            "(evidence_classification = 'PARTIALLY_RECONSTRUCTABLE' "
            "AND quality_tier = 'B')",
        )
        batch.create_check_constraint(
            "ck_game_final_derived_scores",
            "run_differential = home_score - away_score AND "
            "total_runs = home_score + away_score",
        )
        batch.create_check_constraint(
            "ck_game_final_no_ties", "home_score <> away_score"
        )
        batch.create_check_constraint(
            "ck_game_final_home_win",
            "(home_win = true AND home_score > away_score) OR "
            "(home_win = false AND home_score < away_score)",
        )
        batch.create_check_constraint(
            "ck_game_final_reconstruction_time", "reconstructed_at >= retrieved_at"
        )
    for table_name, prefix, semantic_columns in (
        (
            "team_game_history_observations",
            "team_game_history",
            (
                "game_id", "team_id", "source_version_id", "source_mode",
                "source_record_id",
            ),
        ),
        (
            "player_game_history_observations",
            "player_game_history",
            (
                "game_id", "player_id", "team_id", "role",
                "source_version_id", "source_mode", "source_record_id",
            ),
        ),
    ):
        with op.batch_alter_table(table_name, recreate=_recreate()) as batch:
            batch.drop_constraint(
                f"uq_{prefix}_evidence", type_="unique"
            )
            batch.add_column(sa.Column("revision", sa.Integer(), nullable=True))
            batch.add_column(sa.Column("supersedes_observation_id", sa.Uuid()))
            batch.create_foreign_key(
                f"fk_{prefix}_supersedes",
                table_name,
                ["supersedes_observation_id"],
                ["id"],
            )
            batch.create_check_constraint(
                f"ck_{prefix}_revision", "revision >= 1"
            )
            batch.create_check_constraint(
                f"ck_{prefix}_not_self_superseding",
                "supersedes_observation_id IS NULL OR supersedes_observation_id <> id",
            )

        _populate_observation_revisions(table_name, semantic_columns)

        with op.batch_alter_table(table_name, recreate=_recreate()) as batch:
            batch.alter_column(
                "revision", existing_type=sa.Integer(), nullable=False
            )
            if table_name == "team_game_history_observations":
                batch.create_unique_constraint(
                    "uq_team_game_history_revision",
                    [
                        "game_id", "team_id", "source_version_id", "source_mode",
                        "source_record_id", "revision",
                    ],
                )
            else:
                batch.create_unique_constraint(
                    "uq_player_game_history_revision",
                    [
                        "game_id", "player_id", "team_id", "role",
                        "source_version_id", "source_mode", "source_record_id", "revision",
                    ],
                )

    op.create_index(
        "ix_team_game_history_record_revision",
        "team_game_history_observations",
        ["game_id", "team_id", "source_version_id", "source_record_id", "revision"],
    )
    op.create_index(
        "ix_player_game_history_record_revision",
        "player_game_history_observations",
        [
            "game_id", "player_id", "team_id", "role", "source_version_id",
            "source_record_id", "revision",
        ],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_player_game_history_record_revision",
        table_name="player_game_history_observations",
    )
    op.drop_index(
        "ix_team_game_history_record_revision",
        table_name="team_game_history_observations",
    )

    for table_name, prefix in (
        ("player_game_history_observations", "player_game_history"),
        ("team_game_history_observations", "team_game_history"),
    ):
        with op.batch_alter_table(table_name, recreate=_recreate()) as batch:
            batch.drop_constraint(f"uq_{prefix}_revision", type_="unique")
            batch.drop_constraint(
                f"ck_{prefix}_not_self_superseding", type_="check"
            )
            batch.drop_constraint(f"ck_{prefix}_revision", type_="check")
            batch.drop_constraint(f"fk_{prefix}_supersedes", type_="foreignkey")
            batch.drop_column("supersedes_observation_id")
            batch.drop_column("revision")
            if table_name == "team_game_history_observations":
                batch.create_unique_constraint(
                    "uq_team_game_history_evidence",
                    [
                        "game_id", "team_id", "source_version_id", "source_mode",
                        "source_record_id", "payload_checksum",
                    ],
                )
            else:
                batch.create_unique_constraint(
                    "uq_player_game_history_evidence",
                    [
                        "game_id", "player_id", "team_id", "role",
                        "source_version_id", "source_mode", "source_record_id",
                        "payload_checksum",
                    ],
                )

    with op.batch_alter_table("game_final_outcomes", recreate=_recreate()) as batch:
        batch.drop_constraint("ck_game_final_reconstruction_time", type_="check")
        batch.drop_constraint("ck_game_final_home_win", type_="check")
        batch.drop_constraint("ck_game_final_no_ties", type_="check")
        batch.drop_constraint("ck_game_final_derived_scores", type_="check")
        batch.drop_constraint("ck_game_final_class_tier", type_="check")
        batch.drop_constraint("ck_game_final_quality_tier", type_="check")
        batch.drop_constraint("ck_game_final_evidence_class", type_="check")
        batch.drop_constraint("ck_game_final_completion_basis", type_="check")
        batch.drop_column("total_runs")
        batch.drop_column("run_differential")
        batch.drop_column("home_win")
        batch.drop_column("reconstructed_at")
        batch.drop_column("quality_tier")
        batch.drop_column("evidence_classification")
        batch.drop_column("completion_time_limitation")
        batch.drop_column("completion_time_method")
        batch.drop_column("completion_time_source_field")
        batch.drop_column("completion_time_basis")

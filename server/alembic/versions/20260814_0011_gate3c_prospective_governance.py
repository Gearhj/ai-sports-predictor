"""Add append-only Generation 2 prospective plans and claims.

Revision ID: 20260814_0011
Revises: 20260814_0010
Create Date: 2026-08-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260814_0011"
down_revision = "20260814_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prospective_evaluation_plans",
        sa.Column("plan_id", sa.String(length=120), nullable=False),
        sa.Column("schema_version", sa.String(length=100), nullable=False),
        sa.Column("policy_version", sa.String(length=100), nullable=False),
        sa.Column("policy_identity_checksum", sa.String(length=64), nullable=False),
        sa.Column("research_policy_version", sa.String(length=100), nullable=False),
        sa.Column("evaluation_starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_season", sa.Integer(), nullable=False),
        sa.Column("lifecycle", sa.String(length=40), nullable=False),
        sa.Column("forecast_lifecycle", sa.String(length=20), nullable=False),
        sa.Column("forecast_track", sa.String(length=40), nullable=False),
        sa.Column("forecast_family", sa.String(length=40), nullable=False),
        sa.Column("registration_status", sa.String(length=60), nullable=False),
        sa.Column("interim_completed_games", sa.Integer(), nullable=False),
        sa.Column("final_completed_games", sa.Integer(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("baselines", sa.JSON(), nullable=False),
        sa.Column("policy_manifest", sa.JSON(), nullable=False),
        sa.Column("code_revision", sa.String(length=120), nullable=False),
        sa.Column("code_checksum", sa.String(length=64), nullable=False),
        sa.Column("schema_bundle_version", sa.String(length=120), nullable=False),
        sa.Column("schema_bundle_checksum", sa.String(length=64), nullable=False),
        sa.Column("component_identities", sa.JSON(), nullable=False),
        sa.Column("plan_identity_checksum", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "schema_version = 'mlb_gen2_prospective_evaluation_plan_v1' "
            "AND policy_version = 'mlb_gen2_prospective_policy_v1' "
            "AND research_policy_version = 'MLB_GEN2_RESEARCH_POLICY_V1'",
            name="ck_prospective_plan_versions",
        ),
        sa.CheckConstraint(
            "target_season = 2026",
            name="ck_prospective_plan_season",
        ),
        sa.CheckConstraint(
            "lifecycle = 'FINAL_T_MINUS_10' AND forecast_lifecycle = 'T-10'",
            name="ck_prospective_plan_lifecycle",
        ),
        sa.CheckConstraint(
            "forecast_track = 'GEN2_RESEARCH_CHALLENGER' "
            "AND forecast_family = 'ASP_INDEPENDENT' "
            "AND registration_status = 'GEN2_PROSPECTIVE_RESEARCH_CHALLENGER'",
            name="ck_prospective_plan_research_track",
        ),
        sa.CheckConstraint(
            "interim_completed_games = 300 AND final_completed_games = 500",
            name="ck_prospective_plan_thresholds",
        ),
        sa.CheckConstraint(
            "evaluation_starts_at >= created_at",
            name="ck_prospective_plan_window",
        ),
        sa.CheckConstraint(
            "length(policy_identity_checksum) = 64 "
            "AND length(code_checksum) = 64 "
            "AND length(schema_bundle_checksum) = 64 "
            "AND length(plan_identity_checksum) = 64",
            name="ck_prospective_plan_checksums",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", name="uq_prospective_plan_id"),
        sa.UniqueConstraint(
            "plan_identity_checksum", name="uq_prospective_plan_identity"
        ),
        sa.UniqueConstraint(
            "id",
            "plan_identity_checksum",
            name="uq_prospective_plan_row_identity",
        ),
    )
    op.create_index(
        "ix_prospective_plan_window",
        "prospective_evaluation_plans",
        ["target_season", "evaluation_starts_at"],
    )
    op.create_index(
        "ix_prospective_plan_registration",
        "prospective_evaluation_plans",
        ["forecast_track", "registration_status"],
    )

    op.create_table(
        "prospective_evaluation_claims",
        sa.Column("evaluation_plan_id", sa.Uuid(), nullable=False),
        sa.Column("plan_identity_checksum", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=60), nullable=False),
        sa.Column("checkpoint", sa.String(length=60), nullable=False),
        sa.Column("minimum_completed_games", sa.Integer(), nullable=False),
        sa.Column("eligible_game_count", sa.Integer(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("eligible_set_checksum", sa.String(length=64), nullable=False),
        sa.Column("code_revision", sa.String(length=120), nullable=False),
        sa.Column("code_checksum", sa.String(length=64), nullable=False),
        sa.Column("schema_bundle_version", sa.String(length=120), nullable=False),
        sa.Column("schema_bundle_checksum", sa.String(length=64), nullable=False),
        sa.Column("global_claim_identity_checksum", sa.String(length=64), nullable=False),
        sa.Column("claim_identity_checksum", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "schema_version = 'mlb_gen2_prospective_evaluation_claim_v1' "
            "AND status = 'PROSPECTIVE_LABEL_ACCESS_AUTHORIZED'",
            name="ck_prospective_claim_version_status",
        ),
        sa.CheckConstraint(
            "checkpoint IN ('INTERIM_300_COMPLETED_ELIGIBLE_GAMES', "
            "'FINAL_500_COMPLETED_ELIGIBLE_GAMES')",
            name="ck_prospective_claim_checkpoint",
        ),
        sa.CheckConstraint(
            "(checkpoint = 'INTERIM_300_COMPLETED_ELIGIBLE_GAMES' "
            "AND minimum_completed_games = 300 AND eligible_game_count >= 300) OR "
            "(checkpoint = 'FINAL_500_COMPLETED_ELIGIBLE_GAMES' "
            "AND minimum_completed_games = 500 AND eligible_game_count >= 500)",
            name="ck_prospective_claim_threshold",
        ),
        sa.CheckConstraint(
            "length(eligible_set_checksum) = 64 "
            "AND length(plan_identity_checksum) = 64 "
            "AND length(code_checksum) = 64 "
            "AND length(schema_bundle_checksum) = 64 "
            "AND length(global_claim_identity_checksum) = 64 "
            "AND length(claim_identity_checksum) = 64",
            name="ck_prospective_claim_checksums",
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_plan_id", "plan_identity_checksum"],
            [
                "prospective_evaluation_plans.id",
                "prospective_evaluation_plans.plan_identity_checksum",
            ],
            name="fk_prospective_claim_plan_identity",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "plan_identity_checksum",
            "checkpoint",
            name="uq_prospective_claim_plan_checkpoint",
        ),
        sa.UniqueConstraint(
            "global_claim_identity_checksum",
            name="uq_prospective_claim_global_identity",
        ),
        sa.UniqueConstraint(
            "claim_identity_checksum", name="uq_prospective_claim_identity"
        ),
        sa.UniqueConstraint(
            "id",
            "evaluation_plan_id",
            "plan_identity_checksum",
            "checkpoint",
            name="uq_prospective_claim_row_plan_checkpoint",
        ),
        sa.UniqueConstraint(
            "id",
            "plan_identity_checksum",
            "global_claim_identity_checksum",
            "claim_identity_checksum",
            name="uq_prospective_claim_row_identities",
        ),
    )
    op.create_index(
        "ix_prospective_claim_plan_time",
        "prospective_evaluation_claims",
        ["evaluation_plan_id", "claimed_at"],
    )
    op.create_index(
        "ix_prospective_claim_checkpoint",
        "prospective_evaluation_claims",
        ["checkpoint", "claimed_at"],
    )

    op.create_table(
        "prospective_evaluation_game_assignments",
        sa.Column("evaluation_plan_id", sa.Uuid(), nullable=False),
        sa.Column("plan_identity_checksum", sa.String(length=64), nullable=False),
        sa.Column("game_id", sa.String(length=120), nullable=False),
        sa.Column("occurrence_id", sa.String(length=160), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assignment_identity_checksum", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(plan_identity_checksum) = 64 "
            "AND length(assignment_identity_checksum) = 64",
            name="ck_prospective_assignment_checksums",
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_plan_id", "plan_identity_checksum"],
            [
                "prospective_evaluation_plans.id",
                "prospective_evaluation_plans.plan_identity_checksum",
            ],
            name="fk_prospective_assignment_plan_identity",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "game_id",
            "occurrence_id",
            name="uq_prospective_assignment_global_occurrence",
        ),
        sa.UniqueConstraint(
            "assignment_identity_checksum",
            name="uq_prospective_assignment_identity",
        ),
        sa.UniqueConstraint(
            "id",
            "evaluation_plan_id",
            "plan_identity_checksum",
            "game_id",
            "occurrence_id",
            name="uq_prospective_assignment_row_identity",
        ),
    )
    op.create_index(
        "ix_prospective_assignment_plan",
        "prospective_evaluation_game_assignments",
        ["evaluation_plan_id", "assigned_at"],
    )

    op.create_table(
        "prospective_evaluation_claim_bindings",
        sa.Column("evaluation_claim_id", sa.Uuid(), nullable=False),
        sa.Column("evaluation_plan_id", sa.Uuid(), nullable=False),
        sa.Column("plan_identity_checksum", sa.String(length=64), nullable=False),
        sa.Column("checkpoint", sa.String(length=60), nullable=False),
        sa.Column("game_assignment_id", sa.Uuid(), nullable=False),
        sa.Column("binding_ordinal", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.String(length=120), nullable=False),
        sa.Column("occurrence_id", sa.String(length=160), nullable=False),
        sa.Column("forecast_id", sa.String(length=160), nullable=False),
        sa.Column("forecast_identity_checksum", sa.String(length=64), nullable=False),
        sa.Column("forecast_evidence", sa.JSON(), nullable=False),
        sa.Column("eligibility_policy_checksum", sa.String(length=64), nullable=False),
        sa.Column("eligibility_proof_checksum", sa.String(length=64), nullable=False),
        sa.Column("target_revision_id", sa.String(length=160), nullable=False),
        sa.Column("target_revision_checksum", sa.String(length=64), nullable=False),
        sa.Column("target_revision_sequence", sa.Integer(), nullable=False),
        sa.Column("target_available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_status", sa.String(length=24), nullable=False),
        sa.Column("schema_version", sa.String(length=100), nullable=False),
        sa.Column("binding_identity_checksum", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "checkpoint IN ('INTERIM_300_COMPLETED_ELIGIBLE_GAMES', "
            "'FINAL_500_COMPLETED_ELIGIBLE_GAMES')",
            name="ck_prospective_binding_checkpoint",
        ),
        sa.CheckConstraint(
            "binding_ordinal >= 1 AND target_revision_sequence >= 0",
            name="ck_prospective_binding_sequences",
        ),
        sa.CheckConstraint(
            "target_status = 'OFFICIAL_FINAL' "
            "AND schema_version = 'mlb_gen2_prospective_evaluation_claim_binding_v1'",
            name="ck_prospective_binding_target_schema",
        ),
        sa.CheckConstraint(
            "length(plan_identity_checksum) = 64 "
            "AND length(forecast_identity_checksum) = 64 "
            "AND length(eligibility_policy_checksum) = 64 "
            "AND length(eligibility_proof_checksum) = 64 "
            "AND length(target_revision_checksum) = 64 "
            "AND length(binding_identity_checksum) = 64",
            name="ck_prospective_binding_checksums",
        ),
        sa.ForeignKeyConstraint(
            [
                "evaluation_claim_id",
                "evaluation_plan_id",
                "plan_identity_checksum",
                "checkpoint",
            ],
            [
                "prospective_evaluation_claims.id",
                "prospective_evaluation_claims.evaluation_plan_id",
                "prospective_evaluation_claims.plan_identity_checksum",
                "prospective_evaluation_claims.checkpoint",
            ],
            name="fk_prospective_binding_claim_identity",
        ),
        sa.ForeignKeyConstraint(
            [
                "game_assignment_id",
                "evaluation_plan_id",
                "plan_identity_checksum",
                "game_id",
                "occurrence_id",
            ],
            [
                "prospective_evaluation_game_assignments.id",
                "prospective_evaluation_game_assignments.evaluation_plan_id",
                "prospective_evaluation_game_assignments.plan_identity_checksum",
                "prospective_evaluation_game_assignments.game_id",
                "prospective_evaluation_game_assignments.occurrence_id",
            ],
            name="fk_prospective_binding_assignment_identity",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "evaluation_claim_id",
            "game_assignment_id",
            name="uq_prospective_binding_claim_game",
        ),
        sa.UniqueConstraint(
            "evaluation_claim_id",
            "binding_ordinal",
            name="uq_prospective_binding_claim_ordinal",
        ),
        sa.UniqueConstraint(
            "evaluation_claim_id",
            "forecast_id",
            name="uq_prospective_binding_claim_forecast",
        ),
        sa.UniqueConstraint(
            "evaluation_claim_id",
            "binding_identity_checksum",
            name="uq_prospective_binding_claim_identity",
        ),
    )
    op.create_index(
        "ix_prospective_binding_claim",
        "prospective_evaluation_claim_bindings",
        ["evaluation_claim_id", "binding_ordinal"],
    )
    op.create_index(
        "ix_prospective_binding_game",
        "prospective_evaluation_claim_bindings",
        ["game_id", "occurrence_id"],
    )

    op.create_table(
        "prospective_evaluation_label_accesses",
        sa.Column("evaluation_claim_id", sa.Uuid(), nullable=False),
        sa.Column("plan_identity_checksum", sa.String(length=64), nullable=False),
        sa.Column("global_claim_identity_checksum", sa.String(length=64), nullable=False),
        sa.Column("claim_identity_checksum", sa.String(length=64), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("code_revision", sa.String(length=120), nullable=False),
        sa.Column("code_checksum", sa.String(length=64), nullable=False),
        sa.Column("schema_bundle_version", sa.String(length=120), nullable=False),
        sa.Column("schema_bundle_checksum", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=60), nullable=False),
        sa.Column("access_identity_checksum", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "schema_version = 'mlb_gen2_prospective_label_access_v1' "
            "AND status = 'PROSPECTIVE_LABEL_ACCESS_CONSUMED'",
            name="ck_prospective_label_access_version_status",
        ),
        sa.CheckConstraint(
            "length(plan_identity_checksum) = 64 "
            "AND length(global_claim_identity_checksum) = 64 "
            "AND length(claim_identity_checksum) = 64 "
            "AND length(code_checksum) = 64 "
            "AND length(schema_bundle_checksum) = 64 "
            "AND length(access_identity_checksum) = 64",
            name="ck_prospective_label_access_checksums",
        ),
        sa.ForeignKeyConstraint(
            [
                "evaluation_claim_id",
                "plan_identity_checksum",
                "global_claim_identity_checksum",
                "claim_identity_checksum",
            ],
            [
                "prospective_evaluation_claims.id",
                "prospective_evaluation_claims.plan_identity_checksum",
                "prospective_evaluation_claims.global_claim_identity_checksum",
                "prospective_evaluation_claims.claim_identity_checksum",
            ],
            name="fk_prospective_label_access_claim_identity",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "evaluation_claim_id", name="uq_prospective_label_access_claim"
        ),
        sa.UniqueConstraint(
            "global_claim_identity_checksum",
            name="uq_prospective_label_access_global_claim",
        ),
        sa.UniqueConstraint(
            "claim_identity_checksum", name="uq_prospective_label_access_claim_identity"
        ),
        sa.UniqueConstraint(
            "access_identity_checksum", name="uq_prospective_label_access_identity"
        ),
    )
    op.create_index(
        "ix_prospective_label_access_time",
        "prospective_evaluation_label_accesses",
        ["consumed_at"],
    )
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute(
            """
            CREATE TRIGGER trg_prospective_label_access_complete_claim
            BEFORE INSERT ON prospective_evaluation_label_accesses
            FOR EACH ROW
            BEGIN
                SELECT CASE WHEN
                    (SELECT COUNT(*) FROM prospective_evaluation_claims c
                     WHERE c.id = NEW.evaluation_claim_id
                       AND c.plan_identity_checksum = NEW.plan_identity_checksum
                       AND c.global_claim_identity_checksum = NEW.global_claim_identity_checksum
                       AND c.claim_identity_checksum = NEW.claim_identity_checksum) != 1
                    OR
                    (SELECT COUNT(*) FROM prospective_evaluation_claim_bindings b
                     WHERE b.evaluation_claim_id = NEW.evaluation_claim_id) !=
                    (SELECT c.eligible_game_count FROM prospective_evaluation_claims c
                     WHERE c.id = NEW.evaluation_claim_id)
                    OR
                    COALESCE((SELECT MIN(b.binding_ordinal)
                              FROM prospective_evaluation_claim_bindings b
                              WHERE b.evaluation_claim_id = NEW.evaluation_claim_id), 0) != 1
                    OR
                    COALESCE((SELECT MAX(b.binding_ordinal)
                              FROM prospective_evaluation_claim_bindings b
                              WHERE b.evaluation_claim_id = NEW.evaluation_claim_id), 0) !=
                    (SELECT c.eligible_game_count FROM prospective_evaluation_claims c
                     WHERE c.id = NEW.evaluation_claim_id)
                    OR EXISTS (
                        SELECT 1
                        FROM prospective_evaluation_claim_bindings b
                        JOIN prospective_evaluation_claims c
                          ON c.id = b.evaluation_claim_id
                        WHERE b.evaluation_claim_id = NEW.evaluation_claim_id
                          AND b.target_available_at > c.claimed_at
                    )
                THEN RAISE(ABORT, 'prospective claim binding set is incomplete or invalid') END;
            END
            """
        )
    elif dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION enforce_prospective_label_access_complete_claim()
            RETURNS trigger AS $$
            DECLARE
                expected_count integer;
                binding_count integer;
                minimum_ordinal integer;
                maximum_ordinal integer;
            BEGIN
                SELECT c.eligible_game_count
                  INTO expected_count
                  FROM prospective_evaluation_claims c
                 WHERE c.id = NEW.evaluation_claim_id
                   AND c.plan_identity_checksum = NEW.plan_identity_checksum
                   AND c.global_claim_identity_checksum = NEW.global_claim_identity_checksum
                   AND c.claim_identity_checksum = NEW.claim_identity_checksum;
                SELECT COUNT(*), MIN(b.binding_ordinal), MAX(b.binding_ordinal)
                  INTO binding_count, minimum_ordinal, maximum_ordinal
                  FROM prospective_evaluation_claim_bindings b
                 WHERE b.evaluation_claim_id = NEW.evaluation_claim_id;
                IF expected_count IS NULL
                   OR binding_count != expected_count
                   OR minimum_ordinal != 1
                   OR maximum_ordinal != expected_count
                   OR EXISTS (
                       SELECT 1
                         FROM prospective_evaluation_claim_bindings b
                         JOIN prospective_evaluation_claims c
                           ON c.id = b.evaluation_claim_id
                        WHERE b.evaluation_claim_id = NEW.evaluation_claim_id
                          AND b.target_available_at > c.claimed_at
                   )
                THEN
                    RAISE EXCEPTION 'prospective claim binding set is incomplete or invalid';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_prospective_label_access_complete_claim
            BEFORE INSERT ON prospective_evaluation_label_accesses
            FOR EACH ROW
            EXECUTE FUNCTION enforce_prospective_label_access_complete_claim()
            """
        )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_prospective_label_access_complete_claim")
    elif dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_prospective_label_access_complete_claim "
            "ON prospective_evaluation_label_accesses"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS enforce_prospective_label_access_complete_claim()"
        )
    op.drop_index(
        "ix_prospective_label_access_time",
        table_name="prospective_evaluation_label_accesses",
    )
    op.drop_table("prospective_evaluation_label_accesses")

    op.drop_index(
        "ix_prospective_binding_game",
        table_name="prospective_evaluation_claim_bindings",
    )
    op.drop_index(
        "ix_prospective_binding_claim",
        table_name="prospective_evaluation_claim_bindings",
    )
    op.drop_table("prospective_evaluation_claim_bindings")

    op.drop_index(
        "ix_prospective_assignment_plan",
        table_name="prospective_evaluation_game_assignments",
    )
    op.drop_table("prospective_evaluation_game_assignments")

    op.drop_index(
        "ix_prospective_claim_checkpoint", table_name="prospective_evaluation_claims"
    )
    op.drop_index(
        "ix_prospective_claim_plan_time", table_name="prospective_evaluation_claims"
    )
    op.drop_table("prospective_evaluation_claims")

    op.drop_index(
        "ix_prospective_plan_registration", table_name="prospective_evaluation_plans"
    )
    op.drop_index(
        "ix_prospective_plan_window", table_name="prospective_evaluation_plans"
    )
    op.drop_table("prospective_evaluation_plans")

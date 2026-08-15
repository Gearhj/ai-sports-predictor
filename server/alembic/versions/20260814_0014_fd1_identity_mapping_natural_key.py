"""Enforce the immutable natural key for FD1 open-data identity mappings.

Revision ID: 20260814_0014
Revises: 20260814_0013
Create Date: 2026-08-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260814_0014"
down_revision = "20260814_0013"
branch_labels = None
depends_on = None


_CONSTRAINT_NAME = "uq_open_data_identity_mapping_natural"
_NATURAL_KEY_COLUMNS = (
    "source_version_id",
    "entity_type",
    "source_namespace",
    "external_id",
    "source_row_number",
)


def upgrade() -> None:
    duplicate = op.get_bind().execute(
        sa.text(
            "SELECT source_version_id, entity_type, source_namespace, "
            "external_id, source_row_number, COUNT(*) AS row_count "
            "FROM open_data_identity_mappings "
            "GROUP BY source_version_id, entity_type, source_namespace, "
            "external_id, source_row_number "
            "HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).mappings().first()
    if duplicate is not None:
        raise RuntimeError(
            "Cannot add the FD1 identity natural-key constraint while duplicate "
            "open_data_identity_mappings exist"
        )
    with op.batch_alter_table("open_data_identity_mappings") as batch_op:
        batch_op.create_unique_constraint(
            _CONSTRAINT_NAME,
            list(_NATURAL_KEY_COLUMNS),
        )


def downgrade() -> None:
    with op.batch_alter_table("open_data_identity_mappings") as batch_op:
        batch_op.drop_constraint(_CONSTRAINT_NAME, type_="unique")

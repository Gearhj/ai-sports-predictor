"""Extend governed Gen-2 Parquet metadata to FD1 analytical families.

Revision ID: 20260814_0013
Revises: 20260814_0012
Create Date: 2026-08-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260814_0013"
down_revision = "20260814_0012"
branch_labels = None
depends_on = None


_EXTENDED_FAMILIES = (
    "'pitch', 'plate_appearance', 'play_transition', 'pitch_sequence', "
    "'player_game_batting', 'player_game_pitching', 'player_game_fielding', "
    "'starter_usage', 'bullpen_usage', 'weather', 'player_prior'"
)


def _extended_counts() -> str:
    return (
        "row_count > 0 AND game_count >= 0 "
        "AND plate_appearance_count >= 0 AND player_count >= 0 "
        "AND (event_family NOT IN ('pitch', 'plate_appearance', "
        "'play_transition', 'pitch_sequence') OR "
        "(game_count > 0 AND plate_appearance_count > 0 AND player_count > 0)) "
        "AND (event_family NOT IN ('player_game_batting', "
        "'player_game_pitching', 'player_game_fielding', 'starter_usage', "
        "'bullpen_usage') OR (game_count > 0 AND player_count > 0)) "
        "AND (event_family <> 'weather' OR game_count > 0) "
        "AND (event_family <> 'player_prior' OR player_count > 0)"
    )


def _create_fd1_tables() -> None:
    op.create_table(
        "open_data_download_manifests",
        sa.Column("source_version_id", sa.Uuid(), nullable=False),
        sa.Column("source_key", sa.String(length=80), nullable=False),
        sa.Column("object_key", sa.String(length=120), nullable=False),
        sa.Column("source_release", sa.String(length=120), nullable=False),
        sa.Column("upstream_revision", sa.Integer(), nullable=False),
        sa.Column("official_download_uri", sa.String(length=1000), nullable=False),
        sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=False),
        sa.Column("content_length", sa.BigInteger(), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=160), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("archive_sha256", sa.String(length=64), nullable=True),
        sa.Column("license_identity", sa.String(length=160), nullable=False),
        sa.Column("attribution_identity", sa.String(length=64), nullable=False),
        sa.Column("physical_attempt_count", sa.Integer(), nullable=False),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("raw_logical_uri", sa.String(length=1000), nullable=False),
        sa.Column("semantic_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("identity_checksum", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "upstream_revision >= 1 AND content_length > 0 "
            "AND physical_attempt_count >= 1 AND retry_count >= 0 "
            "AND retry_count < physical_attempt_count",
            name="ck_open_data_download_counts",
        ),
        sa.CheckConstraint(
            "http_status >= 200 AND http_status <= 299",
            name="ck_open_data_download_http_status",
        ),
        sa.CheckConstraint(
            "official_download_uri LIKE 'https://%' "
            "AND raw_logical_uri NOT LIKE '/%' "
            "AND raw_logical_uri NOT LIKE '%..%' "
            "AND replace(raw_logical_uri, '\\', '') = raw_logical_uri",
            name="ck_open_data_download_uris",
        ),
        sa.CheckConstraint(
            "length(payload_sha256) = 64 "
            "AND (archive_sha256 IS NULL OR length(archive_sha256) = 64) "
            "AND length(attribution_identity) = 64 "
            "AND length(semantic_fingerprint) = 64 "
            "AND length(identity_checksum) = 64",
            name="ck_open_data_download_checksums",
        ),
        sa.ForeignKeyConstraint(
            ["source_version_id"], ["historical_source_versions.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_version_id",
            "object_key",
            "upstream_revision",
            name="uq_open_data_download_object_revision",
        ),
        sa.UniqueConstraint(
            "identity_checksum", name="uq_open_data_download_identity"
        ),
    )
    op.create_index(
        "ix_open_data_download_source",
        "open_data_download_manifests",
        ["source_key", "source_release"],
    )
    op.create_index(
        "ix_open_data_download_time",
        "open_data_download_manifests",
        ["downloaded_at"],
    )

    op.create_table(
        "open_data_identity_mappings",
        sa.Column("source_version_id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("source_namespace", sa.String(length=80), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("canonical_namespace", sa.String(length=80), nullable=False),
        sa.Column("canonical_id", sa.String(length=255), nullable=True),
        sa.Column("source_row_number", sa.BigInteger(), nullable=False),
        sa.Column("source_row_checksum", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.String(length=24), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_through", sa.Date(), nullable=True),
        sa.Column("evidence_logical_uri", sa.String(length=1000), nullable=False),
        sa.Column("identity_checksum", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "entity_type IN ('person', 'team', 'franchise', 'park', 'game', 'umpire')",
            name="ck_open_data_identity_mapping_entity",
        ),
        sa.CheckConstraint(
            "confidence IN ('EXACT', 'HIGH', 'UNRESOLVED', 'CONFLICT')",
            name="ck_open_data_identity_mapping_confidence",
        ),
        sa.CheckConstraint(
            "source_row_number >= 1",
            name="ck_open_data_identity_mapping_row",
        ),
        sa.CheckConstraint(
            "(confidence IN ('EXACT', 'HIGH') AND canonical_id IS NOT NULL) "
            "OR (confidence IN ('UNRESOLVED', 'CONFLICT'))",
            name="ck_open_data_identity_mapping_resolution",
        ),
        sa.CheckConstraint(
            "valid_through IS NULL OR valid_from IS NULL OR valid_through >= valid_from",
            name="ck_open_data_identity_mapping_window",
        ),
        sa.CheckConstraint(
            "evidence_logical_uri NOT LIKE '/%' "
            "AND evidence_logical_uri NOT LIKE '%..%' "
            "AND replace(evidence_logical_uri, '\\', '') = evidence_logical_uri",
            name="ck_open_data_identity_mapping_uri",
        ),
        sa.CheckConstraint(
            "length(source_row_checksum) = 64 AND length(identity_checksum) = 64",
            name="ck_open_data_identity_mapping_checksums",
        ),
        sa.ForeignKeyConstraint(
            ["source_version_id"], ["historical_source_versions.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "identity_checksum", name="uq_open_data_identity_mapping_identity"
        ),
    )
    op.create_index(
        "ix_open_data_identity_external",
        "open_data_identity_mappings",
        ["source_namespace", "external_id"],
    )
    op.create_index(
        "ix_open_data_identity_canonical",
        "open_data_identity_mappings",
        ["canonical_namespace", "canonical_id"],
    )


def _drop_fd1_tables() -> None:
    op.drop_index(
        "ix_open_data_identity_canonical", table_name="open_data_identity_mappings"
    )
    op.drop_index(
        "ix_open_data_identity_external", table_name="open_data_identity_mappings"
    )
    op.drop_table("open_data_identity_mappings")
    op.drop_index(
        "ix_open_data_download_time", table_name="open_data_download_manifests"
    )
    op.drop_index(
        "ix_open_data_download_source", table_name="open_data_download_manifests"
    )
    op.drop_table("open_data_download_manifests")


def upgrade() -> None:
    with op.batch_alter_table("historical_event_partitions") as batch_op:
        batch_op.drop_constraint(
            "ck_historical_event_partition_family", type_="check"
        )
        batch_op.drop_constraint(
            "ck_historical_event_partition_counts", type_="check"
        )
        batch_op.create_check_constraint(
            "ck_historical_event_partition_family",
            f"event_family IN ({_EXTENDED_FAMILIES})",
        )
        batch_op.create_check_constraint(
            "ck_historical_event_partition_counts",
            _extended_counts(),
        )
    _create_fd1_tables()


def downgrade() -> None:
    connection = op.get_bind()
    extended_row = connection.execute(
        sa.text(
            "SELECT 1 FROM historical_event_partitions "
            "WHERE event_family NOT IN ('pitch', 'plate_appearance') LIMIT 1"
        )
    ).first()
    if extended_row is not None:
        raise RuntimeError(
            "Cannot downgrade FD1 partition families while extended-family evidence exists"
        )
    _drop_fd1_tables()
    with op.batch_alter_table("historical_event_partitions") as batch_op:
        batch_op.drop_constraint(
            "ck_historical_event_partition_counts", type_="check"
        )
        batch_op.drop_constraint(
            "ck_historical_event_partition_family", type_="check"
        )
        batch_op.create_check_constraint(
            "ck_historical_event_partition_family",
            "event_family IN ('pitch', 'plate_appearance')",
        )
        batch_op.create_check_constraint(
            "ck_historical_event_partition_counts",
            "row_count > 0 AND game_count > 0 "
            "AND plate_appearance_count > 0 AND player_count > 0",
        )

"""NBA source-confirmed finals lane, and the slate release a V2 package binds

Additive only.

* New table ``nba_final_discovery_manifests``: one verified
  ``NBA_GEN1_FINAL_DISCOVERY_V1`` manifest -- the production final path's
  discovery document, with its contract, release, capture receipt and source
  object recorded by content address.
* ``nba_final_observations`` gains the exchange-lane columns: which lane a row
  came from, the physical record it was proved from, its revision key and
  current source membership, the producer's separate capture/validation/
  admission clocks, and an explicitly always-null ``completed_at_utc`` guarded
  by a CHECK -- no clock may ever be promoted to a completion time. Its state
  observation becomes nullable so a withdrawn, corrected-away or unreconciled
  final can be retained as evidence WITHOUT projecting a final, and the ESPN
  team ids become nullable because the exchange lane identifies teams by
  canonical franchise id instead.
* ``nba_gen1_forecasts.slate_release_sha256``: the slate release a V2 package
  binds in ``forecast.bindings.slateRelease``. That release carries the
  package's OWN prospective effective clock; a newer release never re-dates a
  preserved older package.

No MLB/NFL/CFB row is touched. Downgrade drops exactly what upgrade adds.

Revision ID: 20260919_0049
Revises: 20260919_0048
Create Date: 2026-09-19
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260919_0049"
down_revision = "20260919_0048"
branch_labels = None
depends_on = None


def _uuid() -> sa.types.TypeEngine:
    return sa.Uuid(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "nba_final_discovery_manifests",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("manifest_bytes", sa.Integer, nullable=False),
        sa.Column("schema_version", sa.String(80), nullable=False),
        sa.Column("producer_created_utc", sa.DateTime(timezone=True)),
        sa.Column("predecessor_sha256", sa.String(64)),
        sa.Column("contract_sha256", sa.String(64)),
        sa.Column("release_sha256", sa.String(64)),
        sa.Column("capture_receipt_sha256", sa.String(64)),
        sa.Column("source_object_sha256", sa.String(64)),
        sa.Column("outcomes_listed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("outcomes_current", sa.Integer, nullable=False, server_default="0"),
        sa.Column("records_verified", sa.Integer, nullable=False, server_default="0"),
        sa.Column("records_refused", sa.Integer, nullable=False, server_default="0"),
        sa.Column("games_listed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("dispositions", sa.JSON),
        sa.Column("detail", sa.JSON),
        sa.Column("first_verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("manifest_sha256", name="uq_nba_final_discovery_manifest_sha"),
    )

    with op.batch_alter_table("nba_gen1_forecasts") as batch:
        batch.add_column(sa.Column("slate_release_sha256", sa.String(64)))

    # One recreate on SQLite: every new column, both relaxations and the CHECK.
    with op.batch_alter_table("nba_final_observations") as batch:
        batch.add_column(sa.Column("lane", sa.String(40), nullable=False,
                                   server_default="RETAINED_SOURCE_HISTORICAL"))
        # Named, so SQLite batch mode can recreate the table (an unnamed FK is refused there).
        batch.add_column(sa.Column("final_discovery_manifest_id", _uuid(), sa.ForeignKey(
            "nba_final_discovery_manifests.id",
            name="fk_nba_final_observations_final_discovery_manifest_id")))
        batch.add_column(sa.Column("discovery_manifest_sha256", sa.String(64)))
        batch.add_column(sa.Column("final_record_sha256", sa.String(64)))
        batch.add_column(sa.Column("final_record_bytes", sa.Integer))
        batch.add_column(sa.Column("revision_key", sa.String(80)))
        batch.add_column(sa.Column("codex_game_id", sa.String(80)))
        batch.add_column(sa.Column("currently_source_confirmed", sa.Boolean, nullable=False,
                                   server_default=sa.false()))
        batch.add_column(sa.Column("disposition", sa.String(48)))
        batch.add_column(sa.Column("source_validated_at_utc", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("admitted_at_utc", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("completed_at_utc", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("reconciliation_required", sa.Boolean, nullable=False,
                                   server_default=sa.false()))
        batch.add_column(sa.Column("reconciliation_reason", sa.Text))
        batch.alter_column("game_state_observation_id", existing_type=_uuid(), nullable=True)
        batch.alter_column("home_espn_team_id", existing_type=sa.String(16), nullable=True)
        batch.alter_column("away_espn_team_id", existing_type=sa.String(16), nullable=True)
        batch.create_check_constraint("ck_nba_final_no_completion_clock", "completed_at_utc IS NULL")
    op.create_index("ix_nba_final_observations_final_discovery_manifest_id", "nba_final_observations",
                    ["final_discovery_manifest_id"])


def downgrade() -> None:
    op.drop_index("ix_nba_final_observations_final_discovery_manifest_id",
                  table_name="nba_final_observations")
    with op.batch_alter_table("nba_final_observations") as batch:
        batch.drop_constraint("ck_nba_final_no_completion_clock", type_="check")
        batch.alter_column("away_espn_team_id", existing_type=sa.String(16), nullable=False)
        batch.alter_column("home_espn_team_id", existing_type=sa.String(16), nullable=False)
        batch.alter_column("game_state_observation_id", existing_type=_uuid(), nullable=False)
        for column in ("reconciliation_reason", "reconciliation_required", "completed_at_utc",
                       "admitted_at_utc", "source_validated_at_utc", "disposition",
                       "currently_source_confirmed", "codex_game_id", "revision_key",
                       "final_record_bytes", "final_record_sha256", "discovery_manifest_sha256",
                       "final_discovery_manifest_id", "lane"):
            batch.drop_column(column)
    with op.batch_alter_table("nba_gen1_forecasts") as batch:
        batch.drop_column("slate_release_sha256")
    op.drop_table("nba_final_discovery_manifests")

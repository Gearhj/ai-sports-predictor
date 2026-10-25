"""One durable claim per acquisition lane, kickoff cluster and phase

Additive only: one new table. No existing table, column or row is touched, and
no historical provider-ledger row is altered or back-filled.

WHY

The T-26 retime guarded its primary and retry by reading the provider ledger and
then reserving. Model Studio ran two isolated sessions against that code: both
observed the primary as unspent, both committed a RESERVED row a millisecond
apart, and the reservation count was two. The ledger's request fingerprint
includes `requested_at`, so concurrent workers mint different fingerprints for
the same kickoff cluster and phase, and the uniqueness that exists there cannot
see the collision. Scheduler `max_instances=1` narrows the window; the
administrative path (`POST /admin/jobs/{job_name}`) runs on the web event loop
outside every lane, and a timing assumption is not an invariant.

THE KEY IS EXACTLY (lane, kickoff_cluster_utc, phase)

An earlier draft keyed on (lane, provider, sport, kickoff_cluster_utc, phase).
Provider and sport are hard-coded today, so that key looks equivalent -- but it
is weaker: a future second provider or sport value would silently open a second
claim for the same lane, cluster and phase, which is the exact thing this table
exists to forbid. They remain as descriptive columns and carry no uniqueness.

That earlier revision was never deployed to any database, so it is replaced
outright rather than amended, and this is the only claim migration that has
ever existed.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260923_0052"
down_revision = "20260921_0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nfl_acquisition_phase_claims",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        # THE UNIQUENESS.
        sa.Column("lane", sa.String(64), nullable=False),
        sa.Column("kickoff_cluster_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("phase", sa.String(16), nullable=False),
        # DESCRIPTIVE ONLY -- never part of the key.
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("sport", sa.String(16), nullable=False),
        # WHAT THE CLAIM COVERS AND WHO TOOK IT.
        sa.Column("due_game_ids_sha256", sa.String(64), nullable=False),
        sa.Column("due_game_count", sa.Integer(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invocation_id", sa.String(120), nullable=False),
        sa.Column("invocation_path", sa.String(32), nullable=False),
        # Bound after the reservation exists, for reconciliation only. Never
        # part of the uniqueness: the CLAIM is exclusive, not its outcome.
        sa.Column("reservation_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint("phase IN ('PRIMARY', 'RETRY')",
                           name="ck_nfl_phase_claim_phase"),
        sa.UniqueConstraint("lane", "kickoff_cluster_utc", "phase",
                            name="uq_nfl_phase_claim"),
    )
    op.create_index("ix_nfl_phase_claim_cluster", "nfl_acquisition_phase_claims",
                    ["kickoff_cluster_utc", "phase"])


def downgrade() -> None:
    op.drop_index("ix_nfl_phase_claim_cluster", table_name="nfl_acquisition_phase_claims")
    op.drop_table("nfl_acquisition_phase_claims")

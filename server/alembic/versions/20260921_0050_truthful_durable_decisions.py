"""A decision record that tells the truth about what was decided AND executed

Additive only. Every column is nullable, so no historical row is altered and no
historical row is back-filled: a NULL here means "this row predates the fields",
which is exactly what is true of it.

WHY (defects D2, D3 and D6 of the 2026-09-21 frozen review)

D2. The stored decision recorded the PRE-staking qualification and stopped
    there. On 2026-09-20 NO@BAL, PHI@TEN and PIT@NE were persisted as
    ``QUALIFIED_BET / ALL_GATES_PASSED`` while the job receipt recorded
    ``DECLINED / STAKING_OR_EXPOSURE_CAP`` with ``bound_by: daily_cap``. The
    decision table -- which every evaluation, export and scorecard reads -- said
    AISP had recommended a bet on three games it had in fact declined for
    exposure. The truth survived only in ``job_runs.result``, which is
    operational telemetry with its own retention, not a decision record.

    So the two questions are now two columns. ``qualification_*`` is what the
    gates said before any money was sized; ``execution_*`` is what happened
    after staking. ``recommendation`` becomes the POST-staking disposition,
    because that is the field a reader already treats as "what AISP did".

D3. The checkpoint recorded which forecast it used only inside the evidence
    JSON, and ``forecast_id`` was NULL on every NFL row. Which snapshot phase
    controlled a decision -- CURRENT or FINAL_PREGAME -- could not be read at
    all. The new columns name the phase and carry the publication and import
    clocks, so "was a FINAL forecast available before this cutoff" becomes a
    question the row itself answers.

D6. The eligibility binding that authorised a decision was stripped at persist
    time (``if k not in ("eligibility",)``), so the authority behind a decision
    was unrecoverable without job telemetry. It is now a column.

Nothing here changes a probability, a threshold, a stake rule, an allocation or
a settled position. Downgrade drops exactly what upgrade adds.

Revision ID: 20260921_0050
Revises: 20260919_0049
Create Date: 2026-09-21
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260921_0050"
down_revision = "20260919_0049"
branch_labels = None
depends_on = None

TABLE = "shadow_experiment_decisions"

#: (name, type, comment). All nullable, all additive.
COLUMNS = (
    # -- D2: the two questions, kept apart ---------------------------------
    ("qualification_outcome", sa.String(length=40),
     "Pre-staking: what the qualification gates decided, before any money was "
     "sized. QUALIFIED / DECLINED / BLOCKED."),
    ("qualification_reason", sa.String(length=80),
     "Pre-staking reason, e.g. ALL_GATES_PASSED or NO_QUALIFYING_VALUE."),
    ("execution_outcome", sa.String(length=40),
     "Post-staking: EXECUTED when a position was created, NOT_EXECUTED when "
     "none was. A value-qualified game that received no position is "
     "NOT_EXECUTED here and DECLINED in `recommendation`."),
    ("execution_reason", sa.String(length=80),
     "Why execution ended as it did, e.g. POSITION_TAKEN or "
     "STAKING_OR_EXPOSURE_CAP."),
    ("staking_detail", sa.JSON(),
     "The staking arithmetic, durable rather than telemetry-only: the binding "
     "limits, bound_by, the stake, and the analytical "
     "recommended_kelly_stake / recommended_kelly_fraction / "
     "constrained_bankroll_portfolio."),

    # -- D3: which forecast actually controlled ----------------------------
    ("forecast_snapshot_phase", sa.String(length=40),
     "The snapshot phase that CONTROLLED this decision: FINAL_PREGAME or "
     "CURRENT. Previously unreadable outside the evidence JSON."),
    ("forecast_published_at", sa.DateTime(timezone=True),
     "The producer's publication clock for the controlling forecast."),
    ("forecast_imported_at", sa.DateTime(timezone=True),
     "When AISP's import of the controlling forecast COMPLETED and the row "
     "became visible. Distinct from the row's created_at, which in Postgres is "
     "the transaction-start stamp and can precede visibility by minutes."),
    ("eligibility_supplement_id", sa.String(length=200),
     "The producer eligibility supplement this decision bound."),
    ("controlling_forecast_identity", sa.String(length=200),
     "The content-addressed identity of the forecast that CONTROLLED this "
     "decision, e.g. asp-sports-game-forecast:sha256:... . The existing uuid "
     "`forecast_id` column cannot carry it: that column is a foreign key to "
     "`forecasts`, while an NFL forecast lives in `shadow_forecasts` and is "
     "already referenced by `shadow_forecast_id`. So the durable identity gets "
     "its own column rather than being forced into a key that points at the "
     "wrong table, and an evaluator can recover the exact forecast without "
     "reading evidence JSON or job telemetry."),

    # -- D6: the authority behind the decision ------------------------------
    ("eligibility_binding", sa.JSON(),
     "The complete eligibility binding that authorised this decision, no "
     "longer stripped at persist time."),
)


def upgrade() -> None:
    for name, type_, comment in COLUMNS:
        op.add_column(TABLE, sa.Column(name, type_, nullable=True, comment=comment))


def downgrade() -> None:
    for name, _type, _comment in reversed(COLUMNS):
        op.drop_column(TABLE, name)

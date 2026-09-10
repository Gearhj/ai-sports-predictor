"""The bankroll ledger's per-day rows: stable reasons, and a bounded read.

Two properties, both of which were broken in ways that do not announce
themselves.

The read pulled every decision row the experiment had ever made -- 16,360 of
them on the live database -- as whole ORM objects. Each carries a ~2 KB
``evidence`` JSONB blob that nothing on this page reads, so a request spent
several seconds decoding tens of megabytes of JSON in order to print a few
sentences of explanation. That is the sort of defect that reads as "the app is
slow" forever without ever being attributed to a cause.

The reasons list was also only accidentally stable. ``recommended_at`` is not
unique -- a slate stamps many decisions on the same instant -- and the list is
truncated to eight, so which reasons survive depended on the order Postgres
happened to return equal rows in. It was consistent between identical queries
and changed the moment the query changed.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event, select

from app.models import (
    ExperimentDecisionPhase,
    Game,
    ShadowExperiment,
    ShadowExperimentDecision,
)
from app.services.read_service import ReadService
from app.services.shadow_experiment_service import ShadowExperimentService

def _slate_and_tie(settings) -> tuple[date, datetime]:
    """Today's local slate, and one instant on it that many rows will share.

    The experiment refuses any start date but the real local one -- historical
    backfill is not permitted -- so these tests run on today rather than on a
    frozen day.
    """
    zone = ZoneInfo(settings.display_timezone)
    slate = datetime.now(zone).date()
    return slate, datetime.combine(slate, time(12, 0), tzinfo=zone).astimezone(UTC)


def _experiment(session, settings, *, start: date) -> ShadowExperiment:
    """The V1 experiment, started on a fixed day, with nothing placed."""
    service = ShadowExperimentService(session, settings)
    zone = ZoneInfo(settings.display_timezone)
    experiment = service.ensure_started(
        start_date=start,
        now=datetime.combine(start, time(12, 0), tzinfo=zone).astimezone(UTC),
        code_commit="abc123",
    )
    session.commit()
    return experiment


def _decision(session, experiment, game, *, slate_date, recommended_at, reason, evidence_bytes=0):
    """One NO_BET decision carrying its own reason and a payload of evidence."""
    session.add(ShadowExperimentDecision(
        id=uuid4(),
        experiment_id=experiment.id,
        game_id=game.id,
        slate_date=slate_date,
        phase=ExperimentDecisionPhase.FINAL_PREGAME,
        sport="MLB",
        market="MONEYLINE",
        selection=f"SEL-{reason}",
        recommended_at=recommended_at,
        recommendation="NO_BET",
        recommendation_reason=reason,
        qualification_reasons=[],
        source_mode="LIVE",
        # The column this read must not pay for.
        evidence={"filler": "x" * evidence_bytes} if evidence_bytes else {},
    ))


@pytest.mark.asyncio
async def test_no_bet_reasons_are_stable_when_timestamps_tie(session, settings) -> None:
    """The defect this pins is a silent one.

    Twelve decisions share one instant and only eight reasons are shown. If the
    order of equal rows decides which eight, then an operator reading "why did
    nothing bet today" sees a different answer after an unrelated query change,
    a new index, or a VACUUM -- with nothing in the system reporting that the
    explanation moved.
    """
    from app.jobs import seed_demo_data

    slate, tied_at = _slate_and_tie(settings)
    await seed_demo_data(session, settings, slate)
    game = session.scalars(select(Game)).first()
    experiment = _experiment(session, settings, start=slate)

    for index in range(12):
        _decision(session, experiment, game, slate_date=slate,
                  recommended_at=tied_at, reason=f"reason-{index:02d}")
    session.commit()

    reads = [ReadService(session, settings).bankroll().daily for _ in range(4)]
    reasons = [tuple(day.no_bet_reasons) for read in reads for day in read if day.date == slate.isoformat()]
    assert reasons, "the slate day must appear in the ledger"
    assert len(set(reasons)) == 1, f"reason order is not stable across reads: {set(reasons)}"

    # And it is the deterministic order the tie-break defines, not an arbitrary
    # eight: ordering by (slate_date, recommended_at, id) with equal timestamps
    # leaves id as the discriminator, so the same eight ids always win.
    ordered = list(session.scalars(
        select(ShadowExperimentDecision.recommendation_reason)
        .where(ShadowExperimentDecision.experiment_id == experiment.id)
        .order_by(ShadowExperimentDecision.slate_date,
                  ShadowExperimentDecision.recommended_at,
                  ShadowExperimentDecision.id)
    ))
    assert list(reasons[0]) == ordered[:8]


@pytest.mark.asyncio
async def test_the_daily_ledger_never_reads_the_evidence_blob(session, settings) -> None:
    """A page that prints sentences must not pay to decode every decision.

    Asserted on the emitted SQL rather than on a stopwatch, because the cost is
    proportional to history: it is invisible on a fixture with three rows and it
    is twenty seconds on a real experiment with sixteen thousand.
    """
    from app.jobs import seed_demo_data

    slate, tied_at = _slate_and_tie(settings)
    await seed_demo_data(session, settings, slate)
    game = session.scalars(select(Game)).first()
    experiment = _experiment(session, settings, start=slate)
    for index in range(5):
        _decision(session, experiment, game, slate_date=slate,
                  recommended_at=tied_at + timedelta(seconds=index),
                  reason=f"reason-{index}", evidence_bytes=2048)
    session.commit()

    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        ReadService(session, settings).bankroll()
    finally:
        event.remove(engine, "before_cursor_execute", record)

    decision_reads = [
        s for s in statements
        if "shadow_experiment_decisions" in s and s.lstrip().upper().startswith("SELECT")
    ]
    assert decision_reads, "the ledger must read decisions at all"

    # The ledger also joins decisions to positions to label the public rows, and
    # that read DOES carry the evidence column. It is bounded by the number of
    # positions the experiment has ever taken -- eleven -- so it costs nothing
    # and is not what this test is about. The unbounded read is the one that
    # scans every decision the experiment has ever made, and it grows forever.
    unbounded = [s for s in decision_reads if "official_asp_positions" not in s]
    assert unbounded, "the daily rows must still read decisions for the whole experiment"
    offenders = [s for s in unbounded if "evidence" in s.lower()]
    assert not offenders, (
        "an unbounded decision scan selected the evidence blob it never renders:\n"
        + "\n\n".join(offenders[:2])
    )

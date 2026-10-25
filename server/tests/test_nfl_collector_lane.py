"""The NFL pregame odds collector has its own lane (2026-09-26).

WHY
---
`asp-nfl-pregame-odds` and `asp-nfl-shadow-import` both ran on the single
``core`` worker. On a Sunday the shadow import fires at about 16:30:34 UTC and
runs ~160 s; if ``core`` is still busy at 16:35:00 the retimed PRIMARY interval
[T-27, T-25] = 16:33:00-16:35:00 for the 17:00 UTC cluster is missed outright,
and no retry is owed because no request was ever made.

WHAT IS PINNED HERE
-------------------
1. Exactly ONE scheduler job runs `collect-nfl-pregame-odds`; it carries the
   `asp-nc-` prefix, is alone on the `nfl-pregame-collector` lane, and its
   trigger is otherwise what it was (1-minute interval under the retime, no
   jitter, next_run_time now+2 min, max_instances=1, coalesce, 900 s grace).
   The shadow import is NOT here either: since 2026-10-17 it is
   `asp-ns-nfl-shadow-import` on its own `nfl-shadow-import` lane.
2. The lane is a separate single-worker executor, created on first use.
3. With ``core`` deliberately held by a long job, the REGISTERED collector job
   fires at its natural tick inside the primary and claims the nine-game 17:00
   cluster exactly once: one claim, one reservation, one stubbed provider call.
   A second fire queued on the same lane and an admin-path run both stay inert
   (PHASE_ALREADY_CLAIMED at the database claim when the lane is open;
   LANE_HALTED when the one billed response bound nothing).

The module keeps the T-26 tests' autouse network tripwire: any real HTTP
transport fails the test. SQLite (a file, shared across the lane thread and the
test thread) is used because the two-session PostgreSQL race test reads the
LIVE checkout's .env and creates a scratch schema in that database; the
single-owner claim race itself is pinned there, not here.
"""
from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.db.base import Base
from app.jobs import lanes
from app.jobs import scheduler as scheduler_module
from app.jobs.orchestrator import JobOrchestrator
from app.jobs.scheduler import build_scheduler, off_event_loop
from app.models import (
    Game, GameStatus, GameTeam, JobRun, League, NflAcquisitionPhaseClaim,
    ProviderRequestLedger, Season, Sport, Team,
)
from app.services import sgo_nfl_pregame as pregame

KICK = datetime(2026, 9, 27, 17, 0, tzinfo=UTC)
PRIMARY_OPENS = KICK - timedelta(minutes=27)     # 16:33:00
PRIMARY_CLOSES = KICK - timedelta(minutes=25)    # 16:35:00
CLUSTER = 9
COLLECTOR_ID = "asp-nc-nfl-pregame-odds"
COLLECTOR_JOB = "collect-nfl-pregame-odds"


@pytest.fixture(autouse=True)
def no_network():
    """Any real HTTP transport in this module fails loudly (the T-26 tripwire)."""

    import httpx

    original = httpx.Client.__init__

    def refuse(self, *args, **kwargs):
        raise AssertionError(
            "UNEXPECTED_NETWORK_CALL: a test tried to use the real HTTP "
            "transport. The provider must be stubbed once, around the whole "
            "execution, never per thread.")

    httpx.Client.__init__ = refuse
    try:
        yield
    finally:
        httpx.Client.__init__ = original


def _production_like(settings, *, retime: bool = True):
    return settings.model_copy(update={
        "scheduler_core_jobs_enabled": True,
        "nfl_late_checkpoint_retime_enabled": retime,
        "nfl_shadow_enabled": True,
        "sportsgameodds_enabled": True,
        "sportsgameodds_api_key": "not-a-real-key",  # secret-guard: test-fixture
        "sportsgameodds_base_url": "https://example.invalid",
        "sgo_exact_event_selection_mode": "OFF",
    })


def _collector_jobs(scheduler):
    return [job for job in scheduler.get_jobs()
            if (job.kwargs or {}).get("job_name") == COLLECTOR_JOB]


# --------------------------------------------------------------------------
# 1. Registration: one job, one lane, the same trigger
# --------------------------------------------------------------------------

def test_exactly_one_collector_job_on_its_own_lane_with_an_unchanged_trigger(settings):
    before = datetime.now(UTC)
    scheduler = build_scheduler(_production_like(settings))
    after = datetime.now(UTC)

    jobs = _collector_jobs(scheduler)
    assert len(jobs) == 1, [j.id for j in jobs]
    job = jobs[0]
    assert job.id == COLLECTOR_ID
    assert lanes.lane_for(job.id) == lanes.NFL_PREGAME_COLLECTOR_LANE
    assert getattr(job.func, "__aisp_job_lane__") == lanes.NFL_PREGAME_COLLECTOR_LANE
    # The old id is gone, so the job cannot be registered twice under two names.
    registered = {j.id for j in scheduler.get_jobs()}
    assert "asp-nfl-pregame-odds" not in registered

    # The trigger is what it was before the lane: only the thread changed.
    assert isinstance(job.trigger, IntervalTrigger)
    assert job.trigger.interval == timedelta(minutes=1)
    assert job.trigger.jitter is None
    assert job.max_instances == 1
    assert job.coalesce is True
    assert job.misfire_grace_time == 900
    assert before + timedelta(minutes=2) <= job.next_run_time <= after + timedelta(minutes=2)

    # Nothing else shares the lane; the shadow import has a lane of its own
    # (and is on neither core nor this one).
    by_lane: dict[str, set[str]] = {}
    for j in scheduler.get_jobs():
        lane = getattr(j.func, "__aisp_job_lane__", None)
        if lane:
            by_lane.setdefault(lane, set()).add(j.id)
    assert by_lane[lanes.NFL_PREGAME_COLLECTOR_LANE] == {COLLECTOR_ID}
    assert by_lane[lanes.NFL_SHADOW_IMPORT_LANE] == {"asp-ns-nfl-shadow-import"}
    assert "asp-nfl-shadow-import" not in {j.id for j in scheduler.get_jobs()}
    assert lanes.lane_for("asp-ns-nfl-shadow-import") == lanes.NFL_SHADOW_IMPORT_LANE


def test_the_unretimed_cadence_is_unchanged_too(settings):
    job, = _collector_jobs(build_scheduler(_production_like(settings, retime=False)))
    assert job.id == COLLECTOR_ID
    assert job.trigger.interval == timedelta(minutes=15)


def test_the_collector_lane_is_its_own_single_worker():
    pool = lanes.executor(lanes.NFL_PREGAME_COLLECTOR_LANE)
    assert pool._max_workers == 1
    assert pool is lanes.executor(lanes.NFL_PREGAME_COLLECTOR_LANE)   # one per process
    for other in (lanes.CORE_LANE, lanes.DECISION_CHECKPOINT_LANE, lanes.IMPORT_LANE,
                  lanes.NFL_FINAL_IMPORT_LANE, lanes.PREDICTION_MARKET_LANE,
                  lanes.RESEARCH_EXPORT_LANE):
        assert lanes.NFL_PREGAME_COLLECTOR_LANE != other
        assert pool is not lanes.executor(other)
    assert pool._thread_name_prefix == "aisp-job-nfl-pregame-collector"


def test_every_phase_of_the_one_minute_tick_lands_inside_the_primary(settings):
    """Whatever second the process started at, >= 2 natural ticks fall in [T-27, T-25]."""

    job, = _collector_jobs(build_scheduler(_production_like(settings)))
    for second in range(60):
        trigger = IntervalTrigger(
            minutes=int(job.trigger.interval.total_seconds() // 60),
            start_date=datetime(2026, 9, 26, 12, 0, second, tzinfo=UTC), timezone=UTC)
        fire = trigger.get_next_fire_time(None, PRIMARY_OPENS - timedelta(minutes=5))
        inside = 0
        while fire <= PRIMARY_CLOSES:
            inside += PRIMARY_OPENS <= fire <= PRIMARY_CLOSES
            fire = trigger.get_next_fire_time(fire, fire + timedelta(microseconds=1))
        assert inside >= 2, second


# --------------------------------------------------------------------------
# 2. The incident, with core held: the collector still claims exactly once
# --------------------------------------------------------------------------

@pytest.fixture()
def world(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 't26_lane.db').as_posix()}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        sport = Sport(code="FOOTBALL", name="NFL"); db.add(sport); db.flush()
        league = League(sport_id=sport.id, code="NFL", name="NFL"); db.add(league); db.flush()
        season = Season(league_id=league.id, name="2026",
                        starts_at=KICK - timedelta(days=30), ends_at=KICK + timedelta(days=90))
        db.add(season); db.flush()
        for n in range(CLUSTER):
            game = Game(league_id=league.id, season_id=season.id, starts_at=KICK,
                        status=GameStatus.SCHEDULED)
            db.add(game); db.flush()
            for is_home, abbr in ((True, f"H{n:02d}"), (False, f"A{n:02d}")):
                team = Team(league_id=league.id, name=abbr, abbreviation=abbr)
                db.add(team); db.flush()
                db.add(GameTeam(game_id=game.id, team_id=team.id, is_home=is_home))
        db.commit()
    yield engine
    engine.dispose()


class _FullGrant:
    def __init__(self, wanted: int) -> None:
        self.allowed = True
        self.granted = wanted
        self.reason = "GRANTED_FOR_LANE_TEST"

    def as_dict(self) -> dict:
        return {"allowed": True, "granted": self.granted, "reason": self.reason}


def _frozen_at(moment: datetime):
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: D401 - the collector's own clock read
            return moment if tz is not None else moment.replace(tzinfo=None)

    return _Frozen


def _counts(engine) -> dict:
    with Session(engine) as s:
        rows = s.scalars(select(ProviderRequestLedger)).all()
        return {
            "claims": s.scalar(select(func.count()).select_from(NflAcquisitionPhaseClaim)),
            "reservations": sum(1 for r in rows if str(r.status) == "RESERVED"),
            "terminal": sum(1 for r in rows if str(r.status) != "RESERVED"),
        }


#: Both terminal outcomes of the ONE primary request. A response that binds
#: none of the selection is billed and HALTS the lane (the existing rule), so
#: every later contender stops at the halt; a terminal zero-object failure
#: leaves the lane open, so a later contender reaches the atomic claim itself.
OUTCOMES = {
    "collected": {"first": "COLLECTED", "terminal_status": "SUCCEEDED",
                  "second": "LANE_HALTED", "admin": "LANE_HALTED"},
    "failed": {"first": "REQUEST_FAILED", "terminal_status": "FAILED",
               "second": "NO_NFL_GAME_IN_THE_COLLECTION_WINDOW",
               "admin": "PHASE_ALREADY_CLAIMED"},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", sorted(OUTCOMES))
async def test_a_held_core_lane_cannot_keep_the_collector_out_of_its_primary(
        settings, world, monkeypatch, outcome):
    expect = OUTCOMES[outcome]
    import app.services.provider_allocation as allocation
    from app.services.sgo_collection import FallbackFetch, ParseResult

    live = _production_like(settings)
    scheduler = build_scheduler(live)
    job, = _collector_jobs(scheduler)

    # The NATURAL tick: the registered trigger's own next fire at/after 16:33:00.
    tick = job.trigger.get_next_fire_time(None, PRIMARY_OPENS).astimezone(UTC)
    assert PRIMARY_OPENS <= tick < PRIMARY_OPENS + timedelta(minutes=1)

    # The scheduled path, on this database; ownership as on a supervised host.
    monkeypatch.setattr(scheduler_module, "SessionLocal",
                        sessionmaker(bind=world, expire_on_commit=False))
    monkeypatch.setattr(scheduler_module, "is_current_scheduler_owner", lambda: True)
    # The collector reads the clock itself; freeze only ITS clock at the tick.
    monkeypatch.setattr(pregame, "datetime", _frozen_at(tick))
    # A funded grant, as the PostgreSQL race test does, so the lane -- not the
    # allocator -- is what is under test. The claim and ledger stay real.
    monkeypatch.setattr(allocation, "request_objects",
                        lambda session, *, lane, wanted, now: _FullGrant(wanted))
    calls: list[dict] = []
    calls_lock = threading.Lock()

    def stub_fetch(settings, **kwargs):
        with calls_lock:
            calls.append({"thread": threading.current_thread().name, "limit": kwargs.get("limit")})
        if outcome == "failed":
            raise RuntimeError("stubbed terminal provider failure")
        return FallbackFetch(
            requested_at=tick, objects=kwargs.get("limit", 0), pairs=[],
            parse=ParseResult(pairs=[], rejected=[], events_seen=0, pregame_events=0),
            provider_league="NFL")

    monkeypatch.setattr(pregame, "fetch_nfl_pregame", stub_fetch)

    # HOLD CORE: a long core job occupies the single core worker, and a second
    # core job queues behind it -- the shadow import's position on a Sunday.
    release = threading.Event()
    core_holding = threading.Event()
    core_queued_ran = threading.Event()

    async def long_core_job():
        core_holding.set()
        release.wait(timeout=60)

    async def queued_core_job():
        core_queued_ran.set()

    holder = asyncio.ensure_future(off_event_loop(long_core_job, lanes.CORE_LANE)())
    queued = asyncio.ensure_future(off_event_loop(queued_core_job, lanes.CORE_LANE)())
    try:
        for _ in range(200):
            if core_holding.is_set():
                break
            await asyncio.sleep(0.01)
        assert core_holding.is_set()

        # Fire the REGISTERED job twice on its lane, as two ticks would: the
        # lane runs them one after the other, never side by side.
        await asyncio.wait_for(asyncio.gather(
            job.func(**job.kwargs), job.func(**job.kwargs)), timeout=60)

        # Core never let go while the collector did its work.
        assert not release.is_set() and not core_queued_ran.is_set()
        assert not holder.done()
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(holder, queued), timeout=30)

    # ONE provider call, on the collector's thread, for the whole cluster.
    assert len(calls) == 1, calls
    assert calls[0]["thread"].startswith("aisp-job-nfl-pregame-collector")
    assert calls[0]["limit"] == CLUSTER

    with Session(world) as s:
        claim = s.scalars(select(NflAcquisitionPhaseClaim)).one()
        assert claim.phase == pregame.PHASE_PRIMARY
        assert claim.lane == pregame.LANE
        assert claim.due_game_count == CLUSTER
        assert claim.invocation_path == "SCHEDULED"
        cluster = claim.kickoff_cluster_utc
        assert (cluster if cluster.tzinfo else cluster.replace(tzinfo=UTC)) == KICK

        runs = s.scalars(select(JobRun).where(JobRun.job_name == COLLECTOR_JOB)
                         .order_by(JobRun.started_at)).all()
        assert len(runs) == 2
        reasons = [r.result["reason"] for r in runs]
        # The first made the one request; the second, queued behind it on the
        # same single worker, was inert under the existing rules.
        assert reasons[0] == expect["first"], runs[0].result
        assert reasons[1] == expect["second"], runs[1].result
        assert runs[1].result["objects"] == 0
        assert runs[1].result["attempted"] is False
        for run in runs:
            assert run.parameters["scheduling"]["lane"] == lanes.NFL_PREGAME_COLLECTOR_LANE
        terminal = s.scalars(select(ProviderRequestLedger)
                             .where(ProviderRequestLedger.status != "RESERVED")).one()
        assert str(terminal.status) == expect["terminal_status"]
    assert _counts(world) == {"claims": 1, "reservations": 1, "terminal": 1}

    # THE ADMIN PATH (outside every lane) still meets the same guards. With the
    # due population forced open -- a contender that had already passed the due
    # check before the winner's ledger row existed -- an open lane is refused
    # by the database claim alone, and a halted lane by the halt.
    monkeypatch.setattr(pregame, "_primary_is_unspent", lambda session, *, kickoff: True)
    monkeypatch.setattr(pregame, "_retry_is_owed", lambda session, *, kickoff: True)
    with Session(world) as s:
        admin = await JobOrchestrator(s, live).run(
            COLLECTOR_JOB, requested_by_user_id=uuid.uuid4())
        assert admin.result["reason"] == expect["admin"], admin.result
        assert admin.result["objects"] == 0
        assert "scheduling" not in admin.parameters      # it ran outside every lane
    assert len(calls) == 1
    assert _counts(world) == {"claims": 1, "reservations": 1, "terminal": 1}

"""Decision checkpoints run on a lane no long job shares (2026-09-20).

THE INCIDENT
------------
At 12:30:13 ET ``generate-nfl-final-pregame-forecasts`` started and ran 799
seconds against its usual ~480. It holds the single ``core`` job thread, so
``evaluate-nfl-paper-checkpoint`` -- due at 12:29:52 -- could not run before its
window closed at 12:45. Eight games came within minutes of having no decision,
and were saved only because an operator triggered the checkpoint by hand at
12:41:32 through the admin path, which runs outside that thread.

WHAT IS PINNED HERE
-------------------
1. A long ``core`` job no longer delays a checkpoint: the two lanes run at once.
2. The lane changed WHERE a checkpoint runs, never WHEN it is eligible. The
   T-30..T-15 window is still enforced, and a run that reaches its lane after
   the cutoff still refuses rather than being backdated to when it was due.
3. Idempotency survives the new concurrency. Three fires inside one fifteen
   minute window still record one decision and one position per game, and two
   genuinely concurrent executions do not interleave between the read and the
   write.
"""
from __future__ import annotations

import asyncio
import inspect
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.jobs import lanes
from app.jobs.orchestrator import JobOrchestrator
from app.jobs.scheduler import build_scheduler, off_event_loop
from app.models import (
    Game, GameStatus, GameTeam, League, Market, MarketOutcome, MarketType,
    OddsSnapshot, OfficialAspPosition, Season, ShadowExperimentDecision,
    Sport, Sportsbook, Team,
)
from app.services import nfl_paper_decision as decision

#: A Sunday 13:00 ET kickoff, in UTC, and the two moments that matter around it.
KICKOFF = datetime(2026, 9, 20, 17, 0, tzinfo=UTC)
#: Inside T-30..T-15: the window the checkpoint may act in.
IN_WINDOW = KICKOFF - timedelta(minutes=22)
#: The activation moment the NFL era was opened at.
ACTIVATED = datetime(2026, 9, 14, 16, 50, tzinfo=UTC)


# --------------------------------------------------------------------------
# 1. Lane assignment
# --------------------------------------------------------------------------

def test_the_checkpoint_prefix_selects_the_checkpoint_lane():
    for job_id in ("asp-dc-nfl-paper-checkpoint", "asp-dc-cfb-paper-checkpoint",
                   "asp-dc-shadow-experiment-freeze"):
        assert lanes.lane_for(job_id) == lanes.DECISION_CHECKPOINT_LANE
    # And the lane is not a catch-all: everything unprefixed still runs on core.
    for job_id in ("asp-nfl-final-pregame", "asp-finalize-games",
                   "asp-model-exchange-consumer", "asp-odds-checkpoint"):
        assert lanes.lane_for(job_id) == lanes.CORE_LANE
    assert lanes.lane_for("asp-pm-discover") == lanes.PREDICTION_MARKET_LANE
    assert lanes.lane_for("asp-rx-v18-market-pilot-seal") == lanes.RESEARCH_EXPORT_LANE


def test_only_the_decision_checkpoints_are_registered_on_the_lane(settings):
    """The registration itself, not just the naming rule.

    The point of the lane is that it is EMPTY apart from the checkpoints. A
    job that quietly joins it reintroduces the incident on a smaller lane.
    """

    scheduler = build_scheduler(settings.model_copy(update={
        "scheduler_core_jobs_enabled": True, "live_shadow_enabled": True,
        "cfb_production_consumer_enabled": True, "nfl_shadow_enabled": True,
        "nfl_shadow_producer_enabled": True}))
    by_lane: dict[str, set[str]] = {}
    for job in scheduler.get_jobs():
        if inspect.iscoroutinefunction(job.func):
            by_lane.setdefault(getattr(job.func, "__aisp_job_lane__"), set()).add(job.id)

    assert by_lane[lanes.DECISION_CHECKPOINT_LANE] == {
        "asp-dc-nfl-paper-checkpoint", "asp-dc-cfb-paper-checkpoint",
        "asp-dc-shadow-experiment-freeze"}
    # `asp-nfl-shadow-producer-final` runs `generate-nfl-final-pregame-forecasts`
    # -- the 799-second job of 2026-09-20. It stays exactly where it was; what
    # changed is that the checkpoint no longer queues behind it.
    assert "asp-nfl-shadow-producer-final" in by_lane[lanes.CORE_LANE]


# --------------------------------------------------------------------------
# 2. The incident: a long core job no longer delays a checkpoint
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_long_core_job_does_not_delay_a_checkpoint():
    """12:30:13. The forecast pass runs long; the checkpoint runs anyway."""

    events: dict[str, float] = {}
    origin = time.perf_counter()

    async def long_forecast_generation():
        events["forecasts:start"] = time.perf_counter() - origin
        time.sleep(0.6)  # the 799-second run, in miniature
        events["forecasts:end"] = time.perf_counter() - origin

    async def paper_checkpoint():
        events["checkpoint:start"] = time.perf_counter() - origin

    slow = asyncio.ensure_future(
        off_event_loop(long_forecast_generation, lanes.CORE_LANE)())
    await asyncio.sleep(0.05)
    await off_event_loop(paper_checkpoint, lanes.DECISION_CHECKPOINT_LANE)()

    assert "forecasts:end" not in events, (
        "the checkpoint waited for the forecast job -- this is the 2026-09-20 incident")
    assert events["checkpoint:start"] < 0.4
    await slow


@pytest.mark.asyncio
async def test_the_checkpoints_are_still_serial_with_each_other():
    """One lane, one worker. NFL, CFB and MLB never overlap each other.

    This is load-bearing, not incidental: each owner decides whether to write by
    reading first, so they must not run at the same time as one another.
    """

    order: list[str] = []

    def make(name):
        async def job():
            order.append(f"{name}:start")
            time.sleep(0.1)
            order.append(f"{name}:end")
        return job

    lane = lanes.DECISION_CHECKPOINT_LANE
    await asyncio.gather(off_event_loop(make("nfl"), lane)(),
                         off_event_loop(make("mlb"), lane)())
    assert order == ["nfl:start", "nfl:end", "mlb:start", "mlb:end"]


def test_the_checkpoint_lane_records_how_long_it_waited(session, settings, monkeypatch):
    """Queue wait stays measured, so a future delay is visible rather than inferred."""

    async def instant(self, name, event_date):
        return {"ok": True}

    monkeypatch.setattr(JobOrchestrator, "_execute", instant)
    fired = datetime.now(UTC) - timedelta(seconds=45)
    run = lanes.run_with_meta(
        lanes.DECISION_CHECKPOINT_LANE, fired,
        lambda: asyncio.run(JobOrchestrator(session, settings).run("refresh-scoreboard")))
    scheduling = run.parameters["scheduling"]
    assert scheduling["lane"] == "decision-checkpoints"
    assert scheduling["fired_at"] == fired.isoformat()
    assert scheduling["queue_wait_seconds"] >= 45


# --------------------------------------------------------------------------
# Fixtures: one NFL game and one live era
# --------------------------------------------------------------------------

@pytest.fixture
def nfl(session: Session, settings):
    from app.services.nfl_activation import activate

    sport = Sport(code="FOOTBALL", name="Football")
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, code="NFL", name="NFL")
    session.add(league)
    session.flush()
    season = Season(league_id=league.id, name="2026",
                    starts_at=KICKOFF - timedelta(days=30),
                    ends_at=KICKOFF + timedelta(days=120))
    session.add(season)
    session.flush()
    teams = []
    for abbreviation in ("KC", "DEN"):
        team = Team(league_id=league.id, name=f"{abbreviation} team",
                    abbreviation=abbreviation)
        session.add(team)
        teams.append(team)
    session.flush()
    game = Game(league_id=league.id, season_id=season.id, starts_at=KICKOFF,
                status=GameStatus.SCHEDULED)
    session.add(game)
    session.flush()
    session.add_all([GameTeam(game_id=game.id, team_id=teams[0].id, is_home=True),
                     GameTeam(game_id=game.id, team_id=teams[1].id, is_home=False)])
    book = Sportsbook(key="sgo:book", name="Book (research)", region="us", active=True)
    session.add(book)
    session.flush()
    market = Market(game_id=game.id, market_type=MarketType.MONEYLINE, period="full_game")
    session.add(market)
    session.flush()
    outcome = MarketOutcome(market_id=market.id, outcome_key="away:None", label="away")
    session.add(outcome)
    session.flush()
    snapshot = OddsSnapshot(
        market_outcome_id=outcome.id, sportsbook_id=book.id,
        observed_at=IN_WINDOW - timedelta(minutes=20),
        available_as_of=IN_WINDOW - timedelta(minutes=20),
        american_odds=124, decimal_odds=2.24, implied_probability=0.446,
        source="SPORTSGAMEODDS", source_event_id=f"evt-{uuid.uuid4().hex[:8]}",
        source_mode="LIVE")
    session.add(snapshot)
    session.flush()
    activate(session, settings, now=ACTIVATED)
    session.commit()
    era = decision._era(session)
    assert era is not None, "the NFL paper era must be active for these tests"
    return {"session": session, "game": game, "era": era,
            "outcome": outcome, "snapshot": snapshot}


def qualifying(nfl) -> dict:
    """A decision that passed every gate, as the evaluator would hand it over."""

    return {
        "game_id": str(nfl["game"].id),
        "matchup": "DEN at KC",
        "starts_at": KICKOFF.isoformat(),
        "evaluated_at": IN_WINDOW.isoformat(),
        "outcome": "QUALIFIED",
        "reason": "ALL_GATES_PASSED",
        "selection": {
            "side": "AWAY", "american_odds": 124,
            "market_outcome_id": nfl["outcome"].id,
            "odds_snapshot_id": nfl["snapshot"].id,
            "observed_at": (IN_WINDOW - timedelta(minutes=20)).isoformat(),
            "sportsbook": "sgo:book", "source": "SPORTSGAMEODDS",
            "win_probability": 0.5753, "expected_value": 0.0518,
            "quarter_kelly": 0.0699,
        },
    }


def counts(session, era, game) -> tuple[int, int]:
    """Decisions recorded for this game's phase, and positions behind them."""

    decisions = session.scalar(select(func.count(ShadowExperimentDecision.id)).where(
        ShadowExperimentDecision.experiment_id == era.id,
        ShadowExperimentDecision.game_id == game.id,
        ShadowExperimentDecision.phase == decision.PHASE))
    positions = session.scalar(select(func.count(OfficialAspPosition.id)).where(
        OfficialAspPosition.shadow_experiment_id == era.id))
    return int(decisions), int(positions)


# --------------------------------------------------------------------------
# 3. The window is unchanged: the lane moved the thread, not the clock
# --------------------------------------------------------------------------

def test_the_checkpoint_still_refuses_outside_its_window(nfl, settings):
    session = nfl["session"]
    # Before the window opens (T-31) and after it closes (T-14). Neither is a
    # game "at its checkpoint", whichever thread the job happens to run on.
    for moment, label in ((KICKOFF - timedelta(minutes=31), "before T-30"),
                          (KICKOFF - timedelta(minutes=14), "after T-15")):
        result = decision.evaluate(session, settings, now=moment)
        assert result["reason"] == "NO_NFL_GAME_AT_ITS_CHECKPOINT", label
        assert result["records"] == 0, label
        assert result["decisions"] == [], label
    # And inside it, the same game IS due -- so the refusals above are the
    # window doing its job, not an empty fixture.
    assert decision.games_at_checkpoint(session, now=IN_WINDOW)


def test_a_run_that_reaches_its_lane_after_the_cutoff_is_not_backdated(nfl, settings):
    """The queue-delay case, which is exactly what happened on 2026-09-20.

    A job fired at 12:29:52 that only starts executing at 12:46 must refuse. It
    refuses because every owner reads the clock itself when its body runs;
    `fired_at` is recorded as evidence and is never used as the evaluation
    moment. If a lane ever passed the fire time through as `now`, a late run
    would silently write decisions against a window that had already closed.
    """

    session = nfl["session"]
    fired_at = KICKOFF - timedelta(minutes=29)          # due inside the window
    reached_lane = KICKOFF - timedelta(minutes=13)      # ran after it closed

    assert decision.games_at_checkpoint(session, now=fired_at)
    result = decision.evaluate(session, settings, now=reached_lane)
    assert result["reason"] == "NO_NFL_GAME_AT_ITS_CHECKPOINT"
    assert counts(session, nfl["era"], nfl["game"]) == (0, 0)


# --------------------------------------------------------------------------
# 4. Idempotency, repeated and concurrent
# --------------------------------------------------------------------------

def test_three_fires_in_one_window_record_one_decision_and_one_position(nfl):
    """A five-minute job fires up to three times in a fifteen-minute window.

    The guarantee is `record_outcome`'s read-then-write on (experiment, game,
    phase): the first tick writes, the later ones find the stored decision and
    reuse it. Nothing about the lane changes that -- the three checkpoints
    still share one single-worker lane, so they are as serialised as they were
    on `core`.
    """

    session, era, game = nfl["session"], nfl["era"], nfl["game"]
    receipts = []
    for tick in range(3):
        receipts.append(decision.record_outcome(
            session, experiment=era, game_id=game.id, decision=qualifying(nfl),
            moment=IN_WINDOW + timedelta(minutes=5 * tick)))
        session.commit()

    assert counts(session, era, game) == (1, 1)
    assert receipts[0]["persisted"] == "RECORDED"
    assert receipts[0]["position_id"] is not None
    for repeat in receipts[1:]:
        assert repeat["persisted"] == "REUSED_EXISTING_DECISION"
        # The repeat names the decision and position that were actually stored,
        # so a reader is never shown a re-evaluation as though it were written.
        assert repeat["decision_id"] == receipts[0]["decision_id"]
        assert repeat["position_id"] == receipts[0]["position_id"]


def test_a_concurrent_double_execution_does_not_duplicate(session, settings, monkeypatch):
    """Two executions at once: one does the work, the other declines and says so.

    The scheduled fires cannot overlap -- one lane, one worker, max_instances=1.
    The admin path can: `POST /admin/jobs/{job_name}` awaits the orchestrator on
    the web event loop, outside every lane, and that is the path an operator
    reaches for when a window is already in trouble (12:41:32 on 2026-09-20).
    Since each owner decides whether to write by reading first, two executions
    interleaving between the read and the write would write twice.
    """

    entered = threading.Event()
    release = threading.Event()
    runs = []

    def blocking_evaluate(_session, _settings):
        runs.append("evaluated")
        entered.set()
        assert release.wait(timeout=5), "the held execution was never released"
        return {"records": 1, "reason": "CHECKPOINT_EVALUATED", "decisions": []}

    monkeypatch.setattr("app.services.nfl_paper_decision.evaluate", blocking_evaluate)
    orchestrator = JobOrchestrator(session, settings)
    results: dict[str, dict] = {}

    def first():
        results["first"] = asyncio.run(
            orchestrator.evaluate_nfl_paper_checkpoint(IN_WINDOW.date()))

    holder = threading.Thread(target=first)
    holder.start()
    assert entered.wait(timeout=5), "the first execution never started"

    # The second execution arrives while the first still holds the slot.
    results["second"] = asyncio.run(
        orchestrator.evaluate_nfl_paper_checkpoint(IN_WINDOW.date()))
    release.set()
    holder.join(timeout=5)

    # Exactly one execution reached the decision owner at all.
    assert runs == ["evaluated"]
    assert results["second"]["reason"] == "CHECKPOINT_ALREADY_RUNNING"
    assert results["second"]["records"] == 0
    assert results["second"]["decisions"] == []
    assert results["first"]["reason"] == "CHECKPOINT_EVALUATED"

    # And declining is not the same as finding nothing to do. A reader must be
    # able to tell this run apart from "no game was at its checkpoint".
    assert results["second"]["reason"] != "NO_NFL_GAME_AT_ITS_CHECKPOINT"


def test_declining_never_blocks_the_caller(session, settings, monkeypatch):
    """The refusal is immediate: a second execution must not queue.

    A blocking lock taken from the web event loop would freeze HTTP for the
    length of the checkpoint -- the exact pathology the lanes exist to remove.
    """

    entered = threading.Event()
    release = threading.Event()

    def blocking_evaluate(_session, _settings):
        entered.set()
        assert release.wait(timeout=5)
        return {"records": 0, "reason": "CHECKPOINT_EVALUATED", "decisions": []}

    monkeypatch.setattr("app.services.nfl_paper_decision.evaluate", blocking_evaluate)
    orchestrator = JobOrchestrator(session, settings)
    holder = threading.Thread(
        target=lambda: asyncio.run(
            orchestrator.evaluate_nfl_paper_checkpoint(IN_WINDOW.date())))
    holder.start()
    assert entered.wait(timeout=5)

    started = time.perf_counter()
    asyncio.run(orchestrator.evaluate_nfl_paper_checkpoint(IN_WINDOW.date()))
    elapsed = time.perf_counter() - started
    release.set()
    holder.join(timeout=5)
    assert elapsed < 0.5, f"the declining execution waited {elapsed:.2f}s instead of returning"


def test_the_slot_is_released_when_a_checkpoint_fails(session, settings, monkeypatch):
    """A crashed execution must not leave the checkpoint permanently refused."""

    def exploding(_session, _settings):
        raise RuntimeError("the decision owner failed")

    monkeypatch.setattr("app.services.nfl_paper_decision.evaluate", exploding)
    orchestrator = JobOrchestrator(session, settings)
    with pytest.raises(RuntimeError):
        asyncio.run(orchestrator.evaluate_nfl_paper_checkpoint(IN_WINDOW.date()))

    monkeypatch.setattr("app.services.nfl_paper_decision.evaluate",
                        lambda _s, _t: {"records": 0, "reason": "CHECKPOINT_EVALUATED"})
    result = asyncio.run(orchestrator.evaluate_nfl_paper_checkpoint(IN_WINDOW.date()))
    assert result["reason"] == "CHECKPOINT_EVALUATED"


def test_each_checkpoint_holds_its_own_slot(session, settings, monkeypatch):
    """NFL running must not make CFB or MLB decline: they judge different games."""

    entered = threading.Event()
    release = threading.Event()

    def blocking_evaluate(_session, _settings):
        entered.set()
        assert release.wait(timeout=5)
        return {"records": 0, "reason": "CHECKPOINT_EVALUATED", "decisions": []}

    monkeypatch.setattr("app.services.nfl_paper_decision.evaluate", blocking_evaluate)
    monkeypatch.setattr("app.cfb.paper_decision.evaluate",
                        lambda _s, _t: {"records": 0, "reason": "CFB_EVALUATED"})
    orchestrator = JobOrchestrator(session, settings)
    holder = threading.Thread(
        target=lambda: asyncio.run(
            orchestrator.evaluate_nfl_paper_checkpoint(IN_WINDOW.date())))
    holder.start()
    assert entered.wait(timeout=5)
    cfb = asyncio.run(orchestrator.evaluate_cfb_paper_checkpoint(IN_WINDOW.date()))
    release.set()
    holder.join(timeout=5)
    assert cfb["reason"] == "CFB_EVALUATED"


def test_the_cfb_checkpoint_ticks_every_two_minutes_and_the_cadence_is_a_setting(settings):
    """2026-10-17: 5 -> 2 minutes (CFB_PAPER_CHECKPOINT_INTERVAL_MINUTES). Under load the producer's
    discovery leases lapsed for 10+ minutes, so a 5-minute tick reached a current lease only once or
    twice inside T-30..T-15. A BLOCKED tick is superseded by a later one (see
    test_cfb_consumer_activation.test_a_recovery_inside_the_window_adds_a_new_evaluation_at_its_own_time),
    so more ticks only add chances. Same lane, same first fire, nothing else moved."""

    from apscheduler.triggers.interval import IntervalTrigger

    on = {"scheduler_core_jobs_enabled": True, "cfb_production_consumer_enabled": True,
          "live_shadow_enabled": True, "nfl_shadow_enabled": True}
    assert settings.cfb_paper_checkpoint_interval_minutes == 2
    before = datetime.now(UTC)
    scheduler = build_scheduler(settings.model_copy(update=on))
    job = scheduler.get_job("asp-dc-cfb-paper-checkpoint")
    assert isinstance(job.trigger, IntervalTrigger) and job.trigger.interval == timedelta(minutes=2)
    assert getattr(job.func, "__aisp_job_lane__") == lanes.DECISION_CHECKPOINT_LANE
    assert (job.max_instances, job.coalesce) == (1, True)
    assert before + timedelta(minutes=2) <= job.next_run_time
    # The other checkpoints on the lane keep their 5-minute cadence.
    for other in ("asp-dc-nfl-paper-checkpoint", "asp-dc-shadow-experiment-freeze"):
        assert scheduler.get_job(other).trigger.interval == timedelta(minutes=5)
    # Reverting is an .env change, not a code rollback.
    back = build_scheduler(settings.model_copy(update={**on, "cfb_paper_checkpoint_interval_minutes": 5}))
    assert back.get_job("asp-dc-cfb-paper-checkpoint").trigger.interval == timedelta(minutes=5)

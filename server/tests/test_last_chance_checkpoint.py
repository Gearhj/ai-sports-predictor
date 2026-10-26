"""Owner decision B (2026-10-25): one guaranteed checkpoint evaluation per start cluster at T-16.

The sport checkpoints themselves are replaced by recording stubs (their own
behaviour is pinned by their own suites). Pinned here:

* due only in [T-16, T-15), per sport and start cluster, and only for a sport
  whose checkpoint is switched on (a switched-off sport is named, not hidden);
* skipped by name when every game already holds a non-BLOCKED FINAL_PREGAME
  decision; a BLOCKED one does not count as decided;
* runs the sport's own checkpoint job ONCE (its own job_runs row), never twice
  across ticks or restarts;
* a checkpoint that declines because another instance holds its slot hands the
  claim back, and the next tick inside the window runs it;
* NBA's checkpoint now holds the same slot guard as the others;
* the switch off means nothing runs; the tick is registered on the decision lane.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.jobs import lanes
from app.jobs.orchestrator import JobOrchestrator, _checkpoint_already_running
from app.models import (
    ExperimentDecisionPhase, Game, GameStatus, JobRun, League, NflAcquisitionPhaseClaim, ShadowExperimentDecision,
    Sport,
)
from app.services import last_chance_checkpoint as last_chance
from tests.postponement_fixtures import mlb_experiment

START = datetime(2026, 10, 26, 20, 25, tzinfo=UTC)
T16 = START - timedelta(minutes=16)


def _league(session, code: str) -> League:
    league = session.scalar(select(League).where(League.code == code))
    if league is None:
        sport = Sport(code=f"S-{code}", name=code)
        session.add(sport)
        session.flush()
        league = League(sport_id=sport.id, code=code, name=code)
        session.add(league)
        session.flush()
    return league


def _game(session, code: str = "NFL", start: datetime = START, *, source_mode: str = "LIVE") -> Game:
    game = Game(league_id=_league(session, code).id, starts_at=start, status=GameStatus.SCHEDULED,
                source_mode=source_mode)
    session.add(game)
    session.flush()
    return game


def _decision(session, game: Game, recommendation: str) -> None:
    n = len(list(session.scalars(select(ShadowExperimentDecision))))
    experiment = mlb_experiment(session, version=n + 7, era=f"TEST_ERA_{n}")
    session.add(ShadowExperimentDecision(
        experiment_id=experiment.id, game_id=game.id, slate_date=date(2026, 10, 26),
        phase=ExperimentDecisionPhase.FINAL_PREGAME, sport="NFL", market="MONEYLINE", period="full_game",
        selection="HOME", recommended_at=START - timedelta(minutes=25), recommendation=recommendation,
        recommendation_reason="fixture", qualification_reasons={"reason": "fixture"}))
    session.flush()


def _stub(monkeypatch, name: str, calls: list, result=None, error: Exception | None = None):
    async def fake(self, event_date):
        calls.append((name, event_date))
        if error is not None:
            raise error
        return result if result is not None else {"records": 1, "decisions": []}
    monkeypatch.setattr(JobOrchestrator, name, fake)


def _states(planned) -> list[tuple[str, str]]:
    return [(e["sport"], e["state"]) for e in planned["entries"]]


# -- the plan ------------------------------------------------------------------------------------
@pytest.mark.parametrize("minutes, due", [(17, False), (16.01, False), (16, True), (15.5, True), (15.01, True),
                                          (15, False), (14, False)])
def test_due_only_in_the_last_chance_minute(session, settings, minutes, due):
    _game(session)
    planned = last_chance.plan(session, settings, now=START - timedelta(minutes=minutes))
    assert bool(planned["due"]) is due
    if due:
        [entry] = planned["due"]
        assert (entry["sport"], entry["job_name"]) == ("NFL", "evaluate-nfl-paper-checkpoint")


def test_the_offset_is_a_setting(session, settings):
    _game(session)
    earlier = settings.model_copy(update={"last_chance_checkpoint_minutes_before_start": 18})
    assert last_chance.plan(session, earlier, now=START - timedelta(minutes=17.5))["due"]
    assert not last_chance.plan(session, settings, now=START - timedelta(minutes=17.5))["due"]


def test_a_decided_cluster_is_skipped_by_name_and_blocked_is_not_decided(session, settings):
    first, second = _game(session), _game(session)
    _decision(session, first, "DECLINED")
    assert _states(last_chance.plan(session, settings, now=T16)) == [("NFL", last_chance.DUE)]
    _decision(session, second, "BLOCKED")
    planned = last_chance.plan(session, settings, now=T16)
    assert _states(planned) == [("NFL", last_chance.DUE)]
    assert planned["due"][0]["undecided_games"] == [str(second.id)]
    _decision(session, second, "QUALIFIED_BET")
    assert _states(last_chance.plan(session, settings, now=T16)) == [("NFL", last_chance.ALREADY_DECIDED)]


def test_each_sport_runs_its_own_checkpoint_only_when_switched_on(session, settings):
    for code in ("NFL", "CFB", "NBA", "NHL", "MLB"):
        _game(session, code)
    _game(session, "MLB", START + timedelta(seconds=30), source_mode="DEMO")      # not a slate game
    off = last_chance.plan(session, settings, now=T16)
    assert sorted(_states(off)) == sorted([
        ("NFL", last_chance.DUE), ("CFB", last_chance.CHECKPOINT_DISABLED), ("NBA", last_chance.CHECKPOINT_DISABLED),
        ("NHL", last_chance.CHECKPOINT_DISABLED), ("MLB", last_chance.CHECKPOINT_DISABLED)])
    on = settings.model_copy(update={"cfb_production_consumer_enabled": True, "nba_production_consumer_enabled": True,
                                     "nhl_paper_checkpoint_enabled": True, "live_shadow_enabled": True})
    planned = last_chance.plan(session, on, now=T16)
    assert {e["sport"]: e["job_name"] for e in planned["due"]} == {
        "NFL": "evaluate-nfl-paper-checkpoint", "CFB": "evaluate-cfb-paper-checkpoint",
        "NBA": "evaluate-nba-paper-checkpoint", "NHL": "evaluate-nhl-paper-checkpoint",
        "MLB": "freeze-shadow-experiment"}


# -- the run -------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_checkpoint_runs_once_through_its_own_job(session, settings, monkeypatch):
    _game(session)
    session.commit()
    calls: list = []
    _stub(monkeypatch, "evaluate_nfl_paper_checkpoint", calls)
    result = await JobOrchestrator(session, settings).run_last_chance_checkpoints(T16.date(), now=T16)
    [run] = result["runs"]
    assert run["state"] == last_chance.RAN and result["records"] == 1
    assert calls == [("evaluate_nfl_paper_checkpoint", date(2026, 10, 26))]
    job = session.scalar(select(JobRun).where(JobRun.job_name == "evaluate-nfl-paper-checkpoint"))
    assert job is not None and str(job.id) == run["job_run_id"]
    [claim] = session.scalars(select(NflAcquisitionPhaseClaim)).all()
    assert (claim.lane, claim.kickoff_cluster_utc.replace(tzinfo=UTC)) == ("last_chance_checkpoint:NFL", START)
    # The next tick in the same minute, or after a restart: claimed, so nothing runs again.
    again = await JobOrchestrator(session, settings).run_last_chance_checkpoints(
        T16.date(), now=T16 + timedelta(seconds=30))
    assert again["runs"] == [] and len(calls) == 1
    assert [e["state"] for e in again["skipped"]] == [last_chance.ALREADY_CLAIMED]


@pytest.mark.asyncio
async def test_a_checkpoint_already_running_hands_the_claim_back(session, settings, monkeypatch):
    _game(session)
    session.commit()
    calls: list = []
    _stub(monkeypatch, "evaluate_nfl_paper_checkpoint", calls,
          result=_checkpoint_already_running("evaluate-nfl-paper-checkpoint"))
    result = await JobOrchestrator(session, settings).run_last_chance_checkpoints(T16.date(), now=T16)
    assert [r["state"] for r in result["runs"]] == [last_chance.DEFERRED_CHECKPOINT_ALREADY_RUNNING]
    assert session.scalars(select(NflAcquisitionPhaseClaim)).all() == []
    _stub(monkeypatch, "evaluate_nfl_paper_checkpoint", calls)
    later = await JobOrchestrator(session, settings).run_last_chance_checkpoints(
        T16.date(), now=T16 + timedelta(seconds=40))
    assert [r["state"] for r in later["runs"]] == [last_chance.RAN] and len(calls) == 2


@pytest.mark.asyncio
async def test_a_failed_checkpoint_is_recorded_and_not_rerun(session, settings, monkeypatch):
    _game(session)
    session.commit()
    calls: list = []
    _stub(monkeypatch, "evaluate_nfl_paper_checkpoint", calls, error=RuntimeError("fixture failure"))
    result = await JobOrchestrator(session, settings).run_last_chance_checkpoints(T16.date(), now=T16)
    assert [(r["state"], r["error_type"]) for r in result["runs"]] == [(last_chance.CHECKPOINT_FAILED, "RuntimeError")]
    again = await JobOrchestrator(session, settings).run_last_chance_checkpoints(
        T16.date(), now=T16 + timedelta(seconds=30))
    assert again["runs"] == [] and len(calls) == 1


@pytest.mark.asyncio
async def test_switched_off_runs_nothing(session, settings, monkeypatch):
    _game(session)
    session.commit()
    calls: list = []
    _stub(monkeypatch, "evaluate_nfl_paper_checkpoint", calls)
    off = settings.model_copy(update={"last_chance_checkpoint_enabled": False})
    result = await JobOrchestrator(session, off).run_last_chance_checkpoints(T16.date(), now=T16)
    assert result == {"records": 0, "status": last_chance.DISABLED} and calls == []


@pytest.mark.asyncio
async def test_the_nba_checkpoint_now_holds_its_slot(session, settings, monkeypatch):
    from app.nba import gen1_paper

    called: list = []
    monkeypatch.setattr(gen1_paper, "evaluate", lambda s, st: called.append(1) or {"records": 0})
    with lanes.decision_checkpoint_guard("evaluate-nba-paper-checkpoint") as mine:
        assert mine
        held = await JobOrchestrator(session, settings).evaluate_nba_paper_checkpoint(T16.date())
    assert held["reason"] == "CHECKPOINT_ALREADY_RUNNING" and called == []
    free = await JobOrchestrator(session, settings).evaluate_nba_paper_checkpoint(T16.date())
    assert free == {"records": 0} and called == [1]


def test_the_tick_is_registered_on_the_decision_lane_every_minute(settings):
    from apscheduler.triggers.interval import IntervalTrigger

    from app.jobs.scheduler import build_scheduler

    scheduler = build_scheduler(settings.model_copy(update={"scheduler_core_jobs_enabled": True}))
    job = scheduler.get_job("asp-dc-last-chance-checkpoint")
    assert job is not None and job.kwargs["job_name"] == "run-last-chance-checkpoints"
    assert getattr(job.func, "__aisp_job_lane__") == lanes.DECISION_CHECKPOINT_LANE
    assert isinstance(job.trigger, IntervalTrigger) and job.trigger.interval == timedelta(minutes=1)
    assert (job.max_instances, job.coalesce) == (1, True)
    off = build_scheduler(settings.model_copy(update={"scheduler_core_jobs_enabled": True,
                                                      "last_chance_checkpoint_enabled": False}))
    assert off.get_job("asp-dc-last-chance-checkpoint") is None

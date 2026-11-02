"""Research-only jobs step aside while an NFL decision is being made (owner 11-01)."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from app.jobs import scheduler as sch
from app.models.entities import Game, GameStatus, League, Sport

NOW = datetime(2026, 11, 2, 0, 0, tzinfo=UTC)


def _league(session, code):
    sport = Sport(code=f"S-{code}", name=code)
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, code=code, name=code)
    session.add(league)
    session.flush()
    return league


def test_the_window_covers_kickoffs_10_to_75_minutes_out_for_scheduled_nfl_games_only(session):
    nfl, nhl = _league(session, "NFL"), _league(session, "NHL")
    assert sch.nfl_decision_window_open(session, NOW) is False
    session.add(Game(league_id=nhl.id, starts_at=NOW + timedelta(minutes=40), status=GameStatus.SCHEDULED))
    session.add(Game(league_id=nfl.id, starts_at=NOW + timedelta(minutes=5), status=GameStatus.SCHEDULED))   # too late
    session.add(Game(league_id=nfl.id, starts_at=NOW + timedelta(minutes=90), status=GameStatus.SCHEDULED))  # too early
    session.add(Game(league_id=nfl.id, starts_at=NOW + timedelta(minutes=40), status=GameStatus.POSTPONED))
    session.flush()
    assert sch.nfl_decision_window_open(session, NOW) is False
    session.add(Game(league_id=nfl.id, starts_at=NOW + timedelta(minutes=60), status=GameStatus.SCHEDULED))
    session.flush()
    assert sch.nfl_decision_window_open(session, NOW) is True


def test_a_failed_check_never_blocks_a_job():
    class Broken:
        def scalar(self, *_a, **_k):
            raise RuntimeError("db down")
    assert sch.nfl_decision_window_open(Broken(), NOW) is False


def test_only_research_jobs_yield_and_the_rest_run(monkeypatch):
    ran: list[str] = []

    class Orchestrator:
        def __init__(self, *_a, **_k):
            pass

        async def run(self, job_name, event_date=None):
            ran.append(job_name)

    @contextmanager
    def fake_session():
        yield object()

    monkeypatch.setattr(sch, "is_current_scheduler_owner", lambda: True)
    monkeypatch.setattr(sch, "SessionLocal", fake_session)
    monkeypatch.setattr(sch, "JobOrchestrator", Orchestrator)
    monkeypatch.setattr(sch, "_scheduled_event_dates", lambda *_a: [NOW.date()])
    monkeypatch.setattr(sch, "nfl_decision_window_open", lambda _s, now=None: True)
    for job in ("import-nfl-shadow-forecast-bundle", "evaluate-nfl-research-forecasts",
                "evaluate-mlb-v18-research-forecasts", "import-nfl-final-pair",
                "consume-model-exchange-research-artifacts", "evaluate-nfl-paper-checkpoint"):
        asyncio.run(sch.execute_scheduled_job(job, settings=None))
    assert ran == ["import-nfl-final-pair", "consume-model-exchange-research-artifacts",
                   "evaluate-nfl-paper-checkpoint"]
    monkeypatch.setattr(sch, "nfl_decision_window_open", lambda _s, now=None: False)
    asyncio.run(sch.execute_scheduled_job("import-nfl-shadow-forecast-bundle", settings=None))
    assert ran[-1] == "import-nfl-shadow-forecast-bundle"

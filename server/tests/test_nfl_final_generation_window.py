"""The FINAL_PREGAME generation due window: it must open at T-60, not T-30.

Model Studio traced the 2026-09-21 near-miss to this gate. The producer's policy
is T-60 / T-21 / T-15, but the gate only invited it from T-30, so NYG @ LAR
published at 23:47:12 against a 23:54 hard boundary. These tests pin both
boundaries, and that the owner stays one owner with its existing guards.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.jobs import scheduler as sched
from app.models import ShadowForecast, ShadowForecastBundle

NOW = datetime(2026, 9, 24, 23, 15, tzinfo=UTC)


def _forecast(session: Session, *, starts_at: datetime, snapshot: str = "CURRENT",
              event_id: str = "2026_04_AWY_HOM") -> ShadowForecast:
    sha = uuid.uuid4().hex
    bundle = ShadowForecastBundle(
        bundle_sha256=sha, bundle_id=f"b-{sha[:8]}", logical_uri="test://bundle",
        source_fixture_id="f", slate_date=starts_at.date(), league="NFL", sport="NFL",
        status="IMPORTED", research_shadow_only=True, generated_at=starts_at - timedelta(days=1),
        forecast_count=1, quote_count=0, candidate_count=0,
        imported_at=starts_at - timedelta(days=1), contract_version="v1", evidence={})
    session.add(bundle)
    session.flush()
    row = ShadowForecast(
        bundle_id=bundle.id, forecast_id=f"asp-sports-game-forecast:sha256:{uuid.uuid4().hex}",
        engine_id="NFL_M1_XGBOOST", engine_version="1.0.0",
        model_artifact_sha256=uuid.uuid4().hex, simulator_id="s", simulator_version="1",
        simulator_seed=1, event_id=event_id, source_event_id=f"nflverse:{event_id}",
        event_date=starts_at.date(), season=2026, home_team_id="H", away_team_id="A",
        is_ready=True, readiness_score=99.0, readiness_threshold=50.0, status="READY",
        full_home_win_probability=0.55, full_away_win_probability=0.42,
        full_expected_home_score=21.0, full_expected_away_score=20.0, distributions={},
        uncertainty={}, readiness_detail={}, forecast_schema_version="1",
        snapshot_type=snapshot, snapshot_version=1, identity_evidence={}, lineage_detail={},
        scheduled_start_utc=starts_at, available_as_of_utc=starts_at - timedelta(days=1),
        forecast_cutoff_utc=starts_at - timedelta(days=1))
    session.add(row)
    session.flush()
    return row


@pytest.fixture
def invoked(session: Session, settings, monkeypatch) -> list[str]:
    """Record which jobs the owner would invoke, without running any of them."""

    calls: list[str] = []

    async def fake_execute(job_name, _settings):
        calls.append(job_name)

    monkeypatch.setattr(sched, "execute_scheduled_job", fake_execute)
    monkeypatch.setattr(sched, "SessionLocal", lambda: _Borrowed(session))

    class _Clock:
        @staticmethod
        def now(tz=None):
            return NOW

    monkeypatch.setattr(sched, "datetime", _Clock)
    return calls


class _Borrowed:
    """Hands the owner the test's own session and never closes it."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def __enter__(self) -> Session:
        return self.session

    def __exit__(self, *_: object) -> bool:
        return False


def _run(settings) -> None:
    asyncio.run(sched.execute_nfl_final_pregame_generation(settings))


@pytest.mark.parametrize("minutes_to_kickoff, expected", [
    (61, False),   # outside: the window has not opened
    (60, True),    # T-60 exactly: the producer's own earliest start
    (45, True),    # the whole hour is now usable
    (30, True),    # what the old gate allowed
    (16, True),
    (15, True),    # T-15 exactly: the consumer's cutoff, still invited
    (14, False),   # past the cutoff: a forecast could no longer control a decision
])
def test_the_window_opens_at_t60_and_still_closes_at_t15(
        session, settings, invoked, minutes_to_kickoff, expected):
    _forecast(session, starts_at=NOW + timedelta(minutes=minutes_to_kickoff))
    session.commit()

    _run(settings)

    assert bool(invoked) is expected


def test_a_game_already_frozen_is_never_regenerated(session, settings, invoked):
    """The unfrozen-game check is unchanged: a FINAL already exists, so nothing runs."""

    starts = NOW + timedelta(minutes=45)
    _forecast(session, starts_at=starts)
    _forecast(session, starts_at=starts, snapshot="FINAL_PREGAME")
    session.commit()

    _run(settings)

    assert invoked == []


def test_one_owner_invokes_generation_then_freeze_in_order(session, settings, invoked):
    _forecast(session, starts_at=NOW + timedelta(minutes=59))
    session.commit()

    _run(settings)

    assert invoked == ["generate-nfl-final-pregame-forecasts", "freeze-nfl-final-pregame"]


def test_an_unfrozen_game_in_the_new_hour_is_what_the_old_gate_missed(session, settings, invoked):
    """The exact 2026-09-21 shape: T-60 due, nothing frozen yet."""

    _forecast(session, starts_at=NOW + timedelta(minutes=60), event_id="2026_04_ATL_GB")
    session.commit()

    _run(settings)

    assert invoked == ["generate-nfl-final-pregame-forecasts", "freeze-nfl-final-pregame"]


def test_the_owner_adds_no_second_scheduler_or_job():
    """One owner, one scheduler id, on the core lane it already used."""

    from app.jobs import lanes

    source = Path(sched.__file__).read_text(encoding="utf-8")
    assert source.count('id="asp-nfl-final-pregame-generation"') <= 1
    assert sched.NFL_FINAL_GENERATION_OPENS == timedelta(minutes=60)
    assert sched.NFL_FINAL_GENERATION_CLOSES == timedelta(minutes=15)
    # The producer invocation is not on the decision-checkpoint lane, and the
    # consumer's own T-21/T-15 boundaries live elsewhere and are untouched.
    assert lanes.lane_for("asp-nfl-final-pregame-generation") != lanes.DECISION_CHECKPOINT_LANE

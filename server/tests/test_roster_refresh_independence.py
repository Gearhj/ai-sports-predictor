"""Team-stat failure must not skip roster or bullpen acquisition.

The retained incident: on 2026-09-14 (AISP ingestion run 99515dec) get_team_stats
raised a ConnectTimeout for the Chicago Cubs, and because the roster and bullpen
calls sat inside the same team-level try block, neither was attempted. Applied
Model Studio's exact patch (c3cfb296) plus structured per-family receipts.

These run the real refresh_mlb_data against a LIVE game with the adapters
replaced, so the orchestration is exercised end to end without a network call.
"""
from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from app.data_sources import MLBStatsAPIAdapter
from app.data_sources.base import AdapterResponse, SourceEnvelope, SourceError
from app.ingestion import CanonicalNormalizer
from app.jobs.orchestrator import JobOrchestrator

from tests.test_gate2_bullpen_ingestion import FIRST_PITCH, TARGET_DATE, _game


def _empty(source: str = "MLB_STATS_API") -> AdapterResponse:
    return AdapterResponse(
        envelope=SourceEnvelope(source=source, source_mode="LIVE",
                                fetched_at=datetime.now(UTC), data=[]),
        raw_payload={},
    )


@pytest.fixture()
def calls(monkeypatch):
    log: list[tuple[str, str]] = []

    async def team_stats(self, external_id, season):
        log.append(("TEAM_STATS", str(external_id)))
        if str(external_id) == "147":  # the away team: the retained ConnectTimeout
            raise SourceError("MLB_STATS_API", f"ConnectTimeout: {httpx.ConnectTimeout('timed out')}")
        raise SourceError("MLB_STATS_API", "team stats unavailable in this test")

    async def roster(self, external_id, event_date):
        log.append(("ACTIVE_ROSTER", str(external_id)))
        return _empty()

    async def bullpen(self, external_id, event_date):
        log.append(("BULLPEN_WORKLOAD", str(external_id)))
        return _empty()

    async def lineups(self, game_pk):
        return _empty()

    monkeypatch.setattr(MLBStatsAPIAdapter, "get_team_stats", team_stats)
    monkeypatch.setattr(MLBStatsAPIAdapter, "get_active_roster", roster)
    monkeypatch.setattr(MLBStatsAPIAdapter, "get_bullpen_workload", bullpen)
    monkeypatch.setattr(MLBStatsAPIAdapter, "get_game_lineups", lineups)
    return log


async def _run(session, settings):
    CanonicalNormalizer(session).ingest_schedule(
        [_game()], "MLB_STATS_API", available_as_of=FIRST_PITCH.replace(hour=12))
    session.commit()
    live_settings = settings.model_copy(update={"app_env": "development"})
    orchestrator = JobOrchestrator(session, live_settings)
    orchestrator.archive.write = lambda *args, **kwargs: None  # no disk writes in a test
    try:
        return await orchestrator.refresh_mlb_data(TARGET_DATE), None
    except SourceError as error:  # every team's stats failed: the job reports failure
        return None, error


@pytest.mark.asyncio
async def test_a_team_stats_timeout_still_attempts_roster_and_bullpen(session, settings, calls):
    await _run(session, settings)

    for team in ("147", "111"):
        families = [family for family, ext in calls if ext == team]
        assert families == ["TEAM_STATS", "ACTIVE_ROSTER", "BULLPEN_WORKLOAD"], team


@pytest.mark.asyncio
async def test_each_family_gets_exactly_one_attempt_and_no_retry(session, settings, calls):
    await _run(session, settings)

    for family in ("TEAM_STATS", "ACTIVE_ROSTER", "BULLPEN_WORKLOAD"):
        assert sum(1 for name, _ in calls if name == family) == 2  # two teams, one each


@pytest.mark.asyncio
async def test_failures_are_recorded_per_family_and_not_substituted(session, settings, calls):
    from sqlalchemy import select

    from app.models import IngestionRun

    await _run(session, settings)
    run = session.scalars(select(IngestionRun).order_by(IngestionRun.started_at.desc())).first()
    metadata = run.source_metadata or {}
    families = metadata.get("source_families") or {}

    assert families["TEAM_STATS"]["attempted"] == 2
    assert families["TEAM_STATS"]["failed"] == 2
    assert {f["team"] for f in families["TEAM_STATS"]["failures"]} == {"Away Team", "Home Team"}
    assert families["ACTIVE_ROSTER"] == {"attempted": 2, "completed": 2, "failed": 0, "failures": []}
    assert families["BULLPEN_WORKLOAD"] == {"attempted": 2, "completed": 2, "failed": 0, "failures": []}
    assert metadata["automatic_retries_added"] == 0
    assert metadata["stale_evidence_substituted"] is False


def test_the_patch_introduces_no_freshness_rule():
    """The governed 30-hour gate lives in the readiness evaluator, not here. The
    one-attempt-per-family proof is behavioural, in the test above."""
    import inspect

    import app.jobs.orchestrator as orchestrator

    source = inspect.getsource(orchestrator.JobOrchestrator.refresh_mlb_data)
    assert "hours=30" not in source

"""The admin job path never starts a second producer/authority job while one is RUNNING (11-01, SNF miss)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.entities import JobRun, RunStatus


def _running(session, name, started):
    run = JobRun(job_name=name, status=RunStatus.RUNNING, started_at=started, parameters={})
    session.add(run)
    session.commit()
    return run


def test_a_second_final_generation_is_refused_while_one_runs(client, session, admin_headers):
    _running(session, "generate-nfl-final-pregame-forecasts", datetime.now(UTC) - timedelta(minutes=10))
    for job in ("generate-nfl-final-pregame-forecasts",):
        refused = client.post(f"/api/v1/admin/jobs/{job}", headers=admin_headers)
        assert refused.status_code == 409
        assert "already running" in refused.json()["detail"]


def test_a_stale_running_row_older_than_two_hours_does_not_block(client, session, admin_headers, monkeypatch):
    _running(session, "freeze-nfl-final-pregame", datetime.now(UTC) - timedelta(hours=3))
    from app.jobs.orchestrator import JobOrchestrator

    async def fake_run(self, job_name, **_k):
        return JobRun(id=None, job_name=job_name, status=RunStatus.SUCCEEDED, result={})
    monkeypatch.setattr(JobOrchestrator, "run", fake_run)
    response = client.post("/api/v1/admin/jobs/freeze-nfl-final-pregame", headers=admin_headers)
    assert response.status_code != 409

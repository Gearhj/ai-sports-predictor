"""The scheduler may only run in the process the current supervisor launched.

A backend that outlives its supervisor keeps a perfectly valid host-wide owner
token, and once it has lost its port the supervisor's port check cannot see it
either. One did exactly that for two days, running a second full scheduler
against the same database and spending a bounded price allowance under
superseded code. These tests pin the gate that retires such a process.
"""

from __future__ import annotations

import pytest

from app.jobs import scheduler as sched


@pytest.fixture
def owner_file(tmp_path, monkeypatch):
    path = tmp_path / "scheduler-owner"
    monkeypatch.setattr(sched, "_owner_file", lambda: path)
    return path


def _token(monkeypatch, value: str) -> None:
    monkeypatch.setattr(sched, "_OWNER_TOKEN", value)


def test_no_owner_file_means_the_mechanism_is_not_deployed_and_jobs_run(owner_file, monkeypatch):
    """Fail-open, deliberately.

    A missing file means a developer running uvicorn by hand or a test host,
    not a superseded process. Refusing every job there would be a worse failure
    than the one being prevented.
    """

    _token(monkeypatch, "")
    assert not owner_file.exists()
    assert sched.is_current_scheduler_owner() is True


def test_an_empty_owner_file_is_treated_as_absent(owner_file, monkeypatch):
    owner_file.write_text("   \n", encoding="ascii")
    _token(monkeypatch, "")
    assert sched.is_current_scheduler_owner() is True


def test_the_launched_process_owns_the_scheduler(owner_file, monkeypatch):
    owner_file.write_text("abc123", encoding="ascii")
    _token(monkeypatch, "abc123")
    assert sched.is_current_scheduler_owner() is True


def test_trailing_whitespace_does_not_break_the_match(owner_file, monkeypatch):
    owner_file.write_text("abc123\n", encoding="ascii")
    _token(monkeypatch, "abc123")
    assert sched.is_current_scheduler_owner() is True


def test_a_survivor_of_an_earlier_launch_is_not_the_owner(owner_file, monkeypatch):
    """The exact scenario: a newer supervisor has claimed the scheduler."""

    owner_file.write_text("newer-supervisor", encoding="ascii")
    _token(monkeypatch, "older-supervisor")
    assert sched.is_current_scheduler_owner() is False


def test_a_process_with_no_token_is_not_the_owner_once_someone_has_claimed(
    owner_file, monkeypatch,
):
    """A process started before this mechanism existed must also stand down.

    It has no token at all. Once a claim exists, "I predate the mechanism" is
    not a reason to keep running a competing scheduler.
    """

    owner_file.write_text("newer-supervisor", encoding="ascii")
    _token(monkeypatch, "")
    assert sched.is_current_scheduler_owner() is False


@pytest.mark.anyio
async def test_a_superseded_process_runs_nothing_and_shuts_its_scheduler_down(
    owner_file, monkeypatch,
):
    owner_file.write_text("newer-supervisor", encoding="ascii")
    _token(monkeypatch, "older-supervisor")
    monkeypatch.setattr(sched, "_STOOD_DOWN", False)

    ran: list[str] = []

    def _explode(*args, **kwargs):
        ran.append("session")
        raise AssertionError("a superseded process must not touch the database")

    monkeypatch.setattr(sched, "SessionLocal", _explode)

    shutdowns: list[bool] = []

    class _Scheduler:
        def shutdown(self, wait: bool = True) -> None:
            shutdowns.append(wait)

    monkeypatch.setattr(sched, "_SCHEDULER_INSTANCE", _Scheduler())

    await sched.execute_scheduled_job("collect-odds-checkpoint", object())

    assert ran == []
    # Not just this one job refused -- the whole scheduler retires, so the
    # process stops competing instead of being turned away tick after tick.
    assert shutdowns == [False]
    assert sched._STOOD_DOWN is True


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"

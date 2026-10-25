"""The NFL FINAL-pair job never waits for the labs lock (2026-10-25: a pair job parked on it behind the broad
research pass delayed the next FINAL pair past the 1 PM decision window)."""

from types import SimpleNamespace

from app.forecast_exchange.nfl_labs_v1 import LABS_LOCK_KEY, register_labs_current


class _Session:
    def __init__(self, lock_free: bool):
        self.lock_free = lock_free
        self.statements: list[str] = []

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    def execute(self, statement, params=None):
        self.statements.append(str(statement))
        assert params == {"key": LABS_LOCK_KEY}
        return SimpleNamespace(scalar=lambda: self.lock_free)


def test_busy_lock_is_skipped_not_waited_for():
    session = _Session(lock_free=False)
    assert register_labs_current(session, [], wait_for_lock=False) is None
    assert session.statements == ["SELECT pg_try_advisory_xact_lock(:key)"]


def test_free_lock_proceeds_with_the_try_variant():
    session = _Session(lock_free=True)
    assert register_labs_current(session, [], wait_for_lock=False) == []
    assert session.statements == ["SELECT pg_try_advisory_xact_lock(:key)"]


def test_default_still_waits():
    session = _Session(lock_free=True)
    assert register_labs_current(session, []) == []
    assert session.statements == ["SELECT pg_advisory_xact_lock(:key)"]

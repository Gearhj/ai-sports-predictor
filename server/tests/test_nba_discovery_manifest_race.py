"""The NBA discovery manifest insert survives a writer that got there first (2026-10-26).

The exchange consumer and the NBA checkpoint's discovery refresh both call
``gen1_production._record``. Its existence check is a plain read, so when both met
a new manifest on the same tick the loser hit ``uq_nba_discovery_manifest_sha`` and
its whole checkpoint run failed. The insert now runs under a savepoint and falls
back to the winner's row.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from app.models import NbaDiscoveryManifest
from app.nba import gen1_production

SHA = "a" * 64
NOW = datetime(2026, 10, 26, 15, 0, tzinfo=UTC)


def _call(session):
    return gen1_production._record(
        session, sha=SHA, raw=b"{}", doc={"createdAtUtc": "2026-10-26T14:00:00Z", "packages": [], "games": []},
        schema="TEST", lease_semantics="TEST", verdict=SimpleNamespace(detail={}), now=NOW,
        predecessor=None, release=None)


def test_a_new_manifest_is_recorded_once(session):
    record, created = _call(session)
    assert created and record.manifest_sha256 == SHA
    again, created_again = _call(session)
    assert not created_again and again.id == record.id


def test_losing_the_race_returns_the_winners_row_instead_of_failing(session, monkeypatch):
    winner, _ = _call(session)
    session.commit()

    # The race: this writer's existence check ran before the other writer's row landed.
    real_scalar = session.scalar
    calls = {"n": 0}

    def scalar(*args, **kwargs):
        calls["n"] += 1
        return None if calls["n"] == 1 else real_scalar(*args, **kwargs)

    monkeypatch.setattr(session, "scalar", scalar)
    record, created = _call(session)
    assert not created
    assert record.id == winner.id
    # the session is still usable: the failed insert rolled back only its savepoint
    assert session.query(NbaDiscoveryManifest).filter_by(manifest_sha256=SHA).count() == 1

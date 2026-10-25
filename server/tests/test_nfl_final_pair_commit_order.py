"""The NFL FINAL pair commits before the labs family (weekend audit item 10, 2026-10-17).

`run_nfl_final_import` used to commit once, after bundles, supplements AND `nfl_labs_v1`. A labs
IntegrityError therefore rolled the FINAL forecast/eligibility pair back with it, and a slow labs
step (157 s under load on 2026-10-16; 2,136-2,649 s on 10-11 and 10-14) kept a verified pair
invisible to the decision checkpoint that long. Now the pair commits first and labs runs in its
own transaction. The stages are stubbed; each writes a stand-in ledger row so commit/rollback is
observable on the test database.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select

from app.jobs import model_exchange_consumer as mec
from app.models import ProviderRequestLedger


def _row(session, fingerprint: str) -> None:
    session.add(ProviderRequestLedger(provider="TEST_STAGE", endpoint_key="stage", requested_at=datetime.now(UTC),
                                      status="RESERVED", request_fingerprint=fingerprint))
    session.flush()


def _held(session) -> list[str]:
    session.rollback()                     # only what was COMMITTED survives this
    return sorted(session.scalars(select(ProviderRequestLedger.request_fingerprint)
                                  .where(ProviderRequestLedger.provider == "TEST_STAGE")))


def _stub(monkeypatch, *, bundles, supplements, labs):
    monkeypatch.setattr(mec, "_consume_bundles_only", lambda session, *a, **k: bundles(session))
    monkeypatch.setattr(mec, "_consume_supplements", lambda session, *a, **k: supplements(session))
    monkeypatch.setattr(mec, "_consume_nfl_labs_v1", lambda session, *a, **k: labs(session))


def _settings(settings, tmp_path):
    return settings.model_copy(update={"model_exchange_auto_import_enabled": True,
                                       "model_exchange_forecast_bundle_dir": str(tmp_path)})


def test_a_labs_collision_no_longer_rolls_back_the_committed_final_pair(session, settings, tmp_path, monkeypatch):
    _stub(monkeypatch,
          bundles=lambda s: _row(s, "a" * 64),                  # the FINAL forecast bundle
          supplements=lambda s: _row(s, "b" * 64),              # its eligibility supplement
          labs=lambda s: _row(s, "a" * 64))                     # collides (unique provider/endpoint/fp/status)
    payload = mec.run_nfl_final_import(session, _settings(settings, tmp_path))
    assert payload["reused_by_race"] is False
    assert payload["labs_reused_by_race"] is True
    assert payload["pair_committed_before_labs"] is True
    assert set(payload["stage_seconds"]) == {"bundles", "supplements", "nfl_labs_v1"}
    assert _held(session) == ["a" * 64, "b" * 64]


def test_labs_work_commits_in_its_own_transaction_after_the_pair(session, settings, tmp_path, monkeypatch):
    _stub(monkeypatch, bundles=lambda s: _row(s, "a" * 64), supplements=lambda s: _row(s, "b" * 64),
          labs=lambda s: _row(s, "c" * 64))
    payload = mec.run_nfl_final_import(session, _settings(settings, tmp_path))
    assert (payload["reused_by_race"], payload["labs_reused_by_race"]) == (False, False)
    assert _held(session) == ["a" * 64, "b" * 64, "c" * 64]


def test_a_pair_collision_still_rolls_the_pair_back_together_and_labs_still_runs(
        session, settings, tmp_path, monkeypatch):
    """The pair stays atomic: a collision in the supplement stage takes the bundle stage with it."""

    _stub(monkeypatch, bundles=lambda s: _row(s, "a" * 64), supplements=lambda s: _row(s, "a" * 64),
          labs=lambda s: _row(s, "c" * 64))
    payload = mec.run_nfl_final_import(session, _settings(settings, tmp_path))
    assert (payload["reused_by_race"], payload["labs_reused_by_race"]) == (True, False)
    assert _held(session) == ["c" * 64]
    assert session.scalar(select(func.count()).select_from(ProviderRequestLedger)) == 1


def test_a_labs_failure_that_is_not_a_collision_still_fails_the_run_but_keeps_the_pair(
        session, settings, tmp_path, monkeypatch):
    def broken(_session):
        raise RuntimeError("labs exploded")

    _stub(monkeypatch, bundles=lambda s: _row(s, "a" * 64), supplements=lambda s: _row(s, "b" * 64), labs=broken)
    try:
        mec.run_nfl_final_import(session, _settings(settings, tmp_path))
    except RuntimeError as exc:
        assert "labs exploded" in str(exc)
    else:  # pragma: no cover - the run must still report the failure
        raise AssertionError("a non-collision labs failure must still fail the run")
    assert _held(session) == ["a" * 64, "b" * 64]

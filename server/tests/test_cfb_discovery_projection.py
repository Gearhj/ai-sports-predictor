"""CFB discovery lease, projected the way the paper checkpoint reads it -- without a false gap between imports.

TEST FIXTURES ONLY (the consumer-activation suite's invented slate). The shape
being guarded is production's: the producer writes a manifest every ~15 min
whose leases run ~14.7 min; AISP's consumer imports every 15 min. Between a
lease lapsing and the next import, the newest RECORDED manifest says
EXPIRED_LEASE while the producer's pointer already holds the renewal, which the
checkpoint (it imports before deciding) would read as CURRENT.

Each test is one way the projection could mislead or cost too much:

* a verified renewal at the pointer still shown as "expired" (the bug);
* the projection writing, importing or restamping anything;
* a pointer the consumer itself would refuse (lineage, handoff, switch, age,
  a tampered entry) being believed;
* a lapse with no renewal yet hidden instead of labelled as a due renewal;
* the pointer manifest being re-read and re-hashed on every request;
* a historical ``as_of`` replay reading today's pointer.
"""
from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.cfb import discovery_projection as projection
from app.cfb import production_discovery as pd
from app.cfb import production_import
from app.models import CfbDiscoveryManifest, CfbDiscoveryObservation, CfbResearchForecast
from tests.test_cfb_consumer_activation import _consume, world  # noqa: F401 -- the shared fixture
from tests.test_cfb_production_discovery import NOW, _ref, _write_addressed

T0 = NOW                                   # the recorded manifest's creation
LAPSE = T0 + timedelta(minutes=15)         # its leases run out here
READ = LAPSE + timedelta(minutes=1)        # a readiness read one minute after the lapse


@pytest.fixture(autouse=True)
def _fresh_cache():
    projection.clear_cache()
    yield
    projection.clear_cache()


def _publish(world, entries, *, created, predecessor=None, lease_until=None, tamper=None):  # noqa: F811
    """The world's publish, with the producer's own creation clock (the shared helper pins it to NOW)."""

    release = _write_addressed(world.tmp / "releases", "release.json", b'{"release": 1}')
    inputs = _write_addressed(world.tmp / "inputs", "input.json", b'{"schedule": null}')
    entries = [dict(e) for e in entries]
    for e in entries:
        if lease_until is not None:
            e["eligibilityValidUntilUtc"] = lease_until.isoformat()
        if tamper:
            e.update(tamper)
    manifest = json.dumps({"schemaVersion": pd.MANIFEST_SCHEMA, "createdAtUtc": created.isoformat(),
                           "release": _ref(release), "inputVersion": _ref(inputs), "predecessor": predecessor,
                           "packages": entries,
                           "games": [{"gameId": e["gameId"], "disposition": "PUBLISHED_NEW_PROSPECTIVE"}
                                     for e in entries]}).encode()
    path = _write_addressed(world.tmp / "versions", "index.json", manifest)
    (world.tmp / "current.json").write_text(json.dumps({"schemaVersion": pd.POINTER_SCHEMA, "manifest": _ref(path)}))
    return _ref(path)


def _recorded(world):  # noqa: F811
    """One imported manifest whose leases lapse at LAPSE; returns its ref and the 401 forecast."""

    ref = _publish(world, world.entries, created=T0, lease_until=LAPSE)
    world.anchor["sha"] = ref["sha256"]
    assert _consume(world, now=T0 + timedelta(minutes=2))["status"] == "MANIFEST_RECORDED"
    forecast = production_import.current_forecast_for_game(world.session, "401")
    assert production_import.present_eligibility(world.session, forecast, now=READ)["state"] == "EXPIRED_LEASE"
    return ref, forecast


def _counts(session):
    return (session.scalar(select(func.count()).select_from(CfbDiscoveryManifest)),
            session.scalar(select(func.count()).select_from(CfbDiscoveryObservation)),
            session.scalar(select(func.count()).select_from(CfbResearchForecast)))


def test_a_verified_renewal_at_the_pointer_reads_current_and_nothing_is_written(world):  # noqa: F811
    ref, forecast = _recorded(world)
    renewal = _publish(world, world.entries, created=LAPSE - timedelta(seconds=30), predecessor=ref,
                       lease_until=LAPSE + timedelta(minutes=15))
    before = _counts(world.session)
    reading = projection.projected_eligibility(world.session, world.settings, forecast, now=READ)
    assert not world.session.new and not world.session.dirty and _counts(world.session) == before
    assert (reading["state"], reading["eligible"], reading["basis"]) == ("CURRENT", True, projection.POINTER)
    assert reading["manifest_sha256"] == renewal["sha256"]
    assert reading["recorded"] == {**reading["recorded"], "state": "EXPIRED_LEASE", "manifest_sha256": ref["sha256"]}
    assert reading["renewal"]["state"] == "RENEWED_AT_POINTER" and reading["renewal"]["lapsed_minutes"] == 1.0
    assert "renews every 15 min" in reading["label"]

    # The checkpoint's own path (import, then the recorded reading) lands exactly where the projection said.
    assert _consume(world, now=READ)["status"] == "MANIFEST_RECORDED"
    after = production_import.present_eligibility(world.session, forecast, now=READ)
    assert (after["state"], after["manifest_sha256"]) == ("CURRENT", renewal["sha256"])


def test_a_lapse_without_a_renewal_stays_expired_and_is_labelled_as_due(world):  # noqa: F811
    _ref_, forecast = _recorded(world)
    due = projection.projected_eligibility(world.session, world.settings, forecast, now=READ)
    assert (due["state"], due["eligible"], due["basis"], due["pointer"]) == (
        "EXPIRED_LEASE", False, projection.RECORDED, "NO_NEWER_MANIFEST")
    assert due["renewal"] == {"state": "RENEWAL_DUE", "lapsed_minutes": 1.0, "cadence_minutes": 15.0}
    assert due["label"] == "renewing (lapsed 1 min ago; the producer renews every 15 min)"
    # A lapse older than one producer cadence is a real expiry, said plainly.
    stale = projection.projected_eligibility(world.session, world.settings, forecast,
                                             now=LAPSE + timedelta(minutes=25))
    assert stale["state"] == "EXPIRED_LEASE" and "renewal" not in stale and stale["label"] == "expired lease"


@pytest.mark.parametrize("case", ["lineage", "handoff", "switch_off", "older", "tampered", "renewal_lapsed"])
def test_a_pointer_the_consumer_would_refuse_never_upgrades_the_lease(world, monkeypatch, case):  # noqa: F811
    ref, forecast = _recorded(world)
    lease = LAPSE + timedelta(minutes=15)
    created = LAPSE - timedelta(seconds=30)
    if case == "lineage":
        # A renewal two links ahead of anything recorded: the consumer walks it; the projection does not.
        middle = _publish(world, world.entries, created=created - timedelta(minutes=1), predecessor=ref,
                          lease_until=lease)
        _publish(world, world.entries, created=created, predecessor=middle, lease_until=lease)
    elif case == "older":
        _publish(world, world.entries, created=T0 - timedelta(minutes=15), predecessor=ref, lease_until=lease)
    elif case == "tampered":
        _publish(world, world.entries, created=created, predecessor=ref, lease_until=lease,
                 tamper={"packageAddress": "f" * 64})
    elif case == "renewal_lapsed":
        _publish(world, world.entries, created=created, predecessor=ref, lease_until=READ - timedelta(seconds=1))
    else:
        _publish(world, world.entries, created=created, predecessor=ref, lease_until=lease)
    if case == "handoff":
        monkeypatch.setattr(pd, "production_status", lambda _s: pd.HandoffVerdict(pd.STAGED, "TEST"))
    if case == "switch_off":
        world.settings.cfb_production_consumer_enabled = False
    reading = projection.projected_eligibility(world.session, world.settings, forecast, now=READ)
    assert reading["eligible"] is False
    expected = {"lineage": ("EXPIRED_LEASE", projection.RECORDED, "POINTER_LINEAGE_NOT_YET_RECORDED"),
                "handoff": ("EXPIRED_LEASE", projection.RECORDED, "HANDOFF_NOT_ACTIVE"),
                "switch_off": ("EXPIRED_LEASE", projection.RECORDED, "CONSUMER_OFF"),
                "older": ("EXPIRED_LEASE", projection.RECORDED, "POINTER_NOT_NEWER"),
                "tampered": ("REJECTED", projection.POINTER, None),
                "renewal_lapsed": ("EXPIRED_LEASE", projection.POINTER, None)}[case]
    assert (reading["state"], reading["basis"], reading.get("pointer")) == expected
    if case == "tampered":
        assert "PACKAGE_ADDRESS_CHANGED_UNDER_KNOWN_FORECAST" in reading["problems"]


def test_the_pointer_manifest_is_verified_once_per_sha_and_each_entry_once(world, monkeypatch):  # noqa: F811
    ref, forecast = _recorded(world)
    _publish(world, world.entries, created=LAPSE - timedelta(seconds=30), predecessor=ref,
             lease_until=LAPSE + timedelta(minutes=15))
    loads, verifies = [], []
    real_load, real_verify = projection._load_manifest, projection._light_verify
    monkeypatch.setattr(projection, "_load_manifest", lambda p: loads.append(p) or real_load(p))
    monkeypatch.setattr(projection, "_light_verify", lambda *a, **k: verifies.append(a) or real_verify(*a, **k))
    for minute in range(5):
        reading = projection.projected_eligibility(world.session, world.settings, forecast,
                                                   now=READ + timedelta(seconds=minute * 20))
        assert reading["state"] == "CURRENT"
    assert len(loads) == 1 and len(verifies) == 1


def test_slate_reads_project_live_but_a_replay_keeps_what_was_recorded(world):  # noqa: F811
    from app.services import command_center

    ref, forecast = _recorded(world)
    _publish(world, world.entries, created=LAPSE - timedelta(seconds=30), predecessor=ref,
             lease_until=LAPSE + timedelta(minutes=15))
    models = {str(row.canonical_game_id): {"forecast_id": row.forecast_id}
              for row in world.session.scalars(select(CfbResearchForecast))}
    live = command_center._cfb_eligibility(world.session, models, now=READ, settings=world.settings)
    assert {r["now"]["state"] for r in live.values()} == {"CURRENT"}
    assert {r["now"]["basis"] for r in live.values()} == {projection.POINTER}
    replay = command_center._cfb_eligibility(world.session, models, now=READ, as_of=READ, settings=world.settings)
    assert {r["now"]["state"] for r in replay.values()} == {"EXPIRED_LEASE"}
    assert all("basis" not in r["now"] for r in replay.values())


def test_the_cfb_readiness_card_shows_no_false_expiry_between_imports(world, settings, monkeypatch):  # noqa: F811
    import app.core.config as config
    from app.services import sport_readiness

    ref, _forecast = _recorded(world)
    s = settings.model_copy(update={"cfb_discovery_pointer_path": world.settings.cfb_discovery_pointer_path,
                                    "cfb_independent_schedule_path": world.settings.cfb_independent_schedule_path,
                                    "cfb_production_consumer_enabled": True, "cfb_paper_decisions_enabled": True})
    monkeypatch.setattr(config, "get_settings", lambda: s)

    gap = sport_readiness._cfb(world.session, READ)
    assert gap["eligibility"]["discovery_lease"] == "EXPIRED_LEASE"
    assert "discovery lease renewing (lapsed 1 min ago" in gap["pipeline"]["blocker"].lower()

    _publish(world, world.entries, created=LAPSE - timedelta(seconds=30), predecessor=ref,
             lease_until=LAPSE + timedelta(minutes=15))
    projection.clear_cache()
    card = sport_readiness._cfb(world.session, READ)
    assert card["eligibility"]["discovery_lease"] == "CURRENT"
    assert card["eligibility"]["discovery"]["basis"] == projection.POINTER
    assert card["eligibility"]["discovery"]["recorded"]["state"] == "EXPIRED_LEASE"
    assert "renews every 15 min" in card["forecast"]["label"]
    assert "discovery lease" not in (card["pipeline"]["blocker"] or "").lower()
    assert world.session.scalar(select(func.count()).select_from(CfbDiscoveryManifest)) == 1   # nothing imported


def test_the_checkpoint_still_imports_and_reads_the_recorded_manifest_itself():
    """paper_decision is untouched: it never calls the projection; it imports, then reads what it recorded."""

    import inspect

    from app.cfb import paper_decision

    source = inspect.getsource(paper_decision)
    assert "discovery_projection" not in source and "projected_eligibility" not in source
    assert "consume_discovery(session, settings, now=moment)" in source
    assert "lease = present_eligibility(session, forecast, now=moment)" in source

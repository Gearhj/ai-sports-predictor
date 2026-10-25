"""The staged pieces a bounded CFB SportsGameOdds allocation would need. Spends nothing.

* a per-day bound on the lane (CFB_SGO_LANE_DAILY_OBJECT_CAP), unset by default;
* the CFB fetch keeps its exact response bytes, like the MLB/NFL/Show Preview
  paths since b56ed64.

The lane itself stays OFF (CFB_SGO_LANE_ENABLED=false) and the capacity gate
still grants only genuinely uncommitted objects under both guards.
"""
from __future__ import annotations

import gzip
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_sgo_raw_archive import FINGERPRINT, KEY, NOW, _body, provider, sgo_settings  # noqa: F401

KICK = datetime(2026, 9, 19, 23, 0, tzinfo=UTC)


def _open_guards(monkeypatch, *, need: int):
    from app.services import provider_allocation as pa
    from app.services import sgo_collection

    budget = SimpleNamespace(spendable=211, blocked_reason=None, reserve=300, objects_used=1989)
    monkeypatch.setattr(sgo_collection, "object_budget", lambda *a, **k: budget)
    monkeypatch.setattr(pa, "build_plan", lambda *a, **k: SimpleNamespace(as_dict=lambda: {
        "spendable": 211, "lanes": {}, "reserve_never_spent": 300, "used_this_month": 1989,
        "monthly_cap": 2500}))
    monkeypatch.setattr(pa, "daily_snapshot", lambda *a, **k: SimpleNamespace(
        demand={"shaping": {"month_projected_need": need}}))


def _settings(**extra):
    base = dict(cfb_sgo_lane_object_cap=10, cfb_sgo_lane_enabled=True, cfb_sgo_protected_headroom=0,
                cfb_sgo_lane_authorized_until="2026-10-01T00:00:00+00:00")
    base.update(extra)
    return SimpleNamespace(**base)


def test_the_live_position_grants_nothing(session, monkeypatch):
    """09-19 12:36 ET: 211 spendable under both guards, 207 committed to MLB/NFL."""

    from app.cfb import sgo_lane

    _open_guards(monkeypatch, need=207)
    gate = sgo_lane.capacity(session, _settings(cfb_sgo_protected_headroom=10), now=KICK)
    assert gate["uncommitted"] == 0 and gate["objects_allowed"] == 0
    assert gate["state"] == sgo_lane.NO_CAPACITY


def test_the_daily_cap_is_unset_by_default_and_changes_nothing(session, monkeypatch):
    from app.cfb import sgo_lane
    from app.core.config import Settings

    assert Settings.model_fields["cfb_sgo_lane_daily_object_cap"].default is None
    assert Settings.model_fields["cfb_sgo_lane_enabled"].default is False
    _open_guards(monkeypatch, need=150)
    gate = sgo_lane.capacity(session, _settings(), now=KICK)          # no daily cap attribute
    assert gate["objects_allowed"] == 10 and gate["daily_cap"] is None


def test_the_daily_cap_only_lowers_what_the_gate_allows(session, monkeypatch):
    from app.cfb import sgo_lane

    _open_guards(monkeypatch, need=150)                                # 61 uncommitted
    assert sgo_lane.capacity(session, _settings(cfb_sgo_lane_daily_object_cap=3),
                             now=KICK)["objects_allowed"] == 3
    monkeypatch.setattr(sgo_lane, "_lane_spent_today", lambda *_a: 3)
    capped = sgo_lane.capacity(session, _settings(cfb_sgo_lane_daily_object_cap=3), now=KICK)
    assert capped["objects_allowed"] == 0 and capped["state"] == sgo_lane.DAILY_CAP_REACHED
    # Commitments still bind first: a daily cap never opens a closed gate.
    _open_guards(monkeypatch, need=209)
    closed = sgo_lane.capacity(session, _settings(cfb_sgo_lane_daily_object_cap=5,
                                                  cfb_sgo_protected_headroom=10), now=KICK)
    assert closed["objects_allowed"] == 0 and closed["state"] == sgo_lane.NO_CAPACITY


def test_the_cfb_fetch_keeps_its_exact_bytes_in_one_request(sgo_settings, provider):  # noqa: F811
    from app.cfb import sgo_price
    from app.services import sgo_raw_archive

    provider["body"] = _body("NCAAF")
    params = {"leagueID": "NCAAF", "limit": "1", "startsAfter": "2026-09-19T22:55:00Z",
              "startsBefore": "2026-09-19T23:05:00Z", "finalized": "false", "oddsPresent": "true"}
    payload, archive = sgo_price.fetch_events_archived(sgo_settings, params, requested_at=NOW,
                                                       request_fingerprint=FINGERPRINT)
    assert len(provider["requests"]) == 1
    assert payload["data"][0]["leagueID"] == "NCAAF"
    assert archive["status"] == sgo_raw_archive.ARCHIVED
    assert archive["responseBodySha256"] == hashlib.sha256(provider["body"]).hexdigest()
    body = gzip.decompress(Path(archive["responseBodyPath"]).read_bytes())
    assert body == provider["body"] and KEY.encode() not in body

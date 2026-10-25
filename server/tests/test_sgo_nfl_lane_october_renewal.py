"""The NFL pregame lane's bounded October renewal, and nothing wider.

What these pin:
* the NFL pregame lane (sgo_nfl_events) is eligible through 2026-10-31 UTC and
  refused from 2026-11-01T00:00Z;
* every other SportsGameOdds path (MLB fallback, NFL live scores, Show Preview)
  keeps `sgo_collection.eligible`, which still closes after 2026-09-30, and the
  governed source set never regains SportsGameOdds;
* the October spend is capped at NFL_LANE_RENEWAL_OBJECT_CAP on top of the
  unchanged allocation and reserve guard, and the cap can only lower a grant;
* September behaviour is byte-for-byte the old reason code.

No test here reaches the network: the real HTTP transport raises.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.data_sources import provider_registry as registry
from app.models import NflAcquisitionPhaseClaim, ProviderRequestLedger
from app.services import sgo_collection
from app.services import sgo_nfl_pregame as lane

EASTERN = ZoneInfo("America/New_York")
OCT1_T26 = datetime(2026, 10, 1, 23, 49, tzinfo=UTC)       # PIT-CLE primary claim
PIT_CLE_KICKOFF = datetime(2026, 10, 2, 0, 15, tzinfo=UTC)


def cfg(**over):
    base = dict(sportsgameodds_enabled=True, sportsgameodds_api_key="test-key-not-real",  # secret-guard: test-fixture
                sportsgameodds_base_url="https://sgo.invalid/v2",
                nfl_late_checkpoint_retime_enabled=True,
                show_preview_freeze_time="16:30", show_preview_timezone="America/New_York")
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import httpx

    def refuse(self, *args, **kwargs):
        raise AssertionError("UNEXPECTED_NETWORK_CALL")

    monkeypatch.setattr(httpx.Client, "__init__", refuse)


# --------------------------------------------------------------------------
# The dated windows
# --------------------------------------------------------------------------

def test_the_constants_are_the_reviewed_ones():
    assert registry.GOVERNED_FALLBACK_UNTIL == date(2026, 9, 30)      # unchanged
    assert registry.NFL_LANE_ELIGIBLE_UNTIL == date(2026, 10, 31)
    assert registry.NFL_LANE_RENEWAL_BILLING_MONTH == "2026-10"
    assert registry.NFL_LANE_RENEWAL_OBJECT_CAP == 150
    assert registry.NFL_LANE_RENEWAL_ENDPOINT == lane.LANE == "sgo_nfl_events"


@pytest.mark.parametrize("moment, nfl_open, general_open", [
    (datetime(2026, 9, 30, 23, 59, 59, tzinfo=UTC), True, True),
    (datetime(2026, 10, 1, 0, 0, tzinfo=UTC), True, False),
    (OCT1_T26, True, False),
    (datetime(2026, 10, 31, 23, 59, 59, tzinfo=UTC), True, False),
    (datetime(2026, 11, 1, 0, 0, tzinfo=UTC), False, False),
])
def test_the_nfl_window_is_october_only_and_the_general_one_stays_closed(
        moment, nfl_open, general_open):
    assert registry.nfl_lane_window_open(moment) is nfl_open
    assert registry.fallback_window_open(moment) is general_open


def test_an_eastern_instant_is_judged_on_its_utc_day():
    # 2026-10-31 21:00 ET is 2026-11-01 01:00Z: closed, although its ET date is Oct 31.
    assert registry.nfl_lane_window_open(datetime(2026, 10, 31, 21, 0, tzinfo=EASTERN)) is False
    # 2026-09-30 21:00 ET is 2026-10-01 01:00Z: the renewal, not the old window.
    assert registry.nfl_lane_renewal_in_force(datetime(2026, 9, 30, 21, 0, tzinfo=EASTERN)) is True


def test_governed_sources_never_regain_sportsgameodds_in_october():
    assert "SPORTSGAMEODDS" not in registry.governed_sources(now=OCT1_T26, fallback_enabled=True)


# --------------------------------------------------------------------------
# Eligibility: the NFL lane only
# --------------------------------------------------------------------------

def test_the_general_eligibility_still_refuses_every_other_lane_in_october():
    ok, reason = sgo_collection.eligible(cfg(), now=OCT1_T26)
    assert ok is False
    assert reason == "FALLBACK_WINDOW_CLOSED_AFTER_2026-09-30"


def test_the_nfl_lane_is_eligible_in_october_by_its_renewal():
    assert lane.eligible(cfg(), now=OCT1_T26) == (True, "NFL_LANE_RENEWAL_ELIGIBLE")
    assert sgo_collection.nfl_lane_eligible(cfg(), now=OCT1_T26) == (
        True, "NFL_LANE_RENEWAL_ELIGIBLE")


def test_september_keeps_the_old_reason_code():
    moment = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
    assert lane.eligible(cfg(), now=moment) == sgo_collection.eligible(cfg(), now=moment) == (
        True, "FALLBACK_ELIGIBLE")


def test_the_nfl_lane_closes_after_october():
    ok, reason = lane.eligible(cfg(), now=datetime(2026, 11, 1, 0, 0, tzinfo=UTC))
    assert ok is False
    assert reason == "NFL_LANE_WINDOW_CLOSED_AFTER_2026-10-31"


@pytest.mark.parametrize("over, reason", [
    ({"sportsgameodds_enabled": False}, "FALLBACK_DISABLED"),
    ({"sportsgameodds_api_key": ""}, "FALLBACK_NO_CREDENTIAL"),
    ({"sportsgameodds_base_url": ""}, "FALLBACK_NO_BASE_URL"),
])
def test_the_non_date_conditions_are_unchanged(over, reason):
    assert lane.eligible(cfg(**over), now=OCT1_T26) == (False, reason)


def test_only_the_nfl_pregame_lane_uses_the_renewal():
    """Every other SGO request path still reads the general window."""

    root = Path(__file__).resolve().parents[1] / "app"
    users = sorted(str(p.relative_to(root)).replace("\\", "/")
                   for p in root.rglob("*.py")
                   if "nfl_lane_eligible" in p.read_text(encoding="utf-8")
                   or "nfl_lane_window_open" in p.read_text(encoding="utf-8"))
    assert users == ["data_sources/provider_registry.py", "services/sgo_collection.py",
                     "services/sgo_nfl_pregame.py"]
    for other in ("scoreboard/collect.py", "scoreboard/nfl_live.py", "show_preview/collection.py"):
        text = (root / other).read_text(encoding="utf-8")
        assert "eligible(settings, now=" in text


def test_the_mlb_fetch_is_still_refused_in_october_before_any_network():
    with pytest.raises(Exception) as caught:
        sgo_collection.fetch_pregame_moneylines(cfg(), now=OCT1_T26)
    assert "FALLBACK_WINDOW_CLOSED_AFTER_2026-09-30" in str(caught.value)


def test_the_nfl_fetch_is_refused_in_november_before_any_network():
    with pytest.raises(Exception) as caught:
        lane.fetch_nfl_pregame(cfg(), now=datetime(2026, 11, 1, 0, 5, tzinfo=UTC))
    assert "NFL_LANE_WINDOW_CLOSED_AFTER_2026-10-31" in str(caught.value)
    assert "UNEXPECTED_NETWORK_CALL" not in str(caught.value)


def test_the_nfl_fetch_passes_eligibility_in_october():
    # Eligibility passes, so the next thing reached is the (refusing) transport.
    with pytest.raises(AssertionError, match="UNEXPECTED_NETWORK_CALL"):
        lane.fetch_nfl_pregame(cfg(), now=OCT1_T26, starts_after=PIT_CLE_KICKOFF)


# --------------------------------------------------------------------------
# The October cap, on the real collector
# --------------------------------------------------------------------------

def _ledger(session, *, at, status, charged=None, cost=1, reservation_id=None,
            endpoint="sgo_nfl_events"):
    row = ProviderRequestLedger(
        provider="SPORTSGAMEODDS", endpoint_key=endpoint, requested_at=at,
        status=status, quota_cost=cost, records_received=charged,
        reservation_id=reservation_id, request_fingerprint=uuid4().hex,
        completed_at=None if status == "RESERVED" else at + timedelta(seconds=1))
    session.add(row); session.flush()
    return row


def _spend(session, *, at, objects, endpoint="sgo_nfl_events"):
    reservation = _ledger(session, at=at, status="RESERVED", cost=objects, endpoint=endpoint)
    _ledger(session, at=at, status="SUCCEEDED", charged=objects, cost=objects,
            reservation_id=reservation.id, endpoint=endpoint)


class _Grant:
    def __init__(self, granted):
        self.allowed = granted > 0
        self.granted = granted
        self.reason = "GRANTED"

    def as_dict(self):
        return {"granted": self.granted}


class _Budget:
    def __init__(self):
        self.asked = None

    def may_spend(self, objects):
        self.asked = objects
        return False, "STOP_HERE_FOR_THE_TEST"

    def as_dict(self):
        return {"asked": self.asked}


def _wire(monkeypatch, *, due=3, granted=3):
    from app.services import provider_allocation

    games = [{"game_id": f"g{n}", "starts_at": PIT_CLE_KICKOFF.isoformat(),
              "minutes_to_kickoff": 26.0, "away": "PIT", "home": "CLE",
              "pass": "CHECKPOINT", "phase": "PRIMARY",
              "kickoff_cluster": PIT_CLE_KICKOFF.isoformat()} for n in range(due)]
    monkeypatch.setattr(lane, "due_games", lambda *a, **k: list(games))
    monkeypatch.setattr(provider_allocation, "request_objects",
                        lambda *a, **k: _Grant(granted))
    budget = _Budget()
    monkeypatch.setattr(sgo_collection, "object_budget", lambda *a, **k: budget)
    return budget


def test_the_renewal_month_count_is_conservative(session):
    oct2 = datetime(2026, 10, 2, 17, 0, tzinfo=UTC)
    _spend(session, at=oct2, objects=9)
    _ledger(session, at=oct2 + timedelta(minutes=5), status="RESERVED", cost=4)  # in flight
    failed = _ledger(session, at=oct2 + timedelta(minutes=9), status="RESERVED", cost=2)
    _ledger(session, at=oct2 + timedelta(minutes=9), status="FAILED", charged=0,
            reservation_id=failed.id)
    _spend(session, at=datetime(2026, 9, 30, 23, 0, tzinfo=UTC), objects=50)     # September
    _spend(session, at=oct2, objects=40, endpoint="sgo_mlb_events")             # another lane
    assert lane.renewal_month_objects(session, now=OCT1_T26) == 9 + 4 + 0


def test_the_cap_refuses_before_the_guard_the_claim_and_the_reservation(session, monkeypatch):
    budget = _wire(monkeypatch)
    _spend(session, at=datetime(2026, 10, 1, 0, 30, tzinfo=UTC), objects=150)
    before = session.query(ProviderRequestLedger).count()
    result = lane.collect_nfl_pregame(session, cfg(), now=OCT1_T26)
    assert result["reason"] == "NFL_LANE_RENEWAL_CAP_REACHED"
    assert result["objects"] == 0 and result["attempted"] is False
    assert budget.asked is None                                  # never reached the guard
    assert session.query(ProviderRequestLedger).count() == before
    assert session.query(NflAcquisitionPhaseClaim).count() == 0  # the claim is not spent


def test_the_cap_only_lowers_the_grant(session, monkeypatch):
    budget = _wire(monkeypatch, due=3, granted=3)
    _spend(session, at=datetime(2026, 10, 1, 0, 30, tzinfo=UTC), objects=149)
    result = lane.collect_nfl_pregame(session, cfg(), now=OCT1_T26)
    assert result["reason"] == "OBJECT_RESERVE_GUARD"            # the unchanged guard B
    assert budget.asked == 1


def test_an_unspent_october_passes_the_allocation_grant_through(session, monkeypatch):
    budget = _wire(monkeypatch, due=1, granted=1)
    lane.collect_nfl_pregame(session, cfg(), now=OCT1_T26)
    assert budget.asked == 1                                      # PIT-CLE's one object


def test_the_cap_never_applies_in_september(session, monkeypatch):
    budget = _wire(monkeypatch, due=3, granted=3)
    _spend(session, at=datetime(2026, 10, 1, 0, 30, tzinfo=UTC), objects=150)
    lane.collect_nfl_pregame(session, cfg(), now=datetime(2026, 9, 30, 17, 0, tzinfo=UTC))
    assert budget.asked == 3


def test_november_is_refused_as_provider_not_eligible(session, monkeypatch):
    budget = _wire(monkeypatch)
    result = lane.collect_nfl_pregame(session, cfg(), now=datetime(2026, 11, 1, 0, 5, tzinfo=UTC))
    assert result["reason"] == "PROVIDER_NOT_ELIGIBLE"
    assert result["detail"] == "NFL_LANE_WINDOW_CLOSED_AFTER_2026-10-31"
    assert budget.asked is None


def test_the_guards_and_reserve_are_unchanged():
    from app.services import provider_allocation

    assert sgo_collection.FREE_TIER_OBJECTS == 2500
    assert sgo_collection.OBJECT_RESERVE == 400
    assert provider_allocation.reserve_at(OCT1_T26) == 400
    assert provider_allocation.reserve_at(datetime(2026, 9, 30, 23, 59, tzinfo=UTC)) == 300
    assert sgo_collection.MIN_SECONDS_BETWEEN_FETCHES == 600


def test_the_t26_clocks_are_unchanged():
    assert lane.retimed_checkpoint_window(PIT_CLE_KICKOFF) == (
        datetime(2026, 10, 1, 23, 48, tzinfo=UTC), datetime(2026, 10, 1, 23, 50, tzinfo=UTC))
    assert lane.retimed_retry_window(PIT_CLE_KICKOFF) == (
        datetime(2026, 10, 1, 23, 52, tzinfo=UTC), datetime(2026, 10, 1, 23, 53, tzinfo=UTC))
    assert lane.RETIME_HARD_STOP == timedelta(minutes=21)

"""1 November: the first reading of a billing month no longer waits for an MLB request.

Before this rule, optional lanes (NFL, CFB, NHL research) were locked whenever no
provider reading existed inside the current UTC billing month, and only a
protected MLB request could take that reading. MLB makes none after its season
ends, so every optional lane would have stayed dark from 2026-11-01. Ledger rows
here are FIXTURES in a throwaway SQLite database; no provider is contacted.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from app.models import ProviderRequestLedger
from app.services import odds_budget_lanes as lanes

NOV_1 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)          # 08:00 ET, no November request yet
OCT_31 = datetime(2026, 10, 31, 23, 0, tzinfo=UTC)
#: NFL 60 + CFB 20 + NHL 20 = the 100-credit optional pool (cap 500, reserve 150, 250 protected): NHL yields
#: to NFL and CFB, so it is funded only from what their allocations leave.
FUNDED = lanes.lanes_with_nhl(20, 8, lanes.lanes_with_cfb(20, 8))


def _fingerprint(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()


def _request(session, endpoint, at, *, remaining=None, used=None, cost=1, reading=True, complete=True):
    """One reserved request and (optionally) its terminal row, as the orchestrator writes them."""

    reserved = ProviderRequestLedger(provider="THE_ODDS_API", endpoint_key=endpoint, requested_at=at,
                                     status="RESERVED", request_fingerprint=_fingerprint(endpoint, at),
                                     response_metadata={"request_shape": {"markets": ["h2h"], "regions": "us"}})
    session.add(reserved)
    session.flush()
    if complete:
        session.add(ProviderRequestLedger(
            provider="THE_ODDS_API", endpoint_key=endpoint, requested_at=at, completed_at=at + timedelta(seconds=2),
            status="SUCCEEDED" if reading or remaining is not None else "FAILED",
            request_fingerprint=reserved.request_fingerprint, reservation_id=reserved.id,
            quota_cost=0 if endpoint == "quota_recovery_probe" else cost,
            quota_remaining=remaining if reading else None,
            response_metadata={"request_shape": {"markets": ["h2h"], "regions": "us"},
                               "requests_used": used if reading else None}))
        session.flush()
    return reserved


def _budget(session, now=NOV_1, *, reserve=150, lanes_=FUNDED):
    return lanes.lane_budget(session, monthly_cap=500, reserve=reserve, daily_cap=18, floor=10, now=now,
                             lanes=lanes_)


def test_on_november_1_an_optional_lane_may_take_the_months_first_reading(session):
    # October was busy and measured; none of it is evidence about November's balance.
    _request(session, "baseball_mlb_odds", OCT_31 - timedelta(hours=3), remaining=60, used=440)
    _request(session, "americanfootball_nfl_odds", OCT_31, remaining=59, used=441)
    state = _budget(session)
    assert state.base.provider_used is None and state.base.requests_this_window == 0
    assert state.reconciliation["state"] == lanes.STATE_FIRST_READING
    assert state.reconciliation["optional_locked"] is False
    for lane in (lanes.LANE_NFL_RESEARCH, lanes.LANE_CFB_RESEARCH, lanes.LANE_NHL_RESEARCH):
        assert state.may_spend(lane, 1) == (True, None), lane
    # The protected lanes were never locked and are unchanged.
    assert state.may_spend(lanes.LANE_MLB_REQUIRED, 1) == (True, None)
    assert state.protected_unspent == 250


def test_the_old_rule_would_have_kept_every_optional_lane_dark(session):
    """The verified trap, reproduced through the unchanged lock: a caller that does not state its
    request count gets exactly the old answer."""

    unknown = lanes._reconcile(provider_used=None, lane_total=0, unattributed=0)
    assert unknown["state"] == lanes.STATE_BALANCE_UNKNOWN and unknown["optional_locked"] is True
    assert lanes._reconcile(provider_used=None, lane_total=0, unattributed=0,
                            chargeable_requests=lanes.FIRST_READING_ATTEMPTS)["optional_locked"] is True


def test_only_the_first_attempts_may_go_and_the_lock_returns_if_none_brings_a_reading(session):
    _request(session, "icehockey_nhl_odds", NOV_1 - timedelta(hours=2), reading=False)       # failed, no reading
    one = _budget(session)
    assert one.base.requests_this_window == 1 and one.reconciliation["state"] == lanes.STATE_FIRST_READING
    assert one.may_spend(lanes.LANE_NHL_RESEARCH, 1) == (True, None)                          # one retry
    _request(session, "americanfootball_nfl_odds", NOV_1 - timedelta(hours=1), complete=False)  # in flight
    two = _budget(session)
    assert two.base.requests_this_window == lanes.FIRST_READING_ATTEMPTS
    assert two.reconciliation["state"] == lanes.STATE_BALANCE_UNKNOWN
    for lane in (lanes.LANE_NFL_RESEARCH, lanes.LANE_CFB_RESEARCH, lanes.LANE_NHL_RESEARCH):
        ok, why = two.may_spend(lane, 1)
        assert not ok and "optional lanes are locked" in why, lane
    assert two.may_spend(lanes.LANE_MLB_REQUIRED, 1) == (True, None)                         # MLB unaffected


def test_once_the_reading_lands_normal_reconciliation_resumes(session):
    _request(session, "icehockey_nhl_odds", NOV_1 - timedelta(hours=1), remaining=499, used=1)
    state = _budget(session)
    assert state.base.provider_used == 1
    assert state.reconciliation["state"] == "RECONCILED" and state.may_spend(lanes.LANE_NHL_RESEARCH, 1) == (True, None)
    # And the provider counting more than AISP can explain still locks optional lanes, as before.
    _request(session, "americanfootball_nfl_odds", NOV_1 - timedelta(minutes=30), remaining=470, used=30)
    over = _budget(session)
    assert over.reconciliation["state"] == "PROVIDER_COUNTED_MORE"
    assert over.may_spend(lanes.LANE_NHL_RESEARCH, 1)[0] is False


def test_a_zero_cost_balance_probe_is_not_an_attempt_and_its_reading_counts(session):
    _request(session, "quota_recovery_probe", NOV_1 - timedelta(hours=1), complete=False)
    assert _budget(session).reconciliation["state"] == lanes.STATE_FIRST_READING             # 0 chargeable
    _request(session, "quota_recovery_probe", NOV_1 - timedelta(minutes=50), remaining=500, used=0)
    assert _budget(session).reconciliation["state"] == "RECONCILED"


@pytest.mark.parametrize("case", ["unfunded", "daily_cap", "protected_holdback", "monthly_reserve"])
def test_every_other_guard_still_applies_to_the_first_reading(session, case):
    if case == "unfunded":
        state = _budget(session, lanes_=lanes.DEFAULT_LANES)
        ok, why = state.may_spend(lanes.LANE_NHL_RESEARCH, 1)
        assert not ok and "0 of its 0" in why
    elif case == "daily_cap":
        state = _budget(session)
        ok, why = state.may_spend(lanes.LANE_NHL_RESEARCH, 9)                                # cap is 8
        assert not ok and "daily credits" in why
    elif case == "protected_holdback":
        state = _budget(session, reserve=245, lanes_=lanes.lanes_with_nhl(500, 48))         # 255 budgeted, 250 held
        assert state.may_spend(lanes.LANE_NFL_RESEARCH, 5) == (True, None)
        for lane in (lanes.LANE_NFL_RESEARCH, lanes.LANE_NHL_RESEARCH):
            ok, why = state.may_spend(lane, 6)
            assert not ok and "reserved for protected lanes" in why, lane
        # Inside the protected holdback NHL still leaves NFL's unspent 60 whole: nothing is left for it.
        ok, why = state.may_spend(lanes.LANE_NHL_RESEARCH, 5)
        assert not ok and "yields to NFL research moneylines" in why
    else:
        state = _budget(session, reserve=500)
        assert state.may_spend(lanes.LANE_MLB_REQUIRED, 1)[0] is False                      # nothing budgeted at all
        assert state.may_spend(lanes.LANE_NHL_RESEARCH, 1)[0] is False
    assert state.reconciliation["state"] == lanes.STATE_FIRST_READING                       # the rule held, the guard refused


def test_the_orchestrator_lane_guard_lets_a_funded_optional_request_take_the_first_reading(session, settings):
    """An empty ledger is exactly 1 November at 00:00Z: no in-window reading, nothing reserved."""

    from app.data_sources import SourceError
    from app.jobs.orchestrator import JobOrchestrator

    funded = settings.model_copy(update={"odds_nhl_lane_monthly_credits": 30, "odds_nhl_lane_daily_credits": 6,
                                         "odds_cfb_lane_monthly_credits": 30, "odds_cfb_lane_daily_credits": 6})
    orchestrator = JobOrchestrator(session, funded)
    for endpoint in ("icehockey_nhl_odds", "americanfootball_ncaaf_odds", "americanfootball_nfl_odds"):
        assert orchestrator._enforce_lane_budget(endpoint, {"markets": ["h2h"], "regions": "us"}) is None, endpoint
    # Two chargeable attempts in this window without a reading: the old lock is back.
    now = datetime.now(UTC)
    for seconds in (1, 2):
        _request(session, "icehockey_nhl_odds", now - timedelta(seconds=seconds), reading=False)
    with pytest.raises(SourceError, match="optional lanes are locked"):
        orchestrator._enforce_lane_budget("icehockey_nhl_odds", {"markets": ["h2h"], "regions": "us"})

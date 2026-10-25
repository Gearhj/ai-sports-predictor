"""The Odds API manual cooldown is per endpoint for CFB and NHL too (hotfix 2026-10-24, review MEDIUM-2).

WHAT HAPPENED
-------------
``JobOrchestrator._enforce_odds_quota_policy`` scoped the 5-minute cooldown per
endpoint only for ``basketball_nba_odds`` and ``americanfootball_nfl_fallback_odds``
and raised "Unqualified cooldown scope" for any other key, so the CFB and NHL
collectors read the GLOBAL newest success and every NBA/NFL buy re-armed their
cooldown. On 10-22 the TNF fallback was refused twice ("cooldown active for 88 /
61 more seconds") by a cooldown another lane had set; 8 NHL, 3 NBA and 4 MLB
collector ticks failed the same way since 10-20. Saturday evening has CFB, NBA
and NHL clusters minutes apart.

THE LINE
--------
The cooldown is double-refresh protection for ONE lane, never a money guard:
the daily request budget, the monthly credit cap/reserve, the provider floor
and its latch stay global and are untouched here. A scoped lane reads only its
own endpoint's newest SUCCEEDED ledger row; its own 5-minute cooldown still
binds. SQLite only; no provider call.
"""
from __future__ import annotations

import hashlib
import itertools
from datetime import UTC, datetime, timedelta

import pytest

from app.cfb import odds_api_price as cfb_odds
from app.data_sources import SourceError
from app.jobs.orchestrator import JobOrchestrator
from app.models import IngestionRun, ProviderRequestLedger, RunStatus
from app.nba import odds_api_price as nba_odds
from app.nhl import odds_api_price as nhl_odds
from app.services import nfl_odds_api_fallback as fallback

_seq = itertools.count()


def _reading(session, at: datetime, *, remaining: int = 19_000) -> None:
    """A zero-cost balance reading inside the billing month (the credit guard needs one)."""

    fingerprint = hashlib.sha256(f"probe|{at.isoformat()}|{next(_seq)}".encode()).hexdigest()
    reserved = ProviderRequestLedger(provider="THE_ODDS_API", endpoint_key="quota_recovery_probe", requested_at=at,
                                     status="RESERVED", request_fingerprint=fingerprint)
    session.add(reserved)
    session.flush()
    session.add(ProviderRequestLedger(provider="THE_ODDS_API", endpoint_key="quota_recovery_probe", requested_at=at,
                                      completed_at=at, status="SUCCEEDED", request_fingerprint=fingerprint,
                                      quota_cost=0, reservation_id=reserved.id, quota_remaining=remaining,
                                      response_metadata={"requests_used": 0}))
    session.flush()


def _success(session, endpoint: str, completed_at: datetime) -> None:
    """One ledgered, SUCCEEDED 1-credit request on ``endpoint``, as the orchestrator writes it."""

    fingerprint = hashlib.sha256(f"{endpoint}|{next(_seq)}".encode()).hexdigest()
    requested_at = completed_at - timedelta(seconds=2)
    reserved = ProviderRequestLedger(provider="THE_ODDS_API", endpoint_key=endpoint, requested_at=requested_at,
                                     status="RESERVED", request_fingerprint=fingerprint, source_mode="LIVE")
    session.add(reserved)
    session.flush()
    session.add(ProviderRequestLedger(provider="THE_ODDS_API", endpoint_key=endpoint, requested_at=requested_at,
                                      completed_at=completed_at, status="SUCCEEDED", request_fingerprint=fingerprint,
                                      quota_cost=1, reservation_id=reserved.id, source_mode="LIVE",
                                      quota_remaining=18_999))
    session.flush()


def _global_anchor(session, completed_at: datetime) -> None:
    """The newest successful Odds API RUN with a quota reading: what the UNSCOPED cooldown reads."""

    session.add(IngestionRun(source="THE_ODDS_API", job_name="collect-odds", status=RunStatus.SUCCEEDED,
                             started_at=completed_at - timedelta(seconds=3), completed_at=completed_at,
                             records_processed=1, source_metadata={"requests_remaining": 18_999}))
    session.flush()


@pytest.fixture
def orchestrator(session, settings):
    now = datetime.now(UTC)
    _reading(session, now - timedelta(days=1))
    funded = settings.model_copy(update={"the_odds_api_key": "fixture-not-a-key", "odds_daily_request_budget": 40,  # secret-guard: test-fixture
                                         "odds_monthly_credit_cap": 20_000, "odds_daily_credit_budget": 40,
                                         "odds_manual_cooldown_minutes": 5})
    session.commit()
    return JobOrchestrator(session, funded)


def test_an_nhl_request_one_second_after_a_cfb_success_is_not_refused(orchestrator, session):
    now = datetime.now(UTC)
    _global_anchor(session, now - timedelta(seconds=1))
    _success(session, cfb_odds.ENDPOINT_KEY, now - timedelta(seconds=1))
    session.commit()
    # NHL's own lane has never bought: no cooldown of its own.
    orchestrator._enforce_odds_quota_policy(1, cooldown_endpoint_key=nhl_odds.ENDPOINT_KEY)
    # Nor NBA's, nor the NFL fallback's.
    orchestrator._enforce_odds_quota_policy(1, cooldown_endpoint_key=nba_odds.ENDPOINT_KEY)
    orchestrator._enforce_odds_quota_policy(1, cooldown_endpoint_key=fallback.ENDPOINT_KEY)


def test_the_same_endpoints_own_five_minute_cooldown_still_applies(orchestrator, session):
    now = datetime.now(UTC)
    _global_anchor(session, now - timedelta(minutes=10))        # the cooldown block is anchored to a successful run
    _success(session, cfb_odds.ENDPOINT_KEY, now - timedelta(seconds=1))
    session.commit()
    with pytest.raises(SourceError, match=r"Odds refresh cooldown active for 29[0-9] more seconds"):
        orchestrator._enforce_odds_quota_policy(1, cooldown_endpoint_key=cfb_odds.ENDPOINT_KEY)
    # ...and NHL against NHL, once NHL has bought.
    _success(session, nhl_odds.ENDPOINT_KEY, now - timedelta(seconds=90))
    session.commit()
    with pytest.raises(SourceError, match=r"cooldown active for 2[01][0-9] more seconds"):
        orchestrator._enforce_odds_quota_policy(1, cooldown_endpoint_key=nhl_odds.ENDPOINT_KEY)


def test_a_lane_is_free_again_once_its_own_cooldown_has_elapsed(orchestrator, session):
    now = datetime.now(UTC)
    _global_anchor(session, now - timedelta(seconds=1))
    _success(session, cfb_odds.ENDPOINT_KEY, now - timedelta(minutes=5, seconds=1))
    _success(session, nhl_odds.ENDPOINT_KEY, now - timedelta(seconds=1))       # another lane, just now
    session.commit()
    orchestrator._enforce_odds_quota_policy(1, cooldown_endpoint_key=cfb_odds.ENDPOINT_KEY)


def test_the_unscoped_global_cooldown_is_unchanged(orchestrator, session):
    """A caller that passes no scope still reads the newest successful run of ANY lane."""

    now = datetime.now(UTC)
    _global_anchor(session, now - timedelta(seconds=1))
    session.commit()
    with pytest.raises(SourceError, match="Odds refresh cooldown active"):
        orchestrator._enforce_odds_quota_policy(1)
    # The scoped lanes are not held by it.
    orchestrator._enforce_odds_quota_policy(1, cooldown_endpoint_key=cfb_odds.ENDPOINT_KEY)
    orchestrator._enforce_odds_quota_policy(1, cooldown_endpoint_key=nhl_odds.ENDPOINT_KEY)


def test_no_endpoint_key_is_an_unqualified_scope_any_more(orchestrator, session):
    """The allow-list raise is gone: scoping is a property of the request, not of two named lanes."""

    now = datetime.now(UTC)
    _global_anchor(session, now - timedelta(seconds=1))
    session.commit()
    orchestrator._enforce_odds_quota_policy(1, cooldown_endpoint_key="baseball_mlb_odds")


def test_the_money_guards_stay_global_under_scoping(orchestrator, session):
    """Scoping the cooldown never weakens the daily request budget."""

    capped = orchestrator.settings.model_copy(update={"odds_daily_request_budget": 1})
    tight = JobOrchestrator(session, capped)
    now = datetime.now(UTC)
    _success(session, nba_odds.ENDPOINT_KEY, now - timedelta(minutes=10))
    session.add(ProviderRequestLedger(provider="THE_ODDS_API", endpoint_key=nba_odds.ENDPOINT_KEY,
                                      requested_at=now - timedelta(minutes=1), status="RESERVED",
                                      request_fingerprint="d" * 64, source_mode="LIVE"))
    session.commit()
    with pytest.raises(SourceError, match="Daily request budget reached"):
        tight._enforce_odds_quota_policy(1, cooldown_endpoint_key=cfb_odds.ENDPOINT_KEY)


# ------------------------------------------------------------- the call sites --
@pytest.mark.asyncio
async def test_the_cfb_collector_asks_about_its_own_lane_only(session, settings, monkeypatch):
    seen: dict = {}

    def spy(self, credit_cost=1, *, request_holdback=0, holdback_for="", cooldown_endpoint_key=None):
        seen["key"] = cooldown_endpoint_key
        raise SourceError("THE_ODDS_API", "stop here", retryable=False)

    monkeypatch.setattr(JobOrchestrator, "_enforce_odds_quota_policy", spy)
    monkeypatch.setattr(cfb_odds, "plan", lambda session, now=None: {"due": [{"game_id": "x"}]})
    on = settings.model_copy(update={"cfb_odds_api_enabled": True, "the_odds_api_key": "fixture-not-a-key"})  # secret-guard: test-fixture
    with pytest.raises(SourceError, match="stop here"):
        await JobOrchestrator(session, on).collect_cfb_odds_api(datetime.now(UTC).date())
    assert seen["key"] == cfb_odds.ENDPOINT_KEY == "americanfootball_ncaaf_odds"


@pytest.mark.asyncio
async def test_the_nhl_collector_asks_about_its_own_lane_only(session, settings, monkeypatch):
    seen: dict = {}

    def spy(self, credit_cost=1, *, request_holdback=0, holdback_for="", cooldown_endpoint_key=None):
        seen["key"] = cooldown_endpoint_key
        seen["holdback"] = request_holdback
        raise SourceError("THE_ODDS_API", "stop here", retryable=False)

    monkeypatch.setattr(JobOrchestrator, "_enforce_odds_quota_policy", spy)
    monkeypatch.setattr(nhl_odds, "plan", lambda session, now=None: {"due": [{"game_id": "x"}]})
    on = settings.model_copy(update={"nhl_odds_api_enabled": True, "the_odds_api_key": "fixture-not-a-key"})  # secret-guard: test-fixture
    with pytest.raises(SourceError, match="stop here"):
        await JobOrchestrator(session, on).collect_nhl_odds_api(datetime.now(UTC).date())
    assert seen["key"] == nhl_odds.ENDPOINT_KEY == "icehockey_nhl_odds"
    assert "holdback" in seen                                   # the NHL yield rule still rides along

"""NHL yields to NFL and CFB in every shared Odds API guard (review MAJOR x2, 2026-10-16).

Before: ``may_spend`` let any optional lane spend whatever the protected holdback left, first come first
served, and ``precedence`` was display order only. Funded at the planned 150/month and 8/day, NHL could
spend the whole ~100-credit optional pool and the shared ODDS_DAILY_REQUEST_BUDGET before NFL's T-26
fallback asked ("250 credits are reserved for protected lanes and only 0 are free" for an NFL lane that had
spent 0 of its 60; "Daily request budget reached (8/8)" after eight NHL reservations).

Now NHL_RESEARCH ``yields_to`` NFL_RESEARCH and CFB_RESEARCH: their unspent monthly allocation always, and
on a day their sport is still in play, their unspent daily allocation of the shared daily credit budget and
of the shared daily request budget. Ledger rows and games are FIXTURES in a throwaway SQLite database; no
provider is contacted.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from app.models import Game, GameStatus, League, ProviderRequestLedger, Season, Sport
from app.services import odds_budget_lanes as lanes

NOW = datetime(2026, 11, 25, 15, 0, tzinfo=UTC)               # Wed 10:00 ET, late in a billing month
DEPLOY = lanes.lanes_with_nhl(150, 8, lanes.lanes_with_cfb(20, 8))   # the planned NHL funding; CFB funded too
ENDPOINT = {lanes.LANE_NFL_RESEARCH: "americanfootball_nfl_odds", lanes.LANE_CFB_RESEARCH: "americanfootball_ncaaf_odds",
            lanes.LANE_NHL_RESEARCH: "icehockey_nhl_odds"}
_seq = iter(range(1, 10_000))


def _spend(session, lane: str, at: datetime, *, terminal: bool = True) -> None:
    """One 1-credit h2h request, reserved and (optionally) completed, as the orchestrator writes it."""

    endpoint = ENDPOINT[lane]
    fingerprint = hashlib.sha256(f"{endpoint}|{next(_seq)}".encode()).hexdigest()
    shape = {"request_shape": {"markets": ["h2h"], "regions": "us"}}
    reserved = ProviderRequestLedger(provider="THE_ODDS_API", endpoint_key=endpoint, requested_at=at,
                                     status="RESERVED", request_fingerprint=fingerprint, source_mode="LIVE",
                                     response_metadata=shape)
    session.add(reserved)
    session.flush()
    if terminal:
        session.add(ProviderRequestLedger(provider="THE_ODDS_API", endpoint_key=endpoint, requested_at=at,
                                          completed_at=at + timedelta(seconds=2), status="SUCCEEDED",
                                          request_fingerprint=fingerprint, quota_cost=1, reservation_id=reserved.id,
                                          source_mode="LIVE", response_metadata=shape))
        session.flush()


def _reading(session, at: datetime, *, used: int = 0) -> None:
    """A provider balance reading inside the billing month (zero-cost probe)."""

    fingerprint = hashlib.sha256(f"probe|{at.isoformat()}|{next(_seq)}".encode()).hexdigest()
    reserved = ProviderRequestLedger(provider="THE_ODDS_API", endpoint_key="quota_recovery_probe", requested_at=at,
                                     status="RESERVED", request_fingerprint=fingerprint)
    session.add(reserved)
    session.flush()
    session.add(ProviderRequestLedger(provider="THE_ODDS_API", endpoint_key="quota_recovery_probe",
                                      requested_at=at, completed_at=at, status="SUCCEEDED",
                                      request_fingerprint=fingerprint, quota_cost=0, reservation_id=reserved.id,
                                      quota_remaining=500 - used, response_metadata={"requests_used": used}))
    session.flush()


def _month_reading(session, now: datetime) -> None:
    """A balance reading at the start of ``now``'s billing month (so it is not a request of today)."""

    from app.services.odds_budget import billing_window

    _reading(session, billing_window(now)[0] + timedelta(minutes=1))


def _reserved_today(session, settings) -> int:
    """What the shared daily request guard counts (``_enforce_odds_quota_policy``): RESERVED LIVE rows today."""

    from sqlalchemy import func, select

    from app.core.time import local_day_bounds, local_today

    start, end = local_day_bounds(local_today(settings.display_timezone), settings.display_timezone)
    return session.scalar(select(func.count(ProviderRequestLedger.id)).where(
        ProviderRequestLedger.provider == "THE_ODDS_API", ProviderRequestLedger.requested_at >= start,
        ProviderRequestLedger.requested_at < end, ProviderRequestLedger.source_mode == "LIVE",
        ProviderRequestLedger.status == "RESERVED")) or 0


def _budget(session, *, now=NOW, in_play=frozenset(), lanes_=DEPLOY):
    return lanes.lane_budget(session, monthly_cap=500, reserve=150, daily_cap=18, floor=10, now=now,
                             lanes=lanes_, in_play_today=in_play)


def _league_game(session, code: str, starts_at: datetime, status=GameStatus.SCHEDULED) -> Game:
    league = session.query(League).filter_by(code=code).one_or_none()
    if league is None:
        sport = Sport(code=f"SPORT_{code}", name=code)
        session.add(sport)
        session.flush()
        league = League(sport_id=sport.id, code=code, name=code)
        session.add(league)
        session.flush()
    season = Season(league_id=league.id, name=f"{code}-{next(_seq)}", starts_at=starts_at - timedelta(days=60),
                    ends_at=starts_at + timedelta(days=60))
    session.add(season)
    session.flush()
    game = Game(league_id=league.id, season_id=season.id, starts_at=starts_at, status=status)
    session.add(game)
    session.flush()
    return game


def _past_slots(start: datetime, end: datetime, per_day: int):
    day = start
    while day < end:
        for k in range(per_day):
            at = day + timedelta(hours=1 + k)
            if at < end:
                yield at
        day += timedelta(days=1)


def test_only_nhl_yields_so_every_other_lanes_arithmetic_is_unchanged():
    assert {lane.key: lane.yields_to for lane in lanes.DEFAULT_LANES if lane.yields_to} == {
        lanes.LANE_NHL_RESEARCH: (lanes.LANE_NFL_RESEARCH, lanes.LANE_CFB_RESEARCH),
        # MAIN's NBA Odds API lane (10-20) yields to all three; nothing else yields.
        lanes.LANE_NBA_RESEARCH: (lanes.LANE_NFL_RESEARCH, lanes.LANE_CFB_RESEARCH, lanes.LANE_NHL_RESEARCH)}
    # Funding from settings keeps the yield (dataclasses.replace).
    assert {lane.key: lane.yields_to for lane in DEPLOY}[lanes.LANE_NHL_RESEARCH] == (
        lanes.LANE_NFL_RESEARCH, lanes.LANE_CFB_RESEARCH)


def test_nhl_spending_everything_it_may_never_refuses_an_nfl_or_cfb_request(session):
    """The reviewer's probe, inverted: NHL at the deploy funding spends at its daily pace until refused;
    NFL and CFB then still spend their WHOLE remaining allocations."""

    window_start = datetime(2026, 11, 1, tzinfo=UTC)
    _reading(session, window_start + timedelta(minutes=5), used=0)
    for at in (window_start + timedelta(hours=10 + k) for k in range(10)):
        _spend(session, lanes.LANE_NFL_RESEARCH, at)                     # NFL has used 10 of its 60
    for at in (window_start + timedelta(days=1, hours=10 + k) for k in range(5)):
        _spend(session, lanes.LANE_CFB_RESEARCH, at)                     # CFB has used 5 of its 20
    assert _budget(session).may_spend(lanes.LANE_NFL_RESEARCH, 1) == (True, None)

    nhl = 0
    for at in _past_slots(window_start + timedelta(days=2), NOW.replace(hour=0), per_day=7):
        if not _budget(session, now=at).may_spend(lanes.LANE_NHL_RESEARCH, 1)[0]:
            continue
        _spend(session, lanes.LANE_NHL_RESEARCH, at)
        nhl += 1
    # The optional pool is 100 (500 - 150 reserve - 250 protected MLB); NFL's 60 and CFB's 20 stay theirs.
    assert nhl == 100 - 60 - 20 == 20
    after = _budget(session)
    ok, why = after.may_spend(lanes.LANE_NHL_RESEARCH, 1)
    assert not ok and "yields to NFL research moneylines, CFB paper moneylines" in why, why
    hold = after.yield_holdback(lanes.LANE_NHL_RESEARCH)
    assert hold["monthly"] == 50 + 15 and hold["daily"] == 0          # neither sport in play (in_play=())

    # Before the fix NFL read "only 0 are free" here. Now every remaining NFL and CFB credit is spendable.
    for lane, remaining in ((lanes.LANE_NFL_RESEARCH, 50), (lanes.LANE_CFB_RESEARCH, 15)):
        for i in range(remaining):
            at = NOW - timedelta(days=1 + i // 6, minutes=i)            # past days: inside each daily cap
            assert _budget(session).may_spend(lane, 1) == (True, None), (lane, i)
            _spend(session, lane, at)
        ok, why = _budget(session).may_spend(lane, 1)
        assert not ok and "monthly credits left" in why                 # its own allocation, nothing else
    final = _budget(session)
    assert final.protected_unspent == 250 and final.base.budgeted_remaining == 250   # MLB still whole


def test_on_a_day_nfl_plays_nhl_leaves_nfl_its_share_of_the_shared_daily_credit_budget(session):
    sunday = datetime(2026, 11, 22, 15, 0, tzinfo=UTC)                  # 10:00 ET
    _reading(session, datetime(2026, 11, 1, 0, 5, tzinfo=UTC))
    _league_game(session, "NFL", sunday + timedelta(hours=3))           # 1:00 PM ET kickoff, still to come
    in_play = lanes.lanes_in_play_today(session, now=sunday)
    assert in_play == frozenset({lanes.LANE_NFL_RESEARCH})
    # A lighter shared daily cap makes the arithmetic visible: 10 credits today, NFL's 6 held.
    def budget():
        return lanes.lane_budget(session, monthly_cap=500, reserve=150, daily_cap=10, floor=10, now=sunday,
                                 lanes=DEPLOY, in_play_today=in_play)

    spent = 0
    while budget().may_spend(lanes.LANE_NHL_RESEARCH, 1)[0]:
        _spend(session, lanes.LANE_NHL_RESEARCH, sunday - timedelta(minutes=30 - spent))
        spent += 1
    assert spent == 10 - 6
    ok, why = budget().may_spend(lanes.LANE_NHL_RESEARCH, 1)
    assert not ok and "of today's shared 10-credit daily budget stay for them" in why
    for i in range(6):                                                   # NFL's whole daily cap still fits
        assert budget().may_spend(lanes.LANE_NFL_RESEARCH, 1) == (True, None), i
        _spend(session, lanes.LANE_NFL_RESEARCH, sunday - timedelta(minutes=10 - i))
    # With no NFL game left today, nothing is held for NFL: NHL may use the shared day (and its own cap).
    none_left = lanes.lane_budget(session, monthly_cap=500, reserve=150, daily_cap=18, floor=10, now=sunday,
                                  lanes=DEPLOY, in_play_today=frozenset())
    assert none_left.yield_holdback(lanes.LANE_NHL_RESEARCH)["daily"] == 0
    assert none_left.may_spend(lanes.LANE_NHL_RESEARCH, 1) == (True, None)


def test_in_play_means_a_scheduled_game_still_to_start_this_operational_day(session):
    saturday = datetime(2026, 11, 21, 16, 0, tzinfo=UTC)                 # 11:00 ET
    _league_game(session, "NFL", saturday - timedelta(hours=1))         # already started
    _league_game(session, "NFL", saturday + timedelta(hours=2), status=GameStatus.POSTPONED)
    _league_game(session, "NFL", saturday + timedelta(days=1))          # tomorrow
    assert lanes.lanes_in_play_today(session, now=saturday) == frozenset()
    _league_game(session, "CFB", datetime(2026, 11, 22, 6, 0, tzinfo=UTC))   # 1:00 AM ET: buy window opens tonight
    assert lanes.lanes_in_play_today(session, now=saturday) == frozenset({lanes.LANE_CFB_RESEARCH})
    _league_game(session, "NHL", saturday + timedelta(hours=7))         # NHL never puts a yielded lane in play
    _league_game(session, "NFL", saturday + timedelta(hours=8))         # a Saturday NFL game (December)
    assert lanes.lanes_in_play_today(session, now=saturday) == frozenset(
        {lanes.LANE_NFL_RESEARCH, lanes.LANE_CFB_RESEARCH})
    # A caller that does not compute it gets the conservative reading: every yielded lane is in play.
    unknown = lanes.lane_budget(session, monthly_cap=500, reserve=150, daily_cap=18, floor=10, now=saturday,
                                lanes=DEPLOY)
    assert unknown.yield_holdback(lanes.LANE_NHL_RESEARCH)["daily"] == 6 + 8
    assert unknown.as_dict()["in_play_today"] is None


def test_nhl_leaves_nfl_its_share_of_the_shared_daily_request_budget(session, settings):
    """The reviewer's daily-budget probe through the orchestrator: budget 8, NFL kicking off later today."""

    from app.data_sources import SourceError
    from app.jobs.orchestrator import JobOrchestrator

    now = datetime.now(UTC)
    _month_reading(session, now)
    nfl_game = _league_game(session, "NFL", now + timedelta(minutes=30))
    baseline = _reserved_today(session, settings)                        # 0 except in the month's first hours
    budget = baseline + 8
    funded = settings.model_copy(update={"odds_daily_request_budget": budget, "odds_nhl_lane_monthly_credits": 150,
                                         "odds_nhl_lane_daily_credits": 8, "nhl_odds_api_enabled": True})
    orc = JobOrchestrator(session, funded)
    hold = orc._odds_lane_yield(lanes.LANE_NHL_RESEARCH)
    assert hold["daily"] == 6 and [h["lane"] for h in hold["lanes"] if h["today"]] == [lanes.LANE_NFL_RESEARCH]

    def nhl_check():
        h = orc._odds_lane_yield(lanes.LANE_NHL_RESEARCH)
        orc._enforce_odds_quota_policy(1, request_holdback=h["daily"], holdback_for="NFL research moneylines")

    used = 0
    while True:
        try:
            nhl_check()
        except SourceError as exc:
            assert "held for NFL research moneylines" in str(exc) and "yields to them" in str(exc)
            break
        _spend(session, lanes.LANE_NHL_RESEARCH, now - timedelta(seconds=60 - used), terminal=False)
        used += 1
    assert used == 8 - 6                                                 # NFL's six stay out of NHL's reach
    # NFL's own check (no holdback) still passes for all six of its daily requests.
    for i in range(6):
        orc._enforce_odds_quota_policy(1)
        _spend(session, lanes.LANE_NFL_RESEARCH, now - timedelta(seconds=30 - i), terminal=False)
    with pytest.raises(SourceError, match=rf"Daily request budget reached \({budget}/{budget}\)"):
        orc._enforce_odds_quota_policy(1)                                # the shared budget, honestly full
    # Once NFL has nothing left to start today, nothing is held for it.
    nfl_game.status = GameStatus.FINAL
    session.flush()
    assert orc._odds_lane_yield(lanes.LANE_NHL_RESEARCH)["daily"] == 0


@pytest.mark.asyncio
async def test_the_nhl_collector_is_refused_before_any_request_when_only_nfls_share_is_left(
        session, settings, monkeypatch):
    from app.data_sources import SourceError
    from app.data_sources.odds import TheOddsAPIAdapter
    from app.jobs.orchestrator import JobOrchestrator
    from app.nhl import odds_api_price as nhl_odds
    from tests.test_nhl_odds_api_source import FETCHED, ON, TOR_MTL, _fake_snapshot, _in_process, raw_event

    _in_process(session)
    now = datetime.now(UTC)
    _month_reading(session, now)
    _league_game(session, "NFL", now + timedelta(minutes=30))
    for i in range(2):                                                   # two NHL requests already today
        _spend(session, lanes.LANE_NHL_RESEARCH, now - timedelta(minutes=20 - i))
    session.commit()
    used_today = _reserved_today(session, settings)                      # the two NHL requests (+ a 1st-hour probe)
    calls: list = []
    monkeypatch.setattr(TheOddsAPIAdapter, "get_market_snapshot",
                        _fake_snapshot(calls, [raw_event("FIXTURENHL00001", *TOR_MTL)]))
    real_plan = nhl_odds.plan
    monkeypatch.setattr(nhl_odds, "plan", lambda s, now=None: real_plan(s, now=FETCHED))
    before = session.query(ProviderRequestLedger).count()
    tight = settings.model_copy(update={**ON, "odds_daily_request_budget": used_today + 6})   # used + 6 held
    with pytest.raises(SourceError, match="held for NFL research moneylines"):
        await JobOrchestrator(session, tight).collect_nhl_odds_api(now.date())
    session.rollback()
    assert calls == [] and session.query(ProviderRequestLedger).count() == before    # nothing reserved
    roomy = settings.model_copy(update={**ON, "odds_daily_request_budget": used_today + 6 + 1})
    result = await JobOrchestrator(session, roomy).collect_nhl_odds_api(now.date())
    assert result["provider_called"] and len(calls) == 1


# -- the owner's 2026-10-16 priority: "prioritize MLB and NHL over CFB" -------------------
def test_with_yields_to_cfb_off_nhl_yields_to_nfl_only_and_every_other_lane_is_unchanged():
    off = lanes.lanes_with_nhl(150, 10, lanes.lanes_with_cfb(300, 30), yields_to_cfb=False)
    on = lanes.lanes_with_nhl(150, 10, lanes.lanes_with_cfb(300, 30))
    by_key_off, by_key_on = {s.key: s for s in off}, {s.key: s for s in on}
    assert by_key_off[lanes.LANE_NHL_RESEARCH].yields_to == (lanes.LANE_NFL_RESEARCH,)
    assert by_key_on[lanes.LANE_NHL_RESEARCH].yields_to == (lanes.LANE_NFL_RESEARCH, lanes.LANE_CFB_RESEARCH)
    for key in by_key_off:                                                # nothing else differs
        if key != lanes.LANE_NHL_RESEARCH:
            assert by_key_off[key] == by_key_on[key]
    assert "Yields to NFL:" in by_key_off[lanes.LANE_NHL_RESEARCH].purpose
    assert "Yields to NFL and CFB" in by_key_on[lanes.LANE_NHL_RESEARCH].purpose
    assert (by_key_off[lanes.LANE_NHL_RESEARCH].monthly_allocation, by_key_off[lanes.LANE_NHL_RESEARCH].daily_cap) \
        == (150, 10)
    # The compiled default (and any caller that does not pass the switch) keeps the 1aee96d arithmetic.
    assert lanes.LANES[lanes.LANE_NHL_RESEARCH].yields_to == (lanes.LANE_NFL_RESEARCH, lanes.LANE_CFB_RESEARCH)


def test_on_a_cfb_saturday_nhl_holds_nothing_back_for_cfb_unless_the_switch_restores_it(session, settings):
    """Saturday 2026-10-17: CFB funded 300/30 and in play, NFL not. With ODDS_NHL_YIELDS_TO_CFB off (the
    default) NHL leaves CFB nothing of the shared daily request budget; on, it leaves CFB's unspent 30."""

    from app.jobs.orchestrator import JobOrchestrator

    now = datetime.now(UTC)
    _month_reading(session, now)
    _league_game(session, "CFB", now + timedelta(minutes=45))
    funded = settings.model_copy(update={"odds_cfb_lane_monthly_credits": 300, "odds_cfb_lane_daily_credits": 30,
                                         "odds_nhl_lane_monthly_credits": 150, "odds_nhl_lane_daily_credits": 10,
                                         "odds_daily_request_budget": 48, "nhl_odds_api_enabled": True})
    assert funded.odds_nhl_yields_to_cfb is False                         # the default
    hold = JobOrchestrator(session, funded)._odds_lane_yield(lanes.LANE_NHL_RESEARCH)
    assert hold["daily"] == 0 and [h["lane"] for h in hold["lanes"]] == [lanes.LANE_NFL_RESEARCH]
    budget = JobOrchestrator(session, funded)._odds_lane_budget(yielding_lane=lanes.LANE_NHL_RESEARCH)
    assert budget.may_spend(lanes.LANE_NHL_RESEARCH, 1) == (True, None)
    restored = funded.model_copy(update={"odds_nhl_yields_to_cfb": True})
    hold = JobOrchestrator(session, restored)._odds_lane_yield(lanes.LANE_NHL_RESEARCH)
    assert [h["lane"] for h in hold["lanes"]] == [lanes.LANE_NFL_RESEARCH, lanes.LANE_CFB_RESEARCH]
    assert hold["daily"] == 30                                           # CFB's whole unspent daily share

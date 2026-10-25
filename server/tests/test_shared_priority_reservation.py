"""AISP_SHARED_100_PRIORITY_MLB_NHL_V1 (owner, 2026-10-16 21:20Z: "I want to prioritize MLB and NHL over CFB").

The shared $100 per Eastern slate date is unchanged. MLB and NHL games still
waiting for their decision hold their policy maximum back from every other
sport; the hold releases when the game is decided (bet or pass) or starts; a
value-qualified decision sized to $0 by the allowance says so and is never a
no-bet. Offline: SQLite fixtures only (the PostgreSQL advisory lock is checked
for ORDER with a recording session). TEST FIXTURES ONLY -- no real games.
"""
from __future__ import annotations

import random
import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.models import (
    AuditEvent, ExperimentDecisionPhase, Game, GameStatus, Market, MarketOutcome, MarketType, OfficialAspPosition,
    PositionResult, PositionStatus, ShadowExperiment, ShadowExperimentDecision,
)
from app.services import shared_daily_allowance as sda
from app.services import shared_priority_reservation as spr
from app.services.research_paper_policy import NHL
from tests.test_cfb_consumer_activation import era, world  # noqa: F401 -- fixtures
from tests.test_cfb_daily_limit_amendment import checkpoint  # noqa: F401 -- fixture
from tests.test_nfl_truthful_decisions_and_final_binding import slate  # noqa: F401 -- fixture

SAT = date(2026, 10, 17)
ET_NOON = datetime(2026, 10, 17, 16, 0, tzinfo=UTC)               # 12:00 PM ET


# ------------------------------------------------------------------ world --
def _settings(settings, **overrides):
    return settings.model_copy(update={"shared_daily_allowance_effective_from": "2026-09-01T00:00:00Z",
                                       "nhl_paper_checkpoint_enabled": True, "nhl_paper_decisions_enabled": True,
                                       **overrides})


def _nhl_era(session):
    era = ShadowExperiment(
        name="NHL PRIORITY TEST", version=7001, started_at=datetime(2026, 10, 12, tzinfo=UTC),
        start_date=date(2026, 10, 12), starting_bankroll=1000, sport="NHL",
        recommendation_policy_version=NHL.policy_version, staking_policy_version=NHL.staking_version,
        decision_policy=NHL.policy_version, controlling_engine=NHL.controlling_engine,
        supported_markets=["MONEYLINE"], kelly_fraction=0.25, unit_percentage=0.01, max_position_percentage=0.005,
        max_same_game_percentage=0.01, max_daily_percentage=0.02, code_commit="t", code_identity="t", is_active=True)
    session.add(era)
    session.flush()
    return era


_NHL_PAIRS = (("BOS", "NYR"), ("EDM", "CGY"), ("COL", "VGK"), ("DAL", "MIN"), ("TOR", "MTL"))


def _nhl_game(session, start):
    """An NHL game with an admissible (producer-route) forecast published 9 h before it."""

    from app.models.nhl_research import NhlResearchForecast
    from tests.test_nhl_paper_lifecycle import _forecast

    n = int(session.scalar(select(func.count()).select_from(NhlResearchForecast)) or 0)
    home, away = _NHL_PAIRS[n % len(_NHL_PAIRS)]
    forecast, _block = _forecast(session, source_id=str(2026020300 + n), home=home, away=away, start=start)
    return session.get(Game, forecast.game_id)


def _mlb_world(session, settings, start):
    from tests.test_mlb_v18_funding_pause import V18_ID, _experiment, _game

    era = session.get(ShadowExperiment, V18_ID) or _experiment(session, settings)
    return era, _game(session, starts_at=start)


def _cfb_game(session, start):
    from tests.test_shared_daily_allowance import _game

    return _game(session, start)


def _outcome(session, game):
    market = Market(game_id=game.id, market_type=MarketType.MONEYLINE, period=f"p{uuid.uuid4().hex[:8]}")
    session.add(market)
    session.flush()
    outcome = MarketOutcome(market_id=market.id, outcome_key="HOME", label="HOME")
    session.add(outcome)
    session.flush()
    return outcome


def _decide(session, era, game, *, sport, at, reason="ALL_GATES_PASSED", recommendation=None, stake=None,
            slate=SAT):
    """A recorded FINAL_PREGAME decision (and its position when ``stake``) for ``era`` on ``game``."""

    row = ShadowExperimentDecision(
        experiment_id=era.id, game_id=game.id, slate_date=slate, phase=ExperimentDecisionPhase.FINAL_PREGAME,
        sport=sport, market="MONEYLINE", selection="HOME", recommended_at=at,
        recommendation=recommendation or ("QUALIFIED_BET" if stake else "DECLINED"), recommendation_reason=reason,
        qualification_reasons={"reason": reason, "outcome": "QUALIFIED" if stake else "DECLINED"},
        evidence={"shared_daily_allowance": {"slate_date": slate.isoformat()}})
    session.add(row)
    session.flush()
    if stake:
        session.add(OfficialAspPosition(
            market_outcome_id=_outcome(session, game).id, placed_at=at, stake=stake, decimal_odds=1.95,
            status=PositionStatus.OPEN, result=PositionResult.PENDING, shadow_experiment_id=era.id,
            shadow_decision_id=row.id))
        session.flush()
    return row


def _cfb_bet(session, era, game, at, stake, slate=SAT):
    return _decide(session, era, game, sport="CFB", at=at, stake=stake, slate=slate)


def _entered(session):
    return sda.entered_on(session, SAT)


def _plain_cfb_era(session):
    era = ShadowExperiment(
        name="CFB PRIORITY TEST", version=7002, started_at=datetime(2026, 9, 1, tzinfo=UTC),
        start_date=date(2026, 9, 1), starting_bankroll=1000, sport="CFB",
        recommendation_policy_version="AISP_CFB_PAPER_POLICY_V1", staking_policy_version="AISP_QUARTER_KELLY_CFB_V1",
        decision_policy="AISP_CFB_PAPER_POLICY_V1", controlling_engine="CFB_GEN1_JOINT_BASELINE",
        supported_markets=["MONEYLINE"], kelly_fraction=0.25, unit_percentage=0.01, max_position_percentage=0.005,
        max_same_game_percentage=0.01, max_daily_percentage=0.02, code_commit="t", code_identity="t", is_active=True)
    session.add(era)
    session.flush()
    return era


@pytest.fixture
def saturday(session, settings):
    """3 NHL + 1 MLB pending (the real Saturday shape, shrunk), 11 CFB noon games, $50 already entered."""

    s = _settings(settings)
    nhl_era = _nhl_era(session)
    nhl = [_nhl_game(session, datetime(2026, 10, 17, 17, 0, tzinfo=UTC)),
           _nhl_game(session, datetime(2026, 10, 17, 23, 0, tzinfo=UTC)),
           _nhl_game(session, datetime(2026, 10, 17, 23, 0, tzinfo=UTC))]
    mlb_era, mlb = _mlb_world(session, settings, datetime(2026, 10, 18, 0, 0, tzinfo=UTC))   # 8 PM ET Saturday
    cfb_era = _plain_cfb_era(session)
    morning = _cfb_game(session, datetime(2026, 10, 17, 13, 0, tzinfo=UTC))
    for _ in range(10):
        _cfb_bet(session, cfb_era, morning, datetime(2026, 10, 17, 12, 40, tzinfo=UTC), 5.0)     # $50 entered
    cfb = [_cfb_game(session, ET_NOON) for _ in range(11)]
    session.commit()
    return SimpleNamespace(s=s, nhl_era=nhl_era, nhl=nhl, mlb_era=mlb_era, mlb=mlb, cfb_era=cfb_era, cfb=cfb)


# ------------------------------------------------- the Saturday allocation --
def test_saturday_cfb_room_is_reduced_by_every_pending_mlb_and_nhl_maximum(session, saturday):
    w = saturday
    moment = ET_NOON - timedelta(minutes=30)                                   # 15:30Z, the CFB noon cohort
    first = sda.room(session, w.s, day=SAT, moment=moment, sport="CFB", game_id=w.cfb[0].id)
    # 3 NHL x $5 (policy max_stake) + MLB 1% x $1,000 (the V18 era's own cap) = $25 held.
    assert first["reserved_usd"] == 25.0 and first["reserved_games"] == 4 and first["priority"] is False
    assert first["entered_usd"] == 50.0 and first["room_usd"] == 25.0 and first["room_before_reservations_usd"] == 50.0
    assert first["priority_policy"]["policy"] == "AISP_SHARED_100_PRIORITY_MLB_NHL_V1"
    assert "prioritize MLB and NHL over CFB" in first["priority_policy"]["owner_decision"]
    assert {r["sport"] for r in first["reservations"]} == {"NHL", "MLB"}
    mlb = next(r for r in first["reservations"] if r["sport"] == "MLB")
    assert mlb["reserve_usd"] == 10.0 and mlb["forecast"]["state"] == "AWAITING_V18_PUBLICATION"

    stakes = []
    for game in w.cfb:                                                           # the 11-game cohort, $5 each
        room = sda.room(session, w.s, day=SAT, moment=moment, sport="CFB", game_id=game.id)
        stake = round(min(5.0, room["room_usd"]), 2)
        stakes.append(stake)
        if stake > 0:
            _cfb_bet(session, w.cfb_era, game, moment, stake)
        else:
            assert sda.zero_room_reason(room) == sda.ROOM_RESERVED
        session.commit()
    assert stakes == [5.0] * 5 + [0.0] * 6 and _entered(session) == 75.0


def test_saturday_nhl_and_mlb_are_always_fundable_up_to_their_cap_and_the_total_is_one_hundred(session, saturday):
    w = saturday
    # CFB takes everything it may at noon: $75 entered, $25 still held for MLB/NHL.
    for game in w.cfb:
        room = sda.room(session, w.s, day=SAT, moment=ET_NOON - timedelta(minutes=30), sport="CFB", game_id=game.id)
        if room["room_usd"] > 0:
            _cfb_bet(session, w.cfb_era, game, ET_NOON - timedelta(minutes=30), min(5.0, room["room_usd"]))
    assert _entered(session) == 75.0
    # NHL 17:00Z at T-20: its own hold is what it consumes.
    nhl0 = sda.room(session, w.s, day=SAT, moment=datetime(2026, 10, 17, 16, 40, tzinfo=UTC), sport="NHL",
                    game_id=w.nhl[0].id)
    assert nhl0["priority"] is True and nhl0["room_usd"] >= NHL.max_stake
    _decide(session, w.nhl_era, w.nhl[0], sport="NHL", at=datetime(2026, 10, 17, 16, 40, tzinfo=UTC), stake=5.0)
    # A CFB request in between still cannot touch the remaining holds.
    later = sda.room(session, w.s, day=SAT, moment=datetime(2026, 10, 17, 19, 0, tzinfo=UTC), sport="CFB")
    assert later["room_usd"] == 0.0 and later["reserved_usd"] == 20.0
    for game in w.nhl[1:]:
        at = datetime(2026, 10, 17, 22, 40, tzinfo=UTC)
        room = sda.room(session, w.s, day=SAT, moment=at, sport="NHL", game_id=game.id)
        assert room["room_usd"] >= NHL.max_stake
        _decide(session, w.nhl_era, game, sport="NHL", at=at, stake=5.0)
    at = datetime(2026, 10, 17, 23, 50, tzinfo=UTC)                              # MLB after its T-15 cutoff
    room = sda.room(session, w.s, day=SAT, moment=at, sport="MLB", game_id=w.mlb.id)
    assert room["room_usd"] == 10.0 and room["reserved_usd"] == 0.0
    _decide(session, w.mlb_era, w.mlb, sport="BASEBALL_MLB", at=at - timedelta(minutes=15), stake=10.0)
    assert _entered(session) == 100.0
    assert sda.room(session, w.s, day=SAT, moment=at, sport="CFB")["room_state"] == "EXHAUSTED"


# ------------------------------------------------------------- releases --
def test_an_nhl_hold_releases_on_a_final_decision_or_a_bet_but_not_on_a_revisitable_one(session, saturday):
    w = saturday
    at = ET_NOON - timedelta(minutes=30)
    base = sda.room(session, w.s, day=SAT, moment=at, sport="CFB")["reserved_usd"]
    # A missing price is revisited by a later tick: still held.
    _decide(session, w.nhl_era, w.nhl[1], sport="NHL", at=at, reason="NO_TWO_SIDED_QUOTE")
    assert sda.room(session, w.s, day=SAT, moment=at, sport="CFB")["reserved_usd"] == base
    # A real pass is final: released.
    _decide(session, w.nhl_era, w.nhl[1], sport="NHL", at=at + timedelta(minutes=5), reason="NO_QUALIFYING_EDGE")
    assert sda.room(session, w.s, day=SAT, moment=at, sport="CFB")["reserved_usd"] == base - 5.0
    # A bet: released, and its stake is entered instead.
    _decide(session, w.nhl_era, w.nhl[2], sport="NHL", at=at, stake=3.0)
    out = sda.room(session, w.s, day=SAT, moment=at, sport="CFB")
    assert out["reserved_usd"] == base - 10.0 and out["entered_usd"] == 53.0


def test_a_hold_releases_when_the_game_starts_or_leaves_scheduled(session, saturday):
    w = saturday
    after_first_puck = datetime(2026, 10, 17, 17, 0, 1, tzinfo=UTC)
    held = sda.room(session, w.s, day=SAT, moment=after_first_puck, sport="CFB")
    assert held["reserved_games"] == 3 and held["reserved_usd"] == 20.0         # 17:00Z NHL started
    w.nhl[1].status = GameStatus.POSTPONED
    session.flush()
    assert sda.room(session, w.s, day=SAT, moment=after_first_puck, sport="CFB")["reserved_usd"] == 15.0


def test_mlb_holds_through_its_provisional_window_and_releases_on_a_pass_after_the_cutoff(session, saturday):
    w = saturday
    start = datetime(2026, 10, 18, 0, 0, tzinfo=UTC)

    def mlb_hold(moment):
        return sum(r["reserve_usd"] for r in sda.room(session, w.s, day=SAT, moment=moment, sport="CFB")
                   ["reservations"] if r["sport"] == "MLB")

    # A NO_BET at T-25 is provisional (a later tick can still qualify): still held.
    _decide(session, w.mlb_era, w.mlb, sport="BASEBALL_MLB", at=start - timedelta(minutes=25), reason="no edge",
            recommendation="NO_BET")
    assert mlb_hold(start - timedelta(minutes=20)) == 10.0
    # After the T-15 cutoff with no QUALIFIED_BET at or before it: a pass, released.
    assert mlb_hold(start - timedelta(minutes=10)) == 0.0


def test_mlb_qualified_bet_holds_until_its_position_exists(session, saturday):
    w = saturday
    start = datetime(2026, 10, 18, 0, 0, tzinfo=UTC)
    _decide(session, w.mlb_era, w.mlb, sport="BASEBALL_MLB", at=start - timedelta(minutes=20),
            recommendation="QUALIFIED_BET")
    after_cutoff = start - timedelta(minutes=10)
    hold = [r for r in sda.room(session, w.s, day=SAT, moment=after_cutoff, sport="CFB")["reservations"]
            if r["sport"] == "MLB"]
    assert hold and hold[0]["state"] == "QUALIFIED_AWAITING_POSITION"
    _decide(session, w.mlb_era, w.mlb, sport="BASEBALL_MLB", at=start - timedelta(minutes=19), stake=2.43)
    assert not [r for r in sda.room(session, w.s, day=SAT, moment=after_cutoff, sport="CFB")["reservations"]
                if r["sport"] == "MLB"]


def test_mlb_strict_forecast_mode_and_the_funding_pause(session, saturday, settings):
    w = saturday
    moment = ET_NOON - timedelta(minutes=30)
    strict = _settings(settings, shared_daily_priority_mlb_reserve_before_forecast=False)
    assert not [r for r in sda.room(session, strict, day=SAT, moment=moment, sport="CFB")["reservations"]
                if r["sport"] == "MLB"]
    from tests.test_mlb_v18_funding_pause import _v18_forecast

    forecast = _v18_forecast(session, w.mlb)
    assert forecast.is_ready
    held = [r for r in sda.room(session, strict, day=SAT, moment=moment, sport="CFB")["reservations"]
            if r["sport"] == "MLB"]
    assert held and held[0]["forecast"]["state"] == "V18_FORECAST_HELD"
    paused = _settings(settings, mlb_v18_funding_pause_enabled=True)              # the era funds nothing
    assert not [r for r in sda.room(session, paused, day=SAT, moment=moment, sport="CFB")["reservations"]
                if r["sport"] == "MLB"]


def test_nhl_reserves_nothing_when_its_switches_are_off_and_the_rule_has_a_kill_switch(session, saturday, settings):
    w = saturday
    moment = ET_NOON - timedelta(minutes=30)
    off = _settings(settings, nhl_paper_decisions_enabled=False)
    assert {r["sport"] for r in sda.room(session, off, day=SAT, moment=moment, sport="CFB")["reservations"]} == {"MLB"}
    killed = _settings(settings, shared_daily_priority_reservation_enabled=False)
    out = sda.room(session, killed, day=SAT, moment=moment, sport="CFB")
    assert out["reserved_usd"] == 0.0 and out["room_usd"] == 50.0 and out["priority_policy"]["state"] == "OFF"


def test_the_defaults_are_the_owners_rule_and_the_policy_is_named():
    from app.core.config import Settings

    assert Settings.model_fields[spr.ENABLED_SETTING].default is True
    assert Settings.model_fields[spr.MLB_BEFORE_FORECAST_SETTING].default is True
    assert spr.PRIORITY_SPORTS == ("MLB", "NHL") and spr.POLICY == "AISP_SHARED_100_PRIORITY_MLB_NHL_V1"
    assert sda.SHARED_DAILY_LIMIT_USD == 100.00                                   # the total never moves
    assert spr.is_priority("BASEBALL_MLB") and spr.is_priority("NHL")
    assert not any(spr.is_priority(s) for s in ("CFB", "NFL", "NBA", None))


# ------------------------------------------------- among priority games --
def test_oversubscribed_priority_games_are_first_come_and_never_deadlock(session, settings):
    """Two NHL games share a start and only $5 is left: the first in order gets it, the other waits honestly."""

    s = _settings(settings)
    era = _nhl_era(session)
    start = datetime(2026, 10, 17, 23, 0, tzinfo=UTC)
    a, b = sorted([_nhl_game(session, start), _nhl_game(session, start)], key=lambda g: str(g.id))
    filler_era = _plain_cfb_era(session)
    filler = _cfb_game(session, datetime(2026, 10, 17, 16, 0, tzinfo=UTC))
    for _ in range(19):
        _cfb_bet(session, filler_era, filler, datetime(2026, 10, 17, 15, 30, tzinfo=UTC), 5.0)   # $95 entered
    at = start - timedelta(minutes=20)
    room_b = sda.room(session, s, day=SAT, moment=at, sport="NHL", game_id=b.id)
    room_a = sda.room(session, s, day=SAT, moment=at, sport="NHL", game_id=a.id)
    # The literal "every other priority game" rule would give BOTH zero; ordering gives the first one the $5.
    assert room_a["room_usd"] == 5.0 and room_b["room_usd"] == 0.0
    assert sda.zero_room_reason(room_b) == sda.ROOM_RESERVED
    _decide(session, era, a, sport="NHL", at=at, stake=5.0)
    room_b = sda.room(session, s, day=SAT, moment=at, sport="NHL", game_id=b.id)
    assert room_b["room_usd"] == 0.0 and sda.zero_room_reason(room_b) == sda.ROOM_EXHAUSTED


# ------------------------------------------------- lock and serialization --
def test_the_lock_is_taken_before_the_total_and_the_reservations_are_read():
    calls = []

    class _Dialect:
        name = "postgresql"

    class _Session:
        def get_bind(self):
            return SimpleNamespace(dialect=_Dialect())

        def flush(self):
            calls.append("flush")

        def execute(self, statement, params=None):
            calls.append(str(statement))
            return SimpleNamespace(all=lambda: [])

    s = SimpleNamespace(**{sda.SETTING: "2026-10-10T18:00:00+00:00"})
    out = sda.room(_Session(), s, day=SAT, moment=ET_NOON, sport="CFB", game_id=uuid.uuid4())
    assert "pg_advisory_xact_lock" in calls[0] and calls[1] == "flush" and "official_asp_positions" in calls[2]
    assert "games" in calls[3] and "leagues" in calls[3]                       # reservations read under the lock
    assert out["lock"] == "pg_advisory_xact_lock" and out["room_usd"] == 100.0


def test_requests_serialize_a_second_session_sees_the_first_commit(session, saturday):
    """READ COMMITTED after the lock: a request made after another commits sizes against its stake."""

    from sqlalchemy.orm import Session as OrmSession

    w = saturday
    moment = ET_NOON - timedelta(minutes=30)
    other = OrmSession(bind=session.get_bind(), autoflush=False)
    try:
        first = sda.room(session, w.s, day=SAT, moment=moment, sport="CFB", game_id=w.cfb[0].id)
        _cfb_bet(session, w.cfb_era, w.cfb[0], moment, min(5.0, first["room_usd"]))
        session.commit()                                                     # releases the lock with the write
        second = sda.room(other, w.s, day=SAT, moment=moment, sport="CFB", game_id=w.cfb[1].id)
        assert second["entered_usd"] == first["entered_usd"] + 5.0
        assert second["room_usd"] == first["room_usd"] - 5.0
    finally:
        other.rollback()
        other.close()


@pytest.mark.parametrize("seed", [1, 7, 42, 2026])
def test_the_total_never_exceeds_one_hundred_and_fitting_priority_games_are_always_funded(session, settings, seed):
    rng = random.Random(seed)
    s = _settings(settings)
    nhl_era = _nhl_era(session)
    mlb_era, mlb = _mlb_world(session, settings, datetime(2026, 10, 18, 0, 0, tzinfo=UTC))
    starts = [datetime(2026, 10, 17, h, m, tzinfo=UTC) for h, m in ((17, 0), (19, 30), (22, 0), (23, 0), (23, 0))]
    nhl = [_nhl_game(session, start) for start in starts]
    cfb_era = _plain_cfb_era(session)
    events = []
    for _ in range(30):                                                       # CFB requests across the day
        start = datetime(2026, 10, 17, rng.choice([16, 17, 19, 20, 23]), rng.choice([0, 30]), tzinfo=UTC)
        events.append((start - timedelta(minutes=rng.choice([30, 25, 20])), "CFB", _cfb_game(session, start)))
    for game in nhl:
        events.append((game.starts_at.replace(tzinfo=UTC) - timedelta(minutes=20), "NHL", game))
    events.append((datetime(2026, 10, 17, 23, 50, tzinfo=UTC), "MLB", mlb))
    events.sort(key=lambda e: (e[0], e[1]))
    funded = {}
    for at, sport, game in events:
        room = sda.room(session, s, day=SAT, moment=at, sport=sport, game_id=game.id)
        cap = {"CFB": 5.0, "NHL": NHL.max_stake, "MLB": 10.0}[sport]
        if sport != "CFB" and rng.random() < 0.3:                              # a priority pass releases its hold
            _decide(session, nhl_era if sport == "NHL" else mlb_era, game,
                    sport="NHL" if sport == "NHL" else "BASEBALL_MLB", at=at, reason="NO_QUALIFYING_EDGE",
                    recommendation="NO_BET" if sport == "MLB" else None)
            continue
        stake = round(min(cap, room["room_usd"]), 2)
        funded[str(game.id)] = (sport, stake, cap)
        if stake > 0:
            era = {"CFB": cfb_era, "NHL": nhl_era, "MLB": mlb_era}[sport]
            _decide(session, era, game, sport={"MLB": "BASEBALL_MLB"}.get(sport, sport),
                    at=at - (timedelta(minutes=15) if sport == "MLB" else timedelta()), stake=stake)
        session.commit()
        assert _entered(session) <= 100.0 + 1e-9
    # Every priority game that wanted to bet got its full cap: 5 x $5 + $10 <= $100 always fits.
    for sport, stake, cap in funded.values():
        if sport != "CFB":
            assert stake == cap, (sport, stake, cap)


# ------------------------------------------------- honest reasons, end to end --
def test_cfb_reserved_reason_semantics_and_successor(world, era, checkpoint):
    from app.cfb import paper_decision
    from tests.test_cfb_daily_limit_amendment import _decision_401, _enter, _qualifying_price

    moment, game = checkpoint                                         # Sat 2026-09-26 19:10Z, CFB kickoff 19:30Z
    world.settings.shared_daily_allowance_effective_from = moment - timedelta(minutes=5)
    world.settings.nhl_paper_checkpoint_enabled = True
    world.settings.nhl_paper_decisions_enabled = True
    nhl_era = _nhl_era(world.session)
    nhl = [_nhl_game(world.session, datetime(2026, 9, 26, 23, 0, tzinfo=UTC)) for _ in range(2)]
    _enter(world, era, moment, [PositionStatus.OPEN] * 18)                         # CFB $90 entered
    _qualifying_price(world, game, moment)
    first = _decision_401(paper_decision.evaluate(world.session, world.settings, now=moment))
    assert (first["outcome"], first["reason"]) == ("DECLINED", "SHARED_ROOM_RESERVED_FOR_PRIORITY_SPORTS")
    shared = first["shared_daily_allowance"]
    assert (shared["entered_usd"], shared["reserved_usd"], shared["reserved_games"], shared["room_usd"],
            shared["priority"]) == (90.0, 10.0, 2, 0.0, False)
    assert shared["priority_policy"]["policy"] == "AISP_SHARED_100_PRIORITY_MLB_NHL_V1"
    assert "held for 2 MLB/NHL game(s)" in first["detail"]
    row = world.session.get(ShadowExperimentDecision, uuid.UUID(first["decision_id"]))
    assert row.qualification_reasons["reason"] == "SHARED_ROOM_RESERVED_FOR_PRIORITY_SPORTS"
    assert row.evidence["shared_daily_allowance"]["reserved_usd"] == 10.0
    semantics = paper_decision.checkpoint_semantics(row.recommendation, row.qualification_reasons["reason"],
                                                    row.recommendation_reason)
    assert semantics["class"] == "NO_ROOM" and semantics["priced"] is True
    # The same hold at the next tick reuses the row.
    again = _decision_401(paper_decision.evaluate(world.session, world.settings, now=moment + timedelta(minutes=2)))
    assert again["persisted"] == "REUSED_EXISTING_DECISION"
    # One NHL game passes: its $5 hold frees, and the next tick funds the CFB value as a successor row.
    _decide(world.session, nhl_era, nhl[0], sport="NHL", at=moment, reason="NO_QUALIFYING_EDGE",
            slate=date(2026, 9, 26))
    world.session.commit()
    later = _decision_401(paper_decision.evaluate(world.session, world.settings, now=moment + timedelta(minutes=4)))
    assert later["outcome"] == "QUALIFIED" and later["position_id"] and later["persisted"] == "RECORDED"
    assert later["selection"]["terms"]["stake"] == 5.0
    assert sda.entered_on(world.session, date(2026, 9, 26)) == 95.0


def test_legacy_budget_only_rows_read_as_no_room_not_no_bet():
    from app.cfb.paper_decision import checkpoint_semantics

    legacy = checkpoint_semantics("DECLINED", "NO_QUALIFYING_EDGE", "Best side HOME at fanduel: every cap resolved to zero.")
    assert legacy["class"] == "NO_ROOM"
    assert checkpoint_semantics("DECLINED", "NO_QUALIFYING_EDGE", "edge +0.0100 below the 0.02 minimum")["class"] == "NO_BET"
    assert checkpoint_semantics("DECLINED", "SHARED_ALLOWANCE_EXHAUSTED")["class"] == "NO_ROOM"


def test_nhl_waits_honestly_behind_an_earlier_priority_game_then_bets(session, settings):
    from app.nhl import paper
    from tests.test_nhl_paper_lifecycle import DECIDE, START, _evaluate, _ready

    s, _forecast_row, _game = _ready(session, settings)
    s = s.model_copy(update={"nhl_paper_checkpoint_enabled": True})
    nhl_era = paper.experiment(session)
    earlier = _nhl_game(session, START - timedelta(minutes=10))                  # undecided, ordered first
    filler_era = _plain_cfb_era(session)
    filler = _cfb_game(session, START - timedelta(hours=4))
    day = date(2026, 10, 20)
    for _ in range(19):
        _cfb_bet(session, filler_era, filler, START - timedelta(hours=5), 5.0, slate=day)   # $95 entered on 10-20
    session.commit()
    out = _evaluate(session, s, now=DECIDE)
    blocked = [d for d in out["decisions"] if d["game_id"] != str(earlier.id)]
    # (the earlier game is outside its own window at DECIDE, so only TOR-MTL is evaluated)
    assert blocked and blocked[0]["reason"] == "SHARED_ROOM_RESERVED_FOR_PRIORITY_SPORTS", blocked[0].get("detail")
    assert blocked[0]["shared_daily_allowance"]["priority"] is True
    assert blocked[0]["shared_daily_allowance"]["reserved_games"] == 1
    assert day.isoformat() == blocked[0]["shared_daily_allowance"]["slate_date"]
    # The earlier game passes; the next tick revisits the hold and funds TOR-MTL.
    _decide(session, nhl_era, earlier, sport="NHL", at=DECIDE, reason="NO_QUALIFYING_EDGE", slate=day)
    session.commit()
    later = _evaluate(session, s, now=DECIDE + timedelta(minutes=3))
    funded = [d for d in later["decisions"] if d["game_id"] != str(earlier.id)][0]
    assert funded["outcome"] == "QUALIFIED" and funded["position_id"] and funded["persisted"] == "RECORDED"
    assert 0 < funded["selection"]["terms"]["stake"] <= 5.0


def test_mlb_funding_is_a_priority_request_and_its_disposition_is_persisted(session, settings):
    from app.services.shadow_experiment_service import FUNDING_RECEIPT_ACTION, POSITION_CREATED
    from tests.test_mlb_v18_funding_pause import START, _dispose, _v18_world
    from tests.test_shared_daily_allowance import _other_sport_position

    s = _settings(settings)
    experiment, game, decision = _v18_world(session, s)
    later_nhl_era = _nhl_era(session)
    _nhl_game(session, START + timedelta(hours=3))                               # later priority game: not counted
    _other_sport_position(session, starts_at=START - timedelta(hours=2), stake=95.0)   # CFB-like $95 entered
    assert _dispose(session, s, experiment, game) == POSITION_CREATED
    position = session.scalar(select(OfficialAspPosition).where(OfficialAspPosition.shadow_decision_id == decision.id))
    assert float(position.stake) == 5.0                                          # min(policy $10, room $5)
    receipt = session.scalar(select(AuditEvent).where(AuditEvent.action == FUNDING_RECEIPT_ACTION))
    meta = receipt.metadata_json
    assert meta["disposition"] == POSITION_CREATED and meta["reason"] == "POSITION_TAKEN" and meta["stake"] == 5.0
    assert meta["shared_daily_allowance"]["priority"] is True and meta["shared_daily_allowance"]["room_usd"] == 5.0
    assert meta["shared_daily_allowance"]["priority_policy"]["policy"] == "AISP_SHARED_100_PRIORITY_MLB_NHL_V1"
    assert later_nhl_era is not None


def test_mlb_not_funded_by_an_exhausted_allowance_says_so_once(session, settings):
    from app.services.shadow_experiment_service import FUNDING_RECEIPT_ACTION, POSITION_NOT_FUNDED
    from tests.test_mlb_v18_funding_pause import START, _dispose, _v18_world
    from tests.test_shared_daily_allowance import _other_sport_position

    s = _settings(settings)
    experiment, game, decision = _v18_world(session, s)
    _other_sport_position(session, starts_at=START - timedelta(hours=2), stake=100.0)
    assert _dispose(session, s, experiment, game) == POSITION_NOT_FUNDED
    assert _dispose(session, s, experiment, game) == POSITION_NOT_FUNDED           # a repeat tick writes nothing new
    receipts = list(session.scalars(select(AuditEvent).where(AuditEvent.action == FUNDING_RECEIPT_ACTION)))
    assert len(receipts) == 1
    meta = receipts[0].metadata_json
    assert (meta["disposition"], meta["reason"], meta["stake"]) == (POSITION_NOT_FUNDED, "SHARED_ALLOWANCE_EXHAUSTED", 0.0)
    assert meta["decision_id"] == str(decision.id) and meta["limits"]["daily_cap"] == 0.0
    assert meta["shared_daily_allowance"]["entered_usd"] == 100.0
    assert session.scalar(select(func.count()).select_from(OfficialAspPosition).where(
        OfficialAspPosition.shadow_decision_id == decision.id)) == 0


def test_nfl_room_reserved_is_honest_reused_while_held_and_funded_once_freed(session, settings, slate):
    from app.services import nfl_paper_decision as nfl
    from tests.test_nfl_truthful_decisions_and_final_binding import KICKOFF, TICK, qualifying
    from tests.test_shared_daily_allowance import _other_sport_position

    ctx = slate
    s = _settings(settings)
    nhl_era = _nhl_era(session)
    sunday = date(2026, 9, 20)
    held = [_nhl_game(session, datetime(2026, 9, 20, 23, 0, tzinfo=UTC)) for _ in range(2)]
    _other_sport_position(session, starts_at=KICKOFF - timedelta(hours=1), stake=90.0)
    entry = ctx["games"][0]
    first = nfl.record_outcome(session, experiment=ctx["era"], game_id=entry["game"].id,
                               decision=qualifying(entry, TICK), moment=TICK, settings=s)
    session.commit()
    assert (first["outcome"], first["reason"]) == ("DECLINED", "SHARED_ROOM_RESERVED_FOR_PRIORITY_SPORTS")
    row = session.get(ShadowExperimentDecision, uuid.UUID(first["decision_id"]))
    assert row.execution_outcome == "NOT_EXECUTED"
    assert row.execution_reason == "SHARED_ROOM_RESERVED_FOR_PRIORITY_SPORTS"
    assert row.qualification_reason == "ALL_GATES_PASSED"
    assert row.staking_detail["shared_daily_allowance"]["reserved_usd"] == 10.0
    # Still held at the next tick: the stored row is reused, nothing new is written.
    again = nfl.record_outcome(session, experiment=ctx["era"], game_id=entry["game"].id,
                               decision=qualifying(entry, TICK + timedelta(minutes=2)),
                               moment=TICK + timedelta(minutes=2), settings=s)
    session.commit()
    assert again["persisted"] == "REUSED_EXISTING_DECISION"
    # An NHL game passes; the hold frees and the next tick funds the NFL value.
    _decide(session, nhl_era, held[0], sport="NHL", at=TICK, reason="NO_QUALIFYING_EDGE", slate=sunday)
    session.commit()
    funded = nfl.record_outcome(session, experiment=ctx["era"], game_id=entry["game"].id,
                                decision=qualifying(entry, TICK + timedelta(minutes=4)),
                                moment=TICK + timedelta(minutes=4), settings=s)
    session.commit()
    assert funded["persisted"] == "RECORDED" and funded["position_id"]
    assert session.scalar(select(func.count()).select_from(ShadowExperimentDecision).where(
        ShadowExperimentDecision.game_id == entry["game"].id)) == 2
    assert sda.entered_on(session, sunday) == 95.0


def test_a_reservation_defect_fails_open_to_the_plain_shared_rule_and_keeps_the_transaction(session, saturday,
                                                                                         monkeypatch):
    w = saturday

    def broken(*_a, **_k):
        raise RuntimeError("defect")

    monkeypatch.setattr(spr, "_nhl_pending", broken)
    out = sda.room(session, w.s, day=SAT, moment=ET_NOON - timedelta(minutes=30), sport="CFB")
    assert out["priority_policy"]["state"] == "ERROR:RuntimeError"
    assert out["reserved_usd"] == 0.0 and out["room_usd"] == 50.0                # never more than $100 - entered
    assert sda.entered_on(session, SAT) == 50.0                                  # the session still works

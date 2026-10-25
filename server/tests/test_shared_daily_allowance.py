"""Owner's shared cross-sport daily allowance (2026-10-10): one $100 per Eastern slate date.

Replaces each official sport's aggregate daily ceiling from its effective clock;
every official stake on that Eastern date counts, any sport, any status; settling
or voiding frees nothing; before the clock nothing changes. Offline (SQLite);
the PostgreSQL advisory lock is checked for ORDER with a recording session.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.cfb import paper_decision
from app.core.config import Settings
from app.models import (
    Game, League, Market, MarketOutcome, MarketType, OfficialAspPosition, PositionResult, PositionStatus, Sport,
)
from app.services import shared_daily_allowance as sda
from tests.test_cfb_consumer_activation import era, world  # noqa: F401
from tests.test_cfb_daily_limit_amendment import (  # noqa: F401
    _decision_401, _enter, _qualifying_price, checkpoint,
)

SAT = date(2026, 10, 10)


def _other_sport_position(session, *, starts_at: datetime, stake: float, status=PositionStatus.OPEN):
    """An official position on a game in a different league (no experiment needed for the pool)."""

    sport = session.query(Sport).filter_by(code="ice_hockey_test").one_or_none() or Sport(code="ice_hockey_test",
                                                                                        name="Hockey")
    session.add(sport)
    session.flush()
    league = session.query(League).filter_by(code="NHLTEST").one_or_none() or League(
        sport_id=sport.id, code="NHLTEST", name="Test league")
    session.add(league)
    session.flush()
    game = Game(league_id=league.id, starts_at=starts_at)
    session.add(game)
    session.flush()
    market = Market(game_id=game.id, market_type=MarketType.MONEYLINE, period=f"p{uuid.uuid4().hex[:6]}")
    session.add(market)
    session.flush()
    outcome = MarketOutcome(market_id=market.id, outcome_key="HOME", label="HOME")
    session.add(outcome)
    session.flush()
    session.add(OfficialAspPosition(market_outcome_id=outcome.id, placed_at=starts_at - timedelta(hours=1),
                                    stake=stake, decimal_odds=1.9, status=status,
                                    result=PositionResult.PENDING if status == PositionStatus.OPEN
                                    else PositionResult.PUSH))
    session.flush()


# -- the clock -------------------------------------------------------------------------
def test_setting_defaults_unset_and_unset_or_unreadable_changes_nothing(session):
    assert Settings.model_fields[sda.SETTING].default is None
    now = datetime(2026, 10, 10, 18, tzinfo=UTC)
    for s in (None, SimpleNamespace(), SimpleNamespace(**{sda.SETTING: None}),
              SimpleNamespace(**{sda.SETTING: ""}), SimpleNamespace(**{sda.SETTING: "nope"})):
        assert sda.room(session, s, day=SAT, moment=now) is None


def test_before_the_clock_none_and_at_it_one_hundred(session):
    clock = datetime(2026, 10, 10, 18, 5, tzinfo=UTC)
    s = SimpleNamespace(**{sda.SETTING: clock.isoformat()})
    assert sda.room(session, s, day=SAT, moment=clock - timedelta(seconds=1)) is None
    out = sda.room(session, s, day=SAT, moment=clock)
    assert out["limit_usd"] == 100.0 and out["room_usd"] == 100.0 and out["entered_usd"] == 0.0
    assert out["basis"] == sda.BASIS and out["effective_from"] == clock.isoformat()


# -- the pool --------------------------------------------------------------------------
def test_entered_counts_every_sport_and_status_by_eastern_kickoff_date(session):
    # Sat 8 PM ET (Sun 00:00Z) counts on Saturday; Sun 1 PM ET does not; Fri 11 PM ET does not.
    _other_sport_position(session, starts_at=datetime(2026, 10, 11, 0, 0, tzinfo=UTC), stake=5.0)
    _other_sport_position(session, starts_at=datetime(2026, 10, 10, 16, 0, tzinfo=UTC), stake=5.0,
                          status=PositionStatus.SETTLED)
    _other_sport_position(session, starts_at=datetime(2026, 10, 10, 17, 0, tzinfo=UTC), stake=2.5,
                          status=PositionStatus.VOID)
    _other_sport_position(session, starts_at=datetime(2026, 10, 11, 17, 0, tzinfo=UTC), stake=5.0)
    _other_sport_position(session, starts_at=datetime(2026, 10, 10, 3, 0, tzinfo=UTC), stake=5.0)
    assert sda.entered_on(session, SAT) == 12.5


def test_the_lock_is_taken_before_the_total_is_read():
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
    out = sda.room(_Session(), s, day=SAT, moment=datetime(2026, 10, 10, 19, tzinfo=UTC))
    # Lock first; then pending entries are flushed; then the total is read.
    assert "pg_advisory_xact_lock" in calls[0] and calls[1] == "flush" and "official_asp_positions" in calls[2]
    assert out["lock"] == "pg_advisory_xact_lock"


# -- end to end through the CFB checkpoint ----------------------------------------------
def test_cfb_before_the_clock_keeps_its_own_daily_rule(world, era, checkpoint):
    moment, game = checkpoint
    world.settings.cfb_daily_limit_amendment_effective_from = moment - timedelta(days=2)
    setattr(world.settings, sda.SETTING, moment + timedelta(minutes=1))           # not yet
    _enter(world, era, moment, [PositionStatus.OPEN] * 9 + [PositionStatus.SETTLED])   # $50 under the CFB $50 rule
    _qualifying_price(world, game, moment)
    decision = _decision_401(paper_decision.evaluate(world.session, world.settings, now=moment))
    assert (decision["outcome"], decision["reason"]) == ("DECLINED", "NO_QUALIFYING_EDGE")
    assert decision["daily_exposure"]["limit_usd"] == 50.0 and "shared_daily_allowance" not in decision


def test_cfb_after_the_clock_fifty_prior_entered_leaves_fifty_shared(world, era, checkpoint):
    moment, game = checkpoint
    world.settings.cfb_daily_limit_amendment_effective_from = moment - timedelta(days=2)
    setattr(world.settings, sda.SETTING, moment - timedelta(minutes=5))
    _enter(world, era, moment, [PositionStatus.OPEN] * 8 + [PositionStatus.SETTLED, PositionStatus.VOID])  # $50
    _qualifying_price(world, game, moment)
    decision = _decision_401(paper_decision.evaluate(world.session, world.settings, now=moment))
    assert decision["outcome"] == "QUALIFIED" and decision["position_id"]
    assert decision["daily_exposure"] == {"basis": sda.BASIS, "stake_entered_usd": 50.0, "limit_usd": 100.0}
    assert decision["shared_daily_allowance"]["room_usd"] == 50.0
    assert decision["selection"]["terms"]["stake"] == 5.0          # per-position rule unchanged
    position = world.session.get(OfficialAspPosition, uuid.UUID(decision["position_id"]))
    assert float(position.stake) == 5.0


def test_cfb_other_sport_stakes_on_the_same_eastern_date_use_the_same_pool(world, era, checkpoint):
    moment, game = checkpoint
    setattr(world.settings, sda.SETTING, moment - timedelta(minutes=5))
    kickoff = paper_decision.utc_instant(world.session.get(Game, game["game_id"]).starts_at
                                         if isinstance(game, dict) else game.starts_at)
    _enter(world, era, moment, [PositionStatus.OPEN] * 10)                         # CFB $50
    _other_sport_position(world.session, starts_at=kickoff, stake=47.5,
                          status=PositionStatus.SETTLED)                            # another sport, $47.50
    _qualifying_price(world, game, moment)
    decision = _decision_401(paper_decision.evaluate(world.session, world.settings, now=moment))
    assert decision["shared_daily_allowance"]["entered_usd"] == 97.5
    assert decision["outcome"] == "QUALIFIED" and decision["selection"]["terms"]["stake"] == 2.5


def test_cfb_at_one_hundred_entered_across_sports_declines(world, era, checkpoint):
    moment, game = checkpoint
    setattr(world.settings, sda.SETTING, moment - timedelta(minutes=5))
    kickoff = paper_decision.utc_instant(world.session.get(Game, game["game_id"]).starts_at
                                         if isinstance(game, dict) else game.starts_at)
    _enter(world, era, moment, [PositionStatus.OPEN] * 10)
    _other_sport_position(world.session, starts_at=kickoff, stake=50.0, status=PositionStatus.VOID)
    _qualifying_price(world, game, moment)
    decision = _decision_401(paper_decision.evaluate(world.session, world.settings, now=moment))
    # INTENTIONALLY CHANGED 2026-10-16 (audit item 13, AISP_SHARED_100_PRIORITY_MLB_NHL_V1): the value
    # qualified and only the fully entered shared $100 sized it to zero, so the honest reason is
    # SHARED_ALLOWANCE_EXHAUSTED (was NO_QUALIFYING_EDGE), and it reads as NO_ROOM, never a no-bet.
    assert (decision["outcome"], decision["reason"]) == ("DECLINED", "SHARED_ALLOWANCE_EXHAUSTED")
    assert decision["shared_daily_allowance"]["room_usd"] == 0.0
    assert paper_decision.checkpoint_semantics("DECLINED", decision["reason"])["class"] == "NO_ROOM"


# -- every official writer uses the pool; nothing else does -------------------------------
def test_every_official_position_writer_consults_the_shared_allowance():
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    writers = sorted(str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*.py")
                     if re.search(r"\bOfficialAspPosition\(", p.read_text(encoding="utf-8"))
                     and not str(p).replace("\\", "/").endswith("models/entities.py"))
    assert writers == ["bankroll/positions.py", "cfb/paper_decision.py", "nba/gen1_paper.py", "nhl/paper.py",
                       "services/nfl_paper_decision.py", "services/shadow_experiment_service.py"]
    for rel in writers:
        assert "shared_daily_allowance" in (root / rel).read_text(encoding="utf-8"), rel
    # Community / House Divided wagers are a different table and never reach the pool.
    hd = [p for p in root.rglob("*.py") if "house_divided" in str(p).lower()]
    assert hd and all("shared_daily_allowance" not in p.read_text(encoding="utf-8") for p in hd)


# -- MAIN d605b8c review: R1 pending batch entries, R2 immutable charged slate ----------------
def _decided_position(session, *, exp, game, slate: date, stake: float, sport="CFB", status=PositionStatus.OPEN,
                      evidence=None, staking_detail=None, placed_at=None):
    """An official position with its decision row (the decision carries the slate it was charged to)."""

    from app.models import ShadowExperimentDecision

    market = Market(game_id=game.id, market_type=MarketType.MONEYLINE, period=f"d{uuid.uuid4().hex[:6]}")
    session.add(market)
    session.flush()
    outcome = MarketOutcome(market_id=market.id, outcome_key="HOME", label="HOME")
    session.add(outcome)
    session.flush()
    placed = placed_at or datetime.combine(slate, datetime.min.time(), tzinfo=sda.ET).astimezone(UTC) + timedelta(hours=15)
    decision = ShadowExperimentDecision(
        experiment_id=exp.id, game_id=game.id, slate_date=slate, phase="FINAL_PREGAME", sport=sport,
        market="MONEYLINE", selection="HOME", recommended_at=placed, recommendation="QUALIFIED_BET",
        recommendation_reason="seed", evidence=evidence, staking_detail=staking_detail)
    session.add(decision)
    session.flush()
    session.add(OfficialAspPosition(market_outcome_id=outcome.id, placed_at=placed, stake=stake, decimal_odds=1.9,
                                    status=status, shadow_decision_id=decision.id,
                                    shadow_experiment_id=exp.id,
                                    result=PositionResult.PENDING if status == PositionStatus.OPEN
                                    else PositionResult.PUSH))
    return decision


def _game(session, starts_at):
    sport = session.query(Sport).filter_by(code="football_test").one_or_none() or Sport(code="football_test", name="F")
    session.add(sport)
    session.flush()
    league = session.query(League).filter_by(code="CFBTEST").one_or_none() or League(
        sport_id=sport.id, code="CFBTEST", name="t")
    session.add(league)
    session.flush()
    game = Game(league_id=league.id, starts_at=starts_at)
    session.add(game)
    session.flush()
    return game


def test_r1_pending_batch_entries_count_and_the_batch_stops_at_one_hundred(session, era):
    """Same session, autoflush OFF (as production): two 2.50 candidates after 97.50 entered -> total 100.00."""

    session.autoflush = False
    clock = datetime(2026, 10, 10, 18, tzinfo=UTC)
    s = SimpleNamespace(**{sda.SETTING: clock})
    game = _game(session, datetime(2026, 10, 10, 19, 30, tzinfo=UTC))
    for _ in range(19):
        _decided_position(session, exp=era, game=game, slate=SAT, stake=5.0)
    _decided_position(session, exp=era, game=game, slate=SAT, stake=2.5)
    session.flush()                                                   # 97.50 persisted
    admitted = []
    for _ in range(2):                                                # a legacy-style batch: add, no flush between
        room = sda.room(session, s, day=SAT, moment=clock)
        stake = round(min(2.5, room["room_usd"]), 2)
        admitted.append(stake)
        if stake > 0:
            _decided_position(session, exp=era, game=game, slate=SAT, stake=stake)   # pending, not flushed
    assert admitted == [2.5, 0.0]
    assert sda.entered_on(session, SAT) == 100.0


def test_r2_moving_a_saturday_kickoff_to_sunday_keeps_its_stake_on_saturday(session, era):
    game = _game(session, datetime(2026, 10, 10, 23, 30, tzinfo=UTC))           # Sat 7:30 PM ET
    for _ in range(10):
        _decided_position(session, exp=era, game=game, slate=SAT, stake=5.0, status=PositionStatus.SETTLED)
    session.flush()
    assert sda.entered_on(session, SAT) == 50.0
    game.starts_at = datetime(2026, 10, 11, 17, 0, tzinfo=UTC)                   # retimed to Sun 1 PM ET
    session.flush()
    sun = date(2026, 10, 11)
    assert sda.entered_on(session, SAT) == 50.0 and sda.entered_on(session, sun) == 0.0
    # A genuinely new Sunday entry on the retimed game is charged to Sunday only.
    _decided_position(session, exp=era, game=game, slate=sun, stake=5.0,
                      evidence={"shared_daily_allowance": {"slate_date": sun.isoformat()}})
    assert sda.entered_on(session, sun) == 5.0 and sda.entered_on(session, SAT) == 50.0


def test_r2_nfl_entries_use_the_recorded_charge_or_count_on_every_candidate():
    # NFL decision slate_date is the UTC date of the decision moment: with no recorded charge it is ambiguous
    # and charged to every retained candidate; with the pool's recorded slate it is exact.
    utc_next_day = date(2026, 10, 11)
    placed = datetime(2026, 10, 11, 0, 50, tzinfo=UTC)                          # Sat 8:50 PM ET
    dates, unclear = sda.charged_dates(sport="NFL", decision_slate=utc_next_day, evidence=None,
                                       staking_detail=None, placed_at=placed)
    assert dates == {SAT, utc_next_day} and unclear is True
    dates, unclear = sda.charged_dates(sport="NFL", decision_slate=utc_next_day, evidence=None,
                                       staking_detail={"shared_daily_allowance": {"slate_date": "2026-10-10"}},
                                       placed_at=placed)
    assert dates == {SAT} and unclear is False
    assert sda.charged_dates(sport="CFB", decision_slate=SAT, evidence=None, staking_detail=None,
                             placed_at=placed) == (frozenset({SAT}), False)

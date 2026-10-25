"""AISP_CFB_PAPER_POLICY_V1__STAKING_AMENDMENT_001__DAILY_LIMIT_USD_50 -- prospective, append-only.

The frozen CFB record keeps its 2%-of-bankroll daily limit counted on OPEN
positions. The owner's amendment replaces ONLY the daily limit with a flat $50.00
per game-slate date, counting every stake entered on that date whatever its
status, and only for decisions at or after CFB_DAILY_LIMIT_AMENDMENT_EFFECTIVE_FROM.
Offline, SQLite only.
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.cfb import paper_decision, production_import
from app.core.config import Settings
from app.models import (
    Game, Market, MarketOutcome, MarketType, OfficialAspPosition, PositionResult, PositionStatus,
    ShadowExperimentDecision,
)
from app.services.research_paper_policy import (
    CFB, DAILY_BASIS_ENTERED, DAILY_BASIS_OPEN, NBA, STAKING_AMENDMENTS, effective_daily_limit,
    moneyline_terms, policy_config,
)
from tests.test_cfb_consumer_activation import _favourite_side, _in_window, _moneyline, era, world  # noqa: F401
from tests.test_cfb_production_discovery import KICK

AMENDMENT = "AISP_CFB_PAPER_POLICY_V1__STAKING_AMENDMENT_001__DAILY_LIMIT_USD_50"
SETTING = "cfb_daily_limit_amendment_effective_from"
EFFECTIVE = SimpleNamespace(**{SETTING: KICK - timedelta(days=1)})


def _terms(*, daily_exposure, bankroll=1000.0, daily_limit_usd=None):
    # A clear edge whose quarter Kelly is far above $5: a per-position cap binds unless the daily cap is lower.
    return moneyline_terms(CFB, win_probability=0.60, decimal_odds=2.2, opposite_decimal_odds=1.7,
                           bankroll=bankroll, daily_exposure=daily_exposure, daily_limit_usd=daily_limit_usd)


def _run_day(positions, *, already=0.0, daily_limit_usd=None, bankroll=1000.0):
    """Successive qualifiers on one slate date after ``already`` entered; each one's stake."""

    exposure, stakes = already, []
    for _ in range(positions):
        stake = _terms(daily_exposure=exposure, bankroll=bankroll, daily_limit_usd=daily_limit_usd)["stake"]
        stakes.append(stake)
        exposure += stake
    return stakes


# -- the amendment record and the frozen policy ----------------------------------
def test_the_amendment_is_appended_and_the_frozen_record_is_unchanged():
    assert [a.amendment_id for a in STAKING_AMENDMENTS] == [AMENDMENT]
    amendment = STAKING_AMENDMENTS[0]
    assert (amendment.sport, amendment.policy_version, amendment.staking_version) == (
        "CFB", "AISP_CFB_PAPER_POLICY_V1", "AISP_QUARTER_KELLY_CFB_V1")
    assert (amendment.replaces_max_daily_percentage, amendment.daily_limit_usd) == (0.02, 50.00)
    assert amendment.daily_exposure_basis == DAILY_BASIS_ENTERED
    assert amendment.effective_setting == SETTING
    assert (CFB.max_daily_percentage, CFB.max_stake, CFB.max_position_percentage,
            CFB.max_same_game_percentage, CFB.kelly_fraction, CFB.min_adjusted_edge) == (
        0.02, 5.00, 0.005, 0.01, 0.25, 0.02)
    config = policy_config(CFB, code_commit="c", code_identity="i")
    assert config["max_daily_percentage"] == 0.02 and config["max_stake"] == 5.00
    assert "staking_amendments" not in config and "daily_limit_usd" not in config


def test_the_setting_is_unset_by_default():
    assert Settings.model_fields[SETTING].default is None


@pytest.mark.parametrize("settings", [None, SimpleNamespace(), SimpleNamespace(**{SETTING: None}),
                                      SimpleNamespace(**{SETTING: ""}), SimpleNamespace(**{SETTING: "not a clock"})])
def test_unset_or_unreadable_setting_changes_nothing(settings):
    assert effective_daily_limit(CFB, settings, moment=KICK) == (None, DAILY_BASIS_OPEN, [])


def test_before_the_clock_nothing_and_at_it_the_flat_limit_with_its_basis():
    clock = KICK - timedelta(hours=1)
    settings = SimpleNamespace(**{SETTING: clock})
    assert effective_daily_limit(CFB, settings, moment=clock - timedelta(seconds=1)) == (None, DAILY_BASIS_OPEN, [])
    limit, basis, applied = effective_daily_limit(CFB, settings, moment=clock)
    assert (limit, basis) == (50.00, DAILY_BASIS_ENTERED)
    assert applied == [{"amendment_id": AMENDMENT,
                        "daily_limit": {"from": "2% of bankroll", "to_usd": 50.00},
                        "daily_exposure_basis": DAILY_BASIS_ENTERED,
                        "daily_exposure_rule": "all positions entered on the slate date, any status",
                        "effective_from": clock.isoformat(), "authorized_on": "2026-09-26"}]
    # An ISO string with an offset is the same instant.
    assert effective_daily_limit(CFB, SimpleNamespace(**{SETTING: clock.isoformat()}), moment=clock)[0] == 50.00


def test_the_amendment_never_reaches_nba():
    assert effective_daily_limit(NBA, EFFECTIVE, moment=KICK) == (None, DAILY_BASIS_OPEN, [])


# -- the stake arithmetic -----------------------------------------------------------
def test_frozen_limit_fifth_five_dollar_qualifier_of_the_day_declines():
    assert _run_day(5) == [5.0] * 4 + [0.0]
    fifth = _terms(daily_exposure=20.0)
    assert fifth["reason"] == "every cap resolved to zero" and fifth["limits"]["daily_cap"] == 0.0


def test_flat_limit_allows_ten_five_dollar_positions_and_the_eleventh_declines():
    assert _run_day(11, daily_limit_usd=50.0) == [5.0] * 10 + [0.0]
    eleventh = _terms(daily_exposure=50.0, daily_limit_usd=50.0)
    assert eleventh["qualifies"] is False and eleventh["reason"] == "every cap resolved to zero"
    # The per-position maximum never moves.
    assert _terms(daily_exposure=0.0, daily_limit_usd=50.0)["stake"] == 5.0
    assert _terms(daily_exposure=0.0, daily_limit_usd=50.0)["limits"]["max_stake"] == 5.0


def test_with_five_dollars_already_entered_nine_more_fit_and_the_next_declines():
    assert _run_day(10, already=5.0, daily_limit_usd=50.0) == [5.0] * 9 + [0.0]


@pytest.mark.parametrize("bankroll", [1100.0, 900.0, 1000.0])
def test_the_flat_limit_does_not_scale_with_the_bankroll(bankroll):
    assert _terms(daily_exposure=0.0, bankroll=bankroll, daily_limit_usd=50.0)["limits"]["daily_cap"] == 50.0
    assert _terms(daily_exposure=45.0, bankroll=bankroll, daily_limit_usd=50.0)["limits"]["daily_cap"] == 5.0
    # The frozen rule, by contrast, is 2% of whatever the bankroll is.
    assert _terms(daily_exposure=0.0, bankroll=bankroll)["limits"]["daily_cap"] == pytest.approx(0.02 * bankroll)
    # Position sizing still follows the bankroll exactly as before (0.5% position cap, $5 max).
    assert _terms(daily_exposure=0.0, bankroll=bankroll, daily_limit_usd=50.0)["stake"] == min(
        5.0, round(0.005 * bankroll, 2))


# -- end to end through the CFB checkpoint -----------------------------------------------
def _qualifying_price(world, game, moment):
    side, _p = _favourite_side(world)
    home, away = (200, -260) if side == "HOME" else (-260, 200)
    _moneyline(world.session, game, home=home, away=away, at=moment - timedelta(minutes=10))


def _enter(world, era, moment, statuses):
    """Positions already entered on game 401's slate date (filed on game 402), each $5, with ``statuses``."""

    other = world.session.get(Game, production_import.current_forecast_for_game(
        world.session, "402").canonical_game_id)
    day = paper_decision.slate_date({"starts_at": paper_decision.utc_instant(other.starts_at)})
    for i, status in enumerate(statuses):
        # One seed outcome per position (a position is unique per experiment and outcome).
        market = Market(game_id=other.id, market_type=MarketType.MONEYLINE, period=f"seed_{i}")
        world.session.add(market)
        world.session.flush()
        outcome = MarketOutcome(market_id=market.id, outcome_key="HOME", label="HOME")
        world.session.add(outcome)
        world.session.flush()
        row = ShadowExperimentDecision(
            experiment_id=era.id, game_id=other.id, slate_date=day, phase=paper_decision.PHASE, sport="CFB",
            market="MONEYLINE", selection="HOME", recommended_at=moment - timedelta(hours=5),
            recommendation="QUALIFIED_BET", recommendation_reason="seed",
            qualification_reasons={"reason": "ALL_GATES_PASSED", "outcome": "QUALIFIED"})
        world.session.add(row)
        world.session.flush()
        world.session.add(OfficialAspPosition(
            market_outcome_id=outcome.id, placed_at=moment - timedelta(hours=5), stake=5.00,
            decimal_odds=1.91, status=status,
            result=PositionResult.PENDING if status == PositionStatus.OPEN else PositionResult.PUSH,
            strategy=era.staking_policy_version, shadow_experiment_id=era.id, shadow_decision_id=row.id,
            bankroll_before_position=1000.0))
    world.session.flush()


def _decision_401(result):
    return next(d for d in result["decisions"] if d["source_game_id"] == "401")


@pytest.fixture
def checkpoint(world, era, monkeypatch):
    moment, game = _in_window(world, era, monkeypatch)
    monkeypatch.setattr(paper_decision, "_bankroll", lambda *_a: 1000.0)
    return moment, game


def test_pre_effective_the_frozen_open_only_rule_is_unchanged(world, era, checkpoint):
    moment, game = checkpoint
    world.settings.cfb_daily_limit_amendment_effective_from = moment + timedelta(minutes=1)   # not yet
    # $20 entered, but three are SETTLED: the frozen rule counts only the open $5 -> $5 stake, no amendment.
    _enter(world, era, moment, [PositionStatus.OPEN] + [PositionStatus.SETTLED] * 3)
    _qualifying_price(world, game, moment)
    decision = _decision_401(paper_decision.evaluate(world.session, world.settings, now=moment))
    assert decision["outcome"] == "QUALIFIED" and decision["selection"]["terms"]["stake"] == 5.0
    assert decision["selection"]["terms"]["limits"]["daily_cap"] == 15.0
    assert "staking_amendments" not in decision and "daily_exposure" not in decision


def test_pre_effective_four_open_positions_decline_at_twenty_as_before(world, era, checkpoint):
    moment, game = checkpoint
    _enter(world, era, moment, [PositionStatus.OPEN] * 4)
    _qualifying_price(world, game, moment)
    decision = _decision_401(paper_decision.evaluate(world.session, world.settings, now=moment))
    assert (decision["outcome"], decision["reason"]) == ("DECLINED", "NO_QUALIFYING_EDGE")
    assert "every cap resolved to zero" in decision["detail"]
    assert "staking_amendments" not in decision


def test_post_effective_hawaii_like_five_dollars_counts_and_the_amendment_is_recorded(world, era, checkpoint):
    moment, game = checkpoint
    world.settings.cfb_daily_limit_amendment_effective_from = moment - timedelta(hours=1)
    _enter(world, era, moment, [PositionStatus.OPEN])
    _qualifying_price(world, game, moment)
    decision = _decision_401(paper_decision.evaluate(world.session, world.settings, now=moment))
    assert decision["outcome"] == "QUALIFIED" and decision["position_id"]
    assert decision["selection"]["terms"]["limits"]["daily_cap"] == 45.0
    assert decision["daily_exposure"] == {"basis": DAILY_BASIS_ENTERED, "stake_entered_usd": 5.0, "limit_usd": 50.0}
    position = world.session.get(OfficialAspPosition, uuid.UUID(decision["position_id"]))
    assert float(position.stake) == 5.00
    row = world.session.get(ShadowExperimentDecision, uuid.UUID(decision["decision_id"]))
    applied = row.evidence["staking_amendments"]
    assert [a["amendment_id"] for a in applied] == [AMENDMENT]
    assert applied[0]["daily_limit"]["to_usd"] == 50.0
    assert applied[0]["daily_exposure_rule"] == "all positions entered on the slate date, any status"


def test_post_effective_settled_and_void_stakes_entered_that_date_still_count(world, era, checkpoint):
    moment, game = checkpoint
    world.settings.cfb_daily_limit_amendment_effective_from = moment - timedelta(hours=1)
    # $50 entered: 8 open, 1 settled, 1 void. Settling frees nothing -> the next qualifier declines.
    _enter(world, era, moment, [PositionStatus.OPEN] * 8 + [PositionStatus.SETTLED, PositionStatus.VOID])
    _qualifying_price(world, game, moment)
    decision = _decision_401(paper_decision.evaluate(world.session, world.settings, now=moment))
    assert (decision["outcome"], decision["reason"]) == ("DECLINED", "NO_QUALIFYING_EDGE")
    assert "every cap resolved to zero" in decision["detail"]
    assert decision["daily_exposure"]["stake_entered_usd"] == 50.0
    assert world.session.scalar(select(func.count()).select_from(OfficialAspPosition)) == 10


def test_post_effective_a_settled_five_dollars_counts_toward_the_limit(world, era, checkpoint):
    moment, game = checkpoint
    world.settings.cfb_daily_limit_amendment_effective_from = moment - timedelta(hours=1)
    _enter(world, era, moment, [PositionStatus.SETTLED])
    _qualifying_price(world, game, moment)
    decision = _decision_401(paper_decision.evaluate(world.session, world.settings, now=moment))
    assert decision["outcome"] == "QUALIFIED"
    assert decision["selection"]["terms"]["limits"]["daily_cap"] == 45.0     # not 50: the settled $5 counts


def test_an_earlier_declined_decision_is_not_reopened_by_the_amendment(world, era, checkpoint):
    moment, game = checkpoint
    _enter(world, era, moment, [PositionStatus.OPEN] * 4)
    _qualifying_price(world, game, moment)
    first = _decision_401(paper_decision.evaluate(world.session, world.settings, now=moment))
    assert first["reason"] == "NO_QUALIFYING_EDGE"
    world.settings.cfb_daily_limit_amendment_effective_from = moment       # activated afterwards
    again = _decision_401(paper_decision.evaluate(world.session, world.settings, now=moment + timedelta(minutes=2)))
    assert again["persisted"] == "REUSED_EXISTING_DECISION" and again["position_id"] is None
    assert world.session.scalar(select(func.count()).select_from(OfficialAspPosition)) == 4

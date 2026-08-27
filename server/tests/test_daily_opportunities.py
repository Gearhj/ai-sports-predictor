"""Governed daily-opportunity pricing, selection, staking, and market mapping.

These exercise the pure opportunity engine (no DB, no provider) plus the
read-service outcome→selection mapping. They assert the board reuses the shadow
policy verbatim: a selection is priced only from a real, fresh two-sided quote,
QUALIFIED requires readiness + positive EV + the uncertainty-adjusted edge, and
virtual staking respects the per-position, same-game, and daily exposure caps.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.bankroll.service import StakePolicy, StakingStrategy
from app.forecast_exchange.pricing import ShadowPricingConfig
from app.models import MarketType
from app.services.opportunity_service import (
    PROVISIONAL_EDGE,
    BookQuote,
    MarketInput,
    SideInput,
    allocate_stakes,
    build_market_candidate,
    compute_opportunity_score,
    rank_candidates,
)
from app.services.read_service import ReadService


NOW = datetime(2026, 8, 27, 18, 0, tzinfo=UTC)
CONFIG = ShadowPricingConfig(qualified_edge=0.02, watch_edge=0.02)


def _quote(book: str, side: int, opposite: int, *, minutes_old: int = 5) -> BookQuote:
    return BookQuote(book=book, side_american=side, opposite_american=opposite,
                     observed_at=NOW - timedelta(minutes=minutes_old))


def _side(selection: str, model_p: float, quotes, *, line=None, haircut: float = 0.02) -> SideInput:
    conservative = None if model_p is None else max(0.0, min(1.0, model_p - haircut))
    return SideInput(selection=selection, line=line, model_probability=model_p,
                     conservative_probability=conservative, quotes=tuple(quotes))


def _candidate(selection: str, model_p: float, quotes, *, is_ready=True, critical_ok=True, market="Moneyline", line=None):
    market_input = MarketInput(market_label=market, period="Full game",
                               sides=(_side(selection, model_p, quotes, line=line),))
    return build_market_candidate(market_input, readiness_ok=is_ready, critical_inputs_ok=critical_ok,
                                  config=CONFIG, now=NOW)


# --------------------------------------------------------------------------
# pricing + recommendation classification
# --------------------------------------------------------------------------

def test_qualified_bet_uses_best_book_and_price():
    candidate = _candidate(
        "SF ML", 0.60,
        [_quote("BookA", 100, -120), _quote("BookB", 110, -130)],
    )
    assert candidate is not None
    assert candidate.recommendation == "QUALIFIED_BET"
    # The best book maximizes the bettor's price.
    assert candidate.book == "BookB"
    assert candidate.american_odds == 110
    assert candidate.decimal_odds == 2.10
    assert candidate.expected_value > 0
    assert candidate.uncertainty_adjusted_edge >= CONFIG.qualified_edge
    assert candidate.fair_odds is not None


def test_watch_is_disagreement_without_qualifying():
    # Positive no-vig edge (>2%) but the uncertainty-adjusted edge does not clear
    # the qualified bar: a WATCH, never an actionable bet.
    candidate = _candidate("SF ML", 0.53, [_quote("BookA", -110, -110)])
    assert candidate is not None
    assert candidate.recommendation == "WATCH"


def test_no_bet_when_model_trails_market():
    candidate = _candidate("SF ML", 0.40, [_quote("BookA", -110, -110)])
    assert candidate is not None
    assert candidate.recommendation == "NO_BET"


def test_readiness_gate_demotes_qualified_to_provisional():
    ready = _candidate("SF ML", 0.60, [_quote("BookA", 110, -130)], is_ready=True)
    not_ready = _candidate("SF ML", 0.60, [_quote("BookA", 110, -130)], is_ready=False)
    assert ready.recommendation == "QUALIFIED_BET"
    # Same attractive price, but readiness below the bar → PROVISIONAL, not qualified.
    assert not_ready.recommendation == PROVISIONAL_EDGE


def test_incomplete_critical_inputs_yield_provisional():
    # Readiness clears the bar but a critical input (lineup/starter) is missing.
    candidate = _candidate("SF ML", 0.60, [_quote("BookA", 110, -130)], is_ready=True, critical_ok=False)
    assert candidate.recommendation == PROVISIONAL_EDGE
    assert candidate.is_ready is False
    assert candidate.uncertainty_adjusted_edge is not None  # the edge is still shown


def test_provisional_edge_is_not_staked():
    provisional = _candidate("SF ML", 0.75, [_quote("BookA", 120, -140)], is_ready=False)
    assert provisional.recommendation == PROVISIONAL_EDGE
    # Only genuinely QUALIFIED bets receive a virtual position.
    allocate_stakes([("g1", provisional)] if provisional.recommendation == "QUALIFIED_BET" else [],
                    bankroll=1000.0, policy=_policy(), same_game_cap=20.0, daily_cap=100.0)
    assert provisional.virtual_stake == 0.0


def test_missing_quote_is_not_priced():
    market = MarketInput("Moneyline", "Full game", (_side("SF ML", 0.6, []),))
    assert build_market_candidate(market, readiness_ok=True, critical_inputs_ok=True, config=CONFIG, now=NOW) is None


def test_stale_quote_is_forced_to_no_bet():
    candidate = _candidate("SF ML", 0.60, [_quote("BookA", 110, -130, minutes_old=600)])
    assert candidate is not None
    assert candidate.is_stale is True
    assert candidate.recommendation == "NO_BET"
    assert candidate.uncertainty_adjusted_edge is not None  # edge still shown for context


def test_build_market_candidate_picks_the_better_side():
    market = MarketInput("Moneyline", "Full game", (
        _side("SF ML", 0.60, [_quote("BookA", 110, -130)]),
        _side("LAD ML", 0.40, [_quote("BookA", -130, 110)]),
    ))
    candidate = build_market_candidate(market, readiness_ok=True, critical_inputs_ok=True, config=CONFIG, now=NOW)
    assert candidate.selection == "SF ML"


# --------------------------------------------------------------------------
# ordering / grouping
# --------------------------------------------------------------------------

def test_rank_orders_qualified_then_watch_then_no_bet():
    qualified = _candidate("Total Over 7", 0.66, [_quote("BookA", -110, -110)], market="Total", line=7.0)
    watch = _candidate("SF ML", 0.53, [_quote("BookA", -110, -110)])
    no_bet = _candidate("LAD ML", 0.40, [_quote("BookA", -110, -110)])
    ordered = rank_candidates([no_bet, watch, qualified])
    assert [c.recommendation for c in ordered] == ["QUALIFIED_BET", "WATCH", "NO_BET"]


def test_rank_breaks_ties_by_uncertainty_adjusted_ev():
    strong = _candidate("A", 0.70, [_quote("BookA", 120, -140)])
    modest = _candidate("B", 0.60, [_quote("BookA", 110, -130)])
    ordered = rank_candidates([modest, strong])
    assert ordered[0].selection == "A"
    assert ordered[0].uncertainty_adjusted_ev >= ordered[1].uncertainty_adjusted_ev


# --------------------------------------------------------------------------
# opportunity score
# --------------------------------------------------------------------------

def _score(rec, *, ev=0.10, readiness=80.0, age=300, unc=0.03, disagree=5.0, staked=True):
    return compute_opportunity_score(
        recommendation=rec, uncertainty_adjusted_ev=ev, readiness=readiness,
        quote_age_seconds=age, forecast_uncertainty=unc, model_disagreement=disagree,
        has_virtual_position=staked,
    )


def test_opportunity_score_respects_tier_bands():
    # Tier ordering holds even when a lower tier has stronger raw inputs.
    qualified = _score("QUALIFIED_BET", ev=0.02, readiness=60)
    provisional = _score(PROVISIONAL_EDGE, ev=0.5, readiness=100)
    watch = _score("WATCH", ev=0.5, readiness=100)
    assert qualified > provisional > watch


def test_opportunity_score_rewards_readiness_and_freshness():
    strong = _score("QUALIFIED_BET", readiness=95, age=60, unc=0.01)
    weak = _score("QUALIFIED_BET", readiness=70, age=13000, unc=0.10)
    assert strong > weak


def test_opportunity_score_penalizes_capped_out_exposure():
    staked = _score("QUALIFIED_BET", staked=True)
    capped = _score("QUALIFIED_BET", staked=False)
    assert staked > capped


# --------------------------------------------------------------------------
# virtual payouts + exposure caps
# --------------------------------------------------------------------------

def _policy() -> StakePolicy:
    return StakePolicy(strategy=StakingStrategy.FRACTIONAL_KELLY,
                       kelly_fraction=0.25, maximum_stake_percentage=0.01)


def test_positive_and_negative_american_payouts():
    plus = _candidate("Plus", 0.62, [_quote("BookA", 150, -175)])
    minus = _candidate("Minus", 0.80, [_quote("BookA", -200, 170)])
    allocate_stakes(
        [("g1", plus), ("g2", minus)],
        bankroll=1000.0, policy=_policy(), same_game_cap=20.0, daily_cap=100.0,
    )
    # +150 → decimal 2.5: profit is 1.5x stake; total return 2.5x stake.
    assert plus.decimal_odds == 2.5
    assert plus.potential_profit == round(plus.virtual_stake * 1.5, 2)
    assert plus.total_return == round(plus.virtual_stake * 2.5, 2)
    # -200 → decimal 1.5: profit is 0.5x stake.
    assert minus.decimal_odds == 1.5
    assert minus.potential_profit == round(minus.virtual_stake * 0.5, 2)


def test_daily_and_same_game_exposure_caps_bind():
    # Three qualified selections, each large enough to hit the 1% position cap
    # ($10). The $25 daily cap must bind before the third is fully funded.
    a = _candidate("A", 0.75, [_quote("BookA", 120, -140)])
    b = _candidate("B", 0.72, [_quote("BookA", 115, -135)])
    c = _candidate("C", 0.70, [_quote("BookA", 110, -130)])
    exposure = allocate_stakes(
        [("g1", a), ("g2", b), ("g3", c)],
        bankroll=1000.0, policy=_policy(), same_game_cap=20.0, daily_cap=25.0,
    )
    assert a.virtual_stake == 10.0
    assert b.virtual_stake == 10.0
    assert c.virtual_stake == 5.0  # daily cap leaves only $5 for the third
    assert exposure == 25.0


def test_same_game_cap_limits_two_positions_on_one_game():
    a = _candidate("A", 0.75, [_quote("BookA", 120, -140)])
    b = _candidate("B", 0.74, [_quote("BookA", 118, -138)])
    allocate_stakes(
        [("g1", a), ("g1", b)],
        bankroll=1000.0, policy=_policy(), same_game_cap=15.0, daily_cap=100.0,
    )
    assert a.virtual_stake + b.virtual_stake <= 15.0


# --------------------------------------------------------------------------
# outcome → selection mapping (moneyline / run line / total)
# --------------------------------------------------------------------------

HOME = SimpleNamespace(id="home-id", abbreviation="SF")
AWAY = SimpleNamespace(id="away-id", abbreviation="LAD")
SIM = {
    "home_cover_probabilities": {"-1.5": 0.42},
    "away_cover_probabilities": {"+1.5": 0.58},
    "total_probabilities": {"8.5": {"over": 0.55, "under": 0.45}},
}


def _resolve(market_type, *, participant=None, label=None, point=None):
    outcome = SimpleNamespace(participant_team_id=participant, label=label)
    snapshot = SimpleNamespace(point=point)
    return ReadService._resolve_side(
        market_type, outcome, snapshot, 0.60, 0.40, SIM, HOME, AWAY,
    )


def test_resolve_moneyline_sides():
    assert _resolve(MarketType.MONEYLINE, participant="home-id") == ("HOME", "SF ML", None, 0.60)
    assert _resolve(MarketType.MONEYLINE, participant="away-id") == ("AWAY", "LAD ML", None, 0.40)


def test_resolve_run_line_uses_cover_probabilities():
    assert _resolve(MarketType.SPREAD, participant="home-id", point=-1.5) == ("HOME", "SF -1.5", -1.5, 0.42)
    assert _resolve(MarketType.SPREAD, participant="away-id", point=1.5) == ("AWAY", "LAD +1.5", 1.5, 0.58)


def test_resolve_total_uses_over_under_probabilities():
    over = _resolve(MarketType.TOTAL, label="over", point=8.5)
    under = _resolve(MarketType.TOTAL, label="under", point=8.5)
    assert over == ("OVER", "Over 8.5", 8.5, 0.55)
    assert under[:3] == ("UNDER", "Under 8.5", 8.5)
    assert abs(under[3] - 0.45) < 1e-9

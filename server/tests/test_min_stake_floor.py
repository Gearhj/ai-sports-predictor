"""Owner rule 2026-10-25: no paper bet under $1.00 (bets of $0.01 and $0.19 had been placed from the last cents of
the shared $100)."""

from app.services.research_paper_policy import (
    MIN_STAKE_USD, STAKE_BELOW_MINIMUM, below_minimum, below_minimum_detail, moneyline_terms,
)
from app.nhl.paper import POLICY as NHL_POLICY


def _terms(daily_limit):
    # a clear edge: model 55% on a +120 / -140 pair
    return moneyline_terms(NHL_POLICY, win_probability=0.55, decimal_odds=2.20, opposite_decimal_odds=1.714,
                           bankroll=5000.0, daily_limit_usd=daily_limit)


def test_stake_under_a_dollar_is_not_placed_and_says_why():
    t = _terms(0.19)
    assert t["qualifies"] is False and t["stake"] == 0.0
    assert below_minimum(t) and t["sized_stake"] == 0.19
    assert "under the $1.00 minimum" in t["reason"]
    assert "$0.19" in below_minimum_detail(t, "NHL")


def test_a_dollar_or_more_is_placed():
    t = _terms(1.00)
    assert t["qualifies"] is True and t["stake"] == 1.00 and not below_minimum(t)
    assert _terms(None)["stake"] >= MIN_STAKE_USD


def test_reason_code_is_stable():
    assert STAKE_BELOW_MINIMUM == "STAKE_BELOW_MINIMUM"

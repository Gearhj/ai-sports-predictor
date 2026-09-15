"""NBA and CFB paper policies: the maths, the caps, and the eligibility gate."""
from __future__ import annotations

import pytest

from app.services.research_paper_policy import CFB, NBA, POLICIES, moneyline_terms


def test_both_policies_hold_the_authorised_boundaries():
    for policy in (NBA, CFB):
        assert policy.markets == ("MONEYLINE",)
        assert policy.kelly_fraction == 0.25
        assert policy.max_position_percentage == 0.005
        assert policy.max_stake == 5.00
        assert policy.max_same_game_percentage == 0.01
        assert policy.max_daily_percentage == 0.02
        assert policy.min_adjusted_edge == 0.02
        assert policy.authority["real_money_execution"] is False
        assert policy.authority["execution_outside_aisp"] is False
        assert policy.authority["sportsgameodds_governed_provider"] is False


def test_nba_controls_on_the_baseline_not_the_close_score_challenger():
    assert NBA.controlling_configuration == "NBA_GEN1_JOINT_FINAL_PAIRED_OOF_LATTICE_V1"
    assert "CLOSE" not in NBA.controlling_configuration


def test_nba_uses_the_program_split_year_season_id():
    assert NBA.season_id == "2026-27" and CFB.season_id == "2026"


def test_engine_ids_fit_the_column():
    assert all(len(p.controlling_engine) <= 40 for p in POLICIES.values())


def test_edge_is_measured_against_no_vig_not_raw_implied():
    # Pittsburgh joint 0.7692 against a -250 / +200 pair.
    terms = moneyline_terms(CFB, win_probability=0.7692, decimal_odds=1.40,
                            opposite_decimal_odds=3.00, bankroll=1000.0)
    implied, other = 1 / 1.40, 1 / 3.00
    assert terms["no_vig_probability"] == pytest.approx(implied / (implied + other))
    assert terms["edge"] == pytest.approx(0.7692 - implied / (implied + other))


def test_expected_return_and_quarter_kelly_for_a_zero_tie_sport():
    terms = moneyline_terms(NBA, win_probability=0.60, decimal_odds=2.10,
                            opposite_decimal_odds=1.80, bankroll=1000.0)
    ev = 0.60 * 1.10 - 0.40
    assert terms["expected_return_per_dollar"] == pytest.approx(ev)
    assert terms["quarter_kelly_fraction"] == pytest.approx((ev / 1.10) * 0.25)


def test_the_five_dollar_cap_binds_before_quarter_kelly():
    terms = moneyline_terms(NBA, win_probability=0.60, decimal_odds=2.10,
                            opposite_decimal_odds=1.80, bankroll=1000.0)
    assert terms["qualifies"] and terms["stake"] == 5.00
    assert terms["bound_by"] in ("max_stake", "position_cap")


def test_an_edge_under_two_points_does_not_qualify():
    terms = moneyline_terms(NBA, win_probability=0.51, decimal_odds=1.95,
                            opposite_decimal_odds=1.95, bankroll=1000.0)
    assert not terms["qualifies"] and terms["stake"] == 0.0
    assert "below the 0.02 minimum" in terms["reason"]


def test_daily_exposure_caps_the_stake_to_zero():
    terms = moneyline_terms(NBA, win_probability=0.60, decimal_odds=2.10,
                            opposite_decimal_odds=1.80, bankroll=1000.0, daily_exposure=20.0)
    assert terms["stake"] == 0.0 and not terms["qualifies"]

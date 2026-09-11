"""The two return measures, and the denominator that separates them.

The ledger already computed a field called ``roi``. It divides realized profit
by EVERY dollar staked, including money still riding on an unsettled position,
and the home page rendered it as "ROI" beside a bankroll that had moved a third
of a percent. A reader could only conclude the bankroll was up eleven percent.

So there are now two measures that name what they divide by, and these tests
pin both against a ledger whose arithmetic can be checked by hand.
"""
from __future__ import annotations

import pytest

from app.services.shadow_experiment_service import ShadowExperimentService


def measures(starting: float, settled: list[float], settled_stake: float,
             open_stake: float) -> dict[str, float]:
    """The arithmetic the ledger performs, restated independently."""

    net = round(sum(settled), 2)
    total_risked = settled_stake + open_stake
    return {
        "net": net,
        "bankroll": round(starting + net, 2),
        "bankroll_return_pct": round(net / starting * 100, 3),
        "return_on_settled_stake_pct": round(net / settled_stake * 100, 2),
        "legacy_roi": round(net / total_risked * 100, 2),
    }


#: The live V2.1 ledger: twelve settlements, one still open at $2.50.
SETTLED = [-2.50, 3.58, 4.00, -2.50, -2.50, -2.50, -2.50, 4.70, -2.50, 4.60, -2.50, 4.07]


def test_the_two_measures_are_not_the_same_number():
    result = measures(1000.0, SETTLED, settled_stake=30.0, open_stake=2.50)
    assert result["net"] == 3.45
    assert result["bankroll"] == 1003.45
    # The bankroll moved a third of a percent.
    assert result["bankroll_return_pct"] == pytest.approx(0.345, abs=1e-6)
    # The settled stake returned eleven and a half.
    assert result["return_on_settled_stake_pct"] == pytest.approx(11.5, abs=1e-6)
    # And the legacy figure is a third thing entirely, which is the problem.
    assert result["legacy_roi"] == pytest.approx(10.62, abs=1e-6)
    assert result["legacy_roi"] != result["return_on_settled_stake_pct"]


def test_open_stake_only_moves_the_legacy_figure():
    """The open $2.50 is the entire difference between 11.50% and 10.62%."""

    with_open = measures(1000.0, SETTLED, settled_stake=30.0, open_stake=2.50)
    without_open = measures(1000.0, SETTLED, settled_stake=30.0, open_stake=0.0)
    # Removing the open position changes nothing that is realized...
    assert with_open["net"] == without_open["net"]
    assert with_open["bankroll_return_pct"] == without_open["bankroll_return_pct"]
    assert with_open["return_on_settled_stake_pct"] == without_open["return_on_settled_stake_pct"]
    # ...and moves the legacy figure onto the settled measure, revealing that
    # the only thing it ever measured was how much money happened to be tied up.
    assert without_open["legacy_roi"] == pytest.approx(11.5, abs=1e-6)


def test_the_bankroll_is_exactly_start_plus_realized_profit():
    result = measures(1000.0, SETTLED, settled_stake=30.0, open_stake=2.50)
    running = 1000.0
    for profit in SETTLED:
        running = round(running + profit, 2)
    assert running == result["bankroll"] == 1003.45


def test_a_losing_ledger_reports_both_measures_negative():
    losing = [-2.50] * 6
    result = measures(1000.0, losing, settled_stake=15.0, open_stake=0.0)
    assert result["net"] == -15.0
    assert result["bankroll"] == 985.0
    assert result["bankroll_return_pct"] == pytest.approx(-1.5, abs=1e-6)
    assert result["return_on_settled_stake_pct"] == pytest.approx(-100.0, abs=1e-6)


def test_drawdown_is_peak_to_trough_after_the_peak():
    """$1,005.08 down to $995.08 is ten dollars, not the distance from $1,000."""

    running = 1000.0
    peak = running
    max_drawdown = 0.0
    trough = running
    for profit in SETTLED:
        running = round(running + profit, 2)
        peak = max(peak, running)
        trough = min(trough, running)
        max_drawdown = max(max_drawdown, peak - running)
    assert peak == pytest.approx(1005.08, abs=1e-6)
    assert trough == pytest.approx(995.08, abs=1e-6)
    assert max_drawdown == pytest.approx(10.0, abs=1e-6)
    assert round(max_drawdown / peak * 100, 3) == pytest.approx(0.995, abs=1e-3)


def test_profit_concentration_is_computed_against_net_not_gross():
    """At this sample the top two wins exceed the whole net profit."""

    wins = sorted((profit for profit in SETTLED if profit > 0), reverse=True)
    net = round(sum(SETTLED), 2)
    concentration = round(sum(wins[:2]) / net * 100, 1)
    assert wins[:2] == [4.70, 4.60]
    # 9.30 / 3.45 — over 100%, meaning the experiment is only ahead because of
    # those two results. The page is required to say so.
    assert concentration == pytest.approx(269.6, abs=0.1)
    assert concentration > 100


def test_the_service_exposes_the_named_measures():
    """Guard against the fields being renamed out from under the read model."""

    import inspect

    source = inspect.getsource(ShadowExperimentService.ledger_view)
    for field in (
        "bankroll_return_pct", "return_on_settled_stake_pct",
        "settled_stake", "open_exposure", "lowest_bankroll",
        "top_two_wins_share_of_profit_pct",
    ):
        assert f'"{field}"' in source, f"{field} is no longer published by the ledger"

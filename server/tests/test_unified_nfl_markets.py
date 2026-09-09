"""Only the changed push and timestamp failure cases; DB/provider-free."""
from datetime import UTC, datetime, timedelta

import pytest

from app.services.nfl_market import Cell, outcome_probabilities
from app.services.nfl_read_service import NflReadService


def _entries(now):
    return [{"book": "Fixture", "side": side, "point": 40.0, "_observed": now,
             "observed_at": now.isoformat(), "american": -110, "decimal": 1 + 100 / 110}
            for side in ("OVER", "UNDER")]


def test_whole_total_edge_is_conditional_but_ev_keeps_push():
    now = datetime(2026, 9, 9, 16, tzinfo=UTC)
    cells = [Cell(24, 20, .45), Cell(17, 20, .35), Cell(20, 20, .2)]
    result = NflReadService(None, None)._market_block(
        "TOTAL", _entries(now), cells, outcome_probabilities(cells), now - timedelta(minutes=240))
    assert result["model"] == pytest.approx({"a": .45, "b": .35, "push": .2})
    assert result["model_comparable"] == pytest.approx({"a": .5625, "b": .4375})
    assert result["edge"] == pytest.approx({"a": .0625, "b": -.0625})
    assert result["expected_value"]["a"] == pytest.approx(.45 * (100 / 110) - .35)
    entries = _entries(now)
    for entry, side in zip(entries, ("HOME", "AWAY")):
        entry.update(side=side, point=None)
    moneyline = NflReadService(None, None)._market_block(
        "MONEYLINE", entries, cells, outcome_probabilities(cells), now - timedelta(minutes=240))
    assert moneyline["model"]["a"] == pytest.approx(.5625)
    # Unit-stake return is not conditional: the 20% tied games refund the stake.
    assert moneyline["expected_value"]["a"] == pytest.approx(.45 * (100 / 110) - .35)


def test_one_future_dated_side_never_makes_a_pair_fresh():
    now = datetime(2026, 9, 9, 16, tzinfo=UTC)
    entries = _entries(now)
    entries[1]["_observed"] = now + timedelta(minutes=1)
    result = NflReadService(None, None)._market_block("TOTAL", entries, None, None, now - timedelta(minutes=240))
    assert not result["fresh"] and not result["comparison_available"]
    assert "future-dated" in result["reason"]
    assert "edge" not in result

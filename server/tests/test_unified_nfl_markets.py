"""Only the changed push and timestamp failure cases; DB/provider-free."""
from datetime import UTC, datetime, timedelta

import pytest

from app.services.nfl_market import Cell, outcome_probabilities
from app.services.nfl_read_service import NflReadService


def _entries(now):
    # Shaped like NflReadService._latest_quotes rows: each carries its stored
    # outcome identity (market_outcomes.outcome_key, NOT NULL), e.g. "OVER:40.0".
    return [{"book": "Fixture", "side": side, "outcome_key": f"{side}:40.0", "point": 40.0,
             "_observed": now, "observed_at": now.isoformat(), "american": -110,
             "decimal": 1 + 100 / 110}
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
        entry.update(side=side, point=None, outcome_key=f"{side}:None")
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


def test_a_quote_without_its_outcome_identity_is_excluded_by_name():
    now = datetime(2026, 9, 9, 16, tzinfo=UTC)
    entries = _entries(now)
    del entries[0]["outcome_key"]
    result = NflReadService(None, None)._market_block("TOTAL", entries, None, None, now - timedelta(minutes=240))
    assert result["comparison_available"] is False and result["selected"] is None
    assert "missing or inconsistent outcome identity" in result["reason"]


def test_a_quote_whose_identity_disagrees_with_its_side_is_excluded():
    now = datetime(2026, 9, 9, 16, tzinfo=UTC)
    entries = _entries(now)
    entries[0]["outcome_key"] = "UNDER:40.0"  # entry says OVER
    result = NflReadService(None, None)._market_block("TOTAL", entries, None, None, now - timedelta(minutes=240))
    assert result["comparison_available"] is False
    assert "1 quote(s) excluded" in result["reason"]


def test_moneyline_spread_and_total_stay_distinct_markets():
    now = datetime(2026, 9, 9, 16, tzinfo=UTC)
    spread = [{"book": "Fixture", "side": side, "outcome_key": f"{side}:{point}", "point": point,
               "_observed": now, "observed_at": now.isoformat(), "american": -110,
               "decimal": 1 + 100 / 110} for side, point in (("HOME", -3.0), ("AWAY", 3.0))]
    block = NflReadService(None, None)._market_block("SPREAD", spread, None, None, now - timedelta(minutes=240))
    assert block["market_identity"] == {"market_type": "SPREAD", "period": "full_game", "line": -3.0,
                                        "selections": ["HOME:-3.0", "AWAY:3.0"]}
    total = NflReadService(None, None)._market_block("TOTAL", _entries(now), None, None, now - timedelta(minutes=240))
    assert total["market_identity"]["selections"] == ["OVER:40.0", "UNDER:40.0"]

"""The scoreboard coverage report must render after the shared-allocation refactor."""

from __future__ import annotations


def test_nfl_live_funding_has_no_retired_floor_lookup(session):
    from app.scoreboard.coverage import nfl_live_funding

    funding = nfl_live_funding(session)
    assert funding["mlb_pregame_floor"] is None
    assert {"funded_now", "daily_cap", "spent_today", "daily_room", "objects_spendable", "detail"} <= set(funding)


def test_coverage_endpoint_is_not_a_server_error(client):
    response = client.get("/api/v1/scoreboard/coverage")
    assert response.status_code == 200

"""Game Center story read model and the player-prop availability contract.

The story's chart inputs must be exactly the verified population (cells,
margin and total counts summing to the draw count), its line masses must be
the producer contract's, orientation must follow the contract, a final score
appears only after the game, and no player prop or market is fabricated.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.models import GameStatus
from app.probability import adapter
from app.probability.assessments import daily_assessments, player_props
from app.probability.market_probabilities import evaluate_market
from app.probability.story import game_story
from tests.test_market_probability_contract import (  # noqa: F401  (fixtures re-exported)
    KICKOFF, _no_network, _quote, cfb_game, exchange, nba_game, nfl_game, settings,
)


@pytest.fixture
def coverage(monkeypatch):
    """market_coverage is PostgreSQL SQL; tests supply the coverage rows."""
    rows: list[dict] = []
    monkeypatch.setattr("app.services.command_center.market_coverage",
                        lambda session, day, now=None: {"games": rows})
    return rows


def _row(game, quotes, *, current=True, no_vig=0.55):
    return {"game_id": str(game.id), "league": "NFL", "quotes": quotes, "model": None,
            "price_state": "CURRENT_PRICE" if current else "STORED_REFERENCE_ONLY",
            "consensus_no_vig_home": no_vig, "consensus_is_current": current,
            "best": {"HOME": None, "AWAY": None}, "books": 1,
            "freshest_observed_at": "2026-10-04T12:00:00+00:00"}


SIDES = {"HOME": {"american": -110, "decimal": 1.909}, "AWAY": {"american": -110, "decimal": 1.909}}
OU = {"OVER": {"american": -110, "decimal": 1.909}, "UNDER": {"american": -110, "decimal": 1.909}}


def test_story_inputs_are_the_verified_population(session, settings, coverage):
    game, row = nfl_game(session)
    coverage.append(_row(game, [_quote("MONEYLINE", SIDES), _quote("SPREAD", SIDES, line=-1),
                                _quote("TOTAL", OU, line=3)]))
    story = game_story(session, settings, game.id, now=KICKOFF - timedelta(hours=5))
    population = story["population"]
    channel = adapter.bind(session, settings, game.id)
    assert population["population_sha256"] == channel.binding["populationSha256"]
    assert population["draw_count"] == 10000
    assert sum(c["count"] for c in population["cells"]) == 10000
    assert sum(v["count"] for v in population["margin"]) == 10000
    assert sum(v["count"] for v in population["total"]) == 10000
    assert {(c["away"], c["home"], c["count"]) for c in population["cells"]} == {
        (c.away_score, c.home_score, c.count) for c in channel.population.cells}
    # Margin is home minus away; the golden population has two tied cells.
    assert {v["value"]: v["count"] for v in population["margin"]}[0] == 2500
    assert population["likely_region"]["count"] >= 2500
    # Line masses are the producer contract's, oriented to the attached side.
    spread = story["lines"]["spread"]
    assert spread["line"] == -1 and spread["line_side"] == "HOME"
    expected = evaluate_market(channel.population, {"market": "SPREAD", "selection": "HOME", "line": -1})
    assert spread["sides"]["HOME"]["win"] == expected["probabilities"]["win"]
    assert spread["sides"]["HOME"]["push"] == pytest.approx(0.25)
    away = evaluate_market(channel.population, {"market": "SPREAD", "selection": "AWAY", "line": 1})
    assert spread["sides"]["AWAY"]["win"] == away["probabilities"]["win"]
    total = story["lines"]["total"]
    assert total["sides"]["OVER"]["push"] == pytest.approx(0.5)  # integer line 3 pushes on 4 of 8 cells
    assert story["lines"]["from"] == "CURRENT_QUOTES"
    assert story["model_vs_market"]["model_source"] == "VERIFIED_POPULATION_FORECAST"
    assert story["model_vs_market"]["market_home_no_vig"] == 0.55
    assert story["game"]["final"] is None
    assert story["provider_calls_made"] == 0
    kinds = [f["kind"] for f in story["facts"]]
    assert "DISAGREEMENT" in kinds and "FRESHNESS" in kinds


def test_a_final_score_is_an_after_the_fact_overlay_only(session, settings, coverage):
    game, _ = nfl_game(session)
    before = game_story(session, settings, game.id, now=KICKOFF - timedelta(hours=1))
    assert before["game"]["final"] is None
    game.status = GameStatus.FINAL
    game.home_score, game.away_score = 24, 17
    session.flush()
    after = game_story(session, settings, game.id, now=KICKOFF + timedelta(hours=4))
    assert after["game"]["final"] == {"home": 24, "away": 17}
    assert "not known when the forecast was made" in after["game"]["final_note"]
    # The overlay never changes the forecast population.
    assert after["population"]["population_sha256"] == before["population"]["population_sha256"]


def test_missing_markets_are_reported_not_invented(session, settings, coverage):
    game, _ = nfl_game(session)
    coverage.append(_row(game, [_quote("MONEYLINE", SIDES, fresh=False)], current=False))
    story = game_story(session, settings, game.id, now=KICKOFF - timedelta(hours=5))
    assert story["lines"]["spread"] is None and story["lines"]["total"] is None
    assert story["lines"]["from"] == "STORED_QUOTES"
    assert [q["state"] for q in story["market"]["quotes"]] == ["STORED_REFERENCE_ONLY"]
    assert all(s["edge"] is None for q in story["market"]["quotes"] for s in q["sides"].values())
    missing = [f["text"] for f in story["facts"] if f["kind"] == "MISSING"]
    assert missing == ["Not quoted: spread, total."]


def test_no_population_is_a_precise_unavailable_state(session, settings, coverage):
    game, _ = nfl_game(session, artifact="9" * 64)
    story = game_story(session, settings, game.id, now=KICKOFF - timedelta(hours=5))
    assert story["population"] is None
    assert story["channel"]["status"] == "UNSUPPORTED"
    assert story["channel"]["error"]["code"] == "IDENTITY_MISMATCH"
    assert story["lines"]["spread"] is None
    assert any(f["kind"] == "MISSING" and "No verified score population" in f["text"] for f in story["facts"])


def test_research_sports_have_populations_but_no_market(session, settings, exchange, coverage):
    for game in (nba_game(session, exchange), cfb_game(session, exchange)):
        story = game_story(session, settings, game.id, now=KICKOFF - timedelta(days=1))
        assert story["channel"]["authority"] == adapter.RESEARCH_ONLY
        assert story["population"]["draw_count"] == 10000
        assert story["market"] is None
        assert story["model_vs_market"]["market_home_no_vig"] is None
        assert any("Research sport" in f["text"] for f in story["facts"])


def test_unknown_game_is_404(session, settings, client):
    response = client.get("/api/v1/probability/games/00000000-0000-0000-0000-000000000000/story")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "GAME_NOT_FOUND"


def test_player_props_are_never_fabricated(session, settings, coverage):
    props = player_props()
    assert props["status"] == "NOT_STORED"
    assert props["quotes"] == []
    assert set(props["supported_markets"]) == {"batter_hits", "batter_total_bases", "batter_home_runs",
                                               "pitcher_strikeouts"}
    assert props["producer_disposition"]["player_prop_probabilities"] == "INSUFFICIENT_EVIDENCE"
    assert {"game_id", "player_id", "market_key", "sportsbook", "observed_at"} <= set(props["required_identity"])
    assert props["assessment"] == "Sportsbook market — no AISP assessment"
    game, _ = nfl_game(session)
    coverage.append(_row(game, [_quote("MONEYLINE", SIDES)]))
    payload = daily_assessments(session, settings, KICKOFF.date(), now=KICKOFF - timedelta(hours=5))
    assert payload["player_props"]["quotes"] == []

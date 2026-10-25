"""Historical market replay: a stored moment, shown as it was, and nothing else."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models import (
    Game, GameStatus, GameTeam, League, Market, MarketOutcome, MarketType, OddsSnapshot, Sport, Sportsbook, Team,
)
from app.services import command_center, market_replay

NOW = datetime(2026, 9, 17, 1, 0, tzinfo=UTC)
AS_OF = datetime(2026, 9, 16, 18, 51, tzinfo=UTC)  # 2:51 PM ET


def _world(session):
    sport = Sport(code="S_MLB", name="MLB")
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, code="MLB", name="MLB")
    book = Sportsbook(key="draftkings", name="DraftKings")
    session.add_all([league, book])
    session.flush()
    return league, book


def _game(session, league, starts_at, home="NYM", away="BAL"):
    game = Game(league_id=league.id, starts_at=starts_at, status=GameStatus.SCHEDULED)
    session.add(game)
    session.flush()
    for name, is_home in ((home, True), (away, False)):
        team = Team(league_id=league.id, name=f"{name}-{game.id.hex[:4]}", abbreviation=f"{name[:3]}{game.id.hex[:4]}")
        session.add(team)
        session.flush()
        session.add(GameTeam(game_id=game.id, team_id=team.id, is_home=is_home))
    session.flush()
    return game


def _quotes(session, game, book, observed_at, markets=("MONEYLINE", "SPREAD", "TOTAL"),
            source="THE_ODDS_API", mode="LIVE"):
    for name in markets:
        market = session.scalar(select(Market).where(Market.game_id == game.id,
                                                     Market.market_type == MarketType[name]))
        if market is None:
            market = Market(game_id=game.id, market_type=MarketType[name])
            session.add(market)
            session.flush()
        outcome = MarketOutcome(market_id=market.id, outcome_key=f"{name}-{observed_at.isoformat()}", label=name)
        session.add(outcome)
        session.flush()
        session.add(OddsSnapshot(market_outcome_id=outcome.id, sportsbook_id=book.id, observed_at=observed_at,
                                 available_as_of=observed_at, american_odds=-110, decimal_odds=1.909,
                                 source=source, source_mode=mode))
    session.flush()


def test_choose_as_of_takes_the_newest_complete_pregame_moment(session):
    league, book = _world(session)
    older = _game(session, league, AS_OF + timedelta(hours=2))
    _quotes(session, older, book, AS_OF - timedelta(hours=2))
    newest = _game(session, league, AS_OF + timedelta(hours=4))
    _quotes(session, newest, book, AS_OF)
    # Newer, but incomplete (moneyline only): not a replay moment.
    partial = _game(session, league, AS_OF + timedelta(hours=5))
    _quotes(session, partial, book, AS_OF + timedelta(minutes=30), markets=("MONEYLINE",))
    # Newer and complete, but a fixture row, an unpermitted provider, or an in-play observation.
    demo = _game(session, league, AS_OF + timedelta(hours=5))
    _quotes(session, demo, book, AS_OF + timedelta(minutes=40), mode="DEMO")
    other = _game(session, league, AS_OF + timedelta(hours=5))
    _quotes(session, other, book, AS_OF + timedelta(minutes=40), source="SCRAPED")
    live = _game(session, league, AS_OF + timedelta(minutes=10))
    _quotes(session, live, book, AS_OF + timedelta(minutes=45))
    chosen = market_replay.choose_as_of(session, NOW)
    assert chosen["anchor_game_id"] == str(newest.id)
    assert chosen["as_of"] == AS_OF


def test_a_moment_inside_the_freshness_window_is_never_a_replay(session):
    league, book = _world(session)
    game = _game(session, league, NOW + timedelta(hours=3))
    _quotes(session, game, book, NOW - timedelta(minutes=30))
    assert market_replay.choose_as_of(session, NOW) is None
    assert market_replay.historical_replay(session, now=NOW)["available"] is False
    pinned = market_replay.historical_replay(session, now=NOW, as_of=NOW - timedelta(minutes=239))
    assert pinned["available"] is False and "freshness window" in pinned["message"]


def _coverage_game(game_id, starts_at, observed):
    quote = {"market": "MONEYLINE", "line": None, "sportsbook": "DraftKings", "sportsbook_key": "draftkings",
             "provider": "The Odds API", "classification": "GOVERNED", "observed_at": observed,
             "age_minutes": 5.0, "fresh": True, "no_vig_home": 0.5,
             "sides": {"HOME": {"american": -110, "decimal": 1.909}, "AWAY": {"american": -110, "decimal": 1.909}}}
    return {"game_id": game_id, "league": "MLB", "matchup": "BAL @ NYM", "home": {"abbr": "NYM"},
            "away": {"abbr": "BAL"}, "starts_at": starts_at, "status": "FINAL", "price_state": "CURRENT_PRICE",
            "model": {"home": 0.6}, "best": {"HOME": {"american": -110}}, "consensus_no_vig_home": 0.5,
            "checkpoints": {}, "quotes": [quote]}


def test_replay_is_point_in_time_and_carries_nothing_learned_later(session, monkeypatch):
    seen = {}

    def fake_coverage(session, day, now=None, *, as_of=None):
        seen.update(day=day, as_of=as_of, now=now)
        return {"games": [
            _coverage_game("upcoming", (AS_OF + timedelta(hours=4)).isoformat(), (AS_OF - timedelta(minutes=5)).isoformat()),
            _coverage_game("started", (AS_OF - timedelta(minutes=1)).isoformat(), (AS_OF - timedelta(minutes=90)).isoformat()),
            _coverage_game("later", (AS_OF + timedelta(hours=4)).isoformat(), (AS_OF + timedelta(minutes=1)).isoformat()),
        ]}

    monkeypatch.setattr(command_center, "market_coverage", fake_coverage)
    replay = market_replay.historical_replay(session, now=NOW, as_of=AS_OF)
    assert seen["as_of"] == AS_OF and seen["day"].isoformat() == "2026-09-16"
    assert replay["mode"] == "HISTORICAL_REPLAY" and replay["available"]
    assert replay["banner"] == "Historical market replay — prices shown as they appeared at Sep 16, 2026 2:51 PM ET."
    assert replay["pick_label"] == "Historical virtual pick — not an AISP decision."
    assert replay["freshness_measured_at"] == AS_OF.isoformat()
    assert [g["game_id"] for g in replay["games"]] == ["upcoming"]
    game = replay["games"][0]
    assert set(game) == {"game_id", "league", "matchup", "home", "away", "starts_at", "quotes"}
    quote = game["quotes"][0]
    assert set(quote) == set(market_replay.QUOTE_KEYS)
    assert quote["observed_at"] == (AS_OF - timedelta(minutes=5)).isoformat()  # never restamped
    assert replay["creates_decisions"] is False and replay["provider_calls_made"] == 0


def test_replay_writes_nothing(session, monkeypatch):
    from app.db.base import Base

    league, book = _world(session)
    game = _game(session, league, AS_OF + timedelta(hours=4))
    _quotes(session, game, book, AS_OF)
    monkeypatch.setattr(command_center, "market_coverage", lambda *a, **k: {"games": []})
    before = {n: session.scalar(select(func.count()).select_from(t)) for n, t in Base.metadata.tables.items()}
    market_replay.historical_replay(session, now=NOW)
    after = {n: session.scalar(select(func.count()).select_from(t)) for n, t in Base.metadata.tables.items()}
    assert before == after
    assert not session.new and not session.dirty


@pytest.mark.parametrize("path", ["/api/v1/command-center/replay"])
def test_replay_route_is_read_only(client, session, monkeypatch, path):
    monkeypatch.setattr(market_replay, "choose_as_of", lambda session, now=None: None)
    response = client.get(path)
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "HISTORICAL_REPLAY" and body["available"] is False and body["games"] == []

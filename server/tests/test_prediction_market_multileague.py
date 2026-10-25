"""Prediction markets beyond MLB (2026-10-25).

Kalshi NFL/NBA/NHL event tickers carry only the ET date (KXNFLGAME-26OCT25PITNO), so every NFL market was
excluded as 'unparseable_event_ticker' and none ever mapped. These tests pin the fix: an optional HHMM with a
date-only flag, league-aware Kalshi and Polymarket alias tables, and identity by exact teams within the ET game
day. MLB behaviour is unchanged.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.models import Game, GameStatus, GameTeam, League, Sport, Team
from app.prediction_markets.aliases import (
    kalshi_name_to_aisp,
    kalshi_ticker_to_aisp,
    polymarket_name_to_aisp,
    split_kalshi_team_pair,
)
from app.prediction_markets.contracts import CanonicalSelection, MappingStatus
from app.prediction_markets.kalshi import KalshiPredictionMarketSource, parse_event_ticker
from app.prediction_markets.mapping import map_event_to_game


def _seed(session, league: str, home: tuple[str, str], away: tuple[str, str], start: datetime) -> Game:
    sport = session.scalar(select(Sport).where(Sport.code == "FOOTBALL")) or Sport(code="FOOTBALL", name="Football", active=True)
    session.add(sport)
    session.flush()
    lg = session.scalar(select(League).where(League.code == league))
    if lg is None:
        lg = League(sport_id=sport.id, code=league, name=league, active=True)
        session.add(lg)
        session.flush()

    def team(name: str, abbr: str) -> Team:
        found = session.scalar(select(Team).where(Team.league_id == lg.id, Team.abbreviation == abbr))
        if found:
            return found
        t = Team(league_id=lg.id, name=name, abbreviation=abbr, city=name.split()[0], active=True)
        session.add(t)
        session.flush()
        return t

    h, a = team(*home), team(*away)
    g = Game(league_id=lg.id, starts_at=start, status=GameStatus.SCHEDULED, source_mode="LIVE")
    session.add(g)
    session.flush()
    session.add_all([GameTeam(game_id=g.id, team_id=h.id, is_home=True), GameTeam(game_id=g.id, team_id=a.id, is_home=False)])
    session.commit()
    return g


# -- tickers --------------------------------------------------------------------

def test_date_only_tickers_parse_for_nfl_nba_nhl():
    nfl = parse_event_ticker("KXNFLGAME-26OCT25PITNO", "NFL", "PIT vs NO (Oct 25)")
    assert nfl and nfl["date_only"] and (nfl["away_ticker"], nfl["home_ticker"]) == ("PIT", "NO")
    assert (nfl["starts_at"].year, nfl["starts_at"].month, nfl["starts_at"].day, nfl["starts_at"].hour) == (2026, 10, 25, 0)
    nba = parse_event_ticker("KXNBAGAME-26OCT25INDBKN", "NBA", "IND vs BKN (Oct 25)")
    assert nba and nba["date_only"] and (nba["away_ticker"], nba["home_ticker"]) == ("IND", "BKN")
    nhl = parse_event_ticker("KXNHLGAME-26OCT25NYIDAL", "NHL")
    assert nhl and (nhl["away_ticker"], nhl["home_ticker"]) == ("NYI", "DAL")


def test_mlb_ticker_with_time_is_unchanged():
    mlb = parse_event_ticker("KXMLBGAME-26SEP041915DETCLEG2")
    assert mlb and not mlb["date_only"] and mlb["game_number"] == 2
    assert (mlb["starts_at"].hour, mlb["starts_at"].minute) == (19, 15)
    assert (mlb["away_ticker"], mlb["home_ticker"]) == ("DET", "CLE")


def test_unregistered_league_or_team_fails_closed():
    assert parse_event_ticker("KXNFLGAME-26OCT25PITNO") is None            # MLB table has no NO
    assert parse_event_ticker("KXNFLGAME-26OCT25XXXYYY", "NFL") is None
    assert split_kalshi_team_pair("PITNO", "NFL", "NO vs PIT (Oct 25)") == ("PIT", "NO")  # sub-title disagreeing falls back to the unique split


# -- aliases ----------------------------------------------------------------------

def test_kalshi_codes_translate_to_aisp_per_league():
    assert kalshi_ticker_to_aisp("JAC", "NFL") == "JAX"
    assert kalshi_ticker_to_aisp("BKN", "NBA") == "BRK" and kalshi_ticker_to_aisp("PHX", "NBA") == "PHO" and kalshi_ticker_to_aisp("CHA", "NBA") == "CHO"
    assert kalshi_ticker_to_aisp("TB", "NHL") == "TBL" and kalshi_ticker_to_aisp("LA", "NHL") == "LAK"
    assert kalshi_ticker_to_aisp("TB") == "TB"                             # MLB default untouched
    assert kalshi_name_to_aisp("Los Angeles L", "NBA") == "LAL" and kalshi_name_to_aisp("New York I", "NHL") == "NYI"


def test_polymarket_names_per_league_and_futures_stay_unmapped():
    assert polymarket_name_to_aisp("Commanders", "NFL") == "WAS"
    assert polymarket_name_to_aisp("Los Angeles Rams", "NFL") == "LAR"
    assert polymarket_name_to_aisp("New York Jets", "NFL") == "NYJ"
    assert polymarket_name_to_aisp("Golden Knights", "NHL") == "VGK" and polymarket_name_to_aisp("St. Louis Blues", "NHL") == "STL"
    assert polymarket_name_to_aisp("Los Angeles Lakers", "NBA") == "LAL"
    assert polymarket_name_to_aisp("49ers Season Series Winner", "NFL") is None
    assert polymarket_name_to_aisp("New York Yankees") == "NYY"


# -- normalization ------------------------------------------------------------------

def test_kalshi_nfl_event_normalizes_with_date_only_flag():
    event = {
        "event_ticker": "KXNFLGAME-26OCT25PITNO", "series_ticker": "KXNFLGAME", "sub_title": "PIT vs NO (Oct 25)",
        "title": "Pittsburgh vs New Orleans",
        "markets": [
            {"ticker": "KXNFLGAME-26OCT25PITNO-NO", "yes_sub_title": "New Orleans", "status": "active",
             "rules_primary": "If New Orleans wins the Pittsburgh vs New Orleans Pro Football game originally scheduled for Oct 25, 2026, then the market resolves to Yes.",
             "yes_bid_dollars": "0.40", "yes_ask_dollars": "0.42"},
            {"ticker": "KXNFLGAME-26OCT25PITNO-PIT", "yes_sub_title": "Pittsburgh", "status": "active",
             "rules_primary": "If Pittsburgh wins the Pittsburgh vs New Orleans Pro Football game originally scheduled for Oct 25, 2026, then the market resolves to Yes.",
             "yes_bid_dollars": "0.58", "yes_ask_dollars": "0.60"},
        ],
    }
    excluded: dict[str, int] = {}
    out = KalshiPredictionMarketSource()._normalize_event(event, league="NFL", sport="FOOTBALL", excluded=excluded)
    assert not excluded and len(out) == 2
    sides = {dto.selection: dto for dto, _ in out}
    assert sides[CanonicalSelection.HOME].provider_home_team == "NO" and sides[CanonicalSelection.HOME].provider_away_team == "PIT"
    assert sides[CanonicalSelection.AWAY].outcome_label == "Pittsburgh"
    assert all((dto.raw or {}).get("date_only_start") is True for dto, _ in out)


# -- mapping --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_date_only_maps_exact_teams_within_the_et_day(session, settings):
    game = _seed(session, "NFL", ("New Orleans Saints", "NO"), ("Pittsburgh Steelers", "PIT"), datetime(2026, 10, 25, 13, 30, tzinfo=UTC))
    d = map_event_to_game(session, provider="KALSHI", provider_event_id="KXNFLGAME-26OCT25PITNO", league_code="NFL",
                          provider_home_team="NO", provider_away_team="PIT", scheduled_start_at=datetime(2026, 10, 25, 4, 0, tzinfo=UTC),
                          provider_home_abbr="NO", provider_away_abbr="PIT", start_is_date_only=True)
    assert d.status == MappingStatus.EXACT and str(d.canonical_game_id) == str(game.id)


@pytest.mark.asyncio
async def test_date_only_reaches_a_night_game_after_midnight_utc(session, settings):
    # Sunday night football: 8:20 PM ET is 00:20 UTC the next day; the ticker carries the ET date.
    game = _seed(session, "NFL", ("Seattle Seahawks", "SEA"), ("Kansas City Chiefs", "KC"), datetime(2026, 10, 26, 0, 20, tzinfo=UTC))
    d = map_event_to_game(session, provider="KALSHI", provider_event_id="KXNFLGAME-26OCT25KCSEA", league_code="NFL",
                          provider_home_team="SEA", provider_away_team="KC", scheduled_start_at=datetime(2026, 10, 25, 4, 0, tzinfo=UTC),
                          provider_home_abbr="SEA", provider_away_abbr="KC", start_is_date_only=True)
    assert d.status == MappingStatus.EXACT and str(d.canonical_game_id) == str(game.id)


@pytest.mark.asyncio
async def test_date_only_never_matches_another_day(session, settings):
    _seed(session, "NFL", ("New Orleans Saints", "NO"), ("Pittsburgh Steelers", "PIT"), datetime(2026, 11, 1, 17, 0, tzinfo=UTC))
    d = map_event_to_game(session, provider="KALSHI", provider_event_id="KXNFLGAME-26OCT25PITNO", league_code="NFL",
                          provider_home_team="NO", provider_away_team="PIT", scheduled_start_at=datetime(2026, 10, 25, 4, 0, tzinfo=UTC),
                          provider_home_abbr="NO", provider_away_abbr="PIT", start_is_date_only=True)
    assert d.status == MappingStatus.UNRESOLVED


@pytest.mark.asyncio
async def test_date_only_reversed_teams_need_review(session, settings):
    _seed(session, "NFL", ("Pittsburgh Steelers", "PIT"), ("New Orleans Saints", "NO"), datetime(2026, 10, 25, 17, 0, tzinfo=UTC))
    d = map_event_to_game(session, provider="KALSHI", provider_event_id="KXNFLGAME-26OCT25PITNO", league_code="NFL",
                          provider_home_team="NO", provider_away_team="PIT", scheduled_start_at=datetime(2026, 10, 25, 4, 0, tzinfo=UTC),
                          provider_home_abbr="NO", provider_away_abbr="PIT", start_is_date_only=True)
    assert d.status == MappingStatus.REVIEW_REQUIRED

"""Market board day boundaries: the operational date is the Eastern date.

Games and quotes are stored as UTC instants. The slate a game belongs to, and
whether the newest governed observation is "historical" for a slate, are
decided in the display timezone (America/New_York), across the daylight-saving
change, without restamping any stored observation.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Game, GameStatus, GameTeam, League, Market, MarketOutcome, MarketType, OddsSnapshot,
    Season, Sport, Sportsbook, Team,
)
from app.services.league_scope import MLB
from app.services.read_service import ReadService


def _mlb(session: Session) -> tuple[League, Season, Team, Team]:
    league = session.scalar(select(League).where(League.code == MLB))
    if league is None:
        sport = Sport(code="BASEBALL", name="Baseball")
        session.add(sport)
        session.flush()
        league = League(sport_id=sport.id, code=MLB, name="MLB")
        session.add(league)
        session.flush()
        season = Season(league_id=league.id, name="2026", starts_at=datetime(2026, 3, 1, tzinfo=UTC),
                        ends_at=datetime(2026, 12, 31, tzinfo=UTC))
        home = Team(league_id=league.id, name="Boston Red Sox", abbreviation="BOS")
        away = Team(league_id=league.id, name="Detroit Tigers", abbreviation="DET")
        session.add_all([season, home, away])
        session.flush()
    season = session.scalar(select(Season).where(Season.league_id == league.id))
    home = session.scalar(select(Team).where(Team.abbreviation == "BOS"))
    away = session.scalar(select(Team).where(Team.abbreviation == "DET"))
    return league, season, home, away


def _game(session: Session, starts_at: datetime) -> Game:
    league, season, home, away = _mlb(session)
    game = Game(league_id=league.id, season_id=season.id, starts_at=starts_at,
                status=GameStatus.SCHEDULED, source_mode="LIVE")
    session.add(game)
    session.flush()
    session.add_all([GameTeam(game_id=game.id, team_id=home.id, is_home=True),
                     GameTeam(game_id=game.id, team_id=away.id, is_home=False)])
    session.flush()
    return game


def _quote(session: Session, game: Game, observed: datetime, available: datetime) -> OddsSnapshot:
    _, _, home, away = _mlb(session)
    book = session.scalar(select(Sportsbook).where(Sportsbook.key == "draftkings"))
    if book is None:
        book = Sportsbook(key="draftkings", name="DraftKings")
        session.add(book)
        session.flush()
    market = Market(game_id=game.id, market_type=MarketType.MONEYLINE, period="full_game")
    session.add(market)
    session.flush()
    last = None
    for key, team, american, decimal in (("HOME:None", home, -120, 1.8333), ("AWAY:None", away, 110, 2.10)):
        outcome = MarketOutcome(market_id=market.id, outcome_key=key, label=team.name, participant_team_id=team.id)
        session.add(outcome)
        session.flush()
        last = OddsSnapshot(market_outcome_id=outcome.id, sportsbook_id=book.id, observed_at=observed,
                            available_as_of=available, american_odds=american, decimal_odds=decimal,
                            implied_probability=1 / decimal, source="THE_ODDS_API", source_mode="LIVE")
        session.add(last)
    session.flush()
    return last


def _slate(session: Session, settings, day: date) -> set[str]:
    return {str(g.id) for g in ReadService(session, settings)._games_for_date(day, MLB)}


def test_a_normal_eastern_day_game_is_on_its_own_slate(session: Session, settings) -> None:
    game = _game(session, datetime(2026, 9, 19, 17, 5, tzinfo=UTC))  # 1:05 PM EDT
    _quote(session, game, datetime(2026, 9, 19, 14, 0, tzinfo=UTC), datetime(2026, 9, 19, 14, 1, tzinfo=UTC))
    assert _slate(session, settings, date(2026, 9, 19)) == {str(game.id)}
    board = ReadService(session, settings).market_board(date(2026, 9, 19))
    assert {row.game_id for row in board.rows} == {str(game.id)}
    assert all(row.start_time == datetime(2026, 9, 19, 17, 5, tzinfo=UTC) for row in board.rows)


def test_a_late_game_whose_utc_date_is_tomorrow_stays_on_the_eastern_date(session: Session, settings) -> None:
    late = _game(session, datetime(2026, 9, 20, 1, 30, tzinfo=UTC))  # Sat 9:30 PM EDT
    assert _slate(session, settings, date(2026, 9, 19)) == {str(late.id)}
    assert _slate(session, settings, date(2026, 9, 20)) == set()


def test_the_daylight_saving_change_moves_the_boundary_with_it(session: Session, settings) -> None:
    # DST ends 2026-11-01 at 06:00Z (2:00 AM EDT -> 1:00 AM EST).
    halloween_late = _game(session, datetime(2026, 11, 1, 3, 30, tzinfo=UTC))   # Oct 31 11:30 PM EDT
    after_midnight = _game(session, datetime(2026, 11, 1, 5, 30, tzinfo=UTC))   # Nov 1 1:30 AM EDT
    sunday_late = _game(session, datetime(2026, 11, 2, 4, 30, tzinfo=UTC))      # Nov 1 11:30 PM EST
    monday_early = _game(session, datetime(2026, 11, 2, 5, 30, tzinfo=UTC))     # Nov 2 12:30 AM EST
    assert _slate(session, settings, date(2026, 10, 31)) == {str(halloween_late.id)}
    assert _slate(session, settings, date(2026, 11, 1)) == {str(after_midnight.id), str(sunday_late.id)}
    assert _slate(session, settings, date(2026, 11, 2)) == {str(monday_early.id)}


def test_a_quote_just_before_midnight_eastern_is_historical_for_the_next_slate(session: Session, settings) -> None:
    game = _game(session, datetime(2026, 9, 19, 17, 5, tzinfo=UTC))
    before = datetime(2026, 9, 19, 3, 59, tzinfo=UTC)   # Sep 18 11:59 PM EDT
    snapshot = _quote(session, game, before, datetime(2026, 9, 19, 4, 2, tzinfo=UTC))
    status = ReadService(session, settings)._market_feed_status(date(2026, 9, 19))
    assert status.latest_observation_at.replace(tzinfo=UTC) == before
    assert status.latest_observation_is_historical is True
    # Nothing is restamped: the stored observation and availability instants
    # stay distinct and unchanged.
    session.refresh(snapshot)
    assert snapshot.observed_at.replace(tzinfo=UTC) == before
    assert snapshot.available_as_of.replace(tzinfo=UTC) == datetime(2026, 9, 19, 4, 2, tzinfo=UTC)


def test_a_quote_just_after_midnight_eastern_belongs_to_that_slate(session: Session, settings) -> None:
    game = _game(session, datetime(2026, 9, 19, 17, 5, tzinfo=UTC))
    after = datetime(2026, 9, 19, 4, 1, tzinfo=UTC)     # Sep 19 12:01 AM EDT
    _quote(session, game, after, after)
    status = ReadService(session, settings)._market_feed_status(date(2026, 9, 19))
    assert status.latest_observation_is_historical is False


def test_no_governed_observation_reports_none_instead_of_failing(session: Session, settings) -> None:
    _game(session, datetime(2026, 9, 19, 17, 5, tzinfo=UTC))
    board = ReadService(session, settings).market_board(date(2026, 9, 19))
    assert board.rows == []
    assert board.feed_status.latest_observation_at is None
    assert board.feed_status.latest_observation_is_historical is False

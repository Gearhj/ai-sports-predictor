"""Odds ingestion binds a provider event only inside the league it was requested for.

``source_mode`` is provenance, never sport: a LIVE NBA game (the point-Elo
importer stores BOS @ DET that way) or a RESEARCH_SHADOW College Football game
must never receive MLB prices, whatever their teams are called. Every refusal
leaves a structured receipt. No test here makes a provider request.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.data_sources.base import BookMarketDTO, BookmakerDTO, OddsEventDTO, OddsOutcomeDTO
from app.ingestion.normalizer import CanonicalNormalizer
from app.ingestion.odds_league import (
    EVENT_IDENTITY_AMBIGUOUS, LEAGUE_MISMATCH_EXCLUDED, NO_CANONICAL_CANDIDATE,
    PARTICIPANT_IDENTITY_CONFLICT, PROVIDER_LEAGUE_UNMAPPED, SCHEDULED_TIME_MISMATCH,
    canonical_league_for,
)
from app.jobs.orchestrator import JobOrchestrator
from app.models import Game, GameStatus, GameTeam, League, Market, OddsSnapshot, Season, Sport, Team
from app.services.league_scope import MLB
from app.services.read_service import ReadService

START = datetime(2026, 10, 20, 19, 0, tzinfo=UTC)
FETCHED = START - timedelta(hours=3)
RED_SOX, TIGERS = "Boston Red Sox", "Detroit Tigers"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a provider request was attempted")
    monkeypatch.setattr(httpx.Client, "send", refuse)
    monkeypatch.setattr(httpx.AsyncClient, "send", refuse)


def _league(session: Session, code: str) -> League:
    league = session.scalar(select(League).where(League.code == code))
    if league is None:
        sport = Sport(code=f"SPORT_{code}", name=code)
        session.add(sport)
        session.flush()
        league = League(sport_id=sport.id, code=code, name=code)
        session.add(league)
        session.flush()
    return league


def _game(session: Session, code: str, home: str, away: str, *, starts_at: datetime = START,
          source_mode: str = "LIVE", abbreviations: tuple[str, str] = ("BOS", "DET")) -> Game:
    league = _league(session, code)
    season = Season(league_id=league.id, name=f"{code}-{starts_at.isoformat()}",
                    starts_at=datetime(2026, 1, 1, tzinfo=UTC), ends_at=datetime(2027, 6, 30, tzinfo=UTC))
    session.add(season)
    session.flush()
    game = Game(league_id=league.id, season_id=season.id, starts_at=starts_at,
                status=GameStatus.SCHEDULED, source_mode=source_mode)
    session.add(game)
    session.flush()
    for name, abbreviation, is_home in ((home, abbreviations[0], True), (away, abbreviations[1], False)):
        team = session.scalar(select(Team).where(Team.league_id == league.id, Team.name == name))
        if team is None:
            team = Team(league_id=league.id, name=name, abbreviation=abbreviation)
            session.add(team)
            session.flush()
        session.add(GameTeam(game_id=game.id, team_id=team.id, is_home=is_home))
    session.flush()
    return game


def _event(home: str = RED_SOX, away: str = TIGERS, *, at: datetime = START, event_id: str = "evt-1") -> OddsEventDTO:
    seen = FETCHED - timedelta(minutes=5)
    return OddsEventDTO(
        source_event_id=event_id, commence_time=at, home_team=home, away_team=away,
        bookmakers=[BookmakerDTO(key="draftkings", title="DraftKings", last_update=seen, markets=[
            BookMarketDTO(key="h2h", last_update=seen, outcomes=[
                OddsOutcomeDTO(name=home, price=-130), OddsOutcomeDTO(name=away, price=110),
            ]),
        ])],
    )


def _ingest(session, events, *, league_code=MLB, source="THE_ODDS_API", provider_league="baseball_mlb",
            source_mode="LIVE"):
    normalizer = CanonicalNormalizer(session)
    written = normalizer.ingest_odds(events, source, FETCHED, league_code=league_code,
                                     provider_league=provider_league, source_mode=source_mode)
    return written, normalizer.odds_receipts


def _markets(session, game) -> int:
    return session.scalar(select(func.count()).select_from(Market).where(Market.game_id == game.id))


def test_an_nba_bos_det_game_never_receives_mlb_prices(session):
    # Same names on purpose: only the league can keep them apart.
    nba = _game(session, "NBA", RED_SOX, TIGERS)
    written, receipts = _ingest(session, [_event()])
    assert written == 0 and _markets(session, nba) == 0
    assert [r["reason"] for r in receipts] == [LEAGUE_MISMATCH_EXCLUDED]
    assert receipts[0]["other_league"] == "NBA" and receipts[0]["source_event_id"] == "evt-1"
    assert "price" not in str(receipts[0])  # identifiers only, no payload


def test_nba_detroit_home_never_matches_mlb_detroit(session):
    nba = _game(session, "NBA", TIGERS, RED_SOX, abbreviations=("DET", "BOS"))
    written, receipts = _ingest(session, [_event(home=TIGERS, away=RED_SOX)])
    assert written == 0 and _markets(session, nba) == 0
    assert receipts[0]["reason"] == LEAGUE_MISMATCH_EXCLUDED


def test_same_name_candidates_in_two_leagues_resolve_by_league_first(session):
    nba = _game(session, "NBA", RED_SOX, TIGERS)
    mlb = _game(session, MLB, RED_SOX, TIGERS)
    written, receipts = _ingest(session, [_event()])
    assert written == 2 and receipts == []
    assert _markets(session, mlb) == 1 and _markets(session, nba) == 0


def test_a_cfb_research_game_never_enters_mlb_price_matching(session):
    cfb = _game(session, "CFB", RED_SOX, TIGERS, source_mode="RESEARCH_SHADOW",
                abbreviations=("CFBD48", "CFBD324"))
    written, receipts = _ingest(session, [_event()])
    assert written == 0 and _markets(session, cfb) == 0
    assert receipts[0]["reason"] == NO_CANONICAL_CANDIDATE


def test_an_unknown_mma_request_is_unmapped_and_binds_nothing(session):
    mma = _game(session, "MMA", "Fighter One", "Fighter Two", abbreviations=("F1", "F2"))
    written, receipts = _ingest(session, [_event("Fighter One", "Fighter Two")],
                                league_code="MMA", source="SPORTSGAMEODDS", provider_league="UFC")
    assert written == 0 and _markets(session, mma) == 0
    assert receipts[0]["reason"] == PROVIDER_LEAGUE_UNMAPPED
    # And an MLB-lane request cannot reach the fight either.
    written, receipts = _ingest(session, [_event("Fighter One", "Fighter Two")])
    assert written == 0 and receipts[0]["reason"] == LEAGUE_MISMATCH_EXCLUDED


def test_a_provider_league_for_another_sport_is_refused_before_matching(session):
    mlb = _game(session, MLB, RED_SOX, TIGERS)
    written, receipts = _ingest(session, [_event()], provider_league="americanfootball_nfl")
    assert written == 0 and _markets(session, mlb) == 0
    assert receipts[0]["reason"] == LEAGUE_MISMATCH_EXCLUDED
    assert receipts[0]["provider_maps_to"] == "NFL"
    written, receipts = _ingest(session, [_event()], provider_league=None)
    assert written == 0 and receipts[0]["reason"] == PROVIDER_LEAGUE_UNMAPPED


def test_an_ambiguous_same_league_match_fails_closed(session):
    first = _game(session, MLB, RED_SOX, TIGERS, starts_at=START - timedelta(hours=2))
    second = _game(session, MLB, RED_SOX, TIGERS, starts_at=START + timedelta(hours=2))
    written, receipts = _ingest(session, [_event()])
    assert written == 0 and _markets(session, first) == 0 and _markets(session, second) == 0
    assert receipts[0]["reason"] == EVENT_IDENTITY_AMBIGUOUS


def test_reversed_participants_are_a_conflict_not_a_match(session):
    mlb = _game(session, MLB, RED_SOX, TIGERS)
    written, receipts = _ingest(session, [_event(home=TIGERS, away=RED_SOX)])
    assert written == 0 and _markets(session, mlb) == 0
    assert receipts[0]["reason"] == PARTICIPANT_IDENTITY_CONFLICT


def test_a_start_outside_the_tolerance_is_a_time_mismatch(session):
    mlb = _game(session, MLB, RED_SOX, TIGERS)
    written, receipts = _ingest(session, [_event(at=START + timedelta(hours=8))])
    assert written == 0 and _markets(session, mlb) == 0
    assert receipts[0]["reason"] == SCHEDULED_TIME_MISMATCH


def test_valid_mlb_odds_still_bind_through_both_providers(session):
    mlb = _game(session, MLB, RED_SOX, TIGERS)
    written, receipts = _ingest(session, [_event()])
    assert written == 2 and receipts == []
    written, receipts = _ingest(session, [_event(event_id="sgo-1")], source="SPORTSGAMEODDS", provider_league="MLB")
    assert written == 2 and receipts == []
    sources = set(session.scalars(
        select(OddsSnapshot.source).where(OddsSnapshot.source_event_id.in_(["evt-1", "sgo-1"]))))
    assert sources == {"THE_ODDS_API", "SPORTSGAMEODDS"}
    assert _markets(session, mlb) == 1


def test_a_live_nba_game_does_not_make_the_mlb_lane_live(session, settings):
    # Only a LIVE NBA game and an MLB demo fixture exist. With no explicit
    # source mode, the MLB lane must still resolve to DEMO.
    _game(session, "NBA", RED_SOX, TIGERS)
    demo = _game(session, MLB, RED_SOX, TIGERS, source_mode="DEMO")
    event = _event()
    event.source_mode = "LIVE"
    normalizer = CanonicalNormalizer(session)
    written = normalizer.ingest_odds([event], "ASP_DEMO_FIXTURE", FETCHED,
                                     league_code=MLB, provider_league="baseball_mlb")
    assert written == 2 and _markets(session, demo) == 1
    # The MLB slate's data mode is not made "live" by the NBA game.
    day = START.date()
    no_demo = settings.model_copy(update={"demo_mode": False})
    assert ReadService(session, no_demo)._data_mode(day) == "empty"


def test_an_mlb_slate_with_only_nba_and_cfb_games_is_empty(session, settings):
    _game(session, "NBA", RED_SOX, TIGERS)
    _game(session, "CFB", "Delaware", "Coastal Carolina", source_mode="RESEARCH_SHADOW",
          abbreviations=("CFBD48", "CFBD324"))
    day = START.date()
    assert ReadService(session, settings)._games_for_date(day, MLB) == []
    assert JobOrchestrator(session, settings)._games_for_date(day, MLB) == []


def test_the_provider_league_vocabulary_is_explicit():
    assert canonical_league_for("THE_ODDS_API", "baseball_mlb") == MLB
    assert canonical_league_for("THE_ODDS_API", "americanfootball_nfl") == "NFL"
    assert canonical_league_for("SPORTSGAMEODDS", "NCAAF") is None
    assert canonical_league_for("SPORTSGAMEODDS", "NBA") is None
    assert canonical_league_for("SPORTSGAMEODDS", "UFC") is None

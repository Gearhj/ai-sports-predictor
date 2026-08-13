from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import select

from app.data_sources.base import ScheduleGameDTO, TeamDTO, VenueDTO
from app.ingestion import CanonicalNormalizer
from app.models import (
    Game,
    GameStateObservation,
    GameStatus,
    GameTeam,
    League,
    LineupEntry,
    LineupObservation,
    Market,
    MarketOutcome,
    MarketType,
    OddsSnapshot,
    Player,
    RosterAvailabilityObservation,
    Season,
    Sport,
    Sportsbook,
    StarterObservation,
    StatcastPlayerObservation,
    Team,
    TeamRatingObservation,
    Venue,
    WeatherSnapshot,
)
from app.services.game_state_service import GameStateService


FIRST_PITCH = datetime(2026, 8, 12, 23, tzinfo=UTC)
EARLY = FIRST_PITCH - timedelta(hours=6)
CUTOFF = FIRST_PITCH - timedelta(hours=2)
LATE = FIRST_PITCH - timedelta(hours=1)


def _base_game(session) -> SimpleNamespace:
    sport = Sport(code="G2_BASEBALL", name="Gate 2 Baseball")
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, code="G2_MLB", name="Gate 2 MLB")
    session.add(league)
    session.flush()
    season = Season(
        league_id=league.id,
        name="2026-gate2",
        starts_at=datetime(2026, 3, 1, tzinfo=UTC),
        ends_at=datetime(2026, 11, 15, tzinfo=UTC),
    )
    venue = Venue(name="Gate 2 Park", timezone="UTC")
    home = Team(league_id=league.id, name="Gate 2 Home", abbreviation="G2H")
    away = Team(league_id=league.id, name="Gate 2 Away", abbreviation="G2A")
    session.add_all([season, venue, home, away])
    session.flush()
    game = Game(
        league_id=league.id,
        season_id=season.id,
        venue_id=venue.id,
        starts_at=FIRST_PITCH,
        status=GameStatus.FINAL,
        home_score=9,
        away_score=1,
        final_at=FIRST_PITCH + timedelta(hours=3),
        source_mode="LIVE",
    )
    session.add(game)
    session.flush()
    session.add_all([
        GameTeam(game_id=game.id, team_id=home.id, is_home=True),
        GameTeam(game_id=game.id, team_id=away.id, is_home=False),
    ])
    home_player = Player(team_id=home.id, full_name="Early Home Player", position="P")
    replacement = Player(team_id=home.id, full_name="Late Replacement", position="P")
    away_player = Player(team_id=away.id, full_name="Away Player", position="P")
    session.add_all([home_player, replacement, away_player])
    session.flush()
    return SimpleNamespace(
        game=game,
        season=season,
        venue=venue,
        home=home,
        away=away,
        home_player=home_player,
        replacement=replacement,
        away_player=away_player,
    )


def _game_observation(
    context: SimpleNamespace,
    available_as_of: datetime,
    *,
    source_mode: str = "LIVE",
    status: str = "SCHEDULED",
    home_score: int | None = None,
    away_score: int | None = None,
) -> GameStateObservation:
    return GameStateObservation(
        game_id=context.game.id,
        home_team_id=context.home.id,
        away_team_id=context.away.id,
        scheduled_at=FIRST_PITCH,
        status=status,
        is_postponed=False,
        venue_id=context.venue.id,
        home_score=home_score,
        away_score=away_score,
        final_at=FIRST_PITCH + timedelta(hours=3) if status == "FINAL" else None,
        home_starter_confirmation="UNKNOWN",
        away_starter_confirmation="UNKNOWN",
        observed_at=available_as_of,
        available_as_of=available_as_of,
        source=f"{source_mode}_SCHEDULE_TEST",
        source_mode=source_mode,
        payload_checksum=("1" if source_mode == "LIVE" else "2") * 64,
    )


def _lineup(
    context: SimpleNamespace,
    player: Player,
    available_as_of: datetime,
    status: str,
    *,
    source_mode: str = "LIVE",
) -> tuple[LineupObservation, LineupEntry]:
    observation = LineupObservation(
        game_id=context.game.id,
        team_id=context.home.id,
        lineup_status=status,
        is_complete=False,
        observed_at=available_as_of,
        available_as_of=available_as_of,
        source=f"{source_mode}_LINEUP_TEST",
        source_mode=source_mode,
        payload_checksum=(player.full_name + status).encode().hex()[:64].ljust(64, "0"),
    )
    context.session.add(observation)
    context.session.flush()
    entry = LineupEntry(
        lineup_observation_id=observation.id,
        player_id=player.id,
        batting_order=1,
        defensive_position=player.position,
        is_starter=True,
        expected_plate_appearance_weight=1,
    )
    return observation, entry


def test_later_starter_and_lineup_evidence_cannot_leak_backward(session) -> None:
    context = _base_game(session)
    context.session = session
    session.add(_game_observation(context, EARLY))
    early_lineup, early_entry = _lineup(context, context.home_player, EARLY, "PROJECTED")
    late_lineup, late_entry = _lineup(context, context.replacement, LATE, "CONFIRMED")
    early_starter = StarterObservation(
        game_id=context.game.id,
        team_id=context.home.id,
        probable_pitcher_id=context.home_player.id,
        confirmation_state="PROBABLE",
        observed_at=EARLY,
        available_as_of=EARLY,
        source="LIVE_STARTER_TEST",
        source_mode="LIVE",
        payload_checksum="3" * 64,
    )
    late_starter = StarterObservation(
        game_id=context.game.id,
        team_id=context.home.id,
        probable_pitcher_id=context.replacement.id,
        confirmed_pitcher_id=context.replacement.id,
        confirmation_state="CONFIRMED",
        observed_at=LATE,
        available_as_of=LATE,
        source="LIVE_STARTER_TEST",
        source_mode="LIVE",
        payload_checksum="4" * 64,
    )
    session.add_all([early_entry, late_entry, early_starter, late_starter])
    session.commit()

    service = GameStateService(session)
    early_state = service.get_game_state(context.game.id, CUTOFF)
    assert early_state.home.lineup.id == early_lineup.id
    assert early_state.home.lineup.lineup_status == "PROJECTED"
    assert [entry.player_id for entry in early_state.home.lineup_entries] == [context.home_player.id]
    assert early_state.home.starter.id == early_starter.id
    assert early_state.home.starter.confirmed_pitcher_id is None

    later_state = service.get_game_state(context.game.id, LATE + timedelta(minutes=1))
    assert later_state.home.lineup.id == late_lineup.id
    assert [entry.player_id for entry in later_state.home.lineup_entries] == [context.replacement.id]
    assert later_state.home.starter.id == late_starter.id


def test_trade_and_roster_status_take_effect_only_at_their_knowledge_cutoff(session) -> None:
    context = _base_game(session)
    session.add(_game_observation(context, EARLY))
    active_home = RosterAvailabilityObservation(
        season_id=context.season.id,
        team_id=context.home.id,
        player_id=context.home_player.id,
        availability_status="ACTIVE",
        transaction_status=None,
        effective_at=EARLY,
        active_roster=True,
        observed_at=EARLY,
        available_as_of=EARLY,
        source="LIVE_ROSTER_TEST",
        source_mode="LIVE",
        payload_checksum="5" * 64,
    )
    trade_available = CUTOFF - timedelta(minutes=30)
    trade_effective = CUTOFF + timedelta(minutes=30)
    traded_away = RosterAvailabilityObservation(
        season_id=context.season.id,
        team_id=context.away.id,
        player_id=context.home_player.id,
        availability_status="ACTIVE",
        transaction_status="TRADED",
        effective_at=trade_effective,
        active_roster=True,
        observed_at=trade_available,
        available_as_of=trade_available,
        source="LIVE_ROSTER_TEST",
        source_mode="LIVE",
        payload_checksum="6" * 64,
    )
    unavailable_at = trade_effective + timedelta(minutes=30)
    unavailable = RosterAvailabilityObservation(
        season_id=context.season.id,
        team_id=context.away.id,
        player_id=context.home_player.id,
        availability_status="OUT",
        effective_at=unavailable_at,
        active_roster=False,
        observed_at=unavailable_at,
        available_as_of=unavailable_at,
        source="LIVE_ROSTER_TEST",
        source_mode="LIVE",
        payload_checksum="7" * 64,
    )
    session.add_all([active_home, traded_away, unavailable])
    session.commit()

    service = GameStateService(session)
    before_effective = service.get_game_state(context.game.id, CUTOFF)
    assert [row.player_id for row in before_effective.home.roster] == [context.home_player.id]
    assert context.home_player.id not in {row.player_id for row in before_effective.away.roster}

    after_trade = service.get_game_state(context.game.id, trade_effective + timedelta(minutes=1))
    assert context.home_player.id not in {row.player_id for row in after_trade.home.roster}
    assert [row.player_id for row in after_trade.away.roster] == [context.home_player.id]

    after_out = service.get_game_state(context.game.id, unavailable_at + timedelta(minutes=1))
    assert context.home_player.id not in {row.player_id for row in after_out.away.roster}


def test_weather_odds_statcast_and_rating_obey_available_as_of(session) -> None:
    context = _base_game(session)
    session.add(_game_observation(context, EARLY))
    early_weather = WeatherSnapshot(
        game_id=context.game.id,
        source="LIVE_WEATHER_TEST",
        observed_at=FIRST_PITCH,
        available_as_of=EARLY,
        temperature_f=70,
        source_mode="LIVE",
    )
    late_weather = WeatherSnapshot(
        game_id=context.game.id,
        source="LIVE_WEATHER_TEST",
        observed_at=FIRST_PITCH,
        available_as_of=LATE,
        temperature_f=95,
        source_mode="LIVE",
    )
    sportsbook = Sportsbook(key="gate2_book", name="Gate 2 Book")
    market = Market(game_id=context.game.id, market_type=MarketType.MONEYLINE)
    session.add_all([early_weather, late_weather, sportsbook, market])
    session.flush()
    outcome = MarketOutcome(market_id=market.id, outcome_key="home:none", label=context.home.name, participant_team_id=context.home.id)
    session.add(outcome)
    session.flush()
    early_odds = OddsSnapshot(
        market_outcome_id=outcome.id,
        sportsbook_id=sportsbook.id,
        observed_at=EARLY,
        available_as_of=EARLY,
        american_odds=-110,
        decimal_odds=1.91,
        source="LIVE_ODDS_TEST",
        source_mode="LIVE",
    )
    late_odds = OddsSnapshot(
        market_outcome_id=outcome.id,
        sportsbook_id=sportsbook.id,
        observed_at=LATE,
        available_as_of=LATE,
        american_odds=-150,
        decimal_odds=1.67,
        source="LIVE_ODDS_TEST",
        source_mode="LIVE",
    )
    early_statcast = StatcastPlayerObservation(
        player_id=context.home_player.id,
        team_id=context.home.id,
        season=2026,
        role="HITTER",
        evidence_cutoff=EARLY,
        sample_size=100,
        feature_schema_version="test-v1",
        values={"xwoba": 0.31},
        observed_at=EARLY,
        available_as_of=EARLY,
        source="LIVE_STATCAST_TEST",
        source_mode="LIVE",
        payload_checksum="8" * 64,
    )
    late_statcast = StatcastPlayerObservation(
        player_id=context.home_player.id,
        team_id=context.home.id,
        season=2026,
        role="HITTER",
        evidence_cutoff=LATE,
        sample_size=120,
        feature_schema_version="test-v1",
        values={"xwoba": 0.41},
        observed_at=LATE,
        available_as_of=LATE,
        source="LIVE_STATCAST_TEST",
        source_mode="LIVE",
        payload_checksum="9" * 64,
    )
    early_rating = TeamRatingObservation(
        team_id=context.home.id,
        rating=1500,
        method="TEST_ELO",
        version="v1",
        evidence_cutoff=EARLY,
        observed_at=EARLY,
        available_as_of=EARLY,
        source="LIVE_RATING_TEST",
        source_mode="LIVE",
        payload_checksum="a" * 64,
    )
    late_rating = TeamRatingObservation(
        team_id=context.home.id,
        rating=1700,
        method="TEST_ELO",
        version="v1",
        evidence_cutoff=LATE,
        observed_at=LATE,
        available_as_of=LATE,
        source="LIVE_RATING_TEST",
        source_mode="LIVE",
        payload_checksum="b" * 64,
    )
    session.add_all([early_odds, late_odds, early_statcast, late_statcast, early_rating, late_rating])
    session.commit()

    service = GameStateService(session)
    state = service.get_game_state(context.game.id, CUTOFF)
    assert state.weather.id == early_weather.id
    assert [(row.id, row.american_odds) for row in state.odds] == [(early_odds.id, -110)]
    assert [(row.id, row.values["xwoba"]) for row in state.home.statcast] == [(early_statcast.id, 0.31)]
    assert state.home.rating.id == early_rating.id

    updated = service.get_game_state(context.game.id, LATE + timedelta(minutes=1))
    assert updated.weather.id == late_weather.id
    assert [(row.id, row.american_odds) for row in updated.odds] == [(late_odds.id, -150)]
    assert [(row.id, row.values["xwoba"]) for row in updated.home.statcast] == [(late_statcast.id, 0.41)]
    assert updated.home.rating.id == late_rating.id


def test_postgame_final_score_is_not_selected_for_pregame_state(session) -> None:
    context = _base_game(session)
    pregame = _game_observation(context, EARLY)
    final = _game_observation(
        context,
        FIRST_PITCH + timedelta(hours=3),
        status="FINAL",
        home_score=9,
        away_score=1,
    )
    session.add_all([pregame, final])
    session.commit()

    state = GameStateService(session).get_game_state(context.game.id, CUTOFF)
    assert state.game_observation.id == pregame.id
    assert state.game_observation.status == "SCHEDULED"
    assert state.game_observation.home_score is None
    assert state.game_observation.away_score is None
    assert state.final_at is None


def test_live_state_never_selects_demo_observations(session) -> None:
    context = _base_game(session)
    context.session = session
    live_game = _game_observation(context, EARLY, source_mode="LIVE")
    demo_game = _game_observation(
        context,
        CUTOFF - timedelta(minutes=1),
        source_mode="DEMO",
        status="FINAL",
        home_score=99,
        away_score=0,
    )
    live_lineup, live_entry = _lineup(context, context.home_player, EARLY, "PROJECTED", source_mode="LIVE")
    _, demo_entry = _lineup(context, context.replacement, CUTOFF - timedelta(minutes=1), "CONFIRMED", source_mode="DEMO")
    live_weather = WeatherSnapshot(
        game_id=context.game.id,
        source="LIVE_WEATHER_TEST",
        observed_at=FIRST_PITCH,
        available_as_of=EARLY,
        temperature_f=72,
        source_mode="LIVE",
    )
    demo_weather = WeatherSnapshot(
        game_id=context.game.id,
        source="DEMO_WEATHER_TEST",
        observed_at=FIRST_PITCH,
        available_as_of=CUTOFF - timedelta(minutes=1),
        temperature_f=120,
        source_mode="DEMO",
    )
    session.add_all([live_game, demo_game, live_entry, demo_entry, live_weather, demo_weather])
    session.commit()

    state = GameStateService(session).get_game_state(context.game.id, CUTOFF)
    assert state.source_mode == "LIVE"
    assert state.game_observation.id == live_game.id
    assert state.home.lineup.id == live_lineup.id
    assert [entry.player_id for entry in state.home.lineup_entries] == [context.home_player.id]
    assert state.weather.id == live_weather.id


def test_observed_sides_survive_mutation_of_current_game_team_projection(session) -> None:
    normalizer = CanonicalNormalizer(session)
    games = normalizer.ingest_schedule(
        [ScheduleGameDTO(
            source_game_id="gate2-observed-sides",
            scheduled_at=FIRST_PITCH,
            event_date=FIRST_PITCH.date(),
            status="scheduled",
            away_team=TeamDTO(
                source_id="gate2-away-original",
                name="Gate 2 Original Away",
                abbreviation="G2OA",
            ),
            home_team=TeamDTO(
                source_id="gate2-home-original",
                name="Gate 2 Original Home",
                abbreviation="G2OH",
            ),
            venue=VenueDTO(source_id="gate2-stable-park", name="Gate 2 Stable Park"),
            source_mode="LIVE",
        )],
        "MLB_STATS_API",
        available_as_of=EARLY,
    )
    session.commit()
    game = games[0]
    observation = session.scalar(select(GameStateObservation).where(
        GameStateObservation.game_id == game.id,
    ))
    assert observation is not None
    observed_home_team_id = observation.home_team_id
    observed_away_team_id = observation.away_team_id
    assert observed_home_team_id is not None
    assert observed_away_team_id is not None

    replacement_home = Team(
        league_id=game.league_id,
        name="Gate 2 Mutable Projection Home",
        abbreviation="G2MH",
    )
    replacement_away = Team(
        league_id=game.league_id,
        name="Gate 2 Mutable Projection Away",
        abbreviation="G2MA",
    )
    session.add_all([replacement_home, replacement_away])
    session.flush()
    sides = list(session.scalars(select(GameTeam).where(GameTeam.game_id == game.id)))
    next(side for side in sides if side.is_home).team_id = replacement_home.id
    next(side for side in sides if not side.is_home).team_id = replacement_away.id
    session.commit()

    replay = GameStateService(session).get_game_state(game.id, CUTOFF)
    assert replay.home.team_id == observed_home_team_id
    assert replay.away.team_id == observed_away_team_id
    assert replay.home.team_id != replacement_home.id
    assert replay.away.team_id != replacement_away.id
    assert replay.home.starter is not None
    assert replay.away.starter is not None
    assert replay.home.starter.team_id == observed_home_team_id
    assert replay.away.starter.team_id == observed_away_team_id

    previous_pitch = FIRST_PITCH - timedelta(days=2)
    previous_game = Game(
        league_id=game.league_id,
        season_id=game.season_id,
        venue_id=observation.venue_id,
        starts_at=previous_pitch,
        status=GameStatus.FINAL,
        source_mode="LIVE",
    )
    session.add(previous_game)
    session.flush()
    previous_current_home = GameTeam(
        game_id=previous_game.id,
        team_id=observed_home_team_id,
        is_home=True,
    )
    previous_current_away = GameTeam(
        game_id=previous_game.id,
        team_id=observed_away_team_id,
        is_home=False,
    )
    session.add_all([
        previous_current_home,
        previous_current_away,
        GameStateObservation(
            game_id=previous_game.id,
            home_team_id=observed_home_team_id,
            away_team_id=observed_away_team_id,
            scheduled_at=previous_pitch,
            status="FINAL",
            is_postponed=False,
            venue_id=observation.venue_id,
            home_starter_confirmation="UNKNOWN",
            away_starter_confirmation="UNKNOWN",
            home_score=4,
            away_score=2,
            final_at=previous_pitch + timedelta(hours=3),
            observed_at=previous_pitch + timedelta(hours=3),
            available_as_of=previous_pitch + timedelta(hours=3),
            source="MLB_STATS_API",
            source_mode="LIVE",
            payload_checksum="c" * 64,
        ),
    ])
    session.commit()
    previous_current_home.team_id = replacement_home.id
    previous_current_away.team_id = replacement_away.id
    session.commit()

    replay_with_history = GameStateService(session).get_game_state(game.id, CUTOFF)
    assert replay_with_history.home.days_since_previous_game == 2.0
    assert replay_with_history.away.days_since_previous_game == 2.0
    assert replay_with_history.home.previous_venue_id == observation.venue_id
    assert replay_with_history.away.previous_venue_id == observation.venue_id

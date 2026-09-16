"""League isolation: a sport-specific process selects and accepts only its sport.

``source_mode`` is provenance, not sport. The NBA point-Elo importer stores its
game LIVE (2026-10-20 19:00Z), so every MLB job and MLB slate must select by
league as well as by mode, and every MLB entry point must refuse another
league's game even when a caller hands it in directly.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.jobs.orchestrator import JobOrchestrator
from app.models import (
    AuditEvent,
    ExperimentDecisionPhase,
    Game,
    GameRefreshState,
    GameStatus,
    GameTeam,
    League,
    Season,
    ShadowExperimentDecision,
    Sport,
    Team,
)
from app.services.forecast_service import ForecastService
from app.services.game_refresh_state_service import GameRefreshStateService
from app.services.game_state_service import GameStateUnavailableError
from app.services.league_scope import LEAGUE_MISMATCH, MLB, LeagueMismatchError, in_league
from app.services.live_shadow_service import LiveShadowOperator
from app.services.pregame_evidence_monitor import PregameEvidenceMonitor
from app.services.read_service import ReadService
from app.services.shadow_experiment_service import ShadowExperimentService
from app.services.v18_forecast_view import NO_V18_FORECAST, NOT_AN_MLB_GAME, resolve_for_decision

SLATE = date(2026, 10, 20)
TIPOFF = datetime(2026, 10, 20, 19, 0, tzinfo=UTC)
MORNING = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


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


def _game(session: Session, code: str, *, source_mode: str = "LIVE", starts_at: datetime = TIPOFF,
          home: str = "BOS", away: str = "DET") -> Game:
    league = _league(session, code)
    season = Season(league_id=league.id, name=f"{code}-2026",
                    starts_at=datetime(2026, 1, 1, tzinfo=UTC), ends_at=datetime(2026, 12, 31, tzinfo=UTC))
    session.add(season)
    session.flush()
    game = Game(league_id=league.id, season_id=season.id, starts_at=starts_at,
                status=GameStatus.SCHEDULED, source_mode=source_mode)
    session.add(game)
    session.flush()
    for abbreviation, is_home in ((home, True), (away, False)):
        team = session.scalar(select(Team).where(Team.league_id == league.id, Team.abbreviation == abbreviation))
        if team is None:
            team = Team(league_id=league.id, name=f"{code} {abbreviation}", abbreviation=abbreviation)
            session.add(team)
            session.flush()
        session.add(GameTeam(game_id=game.id, team_id=team.id, is_home=is_home))
    session.flush()
    return game


def _snapshot(session: Session) -> list[tuple]:
    return sorted(
        (str(g.id), g.league_id, g.status, g.source_mode, g.starts_at, g.season_id)
        for g in session.scalars(select(Game))
    )


@pytest.fixture
def mixed_day(session: Session) -> dict[str, Game]:
    """One day holding an MLB game, the NBA game as the importer stores it, a
    hypothetical LIVE College Football game and an NFL research-shadow game."""

    games = {
        "MLB": _game(session, "MLB", starts_at=TIPOFF + timedelta(minutes=5)),
        "NBA": _game(session, "NBA"),
        "CFB": _game(session, "CFB", home="DEL", away="CCU"),
        "NFL": _game(session, "NFL", source_mode="RESEARCH_SHADOW", home="DET", away="BOS"),
    }
    session.commit()
    return games


def test_mixed_day_mlb_slate_holds_only_the_mlb_game(session, settings, mixed_day) -> None:
    mlb = mixed_day["MLB"]
    before = _snapshot(session)

    assert JobOrchestrator(session, settings)._games_for_date(SLATE, MLB) == [mlb]
    read = ReadService(session, settings)
    assert read._games_for_date(SLATE, MLB) == [mlb]
    assert read._live_games_for_date(SLATE, MLB) == [mlb]
    # The selector is not MLB-special: each league gets its own games.
    assert read._live_games_for_date(SLATE, "NBA") == [mixed_day["NBA"]]
    assert JobOrchestrator(session, settings)._games_for_date(SLATE, "CFB") == [mixed_day["CFB"]]

    # Selection mutates nothing and creates nothing.
    assert _snapshot(session) == before


def test_a_date_with_only_other_leagues_is_an_empty_mlb_slate(session, settings) -> None:
    _game(session, "NBA")
    _game(session, "CFB", home="DEL", away="CCU")
    _game(session, "NFL", source_mode="RESEARCH_SHADOW")
    session.commit()

    for demo_mode in (True, False):
        configured = settings.model_copy(update={"demo_mode": demo_mode})
        assert JobOrchestrator(session, configured)._games_for_date(SLATE, MLB) == []
        assert ReadService(session, configured)._games_for_date(SLATE, MLB) == []
        assert ReadService(session, configured)._live_games_for_date(SLATE, MLB) == []


def test_mlb_timing_selectors_ignore_live_games_of_other_leagues(session, settings) -> None:
    nba = _game(session, "NBA")
    cfb = _game(session, "CFB", home="DEL", away="CCU")
    session.commit()
    now = TIPOFF - timedelta(hours=1)
    orchestrator = JobOrchestrator(session, settings)

    assert orchestrator.odds_pregame_window_open(now) is False
    assert LiveShadowOperator(session, settings).monitored_games(now) == []
    assert GameRefreshStateService(session).record_due_states(as_of=now) == []
    assert session.scalar(select(func.count(GameRefreshState.id))) == 0

    # The same timing condition still selects an MLB game.
    mlb = _game(session, "MLB")
    session.commit()
    assert orchestrator.odds_pregame_window_open(now) is True
    assert LiveShadowOperator(session, settings).monitored_games(now) == [mlb]
    assert {nba.id, cfb.id}.isdisjoint(
        {game.id for game in LiveShadowOperator(session, settings).monitored_games(now)})


def test_pregame_evidence_monitor_raises_no_alert_for_another_leagues_game(session, settings) -> None:
    nba = _game(session, "NBA")
    session.commit()

    result = PregameEvidenceMonitor(session, settings).run(SLATE, now=TIPOFF - timedelta(minutes=5))

    assert result["games"] == 0
    alerts = session.scalars(select(AuditEvent).where(AuditEvent.entity_id == nba.id)).all()
    assert alerts == []


@pytest.mark.asyncio
async def test_generate_forecasts_skips_a_game_without_state_and_never_sees_nba(
    session, settings, mixed_day,
) -> None:
    before = _snapshot(session)

    # Before the fix the NBA game raised GameStateUnavailableError here and
    # aborted daily_pipeline ahead of position publishing and the shadow freeze.
    result = await JobOrchestrator(session, settings).generate_forecasts(SLATE)

    assert result["forecasts"] == 0
    skipped = {entry["game_id"] for entry in result["skipped"]}
    assert skipped == {str(mixed_day["MLB"].id)}
    assert _snapshot(session) == before


def test_forecast_generation_refuses_an_explicit_non_mlb_game(session, settings, mixed_day) -> None:
    service = ForecastService(session, settings)
    for code in ("NBA", "CFB", "NFL"):
        with pytest.raises(LeagueMismatchError) as refused:
            service.generate_for_game(mixed_day[code])
        assert refused.value.receipt() == {
            "game_id": str(mixed_day[code].id), "reason": LEAGUE_MISMATCH,
            "process": "MLB forecast generation", "expected_league": MLB, "actual_league": code,
        }
    # An MLB game passes the league gate and fails later, on its missing evidence.
    with pytest.raises(GameStateUnavailableError):
        service.generate_for_game(mixed_day["MLB"])


def test_v18_decision_input_refuses_an_explicit_non_mlb_game(session, mixed_day) -> None:
    cutoff = TIPOFF - timedelta(minutes=15)
    for code in ("NBA", "CFB"):
        assert resolve_for_decision(session, mixed_day[code].id, cutoff=cutoff) == (None, NOT_AN_MLB_GAME)
        assert resolve_for_decision(session, str(mixed_day[code].id), cutoff=cutoff) == (None, NOT_AN_MLB_GAME)
    assert resolve_for_decision(session, mixed_day["MLB"].id, cutoff=cutoff) == (None, NO_V18_FORECAST)


def test_experiment_refuses_decisions_and_positions_on_a_non_mlb_game(session, settings, mixed_day) -> None:
    service = ShadowExperimentService(session, settings)
    experiment = service.ensure_started(start_date=SLATE, now=MORNING, code_commit="test-commit")
    session.commit()
    nba = mixed_day["NBA"]

    assert service._append_decision(
        experiment, nba, None, ExperimentDecisionPhase.MORNING, None, MORNING) is None
    assert service._create_canonical_position(experiment, nba, SLATE, MORNING) is False
    assert session.scalar(select(func.count(ShadowExperimentDecision.id)).where(
        ShadowExperimentDecision.game_id == nba.id)) == 0


def test_freeze_on_an_nba_only_day_writes_no_decision(session, settings) -> None:
    nba = _game(session, "NBA")
    session.commit()
    service = ShadowExperimentService(session, settings)
    experiment = service.ensure_started(start_date=SLATE, now=MORNING, code_commit="test-commit")
    session.commit()

    for phase in ExperimentDecisionPhase:
        result = service.freeze(SLATE, phase=phase, now=TIPOFF - timedelta(minutes=10), experiment=experiment)
        assert result["frozen"] == 0 and result["primary"] == 0

    assert session.scalar(select(func.count(ShadowExperimentDecision.id))) == 0
    assert session.get(Game, nba.id).source_mode == "LIVE"


def test_injury_team_abbreviation_resolves_only_to_an_mlb_team(session, settings) -> None:
    # The NBA "BOS" team exists first, so an unscoped lookup would find it.
    nba = _game(session, "NBA", home="BOS")
    mlb = _game(session, "MLB", home="BOS", away="NYY", starts_at=TIPOFF + timedelta(hours=4))
    session.commit()

    game_id = JobOrchestrator(session, settings)._resolve_injury_game(SimpleNamespace(team_source_id="bos"), SLATE)

    assert game_id == mlb.id != nba.id


def test_in_league_needs_no_join_and_matches_only_that_league(session, mixed_day) -> None:
    for code, game in mixed_day.items():
        assert session.scalars(select(Game.id).where(in_league(code))).all() == [game.id]
    assert session.scalars(select(Game.id).where(in_league("MMA"))).all() == []

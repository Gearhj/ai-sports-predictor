"""College Football research forecasts: canonical game admission and display.

A forecast's game is admitted by its own CFBD ids as a RESEARCH_SHADOW row, so
it never enters the MLB LIVE paths; admission is idempotent; and admission
grants nothing -- paper eligibility stays keyed to the exact forecast id.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cfb.game_binding import CfbBindingError, admit_game, canonical_game_id
from app.cfb.read_service import research_board
from app.models import CfbResearchForecast, ExternalIdMapping, Game, GameTeam, League

KICKOFF = datetime(2026, 9, 19, 15, 30, tzinfo=UTC)


def _forecast(session: Session, *, game_id: str = "401869940", home: str = "Delaware",
              away: str = "Coastal Carolina", home_id: str = "48", away_id: str = "324",
              joint: float = 0.7315, ridge: float = 0.7240, elo: float = 0.5582,
              kickoff: datetime = KICKOFF) -> CfbResearchForecast:
    moment = datetime(2026, 9, 16, 15, tzinfo=UTC)
    row = CfbResearchForecast(
        forecast_id=f"cfb-gen1:{uuid4().hex}", forecast_sha256=uuid4().hex + uuid4().hex,
        forecast_bytes=100, forecast_schema="CFB_GEN1_PROSPECTIVE_RESEARCH_FORECAST_V1",
        package_index_sha256=uuid4().hex + uuid4().hex, package_index_bytes=10,
        baseline_configuration="CFB_GEN1_RIDGE_A100_PAIRED_RESIDUAL_V1",
        source_provider="CFBD", source_game_id=game_id,
        home_source_team_id=home_id, away_source_team_id=away_id,
        home_team_name=home, away_team_name=away,
        neutral_site=False, conference_competition=False, captured_kickoff_utc=kickoff,
        expected_away_points=22.77, expected_home_points=32.95, expected_total=55.72,
        expected_home_margin=10.18,
        ridge_home_win_probability=ridge, ridge_away_win_probability=1 - ridge,
        elo_home_win_probability=elo, elo_away_win_probability=1 - elo,
        joint_home_win_probability=joint, joint_away_win_probability=1 - joint,
        tie_probability=0.0, draw_count=10000, pmfs={}, intervals=[], illustrative_totals=[],
        source_cutoff_utc=datetime(2026, 9, 14, 21, 59, tzinfo=UTC),
        forecast_created_utc=moment, imported_at=moment, research_only=True,
    )
    session.add(row)
    session.flush()
    return row


def test_admission_registers_a_research_shadow_game_by_cfbd_ids(session: Session) -> None:
    row = _forecast(session)
    result = admit_game(session, row)

    assert result["result"] == "ADMITTED"
    game = session.get(Game, canonical_game_id(session, "401869940"))
    assert game is not None
    # Never a LIVE row: the MLB jobs and slates select LIVE games by date.
    assert game.source_mode == "RESEARCH_SHADOW"
    assert game.starts_at.replace(tzinfo=UTC) == KICKOFF
    assert session.get(League, game.league_id).code == "CFB"
    sides = {gt.is_home: gt.team_id for gt in session.scalars(
        select(GameTeam).where(GameTeam.game_id == game.id))}
    mapped = {m.external_id: m.entity_id for m in session.scalars(
        select(ExternalIdMapping).where(ExternalIdMapping.source == "CFBD",
                                        ExternalIdMapping.entity_type == "team"))}
    assert sides[True] == mapped["48"] and sides[False] == mapped["324"]


def test_admission_is_idempotent(session: Session) -> None:
    row = _forecast(session)
    first = admit_game(session, row)
    second = admit_game(session, row)
    assert second["result"] == "ALREADY_ADMITTED"
    assert second["game_id"] == first["game_id"]
    assert len(session.scalars(select(Game)).all()) == 1


def test_a_disagreeing_forecast_for_an_admitted_game_is_refused(session: Session) -> None:
    admit_game(session, _forecast(session))
    swapped = _forecast(session, home_id="324", away_id="48")
    with pytest.raises(CfbBindingError, match="disagrees"):
        admit_game(session, swapped)
    moved = _forecast(session, kickoff=datetime(2026, 9, 19, 19, tzinfo=UTC))
    with pytest.raises(CfbBindingError, match="kicks off"):
        admit_game(session, moved)


def test_a_forecast_without_a_cfbd_game_id_is_refused(session: Session) -> None:
    with pytest.raises(CfbBindingError, match="CFBD game id"):
        admit_game(session, _forecast(session, game_id="coastal-delaware"))


def test_the_board_shows_binding_eligibility_and_each_forecasts_own_favourite(session: Session) -> None:
    bound = _forecast(session)
    admit_game(session, bound)
    _forecast(session, game_id="401858225", home="Pittsburgh", away="Syracuse",
              home_id="221", away_id="183", joint=0.7692, ridge=0.7478, elo=0.6693,
              kickoff=datetime(2026, 9, 17, 23, 30, tzinfo=UTC))
    split = _forecast(session, game_id="401869999", home="Home U", away="Away U",
                      home_id="1", away_id="2", joint=0.55, ridge=0.52, elo=0.45)

    board = {f["game"]["source_game_id"]: f for f in research_board(session)["forecasts"]}
    new = board["401869940"]
    assert new["canonical_game"]["bound"] is True
    assert new["paper_eligibility"]["eligible"] is False
    assert "Research only" in new["paper_eligibility"]["label"]
    assert new["horizon"] == "PROSPECTIVE_RESEARCH"
    assert "not a final pregame forecast" in new["horizon_label"]
    assert new["primary_method_note"].startswith("All three methods favor Delaware.")
    assert [w["method"] for w in new["win_probabilities"]] == ["joint", "ridge", "elo"]
    assert all(w["meaning"] for w in new["win_probabilities"])

    assert board["401858225"]["canonical_game"]["bound"] is False
    assert "Pittsburgh" in board["401858225"]["primary_method_note"]
    assert "disagree" in board[split.source_game_id]["primary_method_note"]
    assert "Elo benchmark favors Away U" in board[split.source_game_id]["primary_method_note"]


def test_an_admitted_game_never_fills_an_empty_mlb_slate(session: Session, settings) -> None:
    from datetime import date

    from app.services.read_service import ReadService

    admit_game(session, _forecast(session))
    reader = ReadService(session, settings)
    # No LIVE game exists on this date; the demo fallback must still skip research rows.
    assert reader._games_for_date(date(2026, 9, 19)) == []
    assert reader._live_games_for_date(date(2026, 9, 19)) == []

"""One state, one row, and a check time that moves.

The behaviour under test is the reason the whole feature can tell a quiet inning
apart from an outage. A two-minute poll over a three-hour game is ninety checks
and in most of them nothing has changed; if every check wrote a row the table
would be noise, and if none of them recorded anything a reader would be shown a
score that looks half an hour old when it was confirmed thirty seconds ago.

So: an unchanged state advances its own check time and writes nothing, and a
changed state is a new row that leaves the old one alone.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.scoreboard import GameScoreboardObservation
from app.scoreboard.read_service import assess_freshness, latest_for, scoreboard_for
from app.scoreboard.store import record_many, record_observation


def state(**over):
    base = {
        "league_code": "MLB",
        "source": "MLB_STATS_API",
        "source_mode": "LIVE",
        "source_game_id": "824225",
        "status": "IN_PROGRESS",
        "status_detail": "In Progress",
        "away_score": 2,
        "home_score": 1,
        "period_number": 5,
        "period_label": "5th",
        "period_state": "Top",
        "in_break": False,
        "clock_seconds_remaining": None,
        "outs": 1,
        "balls": 2,
        "strikes": 1,
        "bases": {"first": None, "second": None, "third": None},
        "periods": [{"number": 1, "label": "1st", "away": 2, "home": 1}],
        "periods_complete": True,
        "periods_reconcile": None,
        "source_observed_at": None,
        "retrieved_at": datetime.now(UTC),
        "checked_at": datetime.now(UTC),
        "payload_checksum": "a" * 64,
    }
    base.update(over)
    return base


@pytest.fixture
def game(session: Session):
    """A game with both clubs attached: orientation is part of what is tested."""

    from app.models import Game, GameTeam, League, Season, Sport, Team, Venue

    sport = Sport(name="Baseball", code="BASEBALL")
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, name="MLB", code="MLB")
    session.add(league)
    session.flush()
    season = Season(
        league_id=league.id, name="2026",
        starts_at=datetime(2026, 3, 1, tzinfo=UTC),
        ends_at=datetime(2026, 11, 1, tzinfo=UTC),
    )
    venue = Venue(name="Test Park")
    away = Team(league_id=league.id, name="Colorado Rockies", abbreviation="COL")
    home = Team(league_id=league.id, name="Detroit Tigers", abbreviation="DET")
    session.add_all([season, venue, away, home])
    session.flush()
    row = Game(
        league_id=league.id, season_id=season.id, venue_id=venue.id,
        starts_at=datetime.now(UTC), status="LIVE",
    )
    session.add(row)
    session.flush()
    session.add_all([
        GameTeam(game_id=row.id, team_id=home.id, is_home=True),
        GameTeam(game_id=row.id, team_id=away.id, is_home=False),
    ])
    session.commit()
    return row


def test_the_same_state_seen_twice_is_one_row_with_a_later_check(session, game):
    first = datetime.now(UTC)
    later = first + timedelta(minutes=2)

    row, created = record_observation(
        session, game_id=game.id, state=state(), checked_at=first)
    assert created is True

    same, created_again = record_observation(
        session, game_id=game.id, state=state(), checked_at=later)
    assert created_again is False
    assert same.id == row.id
    # The check moved; nothing else did.
    assert same.checked_at == later
    assert same.retrieved_at == row.retrieved_at
    assert session.scalar(select(func.count(GameScoreboardObservation.id))) == 1


def test_a_changed_score_is_a_new_row_beside_the_old_one(session, game):
    first = datetime.now(UTC)
    record_observation(session, game_id=game.id, state=state(), checked_at=first)
    record_observation(
        session, game_id=game.id,
        state=state(away_score=3, payload_checksum="b" * 64),
        checked_at=first + timedelta(minutes=2),
    )
    session.commit()
    rows = session.scalars(select(GameScoreboardObservation)).all()
    assert len(rows) == 2
    # The earlier reading -- the one a reader may have acted on -- survives.
    assert {row.away_score for row in rows} == {2, 3}


def test_a_stale_check_never_moves_the_time_backwards(session, game):
    now = datetime.now(UTC)
    record_observation(session, game_id=game.id, state=state(), checked_at=now)
    row, _ = record_observation(
        session, game_id=game.id, state=state(), checked_at=now - timedelta(minutes=10))
    # Compared as instants: SQLite hands the column back naive, and the point
    # being tested is the ordering, not the tzinfo the backend chose.
    stored = row.checked_at
    stored = stored if stored.tzinfo else stored.replace(tzinfo=UTC)
    assert stored == now


def test_the_latest_reading_is_the_most_recently_confirmed_one(session, game):
    now = datetime.now(UTC)
    # Written later, confirmed earlier: a correction that was then superseded.
    record_observation(
        session, game_id=game.id, state=state(), checked_at=now)
    record_observation(
        session, game_id=game.id,
        state=state(away_score=9, payload_checksum="c" * 64),
        checked_at=now - timedelta(minutes=30))
    session.commit()
    latest = latest_for(session, [game.id])[game.id]
    assert latest.away_score == 2


def test_a_reload_does_not_make_a_stale_reading_look_fresh(session, game):
    """The failure the brief names: refreshing must not disguise stale evidence."""

    stale = datetime.now(UTC) - timedelta(minutes=45)
    record_observation(session, game_id=game.id, state=state(), checked_at=stale)
    session.commit()

    # Two "page loads" at different wall-clock moments read the same row and
    # both report it as old, because freshness comes from the stored check.
    first = scoreboard_for(session, [game.id])[str(game.id)]
    second = scoreboard_for(session, [game.id])[str(game.id)]
    assert first["freshness"]["state"] == "UNAVAILABLE"
    assert second["freshness"]["state"] == "UNAVAILABLE"
    # And the last known score is still shown rather than blanked.
    assert first["away_score"] == 2


def test_the_payload_never_presents_retrieval_as_the_score_timestamp(session, game):
    record_observation(session, game_id=game.id, state=state())
    session.commit()
    payload = scoreboard_for(session, [game.id])[str(game.id)]
    assert payload["source"]["source_observed_at"] is None
    assert payload["source"]["retrieved_at"] is not None
    assert payload["freshness"]["source_latency"] == "unknown"
    assert "when it last checked but not when the score changed" in (
        payload["source"]["timestamp_note"])


def test_a_final_score_does_not_claim_a_settled_wager(session, game):
    record_observation(
        session, game_id=game.id,
        state=state(status="FINAL", status_detail="Final", periods_reconcile=True),
    )
    session.commit()
    payload = scoreboard_for(session, [game.id])[str(game.id)]
    assert payload["is_final"] is True
    assert "settled" in payload["settlement_note"]
    assert "may still be pending" in payload["settlement_note"]


def test_a_game_with_no_observation_says_so_rather_than_showing_zeros(session, game):
    payload = scoreboard_for(session, [game.id])[str(game.id)]
    assert payload["available"] is False
    assert "No score has been collected" in payload["unavailable_reason"]
    assert "away_score" not in payload


def test_record_many_reports_new_rows_against_confirmations(session, game):
    now = datetime.now(UTC)
    first = record_many(session, [(game.id, state())], checked_at=now)
    again = record_many(
        session, [(game.id, state())], checked_at=now + timedelta(minutes=2))
    assert first == {"observations_written": 1, "states_confirmed": 0}
    assert again == {"observations_written": 0, "states_confirmed": 1}


def test_an_unknown_field_is_refused_rather_than_silently_dropped(session, game):
    with pytest.raises(ValueError, match="unknown scoreboard fields"):
        record_observation(
            session, game_id=game.id, state=state(home_runs_hit=4))


def test_an_unplayed_period_reaches_the_payload_as_unplayed(session, game):
    record_observation(session, game_id=game.id, state=state(
        status="FINAL",
        periods=[
            {"number": 8, "label": "8th", "away": 0, "home": 0},
            {"number": 9, "label": "9th", "away": 0, "home": None},
        ],
    ))
    session.commit()
    periods = scoreboard_for(session, [game.id])[str(game.id)]["periods"]
    assert periods[-1]["home_played"] is False
    assert periods[-1]["away_played"] is True
    assert periods[-1]["home"] is None

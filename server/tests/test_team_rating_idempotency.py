"""Regression coverage for the finalize-games UniqueViolation on
uq_team_rating_observation.

A split doubleheader puts the same team in one schedule payload twice with
diverging leagueRecord values, so two record_log_odds_shrunk observations used
to be staged for one (team, method, version, source, source_mode,
available_as_of) key and the flush failed the whole finalize run. The insert
site must skip same-key duplicates regardless of payload and absorb the
check-then-insert race between concurrent finalize paths.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from app.data_sources.base import ScheduleGameDTO, TeamDTO, VenueDTO
from app.ingestion import CanonicalNormalizer
from app.models import Team, TeamRatingObservation


EVENT_DATE = date(2026, 8, 30)
GAME_ONE_START = datetime(2026, 8, 30, 17, 5, tzinfo=UTC)
GAME_TWO_START = datetime(2026, 8, 30, 23, 10, tzinfo=UTC)
FETCHED_AT = datetime(2026, 8, 31, 3, 12, tzinfo=UTC)


def _doubleheader_game(
    game_number: int,
    scheduled_at: datetime,
    home_record: tuple[int, int],
    away_record: tuple[int, int],
    status: str,
) -> ScheduleGameDTO:
    return ScheduleGameDTO(
        source_game_id=f"dh-record-game-{game_number}",
        scheduled_at=scheduled_at,
        event_date=EVENT_DATE,
        status=status,
        away_team=TeamDTO(
            source_id="147",
            name="Doubleheader Away",
            abbreviation="DHA",
            record_wins=away_record[0],
            record_losses=away_record[1],
        ),
        home_team=TeamDTO(
            source_id="111",
            name="Doubleheader Home",
            abbreviation="DHH",
            record_wins=home_record[0],
            record_losses=home_record[1],
        ),
        venue=VenueDTO(source_id="1", name="Doubleheader Park"),
        source_mode="LIVE",
        doubleheader_status="Y",
        game_number=game_number,
    )


def _rating_rows(session, team_id) -> list[TeamRatingObservation]:
    return list(session.scalars(
        select(TeamRatingObservation).where(
            TeamRatingObservation.team_id == team_id,
            TeamRatingObservation.method == "record_log_odds_shrunk",
        ).order_by(TeamRatingObservation.available_as_of)
    ))


def test_split_doubleheader_with_diverging_records_persists_one_rating_per_team(session) -> None:
    normalizer = CanonicalNormalizer(session)
    games = normalizer.ingest_schedule(
        [
            _doubleheader_game(1, GAME_ONE_START, home_record=(71, 60), away_record=(60, 71), status="Final"),
            _doubleheader_game(2, GAME_TWO_START, home_record=(70, 60), away_record=(61, 70), status="Scheduled"),
        ],
        "MLB_STATS_API",
        available_as_of=FETCHED_AT,
    )
    session.commit()

    assert len(games) == 2
    home = session.scalar(select(Team).where(Team.name == "Doubleheader Home"))
    away = session.scalar(select(Team).where(Team.name == "Doubleheader Away"))
    home_rows = _rating_rows(session, home.id)
    away_rows = _rating_rows(session, away.id)
    assert len(home_rows) == 1
    assert len(away_rows) == 1
    # The first observation at a knowledge-time key wins; the conflicting
    # payload from the second doubleheader entry is skipped, not merged.
    assert home_rows[0].components["wins"] == 71
    assert away_rows[0].components["wins"] == 60


def test_same_key_reingestion_is_skipped_and_new_fetch_time_still_appends(session) -> None:
    normalizer = CanonicalNormalizer(session)
    _, league, _, _ = normalizer.ensure_core(EVENT_DATE.year)
    team = Team(league_id=league.id, name="Rating Idempotency Team", abbreviation="RIT")
    session.add(team)
    session.flush()

    normalizer._append_record_rating(team.id, 70, 60, "MLB_STATS_API", "LIVE", FETCHED_AT, None)
    session.commit()
    # Same key with a changed record must not raise and must not mutate the
    # committed append-only row.
    normalizer._append_record_rating(team.id, 71, 60, "MLB_STATS_API", "LIVE", FETCHED_AT, None)
    session.commit()

    rows = _rating_rows(session, team.id)
    assert len(rows) == 1
    assert rows[0].components["wins"] == 70

    # A later fetch time is a new knowledge-time key and still appends.
    normalizer._append_record_rating(
        team.id, 71, 60, "MLB_STATS_API", "LIVE", FETCHED_AT + timedelta(minutes=30), None,
    )
    session.commit()
    assert len(_rating_rows(session, team.id)) == 2


def test_concurrent_check_then_insert_race_is_absorbed(session, monkeypatch) -> None:
    normalizer = CanonicalNormalizer(session)
    _, league, _, _ = normalizer.ensure_core(EVENT_DATE.year)
    team = Team(league_id=league.id, name="Rating Race Team", abbreviation="RRT")
    session.add(team)
    session.flush()

    normalizer._append_record_rating(team.id, 70, 60, "MLB_STATS_API", "LIVE", FETCHED_AT, None)
    session.commit()

    # Simulate the interleaved finalize path whose existence check ran before
    # the other transaction's row became visible: the insert itself must land
    # on ON CONFLICT DO NOTHING instead of failing the run.
    monkeypatch.setattr(session, "scalar", lambda *args, **kwargs: None)
    normalizer._append_record_rating(team.id, 70, 60, "MLB_STATS_API", "LIVE", FETCHED_AT, None)
    session.commit()

    assert len(_rating_rows(session, team.id)) == 1

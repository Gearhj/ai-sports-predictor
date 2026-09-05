"""INT-008: recurring finalization must not abort on conflicting team records.

A doubleheader puts the same team in two games at the same batch timestamp. The
old dedup lookup keyed on ``payload_checksum`` while ``uq_team_rating_observation``
excludes it, so a *differing* record for that team bypassed the lookup and then
collided at INSERT, raising IntegrityError and skipping freeze/settlement for the
whole date.

These tests build synthetic doubleheader schedules and call the real
``CanonicalNormalizer.ingest_schedule`` (no provider calls). They run on SQLite
and, when ``AISP_TEST_PG_URL`` is set, on the disposable PostgreSQL cluster too.
``autoflush=False`` matches the production session, under which the original bug
reproduced.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.audit import install_immutability_guards
from app.data_sources.base import ScheduleGameDTO, TeamDTO, VenueDTO
from app.db.base import Base
from app.ingestion.normalizer import CanonicalNormalizer
from app.models import Team, TeamRatingObservation

EVENT_DATE = date(2026, 8, 12)
KNOWLEDGE_TIME = datetime(2026, 8, 12, 16, 0, tzinfo=UTC)
FIRST_PITCH_1 = datetime(2026, 8, 12, 17, 5, tzinfo=UTC)
FIRST_PITCH_2 = datetime(2026, 8, 12, 20, 40, tzinfo=UTC)

_PG_URL = os.getenv("AISP_TEST_PG_URL")
_BACKENDS = ["sqlite"] + (["postgresql"] if _PG_URL else [])


@pytest.fixture(params=_BACKENDS)
def nf_session(request) -> Session:
    """Fresh schema on each backend, with a production-like autoflush=False session."""
    if request.param == "sqlite":
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
    else:
        engine = create_engine(_PG_URL, future=True)
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    install_immutability_guards()
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        yield session
        session.rollback()
    Base.metadata.drop_all(engine)
    engine.dispose()


def _game(
    game_id: str,
    scheduled_at: datetime,
    *,
    home_wins: int,
    home_losses: int,
    away_wins: int,
    away_losses: int,
    mode: str = "LIVE",
) -> ScheduleGameDTO:
    return ScheduleGameDTO(
        source_game_id=game_id,
        scheduled_at=scheduled_at,
        event_date=EVENT_DATE,
        status="scheduled",
        home_team=TeamDTO(
            source_id="HOME", name="Home Nine", abbreviation="HOM",
            record_wins=home_wins, record_losses=home_losses,
        ),
        away_team=TeamDTO(
            source_id="AWAY", name="Away Nine", abbreviation="AWY",
            record_wins=away_wins, record_losses=away_losses,
        ),
        venue=VenueDTO(source_id="V1", name="Doubleheader Park"),
        source_mode=mode,
    )


def _ratings_for(session: Session, abbrev: str) -> list[TeamRatingObservation]:
    team = session.scalar(select(Team).where(Team.abbreviation == abbrev))
    if team is None:
        return []
    return list(session.scalars(
        select(TeamRatingObservation).where(TeamRatingObservation.team_id == team.id)
    ))


def test_identical_doubleheader_records_one_rating_per_team(nf_session: Session) -> None:
    normalizer = CanonicalNormalizer(nf_session)
    games = [
        _game("dh-1", FIRST_PITCH_1, home_wins=50, home_losses=40, away_wins=45, away_losses=45),
        _game("dh-2", FIRST_PITCH_2, home_wins=50, home_losses=40, away_wins=45, away_losses=45),
    ]
    persisted = normalizer.ingest_schedule(games, "TEST_SOURCE", available_as_of=KNOWLEDGE_TIME)
    nf_session.commit()
    assert len(persisted) == 2
    assert len(_ratings_for(nf_session, "HOM")) == 1
    assert len(_ratings_for(nf_session, "AWY")) == 1
    assert normalizer.rating_conflicts == []


def test_conflicting_doubleheader_records_resolve_without_abort(nf_session: Session) -> None:
    normalizer = CanonicalNormalizer(nf_session)
    games = [
        _game("dh-1", FIRST_PITCH_1, home_wins=50, home_losses=40, away_wins=45, away_losses=45),
        # Same team, same batch timestamp, DIFFERENT record -> old code raised here.
        _game("dh-2", FIRST_PITCH_2, home_wins=51, home_losses=40, away_wins=45, away_losses=45),
    ]
    persisted = normalizer.ingest_schedule(games, "TEST_SOURCE", available_as_of=KNOWLEDGE_TIME)
    nf_session.commit()  # must not raise IntegrityError

    assert len(persisted) == 2  # no partial schedule loss
    home_ratings = _ratings_for(nf_session, "HOM")
    assert len(home_ratings) == 1  # exactly one lawful rating per natural key
    # Canonical = most complete record (51-40 -> 91 games).
    assert home_ratings[0].sample_size == 91.0
    assert len(_ratings_for(nf_session, "AWY")) == 1  # unaffected team still rated
    assert len(normalizer.rating_conflicts) == 1
    conflict = normalizer.rating_conflicts[0]
    assert conflict["reason"] == "intra_batch_conflict_resolved"
    assert conflict["chosen_record"] == "51-40"
    assert len(conflict["conflicting_checksums"]) == 2


def test_reversed_game_order_yields_same_canonical(nf_session: Session) -> None:
    normalizer = CanonicalNormalizer(nf_session)
    reversed_games = [
        _game("dh-2", FIRST_PITCH_2, home_wins=51, home_losses=40, away_wins=45, away_losses=45),
        _game("dh-1", FIRST_PITCH_1, home_wins=50, home_losses=40, away_wins=45, away_losses=45),
    ]
    normalizer.ingest_schedule(reversed_games, "TEST_SOURCE", available_as_of=KNOWLEDGE_TIME)
    nf_session.commit()
    home_ratings = _ratings_for(nf_session, "HOM")
    assert len(home_ratings) == 1
    # Order-independent: the 91-game record wins regardless of processing order.
    assert home_ratings[0].sample_size == 91.0


def test_identical_retry_is_idempotent(nf_session: Session) -> None:
    games = [
        _game("dh-1", FIRST_PITCH_1, home_wins=50, home_losses=40, away_wins=45, away_losses=45),
        _game("dh-2", FIRST_PITCH_2, home_wins=50, home_losses=40, away_wins=45, away_losses=45),
    ]
    CanonicalNormalizer(nf_session).ingest_schedule(games, "TEST_SOURCE", available_as_of=KNOWLEDGE_TIME)
    nf_session.commit()
    # A repeated scheduler run / process retry after commit ambiguity.
    CanonicalNormalizer(nf_session).ingest_schedule(games, "TEST_SOURCE", available_as_of=KNOWLEDGE_TIME)
    nf_session.commit()
    total = nf_session.scalar(select(func.count()).select_from(TeamRatingObservation))
    assert total == 2  # no duplicates on retry


def test_conflicting_retry_keeps_persisted_evidence(nf_session: Session) -> None:
    first = [_game("dh-1", FIRST_PITCH_1, home_wins=50, home_losses=40, away_wins=45, away_losses=45)]
    CanonicalNormalizer(nf_session).ingest_schedule(first, "TEST_SOURCE", available_as_of=KNOWLEDGE_TIME)
    nf_session.commit()
    persisted_checksum = _ratings_for(nf_session, "HOM")[0].payload_checksum

    # A later run reports a DIFFERENT record for the same natural key.
    conflicting = [_game("dh-1", FIRST_PITCH_1, home_wins=52, home_losses=40, away_wins=45, away_losses=45)]
    normalizer = CanonicalNormalizer(nf_session)
    normalizer.ingest_schedule(conflicting, "TEST_SOURCE", available_as_of=KNOWLEDGE_TIME)
    nf_session.commit()  # must not raise

    home_ratings = _ratings_for(nf_session, "HOM")
    assert len(home_ratings) == 1  # persisted evidence not overwritten or duplicated
    assert home_ratings[0].payload_checksum == persisted_checksum
    assert len(normalizer.rating_conflicts) == 1
    assert normalizer.rating_conflicts[0]["reason"] == "conflicts_with_persisted_observation"


def test_newer_available_as_of_appends_new_row(nf_session: Session) -> None:
    games = [_game("dh-1", FIRST_PITCH_1, home_wins=50, home_losses=40, away_wins=45, away_losses=45)]
    CanonicalNormalizer(nf_session).ingest_schedule(games, "TEST_SOURCE", available_as_of=KNOWLEDGE_TIME)
    nf_session.commit()
    later = datetime(2026, 8, 13, 16, 0, tzinfo=UTC)
    newer = [_game("dh-1b", FIRST_PITCH_1, home_wins=51, home_losses=40, away_wins=45, away_losses=45)]
    CanonicalNormalizer(nf_session).ingest_schedule(newer, "TEST_SOURCE", available_as_of=later)
    nf_session.commit()
    # A genuinely newer point-in-time (distinct available_as_of) is a distinct key.
    assert len(_ratings_for(nf_session, "HOM")) == 2

"""A doubleheader must not roll back the finalize transaction.

On 2026-09-04 one schedule response listed the same team twice with different
records. The rating existence check keyed on the payload checksum, the unique
constraint did not, and the second insert collided with the first inside the
batch. finalize-games failed 213 times across ten dates, and TB at TEX -- whose
status update was in the same transaction -- sat LIVE for eleven days with its
position unsettled.
"""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.ingestion.normalizer import CanonicalNormalizer
from app.models import TeamRatingObservation

KNOWN = datetime(2026, 9, 5, 2, 55, 9, 824465, tzinfo=UTC)


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    TeamRatingObservation.__table__.create(engine)
    with Session(engine) as db:
        yield db


def call(normalizer, team, wins, losses, at=KNOWN):
    normalizer._append_record_rating(team, wins=wins, losses=losses, source="MLB_STATS_API",
                                     source_mode="LIVE", available_as_of=at, ingestion_run_id=None)


def rows(session):
    return session.scalars(select(TeamRatingObservation)).all()


def test_two_records_for_one_team_at_one_knowledge_time_do_not_collide(session):
    normalizer = CanonicalNormalizer(session)
    team = uuid4()
    call(normalizer, team, 71, 70)   # game one's listing
    call(normalizer, team, 72, 70)   # game two's listing, same response
    session.flush()                  # the original failure raised here

    stored = rows(session)
    assert len(stored) == 1


def test_the_more_complete_record_wins_whichever_order_it_arrives(session):
    team = uuid4()
    normalizer = CanonicalNormalizer(session)
    call(normalizer, team, 72, 70)
    call(normalizer, team, 71, 70)
    session.flush()
    assert rows(session)[0].source_record_id.endswith(":72-70")


def test_the_later_record_replaces_a_pending_earlier_one(session):
    team = uuid4()
    normalizer = CanonicalNormalizer(session)
    call(normalizer, team, 71, 70)
    call(normalizer, team, 72, 70)
    session.flush()
    stored = rows(session)[0]
    assert stored.source_record_id.endswith(":72-70")
    assert stored.sample_size == 142.0


def test_a_committed_observation_is_never_corrected_in_place(session):
    team = uuid4()
    normalizer = CanonicalNormalizer(session)
    call(normalizer, team, 71, 70)
    session.commit()
    call(normalizer, team, 72, 70)   # new transaction, same key
    session.commit()
    stored = rows(session)
    assert len(stored) == 1
    assert stored[0].source_record_id.endswith(":71-70")


def test_a_different_knowledge_time_is_a_new_observation(session):
    team = uuid4()
    normalizer = CanonicalNormalizer(session)
    call(normalizer, team, 71, 70)
    call(normalizer, team, 72, 70, at=datetime(2026, 9, 5, 3, 25, tzinfo=UTC))
    session.flush()
    assert len(rows(session)) == 2

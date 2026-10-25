"""Prospective RFF Core capture: timing, idempotency and what may never happen.

The capture audit established that a reading cannot be rebuilt after the fact,
so these tests are about the integrity of capturing it while it is true:

  * a reading later than the admissible lag is refused, not adjusted;
  * a missed window leaves a receipt and is never backfilled;
  * an offset that passed before activation produces nothing at all;
  * one (game, offset) can produce at most one accepted row, enforced by the
    database as well as by the service;
  * a capture failure never reaches the host job.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Game, GameStatus, GameTeam, League, Market, MarketOutcome, MarketType, OddsSnapshot,
    RffReadingSnapshot, Season, Sport, Sportsbook, Team,
)
from app.services import rff_capture
from app.services.rff_capture import (
    ACTIVATED_AT_UTC, MAX_CAPTURE_LAG_SECONDS, OFFSETS, PRIMARY_OFFSET, STATUS_ACCEPTED,
    STATUS_MISSED, calculation_identity, capture_due_rff_snapshots, due_targets,
)

#: Comfortably after activation, so the fixture's offsets are admissible.
START = ACTIVATED_AT_UTC + timedelta(days=2)
ACTIVATION = ACTIVATED_AT_UTC


@pytest.fixture
def game(session: Session):
    sport = Sport(code="BASEBALL", name="Baseball")
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, code="MLB", name="MLB")
    session.add(league)
    session.flush()
    season = Season(league_id=league.id, name="2026",
                    starts_at=START - timedelta(days=180), ends_at=START + timedelta(days=60))
    home = Team(league_id=league.id, name="Atlanta Braves", abbreviation="ATL")
    away = Team(league_id=league.id, name="Tampa Bay Rays", abbreviation="TB")
    session.add_all([season, home, away])
    session.flush()
    entry = Game(league_id=league.id, season_id=season.id, starts_at=START,
                 status=GameStatus.SCHEDULED)
    session.add(entry)
    session.flush()
    session.add_all([
        GameTeam(game_id=entry.id, team_id=home.id, is_home=True),
        GameTeam(game_id=entry.id, team_id=away.id, is_home=False),
    ])
    market = Market(game_id=entry.id, market_type=MarketType.MONEYLINE, period="full_game")
    session.add(market)
    session.flush()
    outcomes = [
        MarketOutcome(market_id=market.id, outcome_key="a:home", label="Atlanta Braves",
                      participant_team_id=home.id),
        MarketOutcome(market_id=market.id, outcome_key="b:away", label="Tampa Bay Rays",
                      participant_team_id=away.id),
    ]
    session.add_all(outcomes)
    book = Sportsbook(key="bovada", name="Bovada")
    session.add(book)
    session.flush()
    # Two priced frames before every offset, so the tape has something real.
    for minutes, home_price, away_price in ((600, -120, 100), (400, -135, 115)):
        at = START - timedelta(minutes=minutes)
        for outcome, price in zip(outcomes, (home_price, away_price)):
            session.add(OddsSnapshot(
                market_outcome_id=outcome.id, sportsbook_id=book.id, observed_at=at,
                available_as_of=at, american_odds=price,
                decimal_odds=(1 + (100 / abs(price)) if price < 0 else 1 + price / 100),
                source="test", source_mode="LIVE",
            ))
    session.flush()
    return {"session": session, "game": entry}


def at_offset(label: str, *, lag_seconds: int = 0) -> datetime:
    minutes = dict(OFFSETS)[label]
    return START - timedelta(minutes=minutes) + timedelta(seconds=lag_seconds)


# -- what is due ------------------------------------------------------------
def test_the_four_registered_offsets_are_the_ones_captured():
    assert [label for label, _ in OFFSETS] == ["T-6h", "T-3h", "T-90m", "T-30m"]
    assert PRIMARY_OFFSET == "T-90m"


def test_a_target_is_not_due_before_its_instant(game):
    due = due_targets(game["session"], now=at_offset("T-90m", lag_seconds=-60),
                      activated_at=ACTIVATION)
    assert PRIMARY_OFFSET not in {entry["target_offset"] for entry in due}
    assert {"T-6h", "T-3h"} <= {entry["target_offset"] for entry in due}


def test_offsets_that_passed_before_activation_produce_nothing(game):
    """Not a reading, and not even a receipt: there was no capture system then."""

    activated = START - timedelta(minutes=45)  # after every offset above T-30m
    due = due_targets(game["session"], now=at_offset("T-30m", lag_seconds=10),
                      activated_at=activated)
    assert {entry["target_offset"] for entry in due} == {"T-30m"}

    summary = capture_due_rff_snapshots(
        game["session"], now=at_offset("T-30m", lag_seconds=10), activated_at=activated)
    assert summary["accepted"] == 1 and summary["missed"] == 0
    rows = game["session"].scalars(select(RffReadingSnapshot)).all()
    assert [row.target_offset for row in rows] == ["T-30m"], "no backfill of pre-activation offsets"


# -- fail-closed timing -----------------------------------------------------
def test_a_reading_inside_the_admissible_lag_is_accepted(game):
    summary = capture_due_rff_snapshots(
        game["session"], now=at_offset("T-6h", lag_seconds=MAX_CAPTURE_LAG_SECONDS - 1),
        activated_at=ACTIVATION)
    assert summary["accepted"] == 1 and summary["missed"] == 0
    row = game["session"].scalars(select(RffReadingSnapshot)).one()
    assert row.status == STATUS_ACCEPTED
    assert row.capture_lag_seconds == MAX_CAPTURE_LAG_SECONDS - 1
    assert row.as_of_utc is not None


def test_a_late_reading_is_refused_and_never_calculated(game):
    late = at_offset("T-6h", lag_seconds=MAX_CAPTURE_LAG_SECONDS + 1)
    summary = capture_due_rff_snapshots(game["session"], now=late, activated_at=ACTIVATION)
    assert summary["missed"] >= 1
    row = game["session"].scalars(
        select(RffReadingSnapshot).where(RffReadingSnapshot.target_offset == "T-6h")).one()
    assert row.status == STATUS_MISSED
    assert row.as_of_utc is None
    assert row.evidence_coverage is None and row.known_contribution is None
    assert row.publication_tier is None and row.band_key is None
    assert row.components is None, "a refused window runs no calculation at all"
    assert str(MAX_CAPTURE_LAG_SECONDS) in row.missed_reason


def test_a_missed_window_is_never_backfilled_by_a_later_tick(game):
    late = at_offset("T-6h", lag_seconds=MAX_CAPTURE_LAG_SECONDS + 5)
    capture_due_rff_snapshots(game["session"], now=late, activated_at=ACTIVATION)
    capture_due_rff_snapshots(game["session"], now=late + timedelta(minutes=2),
                              activated_at=ACTIVATION)
    rows = game["session"].scalars(
        select(RffReadingSnapshot).where(RffReadingSnapshot.target_offset == "T-6h")).all()
    assert len(rows) == 1 and rows[0].status == STATUS_MISSED


def test_the_admissible_lag_fits_inside_the_host_job_cadence():
    """The host ticks every two minutes; the limit leaves one tick of slack."""

    assert 120 <= MAX_CAPTURE_LAG_SECONDS <= 300
    assert MAX_CAPTURE_LAG_SECONDS < min(minutes for _, minutes in OFFSETS) * 60


# -- idempotency ------------------------------------------------------------
def test_one_accepted_reading_per_game_and_offset(game):
    first = capture_due_rff_snapshots(game["session"], now=at_offset("T-6h", lag_seconds=5),
                                      activated_at=ACTIVATION)
    second = capture_due_rff_snapshots(game["session"], now=at_offset("T-6h", lag_seconds=25),
                                       activated_at=ACTIVATION)
    assert first["accepted"] == 1
    assert second["accepted"] == 0 and second.get("reason") == "NOT_DUE"
    assert len(game["session"].scalars(select(RffReadingSnapshot)).all()) == 1


def test_the_database_refuses_a_second_row_for_the_same_game_and_offset(game):
    """Service-level idempotency is not enough: the constraint is the guarantee."""

    session = game["session"]
    capture_due_rff_snapshots(session, now=at_offset("T-6h", lag_seconds=5),
                              activated_at=ACTIVATION)
    duplicate = RffReadingSnapshot(
        game_id=game["game"].id, league_code="MLB", target_offset="T-6h",
        target_instant_utc=at_offset("T-6h"), captured_at_utc=at_offset("T-6h", lag_seconds=9),
        capture_lag_seconds=9, status=STATUS_ACCEPTED,
    )
    session.add(duplicate)
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


# -- what a snapshot must carry ---------------------------------------------
def test_an_accepted_snapshot_carries_the_whole_contract(game):
    capture_due_rff_snapshots(game["session"], now=at_offset("T-6h", lag_seconds=7),
                              activated_at=ACTIVATION)
    row = game["session"].scalars(select(RffReadingSnapshot)).one()

    assert row.league_code == "MLB" and row.target_offset == "T-6h"
    assert row.target_instant_utc is not None and row.captured_at_utc is not None
    assert row.capture_lag_seconds == 7

    identity = calculation_identity()
    assert row.calculation_version == identity["calculation_version"]
    assert row.calculation_source_sha256 == identity["calculation_source_sha256"]
    assert row.weights_sha256 == identity["weights_sha256"]
    assert row.policy_sha256 == identity["policy_sha256"]
    assert len(row.calculation_source_sha256) == 64

    assert {entry["key"] for entry in row.components} == set(
        entry["key"] for entry in row.components)
    assert len(row.components) == 5, "all five Core components, available or not"
    for entry in row.components:
        assert {"key", "score", "available", "weight", "unavailable_reason"} <= set(entry)

    assert row.evidence_coverage is not None and row.known_contribution is not None
    assert row.lower_bound is not None and row.upper_bound is not None
    assert row.publication_tier and row.band_key
    assert row.evidence_health and "statement" in row.evidence_health
    assert row.source_hashes["quotes"]["rows"] >= 1
    assert len(row.source_hashes["quotes"]["sha256"]) == 64
    assert "INGESTION time" in row.source_hashes["note"], (
        "the lineup/starter timestamp caveat travels with every snapshot")
    assert "quote_latest_per_book" in row.source_row_identities


def test_evidence_identities_are_bounded_by_the_capture_instant(game):
    """A quote that arrives after capture is not part of that snapshot."""

    session = game["session"]
    outcome = session.scalars(select(MarketOutcome)).first()
    book = session.scalars(select(Sportsbook)).first()
    later = at_offset("T-6h", lag_seconds=600)
    session.add(OddsSnapshot(
        market_outcome_id=outcome.id, sportsbook_id=book.id, observed_at=later,
        available_as_of=later, american_odds=-150, decimal_odds=1.67,
        source="test", source_mode="LIVE",
    ))
    session.flush()

    capture_due_rff_snapshots(session, now=at_offset("T-6h", lag_seconds=5),
                              activated_at=ACTIVATION)
    row = session.scalars(select(RffReadingSnapshot)).one()
    assert row.source_hashes["quotes"]["rows"] == 4, "the later quote is excluded"


# -- the host job is never harmed -------------------------------------------
def test_a_capture_failure_is_swallowed_and_reported(game, monkeypatch):
    def explode(*_args, **_kwargs):
        raise RuntimeError("evidence read blew up")

    monkeypatch.setattr(rff_capture, "_capture_one", explode)
    summary = capture_due_rff_snapshots(game["session"], now=at_offset("T-30m", lag_seconds=5),
                                        activated_at=ACTIVATION)
    assert summary["failed"] >= 1 and summary["accepted"] == 0
    assert game["session"].scalars(select(RffReadingSnapshot)).all() == []


def test_nothing_due_reports_not_due_rather_than_failing(game):
    summary = capture_due_rff_snapshots(
        game["session"], now=START - timedelta(hours=9), activated_at=ACTIVATION)
    assert summary == {"accepted": 0, "missed": 0, "failed": 0, "due": 0,
                       "max_capture_lag_seconds": MAX_CAPTURE_LAG_SECONDS,
                       "reason": "NOT_DUE"}


# -- authority --------------------------------------------------------------
def test_capture_has_no_update_or_delete_path():
    source = (rff_capture.__file__ and open(rff_capture.__file__, encoding="utf-8").read()) or ""
    assert ".delete(" not in source and "session.delete" not in source
    assert "update(" not in source
    assert "session.add(row)" in source, "insert only"

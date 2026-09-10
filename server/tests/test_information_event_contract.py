"""The information-event boundary.

Nothing collects these yet. That is precisely why the contract is tested: the
shape a future collector must satisfy should be pinned before a collector
exists, not negotiated afterwards by whatever the first feed happens to send.

The rules that carry weight are the ones about time. When the world learned and
when we learned are different facts, their order is not optional, and a source
that stamps its own publication time at read time is not publishing a
publication time at all.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.information_events import (
    CLASSES, CONTRACT_VERSION, InformationEventReadService, RELIABILITY_TIERS,
    describe_contract, validate_event,
)
from app.information_events.contract_v1 import InformationEventError
from app.information_events.read_model import CLASS_SOURCE_STATUS
from app.models import (
    AvailabilityEvent, Game, GameStatus, GameTeam, League, Season, Sport, Team,
)

NOW = datetime(2026, 9, 10, 20, 0, tzinfo=UTC)


def payload(**over):
    base = {
        "contract_version": CONTRACT_VERSION,
        "event_id": "evt-1",
        "classification": "LINEUP_ANNOUNCEMENT",
        "sport": "BASEBALL", "league": "MLB",
        "source": "MLB_STATS_API", "reliability_tier": "OFFICIAL",
        "published_at_utc": (NOW - timedelta(minutes=8)).isoformat(),
        "ingested_at_utc": NOW.isoformat(),
        "corroboration": "OFFICIALLY_CONFIRMED",
        "headline": "Lineup posted",
    }
    base.update(over)
    return base


def test_a_well_formed_event_is_accepted_and_keeps_both_clocks():
    event = validate_event(payload())
    assert event.contract_version == CONTRACT_VERSION
    assert event.lead_seconds == 480, "eight minutes between publication and ingestion"


def test_ingestion_before_publication_is_refused():
    """An impossible ordering means a broken clock, and a broken clock is
    exactly what silently destroys every lead-lag conclusion built on it."""

    with pytest.raises(InformationEventError, match="after ingested_at_utc"):
        validate_event(payload(published_at_utc=(NOW + timedelta(seconds=1)).isoformat()))


def test_a_missing_publication_time_is_accepted_and_yields_no_lead():
    event = validate_event(payload(published_at_utc=None))
    assert event.published_at_utc is None
    assert event.lead_seconds is None, "null, never zero"


def test_a_naive_timestamp_is_refused():
    with pytest.raises(InformationEventError, match="must carry a timezone"):
        validate_event(payload(ingested_at_utc="2026-09-10T20:00:00"))


def test_an_unknown_classification_tier_or_state_is_refused():
    with pytest.raises(InformationEventError, match="classification"):
        validate_event(payload(classification="VIBES"))
    with pytest.raises(InformationEventError, match="reliability tier"):
        validate_event(payload(reliability_tier="PROBABLY_FINE"))
    with pytest.raises(InformationEventError, match="corroboration"):
        validate_event(payload(corroboration="SEEMS_RIGHT"))


def test_an_unknown_effect_kind_is_refused_but_a_null_effect_is_not():
    assert validate_event(payload(expected_effect=None)).expected_effect is None
    with pytest.raises(InformationEventError, match="expected effect kind"):
        validate_event(payload(expected_effect={"kind": "VIBE_SHIFT"}))


def test_reliability_tiers_are_ordered_best_first():
    assert RELIABILITY_TIERS[0] == "OFFICIAL"
    assert RELIABILITY_TIERS[-1] == "UNKNOWN"
    contract = describe_contract()
    ranks = [entry["rank"] for entry in contract["reliability_tiers"]]
    assert ranks == sorted(ranks)


def test_the_contract_states_that_nothing_is_collected_yet():
    contract = describe_contract()
    assert "No collector exists" in contract["collection_status"]
    assert set(entry["key"] for entry in contract["classes"]) == set(CLASSES)


@pytest.fixture
def game(session: Session):
    sport = Sport(code="BASEBALL", name="Baseball")
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, code="MLB", name="MLB")
    session.add(league)
    session.flush()
    season = Season(league_id=league.id, name="2026",
                    starts_at=NOW - timedelta(days=180), ends_at=NOW + timedelta(days=60))
    home = Team(league_id=league.id, name="Atlanta Braves", abbreviation="ATL")
    away = Team(league_id=league.id, name="Tampa Bay Rays", abbreviation="TB")
    session.add_all([season, home, away])
    session.flush()
    entry = Game(league_id=league.id, season_id=season.id,
                 starts_at=NOW + timedelta(hours=2), status=GameStatus.SCHEDULED)
    session.add(entry)
    session.flush()
    session.add_all([
        GameTeam(game_id=entry.id, team_id=home.id, is_home=True),
        GameTeam(game_id=entry.id, team_id=away.id, is_home=False),
    ])
    session.flush()
    return {"session": session, "game": entry}


def test_a_class_with_no_collector_says_so_rather_than_looking_quiet(game):
    """An empty list reads as 'nothing happened'. It is not the same thing."""

    read = InformationEventReadService(game["session"]).for_game(game_id=game["game"].id, now=NOW)
    by_key = {entry["key"]: entry for entry in read["classes"]}
    assert by_key["SOCIAL_VOLUME_ANOMALY"]["state"] == "NOT_COLLECTED"
    assert by_key["SOCIAL_VOLUME_ANOMALY"]["collected"] is False
    assert by_key["SOCIAL_VOLUME_ANOMALY"]["requires"]
    assert by_key["REPORTER_UPDATE"]["state"] == "NOT_COLLECTED"
    # A class that IS collected but had no event tonight is a different state.
    assert by_key["LINEUP_ANNOUNCEMENT"]["state"] == "COLLECTED_NONE_TONIGHT"


def test_a_stored_event_is_projected_into_the_contract(game):
    session = game["session"]
    published = NOW - timedelta(minutes=20)
    session.add(AvailabilityEvent(
        schema_version="AISP_AVAILABILITY_EVENT_V1", event_type="LINEUP_CONFIRMED",
        source="MLB_STATS_API", source_event_key="abc", sport="BASEBALL", league="MLB",
        game_id=game["game"].id, published_at=published, received_at=NOW,
        description="Lineup status None -> CONFIRMED",
    ))
    session.flush()

    read = InformationEventReadService(session).for_game(game_id=game["game"].id, now=NOW)
    assert read["event_count"] == 1
    event = read["events"][0]
    assert event["classification"] == "LINEUP_ANNOUNCEMENT"
    assert event["reliability_tier"] == "OFFICIAL"
    assert event["lead_seconds"] == 1200
    # No market at all in this fixture, so both sides are null rather than zero.
    assert event["market_before"] is None and event["market_after"] is None
    assert event["expected_effect"]["magnitude"] is None


def test_a_source_stamped_publication_time_after_ingestion_is_dropped_with_its_reason(game):
    """The real quirk in this data, handled without asserting a zero lead."""

    session = game["session"]
    session.add(AvailabilityEvent(
        schema_version="AISP_AVAILABILITY_EVENT_V1", event_type="LINEUP_CONFIRMED",
        source="MLB_STATS_API", source_event_key="abc", sport="BASEBALL", league="MLB",
        game_id=game["game"].id,
        published_at=NOW + timedelta(milliseconds=800), received_at=NOW,
        description="Lineup status None -> CONFIRMED",
    ))
    session.flush()

    event = InformationEventReadService(session).for_game(
        game_id=game["game"].id, now=NOW)["events"][0]
    assert event["published_at_utc"] is None
    assert event["lead_seconds"] is None
    assert "impossible" in event["evidence"]["clock_note"]


def test_an_unclassified_source_is_unknown_rather_than_assumed(game):
    session = game["session"]
    session.add(AvailabilityEvent(
        schema_version="AISP_AVAILABILITY_EVENT_V1", event_type="LINEUP_CONFIRMED",
        source="SOME_GUY_ON_THE_INTERNET", source_event_key="x", sport="BASEBALL", league="MLB",
        game_id=game["game"].id, published_at=NOW - timedelta(minutes=5), received_at=NOW,
        description="heard it from a friend",
    ))
    session.flush()

    event = InformationEventReadService(session).for_game(
        game_id=game["game"].id, now=NOW)["events"][0]
    assert event["reliability_tier"] == "UNKNOWN"
    assert event["corroboration"] == "SINGLE_SOURCE"


def test_the_read_model_declares_that_events_govern_nothing(game):
    read = InformationEventReadService(game["session"]).for_game(game_id=game["game"].id, now=NOW)
    assert read["authority"]["affects_forecast"] is False
    assert read["authority"]["creates_or_suppresses_position"] is False


def test_every_class_has_a_declared_source_status():
    assert set(CLASS_SOURCE_STATUS) == set(CLASSES)
    for key, status in CLASS_SOURCE_STATUS.items():
        assert status["collected"] or status["requires"], f"{key} must say what it needs"

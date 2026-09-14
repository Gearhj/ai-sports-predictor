"""Recovering governed finals: only what is missing, and never twice.

The dependency this closes is the one that left the first recorded parlay open
with both its games finished: nothing created a governed final for a live game
unless a person typed a command.

The trap it has to avoid is the opposite mistake. Re-ingesting a whole slate is
not idempotent at the record level -- the payload checksum covers the
acquisition time, so a repeat mints a new revision of a result that did not
change. Verified on real data: re-running one slate produced revision 2 for
eleven games whose scores, innings and final_at were byte-identical. So the
tests here are mostly about what is NOT requested.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.services.governed_final_recovery import (
    LOOKBACK,
    SETTLE_DELAY,
    missing_finals,
    recover_missing_finals,
)

NOW = datetime(2026, 9, 14, 18, 0, tzinfo=UTC)


@pytest.fixture
def slate(session: Session):
    """Three completed MLB games and one still in progress."""

    from app.models import (
        ExternalIdMapping, Game, GameTeam, League, Season, Sport, Team, Venue)

    sport = Sport(name="Baseball", code="BASEBALL")
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, name="MLB", code="MLB")
    session.add(league)
    session.flush()
    season = Season(
        league_id=league.id, name="2026",
        starts_at=datetime(2026, 3, 1, tzinfo=UTC),
        ends_at=datetime(2026, 11, 1, tzinfo=UTC))
    venue = Venue(name="Test Park")
    away = Team(league_id=league.id, name="Colorado Rockies", abbreviation="COL")
    home = Team(league_id=league.id, name="Detroit Tigers", abbreviation="DET")
    session.add_all([season, venue, away, home])
    session.flush()

    made = {}
    for source_id, hours_ago, status in (
            ("900001", 6, "FINAL"),
            ("900002", 5, "FINAL"),
            ("900003", 4, "FINAL"),
            ("900004", 1, "LIVE"),
    ):
        game = Game(
            league_id=league.id, season_id=season.id, venue_id=venue.id,
            starts_at=NOW - timedelta(hours=hours_ago), status=status)
        session.add(game)
        session.flush()
        session.add_all([
            GameTeam(game_id=game.id, team_id=home.id, is_home=True),
            GameTeam(game_id=game.id, team_id=away.id, is_home=False),
            ExternalIdMapping(
                entity_type="game", entity_id=game.id,
                source="MLB_STATS_API", external_id=source_id),
        ])
        made[source_id] = game
    session.commit()
    return made


def govern(session: Session, game, *, source_id: str, revision: int = 1):
    """A governed final for one game.

    Written directly rather than through the reconstruction service: that
    service needs a source version, a checkpoint and an ingestion run behind it,
    which is correct for production and is a lot of lineage to rebuild for a
    test about which games get REQUESTED. Only the columns the query reads have
    to be true here.
    """

    import uuid as _uuid

    from sqlalchemy import text

    session.execute(text("""
        INSERT INTO game_final_outcomes (
            id, game_id, season_id, home_team_id, away_team_id, venue_id,
            source_version_id, checkpoint_id, official_date, scheduled_at,
            final_at, completion_time_basis, completion_time_source_field,
            completion_time_method, completion_time_limitation,
            evidence_classification,
            quality_tier, reconstructed_at, home_score, away_score, home_win,
            run_differential, total_runs, outcome_status, revision, official,
            source_record_id, retrieved_at, label_available_at,
            availability_basis, payload_checksum, source_mode,
            temporal_semantics, identity_checksum, created_at
        ) VALUES (
            :id, :game_id, :season_id, :home, :away, :venue,
            :version, :checkpoint, :official_date, :scheduled_at,
            :final_at, 'AUTHORITATIVE_FINAL_AT', 'test:final_at',
            'test fixture', 'test fixture',
            'RECONSTRUCTABLE_POINT_IN_TIME',
            'A', :now, 4, 3, 1, 1, 7, 'FINAL', :revision, 1,
            :source_id, :now, :now, 'ASP_ACQUISITION_TIME', :checksum, 'LIVE',
            'RETROSPECTIVE_RECONSTRUCTION', :identity, :now
        )
    """), {
        # SQLite stores these as CHAR(32); bind the hex form the ORM writes.
        "id": _uuid.uuid4().hex,
        "game_id": game.id.hex if hasattr(game.id, "hex") else game.id,
        "season_id": (game.season_id.hex if hasattr(game.season_id, "hex")
                      else game.season_id),
        "home": _uuid.uuid4().hex, "away": _uuid.uuid4().hex,
        "venue": (game.venue_id.hex if hasattr(game.venue_id, "hex")
                  else game.venue_id),
        "version": _uuid.uuid4().hex,
        "checkpoint": _uuid.uuid4().hex,
        "official_date": (game.starts_at - timedelta(hours=8)).date(),
        "scheduled_at": game.starts_at,
        "final_at": game.starts_at + timedelta(hours=3),
        "now": NOW, "revision": revision, "source_id": source_id,
        "checksum": f"{source_id}{revision}".ljust(64, "0"),
        "identity": f"i{source_id}{revision}".ljust(64, "0"),
    })
    session.commit()


def test_only_completed_games_without_a_governed_final_are_targeted(session, slate):
    govern(session, slate["900001"], source_id="900001")
    targets = {row.source_game_id for row in missing_finals(session, now=NOW)}
    # 900001 is governed, 900004 is still being played.
    assert targets == {"900002", "900003"}


def test_a_game_still_in_progress_is_never_targeted(session, slate):
    # Status, not elapsed time. A game that has run long is not over.
    assert "900004" not in {row.source_game_id for row in missing_finals(session, now=NOW)}


def test_a_game_that_only_just_ended_waits_for_the_settle_delay(session, slate):
    # Half the settle delay after 900003 started: it is too recent to recover,
    # even though the games that started earlier are not.
    just_now = slate["900003"].starts_at + SETTLE_DELAY / 2
    targets = {row.source_game_id for row in missing_finals(session, now=just_now)}
    assert "900003" not in targets


def test_a_game_older_than_the_lookback_is_left_alone(session, slate):
    later = NOW + LOOKBACK + timedelta(days=1)
    assert missing_finals(session, now=later) == []


def test_nothing_is_requested_when_nothing_is_missing(session, slate):
    for source_id in ("900001", "900002", "900003"):
        govern(session, slate[source_id], source_id=source_id)

    def explode():
        pytest.fail("no runner may be built when nothing is missing")

    result = asyncio.run(recover_missing_finals(
        session, object(), now=NOW, runner_factory=explode))
    assert result["records"] == 0
    assert result["requests"] == 0
    assert result["reason"] == "NO_COMPLETED_GAME_IS_MISSING_A_GOVERNED_FINAL"


def test_the_request_plan_covers_only_the_missing_games(session, slate):
    govern(session, slate["900001"], source_id="900001")
    seen = {}

    class Runner:
        async def run(self, options):
            seen["options"] = options
            return {"status": "COMPLETED", "canonical_finals": 2,
                    "accounting": {"attempts": 3}}

    result = asyncio.run(recover_missing_finals(
        session, _Settings(), now=NOW, runner_factory=Runner))

    options = seen["options"]
    # The already-governed game is not in the request at all, so it cannot
    # receive a second revision of a result that did not change.
    assert options.only_source_game_ids == frozenset({"900002", "900003"})
    assert options.expected_final_games == 2
    # One schedule call plus one feed per missing game, exactly.
    assert options.max_requests == 3
    assert result["records"] == 2


def test_each_slate_is_requested_separately(session, slate):
    """A game either side of the slate boundary is two runs, not one wide one."""

    from app.models import ExternalIdMapping, Game

    old = Game(
        league_id=slate["900001"].league_id, season_id=slate["900001"].season_id,
        venue_id=slate["900001"].venue_id,
        starts_at=NOW - timedelta(days=2), status="FINAL")
    session.add(old)
    session.flush()
    session.add(ExternalIdMapping(
        entity_type="game", entity_id=old.id,
        source="MLB_STATS_API", external_id="900009"))
    session.commit()

    runs = []

    class Runner:
        async def run(self, options):
            runs.append(options)
            return {"status": "COMPLETED", "canonical_finals": len(
                options.only_source_game_ids), "accounting": {"attempts": 1}}

    asyncio.run(recover_missing_finals(
        session, _Settings(), now=NOW, runner_factory=Runner))
    assert len(runs) == 2
    assert {options.start_date for options in runs} == {
        options.end_date for options in runs}


def test_the_recorded_owner_approval_is_carried_not_invented(session, slate):
    seen = {}

    class Runner:
        async def run(self, options):
            seen["terms"] = options.terms_status
            return {"status": "COMPLETED", "canonical_finals": 0,
                    "accounting": {"attempts": 1}}

    result = asyncio.run(recover_missing_finals(
        session, _Settings(), now=NOW, runner_factory=Runner))
    assert seen["terms"] == "APPROVED"
    # And the run reports which decision it acted under, rather than leaving a
    # reader to assume one.
    assert result["governance"]["approval"] == "project owner, 2026-08-13"
    assert result["governance"]["population"] == "HISTORICAL_RECONSTRUCTION"


class _Settings:
    raw_data_root = "."

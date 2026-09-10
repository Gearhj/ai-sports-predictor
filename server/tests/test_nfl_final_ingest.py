"""Automatic NFL finals, and the counting rule that makes them safe.

The ingestion half is mostly about refusing to guess: the feed has no status
column, so a game with no score is awaiting a final rather than a 0-0 final,
and a game the feed has moved is left alone rather than rescheduled underneath
a forecast that was frozen against the old kickoff.

The counting half is the part that could quietly ruin the product. One game can
end up with three honest evaluations as its result gains provenance -- operator,
then feed, then provider. Keeping all three is right. COUNTING all three would
report three games after one Wednesday night, assembled entirely out of true
statements. Every test below about ``is_current`` exists to make that
impossible.

No test here reaches the network: the source is driven by a stub transport.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.data_sources.base import SourceError
from app.data_sources.nflverse import (
    GAMES_URL, IGNORED_PRICE_COLUMNS, NFLVERSE, NflverseScheduleSource, TIMESTAMP_URL,
    parse_games_csv,
)
from app.models import (
    Game, GameStateObservation, GameStatus, GameTeam, League, NflFinalObservation,
    ResearchForecastEvaluation, Season, ShadowForecast, ShadowForecastBundle, Sport, Team,
)
from app.services import result_provenance as provenance
from app.services.forecast_evaluation import (
    ForecastEvaluationService, SUPERSEDED_BY_CORRECTION, SUPERSEDED_BY_PROVENANCE,
)
from app.services.forecast_evaluation_read import ForecastEvaluationReadService
from app.services.nfl_final_ingest import (
    AWAITING_FINAL, NOT_IN_FEED, NflFinalIngestService, RESCHEDULED, SETTLE_GRACE_MINUTES,
    WEAKER_SOURCE,
)

EVENT = "2026_01_NE_SEA"
KICKOFF = datetime(2026, 9, 10, 0, 20, tzinfo=UTC)   # 2026-09-09 20:20 Eastern
NOW = KICKOFF + timedelta(hours=6)

HEADER = (
    "game_id,season,game_type,week,gameday,weekday,gametime,away_team,away_score,"
    "home_team,home_score,location,result,total,overtime,old_game_id,gsis,"
    "nfl_detail_id,pfr,pff,espn,ftn,away_moneyline,home_moneyline,spread_line,total_line"
)


def csv_for(*, away_score="10", home_score="13", overtime="0",
            gameday="2026-09-09", gametime="20:20", game_id=EVENT) -> bytes:
    row = (f"{game_id},2026,REG,1,{gameday},Wednesday,{gametime},NE,{away_score},"
           f"SEA,{home_score},Home,3,23,{overtime},2026090900,60178,,202609090sea,,"
           "401872656,,140,-166,3,44.5")
    return f"{HEADER}\n{row}\n".encode()


def stub_source(payload: bytes, *, stamp: str = "2026-09-10 17:07:16 EDT",
                calls: list[str] | None = None) -> NflverseScheduleSource:
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        if str(request.url) == TIMESTAMP_URL:
            return httpx.Response(200, json={"last_updated": stamp})
        if str(request.url) == GAMES_URL:
            return httpx.Response(200, content=payload)
        return httpx.Response(404)

    return NflverseScheduleSource(transport=httpx.MockTransport(handler))


@pytest.fixture
def world(session: Session):
    sport = Sport(code="FOOTBALL", name="Football")
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, code="NFL", name="NFL")
    session.add(league)
    session.flush()
    season = Season(league_id=league.id, name="2026",
                    starts_at=KICKOFF - timedelta(days=30), ends_at=KICKOFF + timedelta(days=150))
    home = Team(league_id=league.id, name="Seattle Seahawks", abbreviation="SEA")
    away = Team(league_id=league.id, name="New England Patriots", abbreviation="NE")
    session.add_all([season, home, away])
    session.flush()
    game = Game(league_id=league.id, season_id=season.id, starts_at=KICKOFF,
                status=GameStatus.SCHEDULED, source_mode="RESEARCH_SHADOW")
    session.add(game)
    session.flush()
    session.add_all([
        GameTeam(game_id=game.id, team_id=home.id, is_home=True),
        GameTeam(game_id=game.id, team_id=away.id, is_home=False),
    ])
    bundle = ShadowForecastBundle(
        bundle_sha256="0" * 64, bundle_id="b1", logical_uri="test://bundle/1",
        source_fixture_id="fixture-1", slate_date=KICKOFF.date(), league="NFL",
        sport="FOOTBALL", status="RESEARCH_SHADOW", generated_at=KICKOFF - timedelta(days=2),
        forecast_count=1, quote_count=0, candidate_count=0,
    )
    session.add(bundle)
    session.flush()
    session.add(ShadowForecast(
        bundle_id=bundle.id, forecast_id="forecast:m1:current:9",
        engine_id="NFL_M1_XGBOOST", engine_version="NFL_M1_2026_RESEARCH_SHADOW_V1",
        model_artifact_sha256="1" * 64, simulator_id="sim", simulator_version="1",
        simulator_seed=731001, event_id=EVENT, source_event_id=f"nflverse:{EVENT}",
        event_date=KICKOFF.date(), season=2026, season_type="REG", week=1,
        canonical_game_id=game.id, home_team_id="SEA", away_team_id="NE",
        snapshot_type="CURRENT", snapshot_version=9, is_ready=True,
        readiness_score=1.0, readiness_threshold=0.5, uncertainty={}, readiness_detail={},
        status="RESEARCH_SHADOW", scheduled_start_utc=KICKOFF,
        available_as_of_utc=KICKOFF - timedelta(hours=25),
        source_cutoff_utc=KICKOFF - timedelta(days=12),
        forecast_cutoff_utc=KICKOFF - timedelta(hours=25),
        full_home_win_probability=0.5736, full_away_win_probability=0.3966,
        full_expected_home_score=22.7482, full_expected_away_score=19.9418,
        distributions={"tie_probability": 0.0298},
    ))
    session.flush()
    return {"session": session, "game": game, "home": home, "away": away}


def operator_final(world, *, home=13, away=10, checksum="operator-1",
                   observed: datetime | None = None) -> None:
    observed = observed or KICKOFF + timedelta(hours=3)
    world["session"].add(GameStateObservation(
        game_id=world["game"].id, home_team_id=world["home"].id, away_team_id=world["away"].id,
        scheduled_at=KICKOFF, status=GameStatus.FINAL.value,
        home_score=home, away_score=away, final_at=observed,
        observed_at=observed, available_as_of=observed,
        source="OPERATOR_ENTERED", source_mode="RESEARCH_SHADOW", payload_checksum=checksum,
    ))
    game = world["game"]
    game.status, game.home_score, game.away_score = GameStatus.FINAL, home, away
    world["session"].flush()


async def run(world, *, payload=None, stamp="2026-09-10 17:07:16 EDT",
              now=NOW, calls=None, dry_run=False):
    source = stub_source(payload if payload is not None else csv_for(), stamp=stamp, calls=calls)
    return await NflFinalIngestService(world["session"], source=source).ingest(
        now=now, dry_run=dry_run)


# -- parsing and provenance -------------------------------------------------

def test_the_feed_parses_into_the_final_we_already_believed():
    games, checksum = parse_games_csv(csv_for())
    game = games[EVENT]
    assert (game.home_team, game.home_score) == ("SEA", 13)
    assert (game.away_team, game.away_score) == ("NE", 10)
    assert game.overtime is False
    assert game.is_final is True
    assert len(checksum) == 64
    # Eastern 20:20 on the 9th is 00:20 UTC on the 10th.
    assert game.scheduled_utc == KICKOFF


def test_a_game_with_no_score_is_not_a_nil_nil_final():
    games, _ = parse_games_csv(csv_for(away_score="", home_score=""))
    game = games[EVENT]
    assert game.is_final is False
    assert game.home_score is None and game.away_score is None


def test_overtime_is_carried_and_absent_overtime_is_not_guessed():
    assert parse_games_csv(csv_for(overtime="1"))[0][EVENT].overtime is True
    assert parse_games_csv(csv_for(overtime=""))[0][EVENT].overtime is None


def test_the_price_columns_are_dropped_rather_than_borrowed():
    """games.csv ships a moneyline. It is not a quote and must never become one."""

    games, _ = parse_games_csv(csv_for())
    game = games[EVENT]
    for column in IGNORED_PRICE_COLUMNS:
        assert not hasattr(game, column)
    assert "moneyline" not in json.dumps(game.result_evidence())


def test_the_result_checksum_covers_the_result_and_nothing_else():
    """Retrieval time must stay out, or every refresh mints a 'correction'."""

    game = parse_games_csv(csv_for())[0][EVENT]
    evidence = game.result_evidence()
    assert set(evidence) == {
        "source", "source_event_id", "season", "game_type", "week",
        "home_team", "away_team", "home_score", "away_score", "overtime", "status",
    }


def test_the_provenance_ranking_puts_the_provider_first_and_operator_last():
    assert provenance.result_tier("THE_ODDS_API") < provenance.result_tier(NFLVERSE)
    assert provenance.result_tier(NFLVERSE) < provenance.result_tier("OPERATOR_ENTERED")
    assert provenance.result_tier("SOMETHING_NOBODY_CLASSIFIED") == provenance.UNCLASSIFIED_TIER
    # The free feed is automatic but is NOT described as provider-verified.
    assert provenance.is_automated(NFLVERSE) is True
    assert provenance.is_provider_verified(NFLVERSE) is False
    assert provenance.is_automated("OPERATOR_ENTERED") is False


def test_a_broken_payload_raises_rather_than_recording_nothing_quietly():
    with pytest.raises(SourceError):
        parse_games_csv(b"not,a,schedule\n1,2,3\n")


# -- ingestion --------------------------------------------------------------


async def test_a_completed_game_enters_aisp_with_full_provenance(world):
    summary = await run(world)
    assert summary["status"] == "OK"
    assert summary["finals_detected"] == 1
    assert summary["observations_written"] == 1

    record = world["session"].scalars(select(NflFinalObservation)).one()
    assert record.source == NFLVERSE
    assert (record.home_score, record.away_score) == (13, 10)
    assert record.overtime is False
    assert record.week == 1 and record.game_type == "REG"
    assert record.source_event_id == EVENT
    assert record.source_last_updated == "2026-09-10 17:07:16 EDT"
    assert record.retrieved_at is not None
    assert record.game_state_observation_id is not None

    # The sport-neutral observation is what carries the result identity.
    state = world["session"].get(GameStateObservation, record.game_state_observation_id)
    assert state.source == NFLVERSE
    assert state.payload_checksum == record.payload_checksum
    # And the projection now points at it.
    assert world["session"].get(Game, world["game"].id).status == GameStatus.FINAL



async def test_running_it_again_writes_nothing(world):
    first = await run(world)
    second = await run(world, stamp="2026-09-10 18:00:00 EDT")
    third = await run(world, stamp="2026-09-10 19:00:00 EDT")
    assert first["observations_written"] == 1
    assert (second["observations_written"], third["observations_written"]) == (0, 0)
    assert (second["observations_reused"], third["observations_reused"]) == (1, 1)
    assert world["session"].scalar(
        select(func.count()).select_from(NflFinalObservation)) == 1
    assert world["session"].scalar(
        select(func.count()).select_from(GameStateObservation)) == 1



async def test_an_unchanged_feed_is_not_downloaded_twice(world):
    calls: list[str] = []
    await run(world, calls=calls)
    downloads = [url for url in calls if url == GAMES_URL]
    assert len(downloads) == 1

    calls.clear()
    summary = await run(world, calls=calls)
    assert summary["status"] == "FEED_UNCHANGED_SINCE_LAST_RUN"
    # Only the 43-byte probe was fetched; the 2 MB schedule was not.
    assert calls == [TIMESTAMP_URL]



async def test_a_game_that_has_not_plausibly_finished_is_not_asked_about(world):
    summary = await run(world, now=KICKOFF + timedelta(minutes=SETTLE_GRACE_MINUTES - 10))
    assert summary["status"] == "NO_GAME_AWAITING_A_FINAL"
    assert summary["games_pending"] == 0
    assert summary["requests"] == 0, "a game still in progress costs not even a probe"



async def test_a_started_game_with_no_score_is_awaiting_a_final(world):
    summary = await run(world, payload=csv_for(away_score="", home_score=""))
    assert summary["finals_detected"] == 0
    assert summary["skipped"][0]["skipped"] == AWAITING_FINAL
    assert world["session"].scalar(
        select(func.count()).select_from(GameStateObservation)) == 0



async def test_a_game_the_feed_has_moved_is_left_alone(world):
    summary = await run(world, payload=csv_for(away_score="", home_score="",
                                               gameday="2026-09-14", gametime="13:00"))
    skip = summary["skipped"][0]
    assert skip["skipped"] == RESCHEDULED
    assert "would re-decide it with hindsight" in skip["detail"]
    # The kickoff AISP holds is untouched, so forecast eligibility cannot move.
    assert world["session"].get(Game, world["game"].id).starts_at.replace(tzinfo=UTC) == KICKOFF



async def test_a_game_missing_from_the_feed_records_nothing(world):
    summary = await run(world, payload=csv_for(game_id="2026_01_XXX_YYY"))
    assert summary["skipped"][0]["skipped"] == NOT_IN_FEED
    assert world["session"].scalar(
        select(func.count()).select_from(GameStateObservation)) == 0



async def test_a_feed_that_disagrees_about_which_side_is_home_is_refused(world):
    """A silent home/away swap would invert every margin we score."""

    payload = csv_for().replace(b",SEA,13,", b",BUF,13,")
    summary = await run(world, payload=payload)
    assert summary["skipped"][0]["skipped"] == "HOME_SIDE_MISMATCH"
    assert world["session"].scalar(
        select(func.count()).select_from(GameStateObservation)) == 0



async def test_a_free_feed_never_replaces_a_governed_provider_result(world):
    world["session"].add(GameStateObservation(
        game_id=world["game"].id, home_team_id=world["home"].id, away_team_id=world["away"].id,
        scheduled_at=KICKOFF, status=GameStatus.FINAL.value, home_score=13, away_score=10,
        final_at=KICKOFF + timedelta(hours=3), observed_at=KICKOFF + timedelta(hours=3),
        available_as_of=KICKOFF + timedelta(hours=3),
        source="THE_ODDS_API", source_mode="RESEARCH_SHADOW", payload_checksum="provider-1",
    ))
    world["session"].flush()
    summary = await run(world)
    assert summary["skipped"][0]["skipped"] == WEAKER_SOURCE
    assert world["session"].scalar(
        select(func.count()).select_from(NflFinalObservation)) == 0



async def test_a_dry_run_writes_nothing(world):
    summary = await run(world, dry_run=True)
    assert summary["finals_detected"] == 1
    assert world["session"].scalar(
        select(func.count()).select_from(GameStateObservation)) == 0



async def test_the_run_reports_zero_spend_of_every_kind(world):
    summary = await run(world)
    assert summary["provider_calls"] == {"THE_ODDS_API": 0, "SPORTSGAMEODDS": 0}
    assert summary["sportsbook_quote_credits_spent"] == 0
    assert summary["models_executed"] == 0
    assert summary["positions_created"] == 0
    assert summary["official_decisions_created"] == 0


# -- the counting rule ------------------------------------------------------


async def test_an_automatic_final_upgrades_an_operator_entry_without_adding_a_game(world):
    """The exact scenario the last report flagged: a stronger result arrives."""

    session = world["session"]
    operator_final(world)
    evaluator = ForecastEvaluationService(session)
    evaluator.evaluate_league(league_code="NFL")
    before = ForecastEvaluationReadService(session).nfl_prospective()
    assert before["total_scored_games"] == 1
    assert before["models"][0]["scoreboard"]["scored_games"] == 1
    assert before["models"][0]["games"][0]["result"]["source"] == "OPERATOR_ENTERED"

    await run(world)
    evaluator.evaluate_league(league_code="NFL")

    rows = list(session.scalars(select(ResearchForecastEvaluation)))
    assert len(rows) == 2, "the operator-scored verdict is kept for audit"
    current = [row for row in rows if row.is_current]
    assert len(current) == 1
    assert current[0].result_source == NFLVERSE
    retired = [row for row in rows if not row.is_current][0]
    assert retired.result_source == "OPERATOR_ENTERED"
    assert retired.supersede_reason == SUPERSEDED_BY_PROVENANCE
    assert retired.superseded_by_id == current[0].id

    after = ForecastEvaluationReadService(session).nfl_prospective()
    assert after["total_scored_games"] == 1, "provenance improved; the game count did not"
    assert after["models"][0]["scoreboard"]["scored_games"] == 1
    assert after["superseded_evaluations"] == 1
    # The metrics themselves are untouched: same score, same forecast.
    assert (after["models"][0]["scoreboard"]["average_log_loss"]
            == before["models"][0]["scoreboard"]["average_log_loss"])
    assert after["models"][0]["games"][0]["result"]["source"] == NFLVERSE
    assert after["models"][0]["games"][0]["result"]["provider_verified"] is False
    assert after["models"][0]["games"][0]["result"]["automated"] is True



async def test_an_operator_entry_arriving_late_cannot_demote_an_automatic_result(world):
    session = world["session"]
    await run(world)
    ForecastEvaluationService(session).evaluate_league(league_code="NFL")

    operator_final(world, checksum="operator-late", observed=KICKOFF + timedelta(days=1))
    ForecastEvaluationService(session).evaluate_league(league_code="NFL")

    current = [row for row in session.scalars(select(ResearchForecastEvaluation))
               if row.is_current]
    assert len(current) == 1
    assert current[0].result_source == NFLVERSE, "a later human entry does not outrank the feed"



async def test_a_corrected_score_supersedes_the_prior_current_evaluation(world):
    session = world["session"]
    await run(world)
    ForecastEvaluationService(session).evaluate_league(league_code="NFL")

    # The feed republishes the game with a corrected score.
    await run(world, payload=csv_for(home_score="14"), stamp="2026-09-11 09:00:00 EDT",
              now=NOW + timedelta(days=1))
    ForecastEvaluationService(session).evaluate_league(league_code="NFL")

    rows = list(session.scalars(select(ResearchForecastEvaluation)))
    assert len(rows) == 2
    current = [row for row in rows if row.is_current]
    assert len(current) == 1
    assert current[0].home_score == 14
    retired = [row for row in rows if not row.is_current][0]
    assert retired.home_score == 13
    assert retired.supersede_reason == SUPERSEDED_BY_CORRECTION

    read = ForecastEvaluationReadService(session).nfl_prospective()
    assert read["total_scored_games"] == 1, "a correction is not a second game"
    assert read["models"][0]["games"][0]["final"]["home"] == 14



async def test_the_database_itself_refuses_two_current_evaluations(world):
    """The counting rule is a constraint, not a convention the code remembers."""

    session = world["session"]
    operator_final(world)
    ForecastEvaluationService(session).evaluate_league(league_code="NFL")
    await run(world)
    ForecastEvaluationService(session).evaluate_league(league_code="NFL")

    retired = [row for row in session.scalars(select(ResearchForecastEvaluation))
               if not row.is_current][0]
    retired.is_current = True
    with pytest.raises(Exception):
        session.flush()
    session.rollback()



async def test_scoring_is_unattended_end_to_end_from_an_unscored_game(world):
    """Detect, persist, freeze, score, report -- with nobody in the loop."""

    session = world["session"]
    assert session.scalar(select(func.count()).select_from(ResearchForecastEvaluation)) == 0

    ingest = await run(world)
    assert ingest["observations_written"] == 1
    scored = ForecastEvaluationService(session).evaluate_league(league_code="NFL")
    assert scored["scored"] == 1

    read = ForecastEvaluationReadService(session).nfl_prospective()
    board = read["models"][0]["scoreboard"]
    assert board["scored_games"] == 1
    assert board["correct_winners"] == 1
    assert read["models"][0]["games"][0]["result"]["source"] == NFLVERSE
    # The forecast that got scored is the one frozen before kickoff.
    assert read["models"][0]["games"][0]["forecast"]["snapshot_version"] == 9
    assert read["models"][0]["games"][0]["forecast"]["hours_before_kickoff"] == 25.0
    # And the market stays honestly unavailable: no quote was ever persisted.
    assert read["models"][0]["games"][0]["market"]["available"] is False



async def test_the_result_still_cannot_influence_which_forecast_is_scored(world):
    """The new source changes where results come from, not how forecasts are picked."""

    session = world["session"]
    session.add(ShadowForecast(
        bundle_id=session.scalars(select(ShadowForecastBundle)).one().id,
        forecast_id="forecast:m1:post:10",
        engine_id="NFL_M1_XGBOOST", engine_version="NFL_M1_2026_RESEARCH_SHADOW_V1",
        model_artifact_sha256="1" * 64, simulator_id="sim", simulator_version="1",
        simulator_seed=1, event_id=EVENT, source_event_id=f"nflverse:{EVENT}",
        event_date=KICKOFF.date(), season=2026, season_type="REG", week=1,
        canonical_game_id=world["game"].id, home_team_id="SEA", away_team_id="NE",
        snapshot_type="CURRENT", snapshot_version=10, is_ready=True,
        readiness_score=1.0, readiness_threshold=0.5, uncertainty={}, readiness_detail={},
        status="RESEARCH_SHADOW", scheduled_start_utc=KICKOFF,
        available_as_of_utc=KICKOFF + timedelta(seconds=1),   # after kickoff
        source_cutoff_utc=KICKOFF, forecast_cutoff_utc=KICKOFF,
        full_home_win_probability=0.99, full_away_win_probability=0.01,
        full_expected_home_score=13.0, full_expected_away_score=10.0,
        distributions={"tie_probability": 0.0},
    ))
    session.flush()

    await run(world)
    ForecastEvaluationService(session).evaluate_league(league_code="NFL")
    current = [row for row in session.scalars(select(ResearchForecastEvaluation))
               if row.is_current][0]
    # v10 would have been perfect. It was published one second after kickoff.
    assert current.snapshot_version == 9
    assert current.probability_of_actual == pytest.approx(0.5736, abs=1e-9)

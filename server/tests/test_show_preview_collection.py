"""The day-before preview collector: complete, and provably dark.

The collector is written out in full so that turning it on is an owner decision
rather than a build. That makes the tests here two jobs rather than one:

1. **It cannot run.** Both switches, an unfunded lane of its own, a daily cap,
   a reserve floor and a kill switch each refuse independently and by name, and
   the deployed configuration fails the first of them.
2. **It is finished.** Given switches, a funded lane and a stubbed response, it
   targets the right games, keeps the required markets, pairs same-book,
   classifies its ledger rows to its own lane, repeats nothing it already holds,
   and survives one sport failing without losing another's objects.

No test here makes a provider request. The one function that would is stubbed
by name in every execution test, and the refusal tests never reach it.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models import (
    Game, GameStatus, GameTeam, League, Market, MarketOutcome, MarketType, OddsSnapshot,
    ProviderRequestLedger, Season, Sport, Sportsbook, Team,
)
from app.services import provider_allocation as allocation
from app.show_preview import collection

SLATE = date(2026, 9, 18)
#: 4:00 PM ET on the day before the slate -- the show clock's collection step.
NOW = datetime(2026, 9, 17, 20, 0, tzinfo=UTC)


def on(settings, **extra):
    return settings.model_copy(update={
        "show_preview_enabled": True,
        "show_preview_provider_collection_enabled": True,
        "sportsgameodds_enabled": True,
        "sportsgameodds_api_key": "test-key",
        "sportsgameodds_base_url": "https://example.invalid/v2",
        **extra,
    })


@pytest.fixture
def slate(session):
    """Two MLB games on the slate, one book, no quotes yet."""

    sport = Sport(code="BASEBALL", name="Baseball")
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, code="MLB", name="MLB")
    session.add(league)
    session.flush()
    season = Season(league_id=league.id, name="2026",
                    starts_at=NOW - timedelta(days=180), ends_at=NOW + timedelta(days=40))
    teams = [Team(league_id=league.id, name=name, abbreviation=abbr) for name, abbr in
             (("New York Yankees", "NYY"), ("Boston Red Sox", "BOS"),
              ("Chicago Cubs", "CHC"), ("St. Louis Cardinals", "STL"))]
    book = Sportsbook(key="sgo:draftkings", name="DraftKings")
    session.add_all([season, book, *teams])
    session.flush()

    games = []
    for index, (home, away) in enumerate(((teams[0], teams[1]), (teams[2], teams[3]))):
        game = Game(league_id=league.id, season_id=season.id, status=GameStatus.SCHEDULED,
                    starts_at=datetime(2026, 9, 18, 23, 5, tzinfo=UTC) + timedelta(hours=index))
        session.add(game)
        session.flush()
        session.add_all([GameTeam(game_id=game.id, team_id=home.id, is_home=True),
                         GameTeam(game_id=game.id, team_id=away.id, is_home=False)])
        games.append(game)
    session.flush()
    return {"session": session, "games": games, "book": book, "league": league, "teams": teams}


def _pair(session, game, book, *, kind=MarketType.MONEYLINE, second_book=None, at=None):
    """A two-sided quote. ``second_book`` puts the away leg on a different book."""

    moment = at or (NOW - timedelta(hours=1))
    market = Market(game_id=game.id, market_type=kind, period="full_game")
    session.add(market)
    session.flush()
    for side, key, price in ((True, "HOME", -120), (False, "AWAY", 110)):
        outcome = MarketOutcome(market_id=market.id, outcome_key=key, label=key.title())
        session.add(outcome)
        session.flush()
        session.add(OddsSnapshot(
            market_outcome_id=outcome.id,
            sportsbook_id=(book if side or second_book is None else second_book).id,
            source="SPORTSGAMEODDS", source_mode="LIVE", observed_at=moment,
            available_as_of=moment, american_odds=price,
            decimal_odds=2.2 if price > 0 else 1.83))
    session.flush()


def _fund_preview_lane(monkeypatch):
    """Fund the preview lane, as the owner would have to before it can run."""

    monkeypatch.setattr(allocation, "LANES", tuple(
        allocation.Lane(key=lane.key, label=lane.label,
                        share=0.2 if lane.key == collection.PREVIEW_LANE else lane.share,
                        rationale=lane.rationale, funded=True)
        for lane in allocation.LANES))
    monkeypatch.setattr(allocation, "LANES_BY_KEY",
                        {lane.key: lane for lane in allocation.LANES})


def _usage(session, *, used: int, at: datetime) -> None:
    tag = f"usage{used}{at.isoformat()}".ljust(64, "0")[:64]
    reservation = ProviderRequestLedger(
        provider="SPORTSGAMEODDS", endpoint_key=allocation.USAGE_ENDPOINT_KEY,
        requested_at=at, status="RESERVED", request_fingerprint=tag, quota_cost=0)
    session.add(reservation)
    session.flush()
    session.add(ProviderRequestLedger(
        provider="SPORTSGAMEODDS", endpoint_key=allocation.USAGE_ENDPOINT_KEY,
        requested_at=at, status="SUCCEEDED", request_fingerprint=tag, completed_at=at,
        reservation_id=reservation.id, records_received=0, quota_cost=0,
        response_metadata={"usage": {"monthly_cap": 2500, "used_this_month": used}}))
    session.flush()


class _Fetch:
    """What a provider response would have produced, without the provider."""

    def __init__(self, objects: int):
        self.requested_at = NOW
        self.objects = objects
        self.pairs = []
        self.parse = None
        self.main = None
        self.events_ingested = objects
        self.quotes_written = objects * 2
        self.market_quotes_written = 0


# -- it cannot run ----------------------------------------------------------

def test_the_deployed_state_refuses_before_it_reads_anything(settings) -> None:
    result = collection.collect_preview_markets(settings)
    assert result["reason"] == collection.DISABLED
    assert result["attempted"] is False
    assert result["objects"] == 0 and result["reservations"] == 0
    assert result["switches"]["collection_permitted"] is False


def test_the_main_switch_alone_is_not_enough(settings) -> None:
    half = settings.model_copy(update={"show_preview_enabled": True})
    assert collection.collect_preview_markets(half)["reason"] == collection.DISABLED


def test_the_preview_lane_is_declared_and_unfunded(session) -> None:
    """Its own lane, with no claim on the daily total."""

    lane = allocation.LANES_BY_KEY[collection.PREVIEW_LANE]
    assert lane.funded is False and lane.share == 0.0
    grant = allocation.request_objects(session, lane=collection.PREVIEW_LANE, wanted=1, now=NOW)
    assert grant.allowed is False
    assert grant.reason == "LANE_NOT_FUNDED"


def test_funding_the_preview_lane_takes_nothing_from_the_decision_lanes() -> None:
    """The zero share is what makes this safe to declare while it is off."""

    split = allocation.apportion(11)
    assert split[collection.PREVIEW_LANE] == 0
    assert split["sgo_mlb_events"] == 7 and split["sgo_nfl_events"] == 1


def test_an_unfunded_lane_stops_the_run_and_says_which_guard_did_it(slate, settings) -> None:
    result = collection.collect_preview_markets(
        on(settings), session=slate["session"], slate_date=SLATE, now=NOW)
    assert result["reason"] == collection.LANE_UNFUNDED
    assert result["objects"] == 0 and result["reservations"] == 0
    assert slate["session"].scalar(select(func.count()).select_from(ProviderRequestLedger)) == 0


def test_no_scheduler_job_is_ever_registered_for_collection(settings) -> None:
    from app.show_preview.jobs import AUTO_FREEZE_JOB_ID, register_show_preview_jobs

    class Recorder:
        def __init__(self):
            self.ids = []

        def add_job(self, *args, **kwargs):
            self.ids.append(kwargs["id"])

    recorder = Recorder()
    register_show_preview_jobs(recorder, on(settings, show_preview_auto_freeze_enabled=True), {})
    # Only the freeze. Collection has no job, on any switch combination.
    assert recorder.ids == [AUTO_FREEZE_JOB_ID]


# -- the league identifiers it refuses to guess ------------------------------

def test_the_two_verified_league_identifiers_come_from_retained_evidence() -> None:
    assert collection.league_identity("MLB") == ("MLB", None)
    assert collection.league_identity("NFL") == ("NFL", None)


def test_nba_and_college_football_are_refused_by_name_not_guessed() -> None:
    """A plausible identifier is an object spent to be told nothing."""

    league, blocker = collection.league_identity("NBA")
    assert league is None and blocker == "SGO_LEAGUE_IDENTITY_UNVERIFIED"
    league, blocker = collection.league_identity("CFB")
    assert league is None
    assert blocker == "SGO_LEAGUE_IDENTITY_UNVERIFIED;NO_CANONICAL_GAME_IDENTITY"


def test_an_unverified_sport_is_named_in_the_plan_even_with_no_games(slate, settings) -> None:
    plan = collection.plan_collection(slate["session"], on(settings), slate_date=SLATE, now=NOW)
    assert set(plan.sports_refused) == {"NBA", "CFB"}


def test_an_unverified_sport_is_skipped_rather_than_requested(slate, settings, monkeypatch) -> None:
    """Even reached with a funded lane, the sport is named and never fetched."""

    _fund_preview_lane(monkeypatch)
    session = slate["session"]
    _usage(session, used=0, at=NOW - timedelta(hours=2))
    plan = collection.plan_collection(session, on(settings), slate_date=SLATE, now=NOW)
    plan.targets.append(collection.PreviewTarget(
        sport="NBA", game_id=None, source_game_ref="nba-1",
        kickoff_utc=(NOW + timedelta(days=1)).isoformat(), away="BOS", home="NYK",
        has_preview_forecast=False, missing_markets=list(collection.REQUIRED_MARKETS)))
    plan.objects_grantable = 3

    monkeypatch.setattr(collection, "_fetch", lambda *a, **k: _Fetch(1))
    monkeypatch.setattr(collection, "_ingest", lambda *a, **k: {"events": 1})
    result = collection._execute(session, on(settings), plan, slate_date=SLATE, now=NOW, job_run_id=None)
    nba = [row for row in result["by_sport"] if row["sport"] == "NBA"]
    assert nba and nba[0]["reason"] == collection.LEAGUE_UNVERIFIED
    assert nba[0]["objects"] == 0


# -- the dry-run planner -----------------------------------------------------

def test_the_planner_writes_nothing_and_requests_nothing(slate, settings) -> None:
    session = slate["session"]
    before = session.scalar(select(func.count()).select_from(ProviderRequestLedger))
    plan = collection.plan_collection(session, on(settings), slate_date=SLATE, now=NOW)
    assert plan.objects_wanted == 2
    assert plan.as_dict()["spends_nothing"] is True
    assert session.scalar(select(func.count()).select_from(ProviderRequestLedger)) == before


def test_the_planner_answers_in_the_deployed_state_without_either_switch(slate, settings) -> None:
    """The answer can be inspected before the switch is thrown."""

    plan = collection.plan_collection(slate["session"], settings, slate_date=SLATE, now=NOW)
    assert plan.permitted is False
    assert plan.refusal == collection.DISABLED
    assert plan.objects_grantable == 0
    assert len(plan.targets) == 2, "it still says what it would target"


def test_a_dry_run_through_the_collector_reserves_nothing(slate, settings, monkeypatch) -> None:
    _fund_preview_lane(monkeypatch)
    session = slate["session"]
    _usage(session, used=0, at=NOW - timedelta(hours=2))
    result = collection.collect_preview_markets(
        on(settings), session=session, slate_date=SLATE, now=NOW, dry_run=True)
    assert result["reason"] == collection.DRY_RUN
    assert result["objects"] == 0 and result["reservations"] == 0
    assert session.scalar(select(func.count()).select_from(ProviderRequestLedger).where(
        ProviderRequestLedger.endpoint_key == collection.PREVIEW_LANE)) == 0


# -- what it already holds ---------------------------------------------------

def test_a_game_with_all_three_markets_is_not_requested_again(slate, settings) -> None:
    session = slate["session"]
    for kind in (MarketType.MONEYLINE, MarketType.SPREAD, MarketType.TOTAL):
        _pair(session, slate["games"][0], slate["book"], kind=kind)
    plan = collection.plan_collection(session, on(settings), slate_date=SLATE, now=NOW)
    wanted = [target for target in plan.targets if target.wanted]
    assert len(wanted) == 1
    assert wanted[0].game_id == str(slate["games"][1].id)


def test_a_half_covered_game_is_still_requested(slate, settings) -> None:
    session = slate["session"]
    _pair(session, slate["games"][0], slate["book"], kind=MarketType.MONEYLINE)
    plan = collection.plan_collection(session, on(settings), slate_date=SLATE, now=NOW)
    target = next(t for t in plan.targets if t.game_id == str(slate["games"][0].id))
    assert target.complete_markets == ["MONEYLINE"]
    assert target.missing_markets == ["SPREAD", "TOTAL"]
    assert target.wanted is True


def test_two_legs_from_two_books_are_not_a_pair(slate, settings) -> None:
    """The governed pairing rule, enforced where the spend is decided.

    A home leg at one book and an away leg at another is not a market anyone
    could take, so it must not count as held and suppress a request.
    """

    session = slate["session"]
    other = Sportsbook(key="sgo:fanduel", name="FanDuel")
    session.add(other)
    session.flush()
    _pair(session, slate["games"][0], slate["book"], second_book=other)
    plan = collection.plan_collection(session, on(settings), slate_date=SLATE, now=NOW)
    target = next(t for t in plan.targets if t.game_id == str(slate["games"][0].id))
    assert target.complete_markets == []
    assert "MONEYLINE" in target.missing_markets


def test_nothing_missing_means_nothing_requested(slate, settings, monkeypatch) -> None:
    _fund_preview_lane(monkeypatch)
    session = slate["session"]
    _usage(session, used=0, at=NOW - timedelta(hours=2))
    for game in slate["games"]:
        for kind in (MarketType.MONEYLINE, MarketType.SPREAD, MarketType.TOTAL):
            _pair(session, game, slate["book"], kind=kind)
    result = collection.collect_preview_markets(
        on(settings), session=session, slate_date=SLATE, now=NOW)
    assert result["reason"] == collection.ALREADY_HELD
    assert result["objects"] == 0


def test_a_slate_with_no_games_says_so(session, settings, monkeypatch) -> None:
    _fund_preview_lane(monkeypatch)
    result = collection.collect_preview_markets(
        on(settings), session=session, slate_date=SLATE, now=NOW)
    assert result["reason"] == collection.NO_GAMES


# -- the caps and the floor --------------------------------------------------

def test_the_daily_cap_is_enforced_independently_of_the_lane(slate, settings, monkeypatch) -> None:
    """A second ceiling that still holds when the one above it is wrong."""

    _fund_preview_lane(monkeypatch)
    monkeypatch.setattr(collection, "DAILY_OBJECT_CAP", 0)
    session = slate["session"]
    _usage(session, used=0, at=NOW - timedelta(hours=2))
    result = collection.collect_preview_markets(
        on(settings), session=session, slate_date=SLATE, now=NOW)
    assert result["reason"] == collection.DAILY_CAP_REACHED
    assert result["objects"] == 0


def test_objects_already_spent_today_count_against_the_cap(slate, settings, monkeypatch) -> None:
    _fund_preview_lane(monkeypatch)
    monkeypatch.setattr(collection, "DAILY_OBJECT_CAP", 2)
    session = slate["session"]
    _usage(session, used=0, at=NOW - timedelta(hours=2))
    reservation = ProviderRequestLedger(
        provider="SPORTSGAMEODDS", endpoint_key=collection.PREVIEW_LANE,
        requested_at=NOW - timedelta(hours=1), status="RESERVED",
        request_fingerprint="a".ljust(64, "0"), quota_cost=2)
    session.add(reservation)
    session.flush()
    session.add(ProviderRequestLedger(
        provider="SPORTSGAMEODDS", endpoint_key=collection.PREVIEW_LANE,
        requested_at=NOW - timedelta(hours=1), status="SUCCEEDED",
        request_fingerprint=reservation.request_fingerprint, completed_at=NOW,
        reservation_id=reservation.id, records_received=2))
    session.flush()

    plan = collection.plan_collection(session, on(settings), slate_date=SLATE, now=NOW)
    assert plan.objects_spent_today == 2
    assert plan.refusal == collection.DAILY_CAP_REACHED


def test_an_in_flight_reservation_counts_against_the_cap(slate, settings, monkeypatch) -> None:
    """Two runs must not both claim the last object."""

    _fund_preview_lane(monkeypatch)
    session = slate["session"]
    _usage(session, used=0, at=NOW - timedelta(hours=2))
    session.add(ProviderRequestLedger(
        provider="SPORTSGAMEODDS", endpoint_key=collection.PREVIEW_LANE,
        requested_at=NOW - timedelta(minutes=5), status="RESERVED",
        request_fingerprint="b".ljust(64, "0"), quota_cost=4))
    session.flush()
    plan = collection.plan_collection(session, on(settings), slate_date=SLATE, now=NOW)
    assert plan.objects_spent_today == 4


def test_the_preview_floor_holds_back_four_hundred_objects(slate, settings, monkeypatch) -> None:
    """Show research is the least urgent call on the account."""

    _fund_preview_lane(monkeypatch)
    session = slate["session"]
    _usage(session, used=0, at=NOW - timedelta(hours=2))
    # Everything above the floor is gone.
    reservation = ProviderRequestLedger(
        provider="SPORTSGAMEODDS", endpoint_key="sgo_mlb_events",
        requested_at=NOW - timedelta(days=1), status="RESERVED",
        request_fingerprint="c".ljust(64, "0"), quota_cost=0)
    session.add(reservation)
    session.flush()
    session.add(ProviderRequestLedger(
        provider="SPORTSGAMEODDS", endpoint_key="sgo_mlb_events",
        requested_at=NOW - timedelta(days=1), status="SUCCEEDED",
        request_fingerprint=reservation.request_fingerprint, completed_at=NOW,
        reservation_id=reservation.id,
        records_received=2500 - collection.PREVIEW_RESERVE_FLOOR))
    session.flush()

    plan = collection.plan_collection(session, on(settings), slate_date=SLATE, now=NOW)
    assert plan.refusal in (collection.RESERVE_GUARD, collection.BUDGET_GUARD,
                            f"ALLOCATION_{plan.allocation['reason']}")
    assert plan.objects_grantable == 0


# -- the kill switch ---------------------------------------------------------

def test_a_halted_lane_stops_the_run_before_the_planner(slate, settings, monkeypatch) -> None:
    _fund_preview_lane(monkeypatch)
    session = slate["session"]
    _usage(session, used=0, at=NOW - timedelta(hours=2))
    collection.stop_preview_collection(
        session, reason=collection.KILL_SWITCH, detail="Owner stopped preview collection.", now=NOW)
    session.flush()

    result = collection.collect_preview_markets(
        on(settings), session=session, slate_date=SLATE, now=NOW)
    assert result["reason"] == collection.LANE_HALTED
    assert result["objects"] == 0 and result["reservations"] == 0
    assert result["detail"] == "Owner stopped preview collection."


def test_the_kill_switch_touches_no_other_lane(slate, settings) -> None:
    from app.services.collection_halt import active_halt

    session = slate["session"]
    collection.stop_preview_collection(session, reason=collection.KILL_SWITCH, now=NOW)
    session.flush()
    assert active_halt(session, lane=collection.PREVIEW_LANE) is not None
    assert active_halt(session, lane="sgo_mlb_events") is None
    assert active_halt(session, lane="sgo_nfl_events") is None


# -- what it does when it is allowed to run ----------------------------------

def test_a_permitted_run_reserves_fetches_ingests_and_reconciles(slate, settings, monkeypatch) -> None:
    _fund_preview_lane(monkeypatch)
    session = slate["session"]
    _usage(session, used=0, at=NOW - timedelta(hours=2))
    seen: list[dict] = []

    def fake_fetch(_settings, *, league, limit, now, anchor):
        seen.append({"league": league, "limit": limit, "anchor": anchor})
        return _Fetch(2)

    monkeypatch.setattr(collection, "_fetch", fake_fetch)
    monkeypatch.setattr(collection, "_ingest", lambda *a, **k: {"events": 2, "quotes": 4})

    result = collection.collect_preview_markets(
        on(settings), session=session, slate_date=SLATE, now=NOW)
    assert result["attempted"] is True and result["reason"] == collection.COLLECTED
    assert result["objects"] == 2 and result["reservations"] == 1
    assert result["horizon"] == "SHOW_PREVIEW"
    assert result["grants_wagering_authority"] is False
    assert seen == [{"league": "MLB", "limit": 2, "anchor": seen[0]["anchor"]}]
    # The anchor sits one second below the earliest game it selected, so a
    # lower-ranked game cannot be substituted by the provider's own ordering.
    assert seen[0]["anchor"] == datetime(2026, 9, 18, 23, 5, tzinfo=UTC) - timedelta(seconds=1)


def test_the_ledger_rows_are_classified_to_the_preview_lane(slate, settings, monkeypatch) -> None:
    """Both halves of the pair, not just the reservation.

    A terminal row filed under another lane's key would charge the show's
    objects to a decision lane's daily allowance in the accounting, which is the
    one thing a separate lane exists to prevent.
    """

    _fund_preview_lane(monkeypatch)
    session = slate["session"]
    _usage(session, used=0, at=NOW - timedelta(hours=2))
    monkeypatch.setattr(collection, "_fetch", lambda *a, **k: _Fetch(2))
    monkeypatch.setattr(collection, "_ingest", lambda *a, **k: {"events": 2})

    collection.collect_preview_markets(on(settings), session=session, slate_date=SLATE, now=NOW)

    rows = session.scalars(select(ProviderRequestLedger).where(
        ProviderRequestLedger.endpoint_key == collection.PREVIEW_LANE)).all()
    assert {row.status for row in rows} == {"RESERVED", "SUCCEEDED"}
    terminal = next(row for row in rows if row.status == "SUCCEEDED")
    assert terminal.records_received == 2
    assert terminal.response_metadata["horizon"] == "SHOW_PREVIEW"
    assert terminal.response_metadata["lane"] == collection.PREVIEW_LANE
    # And nothing landed on a decision lane.
    assert session.scalar(select(func.count()).select_from(ProviderRequestLedger).where(
        ProviderRequestLedger.endpoint_key.in_(("sgo_mlb_events", "sgo_nfl_events")))) == 0


def test_a_second_run_over_the_same_slate_collects_nothing_new(slate, settings, monkeypatch) -> None:
    """Idempotent on what it holds, not on how many times it has been called."""

    _fund_preview_lane(monkeypatch)
    session = slate["session"]
    _usage(session, used=0, at=NOW - timedelta(hours=2))

    def ingest_for_real(_session, _settings, _sport, _fetch):
        for game in slate["games"]:
            for kind in (MarketType.MONEYLINE, MarketType.SPREAD, MarketType.TOTAL):
                _pair(session, game, slate["book"], kind=kind)
        return {"events": 2}

    monkeypatch.setattr(collection, "_fetch", lambda *a, **k: _Fetch(2))
    monkeypatch.setattr(collection, "_ingest", ingest_for_real)

    first = collection.collect_preview_markets(on(settings), session=session, slate_date=SLATE, now=NOW)
    assert first["objects"] == 2

    monkeypatch.setattr(collection, "_fetch", _never_called)
    second = collection.collect_preview_markets(on(settings), session=session, slate_date=SLATE, now=NOW)
    assert second["reason"] == collection.ALREADY_HELD
    assert second["objects"] == 0 and second["reservations"] == 0


def _never_called(*args, **kwargs):
    raise AssertionError("a second run must not reach the provider")


def test_one_sport_failing_does_not_lose_another_sports_objects(slate, settings, monkeypatch) -> None:
    """Partial failure, tested where it matters: the objects already bought."""

    _fund_preview_lane(monkeypatch)
    session = slate["session"]
    _usage(session, used=0, at=NOW - timedelta(hours=2))

    # An NFL game on the same slate, so the run covers two sports.
    football = Sport(code="FOOTBALL", name="Football")
    session.add(football)
    session.flush()
    nfl = League(sport_id=football.id, code="NFL", name="NFL")
    session.add(nfl)
    session.flush()
    season = Season(league_id=nfl.id, name="2026",
                    starts_at=NOW - timedelta(days=30), ends_at=NOW + timedelta(days=150))
    home = Team(league_id=nfl.id, name="Buffalo Bills", abbreviation="BUF")
    away = Team(league_id=nfl.id, name="Miami Dolphins", abbreviation="MIA")
    session.add_all([season, home, away])
    session.flush()
    game = Game(league_id=nfl.id, season_id=season.id, status=GameStatus.SCHEDULED,
                starts_at=datetime(2026, 9, 19, 0, 15, tzinfo=UTC))
    session.add(game)
    session.flush()
    session.add_all([GameTeam(game_id=game.id, team_id=home.id, is_home=True),
                     GameTeam(game_id=game.id, team_id=away.id, is_home=False)])
    session.flush()

    calls: list[str] = []

    def flaky(_settings, *, league, limit, now, anchor):
        calls.append(league)
        if league == "NFL":
            raise TimeoutError("connection reset")
        return _Fetch(2)

    monkeypatch.setattr(collection, "_fetch", flaky)
    monkeypatch.setattr(collection, "_ingest", lambda *a, **k: {"events": 2})

    result = collection.collect_preview_markets(
        on(settings), session=session, slate_date=SLATE, now=NOW)

    by_sport = {row["sport"]: row for row in result["by_sport"]}
    assert by_sport["MLB"]["reason"] == collection.COLLECTED
    assert by_sport["MLB"]["objects"] == 2
    assert by_sport["NFL"]["reason"] == collection.REQUEST_FAILED
    assert by_sport["NFL"]["error_type"] == "TimeoutError"
    assert result["objects"] == 2, "the MLB objects are not lost by the NFL failure"
    # One attempt per sport per run. A retry spends a second object to re-ask a
    # question the provider has just failed to answer.
    assert calls.count("NFL") == 1

    # The failed request is still reconciled: a charged-but-unrecorded object is
    # how an allowance is overrun.
    failed = session.scalars(select(ProviderRequestLedger).where(
        ProviderRequestLedger.status == "FAILED",
        ProviderRequestLedger.endpoint_key == collection.PREVIEW_LANE)).all()
    assert len(failed) == 1
    assert failed[0].records_received == 0
    assert failed[0].error_type == "TimeoutError"


def test_only_the_required_markets_are_claimed(slate, settings, monkeypatch) -> None:
    _fund_preview_lane(monkeypatch)
    session = slate["session"]
    _usage(session, used=0, at=NOW - timedelta(hours=2))
    monkeypatch.setattr(collection, "_fetch", lambda *a, **k: _Fetch(2))
    monkeypatch.setattr(collection, "_ingest", lambda *a, **k: {"events": 2})
    result = collection.collect_preview_markets(
        on(settings), session=session, slate_date=SLATE, now=NOW)
    mlb = next(row for row in result["by_sport"] if row["sport"] == "MLB")
    assert mlb["markets_kept"] == ["MONEYLINE", "SPREAD", "TOTAL"]
    # One object carries all three; the request does not ask three times.
    assert mlb["objects"] == 2


def test_the_receipt_never_claims_wagering_authority(slate, settings, monkeypatch) -> None:
    _fund_preview_lane(monkeypatch)
    session = slate["session"]
    _usage(session, used=0, at=NOW - timedelta(hours=2))
    monkeypatch.setattr(collection, "_fetch", lambda *a, **k: _Fetch(2))
    monkeypatch.setattr(collection, "_ingest", lambda *a, **k: {"events": 2})
    result = collection.collect_preview_markets(
        on(settings), session=session, slate_date=SLATE, now=NOW)
    assert result["grants_wagering_authority"] is False
    assert "never final-pregame evidence" in result["note"]

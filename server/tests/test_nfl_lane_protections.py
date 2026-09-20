"""The NFL pregame lane on a tied-kickoff Sunday: bought once, never substituted.

WHAT WENT WRONG
---------------
Week 3's Sunday is fourteen NFL games, eight of them kicking off together at
13:00 ET, and the lane holds fifteen objects for the day.

1. The "already priced" test guarded the PREVIEW pass only, so a checkpoint game
   was re-offered on every fifteen-minute tick inside its own sixty-minute
   window. The first tick bought all eight 13:00 games; the second tick spent
   the seven objects left in the day re-buying seven of them. By about 12:02 ET
   the lane was empty and the 16:05, 16:25 and 20:20 games -- and the next day's
   Show Prep preview -- had nothing to spend.
2. `startsAfter` was anchored at `now`, not at the earliest SELECTED kickoff, so
   the provider was free to answer with an earlier game the rule had not chosen.
3. Nothing checked that what came back was what was asked for, and nothing
   stopped the lane when it was not -- the protection MLB has had since
   2026-09-14.
4. `games_not_funded` was a positional slice of the due list. With eight tied
   kickoffs the provider's own ordering decides which of the funded games come
   back, so the slice named the wrong games and reported re-bought prices as new
   coverage.

Every fixture here is synthetic. The lane funds one object per game and there is
no second one, so none of this is discovered against a real response.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models import (
    CollectionLaneHalt, Game, GameStatus, GameTeam, League, Market, MarketOutcome,
    MarketType, OddsSnapshot, Season, Sport, Sportsbook, Team,
)
from app.services import sgo_nfl_pregame as lane

#: Week 3 Sunday. 13:00 ET is 17:00 UTC.
SUNDAY_1300_ET = datetime(2026, 9, 20, 17, 0, tzinfo=UTC)
SUNDAY_1605_ET = datetime(2026, 9, 20, 20, 5, tzinfo=UTC)
SUNDAY_1625_ET = datetime(2026, 9, 20, 20, 25, tzinfo=UTC)
SUNDAY_2020_ET = datetime(2026, 9, 21, 0, 20, tzinfo=UTC)

#: The first scheduler tick inside the 13:00 cluster's window (11:45-12:45 ET).
FIRST_TICK = datetime(2026, 9, 20, 15, 47, tzinfo=UTC)
#: The next one, fifteen minutes later -- the tick that used to re-buy.
SECOND_TICK = FIRST_TICK + timedelta(minutes=15)


# --------------------------------------------------------------------------
# Fixtures: a canonical NFL slate, built by hand.
# --------------------------------------------------------------------------

@pytest.fixture
def slate(session: Session):
    sport = Sport(code="FOOTBALL", name="Football")
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, code="NFL", name="NFL")
    session.add(league)
    session.flush()
    season = Season(league_id=league.id, name="2026",
                    starts_at=SUNDAY_1300_ET - timedelta(days=30),
                    ends_at=SUNDAY_1300_ET + timedelta(days=120))
    session.add(season)
    session.flush()
    return {"session": session, "league": league, "season": season}


def add_game(ctx, *, kickoff: datetime, away: str, home: str) -> Game:
    session = ctx["session"]
    teams = []
    for abbreviation in (home, away):
        team = Team(league_id=ctx["league"].id, name=f"{abbreviation} team",
                    abbreviation=abbreviation)
        session.add(team)
        teams.append(team)
    session.flush()
    game = Game(league_id=ctx["league"].id, season_id=ctx["season"].id,
                starts_at=kickoff, status=GameStatus.SCHEDULED)
    session.add(game)
    session.flush()
    session.add_all([
        GameTeam(game_id=game.id, team_id=teams[0].id, is_home=True),
        GameTeam(game_id=game.id, team_id=teams[1].id, is_home=False),
    ])
    session.flush()
    return game


def price(ctx, game: Game, *, observed_at: datetime, source: str = "SPORTSGAMEODDS",
          source_event_id: str | None = None) -> None:
    """One persisted moneyline for this game, observed at a stated moment."""

    session = ctx["session"]
    book = session.query(Sportsbook).filter(Sportsbook.key == "sgo:book").one_or_none()
    if book is None:
        book = Sportsbook(key="sgo:book", name="Book (research)", region="us", active=True)
        session.add(book)
        session.flush()
    market = Market(game_id=game.id, market_type=MarketType.MONEYLINE, period="full_game")
    session.add(market)
    session.flush()
    outcome = MarketOutcome(market_id=market.id, outcome_key="home:None", label="home")
    session.add(outcome)
    session.flush()
    session.add(OddsSnapshot(
        market_outcome_id=outcome.id, sportsbook_id=book.id,
        observed_at=observed_at, available_as_of=observed_at,
        american_odds=-110, decimal_odds=1.909, implied_probability=0.524,
        source=source, source_event_id=source_event_id or f"evt-{uuid.uuid4().hex[:8]}",
        source_mode="LIVE"))
    session.flush()


def eight_tied_kickoffs(ctx) -> list[Game]:
    return [add_game(ctx, kickoff=SUNDAY_1300_ET, away=f"A{i:02d}", home=f"H{i:02d}")
            for i in range(8)]


# --------------------------------------------------------------------------
# R1: one buy per window, on the checkpoint pass too.
# --------------------------------------------------------------------------

def test_a_checkpoint_game_priced_inside_its_window_is_not_offered_again(slate):
    """The re-buy. This is the whole Sunday, in one game."""

    game = add_game(slate, kickoff=SUNDAY_1300_ET, away="NO", home="BAL")

    # First tick inside the window: nothing has priced it, so it is due.
    assert [entry["game_id"] for entry in
            lane.due_games(slate["session"], now=FIRST_TICK)] == [str(game.id)]

    # The object is spent and a moneyline lands.
    price(slate, game, observed_at=FIRST_TICK)

    # Second tick, same window: the price it would buy is the price it holds.
    assert lane.due_games(slate["session"], now=SECOND_TICK) == []


def test_a_stale_price_from_before_the_window_still_buys_a_checkpoint(slate):
    """One buy per window is not one buy ever: yesterday's quote is not fresh."""

    game = add_game(slate, kickoff=SUNDAY_1300_ET, away="NO", home="BAL")
    opens, closes = lane.checkpoint_window(SUNDAY_1300_ET)
    assert closes - opens == timedelta(minutes=60)
    # The Show Prep preview bought this game the day before. That quote is
    # 22 hours old at the checkpoint -- far outside the 240-minute rule.
    price(slate, game, observed_at=opens - timedelta(hours=22))

    assert [entry["game_id"] for entry in
            lane.due_games(slate["session"], now=FIRST_TICK)] == [str(game.id)]


def test_sundays_eight_tied_kickoffs_are_bought_once_not_once_per_tick(slate):
    """The 15 objects used to be gone by 12:02 ET on these eight games alone."""

    games = eight_tied_kickoffs(slate)
    assert len(lane.due_games(slate["session"], now=FIRST_TICK)) == 8

    for game in games:  # the first tick's purchase lands
        price(slate, game, observed_at=FIRST_TICK)

    # Every later tick inside the window: nothing due, nothing spent. The
    # remaining seven objects stay in the lane for the afternoon and night games.
    for minutes in (15, 30, 45):
        assert lane.due_games(
            slate["session"], now=FIRST_TICK + timedelta(minutes=minutes)) == []


def test_the_preview_pass_is_unchanged(slate, settings):
    """The rule that already existed must behave exactly as it did."""

    # A Monday-night game. Its Show Prep slot is the Sunday before, 15:00-16:30 ET.
    game = add_game(slate, kickoff=datetime(2026, 9, 22, 0, 15, tzinfo=UTC),
                    away="NYG", home="LAR")
    opens, closes = lane.preview_slot(settings, datetime(2026, 9, 22, 0, 15, tzinfo=UTC))
    assert closes - opens == lane.PREVIEW_LEAD

    inside = opens + timedelta(minutes=2)
    session = slate["session"]
    # Inside the slot and unpriced: due, as a PREVIEW.
    due = lane.due_games(session, now=inside, settings=settings)
    assert [(entry["game_id"], entry["pass"]) for entry in due] == [(str(game.id), "PREVIEW")]

    # Outside the slot: not due.
    assert lane.due_games(session, now=opens - timedelta(minutes=5), settings=settings) == []

    # Priced inside the slot: not due again. One buy is the budget.
    price(slate, game, observed_at=inside)
    assert lane.due_games(session, now=inside + timedelta(minutes=15),
                          settings=settings) == []


# --------------------------------------------------------------------------
# The collector: a stubbed provider, so nothing is ever requested.
# --------------------------------------------------------------------------

class _Grant:
    def __init__(self, granted: int) -> None:
        self.allowed = True
        self.granted = granted
        self.reason = "GRANTED"

    def as_dict(self) -> dict:
        return {"granted": self.granted}


class _Budget:
    def may_spend(self, objects):
        return True, None

    def as_dict(self):
        return {}


class _Pair:
    def __init__(self, event_id: str) -> None:
        self.event_id = event_id
        self.bookmaker_key = "sgo:book"


class _Fetch:
    """Only the fields the collector reads."""

    def __init__(self, *, requested_at: datetime, event_ids: list[str]) -> None:
        self.requested_at = requested_at
        self.objects = len(event_ids)
        self.pairs = [_Pair(event_id) for event_id in event_ids]
        self.main = None
        self.raw_archive = {"responseBodySha256": "0" * 64}


@pytest.fixture
def collector(monkeypatch, slate):
    """Wire the collector to a stub provider and a stated grant.

    Nothing here reaches the network, the allocation tables or the object
    ledger: this exercises the SELECTION, the halt and the receipt.
    """

    calls: dict = {"fetch": [], "ingested": [], "completed": []}

    def _grant_of(size: int):
        monkeypatch.setattr("app.services.provider_allocation.request_objects",
                            lambda session, *, lane, wanted, now=None: _Grant(min(size, wanted)))

    monkeypatch.setattr("app.services.sgo_collection.object_budget",
                        lambda session, now=None: _Budget())
    monkeypatch.setattr(lane, "eligible", lambda settings, now=None: (True, "OK"))
    monkeypatch.setattr(lane, "record_request",
                        lambda session, **kwargs: type("R", (), {"id": uuid.uuid4(),
                                                                 "request_fingerprint": "fp"})())
    monkeypatch.setattr(lane, "complete_request",
                        lambda session, reservation, **kwargs: calls["completed"].append(kwargs))

    class _Service:
        def __init__(self, session, *, source=None) -> None: ...

        def ingest(self, events, *, available_as_of):
            return {"events": len(events), "mapped_events": len(events), "quotes": 0,
                    "duplicates": 0, "rejected_events": 0}

    monkeypatch.setattr("app.services.nfl_shadow_odds_service.NFLShadowOddsService", _Service)
    monkeypatch.setattr(lane, "to_event_dtos", lambda pairs: list(pairs))

    def _returns(event_ids: list[str], game_ids: list[str]):
        def _fetch(settings, **kwargs):
            calls["fetch"].append(kwargs)
            return _Fetch(requested_at=kwargs["now"], event_ids=event_ids)

        monkeypatch.setattr(lane, "fetch_nfl_pregame", _fetch)
        monkeypatch.setattr(lane, "ingested_game_ids",
                            lambda session, fetch: sorted(game_ids))

    return {"calls": calls, "grant_of": _grant_of, "returns": _returns,
            "session": slate["session"], "slate": slate}


# --------------------------------------------------------------------------
# R2: a request the provider could answer with a different game is deferred.
# --------------------------------------------------------------------------

def test_a_grant_smaller_than_a_tied_group_defers_instead_of_gambling(collector, settings):
    """Seven objects, eight games at one kickoff: the provider picks, not us."""

    eight_tied_kickoffs(collector["slate"])
    collector["grant_of"](7)
    collector["returns"]([], [])

    result = lane.collect_nfl_pregame(collector["session"], settings, now=FIRST_TICK)

    assert result["attempted"] is False
    assert result["objects"] == 0
    assert result["reason"] == "DEFERRED_AMBIGUOUS_SELECTION"
    assert len(result["games_deferred"]) == 7
    assert {entry["reason"] for entry in result["games_deferred"]} == {
        lane.DEFERRED_TIED_START}
    # Nothing bound, so every due game is still named as unpriced.
    assert len(result["games_not_funded"]) == 8
    assert len(result["games_beyond_grant"]) == 1
    # Not one request was sent, so not one object was spent.
    assert collector["calls"]["fetch"] == []


def test_a_grant_that_covers_the_whole_tied_group_buys_all_of_it(collector, settings):
    """Eight objects for eight tied games is unambiguous: the request goes."""

    games = eight_tied_kickoffs(collector["slate"])
    ids = [str(game.id) for game in games]
    collector["grant_of"](8)
    collector["returns"]([f"evt-{i}" for i in range(8)], ids)

    result = lane.collect_nfl_pregame(collector["session"], settings, now=FIRST_TICK)

    assert result["reason"] == "COLLECTED"
    assert result["objects"] == 8
    assert sorted(result["games_selected"]) == sorted(ids)
    assert result["games_not_funded"] == []
    assert "games_deferred" not in result


# --------------------------------------------------------------------------
# R3: the request is anchored to the earliest SELECTED kickoff.
# --------------------------------------------------------------------------

def test_starts_after_is_the_earliest_selected_kickoff_not_now(collector, settings):
    """Otherwise the object lands on an earlier game the rule did not choose."""

    ctx = collector["slate"]
    early = [add_game(ctx, kickoff=SUNDAY_1605_ET, away=f"E{i}", home=f"F{i}")
             for i in range(2)]
    for i in range(3):  # a later cluster that must not be able to answer
        add_game(ctx, kickoff=SUNDAY_1625_ET, away=f"L{i}", home=f"M{i}")

    now = SUNDAY_1605_ET - timedelta(minutes=70)  # inside the 16:05 window only
    collector["grant_of"](2)
    collector["returns"](["evt-a", "evt-b"], [str(g.id) for g in early])

    result = lane.collect_nfl_pregame(collector["session"], settings, now=now)

    assert result["reason"] == "COLLECTED"
    sent = collector["calls"]["fetch"][0]
    assert sent["starts_after"] == SUNDAY_1605_ET - timedelta(seconds=1)
    assert sent["starts_after"] > now, "the anchor carries the selection, not the clock"
    assert sent["limit"] == 2


def test_the_anchor_is_never_moved_back_past_the_retrieval_moment(settings, monkeypatch):
    """A game already under way is not pregame, whatever the selection says."""

    captured: dict = {}

    class _Response:
        status_code = 200
        content = b'{"data": []}'

        def json(self):
            return {"data": []}

    class _Client:
        def __init__(self, **kwargs) -> None: ...
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, params=None, headers=None):
            captured["params"] = params
            return _Response()

    monkeypatch.setattr(lane, "eligible", lambda settings, now=None: (True, "OK"))
    monkeypatch.setattr(lane.httpx, "Client", _Client)
    monkeypatch.setattr("app.services.sgo_raw_archive.archive_response",
                        lambda *a, **k: None)

    now = datetime(2026, 9, 20, 17, 30, tzinfo=UTC)
    lane.fetch_nfl_pregame(settings, limit=1, now=now,
                           starts_after=now - timedelta(hours=3))

    assert captured["params"]["startsAfter"] == "2026-09-20T17:30:00Z"


# --------------------------------------------------------------------------
# R5: a provider answer naming an unselected game halts the lane.
# --------------------------------------------------------------------------

def test_an_unselected_game_in_the_answer_halts_the_nfl_lane(collector, settings):
    ctx = collector["slate"]
    chosen = [add_game(ctx, kickoff=SUNDAY_1605_ET, away=f"E{i}", home=f"F{i}")
              for i in range(2)]
    intruder = add_game(ctx, kickoff=SUNDAY_1625_ET, away="X", home="Y")

    now = SUNDAY_1605_ET - timedelta(minutes=70)
    collector["grant_of"](2)
    # The provider answers with one selected game and one nobody asked for.
    collector["returns"](["evt-a", "evt-z"], [str(chosen[0].id), str(intruder.id)])

    result = lane.collect_nfl_pregame(collector["session"], settings, now=now)

    assert result["reason"] == "COLLECTED", "the rows written are kept, not rolled back"
    assert result["lane_halted"] is True
    assert result["selection_reached_provider"] is False
    assert result["selection_mismatch"]["unselected_games_returned"] == [str(intruder.id)]

    halt = collector["session"].query(CollectionLaneHalt).filter(
        CollectionLaneHalt.lane == lane.LANE, CollectionLaneHalt.active.is_(True)).one()
    assert halt.reason == "PROVIDER_RETURNED_UNSELECTED_GAME"


def test_a_latched_nfl_lane_spends_nothing_and_is_never_lifted_here(collector, settings):
    ctx = collector["slate"]
    add_game(ctx, kickoff=SUNDAY_1300_ET, away="NO", home="BAL")
    collector["session"].add(CollectionLaneHalt(
        lane=lane.LANE, reason="PROVIDER_RETURNED_UNSELECTED_GAME", active=True,
        halted_at=FIRST_TICK - timedelta(hours=1), evidence={}, detail="stopped"))
    collector["session"].flush()
    collector["grant_of"](8)
    collector["returns"](["evt-a"], [])

    result = lane.collect_nfl_pregame(collector["session"], settings, now=FIRST_TICK)

    assert result["attempted"] is False
    assert result["reason"] == "LANE_HALTED"
    assert result["objects"] == 0
    assert collector["calls"]["fetch"] == []
    # Still latched afterwards. Nothing in this lane lifts a halt.
    from app.services.collection_halt import active_halt

    assert active_halt(collector["session"], lane=lane.LANE) is not None


def test_a_halt_on_one_lane_does_not_stop_the_other(collector, settings):
    """An MLB halt must leave the NFL reservation exactly where it was."""

    ctx = collector["slate"]
    games = eight_tied_kickoffs(ctx)
    collector["session"].add(CollectionLaneHalt(
        lane="sgo_mlb_events", reason="PROVIDER_RETURNED_UNSELECTED_GAME", active=True,
        halted_at=FIRST_TICK - timedelta(hours=1), evidence={}, detail="stopped"))
    collector["session"].flush()
    collector["grant_of"](8)
    collector["returns"]([f"evt-{i}" for i in range(8)], [str(g.id) for g in games])

    result = lane.collect_nfl_pregame(collector["session"], settings, now=FIRST_TICK)

    assert result["reason"] == "COLLECTED"
    assert result["objects"] == 8


# --------------------------------------------------------------------------
# R7: games_not_funded is measured from what bound.
# --------------------------------------------------------------------------

def test_games_not_funded_names_the_games_that_really_have_no_price(collector, settings):
    """A positional slice named the wrong games when kickoffs are tied."""

    ctx = collector["slate"]
    # Eight tied at 16:05 ET and one at 16:25: at 15:15 ET both windows are open,
    # so the tied group is fully funded and unambiguous while the ninth game is
    # due and beyond the grant.
    games = [add_game(ctx, kickoff=SUNDAY_1605_ET, away=f"A{i:02d}", home=f"H{i:02d}")
             for i in range(8)]
    late = add_game(ctx, kickoff=SUNDAY_1625_ET, away="J", home="K")
    collector["grant_of"](8)

    # The provider answers for six of the eight it was asked for -- a short
    # answer is allowed and costs nothing that was not authorised.
    bound = [str(game.id) for game in games[:6]]
    collector["returns"]([f"evt-{i}" for i in range(6)], bound)

    now = SUNDAY_1605_ET - timedelta(minutes=50)  # both clusters' windows open
    result = lane.collect_nfl_pregame(collector["session"], settings, now=now)

    assert result["reason"] == "COLLECTED"
    assert set(result["games_ingested"]) == set(bound)
    # Two funded-but-unanswered games AND the one beyond the grant.
    assert set(result["games_not_funded"]) == {
        str(games[6].id), str(games[7].id), str(late.id)}
    # The two reasons stay apart.
    assert result["games_beyond_grant"] == [str(late.id)]
    assert result["selection_mismatch"] is None, "a short answer is not a mismatch"
    assert result["lane_halted"] is False


# --------------------------------------------------------------------------
# R4: the lane defers; it does not name provider event IDs.
# --------------------------------------------------------------------------

def test_the_nfl_lane_never_names_event_ids_and_never_spends_the_owners_one_test(
        collector, settings, monkeypatch):
    """AISP holds a verified SportsGameOdds id for every one of these games.

    Using it is governed by `sgo_exact_event_selection_mode`, whose documented
    scope is the MLB fallback and whose VALIDATE state carries a SINGLE
    owner-authorised discriminating test, recorded in and read from the MLB
    lane's own ledger. A second lane reading that switch could spend that one
    test on an NFL Sunday. So this lane defers by name instead, and the mode is
    not consulted here at all.
    """

    eight_tied_kickoffs(collector["slate"])
    monkeypatch.setattr(settings, "sgo_exact_event_selection_mode", "ON", raising=False)
    collector["grant_of"](7)
    collector["returns"]([], [])

    result = lane.collect_nfl_pregame(collector["session"], settings, now=FIRST_TICK)

    assert result["reason"] == "DEFERRED_AMBIGUOUS_SELECTION"
    assert "exact_event_ids" not in result
    assert "exact_event_selection" not in result
    assert "exact_event_selection_mode" not in result
    assert collector["calls"]["fetch"] == []


# --------------------------------------------------------------------------
# The whole of Sunday, walked tick by tick.
# --------------------------------------------------------------------------

def test_the_week_three_sunday_prices_every_game_inside_the_same_fifteen_objects(
        collector, settings):
    """Before this branch: 8 of 14 games, the lane empty by about 12:02 ET."""

    ctx = collector["slate"]
    session = ctx["session"]
    sunday = (
        [add_game(ctx, kickoff=SUNDAY_1300_ET, away=f"A{i:02d}", home=f"H{i:02d}")
         for i in range(8)]
        + [add_game(ctx, kickoff=SUNDAY_1605_ET, away=f"B{i}", home=f"J{i}")
           for i in range(2)]
        + [add_game(ctx, kickoff=SUNDAY_1625_ET, away=f"C{i}", home=f"K{i}")
           for i in range(3)]
        + [add_game(ctx, kickoff=SUNDAY_2020_ET, away="IND", home="KC")])
    # The Monday nighter, whose Show Prep preview slot falls on the Sunday.
    monday = add_game(ctx, kickoff=datetime(2026, 9, 22, 0, 15, tzinfo=UTC),
                      away="NYG", home="LAR")

    lane_objects = {"left": 15}  # Sunday's projected sgo_nfl_events allocation

    class _Walk:
        def __init__(self, granted: int) -> None:
            self.allowed = granted > 0
            self.granted = granted
            self.reason = "GRANTED" if granted else "LANE_DAILY_ALLOWANCE_SPENT"

        def as_dict(self):
            return {"granted": self.granted}

    def _request_objects(session, *, lane, wanted, now=None):
        return _Walk(min(wanted, lane_objects["left"]))

    def _fetch(settings_, **kwargs):
        collector["calls"]["fetch"].append(kwargs)
        return _Fetch(requested_at=kwargs["now"],
                      event_ids=[f"evt-{kwargs['now'].isoformat()}-{i}"
                                 for i in range(kwargs["limit"])])

    import pytest as _pytest

    monkey = _pytest.MonkeyPatch()
    monkey.setattr("app.services.provider_allocation.request_objects", _request_objects)
    monkey.setattr(lane, "fetch_nfl_pregame", _fetch)

    # The request is anchored to the selection, so the provider can only answer
    # with what was selected. Watch the cut the collector actually made and
    # answer with exactly that.
    real_prefix = lane.unambiguous_prefix
    last_cut: dict = {"ids": []}

    def _watch(selected, events, **kwargs):
        kept, deferred = real_prefix(selected, events, **kwargs)
        last_cut["ids"] = [str(entry["game_id"]) for entry in kept]
        return kept, deferred

    monkey.setattr(lane, "unambiguous_prefix", _watch)
    monkey.setattr(lane, "ingested_game_ids",
                   lambda session_, fetch: list(last_cut["ids"]))

    spend: dict[str, int] = {}
    by_id = {str(game.id): game for game in sunday + [monday]}
    moment = FIRST_TICK
    try:
        while moment < datetime(2026, 9, 21, 10, 0, tzinfo=UTC):
            result = lane.collect_nfl_pregame(session, settings, now=moment)
            if result.get("reason") == "COLLECTED":
                for game_id in result["games_ingested"]:
                    price(ctx, by_id[game_id], observed_at=moment)
                    spend[game_id] = spend.get(game_id, 0) + 1
                lane_objects["left"] -= result["objects"]
            moment += timedelta(minutes=15)
    finally:
        monkey.undo()

    assert sum(spend.values()) == 15, "the same fifteen objects, none added"
    assert lane_objects["left"] == 0
    # Every Sunday game priced exactly once...
    assert {str(game.id) for game in sunday} <= set(spend)
    assert max(spend.values()) == 1, "no game was ever bought twice"
    # ...and the Monday preview still got its object.
    assert spend.get(str(monday.id)) == 1
    assert len(spend) == 15

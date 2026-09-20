"""The owner's single-day MLB exception: four objects, four named games, one date.

WHAT WAS AUTHORISED
-------------------
Owner, 2026-09-20: MLB's shaped daily allowance rises from 17 to 21 objects for
the 2026-09-20 Eastern service day only. The four extra objects may be spent
only on San Francisco at the Dodgers, Miami at San Diego, the Yankees at
Arizona and Milwaukee at Baltimore -- at most one object each, no
substitutions, not for refreshes, not for retries charged as another object,
and not for any other game, sport or date. Today's remaining NFL objects stay
untouchable and the monthly reserve does not move.

WHAT THESE TESTS PIN
--------------------
That it is an exception and not a policy change: it is inert on 09-19 and
09-21, it cannot be drawn by a selection carrying any other game, a named game
that already holds a purchased quote cannot draw it a second time, and no other
lane's allowance moves by a single object because of it.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy.orm import Session

from app.models import (
    Market, MarketOutcome, MarketType, OddsSnapshot, ProviderAllocationSnapshot,
    ProviderRequestLedger, Sportsbook,
)
from app.services.provider_allocation import (
    OWNER_DAY_EXCEPTIONS, PROVIDER, build_plan, owner_exception_for,
    owner_exception_state, owner_exception_units, request_objects, reserve_at,
)

LANE = "sgo_mlb_events"
NFL_LANE = "sgo_nfl_events"

#: The authorisation, as recorded. Read from the constant rather than retyped,
#: so a test cannot quietly disagree with the code about which games it covers.
EXCEPTION = OWNER_DAY_EXCEPTIONS[0]
NAMED = [game.game_id for game in EXCEPTION.games]

THE_DAY = datetime(2026, 9, 20, 16, 30, tzinfo=UTC)      # 12:30 ET, 2026-09-20
THE_DAY_BEFORE = datetime(2026, 9, 19, 16, 30, tzinfo=UTC)
THE_DAY_AFTER = datetime(2026, 9, 21, 16, 30, tzinfo=UTC)

#: The shape of the days either side of the exception on the live account: MLB
#: seventeen, NFL fifteen, nothing else funded.
ISSUED = {"sgo_mlb_events": 17, "sgo_nfl_events": 15, "sgo_nfl_live": 0,
          "sgo_nfl_periods": 0, "sgo_show_preview_events": 0}


@pytest.fixture
def day(session: Session):
    """Three service days issued exactly as production issued 2026-09-20."""

    for service_date in (date(2026, 9, 19), date(2026, 9, 20), date(2026, 9, 21)):
        session.add(ProviderAllocationSnapshot(
            provider=PROVIDER, service_date=service_date,
            semantics="EASTERN_SERVICE_DAY", monthly_cap=2500,
            used_this_month=2000, remaining=500,
            balance_source="PROVIDER_ACCOUNT_USAGE",
            balance_read_at=datetime(2026, 9, 20, 4, tzinfo=UTC),
            reserve=300, days_remaining=11, daily_total=sum(ISSUED.values()),
            lanes=dict(ISSUED), demand={},
            taken_at=datetime(2026, 9, 20, 4, tzinfo=UTC)))
    session.commit()
    return session


def spend(session: Session, *, lane: str, objects: int, at: datetime,
          settled: bool = True, metadata: dict | None = None):
    """One lane purchase, written the way the collector writes it."""

    tag = uuid.uuid4().hex.ljust(64, "0")[:64]
    reservation = ProviderRequestLedger(
        provider=PROVIDER, endpoint_key=lane, requested_at=at, status="RESERVED",
        request_fingerprint=tag, quota_cost=objects,
        response_metadata=metadata)
    session.add(reservation)
    session.flush()
    if settled:
        session.add(ProviderRequestLedger(
            provider=PROVIDER, endpoint_key=lane, requested_at=at,
            status="SUCCEEDED", request_fingerprint=tag, completed_at=at,
            reservation_id=reservation.id, records_received=objects))
    session.commit()
    return reservation


def price(session: Session, game_id: str, *, at: datetime = THE_DAY):
    """A purchased SportsGameOdds quote bound to one game -- the object landed."""

    book = Sportsbook(key=f"b{uuid.uuid4().hex[:8]}", name="Book")
    session.add(book)
    market = Market(game_id=uuid.UUID(game_id), market_type=MarketType.MONEYLINE,
                    period="full_game")
    session.add_all([book, market])
    session.flush()
    outcome = MarketOutcome(market_id=market.id, outcome_key="home", label="Home")
    session.add(outcome)
    session.flush()
    session.add(OddsSnapshot(
        market_outcome_id=outcome.id, sportsbook_id=book.id, observed_at=at,
        available_as_of=at, decimal_odds=1.9, source="SPORTSGAMEODDS",
        source_event_id=f"evt-{game_id[:8]}"))
    session.commit()


def exhaust_mlb(session: Session, at: datetime = THE_DAY):
    """The lane's own seventeen objects, spent. This is the live position."""

    spend(session, lane=LANE, objects=17, at=at)


# -- the authorisation itself ---------------------------------------------

def test_the_exception_names_one_day_one_lane_four_games_and_two_caps():
    assert EXCEPTION.service_date == "2026-09-20"
    assert EXCEPTION.lane == LANE
    assert EXCEPTION.per_game_objects == 1
    assert EXCEPTION.total_objects == 4
    assert len(EXCEPTION.games) == 4
    assert len(EXCEPTION.game_ids) == 4, "four distinct games, no duplicates"
    # It cites the authorisation rather than describing a policy.
    assert "project owner" in EXCEPTION.authority
    assert "2026-09-20" in EXCEPTION.citation
    # Exactly one exception exists at all, and it is this one.
    assert len(OWNER_DAY_EXCEPTIONS) == 1


def test_it_is_matched_by_date_and_lane_and_by_nothing_else():
    assert owner_exception_for(date(2026, 9, 20), LANE) is EXCEPTION
    assert owner_exception_for(date(2026, 9, 19), LANE) is None
    assert owner_exception_for(date(2026, 9, 21), LANE) is None
    # Another lane on the authorised day gets nothing.
    assert owner_exception_for(date(2026, 9, 20), NFL_LANE) is None
    assert owner_exception_for(date(2026, 9, 20), "sgo_nfl_live") is None


# -- the allowance, by date -----------------------------------------------

def test_the_mlb_allowance_is_twenty_one_on_the_authorised_day(day):
    plan = build_plan(day, now=THE_DAY)
    mlb = plan.allocations[LANE]
    assert mlb.daily_allowance == 17, "the day's ISSUED allowance does not move"
    assert mlb.owner_exception_units == 4
    assert mlb.authorised_allowance == 21


@pytest.mark.parametrize("moment", [THE_DAY_BEFORE, THE_DAY_AFTER])
def test_the_mlb_allowance_is_seventeen_on_every_other_day(day, moment):
    plan = build_plan(day, now=moment)
    mlb = plan.allocations[LANE]
    assert mlb.daily_allowance == 17
    assert mlb.owner_exception_units == 0
    assert mlb.authorised_allowance == 17, "the code path is inert by date"
    assert mlb.owner_exception is None
    assert "owner_exception" not in mlb.as_dict()


# -- drawing the units ----------------------------------------------------

def test_each_named_game_draws_exactly_one_unit_and_a_fifth_finds_nothing(day):
    exhaust_mlb(day)
    assert request_objects(day, lane=LANE, wanted=1, now=THE_DAY).granted == 0, (
        "the lane's own allowance is spent")

    for game_id in NAMED:
        grant = request_objects(day, lane=LANE, wanted=1, now=THE_DAY,
                                selection=[game_id])
        assert grant.granted == 1, f"{game_id} has its one object"
        assert grant.reason == "GRANTED_OWNER_EXCEPTION"
        # The object lands: the quote is what spends the unit.
        price(day, game_id)
        spend(day, lane=LANE, objects=1, at=THE_DAY)

    # All four units are now spent, so a fifth request -- for a named game or
    # any other -- finds nothing left.
    assert owner_exception_state(day, lane=LANE, now=THE_DAY)["units_available"] == 0
    fifth = request_objects(day, lane=LANE, wanted=1, now=THE_DAY,
                            selection=[NAMED[0]])
    assert fifth.granted == 0
    assert fifth.reason == "LANE_DAILY_ALLOWANCE_SPENT"
    assert build_plan(day, now=THE_DAY).allocations[LANE].authorised_allowance == 17


def test_one_named_game_cannot_draw_two_units(day):
    exhaust_mlb(day)
    # The per-game cap holds even when the whole total is still unspent.
    grant = request_objects(day, lane=LANE, wanted=4, now=THE_DAY,
                            selection=[NAMED[0]])
    assert grant.granted == 1
    assert grant.reason == "GRANTED_OWNER_EXCEPTION_PARTIAL"


def test_the_total_cap_holds_however_the_games_are_combined(day):
    exhaust_mlb(day)
    grant = request_objects(day, lane=LANE, wanted=9, now=THE_DAY, selection=NAMED)
    assert grant.granted == 4, "four games, one object each, four in total"


# -- what cannot draw on it -----------------------------------------------

def test_an_unnamed_game_cannot_draw_on_the_exception(day):
    exhaust_mlb(day)
    stranger = str(uuid.uuid4())
    assert request_objects(day, lane=LANE, wanted=1, now=THE_DAY,
                           selection=[stranger]).granted == 0


def test_a_selection_carrying_any_other_game_draws_nothing_at_all(day):
    exhaust_mlb(day)
    stranger = str(uuid.uuid4())
    # Not "the named ones are funded and the stranger is not" -- the whole
    # selection is refused, because it is not the request the owner authorised.
    grant = request_objects(day, lane=LANE, wanted=4, now=THE_DAY,
                            selection=[*NAMED[:3], stranger])
    assert grant.granted == 0
    assert grant.reason == "LANE_DAILY_ALLOWANCE_SPENT"
    assert owner_exception_units(day, lane=LANE, now=THE_DAY,
                                 selection=[*NAMED, stranger]) == 0


def test_a_request_naming_no_games_cannot_draw_on_it(day):
    exhaust_mlb(day)
    # The ordinary collector path passes no selection at all.
    assert request_objects(day, lane=LANE, wanted=4, now=THE_DAY).granted == 0
    assert request_objects(day, lane=LANE, wanted=4, now=THE_DAY,
                           selection=[]).granted == 0


def test_a_refresh_of_an_already_priced_named_game_cannot_draw_on_it(day):
    exhaust_mlb(day)
    price(day, NAMED[0])
    # Its unit is spent by the quote it already holds, whether or not this
    # process is the one that bought it.
    assert request_objects(day, lane=LANE, wanted=1, now=THE_DAY,
                           selection=[NAMED[0]]).granted == 0
    state = owner_exception_state(day, lane=LANE, now=THE_DAY)
    assert state["already_priced"] == [NAMED[0]]
    assert state["units_available"] == 3
    assert build_plan(day, now=THE_DAY).allocations[LANE].authorised_allowance == 20
    # The other three are untouched.
    assert request_objects(day, lane=LANE, wanted=3, now=THE_DAY,
                           selection=NAMED[1:]).granted == 3


def test_an_open_reservation_holds_its_named_games_against_a_concurrent_run(day):
    exhaust_mlb(day)
    spend(day, lane=LANE, objects=2, at=THE_DAY, settled=False,
          metadata={"owner_exception": {"game_ids": NAMED[:2]}})
    state = owner_exception_state(day, lane=LANE, now=THE_DAY)
    assert state["in_flight"] == sorted(NAMED[:2])
    assert state["units_available"] == 2
    assert request_objects(day, lane=LANE, wanted=1, now=THE_DAY,
                           selection=[NAMED[0]]).granted == 0


@pytest.mark.parametrize("moment", [THE_DAY_BEFORE, THE_DAY_AFTER])
def test_a_named_game_cannot_draw_on_it_on_any_other_day(day, moment):
    exhaust_mlb(day, at=moment)
    assert request_objects(day, lane=LANE, wanted=1, now=moment,
                           selection=[NAMED[0]]).granted == 0
    assert owner_exception_units(day, lane=LANE, now=moment,
                                 selection=NAMED) == 0


def test_the_lane_uses_its_own_allowance_before_the_exception(day):
    # Nothing spent yet: a named game is funded by the ordinary seventeen, and
    # the exception is left whole.
    grant = request_objects(day, lane=LANE, wanted=1, now=THE_DAY,
                            selection=[NAMED[0]])
    assert grant.reason == "GRANTED"
    assert owner_exception_state(day, lane=LANE, now=THE_DAY)["units_available"] == 4


# -- what does not move ---------------------------------------------------

def test_no_other_lane_gains_or_loses_an_object(day):
    exhaust_mlb(day)
    plan = build_plan(day, now=THE_DAY)
    for key in ("sgo_nfl_events", "sgo_nfl_live", "sgo_nfl_periods",
                "sgo_show_preview_events"):
        allocation = plan.allocations[key]
        assert allocation.daily_allowance == ISSUED[key]
        assert allocation.authorised_allowance == ISSUED[key]
        assert allocation.owner_exception_units == 0
    assert plan.daily_total == sum(ISSUED.values()), "the day's total is unchanged"


def test_nfl_keeps_its_fifteen_and_its_remaining_seven(day):
    # Eight of NFL's fifteen spent, as on the live account this afternoon.
    spend(day, lane=NFL_LANE, objects=8, at=THE_DAY)
    exhaust_mlb(day)
    plan = build_plan(day, now=THE_DAY)
    nfl = plan.allocations[NFL_LANE]
    assert nfl.daily_allowance == 15
    assert nfl.spent_today == 8
    assert nfl.room == 7, "the remaining seven stay available"
    # And they stay available after MLB has drawn every one of its four.
    for game_id in NAMED:
        assert request_objects(day, lane=LANE, wanted=1, now=THE_DAY,
                               selection=[game_id]).granted == 1
        price(day, game_id)
        spend(day, lane=LANE, objects=1, at=THE_DAY)
    assert request_objects(day, lane=NFL_LANE, wanted=7, now=THE_DAY).granted == 7


def test_an_nfl_request_cannot_draw_on_the_mlb_exception(day):
    spend(day, lane=NFL_LANE, objects=15, at=THE_DAY)
    assert request_objects(day, lane=NFL_LANE, wanted=1, now=THE_DAY,
                           selection=[NAMED[0]]).granted == 0
    assert owner_exception_units(day, lane=NFL_LANE, now=THE_DAY,
                                 selection=[NAMED[0]]) == 0


def test_the_monthly_reserve_is_untouched(day):
    before = reserve_at(THE_DAY)
    exhaust_mlb(day)
    for game_id in NAMED:
        request_objects(day, lane=LANE, wanted=1, now=THE_DAY, selection=[game_id])
    plan = build_plan(day, now=THE_DAY)
    assert reserve_at(THE_DAY) == before == 300
    assert plan.reserve == 300


# -- the reservation boundary itself --------------------------------------
#
# The allowance arithmetic above is only half of it. These drive
# `collect_for_open_cutoffs` with the real allocation, the real ledger, the
# real cooldown and the real deferral rule, and replace only the provider.

#: 12:40 ET on the authorised day: the three 16:10 ET games are inside their
#: freshness windows, the 19:20 ET game is not yet.
TICK = datetime(2026, 9, 20, 16, 40, tzinfo=UTC)
AT_1610 = datetime(2026, 9, 20, 20, 10, tzinfo=UTC)
AT_1605 = datetime(2026, 9, 20, 20, 5, tzinfo=UTC)      # an UNNAMED game, ranked first
AT_1920 = datetime(2026, 9, 20, 23, 20, tzinfo=UTC)
UNNAMED = str(uuid.uuid4())


def slate_for(offset_days: int = 0) -> dict[str, datetime]:
    """The same five-game shape on any day, so the date is the only variable."""

    from datetime import timedelta

    shift = timedelta(days=offset_days)
    return {NAMED[0]: AT_1610 + shift, NAMED[1]: AT_1610 + shift,
            NAMED[2]: AT_1610 + shift, NAMED[3]: AT_1920 + shift,
            UNNAMED: AT_1605 + shift}


def coverage_events(slate: dict[str, datetime]):
    """Every slate game uncovered, with its cutoff at first pitch."""

    return [{"game_id": gid, "starts_at_utc": start.isoformat(),
             "cutoff_utc": start.isoformat(), "covered": False, "needs_refresh": True}
            for gid, start in slate.items()]


def install_provider(monkeypatch, sent: list, slate: dict[str, datetime] | None = None):
    from types import SimpleNamespace

    from app.data_sources.sgo_parser import ParseResult
    from app.services import sgo_collection

    games = slate if slate is not None else slate_for(0)

    class _Coverage:
        def __init__(self, *args, **kwargs) -> None: ...

        def daily_report(self, **kwargs):
            return {"events": coverage_events(games)}

    def _fetch(settings, *, limit=10, now=None, starts_within_hours=12,
               starts_after=None, event_ids=None, **_):
        # The provider answers the WINDOW: the earliest `limit` games at or
        # after the anchor, which is exactly what makes the anchor load-bearing.
        pool = sorted((s, g) for g, s in games.items()
                      if starts_after <= s and s > now)[:limit]
        ids = [g for _, g in pool]
        sent.append({"limit": limit, "starts_after": starts_after, "games": ids})
        pairs = [SimpleNamespace(event_id=f"evt-{g[:8]}", timestamp_skew_seconds=0.0,
                                 bookmaker_key="draftkings") for g in ids]
        return sgo_collection.FallbackFetch(
            requested_at=now, objects=len(ids), pairs=pairs, parse=ParseResult(),
            main=None, provider_league="MLB",
            raw_archive={"status": "ARCHIVED", "responseBodySha256": "cd" * 32},
            returned_events=[])

    def _ingest(session, settings, fetch, *, league_code, event_date=None):
        # The object LANDS: a quote row bound to each game, which is what makes
        # a named game's unit spent.
        for pair in fetch.pairs:
            for gid in games:
                if pair.event_id == f"evt-{gid[:8]}":
                    price(session, gid, at=fetch.requested_at)
        fetch.events_ingested = len(fetch.pairs)
        return fetch

    monkeypatch.setattr("app.services.quote_coverage.QuoteCoverageService", _Coverage)
    monkeypatch.setattr(sgo_collection, "eligible", lambda settings, now=None: (True, "OK"))
    monkeypatch.setattr(sgo_collection, "fetch_pregame_moneylines", _fetch)
    monkeypatch.setattr(sgo_collection, "ingest", _ingest)
    monkeypatch.setattr(sgo_collection, "_games_missing_total", lambda session, ids: set())
    monkeypatch.setattr(
        sgo_collection, "_ingested_game_ids",
        lambda session, fetch: sorted(g for g in games
                                      if f"evt-{g[:8]}" in {p.event_id for p in fetch.pairs}))
    return sgo_collection


def test_with_the_lane_spent_the_collector_buys_only_the_named_games(day, settings, monkeypatch):
    sent: list = []
    collection = install_provider(monkeypatch, sent)
    exhaust_mlb(day, at=datetime(2026, 9, 20, 16, 0, tzinfo=UTC))

    result = collection.collect_for_open_cutoffs(day, settings, now=TICK)

    assert result["reason"] == "FALLBACK_COLLECTED"
    # The three named games inside their windows -- NOT the unnamed 16:05 game,
    # which the day's own ranking would otherwise have put first.
    assert sorted(result["games_selected"]) == sorted(NAMED[:3])
    assert UNNAMED in result["games_unsupported_today"]
    assert result["objects"] == 3
    # The anchor is still carried into the request, so the provider cannot
    # answer with the unnamed game instead.
    assert sent[0]["starts_after"] > AT_1605
    assert sorted(sent[0]["games"]) == sorted(NAMED[:3])
    # The receipt cites the authorisation rather than describing a policy.
    assert result["owner_exception"]["service_date"] == "2026-09-20"
    assert "project owner" in result["owner_exception"]["authority"]
    assert sorted(result["owner_exception"]["game_ids"]) == sorted(NAMED[:3])
    # Three units are now spent by the quotes they bought. The 19:20 game's
    # window has not opened, so its one unit is all that is left.
    state = owner_exception_state(day, lane=LANE, now=TICK)
    assert state["available_game_ids"] == [NAMED[3]]
    assert state["units_available"] == 1
    assert build_plan(day, now=TICK).allocations[LANE].authorised_allowance == 18
    # The reservation carries the same citation, written before the request.
    reservation = day.query(ProviderRequestLedger).filter_by(
        endpoint_key=LANE, status="RESERVED", quota_cost=3).one()
    assert reservation.response_metadata["owner_exception"]["objects"] == 3
    # And every existing protection still applies to this purchase.
    assert result["raw_archive"]["status"] == "ARCHIVED"
    assert result["lane_halted"] is False
    assert result["selection_reached_provider"] is True


def test_on_any_other_day_the_spent_lane_simply_refuses(day, settings, monkeypatch):
    sent: list = []
    # The same slate, the same spent lane, one day later. Only the date moves.
    collection = install_provider(monkeypatch, sent, slate_for(1))
    other = datetime(2026, 9, 21, 16, 40, tzinfo=UTC)
    exhaust_mlb(day, at=datetime(2026, 9, 21, 16, 0, tzinfo=UTC))

    result = collection.collect_for_open_cutoffs(day, settings, now=other)

    assert result["reason"] == "ALLOCATION_LANE_DAILY_ALLOWANCE_SPENT"
    assert result["objects"] == 0
    assert sent == [], "no request was sent and no object was spent"


def test_once_the_named_games_are_priced_the_collector_refuses_again(day, settings, monkeypatch):
    sent: list = []
    collection = install_provider(monkeypatch, sent)
    exhaust_mlb(day, at=datetime(2026, 9, 20, 16, 0, tzinfo=UTC))
    for game_id in NAMED[:3]:
        price(day, game_id)

    result = collection.collect_for_open_cutoffs(day, settings, now=TICK)

    # The only named game left is the 19:20 one, whose window has not opened,
    # and a refresh of the three that are priced cannot draw on the exception.
    assert result["reason"] == "ALLOCATION_LANE_DAILY_ALLOWANCE_SPENT"
    assert sent == []

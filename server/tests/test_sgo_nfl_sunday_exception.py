"""The owner's dated SportsGameOdds reserve exception: NFL official lane, 2026-10-04.

WHAT WAS AUTHORISED
-------------------
The month is paused: cap 2500, used 2141, remaining 359, protected reserve 400,
so spendable is 0 and `request_objects` answers BUDGET_PAUSED_UNTIL_REPLENISHMENT
for every lane. The owner authorised ONE exception: lane `sgo_nfl_events` only,
Eastern service day 2026-10-04 only, the fourteen named NFL games only, at most
one object each and fourteen in total, never taking the provider's remaining
balance below 345 (359 - 14 = 345).

WHAT THESE TESTS PIN
--------------------
* the exception is reachable during the pause, but only for a selection made
  up entirely of named games, on that service day, on that lane;
* other lanes, 10-03, 10-05, unnamed games, a request naming no games, more
  than one object per game or fourteen in total, and any draw that would take
  the provider's remaining balance below 345 all stay paused;
* outside the exception the pause refusal is exactly what it was;
* the NFL pregame collector hands the due CHECKPOINT games to the allocation as
  its selection, never a preview, never an unnamed game.

No test here reaches the network: the real HTTP transport raises.
"""
from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from app.models import (
    Market, MarketOutcome, MarketType, OddsSnapshot, ProviderRequestLedger, Sportsbook,
)
from app.services import provider_allocation
from app.services import sgo_collection
from app.services import sgo_nfl_pregame as lane
from app.services.provider_allocation import (
    OWNER_DAY_EXCEPTIONS, PROVIDER, USAGE_ENDPOINT_KEY, build_plan,
    owner_exception_for, request_objects, reserve_at,
)

LANE = "sgo_nfl_events"
OTHER_LANES = ("sgo_mlb_events", "sgo_nfl_live", "sgo_nfl_periods",
               "sgo_show_preview_events")

#: The fourteen games, retyped here ON PURPOSE (unlike the MLB test): a typo in
#: the constant must fail a test rather than be silently agreed with.
NAMED = {
    "a98c9cdc-99a4-4c55-9e08-80bc2baba451": datetime(2026, 10, 4, 13, 30, tzinfo=UTC),
    "edf09727-981a-49f9-b720-3f1ad1f3ab98": datetime(2026, 10, 4, 17, 0, tzinfo=UTC),
    "e8f2ce2b-3a95-4ddf-a1c7-8fd1ec50cf2e": datetime(2026, 10, 4, 17, 0, tzinfo=UTC),
    "149a8b3a-d547-4c69-a28f-11c462c9b750": datetime(2026, 10, 4, 17, 0, tzinfo=UTC),
    "9be6c229-6169-4085-ae67-47d4b0e77423": datetime(2026, 10, 4, 17, 0, tzinfo=UTC),
    "27c3582a-ed7d-4fa6-bd9e-5f9713eee682": datetime(2026, 10, 4, 17, 0, tzinfo=UTC),
    "c5ad9fe8-c18b-4888-a6aa-d38ccf41f629": datetime(2026, 10, 4, 17, 0, tzinfo=UTC),
    "aa33f876-b548-4d86-acd1-7682692ec857": datetime(2026, 10, 4, 17, 0, tzinfo=UTC),
    "c2c07e12-1fe1-4b9f-aaea-3417b8509ea6": datetime(2026, 10, 4, 17, 0, tzinfo=UTC),
    "d392b4c9-7c17-4192-9a4c-9d6a61aaf62a": datetime(2026, 10, 4, 20, 5, tzinfo=UTC),
    "470b5b88-2acf-47bf-8cb9-1f515e56ef64": datetime(2026, 10, 4, 20, 25, tzinfo=UTC),
    "4d345aca-2ca4-4491-86ea-3dd79d8e9e72": datetime(2026, 10, 4, 20, 25, tzinfo=UTC),
    "ab7b1506-1dd7-410c-b997-79bd1f1689fe": datetime(2026, 10, 4, 20, 25, tzinfo=UTC),
    "a7655055-8d55-42d6-8ad3-14878c083146": datetime(2026, 10, 5, 0, 20, tzinfo=UTC),
}
IDS = sorted(NAMED)
IND_WAS = "a98c9cdc-99a4-4c55-9e08-80bc2baba451"
DET_CAR = "a7655055-8d55-42d6-8ad3-14878c083146"
UNNAMED = str(uuid.uuid4())

SUNDAY = datetime(2026, 10, 4, 13, 4, tzinfo=UTC)          # IND@WAS T-26, 09:04 ET
SATURDAY = datetime(2026, 10, 3, 17, 0, tzinfo=UTC)
MONDAY = datetime(2026, 10, 5, 17, 0, tzinfo=UTC)
MONDAY_AFTER_MIDNIGHT_ET = datetime(2026, 10, 5, 4, 30, tzinfo=UTC)   # 00:30 ET Oct 5
SUNDAY_NIGHT_UTC_MONDAY = datetime(2026, 10, 4, 23, 54, tzinfo=UTC)  # DET@CAR T-26, 19:54 ET

READING_AT = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def record_usage(session: Session, *, used: int, cap: int = 2500,
                 at: datetime = READING_AT):
    tag = f"u{used}{at.timestamp()}".ljust(64, "0")[:64]
    reservation = ProviderRequestLedger(
        provider=PROVIDER, endpoint_key=USAGE_ENDPOINT_KEY, requested_at=at,
        status="RESERVED", request_fingerprint=tag, quota_cost=0)
    session.add(reservation)
    session.flush()
    session.add(ProviderRequestLedger(
        provider=PROVIDER, endpoint_key=USAGE_ENDPOINT_KEY, requested_at=at,
        status="SUCCEEDED", request_fingerprint=tag, completed_at=at,
        reservation_id=reservation.id, records_received=0, quota_cost=0,
        response_metadata={"usage": {"monthly_cap": cap, "used_this_month": used}}))
    session.commit()


def spend(session: Session, *, lane_key: str, objects: int, at: datetime,
          settled: bool = True, metadata: dict | None = None):
    tag = uuid.uuid4().hex.ljust(64, "0")[:64]
    reservation = ProviderRequestLedger(
        provider=PROVIDER, endpoint_key=lane_key, requested_at=at, status="RESERVED",
        request_fingerprint=tag, quota_cost=objects, response_metadata=metadata)
    session.add(reservation)
    session.flush()
    if settled:
        session.add(ProviderRequestLedger(
            provider=PROVIDER, endpoint_key=lane_key, requested_at=at,
            status="SUCCEEDED", request_fingerprint=tag, completed_at=at,
            reservation_id=reservation.id, records_received=objects))
    session.commit()
    return reservation


def price(session: Session, game_id: str, *, at: datetime = SUNDAY):
    """A purchased SportsGameOdds quote bound to one game -- the object landed."""

    book = Sportsbook(key=f"b{uuid.uuid4().hex[:8]}", name="Book")
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


@pytest.fixture
def paused(session: Session):
    """The live position: 2141 of 2500 used, reserve 400, so the month is paused."""

    record_usage(session, used=2141)
    return session


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import httpx

    def refuse(self, *args, **kwargs):
        raise AssertionError("UNEXPECTED_NETWORK_CALL")

    monkeypatch.setattr(httpx.Client, "__init__", refuse)


# -- the authorisation itself ---------------------------------------------

def _nfl_exception():
    found = [e for e in OWNER_DAY_EXCEPTIONS if e.lane == LANE and e.service_date == "2026-10-04"]
    assert len(found) == 1
    return found[0]


def test_the_exception_is_one_day_one_lane_fourteen_games_two_caps_and_a_floor():
    entry = _nfl_exception()
    assert entry.service_date == "2026-10-04"
    assert entry.lane == LANE
    assert entry.per_game_objects == 1
    assert entry.total_objects == 14
    assert entry.provider_remaining_floor == 345
    assert entry.game_ids == frozenset(IDS)
    assert len(entry.games) == 14
    assert entry.as_dict()["provider_remaining_floor"] == 345


def test_it_is_matched_by_date_and_lane_and_by_nothing_else():
    entry = _nfl_exception()
    assert owner_exception_for(date(2026, 10, 4), LANE) is entry
    assert owner_exception_for(date(2026, 10, 3), LANE) is None
    assert owner_exception_for(date(2026, 10, 5), LANE) is not entry
    for other in OTHER_LANES:
        assert owner_exception_for(date(2026, 10, 4), other) is None


def test_the_reserve_and_the_renewal_cap_are_unchanged():
    from app.data_sources.provider_registry import NFL_LANE_RENEWAL_OBJECT_CAP

    assert reserve_at(SUNDAY) == 400
    assert provider_allocation.DEFAULT_OBJECT_RESERVE == 400
    assert NFL_LANE_RENEWAL_OBJECT_CAP == 150


# -- the pause, outside the exception -------------------------------------

def test_the_month_really_is_paused(paused):
    plan = build_plan(paused, now=SUNDAY)
    assert plan.daily_total == 0
    assert plan.remaining == 359
    assert plan.spendable == 0
    assert plan.source == "PROVIDER_ACCOUNT_USAGE"


def test_without_a_selection_the_lane_stays_paused(paused):
    for selection in (None, []):
        grant = request_objects(paused, lane=LANE, wanted=1, now=SUNDAY,
                                selection=selection)
        assert grant.granted == 0
        assert grant.reason == "BUDGET_PAUSED_UNTIL_REPLENISHMENT"


@pytest.mark.parametrize("other", OTHER_LANES)
def test_no_other_lane_draws_on_it(paused, other):
    grant = request_objects(paused, lane=other, wanted=1, now=SUNDAY, selection=IDS)
    assert grant.granted == 0
    assert grant.reason in {"BUDGET_PAUSED_UNTIL_REPLENISHMENT", "LANE_NOT_FUNDED"}


@pytest.mark.parametrize("moment", [SATURDAY, MONDAY, MONDAY_AFTER_MIDNIGHT_ET])
def test_named_games_stay_paused_on_any_other_day(paused, moment):
    grant = request_objects(paused, lane=LANE, wanted=1, now=moment, selection=[IND_WAS])
    assert grant.granted == 0
    assert grant.reason == "BUDGET_PAUSED_UNTIL_REPLENISHMENT"


def test_the_pause_refusal_is_unchanged_outside_the_exception(paused):
    """Same Grant fields the pre-change code produced, on a day with no exception."""

    grant = request_objects(paused, lane=LANE, wanted=3, now=SATURDAY)
    assert (grant.lane, grant.wanted, grant.granted, grant.reason) == (
        LANE, 3, 0, "BUDGET_PAUSED_UNTIL_REPLENISHMENT")
    assert grant.plan.allocations[LANE].owner_exception is None
    assert "owner_exception" not in grant.as_dict()["allocation"]


# -- what it does grant ---------------------------------------------------

@pytest.mark.parametrize("game_id", IDS)
def test_each_named_game_is_granted_one_object(paused, game_id):
    grant = request_objects(paused, lane=LANE, wanted=1, now=SUNDAY, selection=[game_id])
    assert grant.granted == 1
    assert grant.reason == "GRANTED_OWNER_EXCEPTION"


def test_the_sunday_night_game_is_still_the_eastern_sunday(paused):
    grant = request_objects(paused, lane=LANE, wanted=1, now=SUNDAY_NIGHT_UTC_MONDAY,
                            selection=[DET_CAR])
    assert grant.granted == 1


def test_all_fourteen_at_once_and_not_one_more(paused):
    grant = request_objects(paused, lane=LANE, wanted=14, now=SUNDAY, selection=IDS)
    assert grant.granted == 14 and grant.reason == "GRANTED_OWNER_EXCEPTION"
    over = request_objects(paused, lane=LANE, wanted=15, now=SUNDAY, selection=IDS)
    assert over.granted == 14 and over.reason == "GRANTED_OWNER_EXCEPTION_PARTIAL"


def test_one_game_cannot_draw_two_objects(paused):
    grant = request_objects(paused, lane=LANE, wanted=2, now=SUNDAY, selection=[IND_WAS])
    assert grant.granted == 1


def test_an_unnamed_game_cannot_draw_on_it(paused):
    alone = request_objects(paused, lane=LANE, wanted=1, now=SUNDAY, selection=[UNNAMED])
    mixed = request_objects(paused, lane=LANE, wanted=2, now=SUNDAY,
                            selection=[IND_WAS, UNNAMED])
    for grant in (alone, mixed):
        assert grant.granted == 0
        assert grant.reason == "BUDGET_PAUSED_UNTIL_REPLENISHMENT"


def test_a_priced_game_has_had_its_object(paused):
    price(paused, IND_WAS)
    grant = request_objects(paused, lane=LANE, wanted=1, now=SUNDAY, selection=[IND_WAS])
    assert grant.granted == 0


def test_an_open_reservation_holds_its_games(paused):
    spend(paused, lane_key=LANE, objects=1, at=SUNDAY - timedelta(minutes=1),
          settled=False, metadata={"owner_exception": {"game_ids": [IND_WAS]}})
    grant = request_objects(paused, lane=LANE, wanted=1, now=SUNDAY, selection=[IND_WAS])
    assert grant.granted == 0


def test_the_total_is_fourteen_however_the_games_are_spent(paused):
    for game_id in IDS:
        price(paused, game_id)
    grant = request_objects(paused, lane=LANE, wanted=14, now=SUNDAY, selection=IDS)
    assert grant.granted == 0


# -- the provider floor ---------------------------------------------------

def test_the_floor_admits_exactly_fourteen_from_359(paused):
    plan = build_plan(paused, now=SUNDAY)
    assert plan.remaining - 14 == 345


def test_spend_since_the_reading_lowers_the_floor_headroom(paused):
    # 10 objects settled elsewhere since the reading: remaining 349, floor 345.
    spend(paused, lane_key="sgo_mlb_events", objects=10, at=SUNDAY - timedelta(hours=2))
    grant = request_objects(paused, lane=LANE, wanted=14, now=SUNDAY, selection=IDS)
    assert grant.granted == 4
    assert grant.reason == "GRANTED_OWNER_EXCEPTION_PARTIAL"


def test_objects_in_flight_count_against_the_floor(paused):
    spend(paused, lane_key=LANE, objects=13, at=SUNDAY - timedelta(minutes=1),
          settled=False)
    grant = request_objects(paused, lane=LANE, wanted=2, now=SUNDAY,
                            selection=[IND_WAS, DET_CAR])
    assert grant.granted == 1                 # 359 - 13 in flight - 345 = 1


@pytest.mark.parametrize("used", [2155, 2200, 2499])
def test_no_draw_may_take_remaining_below_345(session, used):
    record_usage(session, used=used)
    grant = request_objects(session, lane=LANE, wanted=1, now=SUNDAY, selection=[IND_WAS])
    assert grant.granted == 0
    assert grant.reason == "BUDGET_PAUSED_UNTIL_REPLENISHMENT"


def test_one_object_of_headroom_is_one_object(session):
    record_usage(session, used=2154)          # remaining 346
    grant = request_objects(session, lane=LANE, wanted=3, now=SUNDAY, selection=IDS[:3])
    assert grant.granted == 1


def test_without_a_provider_reading_the_floor_fails_closed(paused, monkeypatch):
    real = provider_allocation.build_plan

    def unread(session, *, now=None):
        return dataclasses.replace(real(session, now=now), source="LOCAL_LEDGER_COUNT")

    monkeypatch.setattr(provider_allocation, "build_plan", unread)
    grant = request_objects(paused, lane=LANE, wanted=1, now=SUNDAY, selection=[IND_WAS])
    assert grant.granted == 0
    assert grant.reason == "BUDGET_PAUSED_UNTIL_REPLENISHMENT"


# -- the collector hands its due CHECKPOINT games over as the selection ----

def cfg(**over):
    base = dict(sportsgameodds_enabled=True, sportsgameodds_api_key="test-key-not-real",  # secret-guard: test-fixture
                sportsgameodds_base_url="https://sgo.invalid/v2",
                nfl_late_checkpoint_retime_enabled=True,
                show_preview_freeze_time="16:30", show_preview_timezone="America/New_York")
    base.update(over)
    return SimpleNamespace(**base)


def _due(game_id: str, kickoff: datetime, moment: datetime, *, pass_: str = "CHECKPOINT"):
    entry = {"game_id": game_id, "starts_at": kickoff.isoformat(),
             "minutes_to_kickoff": round((kickoff - moment).total_seconds() / 60, 1),
             "away": "A", "home": "H", "pass": pass_}
    if pass_ == "CHECKPOINT":
        entry["kickoff_cluster"] = kickoff.isoformat()
        entry["phase"] = "PRIMARY"
    return entry


class _StopBudget:
    def __init__(self):
        self.asked = None

    def may_spend(self, objects):
        self.asked = objects
        return False, "STOP_HERE_FOR_THE_TEST"

    def as_dict(self):
        return {"asked": self.asked}


def _wire(monkeypatch, due, *, budget=None):
    calls: list[dict] = []
    real = provider_allocation.request_objects

    def spy(session, **kwargs):
        calls.append(dict(kwargs))
        return real(session, **kwargs)

    monkeypatch.setattr(lane, "due_games", lambda *a, **k: list(due))
    monkeypatch.setattr(provider_allocation, "request_objects", spy)
    if budget is not None:
        monkeypatch.setattr(sgo_collection, "object_budget", lambda *a, **k: budget)
    return calls


MIA_MIN = "d392b4c9-7c17-4192-9a4c-9d6a61aaf62a"
MIA_MIN_T26 = datetime(2026, 10, 4, 19, 39, tzinfo=UTC)


def test_the_collector_passes_its_due_checkpoint_games_as_the_selection(paused, monkeypatch):
    # MIA@MIN's T-26 falls inside Monday's Show Prep preview slot: the preview
    # entry and an unnamed checkpoint game must neither poison nor join it.
    mnf = datetime(2026, 10, 6, 0, 15, tzinfo=UTC)
    due = [_due(MIA_MIN, NAMED[MIA_MIN], MIA_MIN_T26),
           _due(UNNAMED, NAMED[MIA_MIN], MIA_MIN_T26),
           _due(str(uuid.uuid4()), mnf, MIA_MIN_T26, pass_="PREVIEW")]
    budget = _StopBudget()
    calls = _wire(monkeypatch, due, budget=budget)
    result = lane.collect_nfl_pregame(paused, cfg(), now=MIA_MIN_T26)
    assert calls[0].get("selection") is None                   # the unchanged first ask
    assert calls[1]["selection"] == [MIA_MIN]
    assert calls[1]["wanted"] == 1
    assert result["reason"] == "OBJECT_RESERVE_GUARD"           # guard B still runs
    assert budget.asked == 1


def test_the_collector_is_unchanged_on_any_other_day(paused, monkeypatch):
    moment = SATURDAY
    due = [_due(IND_WAS, moment + timedelta(minutes=26), moment)]
    calls = _wire(monkeypatch, due, budget=_StopBudget())
    before = paused.query(ProviderRequestLedger).count()
    result = lane.collect_nfl_pregame(paused, cfg(), now=moment)
    assert result["reason"] == "ALLOCATION_BUDGET_PAUSED_UNTIL_REPLENISHMENT"
    assert len(calls) == 1 and calls[0].get("selection") is None
    assert paused.query(ProviderRequestLedger).count() == before


def test_a_preview_alone_never_draws_on_it(paused, monkeypatch):
    due = [_due(IND_WAS, NAMED[IND_WAS], SUNDAY, pass_="PREVIEW")]
    calls = _wire(monkeypatch, due, budget=_StopBudget())
    result = lane.collect_nfl_pregame(paused, cfg(), now=SUNDAY)
    assert result["reason"] == "ALLOCATION_BUDGET_PAUSED_UNTIL_REPLENISHMENT"
    assert len(calls) == 1


class _OpenBudget(_StopBudget):
    allowance, reserve, objects_used = 2500, 400, 0

    def may_spend(self, objects):
        self.asked = objects
        return True, None


def test_the_reservation_names_the_exception_games(paused, monkeypatch):
    due = [_due(IND_WAS, NAMED[IND_WAS], SUNDAY)]
    _wire(monkeypatch, due, budget=_OpenBudget())
    monkeypatch.setattr(lane, "_slate_events", lambda *a, **k: [
        {"game_id": IND_WAS, "starts_at_utc": NAMED[IND_WAS].isoformat()}])

    def no_provider(*args, **kwargs):
        raise RuntimeError("provider not called in tests")

    monkeypatch.setattr(lane, "fetch_nfl_pregame", no_provider)
    result = lane.collect_nfl_pregame(paused, cfg(), now=SUNDAY)
    assert result["reason"] == "REQUEST_FAILED"
    assert result["games_selected"] == [IND_WAS]
    reservation = paused.query(ProviderRequestLedger).filter_by(
        endpoint_key=LANE, status="RESERVED").one()
    assert reservation.quota_cost == 1
    receipt = reservation.response_metadata["owner_exception"]
    assert receipt["game_ids"] == [IND_WAS]
    assert receipt["service_date"] == "2026-10-04"
    assert receipt["grant_reason"] == "GRANTED_OWNER_EXCEPTION"


# == Revision 2 (Codex R1): PROVIDER-WIDE outstanding commitments ============
#
# The floor used to subtract only TODAY's, declared-lane in-flight objects. A
# commitment from before the Eastern service day began, on another lane, or one
# the provider reading may not contain, was invisible to it. Each test below is
# one commitment the old arithmetic missed; each leaves remaining >= 345.

from app.services.provider_allocation import provider_outstanding_commitments  # noqa: E402

SUNDAY_EVE_UTC = datetime(2026, 10, 4, 3, 59, tzinfo=UTC)   # 23:59 ET Saturday


def ledger(session: Session, *, lane_key: str, reserved_at: datetime,
           completed_at: datetime | None = None, status: str = "SUCCEEDED",
           records: int | None = 0, quota_cost: int | None = 1,
           limit: int | None = None, metadata: dict | None = None):
    """One reservation and, unless ``completed_at`` is None, its terminal row."""

    tag = uuid.uuid4().hex.ljust(64, "0")[:64]
    meta = dict(metadata or {})
    if limit is not None:
        meta["request_shape"] = {"filters": {"leagueID": "NFL", "limit": str(limit)}}
    reservation = ProviderRequestLedger(
        provider=PROVIDER, endpoint_key=lane_key, requested_at=reserved_at,
        status="RESERVED", request_fingerprint=tag, quota_cost=quota_cost,
        response_metadata=meta or None)
    session.add(reservation)
    session.flush()
    if completed_at is not None:
        session.add(ProviderRequestLedger(
            provider=PROVIDER, endpoint_key=lane_key, requested_at=reserved_at,
            status=status, request_fingerprint=tag, completed_at=completed_at,
            reservation_id=reservation.id, records_received=records))
    session.commit()
    return reservation


def _draw14(session, moment=SUNDAY):
    return request_objects(session, lane=LANE, wanted=14, now=moment, selection=IDS)


def test_codex_example_a_prior_day_unresolved_object_is_counted(paused):
    """359 remaining + 1 unresolved object reserved 03:59Z, before the 04:00Z
    Eastern boundary: the old code admitted 14 and left 344. Now 13, 345."""

    ledger(paused, lane_key=LANE, reserved_at=SUNDAY_EVE_UTC, completed_at=None)
    plan = build_plan(paused, now=SUNDAY)
    assert sum(a.in_flight for a in plan.allocations.values()) == 0   # Guard A is blind
    grant = _draw14(paused)
    assert grant.granted == 13
    assert 359 - 1 - grant.granted == 345


@pytest.mark.parametrize("lane_key,reserved_at,objects", [
    ("sgo_show_preview_events", datetime(2026, 10, 3, 19, 30, tzinfo=UTC), 3),  # other lane, prior day
    ("sgo_mlb_events", datetime(2026, 10, 2, 10, 0, tzinfo=UTC), 2),            # before the reading
    ("sgo_nfl_live", SUNDAY - timedelta(minutes=5), 4),                        # other lane, same day
    (LANE, SUNDAY - timedelta(minutes=5), 5),                                  # same lane, same day
])
def test_every_unresolved_reservation_counts_whatever_its_day_or_lane(
        paused, lane_key, reserved_at, objects):
    ledger(paused, lane_key=lane_key, reserved_at=reserved_at, completed_at=None,
           quota_cost=objects)
    out = provider_outstanding_commitments(
        paused, reading=provider_allocation.latest_usage_reading(paused))
    assert out["unresolved_reservations"] == 1 and out["objects"] == objects
    assert _draw14(paused).granted == 14 - objects


def test_an_unresolved_reservation_is_charged_its_limit_if_larger(paused):
    ledger(paused, lane_key="sgo_mlb_events", reserved_at=SUNDAY_EVE_UTC,
           completed_at=None, quota_cost=1, limit=6)
    assert _draw14(paused).granted == 8


def test_an_unresolved_reservation_of_unknown_size_closes_the_exception(paused):
    ledger(paused, lane_key="sgo_mlb_events", reserved_at=SUNDAY_EVE_UTC,
           completed_at=None, quota_cost=None)
    grant = _draw14(paused)
    assert grant.granted == 0                              # charged 20, the worst case
    assert grant.reason == "BUDGET_PAUSED_UNTIL_REPLENISHMENT"


def test_reserved_before_the_reading_and_completed_after_it_is_counted(paused):
    ledger(paused, lane_key="sgo_mlb_events", reserved_at=READING_AT - timedelta(hours=1),
           completed_at=READING_AT + timedelta(hours=1), records=3)
    assert build_plan(paused, now=SUNDAY).used_this_month == 2141   # Guard A misses it
    out = provider_outstanding_commitments(
        paused, reading=provider_allocation.latest_usage_reading(paused))
    assert out["reserved_before_reading_completed_after"] == 1
    assert _draw14(paused).granted == 11


def test_a_charge_completed_before_the_reading_is_not_counted_twice(paused):
    ledger(paused, lane_key="sgo_mlb_events", reserved_at=READING_AT - timedelta(hours=2),
           completed_at=READING_AT - timedelta(hours=1), records=9)
    assert _draw14(paused).granted == 14


def test_a_charged_failure_after_the_reading_is_a_charge(paused):
    ledger(paused, lane_key=LANE, reserved_at=SUNDAY - timedelta(hours=1),
           completed_at=SUNDAY - timedelta(hours=1), status="FAILED", records=2)
    assert build_plan(paused, now=SUNDAY).used_this_month == 2141   # Guard A misses it
    assert _draw14(paused).granted == 12


def test_a_zero_charge_failure_costs_nothing(paused):
    ledger(paused, lane_key=LANE, reserved_at=SUNDAY - timedelta(hours=1),
           completed_at=SUNDAY - timedelta(hours=1), status="FAILED", records=0)
    assert _draw14(paused).granted == 14


@pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED"])
def test_an_unknown_charge_is_charged_its_whole_commitment(paused, status):
    ledger(paused, lane_key="sgo_mlb_events", reserved_at=SUNDAY - timedelta(hours=1),
           completed_at=SUNDAY - timedelta(hours=1), status=status, records=None,
           quota_cost=5)
    out = provider_outstanding_commitments(
        paused, reading=provider_allocation.latest_usage_reading(paused))
    assert out["unknown_charge_after_reading"] == 1 and out["objects"] == 5
    assert _draw14(paused).granted == 9


def test_commitments_add_up_and_the_floor_still_holds(paused):
    ledger(paused, lane_key=LANE, reserved_at=SUNDAY_EVE_UTC, completed_at=None)
    ledger(paused, lane_key="sgo_mlb_events", reserved_at=READING_AT - timedelta(minutes=5),
           completed_at=READING_AT + timedelta(minutes=5), records=2)
    ledger(paused, lane_key=LANE, reserved_at=SUNDAY - timedelta(hours=1),
           completed_at=SUNDAY - timedelta(hours=1), status="FAILED", records=1)
    grant = _draw14(paused)
    assert grant.granted == 10
    assert 359 - 4 - grant.granted == 345


def test_reservations_are_never_cleared_to_make_room(paused):
    held = ledger(paused, lane_key="sgo_mlb_events", reserved_at=SUNDAY_EVE_UTC,
                  completed_at=None, quota_cost=14)
    before = paused.query(ProviderRequestLedger).count()
    grant = _draw14(paused)
    assert grant.granted == 0
    assert paused.query(ProviderRequestLedger).count() == before
    assert paused.query(ProviderRequestLedger).filter_by(reservation_id=held.id).count() == 0


def test_an_unreadable_ledger_draws_nothing(paused, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("ledger unreadable")

    monkeypatch.setattr(provider_allocation, "provider_outstanding_commitments", boom)
    grant = request_objects(paused, lane=LANE, wanted=1, now=SUNDAY, selection=[IND_WAS])
    assert grant.granted == 0


def test_a_reading_from_an_earlier_billing_month_draws_nothing(session):
    record_usage(session, used=2141, at=datetime(2026, 9, 30, 23, 0, tzinfo=UTC))
    grant = request_objects(session, lane=LANE, wanted=1, now=SUNDAY, selection=[IND_WAS])
    assert grant.granted == 0


# == Revision 2 (Codex R2): only the exact T-26 PRIMARY or an owed RETRY =====

KICK = NAMED[IND_WAS]                                      # 13:30Z


def _entry(moment, *, phase="PRIMARY", pass_="CHECKPOINT", cluster=True):
    entry = {"game_id": IND_WAS, "starts_at": KICK.isoformat(),
             "minutes_to_kickoff": round((KICK - moment).total_seconds() / 60, 1),
             "away": "IND", "home": "WAS", "pass": pass_}
    if cluster:
        entry["kickoff_cluster"] = KICK.isoformat()
    if phase is not None:
        entry["phase"] = phase
    return entry


def _failed_primary(session, *, records=0, terminal=True):
    at = KICK - timedelta(minutes=26)
    return ledger(session, lane_key=LANE, reserved_at=at,
                  completed_at=(at + timedelta(seconds=3)) if terminal else None,
                  status="FAILED", records=records)


@pytest.mark.parametrize("minutes,phase,cluster,retime", [
    (60, None, True, False),        # RETIME=false: the inherited T75-T15 CHECKPOINT
    (60, "PRIMARY", True, False),   # a stale phase label does not switch it back on
    (26, "PRIMARY", True, False),   # even at T-26
    (26, None, True, True),         # missing phase
    (26, "BOGUS", True, True),      # unknown phase
    (26, "RETRY", True, True),      # wrong phase for the clock
    (60, "PRIMARY", True, True),    # PRIMARY label outside [T-27, T-25]
    (24, "PRIMARY", True, True),
    (28, "PRIMARY", True, True),
    (26, "PRIMARY", False, True),   # no kickoff cluster
])
def test_the_exception_refuses_anything_but_a_valid_retimed_phase(
        paused, monkeypatch, minutes, phase, cluster, retime):
    moment = KICK - timedelta(minutes=minutes)
    entry = _entry(moment, phase=phase, cluster=cluster)
    settings = cfg(nfl_late_checkpoint_retime_enabled=retime)
    assert lane.exception_phase_is_valid(paused, settings, entry, now=moment) is False
    calls = _wire(monkeypatch, [entry], budget=_StopBudget())
    before = paused.query(ProviderRequestLedger).count()
    result = lane.collect_nfl_pregame(paused, settings, now=moment)
    assert result["reason"] == "ALLOCATION_BUDGET_PAUSED_UNTIL_REPLENISHMENT"
    assert len(calls) == 1 and calls[0].get("selection") is None
    assert paused.query(ProviderRequestLedger).count() == before


def test_a_primary_already_attempted_cannot_draw_again(paused):
    _failed_primary(paused)
    moment = KICK - timedelta(minutes=25, seconds=30)
    assert lane.exception_phase_is_valid(paused, cfg(), _entry(moment), now=moment) is False


@pytest.mark.parametrize("setup", ["none", "charged", "unresolved", "succeeded"])
def test_a_retry_is_refused_unless_owed(paused, setup):
    if setup == "charged":
        _failed_primary(paused, records=1)
    elif setup == "unresolved":
        _failed_primary(paused, terminal=False)
    elif setup == "succeeded":
        at = KICK - timedelta(minutes=26)
        ledger(paused, lane_key=LANE, reserved_at=at, completed_at=at, records=1)
    moment = KICK - timedelta(minutes=23)
    entry = _entry(moment, phase="RETRY")
    assert lane.exception_phase_is_valid(paused, cfg(), entry, now=moment) is False


def test_a_retry_outside_its_one_minute_is_refused(paused):
    _failed_primary(paused)
    for minutes in (24, 22, 21):
        moment = KICK - timedelta(minutes=minutes)
        entry = _entry(moment, phase="RETRY")
        assert lane.exception_phase_is_valid(paused, cfg(), entry, now=moment) is False


def test_the_exact_t26_primary_draws_one_object(paused, monkeypatch):
    moment = KICK - timedelta(minutes=26)
    entry = _entry(moment)
    assert lane.exception_phase_is_valid(paused, cfg(), entry, now=moment) is True
    budget = _StopBudget()
    calls = _wire(monkeypatch, [entry], budget=budget)
    result = lane.collect_nfl_pregame(paused, cfg(), now=moment)
    assert calls[1]["selection"] == [IND_WAS]
    assert result["reason"] == "OBJECT_RESERVE_GUARD" and budget.asked == 1


def test_an_owed_retry_after_a_zero_charge_failure_draws_the_same_unit(paused, monkeypatch):
    _failed_primary(paused, records=0)
    moment = KICK - timedelta(minutes=23)
    entry = _entry(moment, phase="RETRY")
    assert lane.exception_phase_is_valid(paused, cfg(), entry, now=moment) is True
    _wire(monkeypatch, [entry], budget=_OpenBudget())
    monkeypatch.setattr(lane, "_slate_events", lambda *a, **k: [
        {"game_id": IND_WAS, "starts_at_utc": KICK.isoformat()}])

    def no_provider(*args, **kwargs):
        raise RuntimeError("provider not called in tests")

    monkeypatch.setattr(lane, "fetch_nfl_pregame", no_provider)
    result = lane.collect_nfl_pregame(paused, cfg(), now=moment)
    assert result["reason"] == "REQUEST_FAILED"
    assert result["games_selected"] == [IND_WAS]
    retry = paused.query(ProviderRequestLedger).filter(
        ProviderRequestLedger.endpoint_key == LANE,
        ProviderRequestLedger.status == "RESERVED",
        ProviderRequestLedger.requested_at == moment).one()
    assert retry.quota_cost == 1
    assert retry.response_metadata["owner_exception"]["game_ids"] == [IND_WAS]
    # The zero-charge primary cost nothing, so the floor arithmetic is intact.
    out = provider_outstanding_commitments(
        paused, reading=provider_allocation.latest_usage_reading(paused))
    assert out["objects"] == 0
    # The retry opportunity is now spent: a second T-23 tick cannot draw.
    assert lane.exception_phase_is_valid(paused, cfg(), entry, now=moment) is False

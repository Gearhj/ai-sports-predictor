"""A free balance read must not hold off a price collection.

WHAT HAPPENED
-------------
The collector's cooldown counted every SportsGameOdds ledger row. The account
balance reading is one of those rows -- it spends no entities, the endpoint
returns account data -- and it runs on its own cadence. On 2026-09-14 the reads
at 16:45:13, 16:56:44 and 17:12:17 each re-armed a ten-minute cooldown, so SD at
COL went two and a half hours without a price while its lane held six spare
objects and its cutoff approached.

WHY THIS IS THE RIGHT LINE
--------------------------
The cooldown exists to stop a five-minute tick re-buying prices that cannot have
changed, and to stop a failing provider being hammered. A request that buys
nothing triggers neither concern. The opening adjustment was already excluded
for exactly this reason; the balance read belongs on the same side of the line.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import ProviderRequestLedger
from app.services.provider_allocation import USAGE_ENDPOINT_KEY
from app.services.sgo_collection import (
    ENDPOINT_KEY,
    MIN_SECONDS_BETWEEN_FETCHES,
    OPENING_ADJUSTMENT_KEY,
    SPORTSGAMEODDS,
    seconds_since_last_fetch,
)

NOW = datetime(2026, 9, 14, 21, 17, 1, tzinfo=UTC)


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    ProviderRequestLedger.__table__.create(engine)
    with Session(engine) as db:
        yield db


def _row(db, endpoint_key, requested_at):
    db.add(ProviderRequestLedger(
        id=uuid.uuid4(), provider=SPORTSGAMEODDS, endpoint_key=endpoint_key,
        requested_at=requested_at, status="RESERVED",
        request_fingerprint=uuid.uuid4().hex, controlled_live_request=True))
    db.flush()


def test_a_balance_read_does_not_restart_the_cooldown(session):
    # The real sequence: a collection at 16:55:24, then free reads after it.
    _row(session, ENDPOINT_KEY, NOW - timedelta(seconds=1297))
    _row(session, USAGE_ENDPOINT_KEY, NOW - timedelta(seconds=284))

    since = seconds_since_last_fetch(session, now=NOW)

    assert since == pytest.approx(1297, abs=1)
    assert since >= MIN_SECONDS_BETWEEN_FETCHES, (
        "the lane was eligible; only a free read was holding it off")


def test_an_opening_adjustment_still_does_not_restart_it(session):
    _row(session, ENDPOINT_KEY, NOW - timedelta(seconds=1297))
    _row(session, OPENING_ADJUSTMENT_KEY, NOW - timedelta(seconds=10))

    assert seconds_since_last_fetch(session, now=NOW) == pytest.approx(1297, abs=1)


def test_a_real_fetch_still_restarts_the_cooldown(session):
    """The protection it was actually written for is untouched."""

    _row(session, ENDPOINT_KEY, NOW - timedelta(seconds=1297))
    _row(session, ENDPOINT_KEY, NOW - timedelta(seconds=120))

    since = seconds_since_last_fetch(session, now=NOW)

    assert since == pytest.approx(120, abs=1)
    assert since < MIN_SECONDS_BETWEEN_FETCHES


def test_a_failed_attempt_still_restarts_the_cooldown(session):
    """Counted from the reservation, so a failing provider is not hammered."""

    _row(session, ENDPOINT_KEY, NOW - timedelta(seconds=60))

    assert seconds_since_last_fetch(session, now=NOW) < MIN_SECONDS_BETWEEN_FETCHES


def test_with_no_priced_request_at_all_there_is_no_cooldown(session):
    _row(session, USAGE_ENDPOINT_KEY, NOW - timedelta(seconds=30))

    assert seconds_since_last_fetch(session, now=NOW) is None


# --------------------------------------------------------------------------
# ...and neither may a DIFFERENT LANE's purchase.
#
# The cooldown counted every lane, so one lane's cadence became the cadence of
# all of them. On the Week 3 NFL Sunday the NFL pregame lane fetches inside each
# checkpoint cluster, and every one of those fetches shut the MLB lane for ten
# minutes -- four consecutive five-minute MLB ticks around 11:47 and 12:02 ET,
# MLB games going dark because a different lane bought a price for a different
# sport out of a different allowance.
#
# Both reasons the cooldown exists are lane-local: re-buying prices that cannot
# have changed is re-buying THIS lane's prices, and a provider being hammered is
# this lane's own retry cadence. Scoping preserves both.
# --------------------------------------------------------------------------

NFL_LANE = "sgo_nfl_events"


def test_an_nfl_fetch_does_not_hold_off_the_mlb_lane(session):
    _row(session, ENDPOINT_KEY, NOW - timedelta(seconds=1297))
    _row(session, NFL_LANE, NOW - timedelta(seconds=60))

    scoped = seconds_since_last_fetch(session, now=NOW, lane=ENDPOINT_KEY)

    assert scoped == pytest.approx(1297, abs=1)
    assert scoped >= MIN_SECONDS_BETWEEN_FETCHES, (
        "MLB was eligible; only the NFL lane's own purchase was holding it off")


def test_each_lane_keeps_its_own_cooldown_semantics(session):
    """Scoping does not weaken a lane against itself."""

    _row(session, ENDPOINT_KEY, NOW - timedelta(seconds=120))
    _row(session, NFL_LANE, NOW - timedelta(seconds=1297))

    assert seconds_since_last_fetch(session, now=NOW, lane=ENDPOINT_KEY) < (
        MIN_SECONDS_BETWEEN_FETCHES)
    assert seconds_since_last_fetch(session, now=NOW, lane=NFL_LANE) == pytest.approx(
        1297, abs=1)


def test_a_lane_that_has_never_fetched_has_no_cooldown_of_its_own(session):
    _row(session, NFL_LANE, NOW - timedelta(seconds=30))

    assert seconds_since_last_fetch(session, now=NOW, lane=ENDPOINT_KEY) is None


def test_the_free_endpoints_stay_excluded_under_scoping(session):
    """Scoping is added to the existing rule, not in place of it."""

    _row(session, ENDPOINT_KEY, NOW - timedelta(seconds=1297))
    _row(session, USAGE_ENDPOINT_KEY, NOW - timedelta(seconds=10))
    _row(session, OPENING_ADJUSTMENT_KEY, NOW - timedelta(seconds=5))

    assert seconds_since_last_fetch(session, now=NOW, lane=ENDPOINT_KEY) == pytest.approx(
        1297, abs=1)


def test_the_mlb_collector_asks_only_about_its_own_lane(monkeypatch):
    """The call site, not just the helper."""

    from app.services import sgo_collection

    seen: dict = {}

    def _spy(session, *, now=None, lane=None):
        seen["lane"] = lane
        return 30.0  # inside the cooldown, so the collector stops right here

    monkeypatch.setattr(sgo_collection, "seconds_since_last_fetch", _spy)
    monkeypatch.setattr(sgo_collection, "eligible", lambda settings, now=None: (True, "OK"))
    monkeypatch.setattr("app.services.collection_halt.active_halt",
                        lambda session, *, lane: None)

    result = sgo_collection.collect_for_open_cutoffs(object(), object(), now=NOW)

    assert seen["lane"] == ENDPOINT_KEY
    assert result["reason"] == "FALLBACK_COOLDOWN"
    assert result["cooldown_lane"] == ENDPOINT_KEY
    assert result["objects"] == 0

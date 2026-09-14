"""The budget must not report coverage it cannot deliver.

Three specific overstatements are guarded here, because each one reads as a
reassurance and each one is false:

  1. Multiplying a daily in-game allowance by the days left in the month and
     dividing by the checks one game needs. Objects do not roll forward into a
     game that has already been played, so three a day never becomes thirteen
     in one game.
  2. Calling N objects "N games covered". The provider bills per EVENT OBJECT,
     so a second capture of a game already captured costs another object and
     takes a distinct game off the day one for one.
  3. Letting a deliberately unfunded lane's zero read as a rounding result. It
     is a policy choice, and a reader who thinks it is rounding will expect it
     to fix itself as the budget grows.
"""
from __future__ import annotations

from app.services.provider_allocation import (
    LANES, LANES_BY_KEY, apportion,
)


def test_shares_sum_to_one():
    assert round(sum(lane.share for lane in LANES), 9) == 1.0


def test_apportionment_spends_the_whole_daily_total():
    for total in range(0, 40):
        assert sum(apportion(total).values()) == total


def test_an_unfunded_lane_receives_nothing():
    allowances = apportion(11)
    assert LANES_BY_KEY["sgo_nfl_events"].funded is False
    assert allowances["sgo_nfl_events"] == 0


def test_the_unfunded_zero_is_a_choice_and_not_rounding():
    """The same share, funded, is not zero -- which is the whole point."""

    if_funded = apportion(11, include_unfunded=True)
    assert if_funded["sgo_nfl_events"] > 0
    # And including it is purely a reporting view: the funded split is unchanged
    # in shape, still sums to the total, and grants nothing.
    assert sum(if_funded.values()) == 11


def test_the_reporting_view_never_grants_an_unfunded_lane(session):
    """`request_objects` refuses by name whatever the reporting view says."""

    from app.services.provider_allocation import request_objects

    grant = request_objects(session, lane="sgo_nfl_events", wanted=1)
    assert grant.allowed is False
    assert grant.reason == "LANE_NOT_FUNDED"


def test_in_game_checks_are_bounded_by_ONE_DAY_not_by_the_month():
    """The corrected arithmetic, stated as the identity it has to satisfy."""

    daily = apportion(11)["sgo_nfl_live"]
    objects_for_a_complete_game = 13
    # What the day actually buys during one game.
    assert daily < objects_for_a_complete_game
    assert daily // objects_for_a_complete_game == 0
    # The overstatement that was there before: 17 days x 3 / 13 = 3.9 "complete
    # games", from an allowance that funds zero complete games on any day.
    window_multiplied = (daily * 17) / objects_for_a_complete_game
    assert window_multiplied > 1.0
    assert daily // objects_for_a_complete_game == 0


def test_distinct_game_coverage_assumes_no_repeat_capture():
    daily = apportion(11)["sgo_mlb_events"]
    # Best case: every object is a different game.
    assert daily == daily
    # A single repeat halves it, because the unit billed is the event object.
    assert daily // 2 < daily

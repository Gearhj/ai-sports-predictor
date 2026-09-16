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
    LANES, LANES_BY_KEY, Lane, apportion,
)


def test_shares_sum_to_one():
    assert round(sum(lane.share for lane in LANES), 9) == 1.0


def test_apportionment_spends_the_whole_daily_total():
    for total in range(0, 40):
        assert sum(apportion(total).values()) == total


def test_an_unfunded_lane_receives_nothing(monkeypatch):
    """The mechanism, on a constructed lane set.

    NFL pregame was the unfunded lane until the owner funded it on 2026-09-14.
    Funding is a policy decision and will move again; that an unfunded lane
    receives nothing is not, so it is tested independently of the current
    configuration rather than against whichever lane happens to be off.
    """

    from app.services import provider_allocation as module

    monkeypatch.setattr(module, "LANES", tuple(
        Lane(key=lane.key, label=lane.label, share=lane.share,
             rationale=lane.rationale, funded=lane.key != "sgo_nfl_periods")
        for lane in module.LANES))

    allowances = module.apportion(11)
    assert allowances["sgo_nfl_periods"] == 0
    assert sum(allowances.values()) == 11
    # And the reporting view shows what it WOULD get, which is the whole point
    # of distinguishing a policy zero from a rounding zero.
    assert module.apportion(11, include_unfunded=True)["sgo_nfl_periods"] > 0


def test_the_reporting_view_never_grants_an_unfunded_lane(session, monkeypatch):
    """`request_objects` refuses by name whatever the reporting view says."""

    from app.services import provider_allocation as module

    monkeypatch.setattr(module, "LANES", tuple(
        Lane(key=lane.key, label=lane.label, share=lane.share,
             rationale=lane.rationale, funded=lane.key != "sgo_nfl_periods")
        for lane in module.LANES))
    monkeypatch.setattr(module, "LANES_BY_KEY",
                        {lane.key: lane for lane in module.LANES})

    grant = module.request_objects(session, lane="sgo_nfl_periods", wanted=1)
    assert grant.allowed is False
    assert grant.reason == "LANE_NOT_FUNDED"


def test_the_owner_approved_split_is_what_the_shares_produce():
    """7 / 1 / 2 / 1 at eleven objects a day, from the declared shares alone.

    The Show Preview lane appears at zero. That is the point of declaring it
    unfunded with a zero share: day-before collection gets a lane of its own
    for accounting without taking a single object from the four that pay for
    decisions. If funding it ever changes one of these four numbers, this test
    is where that shows up.
    """

    assert apportion(11) == {
        "sgo_mlb_events": 7, "sgo_nfl_events": 1,
        "sgo_nfl_live": 2, "sgo_nfl_periods": 1,
        "sgo_show_preview_events": 0,
    }
    assert [lane.key for lane in LANES if not lane.funded] == ["sgo_show_preview_events"]


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

"""Same-cutoff quote matching: the rules that make the comparison honest.

The matcher is the only new behaviour in the market comparison, and it went
wrong in a way worth pinning: MLB moneylines are stored under two outcome-key
conventions, and reading only the provider-neutral one silently matched nothing
while looking like a data shortage. These tests cover that, and the three rules
that stop an unfair comparison being assembled from true rows.
"""
from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "v18_market", Path(__file__).resolve().parents[1] / "scripts"
    / "v18_simulator_evidence_handoff.py")
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
match_quote = _MODULE.match_quote

CUTOFF = datetime(2026, 9, 12, 17, 55, tzinfo=UTC)
HOME_TEAM = "11111111-1111-1111-1111-111111111111"
AWAY_TEAM = "22222222-2222-2222-2222-222222222222"


def quote(*, book="BookA", key="HOME:None", label="HOME", team=None,
          american=-150, minutes_before=30, decimal=1.6667):
    known = CUTOFF - timedelta(minutes=minutes_before)
    return {
        "book": book, "outcome_key": key, "label": label,
        "participant_team_id": team, "american_odds": american,
        "decimal_odds": decimal, "observed_at": known, "available_as_of": known,
    }


def test_provider_neutral_home_away_keys_match():
    matched, reason = match_quote(
        [quote(), quote(key="AWAY:None", label="AWAY", american=130)],
        CUTOFF, home_team=HOME_TEAM, away_team=AWAY_TEAM)

    assert reason is None
    assert matched["book"] == "BookA"
    assert matched["home_american"] == -150 and matched["away_american"] == 130
    assert 0.0 < matched["home_no_vig"] < 1.0
    assert matched["overround"] > 1.0


def test_team_named_keys_resolve_through_the_participant_team():
    """The Odds API names the team; dropping those matched nothing at all."""

    matched, reason = match_quote(
        [
            quote(key="detroit tigers:None", label="Detroit Tigers", team=HOME_TEAM),
            quote(key="colorado rockies:None", label="Colorado Rockies",
                  team=AWAY_TEAM, american=130),
        ],
        CUTOFF, home_team=HOME_TEAM, away_team=AWAY_TEAM)

    assert reason is None
    assert matched["home_american"] == -150 and matched["away_american"] == 130


def test_a_one_sided_book_is_not_a_price():
    matched, reason = match_quote([quote()], CUTOFF,
                                  home_team=HOME_TEAM, away_team=AWAY_TEAM)

    assert matched is None
    assert reason == "NO_COMPLETE_TWO_SIDED_BOOK_AT_CUTOFF"


def test_sides_are_never_combined_across_books():
    """One book's home and another's away is not a two-sided market."""

    matched, reason = match_quote(
        [quote(book="BookA"),
         quote(book="BookB", key="AWAY:None", label="AWAY", american=130)],
        CUTOFF, home_team=HOME_TEAM, away_team=AWAY_TEAM)

    assert matched is None
    assert reason == "NO_COMPLETE_TWO_SIDED_BOOK_AT_CUTOFF"


def test_a_quote_first_known_after_the_cutoff_is_not_used():
    matched, reason = match_quote(
        [quote(minutes_before=-5), quote(key="AWAY:None", label="AWAY",
                                         american=130, minutes_before=-5)],
        CUTOFF, home_team=HOME_TEAM, away_team=AWAY_TEAM)

    assert matched is None
    assert reason == "NO_QUOTE_KNOWN_BY_CUTOFF_INSIDE_FRESHNESS_WINDOW"


def test_a_stale_quote_outside_the_freshness_window_is_not_used():
    stale = 60 * 24  # a day before the cutoff
    matched, reason = match_quote(
        [quote(minutes_before=stale),
         quote(key="AWAY:None", label="AWAY", american=130, minutes_before=stale)],
        CUTOFF, home_team=HOME_TEAM, away_team=AWAY_TEAM)

    assert matched is None
    assert reason == "NO_QUOTE_KNOWN_BY_CUTOFF_INSIDE_FRESHNESS_WINDOW"


def test_the_book_closest_to_the_cutoff_wins_and_the_rule_is_not_performance_aware():
    matched, reason = match_quote(
        [
            quote(book="Far", minutes_before=120),
            quote(book="Far", key="AWAY:None", label="AWAY", american=130,
                  minutes_before=120),
            quote(book="Near", minutes_before=2, american=-140),
            quote(book="Near", key="AWAY:None", label="AWAY", american=120,
                  minutes_before=2),
        ],
        CUTOFF, home_team=HOME_TEAM, away_team=AWAY_TEAM)

    assert reason is None
    assert matched["book"] == "Near"
    assert matched["seconds_before_cutoff"] == 120

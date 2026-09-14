"""An uncovered game is priced before one that already has a price.

WHAT HAPPENED
-------------
Ordering by decision cutoff alone re-buys whichever game is closest to locking,
every run. On 2026-09-14 that spent two objects refreshing CWS at CLE -- already
priced across nine books -- while SD at COL, SEA at LAA and MIA at AZ had no
price at all and the lane had seven objects spare.

WHY COVERAGE WINS
-----------------
Only an uncovered game can be helped by spending an object. A covered game is
merely made slightly fresher, and freshness it does not yet need is worth less
than a price that does not yet exist. Within each group the original rule is
untouched: earliest cutoff, ties on the provider's game id, fixed before any
result is seen so no forecast or edge can influence it.
"""
from __future__ import annotations

from app.services.sgo_collection import SELECTION_RULE


def order(events):
    """The production ordering, applied to a list of candidate events."""

    return sorted(
        events,
        key=lambda event: (
            bool(event.get("covered")),
            event["cutoff_utc"],
            str(event.get("game_id") or ""),
        ),
    )


def game(gid, cutoff, covered):
    return {"game_id": gid, "cutoff_utc": cutoff, "covered": covered}


def test_an_uncovered_later_game_outranks_a_covered_earlier_one():
    """The exact 2026-09-14 case."""

    events = [
        game("cws-cle", "2026-09-14T22:25:00+00:00", True),   # priced, locks first
        game("sd-col", "2026-09-15T00:25:00+00:00", False),   # unpriced, later
    ]
    assert [e["game_id"] for e in order(events)] == ["sd-col", "cws-cle"]


def test_within_uncovered_games_the_earliest_cutoff_still_goes_first():
    events = [
        game("mia-az", "2026-09-15T01:25:00+00:00", False),
        game("sd-col", "2026-09-15T00:25:00+00:00", False),
        game("sea-laa", "2026-09-15T01:23:00+00:00", False),
    ]
    assert [e["game_id"] for e in order(events)] == ["sd-col", "sea-laa", "mia-az"]


def test_within_covered_games_the_earliest_cutoff_still_goes_first():
    events = [
        game("sf-stl", "2026-09-14T23:30:00+00:00", True),
        game("lad-cin", "2026-09-14T22:25:00+00:00", True),
    ]
    assert [e["game_id"] for e in order(events)] == ["lad-cin", "sf-stl"]


def test_every_uncovered_game_precedes_every_covered_one():
    events = [
        game("a", "2026-09-14T22:00:00+00:00", True),
        game("b", "2026-09-15T02:00:00+00:00", False),
        game("c", "2026-09-14T23:00:00+00:00", True),
        game("d", "2026-09-15T01:00:00+00:00", False),
    ]
    ordered = [e["game_id"] for e in order(events)]
    covered_at = [i for i, gid in enumerate(ordered) if gid in ("a", "c")]
    uncovered_at = [i for i, gid in enumerate(ordered) if gid in ("b", "d")]
    assert max(uncovered_at) < min(covered_at)


def test_the_rule_is_deterministic_and_ties_break_on_game_id():
    same = "2026-09-14T22:25:00+00:00"
    events = [game("zzz", same, False), game("aaa", same, False)]
    assert [e["game_id"] for e in order(events)] == ["aaa", "zzz"]
    assert order(events) == order(list(reversed(events)))


def test_the_declared_rule_states_coverage_first():
    # The rule is published in receipts; it must say what the code does.
    assert "UNCOVERED_FIRST" in SELECTION_RULE
    assert "EARLIEST_DECISION_CUTOFF_FIRST" in SELECTION_RULE

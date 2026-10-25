"""NBA paper funding record-only, owner-switchable, default OFF (hotfix 2026-10-24, step 7).

``NBA_PAPER_FUNDING_RECORD_ONLY_EFFECTIVE_FROM`` unset (the shipped default) changes
nothing: an NBA QUALIFIED decision funds exactly as today. Set to a release UTC
clock, every NBA decision made at or after it keeps its value/quote evidence and
is recorded with outcome RECORD_ONLY (``OWNER_NBA_PAPER_FUNDING_RECORD_ONLY``) and
no position -- the same ``owner_funding_restrictions`` mechanism as the MLB 2026
record-only scope, independent of the signed-gap clock. Other sports are
untouched. Pure functions; no database, no provider call.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.services import owner_funding_restrictions as ofr

CLOCK = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
AFTER = CLOCK + timedelta(hours=6)
BEFORE = CLOCK - timedelta(minutes=1)


def _qualified(sport_edge=0.03):
    return {"outcome": "QUALIFIED", "reason": "ALL_GATES_PASSED",
            "selection": {"win_probability": 0.55, "american_odds": -110, "opposite_american": -110,
                          "sportsbook": "draftkings", "source": "THE_ODDS_API", "odds_snapshot_id": "snap-1",
                          "terms": {"stake": 4.5, "no_vig_probability": 0.5}}}


def test_the_setting_ships_off_and_the_shipped_default_funds_as_today(settings):
    assert settings.nba_paper_funding_record_only_effective_from is None
    decision = _qualified()
    assert ofr.apply_to_decision(decision, "NBA", AFTER, settings) is decision
    assert ofr.nba_record_only_active(moment=AFTER, settings=settings) is False


def test_with_the_clock_set_an_nba_qualified_decision_is_recorded_not_funded(settings):
    on = settings.model_copy(update={"nba_paper_funding_record_only_effective_from": CLOCK})
    result = ofr.apply_to_decision(_qualified(), "NBA", AFTER, on)
    assert (result["outcome"], result["reason"]) == ("RECORD_ONLY", ofr.NBA_REASON)
    receipt = result["funding_restriction"]
    assert receipt["rule"] == ofr.NBA_RULE and receipt["record_only"] is True
    assert receipt["effective_from_utc"] == CLOCK.isoformat()
    assert receipt["original_qualification"] == {"outcome": "QUALIFIED", "reason": "ALL_GATES_PASSED"}
    assert receipt["sportsbook"] == "draftkings" and receipt["snapshot_id"] == "snap-1"
    assert receipt["funding_checked_at_utc"] == AFTER.isoformat()
    # The value and quote evidence stay on the decision, byte for byte.
    assert result["selection"] == _qualified()["selection"]
    assert "retained decision and prices; no position" in result["detail"]


def test_before_the_clock_nothing_changes_and_the_clock_is_never_backdated(settings):
    on = settings.model_copy(update={"nba_paper_funding_record_only_effective_from": CLOCK})
    decision = _qualified()
    assert ofr.apply_to_decision(decision, "NBA", BEFORE, on) is decision
    assert ofr.nba_record_only_active(moment=BEFORE, settings=on) is False
    assert ofr.nba_record_only_active(moment=CLOCK, settings=on) is True             # inclusive at the instant


def test_only_qualified_nba_decisions_are_touched(settings):
    on = settings.model_copy(update={"nba_paper_funding_record_only_effective_from": CLOCK})
    declined = {**_qualified(), "outcome": "DECLINED", "reason": "NO_QUALIFYING_EDGE"}
    assert ofr.apply_to_decision(declined, "NBA", AFTER, on) is declined
    for sport in ("NFL", "CFB", "NHL", "MLB"):
        decision = _qualified()
        assert ofr.apply_to_decision(decision, sport, AFTER, on) is decision          # gap clock unset: untouched


def test_the_nba_clock_is_independent_of_the_signed_gap_clock(settings):
    both = settings.model_copy(update={"nba_paper_funding_record_only_effective_from": CLOCK,
                                       "owner_large_gap_record_only_effective_from": CLOCK})
    # A small gap would pass the gap gate; the NBA clock still records it.
    result = ofr.apply_to_decision(_qualified(), "NBA", AFTER, both)
    assert result["reason"] == ofr.NBA_REASON
    # With only the gap clock set, NBA goes through the gap gate exactly as before.
    gap_only = settings.model_copy(update={"owner_large_gap_record_only_effective_from": CLOCK})
    passed = ofr.apply_to_decision(_qualified(), "NBA", AFTER, gap_only)
    assert passed["outcome"] == "QUALIFIED" and passed["funding_restriction"]["reason"] == "GAP_NOT_ABOVE_10"


def test_a_naive_clock_is_refused_like_the_other_owner_clocks():
    with pytest.raises(ValueError, match="timezone"):
        ofr.nba_record_only_active(moment=AFTER, settings=SimpleNamespace(
            nba_paper_funding_record_only_effective_from="2026-10-24T12:00:00"))


def test_runtime_state_shows_the_clock_and_whether_it_is_active(settings):
    off = ofr.runtime_state(settings, now=AFTER)
    assert off["nba_paper_funding_record_only_effective_from_utc"] is None
    assert off["nba_paper_funding_record_only_active_at_observation"] is False
    on = ofr.runtime_state(settings.model_copy(update={"nba_paper_funding_record_only_effective_from": CLOCK}), now=AFTER)
    assert on["nba_paper_funding_record_only_effective_from_utc"] == CLOCK.isoformat()
    assert on["nba_paper_funding_record_only_active_at_observation"] is True


def test_the_env_line_parses_as_an_aware_clock(settings):
    parsed = Settings.model_validate({**settings.model_dump(),
                                      "nba_paper_funding_record_only_effective_from": "2026-10-24T12:00:00Z"})
    assert parsed.nba_paper_funding_record_only_effective_from == CLOCK

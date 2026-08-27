"""SportsDataIO MLB injuries adapter: parsing, mapping, change detection, trial
handling, key safety. Pure functions — no network, no key required.
"""

from __future__ import annotations

import json

from app.core.config import Settings
from app.prospective.contracts import AvailabilityEventType
from app.prospective.sportsdataio import (
    STATUS_ACTIVE,
    STATUS_NOT_CONFIGURED,
    NormalizedInjury,
    SportsDataIOAvailabilityAdapter,
    injuries_to_events,
    injury_event_type,
    is_trial,
    parse_injuries,
    plan_injury_poll,
)


def _row(**overrides):
    base = {
        "PlayerID": "100", "MLBAMID": "578428", "Team": "LAD", "FirstName": "Test",
        "LastName": "Player", "Position": "SP", "Status": "Active", "InjuryStatus": "",
        "InjuryBodyPart": "", "InjuryNotes": "", "InjuryStartDate": "",
    }
    base.update(overrides)
    return base


def _norm(pid, roster, injury, team="LAD"):
    return NormalizedInjury(pid, None, team, "N", "SP", roster, injury, None, None, None)


# --------------------------------------------------------------------------
# configuration + key safety
# --------------------------------------------------------------------------

def test_not_configured_without_key(settings):
    adapter = SportsDataIOAvailabilityAdapter(settings)
    assert adapter.is_configured() is False
    assert adapter.status() == STATUS_NOT_CONFIGURED


def test_configured_with_key_is_active(settings):
    # The feed is deferred (disabled) by default; it activates only when enabled.
    key_only = SportsDataIOAvailabilityAdapter(settings.model_copy(update={"sportsdataio_api_key": "k" * 20}))
    assert key_only.is_configured() is False
    assert key_only.status() == STATUS_NOT_CONFIGURED
    enabled = SportsDataIOAvailabilityAdapter(
        settings.model_copy(update={"sportsdataio_api_key": "k" * 20, "sportsdataio_enabled": True})
    )
    assert enabled.is_configured() is True
    assert enabled.status() == STATUS_ACTIVE


def test_key_never_appears_in_serialized_output(settings):
    secret = "SUPERSECRETKEY1234567890"
    configured = settings.model_copy(update={"sportsdataio_api_key": secret})
    rows = [_row(InjuryStatus="Out", Status="10-Day Injured List")]
    plan = plan_injury_poll(rows, {"100": ["Active", ""]})
    # Neither the plan/events nor the adapter status leak the key.
    assert secret not in json.dumps(plan.snapshot)
    assert all(secret not in json.dumps(e.payload) for e in plan.candidate_events)
    assert secret not in SportsDataIOAvailabilityAdapter(configured).status()


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------

def test_parse_injuries_normalizes_fields():
    parsed = parse_injuries([_row(Name="", FirstName="Mookie", LastName="Betts",
                                  Status="10-Day Injured List", InjuryStatus="Out", InjuryBodyPart="Wrist")])
    assert len(parsed) == 1
    injury = parsed[0]
    assert injury.player_source_id == "100"
    assert injury.mlbam_id == "578428"
    assert injury.team == "LAD"
    assert injury.player_name == "Mookie Betts"      # falls back to First+Last when Name blank
    assert injury.roster_status == "10-Day Injured List"
    assert injury.injury_status == "Out"
    assert injury.body_part == "Wrist"


def test_parse_skips_rows_without_player_id():
    assert parse_injuries([{"Team": "LAD", "Status": "Active"}]) == []


# --------------------------------------------------------------------------
# trial detection
# --------------------------------------------------------------------------

def test_trial_detection_flags_scrambled_data():
    scrambled = [_row(PlayerID=str(i), InjuryStatus="Scrambled", InjuryBodyPart="Scrambled") for i in range(6)]
    assert is_trial(scrambled) is True
    # Production rows are not flagged as trial.
    assert is_trial([_row(InjuryStatus="Out"), _row(PlayerID="2", InjuryStatus="Day-To-Day")]) is False


def test_trial_data_produces_no_events():
    scrambled = [_row(PlayerID=str(i), InjuryStatus="Scrambled") for i in range(4)]
    plan = plan_injury_poll(scrambled, None)
    assert plan.trial is True
    assert plan.candidate_events == []  # never fabricates from scrambled data


# --------------------------------------------------------------------------
# event mapping
# --------------------------------------------------------------------------

def test_event_mapping_covers_transitions():
    assert injury_event_type(_norm("1", "Active", ""), _norm("1", "10-Day Injured List", "")) == AvailabilityEventType.INJURED_LIST_PLACED
    assert injury_event_type(_norm("1", "10-Day Injured List", "Out"), _norm("1", "Active", "")) == AvailabilityEventType.ACTIVATED
    assert injury_event_type(_norm("1", "Active", ""), _norm("1", "Active", "Out")) == AvailabilityEventType.CONFIRMED_OUT
    assert injury_event_type(_norm("1", "Active", ""), _norm("1", "Active", "Day-To-Day")) == AvailabilityEventType.DAY_TO_DAY
    assert injury_event_type(_norm("1", "Active", "Out"), _norm("1", "Active", "Questionable")) == AvailabilityEventType.INJURY_STATUS_CHANGED


def test_no_change_yields_no_event():
    same = _norm("1", "Active", "Day-To-Day")
    assert injury_event_type(same, _norm("1", "Active", "Day-To-Day")) is None


# --------------------------------------------------------------------------
# stateful change detection (baseline-first + dedup)
# --------------------------------------------------------------------------

def test_first_poll_is_baseline_then_changes_emit():
    rows = [_row(InjuryStatus="", Status="Active")]
    baseline = plan_injury_poll(rows, None)
    assert baseline.candidate_events == []            # first sighting only baselines

    changed = plan_injury_poll([_row(InjuryStatus="Out", Status="10-Day Injured List")], baseline.snapshot)
    assert [e.event_type for e in changed.candidate_events] == [AvailabilityEventType.INJURED_LIST_PLACED]

    repeated = plan_injury_poll([_row(InjuryStatus="Out", Status="10-Day Injured List")], changed.snapshot)
    assert repeated.candidate_events == []            # identical response → no new events


def test_injuries_to_events_sets_provider_and_identity():
    events = injuries_to_events(
        [_norm("100", "Active", "Out", team="NYY")],
        {"100": _norm("100", "Active", "")},
    )
    assert len(events) == 1
    assert events[0].event_type == AvailabilityEventType.CONFIRMED_OUT
    assert events[0].team_source_id == "NYY"
    assert events[0].player_source_id == "100"
    assert events[0].payload["provider"] == "SPORTSDATAIO"

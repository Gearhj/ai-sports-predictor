"""SportsDataIO availability adapter: disabled-by-default, pure mapping, no key.

The adapter never fabricates availability and never runs without credentials.
The transaction→event mapping is a pure function tested with no network or key.
"""

from __future__ import annotations

import dataclasses

from app.core.config import Settings
from app.prospective.contracts import AvailabilityEventType
from app.prospective.sportsdataio import (
    STATUS_LIVE,
    STATUS_NOT_CONFIGURED,
    SportsDataIOAvailabilityAdapter,
    map_transaction,
)


def _settings(base: Settings, **overrides) -> Settings:
    return base.model_copy(update=overrides)


def test_disabled_without_credentials_reports_not_configured(settings):
    adapter = SportsDataIOAvailabilityAdapter(settings)
    assert adapter.is_configured() is False
    assert adapter.status() == STATUS_NOT_CONFIGURED
    # No credentials → no events, no provider call, nothing fabricated.
    assert adapter.collect() == []


def test_enabled_without_key_still_not_configured(settings):
    adapter = SportsDataIOAvailabilityAdapter(_settings(settings, sportsdataio_enabled=True))
    assert adapter.status() == STATUS_NOT_CONFIGURED
    assert adapter.collect() == []


def test_enabled_with_key_reports_live(settings):
    adapter = SportsDataIOAvailabilityAdapter(
        _settings(settings, sportsdataio_enabled=True, sportsdataio_api_key="x" * 12)
    )
    assert adapter.is_configured() is True
    assert adapter.status() == STATUS_LIVE


def test_transaction_mapping_covers_supported_types():
    assert map_transaction("Placed on Injured List") == AvailabilityEventType.INJURED_LIST_PLACED
    assert map_transaction("15-Day Injured List") == AvailabilityEventType.INJURED_LIST_PLACED
    assert map_transaction("Activated") == AvailabilityEventType.ACTIVATED
    assert map_transaction("Day-To-Day") == AvailabilityEventType.DAY_TO_DAY
    assert map_transaction("Confirmed Out") == AvailabilityEventType.CONFIRMED_OUT
    assert map_transaction("Late Scratch") == AvailabilityEventType.LATE_SCRATCH
    assert map_transaction("Rehab Assignment") == AvailabilityEventType.REHAB_ASSIGNMENT
    assert map_transaction("Return From Rehab") == AvailabilityEventType.RETURN_FROM_REHAB
    assert map_transaction("Recalled") == AvailabilityEventType.ROSTER_RECALL
    assert map_transaction("Optioned") == AvailabilityEventType.ROSTER_OPTION


def test_unknown_transaction_is_ignored():
    assert map_transaction("Traded") is None
    assert map_transaction("") is None
    assert map_transaction(None) is None


def test_map_rows_builds_manual_style_events_and_skips_unmapped(settings):
    adapter = SportsDataIOAvailabilityAdapter(settings)
    events = adapter.map_rows([
        {"Category": "Placed on Injured List", "Name": "Star Pitcher", "PlayerID": "42", "Team": "NYY"},
        {"Category": "Traded", "Name": "Someone"},  # unmapped → skipped
        {"Category": "Late Scratch", "Name": "Bench Bat", "PlayerID": "7"},
    ])
    assert [event.event_type for event in events] == [
        AvailabilityEventType.INJURED_LIST_PLACED,
        AvailabilityEventType.LATE_SCRATCH,
    ]
    assert events[0].player_name == "Star Pitcher"
    assert events[0].player_source_id == "42"
    assert events[0].payload["provider"] == "SPORTSDATAIO"

"""sgo_exact_event_selection_mode: OFF / VALIDATE / ON, with every local check.

Provider behaviour modelled from the retained documentation (FAQ and OpenAPI,
2026-09-19T18:26:08Z; see sgo_collection.EVENT_ID_FILTER_PARAM): with eventIDs
the provider returns those events and ignores every other filter. Synthetic
event IDs only; the ledger, budget, cooldown and halt are the real ones.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.services import sgo_collection
from tests.test_sgo_exact_event_selection import (
    AT_1840, CHC_CIN, EVENT, EVENT_OF, KC_PIT, LATER, REFRESH_AT, Provider, _active_halt, _bind, _events,
    _install, _ledger_requests,
)

BOTH = [(KC_PIT, EVENT[KC_PIT], "SPORTSGAMEODDS"), (CHC_CIN, EVENT[CHC_CIN], "SPORTSGAMEODDS")]
DF9E390_FILTERS_LIMIT_2 = {
    "leagueID": "MLB", "limit": "2", "startsAfter": "2026-09-18T22:39:59Z",
    "startsBefore": "2026-09-18T23:42:37Z", "finalized": "false", "oddsPresent": "true"}


def _mode(settings, mode):
    return settings.model_copy(update={"sgo_exact_event_selection_mode": mode})


def _terminal(session):
    from app.models import ProviderRequestLedger

    return session.query(ProviderRequestLedger).filter(
        ProviderRequestLedger.endpoint_key == sgo_collection.ENDPOINT_KEY,
        ProviderRequestLedger.status == "SUCCEEDED").all()


def _seed_verified_row(session):
    """A prior exact-ID purchase that came back matching (the live verification)."""
    from app.data_sources.provider_registry import SPORTSGAMEODDS
    from app.models import ProviderRequestLedger

    at = REFRESH_AT - timedelta(hours=1)
    reserved = ProviderRequestLedger(
        provider=SPORTSGAMEODDS, endpoint_key=sgo_collection.ENDPOINT_KEY, status="RESERVED",
        requested_at=at, request_fingerprint="fp-verified", controlled_live_request=True,
        source_mode="LIVE", response_metadata={})
    session.add(reserved)
    session.flush()
    session.add(ProviderRequestLedger(
        provider=SPORTSGAMEODDS, endpoint_key=sgo_collection.ENDPOINT_KEY, status="SUCCEEDED",
        requested_at=at, completed_at=at, reservation_id=reserved.id, request_fingerprint="fp-verified",
        records_received=1, controlled_live_request=True, source_mode="LIVE",
        response_metadata={"exact_event_selection": {"mode": "VALIDATE", "requested_ids": ["X"],
                                                     "returned_ids": ["X"], "match": True}}))
    session.commit()


# -- VALIDATE ------------------------------------------------------------------

def test_validate_uses_ids_on_an_unambiguous_all_verified_selection(session, settings, monkeypatch):
    _bind(session, BOTH)
    provider = Provider(honours_ids=True)
    _install(monkeypatch, events=_events({KC_PIT: True, CHC_CIN: True}), provider=provider, granted=2)
    result = sgo_collection.collect_for_open_cutoffs(session, _mode(settings, "VALIDATE"), now=REFRESH_AT)

    assert result["request_filters"] == {**DF9E390_FILTERS_LIMIT_2,
                                         "eventIDs": f"{EVENT[KC_PIT]},{EVENT[CHC_CIN]}"}
    assert result["objects"] == 2 and result["lane_halted"] is False
    record = result["exact_event_selection"]
    assert record["match"] is True and record["basis"] == "VALIDATE_UNAMBIGUOUS_SELECTION"
    assert record["requested_ids"] == record["returned_ids"] == sorted(EVENT.values())
    assert record["raw_archive_sha256"] == "ab" * 32
    assert record["window_alone_would_select_the_same"] is True
    assert _terminal(session)[0].response_metadata["exact_event_selection"] == record
    assert sgo_collection.exact_selection_verified(session) is True


def test_validate_uses_no_ids_while_any_selected_game_is_unverified(session, settings, monkeypatch):
    _bind(session, [(KC_PIT, EVENT[KC_PIT], "SPORTSGAMEODDS")])
    provider = Provider(honours_ids=True)
    _install(monkeypatch, events=_events({KC_PIT: True, CHC_CIN: True}), provider=provider, granted=2)
    result = sgo_collection.collect_for_open_cutoffs(session, _mode(settings, "VALIDATE"), now=REFRESH_AT)
    assert result["request_filters"] == DF9E390_FILTERS_LIMIT_2          # the window request
    assert "exact_event_selection" not in result and provider.calls[0]["event_ids"] is None


def test_an_ambiguous_selection_defers_before_verification(session, settings, monkeypatch):
    _bind(session, BOTH)
    provider = Provider(honours_ids=True)
    _install(monkeypatch, events=_events({KC_PIT: True, CHC_CIN: True}), provider=provider, granted=1)
    assert sgo_collection.exact_selection_verified(session) is False
    result = sgo_collection.collect_for_open_cutoffs(session, _mode(settings, "VALIDATE"), now=REFRESH_AT)
    assert result["reason"] == "DEFERRED_AMBIGUOUS_SELECTION" and provider.calls == []
    assert _ledger_requests(session) == []


def test_an_ambiguous_selection_uses_ids_once_a_live_match_is_verified(session, settings, monkeypatch):
    _bind(session, BOTH)
    _seed_verified_row(session)
    provider = Provider(honours_ids=True)
    _install(monkeypatch, events=_events({KC_PIT: True, CHC_CIN: True}), provider=provider, granted=1)
    result = sgo_collection.collect_for_open_cutoffs(session, _mode(settings, "VALIDATE"), now=REFRESH_AT)
    assert result["request_filters"]["eventIDs"] == EVENT[KC_PIT]
    assert result["request_filters"]["limit"] == "1" and result["objects"] == 1
    assert result["games_ingested"] == [KC_PIT] and result["lane_halted"] is False
    assert result["exact_event_selection"]["basis"] == "VALIDATE_LIVE_MATCH_VERIFIED"
    assert result["exact_event_selection"]["window_alone_would_select_the_same"] is False


def test_on_uses_ids_for_an_ambiguous_all_verified_selection(session, settings, monkeypatch):
    _bind(session, BOTH)
    provider = Provider(honours_ids=True)
    _install(monkeypatch, events=_events({KC_PIT: True, CHC_CIN: True}), provider=provider, granted=1)
    result = sgo_collection.collect_for_open_cutoffs(session, _mode(settings, "ON"), now=REFRESH_AT)
    assert result["request_filters"]["eventIDs"] == EVENT[KC_PIT] and result["games_ingested"] == [KC_PIT]
    assert result["exact_event_selection"]["basis"] == "MODE_ON"


# -- local checks before sending ---------------------------------------------------

@pytest.mark.parametrize("case,reason", [
    ("started", "GAME_NOT_PREGAME_LIVE"),
    ("postponed", "GAME_POSTPONED"),
    ("moved", "START_MOVED_SINCE_ID_BOUND"),
])
def test_a_game_that_fails_a_pre_send_check_is_not_requested_by_id(
        session, settings, monkeypatch, case, reason):
    from app.models import GameStatus

    kwargs = {"started": {"status": {KC_PIT: GameStatus.LIVE}},
              "postponed": {"postponed": {KC_PIT}},
              "moved": {"scheduled_at_binding": {KC_PIT: AT_1840 - timedelta(hours=2)}}}[case]
    _bind(session, BOTH, **kwargs)
    provider = Provider(honours_ids=True)
    _install(monkeypatch, events=_events({KC_PIT: True, CHC_CIN: True}), provider=provider, granted=2)
    result = sgo_collection.collect_for_open_cutoffs(session, _mode(settings, "ON"), now=REFRESH_AT)
    assert "eventIDs" not in result["request_filters"]
    assert provider.calls[0]["event_ids"] is None
    assert {"game_id": KC_PIT, "event_id": EVENT[KC_PIT], "reason": reason} in result["exact_id_blockers"]


# -- local checks after the response -----------------------------------------------

def test_an_extra_event_is_rejected_and_halts(session, settings, monkeypatch):
    _bind(session, BOTH)
    extra = LATER[0][0]
    provider = Provider(honours_ids=True, extra=[extra])
    _install(monkeypatch, events=_events({KC_PIT: True, CHC_CIN: True}), provider=provider, granted=2)
    result = sgo_collection.collect_for_open_cutoffs(session, _mode(settings, "VALIDATE"), now=REFRESH_AT)
    assert extra not in result["games_ingested"]
    assert result["selection_mismatch"]["unrequested_event_ids_returned"] == [EVENT_OF[extra]]
    assert result["lane_halted"] is True and _active_halt(session) is not None
    assert "exact_event_selection" not in result
    assert sgo_collection.exact_selection_verified(session) is False


def test_a_missing_event_halts(session, settings, monkeypatch):
    _bind(session, BOTH)
    provider = Provider(honours_ids=True, drop=[CHC_CIN])
    _install(monkeypatch, events=_events({KC_PIT: True, CHC_CIN: True}), provider=provider, granted=2)
    result = sgo_collection.collect_for_open_cutoffs(session, _mode(settings, "VALIDATE"), now=REFRESH_AT)
    assert result["selection_mismatch"]["requested_event_ids_missing"] == [EVENT[CHC_CIN]]
    assert result["lane_halted"] is True
    assert "exact_event_selection" not in result


@pytest.mark.parametrize("provider_kwargs,reason", [
    ({"status": {KC_PIT: {"finalized": True}}}, "EVENT_STATUS_FINALIZED"),
    ({"status": {KC_PIT: {"started": True}}}, "EVENT_STATUS_STARTED"),
    ({"status": {KC_PIT: {"cancelled": True}}}, "EVENT_STATUS_CANCELLED"),
    ({"starts": {KC_PIT: AT_1840 + timedelta(hours=7)}}, "EVENT_IDENTITY_SCHEDULED_TIME_MISMATCH"),
], ids=["finalized", "started", "cancelled", "start-outside-binder-tolerance"])
def test_an_event_failing_a_local_check_is_not_ingested(session, settings, monkeypatch, provider_kwargs, reason):
    _bind(session, BOTH)
    provider = Provider(honours_ids=True, **provider_kwargs)
    _install(monkeypatch, events=_events({KC_PIT: True, CHC_CIN: True}), provider=provider, granted=2)
    result = sgo_collection.collect_for_open_cutoffs(session, _mode(settings, "VALIDATE"), now=REFRESH_AT)
    assert result["games_ingested"] == [CHC_CIN]                 # KC @ PIT dropped, CHC @ CIN kept
    rejected = result["exact_event_selection_rejected"]
    assert rejected["failures"] == {EVENT[KC_PIT]: reason} and rejected["match"] is False
    assert "exact_event_selection" not in result                 # verification only on an exact match
    assert result["lane_halted"] is False                        # the sets matched: not a mismatch
    assert sgo_collection.exact_selection_verified(session) is False
    assert result["objects"] == 2                                # both objects were billed


# -- OFF is df9e390, and object counts are unchanged ----------------------------------

def test_off_is_byte_identical_to_the_halt_repair(session, settings, monkeypatch):
    _bind(session, BOTH)                                         # verified IDs exist, but OFF
    provider = Provider(honours_ids=True)
    _install(monkeypatch, events=_events({KC_PIT: True, CHC_CIN: True}), provider=provider, granted=2)
    assert settings.sgo_exact_event_selection_mode == "OFF"
    result = sgo_collection.collect_for_open_cutoffs(session, settings, now=REFRESH_AT)
    assert result["request_filters"] == DF9E390_FILTERS_LIMIT_2
    assert not [key for key in result if key.startswith("exact_")]
    assert _terminal(session)[0].response_metadata.keys() >= {"raw_archive"}
    assert not [key for key in _terminal(session)[0].response_metadata if key.startswith("exact_")]


@pytest.mark.parametrize("mode", ["VALIDATE", "ON"])
def test_an_id_request_costs_what_the_window_request_costs(session, settings, monkeypatch, mode):
    _bind(session, BOTH)
    provider = Provider(honours_ids=True)
    _install(monkeypatch, events=_events({KC_PIT: True, CHC_CIN: True}), provider=provider, granted=2)
    result = sgo_collection.collect_for_open_cutoffs(session, _mode(settings, mode), now=REFRESH_AT)
    reserved = _ledger_requests(session)
    assert len(reserved) == 1 and reserved[0].quota_cost == 2      # same reservation
    assert result["request_filters"]["limit"] == "2" and result["objects"] == 2
    assert len(provider.calls) == 1                                # one request, never a second


def test_the_mode_setting_is_off_by_default_and_normalised():
    from pydantic import ValidationError

    from app.core.config import Settings

    base = dict(app_env="test", database_url="sqlite://",
                secret_key="test-secret-with-at-least-thirty-two-characters")
    assert Settings.model_fields["sgo_exact_event_selection_mode"].default == "OFF"
    assert Settings(**base, sgo_exact_event_selection_mode="validate").sgo_exact_event_selection_mode == "VALIDATE"
    assert Settings(**base, sgo_exact_event_selection_mode="").sgo_exact_event_selection_mode == "OFF"
    with pytest.raises(ValidationError):
        Settings(**base, sgo_exact_event_selection_mode="MAYBE")
    assert "sgo_exact_event_selection_enabled" not in Settings.model_fields

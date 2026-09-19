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


def _seed_verified_row(session, *, test_kind: str | None = None, passed: bool | None = None):
    """A prior exact-ID purchase on the ledger. Without ``test_kind`` it is a plain
    (non-discriminating) match; with DISCRIMINATING_TEST it is the one test."""
    from app.data_sources.provider_registry import SPORTSGAMEODDS
    from app.models import ProviderRequestLedger

    at = REFRESH_AT - timedelta(hours=1)
    reserved = ProviderRequestLedger(
        provider=SPORTSGAMEODDS, endpoint_key=sgo_collection.ENDPOINT_KEY, status="RESERVED",
        requested_at=at, request_fingerprint="fp-verified", controlled_live_request=True,
        source_mode="LIVE", response_metadata={})
    session.add(reserved)
    session.flush()
    record = {"mode": "VALIDATE", "requested_ids": ["X"], "returned_ids": ["X"], "match": True}
    metadata: dict = {"exact_event_selection": record}
    if test_kind:
        record.update({"test_kind": test_kind, "discriminating": True, "passed": passed,
                       "match": bool(passed)})
        if passed:
            metadata["exact_event_selection_transition"] = {
                "from": "VALIDATE", "to": "ON_EQUIVALENT", "at": at.isoformat(),
                "basis_ledger_id": str(reserved.id), "basis_request_fingerprint": "fp-verified",
                "raw_archive_sha": "cd" * 32}
    session.add(ProviderRequestLedger(
        provider=SPORTSGAMEODDS, endpoint_key=sgo_collection.ENDPOINT_KEY, status="SUCCEEDED",
        requested_at=at, completed_at=at, reservation_id=reserved.id, request_fingerprint="fp-verified",
        records_received=1, controlled_live_request=True, source_mode="LIVE",
        response_metadata=metadata))
    session.commit()


def _transitions(session) -> int:
    return sum(1 for row in _terminal(session)
               if (row.response_metadata or {}).get("exact_event_selection_transition"))


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
    assert record["window_alone_would_select_the_same"] is True   # not proof the IDs were applied
    reserved = _ledger_requests(session)[0]
    terminal = _terminal(session)[0]
    assert record["reservation_ledger_id"] == str(reserved.id)
    assert record["request_fingerprint"] == reserved.request_fingerprint
    assert record["terminal_ledger_id"] == str(terminal.id)
    stored = terminal.response_metadata["exact_event_selection"]
    assert stored == {k: v for k, v in record.items() if k != "terminal_ledger_id"}
    # An unambiguous-window match is not the discriminating test and transitions nothing.
    assert record["test_kind"] == "UNAMBIGUOUS_WINDOW_MATCH" and record["discriminating"] is False
    assert record["passed"] is None and "exact_event_selection_transition" not in result
    assert sgo_collection.discriminating_test_state(session)["attempted"] is False
    assert _transitions(session) == 0


def test_validate_uses_no_ids_while_any_selected_game_is_unverified(session, settings, monkeypatch):
    _bind(session, [(KC_PIT, EVENT[KC_PIT], "SPORTSGAMEODDS")])
    provider = Provider(honours_ids=True)
    _install(monkeypatch, events=_events({KC_PIT: True, CHC_CIN: True}), provider=provider, granted=2)
    result = sgo_collection.collect_for_open_cutoffs(session, _mode(settings, "VALIDATE"), now=REFRESH_AT)
    assert result["request_filters"] == DF9E390_FILTERS_LIMIT_2          # the window request
    assert "exact_event_selection" not in result and provider.calls[0]["event_ids"] is None


def test_the_first_ambiguous_all_verified_selection_is_the_discriminating_test(session, settings, monkeypatch):
    """KC @ PIT alone out of the tied 18:40 pair -- the 09-18 shape -- sent by ID once."""

    _bind(session, BOTH)
    provider = Provider(honours_ids=True)
    _install(monkeypatch, events=_events({KC_PIT: True, CHC_CIN: True}), provider=provider, granted=1)
    assert sgo_collection.discriminating_test_state(session)["attempted"] is False
    result = sgo_collection.collect_for_open_cutoffs(session, _mode(settings, "VALIDATE"), now=REFRESH_AT)

    assert result["request_filters"]["eventIDs"] == EVENT[KC_PIT]
    assert result["request_filters"]["limit"] == "1" and result["objects"] == 1      # the same purchase
    assert result["games_ingested"] == [KC_PIT] and result["lane_halted"] is False
    record = result["exact_event_selection"]
    assert record["test_kind"] == sgo_collection.DISCRIMINATING_TEST and record["discriminating"] is True
    assert record["window_alone_would_select_the_same"] is False
    assert record["passed"] is True and record["match"] is True and record["reasons"] == []
    transition = result["exact_event_selection_transition"]
    assert transition["from"] == "VALIDATE" and transition["to"] == "ON_EQUIVALENT"
    assert transition["basis_ledger_id"] == record["reservation_ledger_id"]
    assert transition["basis_request_fingerprint"] == record["request_fingerprint"]
    assert transition["raw_archive_sha"] == "ab" * 32
    assert _terminal(session)[0].response_metadata["exact_event_selection_transition"] == transition
    state = sgo_collection.discriminating_test_state(session)
    assert state["attempted"] is True and state["passed"] is True
    assert len(provider.calls) == 1                                                  # no retry


def test_after_a_failed_test_an_ambiguous_selection_defers(session, settings, monkeypatch):
    _bind(session, BOTH)
    _seed_verified_row(session, test_kind=sgo_collection.DISCRIMINATING_TEST, passed=False)
    provider = Provider(honours_ids=True)
    _install(monkeypatch, events=_events({KC_PIT: True, CHC_CIN: True}), provider=provider, granted=1)
    result = sgo_collection.collect_for_open_cutoffs(session, _mode(settings, "VALIDATE"), now=REFRESH_AT)
    assert result["reason"] == "DEFERRED_AMBIGUOUS_SELECTION" and provider.calls == []
    assert result["discriminating_test_state"]["attempted"] is True
    assert result["discriminating_test_state"]["passed"] is False
    assert [r.request_fingerprint for r in _ledger_requests(session)] == ["fp-verified"]  # nothing new


def test_a_plain_match_on_the_ledger_is_not_the_test(session, settings, monkeypatch):
    """A non-discriminating match neither transitions nor uses the test up."""

    _bind(session, BOTH)
    _seed_verified_row(session)                                    # plain, no test_kind
    state = sgo_collection.discriminating_test_state(session)
    assert state["attempted"] is False and state["passed"] is False


@pytest.mark.parametrize("granted,label", [(1, "ambiguous"), (2, "unambiguous")])
def test_after_a_passed_test_validate_behaves_as_on(session, settings, monkeypatch, granted, label):
    _bind(session, BOTH)
    _seed_verified_row(session, test_kind=sgo_collection.DISCRIMINATING_TEST, passed=True)
    provider = Provider(honours_ids=True)
    _install(monkeypatch, events=_events({KC_PIT: True, CHC_CIN: True}), provider=provider,
             granted=granted)
    result = sgo_collection.collect_for_open_cutoffs(session, _mode(settings, "VALIDATE"), now=REFRESH_AT)
    assert "eventIDs" in result["request_filters"]
    assert result["request_filters"]["limit"] == str(granted) and result["objects"] == granted
    assert result["exact_event_selection"]["basis"] == "VALIDATE_ON_EQUIVALENT"
    assert result["exact_event_selection"]["test_kind"] == "ON_EQUIVALENT"
    assert "exact_event_selection_transition" not in result         # recorded once, on the test
    assert _transitions(session) == 1


def test_an_ambiguous_selection_with_an_unverified_id_defers_and_keeps_the_test(
        session, settings, monkeypatch):
    _bind(session, [(CHC_CIN, EVENT[CHC_CIN], "SPORTSGAMEODDS")])   # KC @ PIT has no verified ID
    provider = Provider(honours_ids=True)
    _install(monkeypatch, events=_events({KC_PIT: True, CHC_CIN: True}), provider=provider, granted=1)
    result = sgo_collection.collect_for_open_cutoffs(session, _mode(settings, "VALIDATE"), now=REFRESH_AT)
    assert result["reason"] == "DEFERRED_AMBIGUOUS_SELECTION" and provider.calls == []
    assert sgo_collection.discriminating_test_state(session)["attempted"] is False


def test_a_mismatch_on_the_test_rejects_halts_and_uses_the_test(session, settings, monkeypatch):
    """If the provider ignored the IDs, the 09-18 answer comes back: rejected, halted."""

    _bind(session, BOTH)
    provider = Provider(honours_ids=False)                          # the filter is ignored
    _install(monkeypatch, events=_events({KC_PIT: True, CHC_CIN: True}), provider=provider, granted=1)
    result = sgo_collection.collect_for_open_cutoffs(session, _mode(settings, "VALIDATE"), now=REFRESH_AT)
    assert result["request_filters"]["eventIDs"] == EVENT[KC_PIT]
    assert result["games_ingested"] == []                           # CHC @ CIN rejected
    assert result["lane_halted"] is True and _active_halt(session) is not None
    record = result["exact_event_selection"]
    assert record["test_kind"] == sgo_collection.DISCRIMINATING_TEST and record["passed"] is False
    assert set(record["reasons"]) >= {"EXTRA_EVENT_IDS", "MISSING_EVENT_IDS", "MISMATCH_HALT"}
    assert "exact_event_selection_transition" not in result and _transitions(session) == 0
    state = sgo_collection.discriminating_test_state(session)
    assert state["attempted"] is True and state["passed"] is False  # used; VALIDATE stays deferral-only
    assert result["objects"] == 1 and result["request_filters"]["limit"] == "1"


def test_a_test_without_an_archived_response_does_not_pass(session, settings, monkeypatch):
    _bind(session, BOTH)
    provider = Provider(honours_ids=True)
    _install(monkeypatch, events=_events({KC_PIT: True, CHC_CIN: True}), provider=provider, granted=1)
    real_fetch = sgo_collection.fetch_pregame_moneylines

    def _no_archive(*args, **kwargs):
        fetch = real_fetch(*args, **kwargs)
        fetch.raw_archive = {"status": "FAILED"}
        return fetch

    monkeypatch.setattr(sgo_collection, "fetch_pregame_moneylines", _no_archive)
    result = sgo_collection.collect_for_open_cutoffs(session, _mode(settings, "VALIDATE"), now=REFRESH_AT)
    record = result["exact_event_selection"]
    assert record["match"] is True and record["passed"] is False
    assert record["reasons"] == ["RAW_ARCHIVE_MISSING"]
    assert "exact_event_selection_transition" not in result
    state = sgo_collection.discriminating_test_state(session)
    assert state["attempted"] is True and state["passed"] is False


def test_on_uses_ids_for_an_ambiguous_all_verified_selection(session, settings, monkeypatch):
    _bind(session, BOTH)
    provider = Provider(honours_ids=True)
    _install(monkeypatch, events=_events({KC_PIT: True, CHC_CIN: True}), provider=provider, granted=1)
    result = sgo_collection.collect_for_open_cutoffs(session, _mode(settings, "ON"), now=REFRESH_AT)
    assert result["request_filters"]["eventIDs"] == EVENT[KC_PIT]
    assert result["request_filters"]["limit"] == "1" and result["objects"] == 1
    assert result["games_ingested"] == [KC_PIT] and result["lane_halted"] is False
    record = result["exact_event_selection"]
    assert record["basis"] == "MODE_ON" and record["match"] is True
    assert record["window_alone_would_select_the_same"] is False    # a discriminating match


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
    record = result["exact_event_selection"]                        # evidence kept on every ID buy
    assert record["match"] is False and record["extra_ids"] == [EVENT_OF[extra]]
    assert _terminal(session)[0].response_metadata["exact_event_selection"]["match"] is False


def test_a_missing_event_halts(session, settings, monkeypatch):
    _bind(session, BOTH)
    provider = Provider(honours_ids=True, drop=[CHC_CIN])
    _install(monkeypatch, events=_events({KC_PIT: True, CHC_CIN: True}), provider=provider, granted=2)
    result = sgo_collection.collect_for_open_cutoffs(session, _mode(settings, "VALIDATE"), now=REFRESH_AT)
    assert result["selection_mismatch"]["requested_event_ids_missing"] == [EVENT[CHC_CIN]]
    assert result["lane_halted"] is True
    record = result["exact_event_selection"]
    assert record["match"] is False and record["missing_ids"] == [EVENT[CHC_CIN]]


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
    record = result["exact_event_selection"]
    assert record["failures"] == {EVENT[KC_PIT]: reason}
    assert record["match"] is False                              # a failed check is never a match
    assert result["lane_halted"] is False                        # the sets matched: not a mismatch
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

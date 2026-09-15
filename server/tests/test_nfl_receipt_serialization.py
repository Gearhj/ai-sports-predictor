"""The NFL checkpoint receipt must survive json.dumps, nested UUIDs included.

WHAT HAPPENED
-------------
On 2026-09-14 the DEN at KC checkpoint qualified, wrote its decision and its
$5.00 position, committed -- and then the job failed with `Object of type UUID
is not JSON serializable`. The transaction was already durable; only the receipt
died. The job reported FAILED at 19:49:35 and again at 19:54:52 for a wager that
existed both times.

WHY THE TOP-LEVEL FIX WOULD NOT HAVE BEEN ENOUGH
------------------------------------------------
`decision_id` and `position_id` were ALREADY strings. The value that killed
json.dumps was `shadow_forecast_row_id`, nested one level down inside
`decisions[]`. One raw UUID anywhere in the tree fails the whole serialization,
so these tests assert on the real shape the evaluator returns rather than a
fixture that happens to contain the fields someone remembered.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest

from app.services.nfl_paper_decision import _jsonable


def raw_uuid_paths(value, path="") -> list[str]:
    """Every place a raw UUID survives, named by its path."""
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in raw_uuid_paths(v, f"{path}.{k}")]
    if isinstance(value, (list, tuple)):
        return [p for i, v in enumerate(value) for p in raw_uuid_paths(v, f"{path}[{i}]")]
    return [path] if isinstance(value, uuid.UUID) else []


#: The shape that actually failed, reconstructed from the persisted decision
#: evidence and the live evaluator return -- UUID-bearing fields included, at
#: the depths they really occur.
def real_receipt():
    forecast_row = uuid.UUID("7a9947ac-50c6-44c8-ab0d-d66771686760")
    outcome_id = uuid.UUID("6ef01973-a1b6-42f1-b006-cd167f023360")
    snapshot_id = uuid.UUID("0fd8f00a-054d-41f0-922a-8c26920e676e")
    game_id = uuid.UUID("14a64986-3d92-48af-ba69-15731e936d54")
    return {
        "records": 1,
        "reason": "CHECKPOINT_EVALUATED",
        "experiment_id": "c25f7272-39ef-4ad6-a196-bfae55efd3f2",
        "policy_version": "AISP_NFL_PAPER_POLICY_V1",
        "decisions": [{
            "game_id": game_id,
            "matchup": "DEN at KC",
            "starts_at": datetime(2026, 9, 15, 0, 15, tzinfo=UTC),
            "evaluated_at": datetime(2026, 9, 14, 23, 49, 35, 655349, tzinfo=UTC),
            "shadow_forecast_row_id": forecast_row,   # <-- the one that failed
            "outcome": "QUALIFIED",
            "reason": "ALL_GATES_PASSED",
            "selection": {
                "side": "AWAY",
                "american_odds": 115,
                "market_outcome_id": outcome_id,       # <-- nested two deep
                "odds_snapshot_id": snapshot_id,
                "observed_at": datetime(2026, 9, 14, 23, 42, 38, 389000, tzinfo=UTC),
                "sportsbook": "sgo:espnbet",
                "provider_classification": "RESEARCH",
                "win_probability": 0.5753,
                "tie_probability": 0.043,
                "loss_probability": 0.3817,
                "expected_value": 0.279895,
                "quarter_kelly": 0.06358070964517741,
                "stake": 5.0,
                "settlement": "STAKE_REFUNDED_PUSH",
            },
            "staking": {"quarter_kelly": 63.58, "position_cap": 5.0,
                        "same_game_cap": 10.0, "daily_cap": 20.0,
                        "stake": 5.0, "bound_by": "position_cap"},
            "decision_id": "94f5efe0-e006-4e8a-bfdd-7c4246961dd9",
            "position_id": "d85909ab-f8ae-488b-be5c-dd0b0cc98117",
            "persisted": "RECORDED",
        }],
    }


def test_the_unrepaired_shape_is_exactly_what_broke():
    """Guard the guard: if this stops failing, the test proves nothing."""

    with pytest.raises(TypeError, match="UUID is not JSON serializable"):
        json.dumps(real_receipt())


def test_the_failing_value_was_nested_not_top_level():
    paths = raw_uuid_paths(real_receipt())

    assert ".decisions[0].shadow_forecast_row_id" in paths
    assert ".decisions[0].selection.market_outcome_id" in paths
    # The two ids someone would think to fix were already strings.
    assert ".decisions[0].decision_id" not in paths
    assert ".decisions[0].position_id" not in paths


def test_the_boundary_conversion_makes_the_real_receipt_serializable():
    repaired = _jsonable(real_receipt())

    assert raw_uuid_paths(repaired) == []
    json.dumps(repaired)  # must not raise


def test_uuids_become_their_canonical_string_and_are_not_dropped():
    repaired = _jsonable(real_receipt())
    decision = repaired["decisions"][0]

    assert decision["shadow_forecast_row_id"] == "7a9947ac-50c6-44c8-ab0d-d66771686760"
    assert decision["selection"]["odds_snapshot_id"] == "0fd8f00a-054d-41f0-922a-8c26920e676e"
    assert decision["selection"]["market_outcome_id"] == "6ef01973-a1b6-42f1-b006-cd167f023360"


def test_nothing_else_about_the_receipt_changes():
    """Numbers stay numbers; a receipt of stringified floats is a new bug."""

    repaired = _jsonable(real_receipt())
    selection = repaired["decisions"][0]["selection"]

    assert selection["stake"] == 5.0
    assert selection["expected_value"] == 0.279895
    assert selection["quarter_kelly"] == 0.06358070964517741
    assert selection["american_odds"] == 115
    assert repaired["records"] == 1


def test_datetimes_survive_as_isoformat():
    repaired = _jsonable(real_receipt())

    assert repaired["decisions"][0]["evaluated_at"] == "2026-09-14T23:49:35.655349+00:00"


def test_a_uuid_added_later_anywhere_is_still_covered():
    """The fix is a boundary, not a list of known fields."""

    receipt = real_receipt()
    receipt["decisions"][0]["selection"]["some_future_id"] = uuid.uuid4()
    receipt["decisions"][0]["nested"] = {"deeper": [{"id": uuid.uuid4()}]}

    assert raw_uuid_paths(_jsonable(receipt)) == []
    json.dumps(_jsonable(receipt))

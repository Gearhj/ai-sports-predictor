"""Evidence helpers the decision board attaches for surfaces that headline a decision."""
from __future__ import annotations

from app.services.command_center import _adjusted, _plain_reasons


def test_plain_reasons_keep_sentences_and_drop_machine_codes() -> None:
    assert _plain_reasons([
        "Adjusted edge -0.1 pts below the 2.0 pt minimum (penalties: starter 2.0, lineup 0.0)",
        "ALL_GATES_PASSED",
        "",
        "Adjusted expected value is not positive",
    ]) == [
        "Adjusted edge -0.1 pts below the 2.0 pt minimum (penalties: starter 2.0, lineup 0.0)",
        "Adjusted expected value is not positive",
    ]
    assert _plain_reasons({"reason": "ALL_GATES_PASSED", "outcome": "QUALIFIED"}) == []
    assert _plain_reasons(None) == []


def test_adjusted_values_come_only_from_recorded_policy_evidence() -> None:
    assert _adjusted({"adjusted_edge": 0.07237, "adjusted_expected_value": 0.2, "adjusted_probability": 0.4188}) == {
        "edge": 0.07237, "expected_value": 0.2, "probability": 0.4188}
    # A policy that recorded no adjusted figures gets none invented.
    assert _adjusted({"selection": {"expected_value": 0.28}}) is None
    assert _adjusted(None) is None

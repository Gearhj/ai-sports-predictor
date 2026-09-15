"""Readiness labels: honest windows, and never "no bet" for an unevaluated game."""
from datetime import UTC, datetime

from app.services.sport_readiness import _et_label, _window


def test_the_decision_window_is_t30_to_t15_in_eastern_time():
    window = _window(datetime(2026, 9, 17, 23, 30, tzinfo=UTC))  # 7:30 PM EDT kickoff
    assert window["opens_at"] == "2026-09-17T23:00:00+00:00"
    assert window["closes_at"] == "2026-09-17T23:15:00+00:00"
    assert window["label"] == "Thu Sep 17, 7:00 PM ET – 7:15 PM ET"


def test_labels_are_portable_without_platform_strftime_flags():
    assert _et_label(datetime(2026, 10, 20, 19, 0, tzinfo=UTC)) == "Tue Oct 20, 3:00 PM ET"


def test_no_state_string_in_the_module_calls_an_unevaluated_game_no_bet():
    import inspect
    import app.services.sport_readiness as module
    source = inspect.getsource(module)
    assert '"NO_BET"' not in source and "'NO_BET'" not in source

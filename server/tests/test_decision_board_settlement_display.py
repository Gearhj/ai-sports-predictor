"""A finished game must not read like a live one while settlement is pending.

The board showed ATH @ TB on 2026-09-17 as an open position for 45 minutes
after the game ended, indistinguishable from SD @ COL which was genuinely still
being played. The wording is the whole fix: it describes state that is already
true and settles nothing.
"""
from __future__ import annotations

from app.services.command_center import _position_display


def test_a_final_game_with_an_open_position_says_settlement_is_pending():
    assert _position_display("OPEN", None, "GameStatus.FINAL") == "Final — settlement pending"


def test_a_live_game_with_an_open_position_says_the_game_is_in_progress():
    # LIVE is the canonical status; IN_PROGRESS is the scoreboard feed's word.
    assert _position_display("OPEN", None, "GameStatus.LIVE") == "Open — game in progress"
    assert _position_display("OPEN", None, "GameStatus.IN_PROGRESS") == "Open — game in progress"


def test_a_settled_position_is_never_called_pending():
    assert _position_display("SETTLED", -2.48, "GameStatus.FINAL") == "Settled"
    # Profit/loss present is enough, whatever the status column says.
    assert _position_display("OPEN", 1.14, "GameStatus.FINAL") == "Settled"


def test_a_voided_position_keeps_its_own_word():
    assert _position_display("VOID", None, "GameStatus.CANCELED") == "Void"


def test_an_open_position_on_a_scheduled_game_is_just_open():
    assert _position_display("OPEN", None, "GameStatus.SCHEDULED") == "Open"


def test_enum_repr_and_bare_strings_are_both_understood():
    """The row carries a python enum repr; a plain string must work too."""

    assert _position_display("PositionStatus.OPEN", None, "FINAL") == "Final — settlement pending"
    assert _position_display("open", None, "final") == "Final — settlement pending"

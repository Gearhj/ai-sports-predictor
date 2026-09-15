"""MLB detailedState to canonical GameStatus.

On 2026-09-14 CWS at CLE was recorded live 7-1 at 21:41, SCHEDULED 7-3 at 22:02
and FINAL at 22:33. MLB's "Game Over" -- sent between the last out and the
official "Final" -- matched nothing in the mapper and fell through to SCHEDULED.
"""
import pytest

from app.ingestion.normalizer import _game_status
from app.models import GameStatus


def test_game_over_is_in_play_not_scheduled():
    assert _game_status("Game Over") == GameStatus.LIVE


def test_game_over_is_not_promoted_to_final():
    """The canonical row feeds settlement; only MLB's official Final may finish it."""
    assert _game_status("Game Over") != GameStatus.FINAL


@pytest.mark.parametrize("state", [
    "In Progress", "Manager challenge: Tag play", "Umpire review", "Delayed: Rain",
])
def test_interrupted_play_stays_live(state):
    assert _game_status(state) == GameStatus.LIVE


@pytest.mark.parametrize("state,expected", [
    ("Scheduled", GameStatus.SCHEDULED),
    ("Pre-Game", GameStatus.SCHEDULED),
    ("Warmup", GameStatus.SCHEDULED),
    ("Delayed Start: Rain", GameStatus.SCHEDULED),
    ("Final", GameStatus.FINAL),
    ("Completed Early: Rain", GameStatus.FINAL),
    ("Postponed: Rain", GameStatus.POSTPONED),
    ("Cancelled", GameStatus.CANCELED),
])
def test_existing_mappings_are_unchanged(state, expected):
    assert _game_status(state) == expected


def test_the_real_2026_09_14_sequence_never_regresses():
    sequence = ["In Progress", "In Progress", "Game Over", "Final"]
    mapped = [_game_status(s) for s in sequence]
    assert GameStatus.SCHEDULED not in mapped
    assert mapped == [GameStatus.LIVE, GameStatus.LIVE, GameStatus.LIVE, GameStatus.FINAL]

"""League scope for canonical games.

INVARIANT
---------
A sport-specific process selects and accepts only its own league's games.

``games`` holds every league. ``source_mode`` (LIVE, DEMO, RESEARCH_SHADOW) says
where a row came from, never which sport it is: the NBA point-Elo importer
stores its game LIVE, NFL and College Football games are RESEARCH_SHADOW. A
process that selected ``source_mode == 'LIVE'`` games by date alone would
therefore run MLB forecast generation, MLB experiments and MLB evidence alerts
on a basketball game. So:

* every sport-specific query carries ``in_league(<its league code>)`` next to
  its lifecycle or timing condition (``in_league`` needs no join);
* shared date selectors take the league code as a required argument, with no
  default, so a new caller has to say which sport it means;
* entry points that can be handed a game directly (forecast generation, V18
  decision resolution, experiment decision append) call ``require_league`` and
  refuse a game of another league, logging the exclusion.

Nothing here is MLB-specific. A new sport's jobs pass their own league code.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session

from app.core.logging import logger
from app.models import Game, League

MLB = "MLB"

#: The reason recorded when a process refuses another league's game.
LEAGUE_MISMATCH = "LEAGUE_MISMATCH"


def in_league(league_code: str) -> ColumnElement[bool]:
    """A WHERE clause keeping only ``league_code`` games. Needs no join."""

    return Game.league_id.in_(select(League.id).where(League.code == league_code))


def league_code_of(session: Session, game: Game) -> str | None:
    return session.scalar(select(League.code).where(League.id == game.league_id))


class LeagueMismatchError(ValueError):
    """A sport-specific process was handed a game of another league."""

    reason = LEAGUE_MISMATCH

    def __init__(self, *, process: str, expected: str, actual: str | None, game_id: Any) -> None:
        self.process = process
        self.expected = expected
        self.actual = actual
        self.game_id = game_id
        super().__init__(
            f"{process} accepts only {expected} games; game {game_id} is {actual or 'of no league'}"
        )

    def receipt(self) -> dict[str, Any]:
        return {"game_id": str(self.game_id), "reason": self.reason,
                "process": self.process, "expected_league": self.expected,
                "actual_league": self.actual}


def require_league(session: Session, game: Game, league_code: str, *, process: str) -> None:
    """Refuse, and log, a game that is not in ``league_code``."""

    actual = league_code_of(session, game)
    if actual == league_code:
        return
    error = LeagueMismatchError(process=process, expected=league_code, actual=actual, game_id=game.id)
    logger.warning("league_mismatch_excluded", **error.receipt())
    raise error


__all__ = [
    "LEAGUE_MISMATCH", "MLB", "LeagueMismatchError", "in_league", "league_code_of",
    "require_league",
]

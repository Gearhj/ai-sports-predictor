"""What a service day actually needs, before capacity is divided.

WHY DEMAND COMES FIRST
----------------------
Fixed percentage shares hand NFL in-game two objects a day whether or not a
single NFL game is being followed, and hand MLB whatever is left over whether
that covers three games or thirteen. On a Monday with one NFL game and ten MLB
games that is exactly backwards.

So the day's real demand is measured from stored evidence first -- is there an
authorised NFL paper game, is any game actually being followed, is a completed
followed game missing its period scores -- and only the reserved remainder goes
to MLB. A lane that has nothing to do that day reserves nothing.

RESERVATIONS EXPIRE
-------------------
Capacity held for a window that has closed returns to the same service day's
pool. Holding an object for an NFL checkpoint that has already passed helps
nobody and costs an MLB game its price.

Reads persisted rows only. No provider request.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Game, GameStatus, League, OfficialAspPosition, PositionStatus,
    ShadowExperimentDecision,
)
from app.services.service_day import service_day_bounds, service_date

#: One targeted object per authorised NFL paper game.
NFL_PREGAME_PER_GAME = 1
#: At most this many in-game checks, and only for a followed game.
NFL_LIVE_PER_GAME = 2
#: One period-breakdown object per completed followed game that lacks one.
NFL_PERIODS_PER_GAME = 1

#: How long before kickoff an NFL pregame object is worth holding.
NFL_PREGAME_HOLD = timedelta(minutes=90)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _nfl_games_today(session: Session, start, end) -> list[tuple[Any, datetime]]:
    return [
        (row[0], _aware(row[1]))
        for row in session.execute(
            select(Game.id, Game.starts_at)
            .join(League, League.id == Game.league_id)
            .where(League.code == "NFL",
                   Game.starts_at >= start, Game.starts_at < end)
            .order_by(Game.starts_at)
        ).all()
    ]


def _followed(session: Session, game_ids: list[Any]) -> set[Any]:
    """Games with a recorded position or parlay leg -- the ones worth paying to watch."""

    if not game_ids:
        return set()
    rows = session.execute(
        select(ShadowExperimentDecision.game_id)
        .join(OfficialAspPosition,
              OfficialAspPosition.shadow_decision_id == ShadowExperimentDecision.id)
        .where(ShadowExperimentDecision.game_id.in_(game_ids),
               OfficialAspPosition.status == PositionStatus.OPEN)
    ).all()
    followed = {row[0] for row in rows}
    try:
        from app.models import ParlayTicketLeg  # optional in some builds
        legs = session.execute(
            select(ParlayTicketLeg.game_id)
            .where(ParlayTicketLeg.game_id.in_(game_ids))
        ).all()
        followed |= {row[0] for row in legs}
    except Exception:  # noqa: BLE001 - a missing parlay model is not an error here
        pass
    return followed


def measure(session: Session, *, now: datetime | None = None) -> dict[str, Any]:
    """The day's reservations, each justified by something that exists."""

    moment = now or datetime.now(UTC)
    day = service_date(moment)
    start, end = service_day_bounds(day)

    nfl = _nfl_games_today(session, start, end)
    nfl_ids = [gid for gid, _ in nfl]
    followed = _followed(session, nfl_ids)

    # 1. One targeted pregame object per authorised NFL paper game still ahead
    #    of its hold window closing.
    from app.services import nfl_paper_policy as policy

    pregame_games = []
    if policy.ACTIVATED:
        pregame_games = [gid for gid, starts in nfl
                         if starts - moment > -NFL_PREGAME_HOLD]
    pregame = min(len(pregame_games), NFL_PREGAME_PER_GAME) if pregame_games else 0

    # 2. In-game checks only for a followed game that is actually in progress
    #    or about to be.
    live_games = [gid for gid, starts in nfl
                  if gid in followed and starts <= moment + timedelta(minutes=30)]
    live = min(len(live_games) * NFL_LIVE_PER_GAME, NFL_LIVE_PER_GAME)

    # 3. A period breakdown only for a completed followed game missing one.
    periods_games = []
    for gid, _starts in nfl:
        if gid not in followed:
            continue
        game = session.get(Game, gid)
        if game is not None and game.status == GameStatus.FINAL:
            periods_games.append(gid)
    periods = min(len(periods_games), NFL_PERIODS_PER_GAME)

    return {
        "service_date": day.isoformat(),
        "nfl_games_today": len(nfl),
        "nfl_games_followed": len(followed),
        "reservations": {
            "sgo_nfl_events": pregame,
            "sgo_nfl_live": live,
            "sgo_nfl_periods": periods,
        },
        "why": {
            "sgo_nfl_events": (
                f"{pregame} object reserved: an authorised NFL paper game is on "
                "this service day." if pregame else
                "No object reserved: no authorised NFL paper game today."),
            "sgo_nfl_live": (
                f"{live} reserved for {len(live_games)} followed game(s) at or "
                "near kickoff." if live else
                "No object reserved: no followed NFL game is in its live window."),
            "sgo_nfl_periods": (
                f"{periods} reserved for a completed followed game missing "
                "period scores." if periods else
                "No object reserved: no completed followed game needs one."),
        },
        # Everything not reserved above goes to MLB price coverage.
        "remainder_lane": "sgo_mlb_events",
    }


__all__ = ["NFL_LIVE_PER_GAME", "NFL_PERIODS_PER_GAME", "NFL_PREGAME_PER_GAME",
           "measure"]

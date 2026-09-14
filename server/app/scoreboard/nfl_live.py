"""In-game NFL score for a bounded set of tracked games.

WHY THIS IS NOT "POLL THE SLATE"
--------------------------------
SportsGameOdds bills per event object against a 2,500-object rolling 30-day
allowance. A thirteen-game Sunday polled every ten minutes is 260 objects in one
afternoon -- more than an eighth of the month's entire allowance for one day of
one sport. So this tracks GAMES SOMEONE IS FOLLOWING, not a slate:

    a game carrying a recorded wager, a parlay leg, or a qualifying decision,
    and if none of those exist, the smallest set the slate offers.

and it polls each one only between its own kickoff and its own final. Outside
that window a tracked game costs nothing, which is most of the week.

WHAT "AFFORDABLE" MEANS HERE
----------------------------
Three guards, because one is not enough when the failure mode is spending money
that belongs to something else:

*   the provider budget's own reserve, which is never touched;
*   a FLOOR above that reserve, held for MLB pregame odds -- the collection this
    feature must not quietly outbid;
*   a per-day object cap for NFL in-game specifically, so a long overtime or a
    stuck watch window cannot turn into an open tap.

Every refusal names itself and reports the arithmetic, because "no score today"
and "no allowance today" are different facts and a reader is owed the second.

WHAT THE SOURCE DOES NOT GIVE
-----------------------------
No clock. The event object has no clock field at all -- verified against a live
response, not assumed from documentation -- so an NFL game shows its quarter and
nothing about time remaining. No score timestamp either, so freshness is the age
of AISP's own check and source latency is reported as unknown.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Game, League

#: How long after kickoff a game is still worth checking. Generous enough for
#: overtime and a weather delay; bounded so a game whose final never arrives
#: stops costing objects.
WATCH_AFTER_KICKOFF = timedelta(hours=4, minutes=30)
WATCH_BEFORE_KICKOFF = timedelta(minutes=5)

#: The cadence this is scheduled at. One object per tracked game per cycle.
DEFAULT_CADENCE_MINUTES = 15

#: Ceiling on how many games are followed at once. Not a slate: this is the
#: number of games a reader can plausibly be following at the same time.
MAX_TRACKED_GAMES = 4

#: How many objects this lane may spend in a day is no longer decided here: the
#: shared allocation in `provider_allocation` owns it, so NFL in-game and MLB
#: pregame are two shares of one budget rather than two budgets arguing. Both
#: names are kept as the ceiling on a single run, not as a second allowance.
MAX_OBJECTS_PER_RUN = MAX_TRACKED_GAMES


def tracked_game_ids(session: Session, *, now: datetime | None = None) -> list[UUID]:
    """NFL games someone is actually following, newest kickoff first.

    Followed means AISP holds something of the reader's against it: a parlay
    leg, or a recorded position. A game nobody has a stake in is not tracked --
    it still gets its final and its quarter breakdown for free from the other
    two collectors.
    """

    moment = now or datetime.now(UTC)
    window_open = moment - WATCH_AFTER_KICKOFF
    window_shut = moment + WATCH_BEFORE_KICKOFF

    from app.models.parlay import ParlayTicketLeg

    in_window = (
        select(Game.id)
        .join(League, League.id == Game.league_id)
        .where(
            League.code == "NFL",
            Game.starts_at >= window_open,
            Game.starts_at <= window_shut,
        )
    )
    candidates = set(session.scalars(in_window).all())
    if not candidates:
        return []

    followed = set(session.scalars(
        select(ParlayTicketLeg.game_id).where(ParlayTicketLeg.game_id.in_(candidates))
    ).all())

    # Straight positions reach a game through their market outcome rather than
    # directly, and NFL cannot fund an official position in this phase anyway,
    # so parlay legs are the whole of "followed" today. When an NFL position
    # becomes possible this is the one place that has to learn about it.
    chosen = followed or candidates
    ordered = session.scalars(
        select(Game.id).where(Game.id.in_(chosen)).order_by(Game.starts_at.desc())
    ).all()
    return list(ordered)[:MAX_TRACKED_GAMES]


def in_watch_window(
    session: Session, game_ids: list[UUID], *, now: datetime | None = None,
) -> list[UUID]:
    """Of the tracked games, those between kickoff and a recorded final.

    A game AISP already knows is over is dropped immediately: the final and the
    quarter breakdown come from the free feed and the once-per-game breakdown
    collector, and paying again for a score that cannot change is waste.
    """

    if not game_ids:
        return []
    moment = now or datetime.now(UTC)
    from app.models.scoreboard import GameScoreboardObservation

    finished = set(session.scalars(
        select(GameScoreboardObservation.game_id).where(
            GameScoreboardObservation.game_id.in_(game_ids),
            GameScoreboardObservation.status.in_(
                ("FINAL", "CANCELLED", "POSTPONED")),
        )
    ).all())

    rows = session.execute(
        select(Game.id, Game.starts_at).where(Game.id.in_(game_ids))
    ).all()
    live: list[UUID] = []
    for game_id, starts_at in rows:
        if game_id in finished:
            continue
        start = starts_at if starts_at.tzinfo else starts_at.replace(tzinfo=UTC)
        if start - WATCH_BEFORE_KICKOFF <= moment <= start + WATCH_AFTER_KICKOFF:
            live.append(game_id)
    return live


def objects_spent_today(session: Session, *, now: datetime | None = None) -> int:
    """NFL in-game objects spent since UTC midnight, from the ledger itself."""

    from sqlalchemy import func

    from app.models import ProviderRequestLedger

    moment = now or datetime.now(UTC)
    midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    total = session.scalar(
        select(func.coalesce(func.sum(ProviderRequestLedger.records_received), 0))
        .where(
            ProviderRequestLedger.provider == "SPORTSGAMEODDS",
            ProviderRequestLedger.endpoint_key == ENDPOINT_KEY,
            ProviderRequestLedger.status == "SUCCEEDED",
            ProviderRequestLedger.requested_at >= midnight,
        )
    )
    return int(total or 0)


#: Its own ledger key, so NFL in-game spend is separable from MLB pregame spend
#: in every report. A shared key would make the daily cap unenforceable and the
#: attribution unarguable in the wrong direction.
ENDPOINT_KEY = "sgo_nfl_live"


def affordability(
    session: Session, *, wanted: int, now: datetime | None = None,
) -> dict[str, Any]:
    """Whether these objects may be spent, from the one shared allocation.

    The private floor this used to keep against MLB pregame is gone. Two
    consumers each defending the same pool from a different direction is not a
    budget; the allocation now decides both, and the number here is the lane's
    own remaining daily allowance.
    """

    from app.services.provider_allocation import request_objects

    moment = now or datetime.now(UTC)
    grant = request_objects(session, lane=ENDPOINT_KEY, wanted=wanted, now=moment)
    allocation = grant.plan.allocations[ENDPOINT_KEY]
    detail = None
    if grant.granted < wanted:
        if grant.plan.daily_total == 0:
            detail = (
                f"{grant.plan.spendable} objects are spendable across "
                f"{grant.plan.days_remaining} days until the balance replenishes "
                f"on {grant.plan.replenishes_on.isoformat()}, so no lane has a "
                "whole object a day."
            )
        else:
            detail = (
                f"NFL in-game has {allocation.daily_allowance} objects a day and "
                f"has used {allocation.spent_today}"
                + (f" with {allocation.in_flight} in flight" if allocation.in_flight
                   else "")
                + "."
            )
    return {
        "allowed": grant.granted,
        "wanted": wanted,
        "spent_today": allocation.spent_today,
        "daily_cap": allocation.daily_allowance,
        "daily_room": allocation.room,
        "reason": grant.reason,
        "budget": grant.plan.as_dict(),
        "detail": detail,
    }


def collect_nfl_live(
    session: Session, settings: Any, *, now: datetime | None = None,
    fetch: Any = None,
) -> dict[str, Any]:
    """One cycle: score every tracked NFL game that is currently in progress."""

    from app.scoreboard.collect import _club, _fetch_sgo, _sgo_time
    from app.scoreboard.nfl import scoreboard_from_event, team_abbreviations
    from app.scoreboard.store import record_many
    from app.services.sgo_collection import (
        complete_request, eligible, object_budget, record_request,
    )

    moment = now or datetime.now(UTC)
    tracked = tracked_game_ids(session, now=moment)
    live = in_watch_window(session, tracked, now=moment)
    if not live:
        return {"records": 0, "league": "NFL", "requests": 0, "objects": 0,
                "tracked_games": len(tracked), "games_in_window": 0,
                "reason": "NO_TRACKED_NFL_GAME_IS_IN_ITS_WATCH_WINDOW"}

    ok, why = eligible(settings, now=moment)
    if not ok:
        return {"records": 0, "league": "NFL", "requests": 0, "objects": 0,
                "games_in_window": len(live), "reason": f"PROVIDER_NOT_ELIGIBLE:{why}"}

    money = affordability(session, wanted=len(live), now=moment)
    if money["allowed"] <= 0:
        # The honest refusal. The last known score stays on screen with its real
        # age; nothing pretends the game is not being played.
        return {"records": 0, "league": "NFL", "requests": 0, "objects": 0,
                "games_in_window": len(live),
                "reason": "NFL_LIVE_BUDGET_EXHAUSTED",
                "detail": money["detail"], "affordability": money}

    count = money["allowed"]
    starts = session.execute(
        select(Game.id, Game.starts_at).where(Game.id.in_(live))
    ).all()
    earliest = min(row[1] for row in starts)
    earliest = earliest if earliest.tzinfo else earliest.replace(tzinfo=UTC)

    params = {
        "leagueID": "NFL",
        "limit": str(count),
        "startsAfter": _sgo_time(earliest - timedelta(minutes=30)),
        "startsBefore": _sgo_time(moment + timedelta(minutes=5)),
        # Deliberately NOT finalized=false: a game that finished between cycles
        # must still return, or its final would never be recorded here.
    }
    budget = object_budget(session, now=moment)
    reservation = record_request(session, requested_at=moment, params=params,
                                 budget=budget, endpoint_key=ENDPOINT_KEY,
                                 objects_requested=count)
    session.commit()

    try:
        payload = (fetch or _fetch_sgo)(settings, params)
    except Exception as error:
        complete_request(session, reservation, objects=0, succeeded=False,
                         budget=budget, error=type(error).__name__)
        session.commit()
        return {"records": 0, "league": "NFL", "requests": 1, "objects": 0,
                "reason": "REQUEST_FAILED", "error_type": type(error).__name__}

    events = payload.get("data") or []
    complete_request(session, reservation, objects=len(events), succeeded=True,
                     budget=budget)

    from app.scoreboard.collect import _nfl_identity

    index: dict[tuple[str, str, str], UUID] = {}
    for game_id, starts_at in starts:
        sides = _nfl_identity(session, game_id)
        start = starts_at if starts_at.tzinfo else starts_at.replace(tzinfo=UTC)
        index[(start.astimezone(UTC).date().isoformat(),
               _club(sides.get("away")), _club(sides.get("home")))] = game_id

    pairs: list[tuple[UUID, dict[str, Any]]] = []
    unmatched: list[str] = []
    for event in events:
        state = scoreboard_from_event(event, retrieved_at=moment)
        sides = team_abbreviations(event)
        starts_at = (event.get("status") or {}).get("startsAt") or ""
        key = (starts_at[:10], _club(sides.get("away")), _club(sides.get("home")))
        game_id = index.get(key)
        if game_id is None:
            unmatched.append(f"{key[1]}@{key[2]} {key[0]}")
            continue
        pairs.append((game_id, state))

    result = record_many(session, pairs, checked_at=moment)
    session.commit()
    after = object_budget(session, now=moment)
    return {
        "records": result["observations_written"],
        "league": "NFL",
        "requests": 1,
        "objects": len(events),
        "tracked_games": len(tracked),
        "games_in_window": len(live),
        "games_matched": len(pairs),
        "unmatched_events": unmatched,
        "states_confirmed": result["states_confirmed"],
        "in_progress": sum(1 for _, s in pairs if s["status"] == "IN_PROGRESS"),
        "cadence_minutes": DEFAULT_CADENCE_MINUTES,
        "affordability": {**money, "budget_after": after.as_dict()},
    }


__all__ = [
    "DEFAULT_CADENCE_MINUTES",
    "ENDPOINT_KEY",
    "MAX_OBJECTS_PER_RUN",
    "MAX_TRACKED_GAMES",
    "WATCH_AFTER_KICKOFF",
    "affordability",
    "collect_nfl_live",
    "in_watch_window",
    "objects_spent_today",
    "tracked_game_ids",
]

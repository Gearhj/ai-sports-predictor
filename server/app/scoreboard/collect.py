"""Collecting scoreboard state, centrally and within the existing allowances.

ONE OWNER, ONE REQUEST PER CYCLE
--------------------------------
This runs inside the existing supervised scheduler, beside the jobs that already
own provider access. No browser tab ever reaches a provider; every surface reads
what this wrote. One MLB request covers the entire slate, so fifteen games cost
one call, not fifteen.

WHY IT ONLY SPEEDS UP WHEN SOMETHING IS ACTUALLY LIVE
-----------------------------------------------------
The fast cadence exists to follow a game in progress. A slate where the first
pitch is four hours away needs nothing of the sort, and polling it every two
minutes would be seven hundred pointless requests a day against a public
endpoint that is being generous by existing. So the job asks a cheap local
question first -- is any game in its window? -- and does nothing when the answer
is no.

SCORES DO NOT DEPEND ON HAVING A BET ON IT
------------------------------------------
Collection is keyed on the slate, not on forecasts, quotes or qualifying
opportunities, and the request carries no pregame filter. A game AISP has no
opinion about still gets its score; a game that has already started still gets
its score. That is deliberate: the filters that make sense for pricing a wager
are exactly the filters that would hide a game a reader is watching.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExternalIdMapping, Game, League
from app.scoreboard.mlb import scoreboards_from_schedule
from app.scoreboard.store import record_many

#: How long before first pitch the slate is worth watching, and how long after a
#: final to keep confirming it. The tail exists because a "final" can be revised.
WATCH_BEFORE = timedelta(minutes=20)
WATCH_AFTER = timedelta(hours=6)

#: Statuses that mean the game is worth a fast check right now.
ACTIVE_STATUSES = {"IN_PROGRESS", "DELAYED", "SUSPENDED"}


def games_in_window(
    session: Session, *, league: str, now: datetime | None = None,
) -> dict[str, UUID]:
    """Every game of this league near enough in time to be worth checking.

    Keyed by the league's own game id, because that is what a provider payload
    carries and matching on anything looser -- team and date, say -- is how a
    doubleheader's two games become one.
    """

    moment = now or datetime.now(UTC)
    source = {"MLB": "MLB_STATS_API"}[league]
    rows = session.execute(
        select(ExternalIdMapping.external_id, Game.id)
        .join(Game, Game.id == ExternalIdMapping.entity_id)
        .join(League, League.id == Game.league_id)
        .where(
            League.code == league,
            ExternalIdMapping.entity_type == "game",
            ExternalIdMapping.source == source,
            Game.starts_at >= moment - WATCH_AFTER,
            Game.starts_at <= moment + WATCH_BEFORE,
        )
    ).all()
    return {str(external): game_id for external, game_id in rows}


async def collect_mlb_scoreboard(
    session: Session, *, event_date: date | None = None,
    now: datetime | None = None, adapter: Any = None,
) -> dict[str, Any]:
    """Read the whole MLB slate's scoreboard state from one schedule response.

    Costs one request against a free, unmetered, already-authorized endpoint --
    the same request the schedule refresh has always made, read for the fields
    it was throwing away.
    """

    moment = now or datetime.now(UTC)
    # The slate is keyed on Eastern-evening reality rather than UTC midnight: a
    # 10pm Pacific game is still "tonight" to the person watching it.
    day = event_date or (moment - timedelta(hours=8)).date()

    watched = games_in_window(session, league="MLB", now=moment)
    if not watched:
        return {"records": 0, "league": "MLB", "reason": "NO_GAME_IN_WATCH_WINDOW",
                "requests": 0}

    if adapter is None:
        from app.data_sources.mlb import MLBStatsAPIAdapter
        adapter = MLBStatsAPIAdapter()
    response = await adapter.get_schedule(day)
    raw = (response.raw_payload or {}).get("schedule") or {}
    retrieved_at = response.envelope.fetched_at

    states = scoreboards_from_schedule(raw, retrieved_at=retrieved_at)
    pairs = [
        (game_id, states[source_id])
        for source_id, game_id in watched.items()
        if source_id in states
    ]
    result = record_many(session, pairs, checked_at=retrieved_at)
    session.commit()

    live = sum(1 for _, state in pairs if state["status"] in ACTIVE_STATUSES)
    return {
        "records": result["observations_written"],
        "league": "MLB",
        "requests": 1,
        "provider_cost": "none (free unmetered endpoint)",
        "games_watched": len(watched),
        "games_matched": len(pairs),
        "games_active": live,
        "states_confirmed": result["states_confirmed"],
        "retrieved_at": retrieved_at.isoformat(),
        "event_date": day.isoformat(),
    }


def should_run_fast(session: Session, *, now: datetime | None = None) -> bool:
    """Is anything actually in progress, or about to be?

    Answered from what AISP already knows, with no provider call, so the cheap
    question stays cheap.
    """

    moment = now or datetime.now(UTC)
    from app.models.scoreboard import GameScoreboardObservation

    active = session.scalar(
        select(GameScoreboardObservation.id)
        .join(Game, Game.id == GameScoreboardObservation.game_id)
        .where(
            GameScoreboardObservation.status.in_(ACTIVE_STATUSES),
            Game.starts_at >= moment - WATCH_AFTER,
            Game.starts_at <= moment + WATCH_BEFORE,
        )
        .limit(1)
    )
    if active is not None:
        return True
    # Nothing is live yet, but a game starting within the window has to be
    # checked or nothing would ever notice it start.
    return bool(games_in_window(session, league="MLB", now=moment))


# -------------------------------------------------------------- NFL ------
#
# A different source, a different economics, and therefore a different shape.
#
# SportsGameOdds bills per event object against a 2,500-object rolling 30-day
# allowance with a reserve that is never spent. A thirteen-game Sunday polled
# every ten minutes is well over two hundred objects in one afternoon, which is
# the entire remaining allowance. So NFL is collected ONCE per completed game,
# for the quarter breakdown the free final feed does not carry, bounded to games
# recent enough to be on a screen.
#
# The live NFL score is deliberately not collected. Showing a number that was
# last confirmed at kickoff, styled like a live score, would be worse than
# showing none -- so the surface reports that in-game NFL updates are
# unavailable and shows the final when the free feed publishes it.

#: How far back a completed NFL game stays worth one object. Beyond this it is
#: history, and history is read from what was already retained.
NFL_BREAKDOWN_WINDOW = timedelta(days=8)

#: Hard ceiling per run, independent of the shared allocation. Two guards,
#: because the expensive failure here is a loop that looks bounded.
NFL_MAX_OBJECTS_PER_RUN = 16

#: This lane's key in the shared allocation and in the provider ledger.
PERIODS_LANE = "sgo_nfl_periods"


def nfl_games_needing_breakdown(
    session: Session, *, now: datetime | None = None, limit: int = NFL_MAX_OBJECTS_PER_RUN,
) -> list[Any]:
    """Completed NFL games with no complete period breakdown stored yet.

    "Completed" is taken from AISP's own final record, never from elapsed time:
    a game that has run long is not over, and treating a stopped clock as a
    result is the mistake this whole feature has to avoid.
    """

    from app.models.scoreboard import GameScoreboardObservation

    moment = now or datetime.now(UTC)
    have = set(session.scalars(
        select(GameScoreboardObservation.game_id).where(
            GameScoreboardObservation.league_code == "NFL",
            GameScoreboardObservation.periods_complete.is_(True),
        )
    ).all())

    rows = session.execute(
        select(Game.id, Game.starts_at, Game.status)
        .join(League, League.id == Game.league_id)
        .where(
            League.code == "NFL",
            Game.starts_at >= moment - NFL_BREAKDOWN_WINDOW,
            Game.starts_at <= moment,
            Game.status == "FINAL",
        )
        .order_by(Game.starts_at.desc())
    ).all()
    return [row for row in rows if row[0] not in have][:max(0, limit)]


def _nfl_identity(session: Session, game_id: Any) -> dict[str, str | None]:
    """Home and away abbreviations for one AISP game, for orientation checking."""

    from app.models import GameTeam, Team

    rows = session.execute(
        select(GameTeam.is_home, Team.abbreviation)
        .join(Team, Team.id == GameTeam.team_id)
        .where(GameTeam.game_id == game_id)
    ).all()
    return {("home" if is_home else "away"): abbreviation for is_home, abbreviation in rows}


def collect_nfl_breakdowns(
    session: Session, settings: Any, *, now: datetime | None = None,
    fetch: Any = None,
) -> dict[str, Any]:
    """One budget-governed request for the quarter breakdowns still missing.

    Returns without spending anything when there is nothing to fetch, when the
    provider is ineligible, or when the object budget would dip into its
    reserve. Every refusal names itself.
    """

    from app.scoreboard.nfl import scoreboard_from_event, team_abbreviations
    from app.services.sgo_collection import (
        complete_request, eligible, object_budget, record_request,
    )

    moment = now or datetime.now(UTC)
    wanted = nfl_games_needing_breakdown(session, now=moment)
    if not wanted:
        return {"records": 0, "league": "NFL", "requests": 0, "objects": 0,
                "reason": "NO_NFL_GAME_NEEDS_A_BREAKDOWN"}

    ok, why = eligible(settings, now=moment)
    if not ok:
        return {"records": 0, "league": "NFL", "requests": 0, "objects": 0,
                "reason": f"PROVIDER_NOT_ELIGIBLE:{why}"}

    # The shared allocation decides, not a private floor. This lane yields to
    # every other one: it buys presentation detail for games already over, so a
    # partial grant simply means fewer breakdowns today and the rest tomorrow.
    from app.services.provider_allocation import request_objects

    budget = object_budget(session, now=moment)
    grant = request_objects(
        session, lane=PERIODS_LANE,
        wanted=min(len(wanted), NFL_MAX_OBJECTS_PER_RUN), now=moment)
    if not grant.allowed:
        return {"records": 0, "league": "NFL", "requests": 0, "objects": 0,
                "reason": f"ALLOCATION_{grant.reason}",
                "games_needing_breakdown": len(wanted),
                "allocation": grant.as_dict()}
    count = grant.granted

    earliest = min(row[1] for row in wanted[:count])
    params = {
        "leagueID": "NFL",
        "limit": str(count),
        "startsAfter": _sgo_time(earliest - timedelta(hours=1)),
        "startsBefore": _sgo_time(moment),
        "finalized": "true",
    }
    reservation = record_request(session, requested_at=moment, params=params,
                                 budget=budget, endpoint_key=PERIODS_LANE,
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

    # Index AISP's games by (UTC kickoff date, away, home) so an event is matched
    # on identity rather than on order, and orientation is verified by the key
    # itself: away must meet away. The one thing worse than a missing breakdown
    # is one attached to the wrong game, or to the right game backwards.
    #
    # The date is normalised to UTC first. `.date()` on an aware datetime gives
    # the date in ITS OWN offset, so a Wednesday 8:20pm Eastern kickoff reads as
    # the 9th locally and the 10th in the UTC the provider publishes -- which
    # silently lost every night game until it was measured.
    index: dict[tuple[str, str, str], Any] = {}
    for game_id, starts_at, _ in wanted[:count]:
        sides = _nfl_identity(session, game_id)
        starts = starts_at if starts_at.tzinfo else starts_at.replace(tzinfo=UTC)
        index[(starts.astimezone(UTC).date().isoformat(),
               _club(sides.get("away")), _club(sides.get("home")))] = game_id

    pairs: list[tuple[Any, dict[str, Any]]] = []
    unmatched: list[str] = []
    for event in events:
        state = scoreboard_from_event(event, retrieved_at=moment)
        sides = team_abbreviations(event)
        starts = (event.get("status") or {}).get("startsAt") or ""
        key = (starts[:10], _club(sides.get("away")), _club(sides.get("home")))
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
        "games_needing_breakdown": len(wanted),
        "games_matched": len(pairs),
        "unmatched_events": unmatched,
        "reconciled": sum(1 for _, s in pairs if s["periods_reconcile"] is True),
        "budget": after.as_dict(),
        "request_filters": params,
    }


#: Where the provider's club spelling differs from AISP's. Not a guess: the
#: live response was read and these are the exact strings it sends. A club that
#: is not listed is used as-is, so an unknown spelling fails to match and is
#: reported rather than being coerced into whichever club sorts nearest.
NFL_CLUB_ALIASES = {
    "LA": "LAR",     # the provider uses LA for the Rams; LAC is sent as LAC
    "WSH": "WAS",
    "JAC": "JAX",
    "LVR": "LV",
    "SD": "LAC",
    "STL": "LAR",
    "OAK": "LV",
}


def _club(abbreviation: str | None) -> str:
    text = str(abbreviation or "").strip().upper()
    return NFL_CLUB_ALIASES.get(text, text)


def _sgo_time(value: datetime) -> str:
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fetch_sgo(settings: Any, params: dict[str, str]) -> dict[str, Any]:
    """One request, no retry, no pagination. A second page is a second charge."""

    import httpx

    url = f"{settings.sportsgameodds_base_url.rstrip('/')}/events"
    headers = {"X-Api-Key": settings.sportsgameodds_api_key, "Accept": "application/json"}
    with httpx.Client(timeout=30.0, follow_redirects=False) as client:
        response = client.get(url, params=params, headers=headers)
    if response.status_code != 200:
        raise RuntimeError(f"SportsGameOdds returned HTTP {response.status_code}")
    return response.json()


__all__ = [
    "ACTIVE_STATUSES",
    "NFL_BREAKDOWN_WINDOW",
    "NFL_MAX_OBJECTS_PER_RUN",
    "PERIODS_LANE",
    "collect_mlb_scoreboard",
    "NFL_CLUB_ALIASES",
    "collect_nfl_breakdowns",
    "games_in_window",
    "nfl_games_needing_breakdown",
    "should_run_fast",
]

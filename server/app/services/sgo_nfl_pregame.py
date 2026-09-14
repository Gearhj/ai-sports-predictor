"""One SportsGameOdds event object for one NFL game, near its checkpoint.

WHY THIS LANE EXISTS AT ALL
---------------------------
The NFL paper policy needs a price that is FRESH AT ITS CHECKPOINT, not a price
that happened to be captured at some point. The 240-minute freshness rule means
a quote taken this morning is already disqualified by 8pm, so the object has to
be spent late or it is wasted. That is the whole design: one object, timed.

WHY IT IS NOT THE MLB COLLECTOR WITH A DIFFERENT league_id
-----------------------------------------------------------
The MLB path ingests through the generic normalizer, which binds a quote to a
game by matching team names and kickoff. NFL has an explicit reviewed franchise
map and a persisted provider-event mapping for exactly that reason, so this
ingests through ``NFLShadowOddsService`` and never joins on names and a date.

WHAT IT WILL NOT DO
-------------------
Spend from another lane. Pregame retrieval is charged to ``sgo_nfl_events`` and
to nothing else -- in particular never to the live-score lane, which pays for a
different thing. If the lane has no room the collector reports that and stops;
it does not borrow.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Game, GameStatus, GameTeam, League, Team
from app.services.sgo_collection import (
    FallbackFetch, _refuse, complete_request, eligible, parse_events,
    parse_main_markets, record_request, to_event_dtos,
)

#: The allocation lane. Never `sgo_nfl_live`, which buys in-game scores.
LANE = "sgo_nfl_events"

#: The provider these quotes actually come from. The registry classifies it as
#: research rather than governed, and that classification travels with every row.
SOURCE = "SPORTSGAMEODDS"

#: How close to kickoff the object is worth spending. The producer's own
#: FINAL_PREGAME eligibility is 30-15 minutes before kickoff; collecting inside
#: a slightly wider band leaves room for one retry without a second object,
#: while still landing far inside the 240-minute freshness limit.
WINDOW_OPENS_BEFORE_KICKOFF = timedelta(minutes=75)
WINDOW_CLOSES_BEFORE_KICKOFF = timedelta(minutes=15)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def due_games(session: Session, *, now: datetime | None = None) -> list[dict[str, Any]]:
    """NFL games inside the collection window, soonest first."""

    moment = now or datetime.now(UTC)
    rows = session.execute(
        select(Game.id, Game.starts_at)
        .join(League, League.id == Game.league_id)
        .where(League.code == "NFL",
               Game.status == GameStatus.SCHEDULED,
               Game.starts_at > moment,
               Game.starts_at <= moment + WINDOW_OPENS_BEFORE_KICKOFF)
        .order_by(Game.starts_at)
    ).all()
    out: list[dict[str, Any]] = []
    for game_id, starts_at in rows:
        starts = _aware(starts_at)
        if starts - moment < WINDOW_CLOSES_BEFORE_KICKOFF:
            continue  # inside the final 15 minutes: too late to be useful
        sides = dict(session.execute(
            select(GameTeam.is_home, Team.abbreviation)
            .join(Team, Team.id == GameTeam.team_id)
            .where(GameTeam.game_id == game_id)
        ).all())
        out.append({
            "game_id": str(game_id),
            "starts_at": starts.isoformat(),
            "minutes_to_kickoff": round((starts - moment).total_seconds() / 60, 1),
            "away": sides.get(False), "home": sides.get(True),
        })
    return out


def fetch_nfl_pregame(
    settings: Settings, *, limit: int = 1, now: datetime | None = None,
    starts_within_hours: int = 2,
) -> FallbackFetch:
    """Exactly one request for upcoming NFL events. No retry, no pagination."""

    ok, reason = eligible(settings, now=now)
    if not ok:
        raise _refuse(f"SportsGameOdds is not eligible: {reason}")
    moment = now or datetime.now(UTC)
    params = {
        "leagueID": "NFL",
        "limit": str(max(1, int(limit))),
        "startsAfter": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "startsBefore": (moment + timedelta(hours=starts_within_hours)
                         ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "finalized": "false",
        "oddsPresent": "true",
    }
    url = f"{settings.sportsgameodds_base_url.rstrip('/')}/events"
    headers = {"X-Api-Key": settings.sportsgameodds_api_key,
               "Accept": "application/json"}
    with httpx.Client(timeout=30.0, follow_redirects=False) as client:
        response = client.get(url, params=params, headers=headers)
    if response.status_code != 200:
        raise _refuse(f"SportsGameOdds returned HTTP {response.status_code}; not retried")
    payload = response.json()
    parse = parse_events(payload, received_at=moment, now=moment, league_id="NFL")
    # The same bytes read for spreads and totals. The provider bills the EVENT,
    # so every market already inside that response is free once it is paid for.
    main = parse_main_markets(payload, received_at=moment, now=moment, league_id="NFL")
    return FallbackFetch(
        requested_at=moment,
        objects=len(payload.get("data") or []),
        pairs=parse.pairs, parse=parse, main=main,
    )


def collect_nfl_pregame(
    session: Session, settings: Settings, *,
    now: datetime | None = None, job_run_id: Any = None,
) -> dict[str, Any]:
    """Reserve one object, fetch it, ingest it. One request or none."""

    from app.services.nfl_shadow_odds_service import NFLShadowOddsService
    from app.services.provider_allocation import request_objects
    from app.services.sgo_collection import object_budget

    moment = now or datetime.now(UTC)
    due = due_games(session, now=moment)
    if not due:
        return {"attempted": False, "reason": "NO_NFL_GAME_IN_THE_COLLECTION_WINDOW",
                "objects": 0, "window_opens_minutes_before_kickoff":
                    int(WINDOW_OPENS_BEFORE_KICKOFF.total_seconds() // 60)}

    ok, reason = eligible(settings, now=moment)
    if not ok:
        return {"attempted": False, "reason": "PROVIDER_NOT_ELIGIBLE",
                "objects": 0, "detail": reason, "games_due": due}

    grant = request_objects(session, lane=LANE, wanted=1, now=moment)
    if not grant.allowed:
        # Named, never borrowed from another lane.
        return {"attempted": False, "reason": f"ALLOCATION_{grant.reason}",
                "objects": 0, "lane": LANE, "allocation": grant.as_dict(),
                "games_due": due}

    budget = object_budget(session, now=moment)
    allowed, refusal = budget.may_spend(1)
    if not allowed:
        return {"attempted": False, "reason": "OBJECT_RESERVE_GUARD",
                "objects": 0, "detail": refusal, "budget": budget.as_dict()}

    reservation = record_request(
        session, requested_at=moment, params={"leagueID": "NFL", "limit": "1"},
        budget=budget, job_run_id=job_run_id, objects_requested=1,
        endpoint_key=LANE,
    )
    session.commit()

    try:
        fetch = fetch_nfl_pregame(settings, limit=1, now=moment)
    except Exception as error:  # one attempt; the schedule owns the next one
        complete_request(session, reservation, objects=0, succeeded=False,
                         budget=budget, error=type(error).__name__,
                         job_run_id=job_run_id)
        session.commit()
        return {"attempted": True, "reason": "REQUEST_FAILED", "objects": 0,
                "error_type": type(error).__name__, "games_due": due}

    # Explicit franchise mapping, never a name-and-date join -- and stamped with
    # the provider that actually supplied it. SportsGameOdds is a RESEARCH
    # provider in the registry, so its books land under the `sgo:` namespace and
    # its snapshots carry SPORTSGAMEODDS provenance. Writing them as
    # THE_ODDS_API would have merged two providers' "draftkings" into one
    # Sportsbook row, and the governed two-sided pairing keys on sportsbook_id.
    result = NFLShadowOddsService(session, source=SOURCE).ingest(
        to_event_dtos(fetch.pairs), available_as_of=fetch.requested_at)
    complete_request(session, reservation, objects=fetch.objects, succeeded=True,
                     budget=budget, job_run_id=job_run_id)
    session.commit()
    return {
        "attempted": True, "reason": "COLLECTED", "lane": LANE,
        "objects": fetch.objects,
        "games_due": due,
        "events": result["events"], "mapped_events": result["mapped_events"],
        "quotes": result["quotes"], "duplicates": result["duplicates"],
        "rejected_events": result["rejected_events"],
        "source": SOURCE,
        "provider_governed": False,
        "bookmakers": sorted({pair.bookmaker_key for pair in fetch.pairs}),
        "main_markets": (fetch.main.by_market() if fetch.main else {}),
        "allocation": grant.as_dict(),
    }


__all__ = ["LANE", "SOURCE", "WINDOW_CLOSES_BEFORE_KICKOFF",
           "WINDOW_OPENS_BEFORE_KICKOFF", "collect_nfl_pregame", "due_games",
           "fetch_nfl_pregame"]

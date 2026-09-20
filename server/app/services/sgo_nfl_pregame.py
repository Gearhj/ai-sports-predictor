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
from app.services.collection_halt import (
    SELECTION_MISMATCH, halt_lane, mismatch as selection_mismatch,
)
# The MLB lane's own selection and read-back helpers, REUSED rather than
# re-derived: the ambiguity rule, the mismatch test and "what actually bound"
# are the same questions about the same provider, and a second copy of them
# would be a second thing to keep correct.
from app.services.sgo_collection import (
    DEFERRED_EARLIER_GAME, DEFERRED_TIED_START, FallbackFetch,
    _ingested_game_ids as ingested_game_ids, _refuse, complete_request, eligible,
    parse_events, parse_main_markets, record_request, to_event_dtos,
    to_market_event_dtos, unambiguous_prefix,
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

#: THE PREVIEW PASS -- timed to Show Prep, not to a rolling horizon.
#:
#: The checkpoint window above answers "what is the price when the decision is
#: made". Show Prep needs a different answer at a different time: a preview
#: price that is still usable when the show board freezes, the day before the
#: slate, at `show_preview_freeze_time` (16:30 ET). Show Prep has its own preview
#: freshness, `show_preview_quote_max_age_hours` (18 h) -- distinct from, and
#: never a substitute for, the 240-minute rule a decision applies.
#:
#: The first design bought a game once it entered an 84-hour horizon. That
#: bought the Week 2 Sunday games at 00:09 on Friday, which made every one of
#: them 40 hours old -- unusable -- at Saturday's 16:30 freeze. It also missed
#: NYG @ LAR entirely: the game entered the horizon at 08:15, after that day's
#: allocation snapshot had already been issued at midnight, and a snapshot does
#: not grow mid-day.
#:
#: So the preview buy now lands in a fixed slot that ends at the freeze and
#: starts PREVIEW_LEAD before it (15:00-16:30 ET for a 16:30 freeze). It covers
#: `show_preview_market_collection_time` (16:00), so the board freezes on a
#: price minutes old rather than days. Because the slot is a fixed time on a
#: known day, the allocation snapshot for that day can count it at midnight.
#:
#: "Once" is structural: a game is due only if it has no moneyline observed
#: since its own slot opened, and the slot is ninety minutes long.
PREVIEW_LEAD = timedelta(minutes=90)
#: How far ahead to look for a game whose preview slot could be open.
PREVIEW_LOOKAHEAD = timedelta(hours=48)


def preview_slot(settings: Any, kickoff: datetime) -> tuple[datetime, datetime]:
    """The Show Prep preview window for a game: [freeze - lead, freeze].

    The slate is the game's own Eastern date; the freeze is Show Prep's planned
    freeze for that slate, which is the day before at the configured time.
    """

    from zoneinfo import ZoneInfo

    from app.show_preview.clock import planned_freeze_utc

    zone = ZoneInfo(getattr(settings, "show_preview_timezone", "America/New_York"))
    slate = _aware(kickoff).astimezone(zone).date()
    freeze = planned_freeze_utc(slate, settings)
    return freeze - PREVIEW_LEAD, freeze


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def checkpoint_window(kickoff: datetime) -> tuple[datetime, datetime]:
    """The checkpoint purchase window for a game: [kickoff-75m, kickoff-15m].

    Named so the checkpoint pass can answer "has this game been priced since
    its own window opened" with the same shape of question the preview pass
    asks about its slot.
    """

    starts = _aware(kickoff)
    return (starts - WINDOW_OPENS_BEFORE_KICKOFF,
            starts - WINDOW_CLOSES_BEFORE_KICKOFF)


def _newest_moneyline(session: Session, game_id: Any) -> datetime | None:
    """When this game's freshest persisted moneyline was observed."""

    from app.models import Market, MarketOutcome, MarketType, OddsSnapshot

    observed = session.scalar(
        select(OddsSnapshot.observed_at)
        .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
        .join(Market, Market.id == MarketOutcome.market_id)
        .where(Market.game_id == game_id, Market.market_type == MarketType.MONEYLINE)
        .order_by(OddsSnapshot.observed_at.desc()).limit(1)
    )
    return _aware(observed) if observed is not None else None


def _slate_events(
    session: Session, *, window_start: datetime, window_end: datetime,
) -> list[dict[str, Any]]:
    """Every canonical NFL game the request's window could return.

    This is what makes the ambiguity test real rather than nominal: it is not
    "the games we chose", it is "the games the provider may answer with". A
    selected game is safe only when every game that shares or precedes its
    kickoff inside this window was selected too.
    """

    rows = session.execute(
        select(Game.id, Game.starts_at)
        .join(League, League.id == Game.league_id)
        .where(League.code == "NFL",
               Game.status == GameStatus.SCHEDULED,
               Game.starts_at >= window_start,
               Game.starts_at <= window_end)
    ).all()
    return [{"game_id": str(game_id),
             "starts_at_utc": _aware(starts_at).isoformat()}
            for game_id, starts_at in rows]


def due_games(
    session: Session, *, now: datetime | None = None, settings: Any = None,
) -> list[dict[str, Any]]:
    """NFL games worth an object right now: checkpoint first, then preview.

    CHECKPOINT: inside the final 75-15 minutes, for the price a decision uses,
    and not yet priced since that window opened.
    PREVIEW: inside its Show Prep slot and not yet priced since the slot opened.

    ONE BUY PER WINDOW, ON BOTH PASSES
    ----------------------------------
    The "already priced" test used to guard the preview pass only. The
    checkpoint window is sixty minutes wide and the collector ticks every
    fifteen, so a checkpoint game was re-offered on every tick inside its own
    window and bought again each time -- the same price, from the same lane,
    for the same decision.

    On a tied NFL Sunday that is not a rounding error. Week 3's eight 13:00 ET
    kickoffs share one window: the first tick bought all eight, and the second
    tick spent every remaining object in the day re-buying seven of them. The
    16:05, 16:25 and 20:20 games, and the next day's Show Prep preview, then had
    no objects left and went unpriced -- the lane's whole allowance consumed
    about an hour into a fourteen-game slate, on prices it already held.

    So both passes ask the same question against their own window: has a
    moneyline been observed for this game since this window opened? The
    checkpoint window sits far inside the 240-minute freshness limit the
    decision applies (a quote bought when the window opens is 60-75 minutes old
    at the checkpoint), so one buy per window is a quote that is genuinely fresh
    where it is used -- and a game that really has no price yet, because its
    purchase failed or never happened, is still due on the next tick.

    The test reads the newest moneyline from ANY source, exactly as the preview
    pass has always read it: the producer's freshness rule is source-agnostic,
    so a game the governed provider has already priced inside this window does
    not need a research object spent on it too.
    """

    if settings is None:
        from app.core.config import get_settings

        settings = get_settings()
    moment = now or datetime.now(UTC)
    rows = session.execute(
        select(Game.id, Game.starts_at)
        .join(League, League.id == Game.league_id)
        .where(League.code == "NFL",
               Game.status == GameStatus.SCHEDULED,
               Game.starts_at > moment,
               Game.starts_at <= moment + PREVIEW_LOOKAHEAD)
        .order_by(Game.starts_at)
    ).all()
    checkpoint: list[dict[str, Any]] = []
    preview: list[dict[str, Any]] = []
    for game_id, starts_at in rows:
        starts = _aware(starts_at)
        to_kickoff = starts - moment
        if to_kickoff < WINDOW_CLOSES_BEFORE_KICKOFF:
            continue  # inside the final 15 minutes: too late to be useful
        in_checkpoint = to_kickoff <= WINDOW_OPENS_BEFORE_KICKOFF
        if in_checkpoint:
            opens, _closes = checkpoint_window(starts)
        else:
            opens, closes = preview_slot(settings, starts)
            if not (opens <= moment <= closes):
                continue  # outside its Show Prep slot
        # ONE BUY PER WINDOW, whichever window this is.
        newest = _newest_moneyline(session, game_id)
        if newest is not None and newest >= opens:
            continue  # already priced inside this window; one buy is the budget
        sides = dict(session.execute(
            select(GameTeam.is_home, Team.abbreviation)
            .join(Team, Team.id == GameTeam.team_id)
            .where(GameTeam.game_id == game_id)
        ).all())
        entry = {
            "game_id": str(game_id),
            "starts_at": starts.isoformat(),
            "minutes_to_kickoff": round(to_kickoff.total_seconds() / 60, 1),
            "away": sides.get(False), "home": sides.get(True),
            "pass": "CHECKPOINT" if in_checkpoint else "PREVIEW",
        }
        (checkpoint if in_checkpoint else preview).append(entry)
    # Checkpoint games come first: if a grant cannot cover everything, the games
    # with a decision to make are the ones that get paid for.
    return checkpoint + preview


def fetch_nfl_pregame(
    settings: Settings, *, limit: int = 1, now: datetime | None = None,
    starts_within_hours: int = 2, request_fingerprint: str | None = None,
    starts_after: datetime | None = None,
) -> FallbackFetch:
    """Exactly one request for upcoming NFL events. No retry, no pagination.

    ``starts_after`` carries the SELECTION into the request, as the MLB lane
    does. A bare "from now" window hands back the earliest events in it, which
    is not the same set as the games the objects were reserved for: on a slate
    with an earlier kickoff still in the window, the object lands on the earlier
    game and the selected one stays unpriced. Anchoring just below the earliest
    SELECTED kickoff excludes every game the rule did not choose. The default
    keeps the old "from now" behaviour for any caller that does not choose.
    """

    ok, reason = eligible(settings, now=now)
    if not ok:
        raise _refuse(f"SportsGameOdds is not eligible: {reason}")
    moment = now or datetime.now(UTC)
    # Never moved backwards past the retrieval moment: a game already under way
    # is not pregame, whatever the selection asked for.
    anchor = max(_aware(starts_after), moment) if starts_after else moment
    params = {
        "leagueID": "NFL",
        "limit": str(max(1, int(limit))),
        "startsAfter": anchor.strftime("%Y-%m-%dT%H:%M:%SZ"),
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
    # The exact bytes, kept before parsing (never fails, retries or waits).
    from app.services.sgo_raw_archive import archive_response

    raw_archive = archive_response(settings, response.content, requested_at=moment,
                                   request_fingerprint=request_fingerprint, lane=LANE,
                                   league="NFL", filters=params)
    payload = response.json()
    parse = parse_events(payload, received_at=moment, now=moment, league_id="NFL")
    # The same bytes read for spreads and totals. The provider bills the EVENT,
    # so every market already inside that response is free once it is paid for.
    main = parse_main_markets(payload, received_at=moment, now=moment, league_id="NFL")
    return FallbackFetch(
        requested_at=moment,
        objects=len(payload.get("data") or []),
        pairs=parse.pairs, parse=parse, main=main,
        raw_archive=raw_archive,
    )


def collect_nfl_pregame(
    session: Session, settings: Settings, *,
    now: datetime | None = None, job_run_id: Any = None,
) -> dict[str, Any]:
    """Reserve, fetch and ingest one object per selected game. One request or none.

    The order of refusals is the order of costs: a latched lane, then nothing
    due, then the provider, then the allocation, then the reserve guard, then an
    ambiguous selection -- each one cheaper to discover than the object it
    saves. Only after all six does anything reach the network, exactly once.
    """

    from app.services.collection_halt import active_halt
    from app.services.nfl_shadow_odds_service import NFLShadowOddsService
    from app.services.provider_allocation import request_objects
    from app.services.sgo_collection import object_budget

    moment = now or datetime.now(UTC)

    # A LATCHED LANE SPENDS NOTHING, AND IS NOT RETRIED.
    #
    # Checked before anything else, because a halt is not a rate limit: it means
    # the last object bought an answer to a question this lane did not ask, and
    # the owner has not yet decided whether the provider can be trusted to
    # answer it. Nothing here lifts it. A halt on this lane never touches MLB's,
    # exactly as MLB's never touched this one.
    halt = active_halt(session, lane=LANE)
    if halt is not None:
        return {"attempted": False, "reason": "LANE_HALTED", "objects": 0,
                "lane": LANE,
                "halt": {"lane": halt.lane, "halt_reason": halt.reason,
                         "halted_at": _aware(halt.halted_at).isoformat(),
                         "detail": halt.detail, "evidence": halt.evidence},
                "detail": ("This lane is stopped pending an owner decision. No "
                           "object is spent and no retry is attempted.")}

    # The collector's own settings, not a global read: the preview slot is
    # computed from them, and a job that was handed a Settings should not go
    # looking for a different one.
    due = due_games(session, now=moment, settings=settings)
    if not due:
        return {"attempted": False, "reason": "NO_NFL_GAME_IN_THE_COLLECTION_WINDOW",
                "objects": 0, "window_opens_minutes_before_kickoff":
                    int(WINDOW_OPENS_BEFORE_KICKOFF.total_seconds() // 60)}

    ok, reason = eligible(settings, now=moment)
    if not ok:
        return {"attempted": False, "reason": "PROVIDER_NOT_ELIGIBLE",
                "objects": 0, "detail": reason, "games_due": due}

    # One object per due game, in ONE request. The provider bills the event
    # object, not the call, so asking for the whole cluster at once costs
    # exactly what asking for them one at a time would have cost -- and a
    # fifteen-game Sunday is no longer fifteen separate chances to be starved
    # by a lane that has already spent its day.
    wanted = len(due)
    grant = request_objects(session, lane=LANE, wanted=wanted, now=moment)
    if not grant.allowed:
        # Named, never borrowed from another lane.
        return {"attempted": False, "reason": f"ALLOCATION_{grant.reason}",
                "objects": 0, "lane": LANE, "allocation": grant.as_dict(),
                "games_due": due}

    # A partial grant is spent, not refused: covering nine of fifteen games is
    # nine games that can be priced, and the rest are named in the receipt.
    granted = max(0, int(getattr(grant, "granted", wanted) or 0)) or wanted
    granted = min(granted, wanted)
    budget = object_budget(session, now=moment)
    allowed, refusal = budget.may_spend(granted)
    if not allowed:
        return {"attempted": False, "reason": "OBJECT_RESERVE_GUARD",
                "objects": 0, "detail": refusal, "budget": budget.as_dict()}

    # WHAT THE GRANT PAYS FOR, IN THE ORDER `due_games` DECLARED.
    # Checkpoint games first, so a short day funds the decisions before the show.
    selected, beyond_grant = due[:granted], due[granted:]

    # The horizon has to reach the furthest game we are paying for, or the
    # provider filters out the very events the objects were reserved for. It is
    # measured over every DUE game, not just the funded prefix, so the window a
    # selection is judged against is the window the request will actually carry.
    horizon = max(2, int(max(entry["minutes_to_kickoff"] for entry in due) // 60) + 2)
    window_end = moment + timedelta(hours=horizon)

    # THE CHOICE, EXPRESSED AS A FILTER -- the MLB lane's rule, reused.
    #
    # `startsAfter` anchored one second below the earliest SELECTED kickoff
    # excludes every game the ordering ranked lower, so an object reserved for
    # a 16:05 game cannot land on a 16:25 one the provider happened to return
    # first. Never moved back past `moment`: a game under way is not pregame.
    anchor = max(
        min(_aware(datetime.fromisoformat(entry["starts_at"])) for entry in selected)
        - timedelta(seconds=1),
        moment,
    )

    # NEVER A REQUEST THE PROVIDER MAY ANSWER WITH A DIFFERENT GAME.
    #
    # The window filter plus a limit is answered with the EARLIEST events in the
    # window, and ties at one kickoff come back in the provider's own order. NFL
    # is the league where that matters most: Week 3 Sunday has eight games at
    # 13:00 ET, so a grant that cannot fund the whole tied group names games the
    # provider is free to substitute for each other.
    #
    # `unambiguous_prefix` (the MLB lane's own helper) keeps only the leading
    # start-time groups that are WHOLLY selected and defers the rest BY NAME. No
    # request is sent for a deferred game, so no object is spent on one, and the
    # next tick -- by when the grant, the slate or the tie has moved -- asks
    # again. AISP holds a SportsGameOdds event id for these games, but naming
    # events by id is governed by `sgo_exact_event_selection_mode`, whose
    # documented scope is the MLB fallback and whose VALIDATE state carries a
    # single owner-authorised discriminating test recorded in the MLB lane's own
    # ledger. Widening either is an owner decision, not a repair, so this lane
    # defers instead.
    slate = _slate_events(session, window_start=anchor, window_end=window_end)
    selected, deferred = unambiguous_prefix(
        [{"game_id": entry["game_id"],
          "starts_at_utc": _aware(datetime.fromisoformat(entry["starts_at"])).isoformat()}
         for entry in selected],
        slate, window_start=anchor, window_end=window_end, moment=moment)
    selected_ids = [str(entry["game_id"]) for entry in selected]
    if not selected_ids:
        return {"attempted": False, "reason": "DEFERRED_AMBIGUOUS_SELECTION",
                "objects": 0, "lane": LANE,
                "games_due": due, "games_due_count": len(due),
                "games_deferred": deferred,
                # Nothing bound, so every due game is still without a price --
                # measured the same way the collected receipt measures it.
                "games_not_funded": [entry["game_id"] for entry in due],
                "games_beyond_grant": [entry["game_id"] for entry in beyond_grant],
                "allocation": grant.as_dict(),
                "detail": ("Every selected game shares its request window with an "
                           "unselected game the provider could return instead. No "
                           "request was sent; no object was spent.")}
    wanted = len(selected_ids)

    reservation = record_request(
        session, requested_at=moment, params={"leagueID": "NFL", "limit": str(wanted)},
        budget=budget, job_run_id=job_run_id, objects_requested=wanted,
        endpoint_key=LANE,
    )
    session.commit()

    try:
        fetch = fetch_nfl_pregame(settings, limit=wanted, now=moment,
                                  starts_within_hours=horizon, starts_after=anchor,
                                  request_fingerprint=getattr(reservation, "request_fingerprint", None))
    except Exception as error:  # one attempt; the schedule owns the next one
        complete_request(session, reservation, objects=0, succeeded=False,
                         budget=budget, error=type(error).__name__,
                         job_run_id=job_run_id)
        session.commit()
        return {"attempted": True, "reason": "REQUEST_FAILED", "objects": 0,
                "error_type": type(error).__name__, "games_due": due,
                "games_selected": selected_ids,
                **({"games_deferred": deferred} if deferred else {})}

    # Explicit franchise mapping, never a name-and-date join -- and stamped with
    # the provider that actually supplied it. SportsGameOdds is a RESEARCH
    # provider in the registry, so its books land under the `sgo:` namespace and
    # its snapshots carry SPORTSGAMEODDS provenance. Writing them as
    # THE_ODDS_API would have merged two providers' "draftkings" into one
    # Sportsbook row, and the governed two-sided pairing keys on sportsbook_id.
    service = NFLShadowOddsService(session, source=SOURCE)
    result = service.ingest(to_event_dtos(fetch.pairs),
                            available_as_of=fetch.requested_at)
    # Spreads and totals from THE SAME event objects that were just paid for.
    # They were parsed and then thrown away, so a governed game showed a
    # moneyline and nothing else while the response on disk held eight books of
    # spreads and seven of totals. This buys nothing new: no extra request, no
    # extra object, the same franchise-map binding, and the same per-outcome
    # duplicate suppression. Each side keeps its own book's line, so an integer
    # spread or total stays an integer and still pushes.
    market_result = {"events": 0, "mapped_events": 0, "quotes": 0,
                     "duplicates": 0, "rejected_events": 0}
    if fetch.main is not None:
        market_events = to_market_event_dtos(fetch.main.quotes)
        if market_events:
            market_result = service.ingest(market_events,
                                           available_as_of=fetch.requested_at)
    raw_archive = getattr(fetch, "raw_archive", None)

    # WHAT THE OBJECTS ACTUALLY BOUGHT, read back from the binding the franchise
    # map made -- never from the events we hoped for. An event the provider
    # substituted, or one that failed to bind, cannot appear here.
    ingested = ingested_game_ids(session, fetch)
    # The mismatch is decided BEFORE the terminal row, so the receipt can say
    # whether this purchase halted the lane.
    divergence = selection_mismatch(selected_ids, ingested)

    complete_request(session, reservation, objects=fetch.objects, succeeded=True,
                     budget=budget, job_run_id=job_run_id,
                     extra_metadata={"raw_archive": raw_archive} if raw_archive is not None else None)

    # THE FIRST MISMATCH STOPS THE LANE.
    #
    # The request names the games it wants, so a response carrying a game that
    # was never selected means an object was charged for a question nobody
    # asked. Retrying spends a second object to re-ask a question the provider
    # has just declined to answer, so nothing is retried and the lane latches
    # shut on the single occurrence.
    #
    # The rows already written stay: they are real prices from a real response,
    # and deleting them would destroy the evidence of what happened. The
    # completed reservation stays too -- the object WAS charged. Nothing in this
    # lane lifts a halt; that judgement belongs to the owner.
    halted = None
    if divergence is not None:
        halted = halt_lane(
            session, lane=LANE, reason=SELECTION_MISMATCH,
            evidence={"request_filters": {"leagueID": "NFL", "limit": str(wanted),
                                          "startsAfter": anchor.isoformat(),
                                          "startsBefore": window_end.isoformat()},
                      "mismatch": divergence,
                      "objects_charged": fetch.objects,
                      "games_selected": sorted(selected_ids),
                      "requested_at": moment.isoformat()},
            detail=("The provider returned an NFL game this request did not "
                    "select. The lane is stopped after this single occurrence; "
                    "no object was spent on a retry."),
            now=moment,
        )
    session.commit()
    return {
        "attempted": True, "reason": "COLLECTED", "lane": LANE,
        "objects": fetch.objects,
        "raw_archive": raw_archive,
        "objects_reserved": wanted,
        "games_due": due,
        "games_due_count": len(due),
        # WHAT WAS CHOSEN, AND WHAT THE OBJECTS ACTUALLY BOUGHT.
        "games_selected": selected_ids,
        "games_ingested": ingested,
        "selection_reached_provider": divergence is None,
        "lane_halted": halted is not None,
        "selection_mismatch": divergence,
        # Named, never silently dropped: which due games still have no price
        # from this purchase. Measured from what BOUND, not from a slice of the
        # due list -- with tied kickoffs the provider's own ordering decides
        # which of the funded games came back, so a positional slice named the
        # wrong games and reported re-bought prices as new coverage.
        "games_not_funded": [entry["game_id"] for entry in due
                             if entry["game_id"] not in set(ingested)],
        # The two reasons a due game can be missing, kept apart.
        "games_beyond_grant": [entry["game_id"] for entry in beyond_grant],
        **({"games_deferred": deferred} if deferred else {}),
        "events": result["events"], "mapped_events": result["mapped_events"],
        "quotes": result["quotes"], "duplicates": result["duplicates"],
        "rejected_events": result["rejected_events"],
        # Spread and total rows from the same objects, reported separately so
        # the receipt shows the extra coverage arrived for zero extra cost.
        "market_quotes": market_result["quotes"],
        "market_duplicates": market_result["duplicates"],
        "market_events_rejected": market_result["rejected_events"],
        "source": SOURCE,
        "provider_governed": False,
        "bookmakers": sorted({pair.bookmaker_key for pair in fetch.pairs}),
        "main_markets": (fetch.main.by_market() if fetch.main else {}),
        "allocation": grant.as_dict(),
    }


__all__ = ["DEFERRED_EARLIER_GAME", "DEFERRED_TIED_START", "LANE", "PREVIEW_LEAD",
           "PREVIEW_LOOKAHEAD", "SOURCE", "checkpoint_window",
           "preview_slot", "WINDOW_CLOSES_BEFORE_KICKOFF",
           "WINDOW_OPENS_BEFORE_KICKOFF", "collect_nfl_pregame", "due_games",
           "fetch_nfl_pregame"]

"""Day-before preview sportsbook collection: complete, and switched off.

WHAT THIS IS
------------
The 4:00 PM ET step of the show-production clock, written out in full so that
turning it on is a switch and an owner decision rather than a build. Every
guard, every refusal and every persistence field is here. Nothing in it runs in
the deployed state.

THE THREE THINGS THAT KEEP IT DARK
----------------------------------
1. **Two switches.** ``show_preview_enabled`` AND
   ``show_preview_provider_collection_enabled`` must both be true. The deployed
   state has the second false and there is no path that infers it.
2. **Its own unfunded lane.** ``sgo_show_preview_events`` is declared in the
   allocation table with ``funded=False`` and a zero share, so the four existing
   lanes' allowances are arithmetically unchanged and ``request_objects``
   refuses it by name with ``LANE_NOT_FUNDED``. Preview collection can never
   take an object from MLB or NFL final-pregame coverage, because it has no
   claim on the daily total at all until the owner funds one.
3. **A kill switch.** ``sgo_show_preview_events`` is a halt-able lane. A halt
   latches the lane shut and nothing here lifts it.

WHY A SEPARATE LANE RATHER THAN A WIDER WINDOW ON THE EXISTING ONES
-------------------------------------------------------------------
A day-before quote and a checkpoint quote are different purchases. The
checkpoint quote has to be fresh at the checkpoint -- that is the whole design
of ``sgo_nfl_pregame`` -- and a preview quote is deliberately old by the time
anyone sees it. Buying them from one allowance would let the show quietly eat
the coverage that funded decisions depend on, and the first symptom would be a
missing price on a decision, not a missing price on the show.

WHAT IT BUYS
------------
One event object per game, which carries the moneyline, the spread/run line and
the total in the same payload. The provider bills the EVENT, so the three
required markets cost one object between them, not three.

WHAT IT REFUSES TO GUESS
------------------------
SportsGameOdds' league identifiers for NBA and College Football are not in any
retained evidence AISP holds: every SportsGameOdds request ever made carried
``leagueID=MLB`` (620 rows) or ``leagueID=NFL`` (16 rows). A plausible-looking
identifier is a request that spends an object to be told nothing, so those two
sports are refused by name here rather than attempted. College Football also has
no canonical game identity in AISP, so a returned quote could not be bound to a
game even if the identifier were known.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.core.config import Settings

#: The preview lane. Declared unfunded in the allocation table; never
#: ``sgo_mlb_events`` or ``sgo_nfl_events``, which pay for decisions.
PREVIEW_LANE = "sgo_show_preview_events"

#: The provider these quotes would come from. The registry classifies it as
#: research rather than governed, and that classification travels with the rows.
SOURCE = "SPORTSGAMEODDS"

#: The markets the Show Board reads. All three arrive inside one event object,
#: so this is a list of what is kept, not a list of what is bought.
REQUIRED_MARKETS = ("MONEYLINE", "SPREAD", "TOTAL")

#: The most objects one preview run may spend in one show day, whatever the
#: lane allowance eventually becomes. A second, independent ceiling: the point
#: of a cap is that it still holds when the thing above it is misconfigured.
DAILY_OBJECT_CAP = 12

#: Preview collection never draws the protected reserve down below this, even
#: if a future allocation would permit it. Show research is the least urgent
#: call on the account.
PREVIEW_RESERVE_FLOOR = 400

#: SportsGameOdds league identifiers AISP has actually observed in its own
#: retained request evidence. Nothing is added here without evidence.
VERIFIED_LEAGUE_IDS: dict[str, str] = {"MLB": "MLB", "NFL": "NFL"}

#: Why the other two show sports cannot be requested. These are the same
#: blockers the sport-readiness view already reports, stated where the spend
#: would happen.
UNVERIFIED_LEAGUES: dict[str, str] = {
    "NBA": "SGO_LEAGUE_IDENTITY_UNVERIFIED",
    "CFB": "SGO_LEAGUE_IDENTITY_UNVERIFIED;NO_CANONICAL_GAME_IDENTITY",
}

# -- refusals, each named so a receipt says which guard stopped it -----------

#: Kept under its original name: callers and tests already read it.
DISABLED = "SHOW_PREVIEW_PROVIDER_COLLECTION_DISABLED"
#: Retained so a caller pinned to V1's answer still gets a truthful one.
NOT_IMPLEMENTED = "SHOW_PREVIEW_PROVIDER_COLLECTION_NOT_AUTHORIZED_IN_V1"
NO_SESSION = "SHOW_PREVIEW_COLLECTION_NEEDS_A_SESSION"
LANE_UNFUNDED = "SHOW_PREVIEW_LANE_NOT_FUNDED"
LANE_HALTED = "SHOW_PREVIEW_LANE_HALTED"
PROVIDER_NOT_ELIGIBLE = "SHOW_PREVIEW_PROVIDER_NOT_ELIGIBLE"
DAILY_CAP_REACHED = "SHOW_PREVIEW_DAILY_OBJECT_CAP_REACHED"
RESERVE_GUARD = "SHOW_PREVIEW_OBJECT_RESERVE_GUARD"
BUDGET_GUARD = "SHOW_PREVIEW_OBJECT_BUDGET_GUARD"
LEAGUE_UNVERIFIED = "SHOW_PREVIEW_LEAGUE_IDENTITY_UNVERIFIED"
NO_GAMES = "SHOW_PREVIEW_NO_GAMES_ON_SLATE"
ALREADY_HELD = "SHOW_PREVIEW_MARKETS_ALREADY_COLLECTED"
REQUEST_FAILED = "SHOW_PREVIEW_REQUEST_FAILED"
COLLECTED = "SHOW_PREVIEW_COLLECTED"
DRY_RUN = "SHOW_PREVIEW_DRY_RUN"

#: A halt reason of this lane's own, distinct from the MLB selection mismatch.
KILL_SWITCH = "SHOW_PREVIEW_COLLECTION_STOPPED"


def switches(settings: Settings) -> dict[str, Any]:
    """The three flags and the one conclusion drawn from them."""

    main = bool(getattr(settings, "show_preview_enabled", False))
    collection = bool(getattr(settings, "show_preview_provider_collection_enabled", False))
    return {
        "show_preview_enabled": main,
        "show_preview_provider_collection_enabled": collection,
        "show_preview_auto_freeze_enabled": bool(getattr(settings, "show_preview_auto_freeze_enabled", False)),
        "collection_permitted": main and collection,
    }


def league_identity(sport: str) -> tuple[str | None, str | None]:
    """The provider's league identifier for a show sport, or why there is none."""

    code = sport.upper()
    if code in VERIFIED_LEAGUE_IDS:
        return VERIFIED_LEAGUE_IDS[code], None
    return None, UNVERIFIED_LEAGUES.get(code, "SGO_LEAGUE_IDENTITY_UNVERIFIED")


@dataclass
class PreviewTarget:
    """One game the preview run would like a price for, and its current state."""

    sport: str
    game_id: str | None
    source_game_ref: str
    kickoff_utc: str
    away: str | None
    home: str | None
    has_preview_forecast: bool
    complete_markets: list[str] = field(default_factory=list)
    missing_markets: list[str] = field(default_factory=list)
    blocker: str | None = None

    @property
    def wanted(self) -> bool:
        return self.blocker is None and bool(self.missing_markets)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sport": self.sport, "game_id": self.game_id,
            "source_game_ref": self.source_game_ref, "kickoff_utc": self.kickoff_utc,
            "away": self.away, "home": self.home,
            "has_preview_forecast": self.has_preview_forecast,
            "complete_markets": list(self.complete_markets),
            "missing_markets": list(self.missing_markets),
            "blocker": self.blocker, "would_request": self.wanted,
        }


@dataclass
class PreviewPlan:
    """What a run would do, computed without writing or requesting anything."""

    slate_date: str
    planned_at_utc: str
    permitted: bool
    refusal: str | None
    targets: list[PreviewTarget] = field(default_factory=list)
    sports_refused: dict[str, str] = field(default_factory=dict)
    objects_wanted: int = 0
    objects_grantable: int = 0
    daily_cap: int = DAILY_OBJECT_CAP
    objects_spent_today: int = 0
    lane: str = PREVIEW_LANE
    budget: dict[str, Any] = field(default_factory=dict)
    allocation: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "slate_date": self.slate_date, "planned_at_utc": self.planned_at_utc,
            "permitted": self.permitted, "refusal": self.refusal,
            "lane": self.lane, "objects_wanted": self.objects_wanted,
            "objects_grantable": self.objects_grantable,
            "daily_object_cap": self.daily_cap,
            "objects_spent_today": self.objects_spent_today,
            "reserve_floor": PREVIEW_RESERVE_FLOOR,
            "required_markets": list(REQUIRED_MARKETS),
            "sports_refused": dict(self.sports_refused),
            "targets": [target.as_dict() for target in self.targets],
            "budget": dict(self.budget), "allocation": dict(self.allocation),
            "notes": list(self.notes),
            "spends_nothing": True,
        }


def _aware(value: Any) -> datetime:
    """The same instant, in UTC. A field named ``_utc`` has to actually be UTC.

    The database hands back Eastern-offset datetimes, which are the right
    instant and the wrong label; printing one under a ``kickoff_utc`` key is a
    small lie that becomes a four-hour one as soon as anybody does arithmetic
    on the string.
    """

    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _slate_bounds(slate_date: date, settings: Settings) -> tuple[datetime, datetime]:
    """The slate day in the show's own timezone, as a UTC half-open interval."""

    from zoneinfo import ZoneInfo

    zone = ZoneInfo(getattr(settings, "show_preview_timezone", "America/New_York"))
    start = datetime.combine(slate_date, datetime.min.time(), zone).astimezone(UTC)
    return start, start + timedelta(days=1)


def _existing_markets(session: Any, game_ids: list[Any], as_of: datetime) -> dict[Any, set[str]]:
    """Which required markets already have a COMPLETE same-book two-sided pair.

    This is what makes a second run idempotent. A pair is only complete when
    both legs come from the SAME sportsbook row and the SAME provider -- the
    governed pairing rule -- so a moneyline with one leg from one book and one
    from another does not count as held, and the game is requested again.
    """

    from collections import defaultdict

    from sqlalchemy import select

    from app.models import Market, MarketOutcome, OddsSnapshot

    held: dict[Any, set[str]] = defaultdict(set)
    if not game_ids:
        return held
    rows = session.execute(
        select(Market.game_id, Market.market_type, Market.id, OddsSnapshot.sportsbook_id,
               OddsSnapshot.source, MarketOutcome.outcome_key, OddsSnapshot.decimal_odds)
        .join(MarketOutcome, MarketOutcome.market_id == Market.id)
        .join(OddsSnapshot, OddsSnapshot.market_outcome_id == MarketOutcome.id)
        .where(Market.game_id.in_(game_ids), OddsSnapshot.source_mode == "LIVE",
               OddsSnapshot.observed_at <= as_of, OddsSnapshot.available_as_of <= as_of)
    ).all()
    sides: dict[tuple, set[str]] = defaultdict(set)
    for game_id, market_type, market_id, book_id, source, outcome_key, decimal_odds in rows:
        kind = _market_name(market_type)
        if kind is None or not decimal_odds or float(decimal_odds) <= 1:
            continue
        # Same book, same provider, same market row: anything looser would
        # count two half-markets as one.
        sides[(game_id, kind, market_id, book_id, str(source))].add(str(outcome_key).upper())
    for (game_id, kind, _market_id, _book_id, _source), seen in sides.items():
        if len(seen) >= 2:
            held[game_id].add(kind)
    return held


def _market_name(market_type: Any) -> str | None:
    raw = str(getattr(market_type, "value", market_type)).lower()
    if raw.endswith("h2h") or "moneyline" in raw:
        return "MONEYLINE"
    if "spread" in raw:
        return "SPREAD"
    if "total" in raw:
        return "TOTAL"
    return None


def preview_targets(session: Any, settings: Settings, *, slate_date: date,
                    now: datetime) -> tuple[list[PreviewTarget], dict[str, str]]:
    """Every show-sport game on the slate, with its blocker or its gap.

    Reads the registered schedule and the preview forecasts AISP already holds.
    Makes no request and writes nothing.
    """

    from sqlalchemy import select

    from app.models import Game, GameStatus, GameTeam, League, Team
    from app.models.show_preview import ShowPreviewForecast

    lo, hi = _slate_bounds(slate_date, settings)
    rows = session.execute(
        select(Game.id, Game.starts_at, League.code)
        .join(League, League.id == Game.league_id)
        .where(League.code.in_(tuple(VERIFIED_LEAGUE_IDS) + tuple(UNVERIFIED_LEAGUES)),
               Game.status == GameStatus.SCHEDULED,
               Game.starts_at >= lo, Game.starts_at < hi)
        .order_by(Game.starts_at)
    ).all()

    game_ids = [row[0] for row in rows]
    held = _existing_markets(session, game_ids, now)
    sides: dict[Any, dict[bool, str]] = {}
    if game_ids:
        for game_id, is_home, abbreviation in session.execute(
            select(GameTeam.game_id, GameTeam.is_home, Team.abbreviation)
            .join(Team, Team.id == GameTeam.team_id)
            .where(GameTeam.game_id.in_(game_ids))
        ).all():
            sides.setdefault(game_id, {})[bool(is_home)] = abbreviation

    forecasts = {
        str(row.canonical_game_id) for row in session.scalars(
            select(ShowPreviewForecast).where(ShowPreviewForecast.slate_date == slate_date)
        ) if row.canonical_game_id is not None
    }

    refused: dict[str, str] = {}
    targets: list[PreviewTarget] = []
    for game_id, starts_at, sport in rows:
        _league, blocker = league_identity(sport)
        if blocker:
            refused[sport] = blocker
        complete = sorted(held.get(game_id, set()))
        targets.append(PreviewTarget(
            sport=sport, game_id=str(game_id), source_game_ref=str(game_id),
            kickoff_utc=_aware(starts_at).isoformat(),
            away=sides.get(game_id, {}).get(False), home=sides.get(game_id, {}).get(True),
            has_preview_forecast=str(game_id) in forecasts,
            complete_markets=complete,
            missing_markets=[m for m in REQUIRED_MARKETS if m not in complete],
            blocker=blocker,
        ))
    # A sport with no registered schedule row still has to say why it is absent,
    # or a reader cannot tell "no games tonight" from "cannot ask".
    for sport, blocker in UNVERIFIED_LEAGUES.items():
        refused.setdefault(sport, blocker)
    return targets, refused


def _objects_spent_today(session: Any, now: datetime) -> int:
    """What this lane has already spent on the current Eastern service day."""

    from sqlalchemy import func, select

    from app.models import ProviderRequestLedger
    from app.services.service_day import service_day_start

    midnight = service_day_start(now)
    settled = session.scalar(
        select(func.coalesce(func.sum(ProviderRequestLedger.records_received), 0)).where(
            ProviderRequestLedger.endpoint_key == PREVIEW_LANE,
            ProviderRequestLedger.status == "SUCCEEDED",
            ProviderRequestLedger.requested_at >= midnight)) or 0
    # In flight counts against the cap too, or two runs claim the same objects.
    terminal = select(ProviderRequestLedger.reservation_id).where(
        ProviderRequestLedger.reservation_id.is_not(None))
    pending = session.scalar(
        select(func.coalesce(func.sum(ProviderRequestLedger.quota_cost), 0)).where(
            ProviderRequestLedger.endpoint_key == PREVIEW_LANE,
            ProviderRequestLedger.status == "RESERVED",
            ProviderRequestLedger.requested_at >= midnight,
            ProviderRequestLedger.id.not_in(terminal))) or 0
    return int(settled) + int(pending)


def plan_collection(session: Any, settings: Settings, *, slate_date: date,
                    now: datetime | None = None) -> PreviewPlan:
    """The dry-run planner. Reads, decides, and spends nothing.

    Safe to call in the deployed state: it answers "what would happen" without
    needing either switch, so the answer can be inspected before the switch is
    ever thrown.
    """

    from app.services.provider_allocation import request_objects
    from app.services.sgo_collection import eligible, object_budget

    moment = now or datetime.now(UTC)
    flags = switches(settings)
    plan = PreviewPlan(
        slate_date=slate_date.isoformat(), planned_at_utc=moment.isoformat(),
        permitted=flags["collection_permitted"], refusal=None)
    plan.notes.append(
        "A dry run. It reserves nothing, requests nothing and writes nothing.")

    targets, refused = preview_targets(session, settings, slate_date=slate_date, now=moment)
    plan.targets = targets
    plan.sports_refused = refused
    plan.objects_wanted = sum(1 for target in targets if target.wanted)
    plan.objects_spent_today = _objects_spent_today(session, moment)

    budget = object_budget(session, now=moment)
    plan.budget = budget.as_dict()
    grant = request_objects(session, lane=PREVIEW_LANE, wanted=max(1, plan.objects_wanted), now=moment)
    plan.allocation = grant.as_dict()

    room_under_cap = max(0, DAILY_OBJECT_CAP - plan.objects_spent_today)
    plan.objects_grantable = min(plan.objects_wanted, grant.granted, room_under_cap)

    if not flags["collection_permitted"]:
        plan.refusal = DISABLED
    elif not targets:
        plan.refusal = NO_GAMES
    elif plan.objects_wanted == 0:
        plan.refusal = ALREADY_HELD
    elif grant.granted == 0:
        plan.refusal = LANE_UNFUNDED if grant.reason == "LANE_NOT_FUNDED" else f"ALLOCATION_{grant.reason}"
    elif room_under_cap == 0:
        plan.refusal = DAILY_CAP_REACHED
    else:
        ok, reason = eligible(settings, now=moment)
        if not ok:
            plan.refusal = PROVIDER_NOT_ELIGIBLE
            plan.notes.append(reason)
        elif budget.allowance - budget.objects_used - PREVIEW_RESERVE_FLOOR < plan.objects_grantable:
            plan.refusal = RESERVE_GUARD
    if plan.refusal is not None:
        plan.objects_grantable = 0
    return plan


def collect_preview_markets(settings: Settings, *, session: Any = None,
                            slate_date: date | None = None, now: datetime | None = None,
                            job_run_id: Any = None, dry_run: bool = False) -> dict[str, Any]:
    """Collect day-before markets for one slate, or say precisely why not.

    Called with settings alone -- the V1 signature -- it answers from the
    switches and never touches a database. Every guard below is checked in
    order, and the FIRST one to refuse is the one reported, so a receipt names
    the binding constraint rather than the last one evaluated.
    """

    flags = switches(settings)
    base = {"attempted": False, "objects": 0, "reservations": 0, "lane": PREVIEW_LANE,
            "switches": flags}
    if not flags["collection_permitted"]:
        return {**base, "reason": DISABLED}
    if session is None:
        return {**base, "reason": NO_SESSION,
                "detail": "Collection reads the schedule and writes a ledger reservation; it needs a session."}

    from app.services.collection_halt import active_halt

    moment = now or datetime.now(UTC)
    if slate_date is None:
        from app.show_preview.clock import next_relevant_slate_date
        slate_date = next_relevant_slate_date(session, moment, settings)

    halt = active_halt(session, lane=PREVIEW_LANE)
    if halt is not None:
        return {**base, "reason": LANE_HALTED, "slate_date": slate_date.isoformat(),
                "detail": halt.detail or halt.reason, "halted_at": _aware(halt.halted_at).isoformat()}

    plan = plan_collection(session, settings, slate_date=slate_date, now=moment)
    if dry_run:
        return {**base, "reason": DRY_RUN, "slate_date": slate_date.isoformat(),
                "plan": plan.as_dict()}
    if plan.refusal is not None:
        return {**base, "reason": plan.refusal, "slate_date": slate_date.isoformat(),
                "plan": plan.as_dict()}

    return _execute(session, settings, plan, slate_date=slate_date, now=moment, job_run_id=job_run_id)


def _execute(session: Any, settings: Settings, plan: PreviewPlan, *, slate_date: date,
             now: datetime, job_run_id: Any) -> dict[str, Any]:
    """One request per sport, each independently reserved and reconciled.

    Sports are independent on purpose. A transport failure on NFL must not lose
    the MLB objects already collected, and it must not be retried inside the
    same run: a retry spends a second object to re-ask a question the provider
    has just failed to answer. The next scheduled run owns the next attempt.
    """

    from app.services.sgo_collection import complete_request, object_budget, record_request

    outcomes: list[dict[str, Any]] = []
    objects = 0
    reservations = 0
    remaining = plan.objects_grantable

    for sport in sorted({target.sport for target in plan.targets if target.wanted}):
        if remaining <= 0:
            outcomes.append({"sport": sport, "reason": DAILY_CAP_REACHED, "objects": 0})
            continue
        league, blocker = league_identity(sport)
        if league is None:
            # Named, never guessed. An invented identifier spends an object to
            # be told nothing.
            outcomes.append({"sport": sport, "reason": LEAGUE_UNVERIFIED, "objects": 0,
                             "blocker": blocker})
            continue
        wanted_games = [t for t in plan.targets if t.sport == sport and t.wanted]
        wanted = min(len(wanted_games), remaining)

        # The budget is re-read for every sport: the previous sport's objects
        # are already spent by now and a stale read would authorise them twice.
        budget = object_budget(session, now=now)
        allowed, refusal = budget.may_spend(wanted)
        headroom = budget.allowance - budget.objects_used - PREVIEW_RESERVE_FLOOR
        if not allowed:
            outcomes.append({"sport": sport, "reason": BUDGET_GUARD, "objects": 0, "detail": refusal})
            continue
        if headroom < wanted:
            outcomes.append({"sport": sport, "reason": RESERVE_GUARD, "objects": 0,
                             "detail": f"only {max(0, headroom)} objects sit above the "
                                       f"{PREVIEW_RESERVE_FLOOR}-object preview floor"})
            continue

        anchor = min(_aware(t.kickoff_utc) for t in wanted_games) - timedelta(seconds=1)
        params = {
            "leagueID": league, "limit": str(wanted),
            "startsAfter": anchor.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "startsBefore": max(_aware(t.kickoff_utc) for t in wanted_games).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "finalized": "false", "oddsPresent": "true",
        }
        reservation = record_request(
            session, requested_at=now, params=params, budget=budget,
            job_run_id=job_run_id, objects_requested=wanted, endpoint_key=PREVIEW_LANE)
        session.commit()
        reservations += 1

        try:
            fetch = _fetch(settings, league=league, limit=wanted, now=now, anchor=anchor)
        except Exception as error:  # noqa: BLE001 - one attempt per sport per run
            _complete(session, reservation, objects=0, succeeded=False, budget=budget,
                      error=type(error).__name__, job_run_id=job_run_id)
            session.commit()
            outcomes.append({"sport": sport, "reason": REQUEST_FAILED, "objects": 0,
                             "error_type": type(error).__name__,
                             "detail": "Not retried in this run; the next scheduled run owns the next attempt."})
            continue

        written = _ingest(session, settings, sport, fetch)
        _complete(session, reservation, objects=fetch.objects, succeeded=True,
                  budget=budget, job_run_id=job_run_id)
        session.commit()
        objects += fetch.objects
        remaining -= fetch.objects
        outcomes.append({
            "sport": sport, "reason": COLLECTED, "objects": fetch.objects,
            "games_requested": [t.source_game_ref for t in wanted_games[:wanted]],
            "request_filters": params, "markets_kept": list(REQUIRED_MARKETS),
            "bookmakers": sorted({pair.bookmaker_key for pair in fetch.pairs}),
            **written,
        })

    after = object_budget(session, now=now)
    return {
        "attempted": True, "reason": COLLECTED if objects else REQUEST_FAILED,
        "lane": PREVIEW_LANE, "horizon": "SHOW_PREVIEW",
        "slate_date": slate_date.isoformat(), "objects": objects,
        "reservations": reservations, "switches": switches(settings),
        "by_sport": outcomes, "plan": plan.as_dict(), "budget": after.as_dict(),
        "grants_wagering_authority": False,
        "note": ("Day-before research markets for the show. These quotes are preview "
                 "evidence: they are never final-pregame evidence and never carry a "
                 "decision, a stake or a position."),
    }


def _fetch(settings: Settings, *, league: str, limit: int, now: datetime, anchor: datetime) -> Any:
    """Exactly one provider request. No retry, no pagination, no second call."""

    import httpx

    from app.services.sgo_collection import (
        FallbackFetch, _refuse, eligible, parse_events, parse_main_markets,
    )

    ok, reason = eligible(settings, now=now)
    if not ok:
        raise _refuse(f"SportsGameOdds is not eligible: {reason}")
    params = {
        "leagueID": league, "limit": str(max(1, int(limit))),
        "startsAfter": anchor.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "startsBefore": (anchor + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "finalized": "false", "oddsPresent": "true",
    }
    url = f"{settings.sportsgameodds_base_url.rstrip('/')}/events"
    headers = {"X-Api-Key": settings.sportsgameodds_api_key, "Accept": "application/json"}
    with httpx.Client(timeout=30.0, follow_redirects=False) as client:
        response = client.get(url, params=params, headers=headers)
    if response.status_code != 200:
        raise _refuse(f"SportsGameOdds returned HTTP {response.status_code}; not retried")
    payload = response.json()
    # `now` stays the true retrieval moment: it becomes `available_as_of` on
    # every ingested row, and a preview quote that claimed to be retrieved later
    # than it was would read as fresher than it is on the show board.
    parse = parse_events(payload, received_at=now, now=now, league_id=league)
    # The same bytes read for spreads and totals. The provider bills the EVENT,
    # so all three required markets are already inside what was paid for.
    main = parse_main_markets(payload, received_at=now, now=now, league_id=league)
    return FallbackFetch(
        requested_at=now, objects=len(payload.get("data") or []),
        pairs=parse.pairs, parse=parse, main=main,
    )


def _ingest(session: Any, settings: Settings, sport: str, fetch: Any) -> dict[str, Any]:
    """Persist through the SAME path the final-pregame lanes use.

    Nothing preview-specific is written into the odds tables. A preview quote is
    an ordinary research quote with an ordinary ``observed_at`` and
    ``available_as_of``; what makes it preview evidence is the show board's own
    cut on those two timestamps, not a flag on the row. That is what lets a
    frozen board be reconstructed later, and it is why there is no second
    persistence path to keep in step with the first.
    """

    from app.services.sgo_collection import ingest, to_event_dtos

    if sport == "NFL":
        from app.services.nfl_shadow_odds_service import NFLShadowOddsService

        result = NFLShadowOddsService(session, source=SOURCE).ingest(
            to_event_dtos(fetch.pairs), available_as_of=fetch.requested_at)
        return {"events": result["events"], "mapped_events": result["mapped_events"],
                "quotes": result["quotes"], "duplicates": result["duplicates"],
                "rejected_events": result["rejected_events"], "source": SOURCE,
                "provider_governed": False}
    ingest(session, settings, fetch)
    return {"events": fetch.events_ingested, "quotes": fetch.quotes_written,
            "market_quotes": fetch.market_quotes_written, "source": SOURCE,
            "provider_governed": False,
            "main_markets": (fetch.main.by_market() if fetch.main else {})}


def _complete(session: Any, reservation: Any, *, objects: int, succeeded: bool,
              budget: Any, job_run_id: Any, error: str | None = None) -> None:
    """Close the reservation on THIS lane.

    ``complete_request`` writes its terminal row under the MLB endpoint key, so
    calling it here would file preview spend against MLB pregame and let the
    show quietly consume a decision lane's daily allowance in the accounting
    even though it never touched its allocation. The row is written here instead,
    with the same fields and the reservation's own lane.
    """

    from app.models import ProviderRequestLedger

    spent = max(0, int(objects))
    remaining = max(0, budget.allowance - budget.reserve - (budget.objects_used + spent))
    session.add(ProviderRequestLedger(
        provider=SOURCE, endpoint_key=PREVIEW_LANE,
        requested_at=reservation.requested_at, completed_at=datetime.now(UTC),
        status="SUCCEEDED" if succeeded else "FAILED",
        request_fingerprint=reservation.request_fingerprint,
        quota_cost=None, quota_remaining=remaining, records_received=spent,
        controlled_live_request=True, source_mode="LIVE", job_run_id=job_run_id,
        reservation_id=reservation.id, error_type=error,
        response_metadata={
            "request_shape": (reservation.response_metadata or {}).get("request_shape"),
            "objects_returned": spent, "billable_unit": "event object",
            "horizon": "SHOW_PREVIEW", "lane": PREVIEW_LANE,
            "objects_used_in_window_after": budget.objects_used + spent,
            "objects_spendable_after": remaining,
        },
    ))
    session.flush()


def stop_preview_collection(session: Any, *, reason: str, detail: str | None = None,
                            evidence: dict[str, Any] | None = None,
                            now: datetime | None = None) -> Any:
    """The kill switch: latch the preview lane shut.

    Separate from the switches because it answers a different question. A switch
    says whether the owner wants preview collection; a halt says this lane has
    done something that must not be repeated. Nothing here lifts a halt -- that
    is the owner's judgement, not a retry policy's.
    """

    from app.services.collection_halt import halt_lane

    return halt_lane(session, lane=PREVIEW_LANE, reason=reason,
                     evidence={"lane": PREVIEW_LANE, "horizon": "SHOW_PREVIEW",
                               **(evidence or {})},
                     detail=detail or "Show Preview collection stopped.", now=now)


__all__ = [
    "ALREADY_HELD", "BUDGET_GUARD", "COLLECTED", "DAILY_CAP_REACHED",
    "DAILY_OBJECT_CAP", "DISABLED", "DRY_RUN", "KILL_SWITCH", "LANE_HALTED",
    "LANE_UNFUNDED", "LEAGUE_UNVERIFIED", "NOT_IMPLEMENTED", "NO_GAMES",
    "NO_SESSION", "PREVIEW_LANE", "PREVIEW_RESERVE_FLOOR", "PROVIDER_NOT_ELIGIBLE",
    "REQUEST_FAILED", "REQUIRED_MARKETS", "RESERVE_GUARD", "SOURCE",
    "UNVERIFIED_LEAGUES", "VERIFIED_LEAGUE_IDS", "PreviewPlan", "PreviewTarget",
    "collect_preview_markets", "league_identity", "plan_collection",
    "preview_targets", "stop_preview_collection", "switches",
]

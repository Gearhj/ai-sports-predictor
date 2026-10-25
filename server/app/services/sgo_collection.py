"""Fetching MLB moneylines from the temporary governed fallback.

WHEN THIS RUNS
--------------
Only when the primary provider cannot answer. The event-aware collector owns
the schedule and the cutoffs; this module owns one bounded fetch, and it refuses
unless the fallback is switched on, inside its dated window, and the primary
lane is genuinely unable to run. It never widens a checkpoint, never relaxes a
cutoff and never changes what a decision requires.

WHAT IT COSTS
-------------
One object per event returned, which is the provider's own published unit. The
provider reports no usage headers at all, so consumption is counted here from
the ledger and an uncounted request is charged the worst case rather than zero.

HOW TIME IS RECORDED
--------------------
AISP already carries the two timestamps this needs, and they are used for what
they mean:

* ``observed_at`` is the price's own age, which is what the 240-minute staleness
  gate reads. For a pair whose sides carry independent provider stamps, that is
  the OLDER of the two -- a pair is only as fresh as its stalest half.
* ``available_as_of`` is when AISP received the response.

Mapping receipt time onto ``observed_at`` instead would have made every fallback
quote permanently fresh at the moment of ingestion, silently disabling the very
gate this phase was told to retain. The per-side stamps and their skew are kept
in the run's provenance, and simultaneity is never claimed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.data_sources.base import (
    BookMarketDTO,
    BookmakerDTO,
    OddsEventDTO,
    OddsOutcomeDTO,
    SourceError,
)
from app.data_sources.provider_registry import (
    GOVERNED_FALLBACK_UNTIL,
    SPORTSGAMEODDS,
    fallback_window_open,
)
from app.data_sources.sgo_parser import (
    AISP_MARKET_KEY,
    MainMarketParseResult,
    MainMarketQuote,
    MoneylinePair,
    ParseResult,
    parse_events,
    parse_main_markets,
)

ENDPOINT_KEY = "sgo_mlb_events"
#: An explicit opening balance, NOT a request. Objects were consumed by
#: authorised validation fetches made before this ledger existed, and that
#: consumption is real whether or not anything recorded it. Inventing four
#: historical request rows to represent it would put four fabrications in an
#: audit trail; one adjustment that declares itself an adjustment does not.
#: It is excluded from the request count and included in the object count,
#: because it describes objects spent, not a call anyone made.
OPENING_ADJUSTMENT_KEY = "sgo_opening_adjustment"
#: The rule that decides which games get a price when the day cannot fund them
#: all. Declared here, before any result, and tied only to the clock.
SELECTION_RULE = (
    "UNCOVERED_FIRST, then EARLIEST_DECISION_CUTOFF_FIRST, ties broken by "
    "provider game id. A game with no stored pair is priced before a game that "
    "already has one, because only the first can be helped by spending an "
    "object; within each group the game closest to locking goes first. Games "
    "beyond the day's allocation are reported by id as unsupported. The "
    "ranking is carried into the request itself: startsAfter is anchored one "
    "second below the earliest selected first pitch, so a lower-ranked game "
    "cannot be substituted by the provider's own ordering."
)

#: The published Amateur allowance, in EVENTS returned -- the provider's own
#: billable unit. Requests are free; events are not.
FREE_TIER_OBJECTS = 2500
#: Held back and never spent, so a miscount or a slate larger than expected
#: cannot walk the allowance to zero. Conservative on purpose: the cost of
#: refusing a fetch is one uncovered checkpoint; the cost of overrunning is a
#: provider relationship and a bill nobody authorised.
OBJECT_RESERVE = 400
#: The provider refreshes free-tier prices every ten minutes, so fetching more
#: often buys nothing and only risks a retry storm. This is also what stops a
#: five-minute scheduler tick from re-requesting the same unchanged prices.
MIN_SECONDS_BETWEEN_FETCHES = 600
#: Objects are counted over the provider's own billing window: the UTC calendar
#: month, which refills on the 1st.
#:
#: This used to be a rolling 30 days, on the reasoning that a rolling window
#: cannot under-count by sitting just after a reset. It cannot -- but it
#: massively OVER-counts after one: September spend kept counting against
#: October until each row aged out, which would have held the MLB and NFL
#: pregame lanes dark for roughly the first ten days of October while the
#: provider balance sat refilled and unused. A guard that refuses money the
#: account actually has is not conservative, it is wrong. The local count is
#: still the only count there is -- the provider returns no usage headers -- so
#: this is still AISP's own ledger, now measured over the provider's boundary.
#: See docs/SGO_BUDGET_GUARD_AUDIT.md.
OBJECT_WINDOW = "UTC calendar month"
#: Objects are events. Two per fetch keeps a whole September inside the 2,500
#: free allowance many times over, and the cap is enforced here as well as asked
#: for in the query.
MAX_EVENTS_PER_FETCH = 20


@dataclass
class FallbackFetch:
    requested_at: datetime
    objects: int
    pairs: list[MoneylinePair]
    parse: ParseResult
    events_ingested: int = 0
    quotes_written: int = 0
    #: Totals and run lines from the SAME response. These cost nothing extra:
    #: SportsGameOdds bills per event object, and every market the event carries
    #: is already inside the payload that was paid for. Retaining them was a
    #: parser omission, not a request or account limitation.
    main: "MainMarketParseResult | None" = None
    market_quotes_written: int = 0
    #: The SportsGameOdds leagueID the request was made for. Ingestion refuses
    #: the batch unless it maps to the canonical league being ingested.
    provider_league: str = ""
    #: Events that were not bound, with the reason (app.ingestion.odds_league).
    unbound: list = field(default_factory=list)
    #: What happened to the response's original bytes
    #: (app.services.sgo_raw_archive.archive_response). Never a reason to fail.
    raw_archive: dict[str, Any] | None = None
    #: Identity, schedule and status fields of every event the response carried
    #: (no prices), for the local checks an exact event-ID request needs.
    returned_events: list = field(default_factory=list)

    def skew_summary(self) -> dict[str, Any]:
        skews = [pair.timestamp_skew_seconds for pair in self.pairs]
        return {
            "pairs": len(skews),
            "max_skew_seconds": max(skews) if skews else None,
            "pairs_with_skew": sum(1 for s in skews if s > 0),
            "simultaneity_claimed": False,
        }


def _refuse(detail: str) -> SourceError:
    return SourceError(SPORTSGAMEODDS, detail, retryable=False)


def _aware_utc(value: datetime) -> datetime:
    """The same instant, in UTC. A naive value is read as UTC, never as local."""

    return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC)


def utc_stamp(value: datetime) -> str:
    """One provider request timestamp: the instant, CONVERTED, then labelled ``Z``.

    THE FAULT THIS EXISTS FOR
    -------------------------
    ``startsAfter``/``startsBefore`` are documented as UTC and the ``Z`` suffix
    asserts it, but ``strftime`` only prints whatever wall clock the datetime is
    carrying. The database session's TimeZone is America/New_York, so every
    ``games.starts_at`` comes back Eastern-offset -- the right instant with the
    wrong wall clock -- and stamping it with a literal ``Z`` moved the request
    window FOUR HOURS EARLY.

    On 2026-09-20 three NFL requests sent ``startsAfter=2026-09-20T16:04:59Z``
    for games that kick off at 20:04:59Z. Each was answered with the 13:00 ET
    games already in progress, each was charged, and nothing bound. The anchor
    is clamped to the retrieval moment one line before it is formatted, so a
    genuinely-UTC anchor could not have produced that value: the mislabel was
    the whole fault.

    Every SportsGameOdds request timestamp goes through here so a new call site
    cannot reintroduce it. Converting an already-UTC value is a no-op, which is
    why the verified-correct MLB lane is unchanged by it.
    """

    return _aware_utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def eligible(settings: Settings, *, now: datetime | None = None) -> tuple[bool, str]:
    """Fail-closed: every condition must hold, and each refusal names itself."""
    moment = now or datetime.now(UTC)
    if not getattr(settings, "sportsgameodds_enabled", False):
        return False, "FALLBACK_DISABLED"
    if not (getattr(settings, "sportsgameodds_api_key", "") or "").strip():
        return False, "FALLBACK_NO_CREDENTIAL"
    if not (getattr(settings, "sportsgameodds_base_url", "") or "").strip():
        return False, "FALLBACK_NO_BASE_URL"
    if not fallback_window_open(moment):
        return False, f"FALLBACK_WINDOW_CLOSED_AFTER_{GOVERNED_FALLBACK_UNTIL.isoformat()}"
    return True, "FALLBACK_ELIGIBLE"


def nfl_lane_eligible(settings: Settings, *, now: datetime | None = None) -> tuple[bool, str]:
    """`eligible()` for the NFL pregame lane ONLY, with its own dated window.

    Every non-date condition is `eligible()`'s own, in the same order. Only the
    window differs: `provider_registry.nfl_lane_window_open`. No other caller
    may use this -- MLB, NFL live scores and Show Preview keep `eligible()`.
    """
    from app.data_sources.provider_registry import (
        NFL_LANE_ELIGIBLE_UNTIL, nfl_lane_renewal_in_force, nfl_lane_window_open,
    )

    moment = now or datetime.now(UTC)
    if not getattr(settings, "sportsgameodds_enabled", False):
        return False, "FALLBACK_DISABLED"
    if not (getattr(settings, "sportsgameodds_api_key", "") or "").strip():
        return False, "FALLBACK_NO_CREDENTIAL"
    if not (getattr(settings, "sportsgameodds_base_url", "") or "").strip():
        return False, "FALLBACK_NO_BASE_URL"
    if not nfl_lane_window_open(moment):
        return False, f"NFL_LANE_WINDOW_CLOSED_AFTER_{NFL_LANE_ELIGIBLE_UNTIL.isoformat()}"
    if nfl_lane_renewal_in_force(moment):
        return True, "NFL_LANE_RENEWAL_ELIGIBLE"
    return True, "FALLBACK_ELIGIBLE"


def to_event_dtos(pairs: list[MoneylinePair]) -> list[OddsEventDTO]:
    """One DTO per event, one bookmaker per book, both sides of one market.

    Built through the ordinary DTO path on purpose: the existing normalizer then
    applies provider namespacing, so these bookmakers land as ``sgo:draftkings``
    and can never share a Sportsbook row -- and therefore never a two-sided
    pair -- with the governed provider.
    """
    by_event: dict[str, list[MoneylinePair]] = {}
    for pair in pairs:
        by_event.setdefault(pair.event_id, []).append(pair)

    events: list[OddsEventDTO] = []
    for event_id, group in by_event.items():
        first = group[0]
        books = [
            BookmakerDTO(
                key=pair.bookmaker_key,
                title=pair.bookmaker_title,
                # A pair is only as fresh as its stalest half. This becomes
                # observed_at, which is what the staleness gate reads.
                last_update=pair.pair_freshness_at,
                markets=[BookMarketDTO(
                    key=AISP_MARKET_KEY,
                    last_update=pair.pair_freshness_at,
                    outcomes=[
                        OddsOutcomeDTO(name=pair.home_team_name, price=pair.home.american),
                        OddsOutcomeDTO(name=pair.away_team_name, price=pair.away.american),
                    ],
                )],
            )
            for pair in sorted(group, key=lambda item: item.bookmaker_key)
        ]
        events.append(OddsEventDTO(
            source_event_id=event_id,
            commence_time=first.commence_time,
            home_team=first.home_team_name,
            away_team=first.away_team_name,
            bookmakers=books,
            source_mode="LIVE",
        ))
    return events


def fetch_pregame_moneylines(
    settings: Settings, *, limit: int = 10, now: datetime | None = None,
    starts_within_hours: int = 12, cache_path: "Path | None" = None,
    starts_after: datetime | None = None, request_fingerprint: str | None = None,
    lane: str = ENDPOINT_KEY, event_ids: list[str] | None = None,
) -> FallbackFetch:
    """Exactly one request for upcoming MLB events. No retry, no pagination.

    A second page would be a second request and a second object charge, so the
    caller is told what was returned and decides; nothing here loops.

    ``event_ids`` adds the provider event-ID filter (``EVENT_ID_FILTER_PARAM``).
    Only the exact-selection path passes it (``sgo_exact_event_selection_mode``
    VALIDATE or ON); see that constant for what the provider does with it.
    """
    ok, reason = eligible(settings, now=now)
    if not ok:
        raise _refuse(f"SportsGameOdds fallback is not eligible: {reason}")
    moment = now or datetime.now(UTC)
    capped = max(1, min(int(limit), MAX_EVENTS_PER_FETCH))
    # WHICH GAMES THE PROVIDER IS ASKED FOR, NOT MERELY WHICH ONES WE WANTED.
    #
    # `limit` plus a start-time window does not name a game. The provider
    # returns the earliest events in the window, so a caller that had carefully
    # chosen SD at COL still received CWS at CLE -- the earliest game in the
    # same window, and the one that already had a price. The selection rule ran,
    # produced a different answer, and never reached the request.
    #
    # `startsAfter` is the one documented filter that can carry that choice:
    # anchoring it just below the chosen game's first pitch excludes every
    # earlier game, so the object cannot be spent on one of them. It is the same
    # parameter already in use, given a value that means something.
    anchor = starts_after or moment
    if anchor < moment:  # never reach back to a game already under way
        anchor = moment
    params = {
        "leagueID": "MLB",
        "limit": str(capped),
        # Documented filters only. "startsAfter" is what makes this a PREGAME
        # fetch; without it the provider returns games already in progress,
        # whose prices are unusable and whose events are past their cutoff.
        # Both stamps are CONVERTED to UTC before the "Z" is attached: the only
        # in-app caller already hands over a UTC anchor (this lane is verified
        # correct at wire level), and `utc_stamp` keeps that true for a caller
        # that passes a `starts_after` or a `now` straight off the DB session,
        # whose TimeZone is America/New_York.
        "startsAfter": utc_stamp(anchor),
        "startsBefore": utc_stamp(moment + timedelta(hours=starts_within_hours)),
        "finalized": "false",
        "oddsPresent": "true",
    }
    if event_ids:
        params[EVENT_ID_FILTER_PARAM] = ",".join(event_ids)
    url = f"{settings.sportsgameodds_base_url.rstrip('/')}/events"
    headers = {"X-Api-Key": settings.sportsgameodds_api_key, "Accept": "application/json"}

    with httpx.Client(timeout=30.0, follow_redirects=False) as client:
        response = client.get(url, params=params, headers=headers)
    if response.status_code != 200:
        raise _refuse(f"SportsGameOdds returned HTTP {response.status_code}; not retried")
    # Keep the exact response bytes before parsing: the per-leg lastUpdatedAt
    # stamps and the response's own identity exist nowhere else. An archive
    # fault is recorded, never raised, retried or waited on.
    from app.services.sgo_raw_archive import archive_response

    raw_archive = archive_response(settings, response.content, requested_at=moment,
                                   request_fingerprint=request_fingerprint, lane=lane,
                                   league="MLB", filters=params)
    payload = response.json()
    # Persist the raw response before parsing. If anything downstream fails, the
    # fix must never cost another request -- and therefore another object.
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(response.content)
    # NOTE: `received_at=moment` is the clock captured BEFORE the request was
    # sent (the ledger's requested_at). It is not a receipt; the completed
    # ledger/job rows are the receipt.
    parse = parse_events(payload, received_at=moment, now=moment, league_id="MLB")
    # The same bytes, read for the other two main markets. No second request and
    # no second object: the provider's published unit is the event, and the
    # totals and run lines were already inside the response the moneyline parse
    # just read.
    main = parse_main_markets(payload, received_at=moment, now=moment, league_id="MLB")
    return FallbackFetch(
        requested_at=moment,
        objects=len(payload.get("data") or []),
        pairs=parse.pairs,
        parse=parse,
        main=main,
        provider_league="MLB",
        raw_archive=raw_archive,
        returned_events=returned_event_fields(payload),
    )


_STATUS_FLAGS = ("started", "live", "completed", "ended", "cancelled", "finalized")


def returned_event_fields(payload: Any) -> list[dict[str, Any]]:
    """Identity, schedule and status of each returned event -- no prices."""

    out: list[dict[str, Any]] = []
    for event in (payload.get("data") or []) if isinstance(payload, dict) else []:
        if not isinstance(event, dict):
            continue
        status = event.get("status") if isinstance(event.get("status"), dict) else {}
        teams = event.get("teams") if isinstance(event.get("teams"), dict) else {}
        home, away = teams.get("home") or {}, teams.get("away") or {}
        out.append({
            "event_id": str(event.get("eventID") or ""),
            "league_id": str(event.get("leagueID") or ""),
            "starts_at": status.get("startsAt"),
            "status": {flag: bool(status.get(flag)) for flag in _STATUS_FLAGS},
            "home_name": str((home.get("names") or {}).get("long") or home.get("teamID") or ""),
            "away_name": str((away.get("names") or {}).get("long") or away.get("teamID") or ""),
        })
    return out


def _games_missing_total(session: Session, game_ids: "list[str]") -> set[str]:
    """Which of these games hold no full-game total quote at all.

    Deliberately "none at all" rather than "none fresh". A stale total is still
    a total, and re-requesting for freshness would reopen the spending loop the
    window rule exists to close; this only fills a market that was never
    collected because the parser used to discard it.
    """

    from sqlalchemy import func, select as _select

    from app.models import Market, MarketOutcome, MarketType, OddsSnapshot

    if not game_ids:
        return set()
    have = session.execute(
        _select(Market.game_id, func.count())
        .select_from(OddsSnapshot)
        .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
        .join(Market, Market.id == MarketOutcome.market_id)
        .where(
            Market.market_type == MarketType.TOTAL,
            Market.period == "full_game",
            Market.game_id.in_([_uuid(value) for value in game_ids]),
        )
        .group_by(Market.game_id)
    ).all()
    covered = {str(row[0]) for row in have if row[1]}
    return {value for value in game_ids if value not in covered}


def _uuid(value: "str | Any") -> Any:
    import uuid as _uuid_module

    try:
        return _uuid_module.UUID(str(value))
    except (TypeError, ValueError):
        return value


def to_market_event_dtos(
    quotes: "list[MainMarketQuote]", *, markets: tuple[str, ...] = ("totals", "spreads"),
) -> list[OddsEventDTO]:
    """Totals and run lines as ordinary odds DTOs, one bookmaker at a time.

    ``h2h`` is excluded by default because ``to_event_dtos`` already writes it
    from the moneyline parse; including it here would put the same quote through
    two code paths for no gain.

    Each side carries its OWN bookmaker line as ``point``. That matters: in the
    retained payload FanDuel posts Houston/Philadelphia at 8.5 while six other
    books post 8.0, and the provider's odd-level consensus says 8.0. Storing the
    consensus against FanDuel's price would record a quote no book ever made.
    """

    by_event: dict[str, list[MainMarketQuote]] = {}
    for quote in quotes:
        if quote.market_key not in markets:
            continue
        by_event.setdefault(quote.event_id, []).append(quote)

    events: list[OddsEventDTO] = []
    for event_id, group in by_event.items():
        first = group[0]
        by_book: dict[str, list[MainMarketQuote]] = {}
        for quote in group:
            by_book.setdefault(quote.bookmaker_key, []).append(quote)
        books = [
            BookmakerDTO(
                key=book_key,
                title=book_quotes[0].bookmaker_title,
                last_update=min(quote.freshness_at for quote in book_quotes),
                markets=[
                    BookMarketDTO(
                        key=quote.market_key,
                        # A two-sided quote is only as fresh as its stalest half,
                        # the same rule the moneyline path already applies.
                        last_update=quote.freshness_at,
                        outcomes=[
                            OddsOutcomeDTO(
                                name=side.label, price=side.american, point=side.point)
                            for side in quote.sides
                        ],
                    )
                    for quote in sorted(book_quotes, key=lambda item: item.market_key)
                ],
            )
            for book_key, book_quotes in sorted(by_book.items())
        ]
        events.append(OddsEventDTO(
            source_event_id=event_id,
            commence_time=first.commence_time,
            home_team=first.home_team_name,
            away_team=first.away_team_name,
            bookmakers=books,
            source_mode="LIVE",
        ))
    return events


def ingest(session: Session, settings: Settings, fetch: FallbackFetch,
           *, league_code: str, event_date: date | None = None) -> FallbackFetch:
    """Normalize the fetched pairs through the ordinary ingestion path.

    ``league_code`` is the canonical league these quotes may bind to; the
    fetch's own provider league must map to it, and only that league's games
    are candidates.
    """
    from app.ingestion.normalizer import CanonicalNormalizer

    normalizer = CanonicalNormalizer(session)
    events = to_event_dtos(fetch.pairs)
    if events:
        written = normalizer.ingest_odds(
            events, source=SPORTSGAMEODDS, available_as_of=fetch.requested_at,
            league_code=league_code, provider_league=fetch.provider_league,
            source_mode="LIVE", event_date=event_date,
        )
        fetch.events_ingested = len(events)
        fetch.quotes_written = int(written)
        fetch.unbound.extend(normalizer.odds_receipts)

    # Totals and run lines from the same already-paid-for response. Ingested
    # through the same normalizer, so provider namespacing, duplicate
    # suppression and canonical game binding are identical to the moneyline
    # path -- nothing here is a second ingestion mechanism.
    if fetch.main is not None:
        market_events = to_market_event_dtos(fetch.main.quotes)
        if market_events:
            fetch.market_quotes_written = int(normalizer.ingest_odds(
                market_events, source=SPORTSGAMEODDS,
                available_as_of=fetch.requested_at,
                league_code=league_code, provider_league=fetch.provider_league,
                source_mode="LIVE", event_date=event_date,
            ))
    return fetch


__all__ = [
    "ENDPOINT_KEY",
    "FREE_TIER_OBJECTS",
    "MIN_SECONDS_BETWEEN_FETCHES",
    "SELECTION_RULE",
    "OBJECT_RESERVE",
    "OPENING_ADJUSTMENT_KEY",
    "ObjectBudget",
    "apply_opening_adjustment",
    "collect_for_open_cutoffs",
    "object_budget",
    "FallbackFetch",
    "eligible",
    "fetch_pregame_moneylines",
    "ingest",
    "to_event_dtos",
    "utc_stamp",
]


# --- ledger accounting and the object budget --------------------------------


@dataclass(frozen=True)
class ObjectBudget:
    """What the local count says is left.

    There is no provider figure to check it against: SportsGameOdds returns no
    usage headers at all, so this IS the number.

    The window is the provider's billing month. ``blocked_reason`` is set when
    the month itself could not be established, and it makes the whole budget
    unspendable: an unknown boundary is not a licence to spend.
    """

    #: The UTC month this count covers, as ``YYYY-MM``.
    billing_month: str
    #: The instant that month began, ISO-8601 UTC.
    window_start: str
    #: Days of the billing month elapsed so far, inclusive of today.
    window_days: int
    requests: int
    objects_used: int
    allowance: int
    reserve: int
    #: Why the reserve is what it is, for a receipt.
    reserve_basis: str = "standing"
    #: Set when the billing month could not be established. Blocks all spending.
    blocked_reason: str | None = None

    @property
    def spendable(self) -> int:
        if self.blocked_reason:
            return 0
        return max(0, self.allowance - self.reserve - self.objects_used)

    def may_spend(self, objects: int) -> tuple[bool, str | None]:
        if self.blocked_reason:
            return False, f"object budget unavailable: {self.blocked_reason}"
        if objects <= 0:
            return False, "a request must declare a positive object cost"
        if objects > self.spendable:
            return False, (
                f"object budget would be exceeded: {self.objects_used} used in "
                f"billing month {self.billing_month}, {self.reserve} held in "
                f"reserve of {self.allowance}, so only {self.spendable} are "
                f"spendable and {objects} were requested"
            )
        return True, None

    def as_dict(self) -> dict[str, Any]:
        return {
            "window": OBJECT_WINDOW,
            "billing_month": self.billing_month,
            "window_start": self.window_start,
            "window_days": self.window_days,
            "requests_in_window": self.requests,
            "objects_used_in_window": self.objects_used,
            "allowance": self.allowance,
            "reserve_never_spent": self.reserve,
            "reserve_basis": self.reserve_basis,
            "objects_spendable": self.spendable,
            "blocked_reason": self.blocked_reason,
            "counted_locally_because": "the provider returns no usage headers",
        }


def _blocked_budget(reason: str) -> ObjectBudget:
    """A budget that authorises nothing, because it does not know its window."""

    return ObjectBudget(
        billing_month="", window_start="", window_days=0, requests=0,
        objects_used=0, allowance=FREE_TIER_OBJECTS, reserve=OBJECT_RESERVE,
        reserve_basis="unknown", blocked_reason=reason)


def phase1_reserve(moment: datetime) -> tuple[int, str]:
    """The reserve this guard holds back, and why -- the SAME schedule as guard A.

    Phase 1 (2026-09-15) held ``max(400, scheduled)`` so the repair itself
    released nothing. Phase 2 (2026-09-18, on the owner's instruction applying
    the September 300 recorded 2026-09-14, commit 21e1cab) takes
    ``provider_allocation.reserve_at`` exactly: 300 through the September 2026
    billing month, 400 from 2026-10-01T00:00Z. The name is kept for callers.
    """

    from app.services.provider_allocation import reserve_at

    reserve = int(reserve_at(moment))
    if reserve < OBJECT_RESERVE:
        return reserve, (f"scheduled reserve {reserve} (owner, recorded 2026-09-14; both guards "
                         "since phase 2, 2026-09-18) for this billing month")
    return reserve, f"standing reserve {reserve}"


def billing_month_window(moment: datetime) -> tuple[datetime, str]:
    """The start of the provider's billing month containing ``moment``.

    Raises ``ValueError`` when the moment carries no offset, because a month
    boundary cannot be placed without one and guessing UTC at 23:40 on the last
    day of a month is exactly the guess that would mis-bill a reset.
    """

    if moment.tzinfo is None:
        raise ValueError("the moment carries no timezone, so its UTC billing month is unknown")
    utc_moment = moment.astimezone(UTC)
    start = utc_moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start, f"{utc_moment.year:04d}-{utc_moment.month:02d}"


def _next_month_start(start: datetime) -> datetime:
    """The exclusive end of the billing month that began at ``start``.

    The window needs a ceiling as well as a floor. A rolling window did not:
    nothing is ever requested in the future. A calendar month is asked about
    from both sides -- the Eastern service day of September 30 runs to 04:00
    UTC on October 1 -- and a row from the far side of the reset belongs to the
    other month's bill, not to both.
    """

    return (start.replace(year=start.year + 1, month=1) if start.month == 12
            else start.replace(month=start.month + 1))


def object_budget(session: Session, *, now: datetime | None = None) -> ObjectBudget:
    """Billing-month request and object totals, counted from our own ledger."""
    from sqlalchemy import func, select

    from app.models import ProviderRequestLedger

    moment = now or datetime.now(UTC)
    try:
        since, billing_month = billing_month_window(moment)
    except ValueError as error:
        return _blocked_budget(str(error))
    until = _next_month_start(since)
    reserve, reserve_basis = phase1_reserve(moment)
    completed, counted, with_counts = session.execute(
        select(
            func.count(),
            func.coalesce(func.sum(ProviderRequestLedger.records_received), 0),
            func.count(ProviderRequestLedger.records_received),
        ).where(
            ProviderRequestLedger.provider == SPORTSGAMEODDS,
            ProviderRequestLedger.status.in_(("SUCCEEDED", "FAILED")),
            ProviderRequestLedger.requested_at >= since,
            ProviderRequestLedger.requested_at < until,
        )
    ).one()
    # Requests and objects are different quantities. An opening adjustment
    # carries objects but is not a call anyone made, so it is left out of the
    # request count and kept in the object count.
    requests = session.scalar(
        select(func.count()).where(
            ProviderRequestLedger.provider == SPORTSGAMEODDS,
            ProviderRequestLedger.status.in_(("SUCCEEDED", "FAILED")),
            ProviderRequestLedger.requested_at >= since,
            ProviderRequestLedger.requested_at < until,
            ProviderRequestLedger.endpoint_key != OPENING_ADJUSTMENT_KEY,
        )
    ) or 0
    # A completed request the provider gave no count for is charged the whole
    # per-fetch cap. Under-counting an allowance is how an allowance is exceeded.
    # Count the unknowns against the SAME population that produced with_counts:
    # every completed row, adjustments included. Using the request count here
    # made this negative the moment adjustments were excluded from it, which
    # quietly SUBTRACTED from the objects used.
    unknown = max(0, int(completed) - int(with_counts))
    return ObjectBudget(
        billing_month=billing_month,
        window_start=since.isoformat(),
        window_days=max(1, (moment.astimezone(UTC).date() - since.date()).days + 1),
        requests=int(requests),
        objects_used=int(counted) + unknown * MAX_EVENTS_PER_FETCH,
        allowance=FREE_TIER_OBJECTS,
        reserve=reserve,
        reserve_basis=reserve_basis,
    )


def seconds_since_last_fetch(
    session: Session, *, now: datetime | None = None, lane: str | None = None,
) -> float | None:
    """How long since the last request that could have SPENT something.

    Counted from reservations, so a failing provider cannot be hammered: a
    request that errored still starts the cooldown.

    WHY THE FREE ENDPOINTS ARE EXCLUDED
    -----------------------------------
    This cooldown exists to stop a five-minute scheduler tick from re-buying
    prices that cannot have changed, and to stop a failing provider being
    hammered. Neither reason applies to a request that buys nothing.

    Counting every ledger row made the balance reading -- which spends no
    entities, and runs on its own cadence -- hold off price collection. On
    2026-09-14 the usage reads at 16:45:13, 16:56:44 and 17:12:17 each pushed
    the next collection out by another ten minutes, so SD at COL sat uncovered
    for two and a half hours with six objects of room in its lane. A free read
    of the account balance is not an attempt to buy a price, exactly as the
    opening adjustment is not.

    WHY IT IS COUNTED PER LANE
    --------------------------
    ``lane`` scopes the count to one allocation lane's own ledger rows. Counting
    every lane made ONE lane's cadence the cadence of all of them: an NFL
    pregame fetch held the MLB lane off for ten minutes even though the two
    spend different allowances on different games. On a fourteen-game NFL Sunday
    the NFL lane fetches inside every checkpoint cluster, and each one shut four
    consecutive five-minute MLB ticks -- MLB games going dark because a
    different lane bought a price for a different sport.

    The two reasons the cooldown exists are both lane-local: re-buying prices
    that cannot have changed is re-buying THIS lane's prices, and a provider
    being hammered is measured by this lane's own retry cadence, which is what
    every lane scoping itself preserves. Passing no lane keeps the old
    every-row reading, so nothing that does not ask for scoping changes.
    """
    from sqlalchemy import func, select

    from app.models import ProviderRequestLedger
    from app.services.provider_allocation import USAGE_ENDPOINT_KEY

    moment = now or datetime.now(UTC)
    filters = [
        ProviderRequestLedger.provider == SPORTSGAMEODDS,
        # Neither of these can consume an entity, so neither may hold off
        # the next real fetch.
        ProviderRequestLedger.endpoint_key.notin_(
            (OPENING_ADJUSTMENT_KEY, USAGE_ENDPOINT_KEY)),
    ]
    if lane is not None:
        filters.append(ProviderRequestLedger.endpoint_key == lane)
    latest = session.scalar(
        select(func.max(ProviderRequestLedger.requested_at)).where(*filters)
    )
    if latest is None:
        return None
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=UTC)
    return (moment - latest).total_seconds()


def record_request(
    session: Session,
    *,
    requested_at: datetime,
    params: dict[str, str],
    budget: ObjectBudget,
    job_run_id: Any = None,
    endpoint_key: str | None = None,
    objects_requested: int | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> Any:
    """Reserve the request in the provider ledger BEFORE it is sent.

    The row carries the filters and the bounded window so the request can be
    audited without the payload, and never any credential material: the API key
    travels in a header, is not in ``params``, and is recorded nowhere.

    ``extra_metadata`` is merged into the reservation's own metadata. It exists
    so a request drawing on a dated owner exception records WHICH named games it
    is drawing for, before it is sent -- that is what stops a second job
    claiming the same game's single object in the gap before its quote exists.
    """
    from hashlib import sha256

    from app.models import ProviderRequestLedger

    shape = {
        "provider": SPORTSGAMEODDS,
        "endpoint": "events",
        "filters": dict(params),
        "billable_unit": "event object",
        "credential": "sent in header; never recorded",
    }
    ledger = ProviderRequestLedger(
        provider=SPORTSGAMEODDS,
        # Defaults to the MLB pregame key. A caller that spends against a
        # different allowance passes its own, so the two can never be added up
        # as one and a per-feature daily cap stays enforceable.
        endpoint_key=endpoint_key or ENDPOINT_KEY,
        requested_at=requested_at,
        # The count this request committed to, recorded BEFORE it is sent. A
        # concurrent job reads it as in-flight and cannot claim the same
        # objects; the terminal row then reconciles it against what actually
        # came back.
        quota_cost=max(0, int(objects_requested or 0)),
        status="RESERVED",
        request_fingerprint=sha256(
            json.dumps({**shape, "at": requested_at.isoformat()}, sort_keys=True).encode()
        ).hexdigest(),
        controlled_live_request=True,
        source_mode="LIVE",
        job_run_id=job_run_id,
        response_metadata={
            "request_shape": shape,
            "object_budget_before": budget.as_dict(),
            **(extra_metadata or {}),
        },
    )
    session.add(ledger)
    session.flush()
    return ledger


def complete_request(
    session: Session,
    reservation: Any,
    *,
    objects: int,
    succeeded: bool,
    budget: ObjectBudget,
    error: str | None = None,
    job_run_id: Any = None,
    endpoint_key: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> None:
    """Write the terminal row: outcome, objects returned, allowance remaining.

    ``records_received`` is the billable count -- EVENTS, not prices and not
    requests. One ledger row is one request; the sum of ``records_received`` is
    the object spend. Keeping them in different columns is what makes the two
    countable separately.

    THE LANE COMES FROM THE RESERVATION
    -----------------------------------
    This used to hard-code the MLB endpoint key, so a lane that reserved
    correctly still settled under someone else's name. Confirmed on the
    production ledger 2026-09-15: `sgo_nfl_events` had four RESERVED rows and
    zero terminal rows, and its four objects settled as `sgo_mlb_events`.

    MLB was not over-charged for them -- `_lane_usage` already subtracts a row
    whose own retained `leagueID` contradicts its lane. But that correction is
    one-sided: it takes the objects OFF the lane that was wrongly charged and
    does not put them ON the lane that really spent them. So the NFL lane read
    as unspent after spending, and its daily allowance was available to draw a
    second time. A cap enforced against a lane that cannot see its own spend is
    not a cap.

    The reservation already carries the right lane, so it is the source of
    truth here; ``endpoint_key`` overrides it only for a caller that has a
    reason to say otherwise.
    """
    from app.models import ProviderRequestLedger

    # Record the object count we actually know, success or failure. A transport
    # failure returned no events and cost nothing, and writing NULL there would
    # have it charged the worst case -- which would let an outage eat the
    # allowance. NULL is reserved for a row whose count is genuinely unknown.
    spent = max(0, objects)
    remaining = max(0, budget.allowance - budget.reserve - (budget.objects_used + spent))
    lane = endpoint_key or getattr(reservation, "endpoint_key", None) or ENDPOINT_KEY
    session.add(
        ProviderRequestLedger(
            provider=SPORTSGAMEODDS,
            endpoint_key=lane,
            requested_at=reservation.requested_at,
            completed_at=datetime.now(UTC),
            status="SUCCEEDED" if succeeded else "FAILED",
            request_fingerprint=reservation.request_fingerprint,
            quota_cost=None,  # this provider bills objects, not credits
            quota_remaining=remaining,
            records_received=spent,
            controlled_live_request=True,
            source_mode="LIVE",
            job_run_id=job_run_id,
            reservation_id=reservation.id,
            error_type=error,
            response_metadata={
                "request_shape": (reservation.response_metadata or {}).get("request_shape"),
                "objects_returned": spent,
                "billable_unit": "event object",
                "lane": lane,
                "objects_used_in_window_after": budget.objects_used + spent,
                "objects_spendable_after": remaining,
                **(extra_metadata or {}),
            },
        )
    )
    session.flush()


# --- never send a request the provider may answer with a different game ------
#
# THE FAULT (2026-09-18 14:42:37 ET, halt aa24c634)
# --------------------------------------------------
# A request names games only through `limit` and a start-time window; it
# carries no event identity. The provider answers with the earliest `limit`
# events in the window, and orders events that share a first pitch its own way.
# The refresh that halted the lane selected KC @ PIT (07ddd2bf) with limit=1
# over a window whose earliest start, 18:40 ET, was shared with CHC @ CIN
# (3e7914d7). The provider returned CHC @ CIN -- an answer the filters allowed.
# Any request whose selection is not exactly the earliest `limit` games of its
# window can do the same: a strict subset of a tied first pitch, or a selected
# game with an unselected one starting before it inside the window.
#
# THE REPAIR
# ----------
# Before a request is sent, the selection is reduced to its longest
# unambiguous prefix: whole start-time groups, earliest first, every game of
# the window up to the last kept start selected. What remains is DEFERRED by
# name -- never widened, never bought as an extra object. The mismatch check
# and the halt after the request are unchanged. When the selection is already
# unambiguous (every request since 09-14 except the one that halted), the
# request is byte-identical to before.

#: The provider's documented event-ID filter on GET /events.
#:
#: What the retained provider documentation says (paraphrased; the copies are
#: git-ignored under data/raw and are never copied into the repository):
#:
#: * OpenAPI, data/raw/provider-docs/sportsgameodds/openapi-20260919T182608Z.json
#:   (sha256 f4de66395961792d7536fbd772c6ee5ba024969c8c25575a8c95cd1a44bce9bb,
#:   retrieved 2026-09-19T18:26:08Z): ``eventIDs`` is a string query parameter
#:   holding a comma-separated list of event IDs to return data for; ``limit``
#:   is the maximum number of events returned.
#: * FAQ, data/raw/provider-docs/sportsgameodds/faq-20260919T182608Z.html
#:   (sha256 67b071ad4d5205981667e21cb27ee79d487e3b1204a02c2d2fec75c1ba79bda6,
#:   same retrieval, "Why are my filters ignored when I pass eventID?"): when
#:   ``eventID``/``eventIDs`` is given, only those IDs select the result and
#:   EVERY other filter -- leagueID, started, finalized, startsAfter,
#:   startsBefore and the rest -- is ignored; response-shaping parameters still
#:   apply.
#:
#: So an ID request carries none of the protection the filters used to give:
#: a finalized, started or moved event comes back if it is named. That is why
#: the ID path checks every selected game locally before sending and every
#: returned event locally before ingesting (``exact_id_blockers``,
#: ``check_exact_response``), and why any difference between the IDs asked for
#: and the IDs returned goes through the unchanged mismatch halt. The window
#: filters are still sent: harmless when the IDs are honoured, and the same
#: safety net as today if they ever were not.
EVENT_ID_FILTER_PARAM = "eventIDs"
DEFERRED_TIED_START = "DEFERRED_AMBIGUOUS_TIED_START_NO_VERIFIED_EVENT_ID"
DEFERRED_EARLIER_GAME = "DEFERRED_AMBIGUOUS_UNSELECTED_EARLIER_GAME_IN_WINDOW_NO_VERIFIED_EVENT_ID"
EXACT_MODES = ("OFF", "VALIDATE", "ON")
#: Tolerance for "the start AISP holds now is the start it held when the ID was
#: bound". Any real reschedule is larger than this.
START_UNCHANGED_TOLERANCE = timedelta(seconds=60)


def exact_selection_mode(settings: Any) -> str:
    """OFF, VALIDATE or ON. Anything unrecognised is OFF."""

    value = str(getattr(settings, "sgo_exact_event_selection_mode", "OFF") or "OFF").strip().upper()
    return value if value in EXACT_MODES else "OFF"


#: The one owner-authorised live test that CAN show whether the provider filters
#: by ID: an ambiguous selection -- one the window alone could answer with a
#: different game -- sent by ID. Authorised once, inside the existing purchase
#: and allowance; never retried.
DISCRIMINATING_TEST = "DISCRIMINATING_TEST"


def discriminating_test_state(session: Session) -> dict[str, Any]:
    """Has VALIDATE's one discriminating test been used, and did it pass?

    Read from the ledger at request time (terminal rows of this lane, SUCCEEDED
    or FAILED), so the transition needs no restart. Any recorded test counts as
    used, whatever its result; only a passed test with its transition record
    lets VALIDATE behave as ON.
    """

    from sqlalchemy import String, cast, select

    from app.models import ProviderRequestLedger

    rows = session.execute(
        select(ProviderRequestLedger.id, ProviderRequestLedger.requested_at,
               ProviderRequestLedger.response_metadata).where(
            ProviderRequestLedger.provider == SPORTSGAMEODDS,
            ProviderRequestLedger.endpoint_key == ENDPOINT_KEY,
            ProviderRequestLedger.status.in_(("SUCCEEDED", "FAILED")),
            cast(ProviderRequestLedger.response_metadata, String).like(f"%{DISCRIMINATING_TEST}%"),
        ).order_by(ProviderRequestLedger.requested_at)
    ).all()
    tests = [(row_id, at, metadata or {}) for row_id, at, metadata in rows
             if ((metadata or {}).get("exact_event_selection") or {}).get("test_kind")
             == DISCRIMINATING_TEST]
    if not tests:
        return {"attempted": False, "passed": False, "test_ledger_id": None, "transition": None}
    row_id, at, metadata = tests[0]
    record = metadata.get("exact_event_selection") or {}
    transition = metadata.get("exact_event_selection_transition")
    return {"attempted": True, "passed": bool(record.get("passed") is True and transition),
            "test_ledger_id": str(row_id), "tested_at": at.isoformat() if at else None,
            "transition": transition}


def exact_id_blockers(
    session: Session, events: list[dict[str, Any]], verified: dict[str, str], *, moment: datetime,
) -> list[dict[str, Any]]:
    """Why any selected game may not be requested by ID right now (empty: none).

    The provider ignores every other filter on an ID request, so AISP's own
    state has to say what the filters used to: still pregame (scheduled, not
    started, not postponed or cancelled), its purchase cutoff still open, and
    the first pitch AISP holds now equal to the one it held when this ID was
    bound -- a moved game may no longer be the event the ID was bound as.
    """

    from sqlalchemy import func, select

    from app.models import Game, GameStateObservation, GameStatus, Market, MarketOutcome, OddsSnapshot

    blockers: list[dict[str, Any]] = []
    for event in events:
        game_id = str(event.get("game_id"))
        event_id = verified.get(game_id)
        reason = None
        game = session.get(Game, _uuid(game_id))
        if event_id is None:
            reason = "NO_VERIFIED_EVENT_ID"
        elif game is None:
            reason = "GAME_NOT_FOUND"
        elif str(getattr(game.status, "value", game.status)) != GameStatus.SCHEDULED.value:
            reason = f"GAME_NOT_PREGAME_{str(getattr(game.status, 'value', game.status)).upper()}"
        elif _aware_utc(game.starts_at) <= moment:
            reason = "GAME_ALREADY_STARTED"
        elif datetime.fromisoformat(event["cutoff_utc"]) <= moment:
            reason = "PURCHASE_CUTOFF_PASSED"
        else:
            latest = session.scalar(
                select(GameStateObservation).where(GameStateObservation.game_id == game.id)
                .order_by(GameStateObservation.available_as_of.desc()).limit(1))
            bound_at = session.scalar(
                select(func.min(OddsSnapshot.available_as_of))
                .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
                .join(Market, Market.id == MarketOutcome.market_id)
                .where(Market.game_id == game.id, OddsSnapshot.source == SPORTSGAMEODDS,
                       OddsSnapshot.source_event_id == event_id))
            at_binding = None if bound_at is None else session.scalar(
                select(GameStateObservation.scheduled_at)
                .where(GameStateObservation.game_id == game.id,
                       GameStateObservation.available_as_of <= bound_at)
                .order_by(GameStateObservation.available_as_of.desc()).limit(1))
            if latest is not None and bool(latest.is_postponed):
                reason = "GAME_POSTPONED"
            elif at_binding is None:
                reason = "START_AT_BINDING_UNKNOWN"
            elif abs(_aware_utc(at_binding) - _aware_utc(game.starts_at)) > START_UNCHANGED_TOLERANCE:
                reason = "START_MOVED_SINCE_ID_BOUND"
        if reason:
            blockers.append({"game_id": game_id, "event_id": event_id, "reason": reason})
    return blockers


def check_exact_response(
    session: Session, fetch: "FallbackFetch", requested: dict[str, str], *, moment: datetime,
) -> dict[str, Any]:
    """Every local check an exact-ID answer must pass before anything is ingested.

    ``requested`` maps game id -> event id. The returned IDs must equal the
    requested set; each returned event must be MLB, pregame by its own status
    fields (not started, live, completed, ended, cancelled or finalized), start
    in the future, and bind to exactly its requested game through the canonical
    binder (league, teams and orientation, start within the binder's tolerance).
    """

    from app.ingestion.normalizer import CanonicalNormalizer
    from app.models import Game

    by_event = {str(e.get("event_id")): e for e in fetch.returned_events if e.get("event_id")}
    returned = sorted(by_event)
    wanted = sorted(set(requested.values()))
    extra = sorted(set(returned) - set(wanted))
    missing = sorted(set(wanted) - set(returned))
    game_of = {event_id: game_id for game_id, event_id in requested.items()}
    normalizer = CanonicalNormalizer(session)
    failures: dict[str, str] = {}
    for event_id in sorted(set(returned) & set(wanted)):
        event = by_event[event_id]
        flags = [flag for flag, on in (event.get("status") or {}).items() if on]
        starts = None
        try:
            starts = datetime.fromisoformat(str(event.get("starts_at")).replace("Z", "+00:00"))
        except ValueError:
            starts = None
        if event.get("league_id") != "MLB":
            failures[event_id] = "EVENT_LEAGUE_NOT_MLB"
        elif flags:
            failures[event_id] = f"EVENT_STATUS_{flags[0].upper()}"
        elif starts is None:
            failures[event_id] = "EVENT_START_MISSING"
        elif _aware_utc(starts) <= moment:
            failures[event_id] = "EVENT_ALREADY_STARTED"
        else:
            game = session.get(Game, _uuid(game_of[event_id]))
            match, why = (None, "GAME_NOT_FOUND") if game is None else normalizer._match_game(
                [game], event.get("home_name") or "", event.get("away_name") or "", _aware_utc(starts))
            if match is None:
                failures[event_id] = f"EVENT_IDENTITY_{str(why).upper()}"
    allowed = sorted(event_id for event_id in set(returned) & set(wanted) if event_id not in failures)
    return {"requested_ids": wanted, "returned_ids": returned, "extra_ids": extra,
            "missing_ids": missing, "failures": failures, "allowed_ids": allowed,
            "match": not extra and not missing and not failures}


def _restrict_to_events(fetch: "FallbackFetch", allowed: list[str]) -> None:
    """Drop every quote from an event that failed a check; nothing else changes."""

    keep = set(allowed)
    fetch.pairs = [pair for pair in fetch.pairs if str(pair.event_id) in keep]
    if fetch.main is not None:
        fetch.main.quotes = [quote for quote in fetch.main.quotes if str(quote.event_id) in keep]


def verified_sgo_event_ids(session: Session, game_ids: list[str]) -> dict[str, str]:
    """The SportsGameOdds event ID AISP itself bound to each game, when unambiguous.

    Only IDs on SPORTSGAMEODDS quote rows count. Those rows exist only when the
    canonical normalizer bound the provider event to that exact game: same
    league, same home/away orientation, first pitch within tolerance, a unique
    nearest candidate (``CanonicalNormalizer._match_game``). An ID is verified
    only when the game carries exactly one such ID and no other game carries
    it. Unbound-event receipts, NFL franchise mappings and anything else are
    not identities for an MLB game and are ignored.
    """

    if not game_ids:
        return {}
    from sqlalchemy import func, select

    from app.models import Market, MarketOutcome, OddsSnapshot

    ids = [str(value) for value in game_ids]
    rows = session.execute(
        select(Market.game_id, OddsSnapshot.source_event_id)
        .join(MarketOutcome, MarketOutcome.market_id == Market.id)
        .join(OddsSnapshot, OddsSnapshot.market_outcome_id == MarketOutcome.id)
        .where(
            Market.game_id.in_([_uuid(value) for value in ids]),
            OddsSnapshot.source == SPORTSGAMEODDS,
            OddsSnapshot.source_event_id.is_not(None),
        )
        .distinct()
    ).all()
    per_game: dict[str, set[str]] = {}
    for game_id, event_id in rows:
        if str(event_id or "").strip():
            per_game.setdefault(str(game_id), set()).add(str(event_id))
    candidates = {g: next(iter(v)) for g, v in per_game.items() if len(v) == 1}
    if not candidates:
        return {}
    # An ID bound to a second game anywhere is not an identity for either.
    shared = session.execute(
        select(OddsSnapshot.source_event_id, func.count(func.distinct(Market.game_id)))
        .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
        .join(Market, Market.id == MarketOutcome.market_id)
        .where(OddsSnapshot.source == SPORTSGAMEODDS,
               OddsSnapshot.source_event_id.in_(sorted(set(candidates.values()))))
        .group_by(OddsSnapshot.source_event_id)
    ).all()
    reused = {str(event_id) for event_id, games in shared if int(games) > 1}
    return {g: e for g, e in candidates.items() if e not in reused}


def _event_start(event: dict[str, Any]) -> datetime:
    value = datetime.fromisoformat(event["starts_at_utc"])
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC)


def unambiguous_prefix(
    selected: list[dict[str, Any]], events: list[dict[str, Any]], *,
    window_start: datetime, window_end: datetime, moment: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The part of ``selected`` the provider's answer is certain to equal.

    ``events`` is every canonical game on the slate. The provider answers with
    the earliest ``limit`` events in [window_start, window_end]; ties at one
    first pitch come back in its own order. So a selection is safe exactly when,
    walking the window's start-time groups earliest first, each group is either
    wholly selected or lies after the last selected game. Returns (kept,
    deferred); each deferred entry names its game and reason.
    """

    chosen = {str(e.get("game_id")) for e in selected}
    window = sorted(
        (e for e in events
         if window_start <= _event_start(e) <= window_end and _event_start(e) > moment),
        key=lambda e: (_event_start(e), str(e.get("game_id"))),
    )
    groups: list[tuple[datetime, list[dict[str, Any]]]] = []
    for event in window:
        start = _event_start(event)
        if groups and groups[-1][0] == start:
            groups[-1][1].append(event)
        else:
            groups.append((start, [event]))
    kept_ids: list[str] = []
    for _start, group in groups:
        ids = {str(e.get("game_id")) for e in group}
        if len(kept_ids) == len(chosen):
            break
        if ids <= chosen:
            kept_ids.extend(sorted(ids))
            continue
        break
    kept = [e for e in selected if str(e.get("game_id")) in set(kept_ids)]
    deferred = []
    unselected_starts = {_event_start(e) for e in window if str(e.get("game_id")) not in chosen}
    for event in selected:
        if str(event.get("game_id")) in set(kept_ids):
            continue
        tied = _event_start(event) in unselected_starts
        deferred.append({"game_id": str(event.get("game_id")),
                         "starts_at_utc": _event_start(event).isoformat(),
                         "reason": DEFERRED_TIED_START if tied else DEFERRED_EARLIER_GAME})
    return kept, deferred


def collect_for_open_cutoffs(
    session: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
    job_run_id: Any = None,
) -> dict[str, Any]:
    """One bounded fallback fetch for MLB games still before their cutoff.

    Everything that could turn this into a second provider is refused here and
    named: the flag, the credential, the dated window, the cooldown, the object
    budget, and -- first of all -- whether there is any game left to help. A
    game past its cutoff is never requested, because a price that arrives after
    the decision is not evidence, it is noise that costs an object.
    """
    from app.services.quote_coverage import (
        MAX_QUOTE_STALENESS_MINUTES,
        QuoteCoverageService,
    )

    moment = now or datetime.now(UTC)
    ok, reason = eligible(settings, now=moment)
    if not ok:
        return {"attempted": False, "reason": reason, "objects": 0}

    # A LATCHED LANE SPENDS NOTHING, AND IS NOT RETRIED.
    #
    # Checked before the cooldown and before the budget, because a halt is not a
    # rate limit: it means the last object bought an answer to a question this
    # lane did not ask, and the owner has not yet decided whether the provider
    # can be trusted to answer it. Nothing here lifts it.
    from app.services.collection_halt import active_halt

    halt = active_halt(session, lane=ENDPOINT_KEY)
    if halt is not None:
        return {"attempted": False, "reason": "LANE_HALTED", "objects": 0,
                "halt": {"lane": halt.lane, "halt_reason": halt.reason,
                         "halted_at": halt.halted_at.isoformat(),
                         "detail": halt.detail,
                         "evidence": halt.evidence},
                "detail": ("This lane is stopped pending an owner decision. No "
                           "object is spent and no retry is attempted.")}

    # No retry storms. The free tier refreshes every ten minutes, so a tighter
    # cadence re-requests prices that cannot have changed -- and a failing
    # provider must not be hammered by a five-minute scheduler tick, so the
    # cooldown counts from the last ATTEMPT, not the last success.
    #
    # Scoped to THIS lane: another lane's purchase spends another allowance on
    # another sport's games, and it never made this lane's prices any fresher.
    since = seconds_since_last_fetch(session, now=moment, lane=ENDPOINT_KEY)
    if since is not None and since < MIN_SECONDS_BETWEEN_FETCHES:
        return {"attempted": False, "reason": "FALLBACK_COOLDOWN", "objects": 0,
                "cooldown_lane": ENDPOINT_KEY,
                "seconds_since_last_fetch": round(since, 1),
                "cooldown_seconds": MIN_SECONDS_BETWEEN_FETCHES}

    coverage = QuoteCoverageService(session, settings).daily_report(league_code="MLB")
    # "Has a quote" is not coverage. A game whose quote will be outside the
    # freshness window by the time the decision is taken is refresh-eligible,
    # because the alternative is a price that is present and unusable -- which
    # reads as covered right up until it silently is not.
    #
    # ...but a refresh only counts if it can still be fresh AT the cutoff. A
    # 10pm game asked for at lunchtime is permanently "needs_refresh": the
    # quote we fetch will have aged out of its own freshness window hours
    # before anyone decides on it, so the next tick asks again, and the next.
    # That is how a bounded allowance gets spent on games it cannot help while
    # the games that could be helped go unfunded. So a game enters the request
    # only once its freshness window has actually opened.
    #
    # Coverage is measured on the MONEYLINE, which was the only market this
    # collector retained. Now that the same event object also yields the full
    # game total and run line, a game can be "covered" and still have no total
    # at all -- and it would stay that way forever, because the moneyline that
    # satisfies the gate never goes stale before first pitch.
    #
    # So a second, narrower reason to refresh: a game inside its window whose
    # full-game total has never been retained. It costs nothing extra (billing
    # is per event object, and the moneyline is in the same response), it is
    # bounded by the same freshness window, cooldown and object budget, and it
    # stops asking as soon as a total exists.
    missing_totals = _games_missing_total(
        session, [event["game_id"] for event in coverage["events"]])

    window_open: list[dict[str, Any]] = []
    too_early = 0
    totals_only = 0
    for event in coverage["events"]:
        cutoff = datetime.fromisoformat(event["cutoff_utc"])
        if cutoff <= moment:
            continue
        needs_moneyline = event.get("needs_refresh", not event["covered"])
        needs_total = event["game_id"] in missing_totals
        if not needs_moneyline and not needs_total:
            continue
        if moment < cutoff - timedelta(minutes=MAX_QUOTE_STALENESS_MINUTES):
            too_early += 1
            continue
        if not needs_moneyline:
            totals_only += 1
        window_open.append(event)
    # DETERMINISTIC SELECTION, DECLARED BEFORE RESULTS.
    #
    # The day's allocation no longer covers every game, so which games get a
    # price has to be decided by a rule rather than by whatever order a query
    # returned. Earliest cutoff first: the game closest to locking is the one
    # whose price is about to become unobtainable, and a rule tied to the clock
    # cannot be influenced by an edge, a forecast or an outcome. Ties break on
    # the provider's own game id, so the same slate always produces the same
    # order.
    #
    # Games past the allocation are reported as unsupported by name rather than
    # silently dropped.
    # COVERAGE BEFORE REFRESH, then earliest cutoff.
    #
    # Ordering by cutoff alone re-buys the nearest game every run. On
    # 2026-09-14 that spent two objects refreshing CWS at CLE -- already priced
    # across nine books -- while SD at COL, SEA at LAA and MIA at AZ had no
    # price at all and the lane had seven objects spare. A game with no stored
    # pair cannot be helped by anything except a request; a game that already
    # has one is only being made slightly fresher. So an uncovered game sorts
    # first, and within each group the rule is unchanged: earliest cutoff, ties
    # on the provider's game id, decided before any result is seen.
    open_games = sorted(
        window_open,
        key=lambda event: (
            bool(event.get("covered")),
            event["cutoff_utc"],
            str(event.get("game_id") or ""),
        ),
    )
    if not open_games:
        return {"attempted": False,
                "reason": ("NO_GAME_INSIDE_ITS_FRESHNESS_WINDOW_NEEDS_A_QUOTE"
                           if too_early else "NO_UNCOVERED_GAME_BEFORE_CUTOFF"),
                "objects": 0,
                "uncovered_but_window_not_open": too_early,
                "freshness_window_minutes": MAX_QUOTE_STALENESS_MINUTES}

    # Bound the request to the games that can still be helped: never earlier
    # than now, never later than the last open cutoff.
    last_cutoff = max(datetime.fromisoformat(event["cutoff_utc"]) for event in open_games)
    # Every game in scope now has its cutoff within the freshness window, so
    # the horizon is at most that window plus an hour of slack -- not the 24
    # hours a full-day sweep used to reach across.
    horizon_hours = max(1, min(24, int((last_cutoff - moment).total_seconds() // 3600) + 2))
    wanted = min(len(open_games), MAX_EVENTS_PER_FETCH)

    budget = object_budget(session, now=moment)
    allowed, refusal = budget.may_spend(wanted)
    if not allowed:
        return {"attempted": False, "reason": "OBJECT_BUDGET_EXHAUSTED", "objects": 0,
                "detail": refusal, "budget": budget.as_dict()}

    # The shared allocation has the final say. The provider reserve above is
    # kept as a second, cruder guard -- it protects against a bug in this
    # planner, which is exactly the sort of thing a reserve is for.
    from app.services.collection_halt import (
        PAID_STORED_NOTHING, SELECTION_MISMATCH, halt_lane,
        mismatch as selection_mismatch, paid_and_stored_nothing,
    )
    from app.services.provider_allocation import (
        owner_exception_state, request_objects,
    )

    grant = request_objects(session, lane=ENDPOINT_KEY, wanted=wanted, now=moment)

    # THE OWNER'S DATED EXCEPTION, TRIED ONLY AFTER THE ORDINARY ALLOWANCE SAID NO.
    #
    # `owner_exception_state` is inert on every day but the one the owner named,
    # for every lane but this one, and for every game but the four it lists, so
    # on any other date this block resolves to "no candidates" and returns the
    # same refusal it always did. When it does apply, the selection is cut to
    # exactly those named games that are still unpriced -- never the day's
    # ranked prefix, which could reach a game the owner did not authorise -- and
    # every protection below it is untouched: the unambiguous-prefix deferral
    # still trims this selection, the mismatch halt still latches the lane, the
    # raw response is still archived and the cooldown still owns the next run.
    exception_state = owner_exception_state(session, lane=ENDPOINT_KEY, now=moment)
    exception_selection: list[dict[str, Any]] = []
    if not grant.allowed and exception_state["units_available"] > 0:
        allowed_ids = set(exception_state["available_game_ids"])
        exception_selection = [event for event in open_games
                               if str(event.get("game_id")) in allowed_ids]
        if exception_selection:
            exception_grant = request_objects(
                session, lane=ENDPOINT_KEY, wanted=len(exception_selection),
                now=moment,
                selection=[str(event.get("game_id")) for event in exception_selection])
            if exception_grant.allowed:
                grant = exception_grant
                exception_selection = exception_selection[:grant.granted]
            else:
                exception_selection = []

    if not grant.allowed:
        return {"attempted": False, "reason": f"ALLOCATION_{grant.reason}",
                "objects": 0, "allocation": grant.as_dict(),
                "games_unsupported_today": [
                    str(event.get("game_id")) for event in open_games],
                "selection_rule": SELECTION_RULE}
    # Ask for no more than the day funds. A partial grant is a smaller slate of
    # games covered, not a smaller number of prices per game.
    wanted = grant.granted
    if exception_selection:
        chosen_ids = {id(event) for event in exception_selection}
        covered = list(exception_selection)
        unsupported = [event for event in open_games if id(event) not in chosen_ids]
    else:
        covered, unsupported = open_games[:wanted], open_games[wanted:]

    # THE CHOICE, EXPRESSED AS A FILTER.
    #
    # Sorting `open_games` decides nothing on its own: the request below is what
    # the provider actually answers, and a bare limit + "from now" window hands
    # back the earliest games in that window. On 2026-09-14 that is precisely
    # what happened -- the collector selected SD at COL and the object bought a
    # refresh of CWS at CLE, which starts two hours earlier and already had a
    # price across nine books.
    #
    # Anchoring `startsAfter` one second below the earliest SELECTED first pitch
    # excludes every game the rule ranked lower, so the games that can still be
    # helped are the only ones the object can land on. The anchor is never moved
    # backwards past `moment`, because a game already under way is not pregame.
    anchor = min(
        datetime.fromisoformat(event["starts_at_utc"]) for event in covered
    ) - timedelta(seconds=1)
    anchor = max(anchor.astimezone(UTC), moment)
    window_end = moment + timedelta(hours=horizon_hours)

    # NEVER A REQUEST THE PROVIDER MAY ANSWER WITH A DIFFERENT GAME.
    #
    # Exact selection names the provider events AISP itself bound to these
    # games (sgo_exact_event_selection_mode):
    #   OFF       never.
    #   VALIDATE  every selected game must have a verified ID and pass the
    #             local pre-send checks, then:
    #             * an UNAMBIGUOUS selection is sent by ID. Its match is evidence
    #               only: the window alone selects the same games, so it proves
    #               nothing about the ID filter and never transitions anything.
    #             * the FIRST ambiguous selection -- one the window could answer
    #               with a different game -- is sent by ID ONCE, as the owner's
    #               authorised DISCRIMINATING_TEST: the purchase the schedule
    #               makes anyway, same limit, same objects, no retry, no wider
    #               query. At most one test ever, whatever its result.
    #             * if that test PASSED (exact IDs back, every local check, no
    #               halt, raw response archived) VALIDATE behaves as ON from
    #               then on; the transition is recorded once, on the passing
    #               purchase. If it failed, VALIDATE stays deferral-only.
    #             Both facts are read from the ledger at request time.
    #   ON        whenever every selected game has a verified ID and passes.
    # Otherwise the selection is cut to its unambiguous prefix and the rest is
    # deferred by name; if nothing unambiguous is left, nothing is requested.
    mode = exact_selection_mode(settings)
    exact_ids: list[str] | None = None
    exact_by_game: dict[str, str] = {}
    exact_basis: str | None = None
    test_kind: str | None = None
    window_equivalent: bool | None = None
    id_blockers: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    test_state: dict[str, Any] | None = None
    if mode != "OFF":
        selected_game_ids = [str(e.get("game_id")) for e in covered]
        verified = verified_sgo_event_ids(session, selected_game_ids)
        if all(game_id in verified for game_id in selected_game_ids):
            id_blockers = exact_id_blockers(session, covered, verified, moment=moment)
            if not id_blockers:
                _kept, would_defer = unambiguous_prefix(
                    covered, coverage["events"], window_start=anchor, window_end=window_end,
                    moment=moment)
                window_equivalent = not would_defer
                if mode == "ON":
                    exact_basis, test_kind = "MODE_ON", "ON"
                else:
                    test_state = discriminating_test_state(session)
                    if test_state["passed"]:
                        exact_basis, test_kind = "VALIDATE_ON_EQUIVALENT", "ON_EQUIVALENT"
                    elif window_equivalent:
                        exact_basis, test_kind = ("VALIDATE_UNAMBIGUOUS_SELECTION",
                                                  "UNAMBIGUOUS_WINDOW_MATCH")
                    elif not test_state["attempted"]:
                        exact_basis, test_kind = ("VALIDATE_DISCRIMINATING_TEST",
                                                  DISCRIMINATING_TEST)
                if exact_basis is not None:
                    exact_by_game = {game_id: verified[game_id] for game_id in selected_game_ids}
                    exact_ids = [verified[game_id] for game_id in selected_game_ids]
    if exact_ids is None:
        covered, deferred = unambiguous_prefix(
            covered, coverage["events"], window_start=anchor, window_end=window_end, moment=moment)
        if not covered:
            return {**({} if mode == "OFF" else {"exact_event_selection_mode": mode,
                                                  "exact_id_blockers": id_blockers,
                                                  "discriminating_test_state": test_state}),
                    "attempted": False, "reason": "DEFERRED_AMBIGUOUS_SELECTION", "objects": 0,
                    "games_deferred": deferred,
                    "games_unsupported_today": [str(e.get("game_id")) for e in unsupported],
                    "detail": ("Every selected game shares its request window with an "
                               "unselected game the provider could return instead, and no "
                               "verified provider event ID can name it. No request was "
                               "sent; no object was spent."),
                    "selection_rule": SELECTION_RULE}
        wanted = len(covered)

    params = {
        "leagueID": "MLB", "limit": str(wanted),
        # `anchor` is already UTC (see its `.astimezone(UTC)` above) and
        # `window_end` is built from `moment`, so this stamping is unchanged in
        # value. It goes through `utc_stamp` so the module has ONE way to write
        # a provider timestamp and no call site can be added without it.
        "startsAfter": utc_stamp(anchor),
        "startsBefore": utc_stamp(window_end),
        "finalized": "false", "oddsPresent": "true",
    }
    if exact_ids is not None:
        params[EVENT_ID_FILTER_PARAM] = ",".join(exact_ids)
    # Written into the RESERVATION, before the request is sent, and naming the
    # FINAL selection -- after the unambiguous-prefix cut above, so a game this
    # request deferred keeps its unit. It is what a concurrent run reads to see
    # that these named games' single objects are already committed.
    exception_receipt: dict[str, Any] | None = None
    if exception_selection:
        exception_receipt = {
            "authority": (exception_state["exception"] or {}).get("authority"),
            "citation": (exception_state["exception"] or {}).get("citation"),
            "service_date": (exception_state["exception"] or {}).get("service_date"),
            "game_ids": [str(event.get("game_id")) for event in covered],
            "objects": wanted,
            "grant_reason": grant.reason,
        }
    reservation = record_request(
        session, requested_at=moment, params=params, budget=budget,
        job_run_id=job_run_id, objects_requested=wanted,
        **({} if exception_receipt is None
           else {"extra_metadata": {"owner_exception": exception_receipt}}))
    session.commit()

    try:
        # `now` stays the true retrieval moment -- it becomes `available_as_of`
        # on every ingested row, and moving it to suit a filter would be
        # backdating retrieval. Only the start-time window carries the choice.
        fetch = fetch_pregame_moneylines(
            settings, limit=wanted, now=moment,
            starts_within_hours=horizon_hours, starts_after=anchor,
            request_fingerprint=getattr(reservation, "request_fingerprint", None),
            **({} if exact_ids is None else {"event_ids": exact_ids}))
    except Exception as error:  # one attempt; the cooldown owns the next one
        # An ID request that failed in transport is still recorded as evidence;
        # a discriminating test that failed this way has been used.
        failed_evidence = None if exact_ids is None else {"exact_event_selection": {
            "mode": mode, "basis": exact_basis, "test_kind": test_kind,
            "discriminating": test_kind == DISCRIMINATING_TEST,
            "requested_ids": sorted(set(exact_ids)), "returned_ids": [], "match": False,
            "passed": False if test_kind == DISCRIMINATING_TEST else None,
            "reasons": [f"REQUEST_FAILED_{type(error).__name__}"],
            "window_alone_would_select_the_same": window_equivalent,
            "reservation_ledger_id": str(getattr(reservation, "id", "") or "") or None,
            "request_fingerprint": getattr(reservation, "request_fingerprint", None)}}
        complete_request(session, reservation, objects=0, succeeded=False, budget=budget,
                         error=type(error).__name__, job_run_id=job_run_id,
                         **({} if failed_evidence is None else {"extra_metadata": failed_evidence}))
        session.commit()
        return {**({} if failed_evidence is None else failed_evidence),
                "attempted": True, "reason": "FALLBACK_REQUEST_FAILED", "objects": 0,
                "error_type": type(error).__name__}

    # AN ID ANSWER IS CHECKED LOCALLY BEFORE ANYTHING IS INGESTED. The provider
    # applied none of the window/status filters to it, so an event that fails
    # identity, status or schedule is dropped here with its reason.
    exact_check: dict[str, Any] | None = None
    if exact_ids is not None:
        exact_check = check_exact_response(session, fetch, exact_by_game, moment=moment)
        _restrict_to_events(fetch, exact_check["allowed_ids"])
    ingest(session, settings, fetch, league_code="MLB")
    ingested = _ingested_game_ids(session, fetch)

    # The mismatch is decided here -- before the terminal row is written -- so
    # the evidence can say whether this purchase halted the lane. The halt
    # itself still follows below, unchanged.
    selected_ids = [str(event.get("game_id")) for event in covered]
    divergence = selection_mismatch(selected_ids, ingested)
    # On an ID request, ANY difference between the IDs asked for and the IDs
    # returned -- an extra event or a missing one -- is a mismatch too, and it
    # takes the same halt. The unrequested event was never ingested above.
    if divergence is None and exact_check is not None and (
            exact_check["extra_ids"] or exact_check["missing_ids"]):
        divergence = {"unselected_games_returned": [], "games_selected": sorted(selected_ids),
                      "games_ingested": sorted(ingested), "selected_games_missing": [],
                      "unrequested_event_ids_returned": exact_check["extra_ids"],
                      "requested_event_ids_missing": exact_check["missing_ids"],
                      "requested_event_ids": exact_check["requested_ids"],
                      "returned_event_ids": exact_check["returned_ids"]}

    # EVIDENCE, written on every ID purchase, matched or not.
    exact_record: dict[str, Any] | None = None
    transition: dict[str, Any] | None = None
    if exact_check is not None:
        archive_sha = (fetch.raw_archive or {}).get("responseBodySha256")
        reasons = ((["EXTRA_EVENT_IDS"] if exact_check["extra_ids"] else [])
                   + (["MISSING_EVENT_IDS"] if exact_check["missing_ids"] else [])
                   + (["LOCAL_CHECK_FAILURES"] if exact_check["failures"] else [])
                   + (["MISMATCH_HALT"] if divergence is not None else [])
                   + ([] if archive_sha else ["RAW_ARCHIVE_MISSING"]))
        passed = exact_check["match"] and divergence is None and bool(archive_sha)
        exact_record = {
            "mode": mode, "basis": exact_basis, "test_kind": test_kind,
            # A match on an unambiguous selection proves nothing about the ID
            # filter; only the one DISCRIMINATING_TEST can.
            "discriminating": test_kind == DISCRIMINATING_TEST,
            "requested_ids": exact_check["requested_ids"],
            "returned_ids": exact_check["returned_ids"],
            "extra_ids": exact_check["extra_ids"], "missing_ids": exact_check["missing_ids"],
            "failures": exact_check["failures"],
            "match": exact_check["match"],
            "passed": passed if test_kind == DISCRIMINATING_TEST else None,
            "reasons": reasons,
            "window_alone_would_select_the_same": window_equivalent,
            "raw_archive_sha256": archive_sha,
            # The RESERVED row this terminal row settles, and the fingerprint
            # the archived response is bound to.
            "reservation_ledger_id": str(getattr(reservation, "id", "") or "") or None,
            "request_fingerprint": getattr(reservation, "request_fingerprint", None),
        }
        if test_kind == DISCRIMINATING_TEST and passed:
            # The owner-authorised bounded transition, recorded once, on the
            # purchase that earned it.
            transition = {"from": "VALIDATE", "to": "ON_EQUIVALENT", "at": moment.isoformat(),
                          "basis_ledger_id": exact_record["reservation_ledger_id"],
                          "basis_request_fingerprint": exact_record["request_fingerprint"],
                          "raw_archive_sha": archive_sha}
    terminal_metadata: dict[str, Any] = {"raw_archive": fetch.raw_archive}
    if exact_record is not None:
        terminal_metadata["exact_event_selection"] = exact_record
    if transition is not None:
        terminal_metadata["exact_event_selection_transition"] = transition
    complete_request(session, reservation, objects=fetch.objects, succeeded=True,
                     budget=budget, job_run_id=job_run_id,
                     extra_metadata=terminal_metadata)
    if exact_record is not None:
        from sqlalchemy import select as _select

        from app.models import ProviderRequestLedger

        # The terminal row's own id, for the job result (the row itself is
        # append-only, so it names its reservation instead).
        exact_record = {**exact_record, "terminal_ledger_id": str(session.scalar(
            _select(ProviderRequestLedger.id).where(
                ProviderRequestLedger.reservation_id == reservation.id,
                ProviderRequestLedger.status == "SUCCEEDED")) or "") or None}

    # THE FIRST MISMATCH STOPS THE LANE.
    #
    # The request now names the games it wants, so a response carrying a game
    # that was never selected means an object was charged for a question nobody
    # asked. Retrying spends a second object to re-ask a question the provider
    # has just declined to answer, so nothing is retried and the lane latches
    # shut on the single occurrence.
    #
    # The rows already written stay. They are real prices from a real response
    # and deleting them would destroy the evidence of what happened. The
    # completed reservation stays too: the object WAS charged, and a ledger that
    # hid that would be worse than the fault it was hiding. (`divergence` was
    # decided above, before the terminal row, so the evidence could name it.)
    halted = None
    if divergence is not None:
        halted = halt_lane(
            session, lane=ENDPOINT_KEY, reason=SELECTION_MISMATCH,
            evidence={"request_filters": params, "mismatch": divergence,
                      "objects_charged": fetch.objects,
                      "events_returned": fetch.events_ingested,
                      "selection_rule": SELECTION_RULE,
                      "requested_at": moment.isoformat()},
            detail=("The provider returned a game this request did not select. "
                    "The lane is stopped after this single occurrence; no "
                    "object was spent on a retry."),
            now=moment,
        )
    # THE OTHER HALF OF THE SAME QUESTION, ON THIS LANE TOO.
    #
    # `divergence` can only see games that BOUND, so a charged response that
    # bound nothing at all leaves it None -- which is exactly how the NFL lane
    # paid three times for the same useless answer on 2026-09-20. This lane has
    # never shown that fault (its request window was verified correct to the
    # second across all 13 archives that day), and this guard is inert while it
    # stays correct: it can only fire when objects were CHARGED and not one
    # selected game was stored, which on a working lane never happens.
    #
    # It is checked second so a response carrying someone else's game keeps the
    # more specific PROVIDER_RETURNED_UNSELECTED_GAME reason.
    paid_empty = None
    if halted is None:
        paid_empty = paid_and_stored_nothing(
            selected_ids, ingested, objects_charged=fetch.objects)
        if paid_empty is not None:
            halted = halt_lane(
                session, lane=ENDPOINT_KEY, reason=PAID_STORED_NOTHING,
                evidence={"request_filters": params, "mismatch": paid_empty,
                          "mismatch_reason": PAID_STORED_NOTHING,
                          "objects_charged": fetch.objects,
                          "games_selected": sorted(selected_ids),
                          "games_ingested": sorted(ingested),
                          "events_returned": fetch.events_ingested,
                          "raw_archive": getattr(fetch, "raw_archive", None),
                          "selection_rule": SELECTION_RULE,
                          "requested_at": moment.isoformat()},
                detail=("Objects were charged and not one selected game was "
                        "stored. The lane is stopped after this single "
                        "occurrence; no object is spent on a retry."),
                now=moment,
            )
    session.commit()
    after = object_budget(session, now=moment)
    # Named only when they happened, so an unambiguous request's receipt is
    # unchanged.
    selection_receipt: dict[str, Any] = {}
    if exception_receipt is not None:
        selection_receipt["owner_exception"] = exception_receipt
    if deferred:
        selection_receipt["games_deferred"] = deferred
    if exact_ids is not None:
        selection_receipt["exact_event_ids"] = exact_ids
    if mode != "OFF":
        selection_receipt["exact_event_selection_mode"] = mode
        selection_receipt["exact_id_blockers"] = id_blockers
        if exact_record is not None:
            selection_receipt["exact_event_selection"] = exact_record
        if transition is not None:
            selection_receipt["exact_event_selection_transition"] = transition
        if test_state is not None:
            selection_receipt["discriminating_test_state"] = test_state
    return {
        **selection_receipt,
        "attempted": True, "reason": "FALLBACK_COLLECTED",
        "objects": fetch.objects,
        "games_open_before_cutoff": len(open_games),
        "selection_rule": SELECTION_RULE,
        # WHAT WAS CHOSEN, AND WHAT THE OBJECT ACTUALLY BOUGHT.
        #
        # These were one field, named for the first and read as the second. On
        # 2026-09-14 it reported SD at COL as covered by a request whose rows
        # all landed on CWS at CLE -- a true statement about the intent and a
        # false one about the slate. They are separate now, and the second is
        # measured from the canonical games the normalizer actually bound.
        "games_selected": [str(e.get("game_id")) for e in covered],
        "games_ingested": ingested,
        # A charged response that stored none of the selection did not have its
        # selection come back either, so it is not "reached" on that receipt.
        "selection_reached_provider": divergence is None and paid_empty is None,
        "paid_stored_nothing": paid_empty is not None,
        "lane_halted": halted is not None,
        "selection_mismatch": divergence,
        # Named, not silently dropped: a reader can see exactly which games this
        # slate could not afford a price for.
        "games_unsupported_today": [str(e.get("game_id")) for e in unsupported],
        "games_admitted_for_missing_total_only": totals_only,
        "uncovered_but_window_not_open": too_early,
        "events_ingested": fetch.events_ingested,
        "quote_rows": fetch.quotes_written,
        # Events that were not bound to an MLB game, each with its reason.
        "unbound_events": fetch.unbound,
        # Totals and run lines from the SAME event objects. Reported separately
        # so the receipt shows the extra coverage arrived for zero extra cost.
        "market_quote_rows": fetch.market_quotes_written,
        "main_markets": (fetch.main.by_market() if fetch.main else {}),
        "markets_observed_not_ingested": (
            dict(fetch.main.observed_not_ingested) if fetch.main else {}),
        "pairs": len(fetch.pairs),
        "bookmakers": sorted({pair.bookmaker_key for pair in fetch.pairs}),
        "skew": fetch.skew_summary(),
        "request_filters": params,
        # Where the response's exact bytes were kept (or why not). Recording a
        # failure here is the whole of the failure handling: the fetch stands.
        "raw_archive": fetch.raw_archive,
        "budget": after.as_dict(),
    }


def _ingested_game_ids(session: Session, fetch: FallbackFetch) -> list[str]:
    """The canonical games this response actually landed on.

    Read back from the binding the normalizer made, not from the events we hoped
    for: an event the provider substituted, or one that failed to bind to a
    canonical game, must not be able to appear here.
    """
    from sqlalchemy import select

    from app.models import Game, Market, MarketOutcome, OddsSnapshot

    source_ids = {str(pair.event_id) for pair in fetch.pairs if pair.event_id}
    if fetch.main is not None:
        source_ids |= {
            str(quote.event_id) for quote in fetch.main.quotes if quote.event_id}
    if not source_ids:
        return []
    rows = session.execute(
        select(Market.game_id)
        .join(Game, Game.id == Market.game_id)
        .join(MarketOutcome, MarketOutcome.market_id == Market.id)
        .join(OddsSnapshot, OddsSnapshot.market_outcome_id == MarketOutcome.id)
        .where(OddsSnapshot.source_event_id.in_(source_ids))
        .distinct()
    ).all()
    return sorted({str(game_id) for (game_id,) in rows})


def apply_opening_adjustment(
    session: Session,
    *,
    objects: int,
    occurred_at: datetime,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """Record consumption that predates this ledger, exactly once.

    Idempotent by construction rather than by checking first: the fingerprint is
    derived from the adjustment's own content, and the ledger holds a unique
    constraint on (provider, endpoint_key, fingerprint, status). Running this
    twice is a no-op, which is what makes it safe to put in a runbook.

    It is dated to the day the objects were actually spent, so it ages out of
    the rolling window on the same schedule as a real request would.
    """
    from hashlib import sha256

    from sqlalchemy import select

    from app.models import ProviderRequestLedger

    payload = {
        "kind": "OPENING_BALANCE_ADJUSTMENT",
        "objects": int(objects),
        "occurred_at": occurred_at.date().isoformat(),
        "evidence": evidence,
        "not_a_request": (
            "Objects consumed by authorised validation fetches made before "
            "ledger accounting existed. Recorded as one adjustment rather than "
            "as reconstructed request rows, which would be fabrications."
        ),
    }
    fingerprint = sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    existing = session.scalar(
        select(ProviderRequestLedger).where(
            ProviderRequestLedger.provider == SPORTSGAMEODDS,
            ProviderRequestLedger.endpoint_key == OPENING_ADJUSTMENT_KEY,
            ProviderRequestLedger.request_fingerprint == fingerprint,
        )
    )
    if existing is not None:
        return {"applied": False, "reason": "ALREADY_RECORDED",
                "objects": int(objects), "ledger_id": str(existing.id)}

    reserved = ProviderRequestLedger(
        provider=SPORTSGAMEODDS, endpoint_key=OPENING_ADJUSTMENT_KEY,
        requested_at=occurred_at, status="RESERVED",
        request_fingerprint=fingerprint, controlled_live_request=False,
        source_mode="LIVE", response_metadata={"request_shape": payload},
    )
    session.add(reserved)
    session.flush()
    session.add(ProviderRequestLedger(
        provider=SPORTSGAMEODDS, endpoint_key=OPENING_ADJUSTMENT_KEY,
        requested_at=occurred_at, completed_at=occurred_at, status="SUCCEEDED",
        request_fingerprint=fingerprint, reservation_id=reserved.id,
        quota_cost=None, records_received=int(objects),
        controlled_live_request=False, source_mode="LIVE",
        response_metadata={"request_shape": payload},
    ))
    session.flush()
    return {"applied": True, "reason": "RECORDED", "objects": int(objects),
            "ledger_id": str(reserved.id)}

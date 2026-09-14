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
from dataclasses import dataclass
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
#: Objects are counted over a rolling window rather than a calendar month
#: because the local count is the only count there is -- the provider returns no
#: usage headers at all -- and a rolling window cannot under-count by sitting
#: just after a reset.
OBJECT_WINDOW_DAYS = 30
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
    starts_after: datetime | None = None,
) -> FallbackFetch:
    """Exactly one request for upcoming MLB events. No retry, no pagination.

    A second page would be a second request and a second object charge, so the
    caller is told what was returned and decides; nothing here loops.
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
        "startsAfter": anchor.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "startsBefore": (moment + timedelta(hours=starts_within_hours)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "finalized": "false",
        "oddsPresent": "true",
    }
    url = f"{settings.sportsgameodds_base_url.rstrip('/')}/events"
    headers = {"X-Api-Key": settings.sportsgameodds_api_key, "Accept": "application/json"}

    with httpx.Client(timeout=30.0, follow_redirects=False) as client:
        response = client.get(url, params=params, headers=headers)
    if response.status_code != 200:
        raise _refuse(f"SportsGameOdds returned HTTP {response.status_code}; not retried")
    payload = response.json()
    # Persist the raw response before parsing. If anything downstream fails, the
    # fix must never cost another request -- and therefore another object.
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(response.content)
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
    )


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
           *, event_date: date | None = None) -> FallbackFetch:
    """Normalize the fetched pairs through the ordinary ingestion path."""
    from app.ingestion.normalizer import CanonicalNormalizer

    normalizer = CanonicalNormalizer(session)
    events = to_event_dtos(fetch.pairs)
    if events:
        written = normalizer.ingest_odds(
            events, source=SPORTSGAMEODDS, available_as_of=fetch.requested_at,
            source_mode="LIVE", event_date=event_date,
        )
        fetch.events_ingested = len(events)
        fetch.quotes_written = int(written)

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
]


# --- ledger accounting and the object budget --------------------------------


@dataclass(frozen=True)
class ObjectBudget:
    """What the local count says is left.

    There is no provider figure to check it against: SportsGameOdds returns no
    usage headers at all, so this IS the number.
    """

    window_days: int
    requests: int
    objects_used: int
    allowance: int
    reserve: int

    @property
    def spendable(self) -> int:
        return max(0, self.allowance - self.reserve - self.objects_used)

    def may_spend(self, objects: int) -> tuple[bool, str | None]:
        if objects <= 0:
            return False, "a request must declare a positive object cost"
        if objects > self.spendable:
            return False, (
                f"object budget would be exceeded: {self.objects_used} used in "
                f"{self.window_days} days, {self.reserve} held in reserve of "
                f"{self.allowance}, so only {self.spendable} are spendable and "
                f"{objects} were requested"
            )
        return True, None

    def as_dict(self) -> dict[str, Any]:
        return {
            "window_days": self.window_days,
            "requests_in_window": self.requests,
            "objects_used_in_window": self.objects_used,
            "allowance": self.allowance,
            "reserve_never_spent": self.reserve,
            "objects_spendable": self.spendable,
            "counted_locally_because": "the provider returns no usage headers",
        }


def object_budget(session: Session, *, now: datetime | None = None) -> ObjectBudget:
    """Rolling-window request and object totals, counted from our own ledger."""
    from sqlalchemy import func, select

    from app.models import ProviderRequestLedger

    moment = now or datetime.now(UTC)
    since = moment - timedelta(days=OBJECT_WINDOW_DAYS)
    completed, counted, with_counts = session.execute(
        select(
            func.count(),
            func.coalesce(func.sum(ProviderRequestLedger.records_received), 0),
            func.count(ProviderRequestLedger.records_received),
        ).where(
            ProviderRequestLedger.provider == SPORTSGAMEODDS,
            ProviderRequestLedger.status.in_(("SUCCEEDED", "FAILED")),
            ProviderRequestLedger.requested_at >= since,
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
        window_days=OBJECT_WINDOW_DAYS,
        requests=int(requests),
        objects_used=int(counted) + unknown * MAX_EVENTS_PER_FETCH,
        allowance=FREE_TIER_OBJECTS,
        reserve=OBJECT_RESERVE,
    )


def seconds_since_last_fetch(session: Session, *, now: datetime | None = None) -> float | None:
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
    """
    from sqlalchemy import func, select

    from app.models import ProviderRequestLedger
    from app.services.provider_allocation import USAGE_ENDPOINT_KEY

    moment = now or datetime.now(UTC)
    latest = session.scalar(
        select(func.max(ProviderRequestLedger.requested_at)).where(
            ProviderRequestLedger.provider == SPORTSGAMEODDS,
            # Neither of these can consume an entity, so neither may hold off
            # the next real fetch.
            ProviderRequestLedger.endpoint_key.notin_(
                (OPENING_ADJUSTMENT_KEY, USAGE_ENDPOINT_KEY)),
        )
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
) -> Any:
    """Reserve the request in the provider ledger BEFORE it is sent.

    The row carries the filters and the bounded window so the request can be
    audited without the payload, and never any credential material: the API key
    travels in a header, is not in ``params``, and is recorded nowhere.
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
) -> None:
    """Write the terminal row: outcome, objects returned, allowance remaining.

    ``records_received`` is the billable count -- EVENTS, not prices and not
    requests. One ledger row is one request; the sum of ``records_received`` is
    the object spend. Keeping them in different columns is what makes the two
    countable separately.
    """
    from app.models import ProviderRequestLedger

    # Record the object count we actually know, success or failure. A transport
    # failure returned no events and cost nothing, and writing NULL there would
    # have it charged the worst case -- which would let an outage eat the
    # allowance. NULL is reserved for a row whose count is genuinely unknown.
    spent = max(0, objects)
    remaining = max(0, budget.allowance - budget.reserve - (budget.objects_used + spent))
    session.add(
        ProviderRequestLedger(
            provider=SPORTSGAMEODDS,
            endpoint_key=ENDPOINT_KEY,
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
                "objects_used_in_window_after": budget.objects_used + spent,
                "objects_spendable_after": remaining,
            },
        )
    )
    session.flush()


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
    since = seconds_since_last_fetch(session, now=moment)
    if since is not None and since < MIN_SECONDS_BETWEEN_FETCHES:
        return {"attempted": False, "reason": "FALLBACK_COOLDOWN", "objects": 0,
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
        SELECTION_MISMATCH, halt_lane, mismatch as selection_mismatch,
    )
    from app.services.provider_allocation import request_objects

    grant = request_objects(session, lane=ENDPOINT_KEY, wanted=wanted, now=moment)
    if not grant.allowed:
        return {"attempted": False, "reason": f"ALLOCATION_{grant.reason}",
                "objects": 0, "allocation": grant.as_dict(),
                "games_unsupported_today": [
                    str(event.get("game_id")) for event in open_games],
                "selection_rule": SELECTION_RULE}
    # Ask for no more than the day funds. A partial grant is a smaller slate of
    # games covered, not a smaller number of prices per game.
    wanted = grant.granted
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

    params = {
        "leagueID": "MLB", "limit": str(wanted),
        "startsAfter": anchor.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "startsBefore": (moment + timedelta(hours=horizon_hours)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "finalized": "false", "oddsPresent": "true",
    }
    reservation = record_request(session, requested_at=moment, params=params,
                                 budget=budget, job_run_id=job_run_id,
                                 objects_requested=wanted)
    session.commit()

    try:
        # `now` stays the true retrieval moment -- it becomes `available_as_of`
        # on every ingested row, and moving it to suit a filter would be
        # backdating retrieval. Only the start-time window carries the choice.
        fetch = fetch_pregame_moneylines(
            settings, limit=wanted, now=moment,
            starts_within_hours=horizon_hours, starts_after=anchor)
    except Exception as error:  # one attempt; the cooldown owns the next one
        complete_request(session, reservation, objects=0, succeeded=False, budget=budget,
                         error=type(error).__name__, job_run_id=job_run_id)
        session.commit()
        return {"attempted": True, "reason": "FALLBACK_REQUEST_FAILED", "objects": 0,
                "error_type": type(error).__name__}

    ingest(session, settings, fetch)
    ingested = _ingested_game_ids(session, fetch)
    complete_request(session, reservation, objects=fetch.objects, succeeded=True,
                     budget=budget, job_run_id=job_run_id)

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
    # hid that would be worse than the fault it was hiding.
    selected_ids = [str(event.get("game_id")) for event in covered]
    divergence = selection_mismatch(selected_ids, ingested)
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
    session.commit()
    after = object_budget(session, now=moment)
    return {
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
        "selection_reached_provider": divergence is None,
        "lane_halted": halted is not None,
        "selection_mismatch": divergence,
        # Named, not silently dropped: a reader can see exactly which games this
        # slate could not afford a price for.
        "games_unsupported_today": [str(e.get("game_id")) for e in unsupported],
        "games_admitted_for_missing_total_only": totals_only,
        "uncovered_but_window_not_open": too_early,
        "events_ingested": fetch.events_ingested,
        "quote_rows": fetch.quotes_written,
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

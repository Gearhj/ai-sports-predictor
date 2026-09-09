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
from app.data_sources.sgo_parser import AISP_MARKET_KEY, MoneylinePair, ParseResult, parse_events

ENDPOINT_KEY = "sgo_mlb_events"
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
    params = {
        "leagueID": "MLB",
        "limit": str(capped),
        # Documented filters only. "startsAfter" is what makes this a PREGAME
        # fetch; without it the provider returns games already in progress,
        # whose prices are unusable and whose events are past their cutoff.
        "startsAfter": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
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
    return FallbackFetch(
        requested_at=moment,
        objects=len(payload.get("data") or []),
        pairs=parse.pairs,
        parse=parse,
    )


def ingest(session: Session, settings: Settings, fetch: FallbackFetch,
           *, event_date: date | None = None) -> FallbackFetch:
    """Normalize the fetched pairs through the ordinary ingestion path."""
    from app.ingestion.normalizer import CanonicalNormalizer

    events = to_event_dtos(fetch.pairs)
    if not events:
        return fetch
    written = CanonicalNormalizer(session).ingest_odds(
        events, source=SPORTSGAMEODDS, available_as_of=fetch.requested_at,
        source_mode="LIVE", event_date=event_date,
    )
    fetch.events_ingested = len(events)
    fetch.quotes_written = int(written)
    return fetch


__all__ = [
    "ENDPOINT_KEY",
    "FallbackFetch",
    "eligible",
    "fetch_pregame_moneylines",
    "ingest",
    "to_event_dtos",
]

"""The NFL price fallback: SportsGameOdds refused -> ONE The Odds API request.

WHY
---
The NFL paper checkpoint needs a fresh, two-sided moneyline around T-26. The only
live NFL price path was the SportsGameOdds pregame lane, and that lane is paused
whenever SportsGameOdds sits below its object reserve. The Odds API answers every
NFL event in one request for one credit (h2h, region us), so when the SGO lane
REFUSES a due checkpoint cluster for budget or eligibility, this asks The Odds
API once instead.

WHAT IT REUSES, UNCHANGED
-------------------------
* every Odds API guard: `_enforce_odds_quota_policy` (daily request budget,
  monthly credit cap and reserve, provider floor and latch) and the NFL_RESEARCH
  credit lane enforced by `_reserve_provider_request`;
* the provider ledger: a RESERVED row before the request, a SUCCEEDED or FAILED
  terminal row after it;
* `NFLShadowOddsService.ingest`: the explicit franchise map, home/away
  direction, kickoff to the second and a unique game -- scoped here to the due
  cluster's games, so no other game is written from this request;
* the SGO lane's atomic per-(lane, kickoff cluster, phase) claim, under this
  lane's own name, so one cluster can buy at most one primary and one owed retry.

WHAT IT WILL NOT DO
-------------------
Run when SportsGameOdds succeeded, deferred or was never asked; run outside the
(T-21, T-30] acquisition window; buy a second primary for a cluster; arm the
legacy interval poller; or touch any threshold, freshness, stake or value rule.

THE WINDOW (hotfix 2026-10-24, strict-gates inventory E3 / review HIGH-4 c)
------------------------------------------------------------------------
The primary used to be eligible only inside [T-27, T-25]: two one-minute ticks.
On TNF 10-22 both were refused by a cooldown another lane had set (a refusal
happens BEFORE any claim or reservation, so it spends nothing and claims
nothing), the window closed, no retry was owed, and NE@CHI was decided on a
1,293-hour-old pair. Now the primary is eligible on EVERY collector tick from
T-30 through T-21 until the cluster holds a captured moneyline, and the single
owed retry (after a proven zero-charge FAILED primary) on any later tick before
the hard stop. Still one claim per (cluster, phase): one charged request per
cluster per phase, every budget and the per-endpoint 5-minute cooldown as
before. The SGO lane's own [T-27, T-25] protocol timing is untouched.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Game, GameStatus, League, Market, MarketOutcome, MarketType,
    NflAcquisitionPhaseClaim, OddsSnapshot, ProviderRequestLedger,
)
from app.services.sgo_nfl_pregame import (
    PHASE_PRIMARY, PHASE_RETRY, RETIME_HARD_STOP, RETIME_PRIMARY_EARLIEST,
    RETIME_PRIMARY_LATEST, RETIME_RETRY_EARLIEST, RETIME_RETRY_LATEST,
    _aware, phase_is_claimed,
)

PROVIDER = "THE_ODDS_API"
#: The fallback's acquisition window: eligible on every tick in (T-21, T-30].
WINDOW_OPENS_BEFORE_KICKOFF = timedelta(minutes=30)
WINDOW_CLOSES_BEFORE_KICKOFF = RETIME_HARD_STOP
#: The claim lane. Distinct from `sgo_nfl_events`, so the SGO lane's own claims
#: and this fallback's never block each other.
CLAIM_LANE = "odds_api_nfl_fallback"
#: Contains "americanfootball" (and not "ncaaf"), so the existing lane
#: attribution charges it to NFL_RESEARCH.
ENDPOINT_KEY = "americanfootball_nfl_fallback_odds"
SPORT_KEY = "americanfootball_nfl"
MARKETS: tuple[str, ...] = ("h2h",)
REGIONS = "us"
#: h2h x one region.
CREDIT_COST = 1

#: The SGO collector's refusals that mean "SportsGameOdds will not buy this":
#: a budget or eligibility answer. Anything else -- nothing due, a deferred
#: ambiguous selection, a claim another run holds, a request that was made --
#: is not a refusal and never triggers the fallback.
SGO_REFUSALS = frozenset({
    "LANE_HALTED", "PROVIDER_NOT_ELIGIBLE", "NFL_LANE_RENEWAL_CAP_REACHED",
    "OBJECT_RESERVE_GUARD",
})
SGO_REFUSAL_PREFIX = "ALLOCATION_"


def enabled(settings: Any) -> bool:
    return bool(getattr(settings, "nfl_odds_api_fallback_enabled", False))


def daily_request_cap(settings: Any) -> int:
    return max(0, int(getattr(settings, "nfl_odds_api_fallback_daily_request_cap", 20)))


def sgo_refused(result: dict[str, Any] | None) -> bool:
    """Did the SGO collector refuse for budget or eligibility (and only that)?"""

    if not isinstance(result, dict) or result.get("attempted") is not False:
        return False
    reason = str(result.get("reason") or "")
    return reason in SGO_REFUSALS or reason.startswith(SGO_REFUSAL_PREFIX)


def requests_today(session: Session, *, now: datetime, display_timezone: str) -> int:
    """Fallback requests RESERVED on the operational day containing ``now``."""

    from app.core.time import local_day_bounds, local_today

    day = local_today(display_timezone, now=_aware(now))
    start, end = local_day_bounds(day, display_timezone)
    return int(session.scalar(
        select(func.count(ProviderRequestLedger.id)).where(
            ProviderRequestLedger.provider == PROVIDER,
            ProviderRequestLedger.endpoint_key == ENDPOINT_KEY,
            ProviderRequestLedger.status == "RESERVED",
            ProviderRequestLedger.requested_at >= start.astimezone(UTC),
            ProviderRequestLedger.requested_at < end.astimezone(UTC),
        )) or 0)


def _claim(session: Session, *, kickoff: datetime, phase: str) -> NflAcquisitionPhaseClaim | None:
    return session.scalar(
        select(NflAcquisitionPhaseClaim).where(
            NflAcquisitionPhaseClaim.lane == CLAIM_LANE,
            NflAcquisitionPhaseClaim.kickoff_cluster_utc == _aware(kickoff),
            NflAcquisitionPhaseClaim.phase == phase).limit(1))


def retry_is_owed(session: Session, *, kickoff: datetime) -> bool:
    """The SGO lane's owed-retry rule, applied to this fallback's own attempt.

    Owed only when this cluster's PRIMARY claim led to a reservation whose
    terminal row is FAILED with no response received and nothing charged, and
    the single RETRY claim has not been taken. A reservation with no terminal
    row is an UNKNOWN charge and is never retried; a primary refused before any
    reservation was never a request and owes nothing.
    """

    primary = _claim(session, kickoff=kickoff, phase=PHASE_PRIMARY)
    if primary is None or primary.reservation_id is None:
        return False
    if phase_is_claimed(session, kickoff_cluster=kickoff, phase=PHASE_RETRY, lane=CLAIM_LANE):
        return False
    terminal = session.execute(
        select(ProviderRequestLedger).where(
            ProviderRequestLedger.reservation_id == primary.reservation_id)
    ).scalars().all()
    if not terminal:
        return False                         # unknown charge: never retry
    # A missing/null quota or a lost response is UNKNOWN, not zero: a retry is owed
    # only when the terminal row carries explicit proof that the request never
    # reached the provider (zero_charge_proven, set from the exception chain).
    return all(
        str(row.status) == "FAILED"
        and (row.response_metadata or {}).get("zero_charge_proven") is True
        and not (row.response_metadata or {}).get("response_received", False)
        and not (row.records_received or 0)
        and not (row.quota_cost or 0)
        for row in terminal)


def zero_charge_proven(exc: BaseException) -> bool:
    """True only when the failure proves the request never reached the provider.

    That is an httpx connection failure (connect error or connect timeout) anywhere
    in the exception chain: no TCP connection, so no request was sent and nothing can
    have been charged. Every other failure (read/write timeouts, HTTP errors, payload
    errors, an unknown cause) may have been received and charged, so it is UNKNOWN.
    """

    import httpx

    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (httpx.ConnectError, httpx.ConnectTimeout)):
            return True
        current = current.__cause__ or current.__context__
    return False


def _captured_since(session: Session, game_id: Any, opens: datetime) -> bool:
    """Has AISP CAPTURED any moneyline for this game since ``opens``?

    Asked on AISP's own capture clock (`available_as_of`), never the book's
    `observed_at`: an unchanged line carries an old book clock, and asking on it
    would make a game captured at T-26 look unpriced on the next tick.
    """

    return session.scalar(
        select(OddsSnapshot.id)
        .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
        .join(Market, Market.id == MarketOutcome.market_id)
        .where(Market.game_id == game_id, Market.market_type == MarketType.MONEYLINE,
               OddsSnapshot.available_as_of >= _aware(opens))
        .limit(1)) is not None


def due_games(session: Session, *, now: datetime) -> list[dict[str, Any]]:
    """NFL games inside this fallback's (T-21, T-30] window: an unclaimed primary, or an owed retry.

    Re-derived from the clock and this lane's own claims and ledger, never from
    the SGO lane's: the SGO retry rule reads SGO attempts, which a refused SGO
    lane never makes. A cluster whose primary is claimed is due again only for
    the single owed retry; a cluster with a captured moneyline is never due.
    """

    moment = _aware(now)
    rows = session.execute(
        select(Game.id, Game.starts_at)
        .join(League, League.id == Game.league_id)
        .where(League.code == "NFL", Game.status == GameStatus.SCHEDULED,
               Game.starts_at > moment + WINDOW_CLOSES_BEFORE_KICKOFF,
               Game.starts_at <= moment + WINDOW_OPENS_BEFORE_KICKOFF)
        .order_by(Game.starts_at)
    ).all()
    due: list[dict[str, Any]] = []
    for game_id, starts_at in rows:
        kickoff = _aware(starts_at)
        to_kickoff = kickoff - moment
        if not WINDOW_CLOSES_BEFORE_KICKOFF < to_kickoff <= WINDOW_OPENS_BEFORE_KICKOFF:
            continue
        if not phase_is_claimed(session, kickoff_cluster=kickoff, phase=PHASE_PRIMARY, lane=CLAIM_LANE):
            phase = PHASE_PRIMARY            # every tick until the one claim is taken
        elif retry_is_owed(session, kickoff=kickoff):
            phase = PHASE_RETRY              # the single owed retry, any later tick before the hard stop
        else:
            continue
        if _captured_since(session, game_id, kickoff - WINDOW_OPENS_BEFORE_KICKOFF):
            continue                         # already captured in this window
        due.append({"game_id": str(game_id), "starts_at": kickoff.isoformat(),
                    "kickoff_cluster": kickoff.isoformat(), "phase": phase,
                    "minutes_to_kickoff": round(to_kickoff.total_seconds() / 60, 1)})
    return due


def clusters(due: list[dict[str, Any]]) -> dict[tuple[datetime, str], list[str]]:
    """Due games grouped by (kickoff cluster, phase)."""

    grouped: dict[tuple[datetime, str], list[str]] = {}
    for entry in due:
        key = (_aware(datetime.fromisoformat(entry["kickoff_cluster"])), entry["phase"])
        grouped.setdefault(key, []).append(entry["game_id"])
    return grouped


__all__ = [
    "CLAIM_LANE", "CREDIT_COST", "ENDPOINT_KEY", "MARKETS", "PROVIDER", "REGIONS",
    "SGO_REFUSALS", "SPORT_KEY", "WINDOW_CLOSES_BEFORE_KICKOFF", "WINDOW_OPENS_BEFORE_KICKOFF",
    "clusters", "daily_request_cap", "due_games", "enabled",
    "requests_today", "retry_is_owed", "sgo_refused",
]

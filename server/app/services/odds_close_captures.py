"""Extra Odds API h2h captures near the close: T-30, T-15 and one in [T-5, T-2].

OWNER DECISION A (James, 2026-10-25)
------------------------------------
For every sport with an Odds API lane (MLB, NFL, CFB, NBA, NHL): one h2h capture
per start-time cluster at T-30 and one at T-15, plus one near-close capture
between T-5 and T-2. "Tens of credits per day" approved. The T-30 capture is the
fresh pair the T-30..T-15 checkpoints (and the T-16 last-chance tick) decide on;
T-15 and the near-close capture are what the market-anchored shadow and every
closing-line comparison measure against.

WHAT THIS MODULE DECIDES (and the orchestrator does)
----------------------------------------------------
This module only PLANS, from persisted rows and the clock: which (sport, start
cluster, capture point) is due now, and whether a retry is owed.
``JobOrchestrator.collect_odds_close_captures`` then, per sport and in the order
of costs: switch and ownership, something due, this module's per-sport daily
cap, the yield to today's still-to-come decision buys, every existing Odds API
guard (``_enforce_odds_quota_policy``) and the sport's own credit lane
(``_enforce_lane_budget``), then the atomic per-(sport, point, cluster) CLAIM,
then a ledger reservation, ONE request, archive, bind/persist with the sport's
own reviewed adapter (``app.nhl/nba/cfb.odds_api_price``,
``NFLShadowOddsService.ingest``, ``CanonicalNormalizer.ingest_odds`` for MLB) and
a SUCCEEDED/FAILED terminal row. Every refusal is logged and returned with its
reason; nothing due is ever skipped silently.

IDEMPOTENCY
-----------
The claim is a row in ``nfl_acquisition_phase_claims`` (the table the NFL lanes
already use for exactly this) under a lane of this module's own --
``close_capture:<SPORT>:<POINT>`` -- keyed (lane, cluster start, phase) by a
database UNIQUE constraint and committed before any provider work, so two ticks,
a restart or an admin run can never buy the same cluster/point twice. One retry
(phase RETRY) is owed only when the primary's reservation ended FAILED, and only
while the point is still due. Several points due on one tick (two clusters, or a
T-15 and another cluster's near-close) share ONE request: The Odds API prices
every event of a sport in one call.

ENDPOINT KEYS
-------------
Each sport has its own ``<sport_key>_close_capture`` ledger endpoint. It contains
the sport key, so ``odds_budget_lanes.league_of_endpoint`` charges it to the
sport's lane (NCAAF before NFL, as everywhere), and it is NOT the buy lanes' key,
so a capture can never count as -- or block -- a buy window
(``odds_request_window``) or the NFL fallback's daily cap.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Game, GameStatus, League, NflAcquisitionPhaseClaim, ProviderRequestLedger
from app.services.odds_budget_lanes import (
    LANE_CFB_RESEARCH, LANE_MLB_REQUIRED, LANE_NBA_RESEARCH, LANE_NFL_RESEARCH, LANE_NHL_RESEARCH,
)

PROVIDER = "THE_ODDS_API"
MARKETS: tuple[str, ...] = ("h2h",)
JOB_NAME = "collect-odds-close-captures"

POINT_T30 = "T30"
POINT_T15 = "T15"
POINT_CLOSE = "CLOSE"

PHASE_PRIMARY = "PRIMARY"
PHASE_RETRY = "RETRY"

#: A decision buy for a cluster happens no later than this before its start (the
#: CFB/NBA/NHL buy windows close at T-35; the NFL fallback and MLB checkpoint are
#: later, so this counts them as still to come for longer, never shorter).
BUY_STILL_TO_COME = timedelta(minutes=35)
#: A game starting this long after the operational day ends still counts as today's
#: buy (its buy window opens before midnight), as in ``odds_budget_lanes``.
BUY_DAY_SPILLOVER = timedelta(hours=2)

# -- refusal / state vocabulary -------------------------------------------------
DISABLED = "DISABLED"
NOT_DUE = "NOT_DUE"
SPORT_ODDS_API_LANE_DISABLED = "SPORT_ODDS_API_LANE_DISABLED"
NOT_THE_ODDS_COLLECTION_OWNER = "NOT_THE_ODDS_COLLECTION_OWNER"
THE_ODDS_API_KEY_NOT_CONFIGURED = "THE_ODDS_API_KEY_NOT_CONFIGURED"
CAPTURE_DAILY_CAP_REACHED = "CAPTURE_DAILY_CAP_REACHED"
CAPTURE_YIELDS_TO_PENDING_BUYS = "CAPTURE_YIELDS_TO_PENDING_BUYS"
CAPTURE_LANE_MONTHLY_FLOOR = "CAPTURE_LANE_MONTHLY_FLOOR"
ODDS_QUOTA_POLICY_REFUSED = "ODDS_QUOTA_POLICY_REFUSED"
ODDS_LANE_BUDGET_REFUSED = "ODDS_LANE_BUDGET_REFUSED"
ALREADY_CLAIMED = "ALREADY_CLAIMED"
CAPTURED = "CAPTURED"
REQUEST_FAILED = "REQUEST_FAILED"


@dataclass(frozen=True)
class SportSpec:
    """One sport's Odds API capture identity. The binding itself is the sport's own code."""

    sport: str
    league_code: str
    sport_key: str
    #: Contains ``sport_key`` so the existing lane attribution charges the sport's lane.
    endpoint_key: str
    lane: str
    #: MLB: only LIVE-mode games are real slate games (``odds_pregame_window_open``).
    live_only: bool = False


SPORTS: tuple[SportSpec, ...] = (
    SportSpec("MLB", "MLB", "baseball_mlb", "baseball_mlb_close_capture", LANE_MLB_REQUIRED, live_only=True),
    SportSpec("NFL", "NFL", "americanfootball_nfl", "americanfootball_nfl_close_capture", LANE_NFL_RESEARCH),
    SportSpec("CFB", "CFB", "americanfootball_ncaaf", "americanfootball_ncaaf_close_capture", LANE_CFB_RESEARCH),
    SportSpec("NBA", "NBA", "basketball_nba", "basketball_nba_close_capture", LANE_NBA_RESEARCH),
    SportSpec("NHL", "NHL", "icehockey_nhl", "icehockey_nhl_close_capture", LANE_NHL_RESEARCH),
)
SPORTS_BY_CODE = {spec.sport: spec for spec in SPORTS}


@dataclass(frozen=True)
class CapturePoint:
    """A capture point: due while ``low < minutes_to_start <= high`` (``low`` inclusive for CLOSE)."""

    name: str
    high: float
    low: float
    low_inclusive: bool = False

    def due(self, minutes_to_start: float) -> bool:
        above = minutes_to_start >= self.low if self.low_inclusive else minutes_to_start > self.low
        return above and minutes_to_start <= self.high


def capture_points(settings: Any) -> tuple[CapturePoint, ...]:
    """The three points from settings (defaults T-30, T-15 with a 6-minute window, [T-5, T-2])."""

    window = int(getattr(settings, "odds_close_capture_point_window_minutes", 6))
    t30 = int(getattr(settings, "odds_close_capture_t30_minutes", 30))
    t15 = int(getattr(settings, "odds_close_capture_t15_minutes", 15))
    earliest = int(getattr(settings, "odds_close_capture_close_earliest_minutes", 5))
    latest = int(getattr(settings, "odds_close_capture_close_latest_minutes", 2))
    return (
        CapturePoint(POINT_T30, float(t30), float(t30 - window)),
        CapturePoint(POINT_T15, float(t15), float(t15 - window)),
        CapturePoint(POINT_CLOSE, float(earliest), float(min(latest, earliest)), low_inclusive=True),
    )


def sport_enabled(settings: Any, sport: str) -> bool:
    """Does this sport have a switched-on Odds API price lane? Only then are its captures bought."""

    if sport == "MLB":
        return bool(getattr(settings, "odds_checkpoint_collection_enabled", False))
    if sport == "NFL":
        return bool(getattr(settings, "nfl_odds_api_fallback_enabled", False)
                    or getattr(settings, "nfl_shadow_odds_enabled", False))
    if sport == "CFB":
        return bool(getattr(settings, "cfb_production_consumer_enabled", False)
                    and getattr(settings, "cfb_odds_api_enabled", False))
    if sport == "NBA":
        return bool(getattr(settings, "nba_production_consumer_enabled", False)
                    and getattr(settings, "nba_odds_api_enabled", False))
    if sport == "NHL":
        return bool(getattr(settings, "nhl_odds_api_enabled", False))
    return False


def claim_lane(sport: str, point: str) -> str:
    return f"close_capture:{sport}:{point}"


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _games(session: Session, spec: SportSpec, *, start: datetime, end: datetime) -> list[tuple[Any, datetime]]:
    query = (select(Game.id, Game.starts_at).join(League, League.id == Game.league_id)
             .where(League.code == spec.league_code, Game.status == GameStatus.SCHEDULED,
                    Game.starts_at >= start, Game.starts_at <= end))
    if spec.live_only:
        query = query.where(Game.source_mode == "LIVE")
    return [(game_id, _aware(starts_at)) for game_id, starts_at in session.execute(query.order_by(Game.starts_at))]


def _claim(session: Session, *, sport: str, point: str, cluster: datetime, phase: str) -> NflAcquisitionPhaseClaim | None:
    return session.scalar(select(NflAcquisitionPhaseClaim).where(
        NflAcquisitionPhaseClaim.lane == claim_lane(sport, point),
        NflAcquisitionPhaseClaim.kickoff_cluster_utc == _aware(cluster),
        NflAcquisitionPhaseClaim.phase == phase).limit(1))


def retry_owed(session: Session, *, sport: str, point: str, cluster: datetime) -> bool:
    """The primary's reservation ended FAILED and the single RETRY claim is not taken.

    A primary claimed but never reserved (a lane refusal raced it, a crash) owes
    nothing: no request was made, and the claim is what spends the opportunity.
    A reservation with no terminal row yet is in flight or an unknown charge: no retry.
    """

    primary = _claim(session, sport=sport, point=point, cluster=cluster, phase=PHASE_PRIMARY)
    if primary is None or primary.reservation_id is None:
        return False
    if _claim(session, sport=sport, point=point, cluster=cluster, phase=PHASE_RETRY) is not None:
        return False
    statuses = list(session.scalars(select(ProviderRequestLedger.status).where(
        ProviderRequestLedger.reservation_id == primary.reservation_id)))
    return bool(statuses) and all(status == "FAILED" for status in statuses)


def due_points(session: Session, settings: Any, spec: SportSpec, *, now: datetime) -> list[dict[str, Any]]:
    """Every (start cluster, capture point) of ``spec`` due at ``now``, with the phase owed.

    A cluster is every SCHEDULED game of the sport sharing one start instant. A
    point whose PRIMARY claim is taken is due again only for the single owed retry.
    """

    moment = _aware(now)
    points = capture_points(settings)
    widest = max(p.high for p in points)
    narrowest = min(p.low for p in points)
    clusters: dict[datetime, list[str]] = {}
    for game_id, starts_at in _games(session, spec, start=moment + timedelta(minutes=narrowest),
                                     end=moment + timedelta(minutes=widest)):
        clusters.setdefault(starts_at, []).append(str(game_id))
    due: list[dict[str, Any]] = []
    for cluster, game_ids in sorted(clusters.items()):
        minutes = (cluster - moment).total_seconds() / 60.0
        for point in points:
            if not point.due(minutes):
                continue
            if _claim(session, sport=spec.sport, point=point.name, cluster=cluster, phase=PHASE_PRIMARY) is None:
                phase = PHASE_PRIMARY
            elif retry_owed(session, sport=spec.sport, point=point.name, cluster=cluster):
                phase = PHASE_RETRY
            else:
                continue
            due.append({"sport": spec.sport, "point": point.name, "phase": phase,
                        "cluster_start": cluster.isoformat(), "minutes_to_start": round(minutes, 2),
                        "game_ids": sorted(game_ids)})
    return due


def claim(session: Session, spec: SportSpec, entry: dict[str, Any], *, invocation_id: str,
          invocation_path: str, now: datetime) -> NflAcquisitionPhaseClaim | None:
    """Take the ONE claim for (sport, point, cluster, phase), committed, or None if another run holds it.

    The database decides (``uq_nfl_phase_claim``), never a read: both contenders
    insert and the loser is refused. Committed BEFORE any provider work, so a
    failed call, a rollback or a crash cannot reopen the opportunity.
    """

    game_ids = list(entry["game_ids"])
    row = NflAcquisitionPhaseClaim(
        lane=claim_lane(spec.sport, entry["point"]), provider=PROVIDER, sport=spec.sport,
        kickoff_cluster_utc=_aware(datetime.fromisoformat(entry["cluster_start"])), phase=entry["phase"],
        due_game_ids_sha256=hashlib.sha256("\n".join(sorted(game_ids)).encode("utf-8")).hexdigest(),
        due_game_count=len(game_ids), claimed_at=_aware(now),
        invocation_id=str(invocation_id)[:120], invocation_path=str(invocation_path)[:32])
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return None
    return row


def requests_today(session: Session, spec: SportSpec, *, now: datetime, display_timezone: str) -> int:
    """Capture reservations for ``spec`` on the operational day containing ``now``."""

    from app.core.time import local_day_bounds, local_today

    start, end = local_day_bounds(local_today(display_timezone, _aware(now)), display_timezone)
    return int(session.scalar(select(func.count(ProviderRequestLedger.id)).where(
        ProviderRequestLedger.provider == PROVIDER, ProviderRequestLedger.endpoint_key == spec.endpoint_key,
        ProviderRequestLedger.status == "RESERVED",
        ProviderRequestLedger.requested_at >= start, ProviderRequestLedger.requested_at < end)) or 0)


def shared_requests_today(session: Session, *, now: datetime, display_timezone: str) -> int:
    """Every LIVE Odds API reservation today: what ``ODDS_DAILY_REQUEST_BUDGET`` counts."""

    from app.core.time import local_day_bounds, local_today

    start, end = local_day_bounds(local_today(display_timezone, _aware(now)), display_timezone)
    return int(session.scalar(select(func.count(ProviderRequestLedger.id)).where(
        ProviderRequestLedger.provider == PROVIDER, ProviderRequestLedger.source_mode == "LIVE",
        ProviderRequestLedger.status == "RESERVED",
        ProviderRequestLedger.requested_at >= start, ProviderRequestLedger.requested_at < end)) or 0)


def pending_buy_clusters(session: Session, spec: SportSpec, *, now: datetime, display_timezone: str) -> int:
    """Start clusters of ``spec`` later today whose decision buy is still to come (one credit each)."""

    from app.core.time import local_day_bounds, local_today

    moment = _aware(now)
    _start, day_end = local_day_bounds(local_today(display_timezone, moment), display_timezone)
    starts = {starts_at for _game, starts_at in _games(
        session, spec, start=moment + BUY_STILL_TO_COME, end=day_end + BUY_DAY_SPILLOVER)}
    return len(starts)


def monthly_floor(monthly_allocation: int, pct: int) -> int:
    return int(math.ceil(max(0, monthly_allocation) * max(0, min(100, pct)) / 100.0))


__all__ = [
    "CAPTURED", "JOB_NAME", "MARKETS", "PHASE_PRIMARY", "PHASE_RETRY", "POINT_CLOSE", "POINT_T15", "POINT_T30",
    "PROVIDER", "SPORTS", "SPORTS_BY_CODE", "CapturePoint", "SportSpec", "capture_points", "claim", "claim_lane",
    "due_points", "monthly_floor", "pending_buy_clusters", "requests_today", "retry_owed", "sport_enabled",
]

"""Bounded, inactive-by-default player-prop quote collector (The Odds API).

``plan`` is side-effect free: it names exactly one request -- one canonical MLB
game, explicitly listed markets, one region -- predicts its credit cost and runs
every budget check without reserving, probing or calling anything.

``execute`` is the only path that can reach the provider. It re-plans, refuses
unless every check passes AND ``PLAYER_PROP_LIVE_ENABLED`` is true AND the
caller passed ``confirm=True``, reserves the credits in the provider ledger,
makes one GET, records the provider's reported balance and stores the quotes.
The API key is read from settings only inside ``execute`` and is never placed
in a plan, receipt, ledger row, log line or return value.

There is no loop, no scheduler hook and no slate-wide collection here.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.player_props import contract as c

ENDPOINT_KEY = "baseball_mlb_player_props_event_odds"
PLAN_SCHEMA = "AISP_PLAYER_PROP_REQUEST_PLAN_V1"
MAX_MARKETS = 1
ALLOWED_REGIONS = ("us",)


class PlanRefused(ValueError):
    """The request is outside the bounded shape; nothing is planned."""


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def provider_event_id(session: Session, game_id: uuid.UUID) -> tuple[str | None, str]:
    """The stored THE_ODDS_API event id for a canonical game, and where it came from."""

    from app.models import ExternalIdMapping, Market, MarketOutcome, OddsSnapshot

    mapped = session.scalars(select(ExternalIdMapping.external_id).where(
        ExternalIdMapping.source == c.PROVIDER, ExternalIdMapping.entity_type == "game",
        ExternalIdMapping.entity_id == game_id)).all()
    if len(set(mapped)) == 1:
        return mapped[0], "EXTERNAL_ID_MAPPING"
    if len(set(mapped)) > 1:
        return None, "EVENT_ID_AMBIGUOUS"
    seen = set(session.scalars(
        select(OddsSnapshot.source_event_id)
        .join(MarketOutcome, MarketOutcome.id == OddsSnapshot.market_outcome_id)
        .join(Market, Market.id == MarketOutcome.market_id)
        .where(Market.game_id == game_id, OddsSnapshot.source == c.PROVIDER,
               OddsSnapshot.source_event_id.is_not(None))
        .distinct()))
    if len(seen) == 1:
        return next(iter(seen)), "STORED_ODDS_SNAPSHOT"
    return None, "EVENT_ID_AMBIGUOUS" if seen else "EVENT_ID_UNRESOLVED"


def research_lanes(settings: Settings):
    """The default lanes with OTHER_RESEARCH funded only by explicit settings."""

    from app.services.odds_budget_lanes import DEFAULT_LANES, LANE_OTHER_RESEARCH

    return tuple(
        replace(lane, monthly_allocation=settings.player_prop_research_monthly_credits,
                daily_cap=settings.player_prop_research_daily_credits,
                purpose="Player-prop quote research. Buys no decision; zero unless explicitly allocated.")
        if lane.key == LANE_OTHER_RESEARCH else lane
        for lane in DEFAULT_LANES
    )


def _shape(event_id: str, game_id: uuid.UUID, markets: list[str], regions: list[str], day: str) -> dict[str, Any]:
    return {"purpose": "PLAYER_PROP_QUOTES", "sport_key": c.SPORT_KEY, "event_id": event_id,
            "game_id": str(game_id), "markets": markets, "regions": regions, "operational_day": day}


def plan(session: Session, settings: Settings, game_id: uuid.UUID | str, *,
         markets: list[str] | tuple[str, ...] = ("batter_hits",), regions: str = "us",
         now: datetime | None = None) -> dict[str, Any]:
    """Describe the one request that would be made, and whether it may be. Writes nothing."""

    from app.core.time import local_day_bounds, local_today
    from app.jobs.odds_quota_recovery import latest_successful_run
    from app.models import Game, League, ProviderRequestLedger
    from app.services import odds_budget
    from app.services.odds_budget_lanes import lane_budget, lane_for_request

    now = _aware(now) or datetime.now(UTC)
    markets = [m.strip() for m in markets if m and m.strip()]
    region_list = [r.strip() for r in regions.split(",") if r.strip()]
    if not markets:
        raise PlanRefused("Markets must be listed explicitly")
    if len(markets) > MAX_MARKETS:
        raise PlanRefused(f"At most {MAX_MARKETS} market per request")
    unsupported = [m for m in markets if m not in c.SUPPORTED_MARKETS]
    if unsupported:
        raise PlanRefused(f"Unsupported player-prop market: {', '.join(unsupported)}")
    if region_list != list(ALLOWED_REGIONS):
        raise PlanRefused("Exactly one region, us, is allowed")
    gid = uuid.UUID(str(game_id))

    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    game = session.get(Game, gid)
    league = session.scalar(select(League.code).where(League.id == game.league_id)) if game else None
    check("canonical_mlb_game", game is not None and league == "MLB",
          "Canonical MLB game" if game is not None and league == "MLB"
          else ("Game not found" if game is None else f"Game belongs to {league}, not MLB"))
    starts_at = _aware(game.starts_at) if game else None
    check("game_not_started", starts_at is not None and starts_at > now,
          f"Scheduled start {starts_at.isoformat()}" if starts_at else "No scheduled start")
    event_id, event_source = provider_event_id(session, gid) if game else (None, "EVENT_ID_UNRESOLVED")
    check("provider_event_bound", event_id is not None,
          f"Event id from {event_source}" if event_id else event_source)

    predicted = len(markets) * len(region_list)
    day = local_today(settings.display_timezone, now)
    shape = _shape(event_id or "", gid, markets, region_list, day.isoformat())
    lane = lane_for_request(league_code="MLB", markets=markets)
    check("lane_is_research", lane == "OTHER_RESEARCH",
          f"Charged to {lane}; protected MLB lanes are never used for player props")
    check("live_execution_enabled", settings.player_prop_live_enabled,
          "PLAYER_PROP_LIVE_ENABLED is true" if settings.player_prop_live_enabled
          else "PLAYER_PROP_LIVE_ENABLED is false (default); live execution is refused")

    start, end = (bound.astimezone(UTC) for bound in local_day_bounds(day, settings.display_timezone))
    reserved_today = session.scalar(select(func.count(ProviderRequestLedger.id)).where(
        ProviderRequestLedger.provider == c.PROVIDER, ProviderRequestLedger.source_mode == "LIVE",
        ProviderRequestLedger.status == "RESERVED",
        ProviderRequestLedger.requested_at >= start, ProviderRequestLedger.requested_at < end)) or 0
    check("daily_request_slot", reserved_today < settings.odds_daily_request_budget,
          f"{reserved_today}/{settings.odds_daily_request_budget} Odds API requests reserved on {day.isoformat()}; "
          "this slot is shared with MLB moneyline collection")

    base = odds_budget.budget_state(
        session, monthly_cap=settings.odds_monthly_credit_cap, reserve=settings.odds_monthly_credit_reserve,
        daily_cap=settings.odds_daily_credit_budget, floor=settings.odds_quota_floor, now=now,
        display_timezone=settings.display_timezone)
    ok, why = base.may_spend(predicted)
    check("monthly_and_daily_budget", ok, why or (
        f"{base.budgeted_remaining} credits spendable above the {base.reserve}-credit reserve; "
        f"{base.daily_remaining} left today"))
    lanes = lane_budget(
        session, monthly_cap=settings.odds_monthly_credit_cap, reserve=settings.odds_monthly_credit_reserve,
        daily_cap=settings.odds_daily_credit_budget, floor=settings.odds_quota_floor, now=now,
        display_timezone=settings.display_timezone, lanes=research_lanes(settings))
    ok, why = lanes.may_spend(lane, predicted)
    state = lanes.lanes[lane]
    check("research_lane_allocation", ok, why or (
        f"{state.remaining} of {state.spec.monthly_allocation} monthly and "
        f"{state.remaining_today} of {state.spec.daily_cap} daily research credits left"))

    latest = latest_successful_run(session)
    reading = (latest.source_metadata or {}).get("requests_remaining") if latest else None
    floor_ok = not base.floor_latched and (reading is None or int(reading) > settings.odds_quota_floor)
    check("provider_floor", floor_ok,
          f"Provider floor {settings.odds_quota_floor}; latest in-window balance "
          f"{base.provider_remaining if base.provider_remaining is not None else 'unknown'}"
          + ("" if floor_ok else " -- at or below the floor, no recovery probe is taken from here"))
    cooldown_ok = True
    if latest is not None and latest.completed_at is not None:
        cooldown_ok = now - _aware(latest.completed_at) >= timedelta(minutes=settings.odds_manual_cooldown_minutes)
    check("cooldown", cooldown_ok, f"{settings.odds_manual_cooldown_minutes}-minute cooldown after the last run")

    fingerprint = hashlib.sha256(json.dumps(shape, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    duplicate = session.scalar(select(ProviderRequestLedger.id).where(
        ProviderRequestLedger.provider == c.PROVIDER, ProviderRequestLedger.endpoint_key == ENDPOINT_KEY,
        ProviderRequestLedger.request_fingerprint == fingerprint, ProviderRequestLedger.status == "RESERVED"))
    check("not_already_requested", duplicate is None,
          "No identical request today" if duplicate is None else "An identical request is already reserved today")

    may_execute = all(item["ok"] for item in checks)
    return {
        "schema_version": PLAN_SCHEMA,
        "mode": "DRY_RUN",
        "provider": c.PROVIDER,
        "endpoint_key": ENDPOINT_KEY,
        "game_id": str(gid),
        "game_starts_at": starts_at.isoformat() if starts_at else None,
        "provider_event_id": event_id,
        "provider_event_source": event_source,
        "request": {
            "method": "GET",
            "path": f"/v4/sports/{c.SPORT_KEY}/events/{event_id or '{eventId}'}/odds",
            "params": {"regions": ",".join(region_list), "markets": ",".join(markets),
                       "oddsFormat": "american", "dateFormat": "iso"},
            "credential": "added at execution time only; never recorded",
        },
        "request_shape": shape,
        "request_fingerprint": fingerprint,
        "predicted_credits": predicted,
        "credit_formula": f"{len(markets)} market x {len(region_list)} region; the provider's x-requests-last header is authoritative",
        "lane": lane,
        "checks": checks,
        "may_execute": may_execute,
        "blocked_by": [item["check"] for item in checks if not item["ok"]],
        "provider_calls_made": 0,
        "credits_spent": 0,
    }


async def execute(session: Session, settings: Settings, game_id: uuid.UUID | str, *,
                  markets: list[str] | tuple[str, ...] = ("batter_hits",), regions: str = "us",
                  confirm: bool = False, client: Any = None, now: datetime | None = None) -> dict[str, Any]:
    """The single live request. Refuses unless the plan passes and ``confirm`` is True."""

    from app.data_sources.base import SourceError
    from app.data_sources.odds import TheOddsAPIAdapter
    from app.models import ProviderRequestLedger
    from app.player_props.ingest import ingest_event_odds

    receipt = plan(session, settings, game_id, markets=markets, regions=regions, now=now)
    if not confirm:
        return receipt
    if not receipt["may_execute"]:
        return {**receipt, "mode": "REFUSED"}

    requested_at = datetime.now(UTC)
    reservation = ProviderRequestLedger(
        provider=c.PROVIDER, endpoint_key=ENDPOINT_KEY, requested_at=requested_at, status="RESERVED",
        request_fingerprint=receipt["request_fingerprint"], controlled_live_request=True, source_mode="LIVE",
        response_metadata={"request_shape": receipt["request_shape"]})
    session.add(reservation)
    session.flush()
    session.commit()  # the reservation stands even if the process dies mid-request

    adapter = TheOddsAPIAdapter(api_key=settings.the_odds_api_key, regions=regions,
                                sport_key=c.SPORT_KEY, client=client)
    quota: dict[str, Any] = {}
    try:
        response = await adapter.get_event_odds(receipt["provider_event_id"], list(receipt["request_shape"]["markets"]))
        quota = {k: response.envelope.metadata.get(k) for k in ("requests_remaining", "requests_used", "requests_last")}
        payload = response.raw_payload or {}
    except SourceError as exc:
        quota = dict(getattr(exc, "quota", {}) or {})
        session.add(ProviderRequestLedger(
            provider=c.PROVIDER, endpoint_key=ENDPOINT_KEY, requested_at=requested_at,
            completed_at=datetime.now(UTC), status="FAILED",
            request_fingerprint=receipt["request_fingerprint"], controlled_live_request=True, source_mode="LIVE",
            reservation_id=reservation.id, error_type=type(exc).__name__,
            quota_cost=quota.get("requests_last"), quota_remaining=quota.get("requests_remaining"),
            response_metadata={"request_shape": receipt["request_shape"], "requests_used": quota.get("requests_used"),
                               "error": str(exc)}))
        session.commit()
        return {**receipt, "mode": "FAILED", "provider_calls_made": 1, "error": str(exc),
                "credits_spent": quota.get("requests_last"),
                "credits_reported": quota.get("requests_last"), "remaining_reported": quota.get("requests_remaining")}

    raw_sha = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    session.add(ProviderRequestLedger(
        provider=c.PROVIDER, endpoint_key=ENDPOINT_KEY, requested_at=requested_at,
        completed_at=datetime.now(UTC), status="SUCCEEDED",
        request_fingerprint=receipt["request_fingerprint"], controlled_live_request=True, source_mode="LIVE",
        reservation_id=reservation.id, quota_cost=quota.get("requests_last"),
        quota_remaining=quota.get("requests_remaining"),
        records_received=sum(len(m.get("outcomes") or []) for b in payload.get("bookmakers") or []
                             for m in b.get("markets") or []),
        response_metadata={"request_shape": receipt["request_shape"], "requests_used": quota.get("requests_used"),
                           "payload_sha256": raw_sha}))
    result = ingest_event_odds(session, payload, request_ledger_id=reservation.id)
    session.commit()
    return {**receipt, "mode": "EXECUTED", "provider_calls_made": 1, "credits_spent": quota.get("requests_last"),
            "credits_reported": quota.get("requests_last"), "remaining_reported": quota.get("requests_remaining"),
            "payload_sha256": raw_sha, "ingest": result.as_dict()}

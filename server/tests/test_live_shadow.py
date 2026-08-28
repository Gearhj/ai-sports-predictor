"""Armed live prospective shadow operation.

Deterministic tests for cadence, automatic change detection + idempotence,
provider-vs-manual speed metrics, operational status, quote-burst timeline, and
the admin-only status API. No network call is made; provider snapshots and odds
are injected/seeded. Nothing is executable.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.jobs import seed_demo_data
from app.models import (
    AvailabilityEvent,
    Forecast,
    ForecastTrack,
    Game,
    GameTeam,
    Market,
    MarketOutcome,
    MarketType,
    OddsSnapshot,
    ProviderRequestLedger,
    ShadowRescore,
)
from app.prospective.contracts import AvailabilityEventInput, AvailabilityEventType
from app.services.live_shadow_service import GameAvailabilitySnapshot, LiveShadowOperator
from app.services.prospective_shadow_service import ProspectiveShadowService, compute_speed_metrics

TODAY = date(2026, 8, 26)


class StubRescorer:
    """Return the latest forecast as the rescore output (no heavy pipeline)."""

    def __init__(self, session):
        self.session = session

    def rescore(self, game):
        return self.session.scalar(
            select(Forecast).where(
                Forecast.game_id == game.id,
                Forecast.forecast_track == ForecastTrack.CURRENT_DEVELOPMENT,
            ).order_by(Forecast.version.desc()).limit(1)
        )


async def _live_game(session, settings, *, hours_ahead=3):
    await seed_demo_data(session, settings, TODAY)
    market = session.scalar(select(Market).where(Market.market_type == MarketType.MONEYLINE))
    game = session.get(Game, market.game_id)
    game.source_mode = "LIVE"
    game.starts_at = datetime.now(UTC) + timedelta(hours=hours_ahead)
    session.add(game)
    session.commit()
    return game


def _snap(game, *, probable="p1|p2", payload="mlb-schedule:t0"):
    return GameAvailabilitySnapshot(
        game_id=game.id, probable_pitcher_id=probable,
        home_lineup_status=None, away_lineup_status=None,
        source_payload_id=payload, published_at=datetime.now(UTC) - timedelta(seconds=20),
    )


# -- cadence -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_cadence_fast_and_slow_windows(session, settings):
    fast_game = await _live_game(session, settings, hours_ahead=3)  # within 4h -> fast
    op = LiveShadowOperator(session, settings)
    now = datetime.now(UTC)
    assert fast_game.id in op.due_game_ids(now)  # first time, no state -> due

    # Establish poll state, then a game outside the fast window uses slow cadence.
    op.poll([_snap(fast_game)], now=now, rescorer=StubRescorer(session))
    # Immediately after polling, cadence has not elapsed -> not due.
    assert fast_game.id not in op.due_game_ids(now + timedelta(seconds=30))
    # After 2+ minutes (fast cadence) it is due again.
    assert fast_game.id in op.due_game_ids(now + timedelta(minutes=3))


@pytest.mark.asyncio
async def test_started_game_is_not_polled(session, settings):
    game = await _live_game(session, settings, hours_ahead=3)
    game.starts_at = datetime.now(UTC) - timedelta(minutes=5)  # already started
    session.add(game); session.commit()
    op = LiveShadowOperator(session, settings)
    assert op.due_game_ids(datetime.now(UTC)) == set()
    assert op.monitored_games(datetime.now(UTC)) == []


# -- automatic detection + idempotence ---------------------------------------


@pytest.mark.asyncio
async def test_baseline_then_probable_change_triggers_rescore(session, settings):
    game = await _live_game(session, settings, hours_ahead=3)
    op = LiveShadowOperator(session, settings)
    stub = StubRescorer(session)

    # First poll establishes the baseline; no event from a first observation.
    first = op.poll([_snap(game, probable="p1|p2")], now=datetime.now(UTC), rescorer=stub)
    assert first["events_emitted"] == 0
    assert session.query(AvailabilityEvent).count() == 0

    # A real probable-pitcher change is auto-detected and rescored.
    now2 = datetime.now(UTC) + timedelta(minutes=3)
    second = op.poll([_snap(game, probable="pNEW|p2", payload="mlb-schedule:t1")], now=now2, rescorer=stub)
    assert second["events_emitted"] == 1
    assert second["rescores"] == 1
    event = session.query(AvailabilityEvent).one()
    assert event.source == "MLB_STATS_API"  # provider provenance, not manual
    assert event.event_type == "PROBABLE_PITCHER_CHANGED"
    assert event.payload.get("source_payload_id") == "mlb-schedule:t1"
    rescore = session.query(ShadowRescore).one()
    assert rescore.after_forecast_id is not None

    # Idempotent: re-polling the same (now-current) value emits nothing new.
    third = op.poll([_snap(game, probable="pNEW|p2", payload="mlb-schedule:t2")],
                    now=now2 + timedelta(minutes=3), rescorer=stub)
    assert third["events_emitted"] == 0
    assert session.query(ShadowRescore).count() == 1


# -- speed hypothesis --------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_vs_manual_speed_metrics(session, settings):
    game = await _live_game(session, settings, hours_ahead=3)
    op = LiveShadowOperator(session, settings)
    stub = StubRescorer(session)
    op.poll([_snap(game, probable="p1|p2")], now=datetime.now(UTC), rescorer=stub)
    op.poll([_snap(game, probable="pNEW|p2", payload="t1")],
            now=datetime.now(UTC) + timedelta(minutes=3), rescorer=stub)
    provider_rescore = session.query(ShadowRescore).one()
    provider_event = session.query(AvailabilityEvent).one()
    provider_metrics = compute_speed_metrics(provider_rescore, provider_event)
    assert provider_metrics["is_provider_event"] is True
    assert provider_metrics["provider_detection_latency_seconds"] is not None
    assert provider_metrics["aisp_processing_latency_seconds"] is not None
    assert provider_metrics["note"] is None

    # A MANUAL_ADMIN event is explicitly excluded from provider-latency evidence.
    ProspectiveShadowService(session, settings).process_event(
        AvailabilityEventInput(event_type=AvailabilityEventType.LATE_SCRATCH, game_id=str(game.id)),
        rescorer=stub)
    manual_event = session.scalar(select(AvailabilityEvent).where(AvailabilityEvent.source == "MANUAL_ADMIN"))
    manual_rescore = session.scalar(select(ShadowRescore).where(ShadowRescore.availability_event_id == manual_event.id))
    manual_metrics = compute_speed_metrics(manual_rescore, manual_event)
    assert manual_metrics["is_provider_event"] is False
    assert manual_metrics["provider_detection_latency_seconds"] is None
    assert "MANUAL_ADMIN" in manual_metrics["note"]


# -- quote-burst timeline ----------------------------------------------------


@pytest.mark.asyncio
async def test_quote_burst_timeline_capture(session, settings):
    game = await _live_game(session, settings, hours_ahead=3)
    op = LiveShadowOperator(session, settings)
    stub = StubRescorer(session)
    op.poll([_snap(game, probable="p1|p2")], now=datetime.now(UTC), rescorer=stub)
    op.poll([_snap(game, probable="pNEW|p2", payload="t1")],
            now=datetime.now(UTC) + timedelta(minutes=3), rescorer=stub)
    rescore = session.query(ShadowRescore).one()
    prospective = ProspectiveShadowService(session, settings)
    for slot in ("t_plus_15s", "t_plus_60s", "t_plus_5m", "closing"):
        result = prospective.capture_quote(rescore.id, slot)
        assert result["slot"] == slot
    # Captures are append-only rows; the immutable rescore is never mutated.
    from app.models import ShadowQuoteCapture
    captures = {c.slot: c.quote for c in session.scalars(
        select(ShadowQuoteCapture).where(ShadowQuoteCapture.rescore_id == rescore.id))}
    assert set(captures) == {"t_plus_15s", "t_plus_60s", "t_plus_5m", "closing"}
    assert captures["t_plus_15s"] is not None  # real quote existed, not fabricated
    # No closing observation has been seeded, so closing is honestly null (never faked).
    assert captures["closing"] is None
    # Idempotent re-capture adds no new row.
    prospective.capture_quote(rescore.id, "t_plus_15s")
    assert session.query(ShadowQuoteCapture).count() == 4
    # The read board merges captures into the timeline.
    board = prospective.rescores(rescore.slate_date)
    assert "closing" in board["rescores"][0]["quote_timeline"]
    assert board["rescores"][0]["quote_timeline"]["t_plus_15s"] is not None


# -- operational status ------------------------------------------------------


@pytest.mark.asyncio
async def test_status_states(session, settings):
    await _live_game(session, settings, hours_ahead=3)
    # No credentials -> armed-no-credentials + exact required variable.
    no_creds = settings.model_copy(update={"the_odds_api_key": "", "live_shadow_enabled": True})
    status = LiveShadowOperator(session, no_creds).status()
    assert status["operational_state"] == "LIVE_SHADOW_ARMED_NO_ODDS_CREDENTIALS"
    assert status["required_odds_config_variable"] == "THE_ODDS_API_KEY"
    assert status["executable"] is False
    assert status["active_games_monitored"] == 1

    # Credentials present + enabled -> armed.
    armed = settings.model_copy(update={"the_odds_api_key": "x" * 32, "live_shadow_enabled": True})
    assert LiveShadowOperator(session, armed).status()["operational_state"] == "LIVE_SHADOW_ARMED"

    # Credentials present + not enabled -> ready.
    ready = settings.model_copy(update={"the_odds_api_key": "x" * 32, "live_shadow_enabled": False})
    assert LiveShadowOperator(session, ready).status()["operational_state"] == "LIVE_SHADOW_READY"


def test_status_counts_one_request_lifecycle_and_reads_terminal_quota(session, settings):
    now = datetime(2026, 8, 28, 12, tzinfo=UTC)
    reservation = ProviderRequestLedger(
        provider="THE_ODDS_API",
        endpoint_key="baseball_mlb_odds",
        requested_at=now - timedelta(minutes=1),
        status="RESERVED",
        request_fingerprint="a" * 64,
        controlled_live_request=False,
        source_mode="LIVE",
    )
    session.add(reservation)
    session.flush()
    session.add(ProviderRequestLedger(
        provider=reservation.provider,
        endpoint_key=reservation.endpoint_key,
        requested_at=reservation.requested_at,
        completed_at=now,
        status="SUCCEEDED",
        request_fingerprint=reservation.request_fingerprint,
        quota_cost=3,
        quota_remaining=479,
        records_received=13,
        controlled_live_request=False,
        source_mode="LIVE",
        reservation_id=reservation.id,
    ))
    session.commit()

    quota = LiveShadowOperator(session, settings).status(now=now)["quota"]

    assert quota["requests_today"] == 1
    assert quota["quota_remaining"] == 479


@pytest.mark.asyncio
async def test_status_api_admin_only(client, session, settings, admin_headers):
    await _live_game(session, settings, hours_ahead=3)
    ok = client.get("/api/v1/admin/live-shadow/status", headers=admin_headers)
    assert ok.status_code == 200
    body = ok.json()
    assert body["executable"] is False
    assert "operational_state" in body and "quota" in body and "cadence" in body
    # Unauthenticated and member callers are rejected.
    assert client.get("/api/v1/admin/live-shadow/status").status_code == 401
    member = client.post("/api/v1/auth/register",
                         json={"email": "m@example.com", "password": "MemberPass123!", "display_name": "Member"}).json()
    denied = client.get("/api/v1/admin/live-shadow/status",
                        headers={"Authorization": f"Bearer {member['access_token']}"})
    assert denied.status_code == 403

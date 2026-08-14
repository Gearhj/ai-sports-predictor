from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.core.time import local_today
from app.jobs import seed_demo_data
from app.models import Game


def test_today_rejects_non_current_date_override(client) -> None:
    response = client.get("/api/v1/dashboard/today?date=2025-03-27")

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "The Today endpoint serves the current local date only."
    )


async def test_today_never_substitutes_demo_or_demo_forecast(
    client,
    session,
    settings,
) -> None:
    event_date = local_today(settings.display_timezone)
    await seed_demo_data(session, settings, event_date - timedelta(days=1))
    for seeded_game in session.scalars(select(Game)):
        seeded_game.starts_at += timedelta(days=1)
    session.commit()

    demo_only = client.get("/api/v1/dashboard/today")
    assert demo_only.status_code == 200
    assert demo_only.json()["data_mode"] == "empty"
    assert demo_only.json()["games"] == []

    game = session.scalar(select(Game).order_by(Game.starts_at).limit(1))
    assert game is not None
    game.source_mode = "LIVE"
    session.commit()

    live_schedule = client.get("/api/v1/dashboard/today")
    assert live_schedule.status_code == 200
    body = live_schedule.json()
    assert body["data_mode"] == "live"
    assert len(body["games"]) == 1
    assert body["games"][0]["forecast_available"] is False
    assert body["games"][0]["market_available"] is False
    assert "FORECAST UNAVAILABLE" in body["games"][0]["status"]

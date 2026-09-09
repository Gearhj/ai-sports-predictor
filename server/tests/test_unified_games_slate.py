"""Calendar navigation reads a dated slate without weakening the Today guard."""
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import routes


def test_calendar_reads_requested_date_but_today_stays_current_only(monkeypatch):
    today = date(2026, 9, 9)
    previous = date(2026, 9, 8)
    read_dates = []
    monkeypatch.setattr(routes, "local_today", lambda timezone: today)
    monkeypatch.setattr(routes, "ReadService", lambda session, settings: SimpleNamespace(dashboard=lambda selected: read_dates.append(selected) or {"date": selected.isoformat()}))
    settings = SimpleNamespace(display_timezone="America/New_York")
    assert routes.games_forecast_slate(None, settings, previous) == {"date": "2026-09-08"}
    with pytest.raises(HTTPException) as rejected:
        routes.dashboard_today(None, settings, previous)
    assert rejected.value.status_code == 400
    assert read_dates == [previous]

"""A game still being played at midnight keeps its scoreboard.

On 2026-09-14 SEA at LAA (9th) and MIA at AZ (7th) both stopped updating at
23:59:04 ET and never recorded a final. The job passes today's local date; at
midnight that became Sept 15, the single schedule request returned the Sept 15
slate, and the two games -- still in the watch window -- were simply absent from
the response. MIA at AZ stayed frozen at 7-4 against a real final of 7-8.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.scoreboard import collect

SEA_LAA, MIA_AZ = "823981", "825034"


class RecordingAdapter:
    """Answers each schedule date with only the games scheduled on it."""

    def __init__(self, slates):
        self.slates = slates
        self.requested: list[date] = []

    async def get_schedule(self, day):
        self.requested.append(day)
        return SimpleNamespace(
            raw_payload={"schedule": {"games": self.slates.get(day, [])}},
            envelope=SimpleNamespace(fetched_at=datetime(2026, 9, 15, 4, 1, tzinfo=UTC)),
        )


def run(monkeypatch, *, passed_day, watched_dates, slates):
    watched = {SEA_LAA: uuid4(), MIA_AZ: uuid4()}
    recorded = {}
    monkeypatch.setattr(collect, "games_in_window", lambda *a, **k: watched)
    monkeypatch.setattr(collect, "_watched_dates", lambda *a, **k: set(watched_dates))
    monkeypatch.setattr(collect, "scoreboards_from_schedule",
                        lambda raw, retrieved_at: {g: {"status": "IN_PROGRESS"} for g in raw["games"]})

    def fake_record(session, pairs, checked_at):
        recorded["pairs"] = pairs
        return {"observations_written": len(pairs), "states_confirmed": 0}

    monkeypatch.setattr(collect, "record_many", fake_record)
    adapter = RecordingAdapter(slates)
    session = SimpleNamespace(commit=lambda: None)
    result = asyncio.run(collect.collect_mlb_scoreboard(
        session, event_date=passed_day, adapter=adapter,
        now=datetime(2026, 9, 15, 4, 1, tzinfo=UTC)))
    return adapter, recorded, result


def test_after_midnight_the_previous_nights_games_are_still_requested(monkeypatch):
    adapter, recorded, result = run(
        monkeypatch,
        passed_day=date(2026, 9, 15),            # the job's "today", after midnight
        watched_dates={date(2026, 9, 14)},       # the games still being played
        slates={date(2026, 9, 14): [SEA_LAA, MIA_AZ], date(2026, 9, 15): []},
    )
    assert date(2026, 9, 14) in adapter.requested
    assert len(recorded["pairs"]) == 2, "both late games must still be matched"
    assert result["requests"] == 2


def test_an_ordinary_cycle_still_costs_one_request(monkeypatch):
    adapter, _, result = run(
        monkeypatch,
        passed_day=date(2026, 9, 14),
        watched_dates={date(2026, 9, 14)},
        slates={date(2026, 9, 14): [SEA_LAA, MIA_AZ]},
    )
    assert adapter.requested == [date(2026, 9, 14)]
    assert result["requests"] == 1


def test_the_regression_it_replaces(monkeypatch):
    """Requesting only the passed day loses both games -- the 23:59:04 cutoff."""

    adapter, recorded, _ = run(
        monkeypatch,
        passed_day=date(2026, 9, 15),
        watched_dates=set(),                      # the old behaviour: today only
        slates={date(2026, 9, 14): [SEA_LAA, MIA_AZ], date(2026, 9, 15): []},
    )
    assert adapter.requested == [date(2026, 9, 15)]
    assert recorded["pairs"] == []

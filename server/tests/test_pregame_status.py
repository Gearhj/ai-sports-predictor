"""Market and model status are separate readings (the CHC @ CIN 2026-09-18 card)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

from app.services import pregame_status as ps

START = datetime(2026, 9, 18, 22, 40, tzinfo=UTC)          # 6:40 PM ET
OPENS, CLOSES = START - timedelta(minutes=30), START - timedelta(minutes=15)
PERMITTED = {"SportsGameOdds", "The Odds API"}


def _quote(observed: datetime, provider: str = "SportsGameOdds") -> dict:
    return {"market": "MONEYLINE", "provider": provider, "observed_at": observed.isoformat(),
            "sides": {"HOME": {"decimal": 2.2}, "AWAY": {"decimal": 1.7}}}


def test_fresh_pair_covers_the_whole_window():
    now = datetime(2026, 9, 18, 20, 55, tzinfo=UTC)
    older = datetime(2026, 9, 18, 18, 27, 4, tzinfo=UTC)   # Caesars, the oldest leg
    status = ps.market_status([_quote(older), _quote(older + timedelta(minutes=5))], PERMITTED,
                              now=now, opens=OPENS, closes=CLOSES)
    assert status["state"] == "ELIGIBLE_THROUGH_WINDOW"
    assert status["books_fresh_through_window"] == 2
    assert status["earliest_expiry"] == (older + timedelta(minutes=240)).isoformat()


def test_pair_that_ages_out_before_the_window_says_so():
    now = datetime(2026, 9, 18, 20, 0, tzinfo=UTC)
    status = ps.market_status([_quote(datetime(2026, 9, 18, 17, 0, tzinfo=UTC))], PERMITTED,
                              now=now, opens=OPENS, closes=CLOSES)
    assert status["state"] == "EXPIRES_BEFORE_WINDOW"


def test_unpermitted_provider_is_not_a_usable_pair():
    now = datetime(2026, 9, 18, 21, 0, tzinfo=UTC)
    status = ps.market_status([_quote(now - timedelta(minutes=5), provider="Other")], PERMITTED,
                              now=now, opens=OPENS, closes=CLOSES)
    assert status["state"] == "NO_TWO_SIDED_QUOTE"


def _receipt(tmp_path, entry: dict, generated: str = "2026-09-18T20:17:58+00:00") -> dict:
    sha = "b" * 64
    folder = tmp_path / "2026-09-18" / "sha256" / sha
    folder.mkdir(parents=True)
    body = {"schemaVersion": ps.RECEIPT_SCHEMA, "receiptSha256": sha, "generatedAt": generated,
            "games": [entry]}
    (folder / "slate-result.json").write_text(json.dumps(body), encoding="utf-8")
    ps._cache.clear()
    return ps.latest_receipt(date(2026, 9, 18), root=tmp_path)


def test_before_target_shows_schedule_and_the_producers_actual_reason(tmp_path):
    gid = "3e7914d7-5840-4b6d-be27-053579c22f7c"
    receipt = _receipt(tmp_path, {"gameId": gid, "checkpoint": {"reason": "CHECKPOINT_NOT_DUE",
                                                                "consumerReadyTargetUtc": "2026-09-18T21:55:00+00:00"}})
    status = ps.model_status("MLB", gid, START, None, now=datetime(2026, 9, 18, 21, 0, tzinfo=UTC), receipt=receipt)
    assert status["state"] == "SCHEDULED"
    assert status["schedule"]["input_checks_from"] == "2026-09-18T21:40:00+00:00"      # 5:40 PM ET
    assert status["schedule"]["publication_target"] == "2026-09-18T21:55:00+00:00"     # 5:55 PM ET
    assert status["schedule"]["target_is_guaranteed"] is False
    assert status["producer"]["reason"] == "CHECKPOINT_NOT_DUE"
    assert status["producer"]["producer_ready_target"] == status["schedule"]["publication_target"]


def test_after_target_without_forecast_is_overdue_with_blockers(tmp_path):
    gid = "g1"
    receipt = _receipt(tmp_path, {"gameId": gid, "checkpoint": {"reason": "PREGAME_SOURCE_CHECKPOINT"},
                                  "blockers": [{"reason": "AWAY_CONFIRMED_ORDERED_NINE_PLAYER_LINEUP_UNAVAILABLE"}]})
    status = ps.model_status("MLB", gid, START, None, now=datetime(2026, 9, 18, 22, 0, tzinfo=UTC), receipt=receipt)
    assert status["state"] == "OVERDUE"
    assert status["producer"]["blocker_text"] == ["the away team's confirmed batting order is not available"]
    late = ps.model_status("MLB", gid, START, None, now=CLOSES, receipt=receipt)
    assert late["state"] == "MISSED"


def test_published_by_producer_but_unbound_is_an_import_gap(tmp_path):
    receipt = _receipt(tmp_path, {"gameId": "g2", "status": "PUBLISHED"})
    status = ps.model_status("MLB", "g2", START, None, now=datetime(2026, 9, 18, 21, 0, tzinfo=UTC), receipt=receipt)
    assert status["state"] == "PUBLISHED_NOT_IMPORTED"


def test_bound_forecast_is_published_and_needs_no_receipt():
    status = ps.model_status("MLB", "g3", START, {"engine_id": "MLB_V18_WHOLE_GAME", "created_at": "x"},
                             now=datetime(2026, 9, 18, 21, 0, tzinfo=UTC), receipt=None)
    assert status["state"] == "PUBLISHED"


def test_a_receipt_whose_address_does_not_match_is_ignored(tmp_path):
    folder = tmp_path / "2026-09-18" / "sha256" / ("c" * 64)
    folder.mkdir(parents=True)
    (folder / "slate-result.json").write_text(json.dumps({"schemaVersion": ps.RECEIPT_SCHEMA,
                                                          "receiptSha256": "d" * 64, "games": []}))
    ps._cache.clear()
    assert ps.latest_receipt(date(2026, 9, 18), root=tmp_path) is None

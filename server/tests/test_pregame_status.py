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


# -- College Football: Akron @ Minnesota, 2026-09-19 12:00 PM ET ---------------
# No quote row, no active collector (SGO CFB lane: no uncommitted capacity,
# switched off, 0 objects spent), and a producer lease that expired at
# 08:20:44 ET; the 11:30 ET checkpoint recorded BLOCKED / DISCOVERY_NOT_CURRENT.

AKRON_KICK = datetime(2026, 9, 19, 16, 0, tzinfo=UTC)
AKRON_OPENS, AKRON_CLOSES = AKRON_KICK - timedelta(minutes=30), AKRON_KICK - timedelta(minutes=15)
AKRON_NOTE = {"reason": "NO_UNCOMMITTED_SGO_CAPACITY", "lane": "sgo_cfb_events", "lane_enabled": False,
              "objects_allowed": 0, "spent_on_lane": 0,
              "detail": "... so 0 are genuinely uncommitted. CFB takes the smaller of 10 and that: 0."}
EXPIRED = {"state": "EXPIRED_LEASE", "lease_valid_until": "2026-09-19T08:20:44.067209-04:00",
           "manifest_sha256": "03ba7f58", "manifest_created_utc": "2026-09-19T08:05:46.539274-04:00",
           "producer_status": "CURRENT_ELIGIBLE"}
AKRON_CHECKPOINT = {"decision_id": "29d99c43", "recorded_at": "2026-09-19T15:30:55.019582+00:00",
                    "recommendation": "BLOCKED", "reason": "DISCOVERY_NOT_CURRENT",
                    "detail": "EXPIRED_LEASE: The producer's eligibility lease has expired; a new owner tick must renew it.",
                    "discovery": {**EXPIRED, "eligible": False}}
AKRON_MODEL = {"engine_id": "CFB_GEN1_JOINT_BASELINE", "forecast_id": "cfb-gen1:ea85d35a", "home": 0.7831}
CFB_PERMITTED = {"SportsGameOdds"}


def test_cfb_with_no_quote_and_no_collector_is_inactive_not_waiting():
    collection = ps.cfb_collection(AKRON_NOTE)
    assert collection["active"] is False and collection["allocation_state"] == "NO_UNCOMMITTED_SGO_CAPACITY"
    assert collection["spent_on_lane"] == 0 and collection["objects_allowed"] == 0
    status = ps.cfb_market_status([], 0, collection, CFB_PERMITTED, now=AKRON_OPENS - timedelta(minutes=30),
                                  opens=AKRON_OPENS, closes=AKRON_CLOSES)
    assert status["state"] == ps.COLLECTION_INACTIVE
    assert status["quotes_stored"] == 0 and status["books_two_sided"] == 0
    # Still inactive once the window has closed: nothing ever fetched a price.
    late = ps.cfb_market_status([], 0, collection, CFB_PERMITTED, now=AKRON_KICK, opens=AKRON_OPENS, closes=AKRON_CLOSES)
    assert late["state"] == ps.COLLECTION_INACTIVE


def test_a_switched_off_lane_is_inactive_even_when_the_gate_cannot_be_read():
    unreadable = ps.cfb_collection({"reason": "PRICE_STATE_UNAVAILABLE", "detail": "OperationalError", "lane_enabled": False})
    assert unreadable["active"] is False
    unknown = ps.cfb_collection({"reason": "PRICE_STATE_UNAVAILABLE", "lane_enabled": True})
    assert unknown["active"] is None
    status = ps.cfb_market_status([], 0, unknown, CFB_PERMITTED, now=AKRON_OPENS, opens=AKRON_OPENS, closes=AKRON_CLOSES)
    assert status["state"] == ps.COLLECTION_STATE_UNKNOWN


def test_only_an_active_collector_makes_a_missing_quote_no_quotes_yet():
    active = ps.cfb_collection({**AKRON_NOTE, "reason": "CAPACITY_AVAILABLE", "lane_enabled": True, "objects_allowed": 4})
    assert active["active"] is True and active["objects_allowed"] == 4
    status = ps.cfb_market_status([], 0, active, CFB_PERMITTED, now=AKRON_OPENS, opens=AKRON_OPENS, closes=AKRON_CLOSES)
    assert status["state"] == ps.NO_QUOTES_YET
    # Switched on with zero capacity is still no collector.
    assert ps.cfb_collection({**AKRON_NOTE, "lane_enabled": True})["active"] is False


def test_cfb_stored_quotes_get_the_ordinary_reading_against_the_cfb_permitted_source():
    now = AKRON_OPENS - timedelta(minutes=20)
    status = ps.cfb_market_status([_quote(now - timedelta(minutes=10))], 1, ps.cfb_collection(AKRON_NOTE),
                                  CFB_PERMITTED, now=now, opens=AKRON_OPENS, closes=AKRON_CLOSES)
    assert status["state"] == "ELIGIBLE_THROUGH_WINDOW"
    stale = ps.cfb_market_status([_quote(now - timedelta(minutes=300))], 1, ps.cfb_collection(AKRON_NOTE),
                                 CFB_PERMITTED, now=now, opens=AKRON_OPENS, closes=AKRON_CLOSES)
    assert stale["state"] == "STORED_REFERENCE_ONLY"
    odds_api_only = ps.cfb_market_status([_quote(now, provider="The Odds API")], 1, ps.cfb_collection(AKRON_NOTE),
                                         CFB_PERMITTED, now=now, opens=AKRON_OPENS, closes=AKRON_CLOSES)
    assert odds_api_only["state"] == "NO_TWO_SIDED_QUOTE"


def test_cfb_eligibility_after_the_checkpoint_is_the_lease_the_checkpoint_recorded():
    status = ps.cfb_model_status(AKRON_MODEL, eligibility_now=EXPIRED, checkpoint=AKRON_CHECKPOINT, starts_at=AKRON_KICK)
    assert status["state"] == ps.ELIGIBILITY_EXPIRED
    assert status["basis"] == "RECORDED_AT_CHECKPOINT"
    assert status["eligibility"]["lease_valid_until"] == "2026-09-19T08:20:44.067209-04:00"
    assert status["checkpoint"]["reason"] == "DISCOVERY_NOT_CURRENT"
    assert status["renewed_since_checkpoint"] is False
    assert status["schedule"]["publication_target"] is None     # no invented producer target for CFB


def test_cfb_eligibility_before_the_checkpoint_reads_the_present_manifest():
    status = ps.cfb_model_status(AKRON_MODEL, eligibility_now=EXPIRED, checkpoint=None, starts_at=AKRON_KICK)
    assert (status["state"], status["basis"]) == (ps.ELIGIBILITY_EXPIRED, "PRESENT_DISCOVERY")
    current = ps.cfb_model_status(AKRON_MODEL, eligibility_now={**EXPIRED, "state": "CURRENT"}, checkpoint=None,
                                  starts_at=AKRON_KICK)
    assert current["state"] == ps.ELIGIBILITY_CURRENT
    unlisted = ps.cfb_model_status(AKRON_MODEL, eligibility_now={"state": "NOT_IN_CURRENT_DISCOVERY"},
                                   checkpoint=None, starts_at=AKRON_KICK)
    assert unlisted["state"] == ps.ELIGIBILITY_NOT_CURRENT
    assert ps.cfb_model_status(None, eligibility_now=None, checkpoint=None, starts_at=AKRON_KICK)["state"] == "NOT_PUBLISHED"


def test_a_renewal_after_the_checkpoint_never_rewrites_what_the_checkpoint_recorded():
    renewed = {**EXPIRED, "state": "CURRENT", "lease_valid_until": "2026-09-19T12:05:00-04:00",
               "manifest_created_utc": "2026-09-19T11:50:00-04:00"}
    status = ps.cfb_model_status(AKRON_MODEL, eligibility_now=renewed, checkpoint=AKRON_CHECKPOINT, starts_at=AKRON_KICK)
    assert status["state"] == ps.ELIGIBILITY_EXPIRED
    assert status["eligibility"] == AKRON_CHECKPOINT["discovery"]
    assert status["eligibility_now"]["state"] == "CURRENT"
    assert status["renewed_since_checkpoint"] is True

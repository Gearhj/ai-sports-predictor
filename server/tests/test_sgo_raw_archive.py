"""SportsGameOdds raw-response retention: exact bytes kept, never a credential, never a failed fetch.

Synthetic fixtures only: no provider is contacted. HTTP is served by an
in-process ``httpx.MockTransport`` that also checks the key travels only as a
header.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from app.ingestion.archive import RawArchive
from app.models import ProviderRequestLedger
from app.research_exports import v18_market_pilot as pilot
from app.services import sgo_collection, sgo_nfl_pregame, sgo_raw_archive
from app.show_preview import collection as show_preview

NOW = datetime(2026, 9, 20, 16, 0, tzinfo=UTC)
KEY = "sgo-test-key-not-a-real-credential"  # secret-guard: test-fixture
FINGERPRINT = "ab" * 32


def _body(league: str = "MLB", *, extra: str = "") -> bytes:
    """A small /events response in the provider's real shape (two books, both sides)."""

    def side(odd_id, opposing, entity, price, stamp):
        return {"oddID": odd_id, "opposingOddID": opposing, "marketName": "Moneyline", "statID": "points",
                "statEntityID": entity, "periodID": "game", "betTypeID": "ml", "sideID": entity,
                "started": False, "ended": False, "cancelled": False,
                "byBookmaker": {"draftkings": {"odds": price, "lastUpdatedAt": stamp, "available": True}}}

    event = {
        "eventID": f"EVT-{league}", "sportID": "BASEBALL", "leagueID": league, "type": "match",
        "teams": {"home": {"teamID": f"HOME_{league}", "names": {"long": "Home Club"}},
                  "away": {"teamID": f"AWAY_{league}", "names": {"long": "Away Club"}}},
        "status": {"started": False, "completed": False, "cancelled": False, "ended": False, "live": False,
                   "startsAt": (NOW + timedelta(minutes=50)).strftime("%Y-%m-%dT%H:%M:%S.000Z")},
        "odds": {
            "points-home-game-ml-home": side("points-home-game-ml-home", "points-away-game-ml-away", "home",
                                             "-125", "2026-09-20T15:51:02.140Z"),
            "points-away-game-ml-away": side("points-away-game-ml-away", "points-home-game-ml-home", "away",
                                             "+105", "2026-09-20T15:49:40.004Z"),
        },
    }
    text = json.dumps({"success": True, "data": [event], "notice": "synthetic" + extra,
                       "nextCursor": "C0"}, indent=1)
    return text.encode("utf-8")


@pytest.fixture
def sgo_settings(settings, tmp_path):
    return settings.model_copy(update={
        "sportsgameodds_enabled": True, "sportsgameodds_api_key": KEY,
        "sportsgameodds_base_url": "https://sgo.invalid/v2", "raw_data_root": tmp_path / "raw"})


@pytest.fixture
def provider(monkeypatch):
    """Serve /events in-process; record every request; the key must be a header only."""

    state = {"requests": [], "body": _body()}
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        assert request.headers.get("X-Api-Key") == KEY
        assert KEY not in str(request.url)
        return httpx.Response(200, content=state["body"], headers={"content-type": "application/json"})

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler),
                                                                         **kwargs))
    for module in (sgo_collection, sgo_nfl_pregame):
        monkeypatch.setattr(module, "eligible", lambda settings, now=None: (True, "OK"))
    return state


def _fetchers():
    return {
        "mlb_checkpoint": lambda s: sgo_collection.fetch_pregame_moneylines(
            s, limit=1, now=NOW, request_fingerprint=FINGERPRINT),
        "nfl_pregame": lambda s: sgo_nfl_pregame.fetch_nfl_pregame(
            s, limit=1, now=NOW, request_fingerprint=FINGERPRINT),
        "show_preview": lambda s: show_preview._fetch(
            s, league="MLB", limit=1, now=NOW, anchor=NOW, request_fingerprint=FINGERPRINT),
    }


def _archived(root: Path) -> list[Path]:
    return sorted((root / "sportsgameodds").rglob("*.json"))


# ---------------------------------------------------------------------------
# The archive primitive
# ---------------------------------------------------------------------------
def test_write_response_keeps_the_exact_bytes_in_the_live_layout(tmp_path):
    body = _body()
    record = RawArchive(tmp_path).write_response("SPORTSGAMEODDS", body, request_fingerprint=FINGERPRINT,
                                                 extra_metadata={"lane": "sgo_mlb_events"})
    wrapper = Path(record.path)
    assert wrapper.parent.relative_to(tmp_path).parts[0] == "sportsgameodds"
    assert pilot._RAW_NAME.match(wrapper.name)                      # the layout the exporter links
    document = json.loads(wrapper.read_bytes())
    metadata = document["metadata"]
    assert metadata["source"] == "SPORTSGAMEODDS" and metadata["request_fingerprint"] == FINGERPRINT
    assert metadata["ingestion_timestamp"] and metadata["checksum"] == record.payload_checksum
    assert document["payload"] == json.loads(body)
    exact = gzip.decompress(Path(record.body_path).read_bytes())
    assert exact == body                                           # byte-exact, whitespace and all
    assert metadata["response_body_sha256"] == hashlib.sha256(body).hexdigest() == record.body_sha256
    assert metadata["response_body_bytes"] == len(body) == record.body_bytes
    assert record.sha256 == hashlib.sha256(wrapper.read_bytes()).hexdigest()
    again = RawArchive(tmp_path).write_response("SPORTSGAMEODDS", body, request_fingerprint=FINGERPRINT)
    assert again.path != record.path                               # write-once, never overwritten


def test_the_exporter_links_an_archived_response_and_checks_its_exact_body(tmp_path):
    before = datetime.now(UTC)
    record = RawArchive(tmp_path).write_response("SPORTSGAMEODDS", _body(), request_fingerprint=FINGERPRINT)
    after = datetime.now(UTC) + timedelta(seconds=1)
    found = pilot.sgo_retained_responses(tmp_path, before, after, not_after=after,
                                         request_fingerprint=FINGERPRINT)
    assert [r.path for r in found] == [record.path]
    assert found[0].body_verified is True and found[0].body_sha256 == record.body_sha256
    assert pilot.sgo_retained_responses(tmp_path, before, after, not_after=after,
                                        request_fingerprint="cd" * 32) == []
    Path(record.body_path).write_bytes(gzip.compress(b"tampered", mtime=0))
    tampered = pilot.sgo_retained_responses(tmp_path, before, after, not_after=after)
    assert tampered[0].body_verified is False
    link = pilot.link_sgo_pair(tampered, event_id="EVT-MLB", book_key="draftkings", home_american=-125,
                               away_american=105, stored_observed=datetime(2026, 9, 20, 15, 49, 40, 4000,
                                                                           tzinfo=UTC))
    assert link.linked is False and link.reason_codes == ["RETAINED_EXACT_BODY_DOES_NOT_VERIFY"]


# ---------------------------------------------------------------------------
# Every SGO fetch path keeps its bytes -- and nothing else changes
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", sorted(_fetchers()))
def test_every_fetch_path_archives_its_successful_response_without_the_credential(
        sgo_settings, provider, path):
    fetch = _fetchers()[path](sgo_settings)
    assert len(provider["requests"]) == 1                          # one request, no retry, no extra call
    archive = fetch.raw_archive
    assert archive["status"] == sgo_raw_archive.ARCHIVED
    assert archive["responseBodySha256"] == hashlib.sha256(provider["body"]).hexdigest()
    assert archive["requestFingerprint"] == FINGERPRINT
    wrapper = Path(archive["path"])
    raw_bytes = wrapper.read_bytes() + gzip.decompress(Path(archive["responseBodyPath"]).read_bytes())
    assert KEY.encode() not in raw_bytes
    metadata = json.loads(wrapper.read_bytes())["metadata"]
    assert "sgo.invalid" not in json.dumps(metadata)               # no request URL is stored
    assert metadata["request_filters"] == dict(provider["requests"][0].url.params)
    assert metadata["requested_at"] == NOW.isoformat()
    assert _archived(sgo_settings.raw_data_root) == [wrapper]


@pytest.mark.parametrize("path", sorted(_fetchers()))
def test_an_archive_failure_never_fails_delays_or_repeats_the_fetch(sgo_settings, provider, monkeypatch, path):
    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(RawArchive, "write_response", broken)
    provider["body"] = _body("NFL" if path == "nfl_pregame" else "MLB")
    fetch = _fetchers()[path](sgo_settings)
    assert len(provider["requests"]) == 1
    assert fetch.raw_archive == {"status": sgo_raw_archive.FAILED, "reason": "OSError", "detail": "disk full"}
    assert fetch.objects == 1 and len(fetch.pairs) == 1            # the fetch itself stands, untouched


def test_a_body_carrying_the_key_is_refused_and_never_written(sgo_settings, provider):
    provider["body"] = _body(extra=KEY)
    fetch = _fetchers()["mlb_checkpoint"](sgo_settings)
    assert fetch.raw_archive["status"] == sgo_raw_archive.REFUSED
    assert fetch.raw_archive["reason"] == "RESPONSE_BODY_CONTAINS_THE_CONFIGURED_CREDENTIAL"
    assert not (sgo_settings.raw_data_root / "sportsgameodds").exists()
    assert fetch.objects == 1


def test_the_checkpoint_collector_files_the_archive_in_its_result_and_its_ledger_row(
        sgo_settings, provider, session, monkeypatch):
    game_id = "11111111-2222-3333-4444-555555555555"

    class _Coverage:
        def __init__(self, *args, **kwargs) -> None: ...

        def daily_report(self, **kwargs):
            return {"events": [{"game_id": game_id, "covered": False, "needs_refresh": True,
                                "cutoff_utc": (NOW + timedelta(minutes=30)).isoformat(),
                                "starts_at_utc": (NOW + timedelta(minutes=45)).isoformat()}]}

    class _Grant:
        allowed, granted, reason = True, 1, "GRANTED"

        def as_dict(self):
            return {"allowed": True, "granted": 1}

    monkeypatch.setattr("app.services.quote_coverage.QuoteCoverageService", _Coverage)
    monkeypatch.setattr("app.services.provider_allocation.request_objects", lambda *a, **k: _Grant())
    monkeypatch.setattr(sgo_collection, "seconds_since_last_fetch", lambda session, now=None: None)
    monkeypatch.setattr(sgo_collection, "ingest", lambda session, settings, fetch, **kwargs: fetch)
    monkeypatch.setattr(sgo_collection, "_ingested_game_ids", lambda session, fetch: [game_id])

    result = sgo_collection.collect_for_open_cutoffs(session, sgo_settings, now=NOW)
    assert result["reason"] == "FALLBACK_COLLECTED" and len(provider["requests"]) == 1
    assert result["raw_archive"]["status"] == sgo_raw_archive.ARCHIVED
    rows = {row.status: row for row in session.query(ProviderRequestLedger).all()}
    assert rows["SUCCEEDED"].response_metadata["raw_archive"] == result["raw_archive"]
    # The archive binds to the reservation's own fingerprint, which the exporter checks.
    assert result["raw_archive"]["requestFingerprint"] == rows["RESERVED"].request_fingerprint


def test_the_raw_archive_is_git_ignored_and_under_the_raw_data_root():
    repo = Path(__file__).resolve().parents[2]
    ignored = [line.strip() for line in (repo / ".gitignore").read_text(encoding="utf-8").splitlines()]
    assert "data/raw/**" in ignored
    from app.core.config import Settings

    assert Settings.model_fields["raw_data_root"].default == repo / "data" / "raw"

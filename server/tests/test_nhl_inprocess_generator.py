"""AISP's in-process NHL Gen1 generator: fetch, fail closed, persist honestly, run as an off-by-default job.

Hermetic: every HTTP response comes from an ``httpx.MockTransport``; the schedule
bytes are subsets cut from the producer's retained real captures
(``tests/fixtures/nhl_inprocess``). The reproduction proof itself lives in
``test_nhl_gen1_frozen_reproduction.py``.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import pathlib
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.nhl import contract as c
from app.nhl import gen1_frozen as frozen
from app.nhl import inprocess_generator as gen

FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "nhl_inprocess"
TOLERANCE = 1e-9
REDIRECT_HOST = "release-assets.githubusercontent.com"
README_OK = (b"# sportsdataverse-data\n\n[![License: CC\nBY 4.0](https://img.shields.io/badge/License-CC%20BY%204.0-"
             b"blue.svg?style=flat-square)](https://creativecommons.org/licenses/by/4.0/)\n\nData releases.\n")
LICENSE = (FIX / "sdv_license.response").read_bytes()


def _expected(tag: str) -> dict:
    return json.loads((FIX / f"expected_{tag}.json").read_text(encoding="utf-8"))


def _schedule(tag: str) -> bytes:
    return (FIX / f"schedule_{tag}_subset.csv").read_bytes()


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class Clock:
    """A strictly increasing fake UTC clock: every read advances one millisecond."""

    def __init__(self, start: datetime) -> None:
        self.t = start

    def __call__(self) -> datetime:
        self.t += timedelta(milliseconds=1)
        return self.t


def _transport(schedule: bytes, *, seen: list[str], license_bytes: bytes = LICENSE, readme: bytes = README_OK,
               digest: str | None = None, size: int | None = None, updated_at: str = "2026-10-13T08:04:01Z",
               redirect_host: str = REDIRECT_HOST) -> httpx.MockTransport:
    release = {"tag_name": "nhl_schedules", "assets": [
        {"name": "nhl_schedule_2026.csv", "browser_download_url": "https://example.invalid/other.csv",
         "digest": "sha256:" + "0" * 64, "size": 10, "updated_at": updated_at, "id": 1},
        {"name": gen.ASSET_NAME, "browser_download_url": gen.ASSET_URL, "id": 636336550,
         "digest": digest if digest is not None else "sha256:" + _sha(schedule),
         "size": size if size is not None else len(schedule), "updated_at": updated_at}]}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen.append(url)
        if url == gen.LICENSE_URL:
            return httpx.Response(200, content=license_bytes)
        if url == gen.README_URL:
            return httpx.Response(200, content=readme)
        if url == gen.RELEASE_API_URL:
            return httpx.Response(200, json=release, headers={"x-ratelimit-remaining": "58"})
        if url == gen.ASSET_URL:
            return httpx.Response(302, headers={"location": f"https://{redirect_host}/production/asset?sig=abc"})
        if request.url.host == redirect_host:
            return httpx.Response(200, content=schedule)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def _run(session, settings, schedule: bytes, start: datetime, **transport_kw):
    seen: list[str] = []
    async with httpx.AsyncClient(transport=_transport(schedule, seen=seen, **transport_kw)) as client:
        result = await gen.run_generation(session, settings, client=client, now=Clock(start), revision="f" * 40)
    return result, seen


def _forecasts(session):
    from app.models import NhlResearchForecast

    return session.scalars(select(NhlResearchForecast).order_by(NhlResearchForecast.imported_at)).all()


def _aware(value):
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


# ================================================================ generation ==
OCT13_RUN = datetime(2026, 10, 13, 16, 30, tzinfo=UTC)        # 12:30 ET: targets Oct 13 and Oct 14


async def test_a_run_writes_todays_and_tomorrows_forecasts_per_the_interface_contract(session, settings):
    from app.models import ExternalIdMapping, Game, NhlImportReceipt

    result, seen = await _run(session, settings, _schedule("20261013"), OCT13_RUN)
    assert result["status"] == "COMPLETED", result
    assert result["target_dates"] == ["2026-10-13", "2026-10-14"]
    # Request budget: LICENSE, README, release API, asset + one redirect hop. Nothing else, nowhere else.
    assert len(seen) == 5 and all(httpx.URL(u).host in gen.ALLOWED_HOSTS for u in seen)
    rows = _forecasts(session)
    oct13 = _expected("20261013")
    want = {f["canonicalGameId"]: f["document"] for f in oct13["forecasts"]}
    tomorrow = [r for r in rows if r.canonical_game_ref not in want]
    # Every Oct 14 game has a team whose (unstarted) Oct 13 game the receipt clock precedes: no day-ahead
    # forecast can carry its true rest, so none is written (the game-day run forecasts them).
    assert {r.canonical_game_ref for r in rows} == set(want) and tomorrow == []
    day_ahead = {e["sourceGameId"]: e["reason"] for e in result["excluded"] if e["targetDate"] == "2026-10-14"}
    assert day_ahead == {"2026020106": f"{gen.REST_INCOMPLETE}:WSH:2026020093",
                         "2026020107": f"{gen.REST_INCOMPLETE}:COL:2026020100"}, day_ahead
    assert result["counts"]["IMPORTED"] == len(rows) == result["records"]
    observed = c.parse_utc(result["observed_utc"])
    for row in rows:
        doc = row.document
        assert (row.record_class, row.model_id, row.model_version, row.configuration_sha256) == (
            "FORECAST", "NHL_GEN1_REGULATION_POISSON", "1.0.0", gen.CONFIGURATION_SHA256)
        assert row.producer_evidence_status == "AISP_IN_PROCESS_GENERATED"
        assert row.evidence_mode == "AISP_IN_PROCESS_FROZEN_GEN1" == doc["provenance"]["evidenceMode"]
        assert row.forecast_schema == "NHL_GEN1_FORECAST_V1" and row.research_only is True
        assert abs(row.final_home_probability + row.final_away_probability - 1.0) <= 1e-12
        assert None not in (row.regulation_home_probability, row.regulation_tie_probability,
                            row.regulation_away_probability, row.regulation_expected_home_goals)
        assert row.home_goalie_status == row.away_goalie_status == "UNKNOWN"
        assert row.goalie_feature_mode == "TEAM_LEVEL"
        # Clocks: source = when the schedule bytes arrived; created = published = generation; imported >= that.
        assert _aware(row.source_retrieved_utc) == _aware(row.source_cutoff_utc) == observed
        assert _aware(row.forecast_created_utc) == _aware(row.published_utc)
        assert observed < _aware(row.published_utc) <= _aware(row.imported_at) == _aware(row.available_as_of)
        assert _aware(row.published_utc) < _aware(row.scheduled_start_utc)
        assert row.publication_clock_source == "AISP_IN_PROCESS_GENERATION_CLOCK"
        # Identity and bytes, as handoff_import stores producer forecasts.
        assert row.forecast_id == c.forecast_identity(doc) == doc["forecastId"]
        assert row.object_sha256 == _sha(c.canonical_bytes(doc)) and row.object_bytes == len(c.canonical_bytes(doc))
        assert row.canonical_game_ref == doc["game"]["canonicalGameId"] and row.game_id is not None
        assert row.supersedes_forecast_id is None and row.manifest_sha256 is None
        assert c.validate_forecast(doc, evidence_modes=gen.EVIDENCE_MODES) == []
        # An exchange delivery can never claim AISP's in-process mode.
        assert "PROVENANCE_INVALID:evidenceMode" in c.validate_forecast(doc)
        # Provenance: the evidence document is the row column, bound by hash from the forecast; the column
        # also carries {path, sha256, bytes} so every NHL reader (and NhlForecastCard) sees the usual shape.
        ev = row.producer_evidence_ref
        body = {k: v for k, v in ev.items() if k not in ("path", "sha256", "bytes")}
        ref = {"path": gen.EVIDENCE_LOCATION, "sha256": _sha(c.canonical_bytes(body)),
               "bytes": len(c.canonical_bytes(body))}
        assert doc["provenance"]["producerEvidenceRef"] == ref == {k: ev[k] for k in ("path", "sha256", "bytes")}
        assert gen.evidence_binding(ev) == ref
        assert ev["producer"] == "AISP_IN_PROCESS_NHL_GEN1_V1" and ev["generatorGitRevision"] == "f" * 40
        assert ev["code"]["selectedModelSha256"] == gen.CONFIGURATION_SHA256
        assert {"generatorModuleSha256", "modelPortSha256", "producerModelCodeSha256"} <= set(ev["code"])
        assert ev["schedule"]["url"] == gen.ASSET_URL and ev["schedule"]["publisherDigestVerified"] is True
        assert ev["schedule"]["releaseDigest"] == "sha256:" + ev["schedule"]["sha256"]
        assert ev["rights"]["status"] == "RIGHTS_UNCHANGED" and ev["rights"]["licenseUnchanged"] is True
        assert ev["rights"]["readmeDeclaresCcBy40"] is True and ev["outcomeColumnsLoaded"] == []
        assert ev["restComplete"] is True and ev["restPendingEarlierGames"] == []
        assert ev["restCompleteRule"] == gen.REST_COMPLETE_RULE
        # The bound game is the forecast's game at the forecast's start.
        game = session.get(Game, row.game_id)
        assert _aware(game.starts_at) == _aware(row.scheduled_start_utc) and game.source_mode == "RESEARCH_SHADOW"
        mapped = session.scalar(select(ExternalIdMapping.entity_id).where(
            ExternalIdMapping.source == "NHL_STATS_GAME", ExternalIdMapping.external_id == row.canonical_game_ref))
        assert mapped == row.game_id
        if row.canonical_game_ref in want:
            theirs = want[row.canonical_game_ref]["probabilities"]
            assert abs(row.final_home_probability - theirs["final"]["home"]) <= TOLERANCE
            assert abs(row.regulation_tie_probability - theirs["regulation"]["tie"]) <= TOLERANCE
    receipts = session.scalars(select(NhlImportReceipt)).all()
    assert len(receipts) == len(rows) and {r.disposition for r in receipts} == {"IMPORTED"}
    assert all(r.source_label.startswith("AISP_IN_PROCESS_NHL_GEN1_V1:") for r in receipts)
    # Every response body is retained byte-exact; the schedule's archive is named in the evidence.
    archived = rows[0].producer_evidence_ref["schedule"]["rawArchive"]
    assert archived["status"] == "ARCHIVED" and archived["bodySha256"] == _sha(_schedule("20261013"))
    assert gzip.decompress(pathlib.Path(archived["bodyPath"]).read_bytes()) == _schedule("20261013")
    assert len(list(pathlib.Path(settings.raw_data_root).rglob("*.body.gz"))) == 4
    json.dumps(result)                                 # the job result is plain JSON


async def test_the_read_surface_shows_an_in_process_forecast_in_the_shape_the_card_reads(session, settings):
    from app.nhl.read_service import NhlReadService

    await _run(session, settings, _schedule("20261013"), OCT13_RUN)
    row = _forecasts(session)[0]
    summary = NhlReadService.forecast_summary(row, OCT13_RUN + timedelta(hours=1))
    ref = summary["provenance"]["producer_evidence_ref"]
    assert set(ref) == {"path", "sha256", "bytes"} and len(ref["sha256"]) == 64 and ref["bytes"] > 0
    assert summary["provenance"]["producer_evidence_status"] == "AISP_IN_PROCESS_GENERATED"
    assert summary["published_before_start"] is True
    json.dumps(summary)


async def test_a_rerun_with_identical_inputs_writes_nothing(session, settings):
    first, _ = await _run(session, settings, _schedule("20261013"), OCT13_RUN)
    session.commit()
    count = len(_forecasts(session))
    again, _ = await _run(session, settings, _schedule("20261013"), OCT13_RUN + timedelta(hours=3))
    assert again["status"] == "COMPLETED" and again["records"] == 0 and again["writes"] is False
    assert again["counts"]["UNCHANGED"] == first["counts"]["IMPORTED"] == count == len(_forecasts(session))


async def test_a_moved_start_supersedes_and_moves_the_game(session, settings):
    from app.models import Game

    await _run(session, settings, _schedule("20261013"), OCT13_RUN)
    session.commit()
    before = {r.canonical_game_ref: r for r in _forecasts(session)}
    moved = _schedule("20261013").replace(b"2026020090,20262027,R,2026-10-13,2026-10-13T22:00:00Z",
                                          b"2026020090,20262027,R,2026-10-13,2026-10-13T23:30:00Z")
    assert moved != _schedule("20261013")
    result, _ = await _run(session, settings, moved, OCT13_RUN + timedelta(hours=1))
    assert result["counts"]["REVISED"] == 1 and result["records"] == 1
    assert result["counts"]["UNCHANGED"] == result["counts"]["considered"] - 1
    rows = [r for r in _forecasts(session) if r.canonical_game_ref == "nhl:game:2026020090"]
    old, new = sorted(rows, key=lambda r: _aware(r.published_utc))
    assert old.forecast_id == before["nhl:game:2026020090"].forecast_id      # kept, never edited
    assert _aware(old.scheduled_start_utc) == datetime(2026, 10, 13, 22, tzinfo=UTC)
    assert new.supersedes_forecast_id == old.forecast_id == new.document["supersedesForecastId"]
    assert _aware(new.scheduled_start_utc) == datetime(2026, 10, 13, 23, 30, tzinfo=UTC)
    assert _aware(session.get(Game, new.game_id).starts_at) == datetime(2026, 10, 13, 23, 30, tzinfo=UTC)
    assert result["start_moved"] and result["start_moved"][0]["canonicalGameId"] == "nhl:game:2026020090"


async def test_a_back_to_back_game_is_not_forecast_a_day_ahead_and_the_game_day_run_forecasts_it(session, settings):
    """Oct 13 games are scheduled-but-unplayed at the Oct 13 run, so Oct 14's rest is not yet knowable:
    the day-ahead run writes nothing for those games, the Oct 14 09:20 ET run writes them fresh."""

    first, _ = await _run(session, settings, _schedule("20261013"), OCT13_RUN)
    session.commit()
    assert not any(r.document["game"]["scheduledStartUtc"] >= "2026-10-14T12" for r in _forecasts(session))
    assert {e["sourceGameId"] for e in first["excluded"] if e["reason"].startswith(gen.REST_INCOMPLETE)} == {
        "2026020106", "2026020107"}
    result, _ = await _run(session, settings, _schedule("20261013"), datetime(2026, 10, 14, 13, 20, tzinfo=UTC))
    assert result["target_dates"] == ["2026-10-14", "2026-10-15"]
    fresh = {g["canonicalGameId"]: g for g in result["games"] if g["disposition"] == "IMPORTED"}
    assert set(fresh) == {"nhl:game:2026020106", "nhl:game:2026020107"}, result["games"]
    assert not [g for g in result["games"] if g["disposition"] == "REVISED"]
    for ref, entry in fresh.items():
        assert entry["supersedesForecastId"] is None
        new = next(r for r in _forecasts(session) if r.forecast_id == entry["forecastId"])
        feats = new.producer_evidence_ref["inputFeatures"]
        # Both teams played on Oct 13: one day of rest, counted from that game.
        assert (feats["home_rest_days"], feats["away_rest_days"]) == (1, 1)
        assert feats["home_last_scheduled_game_date"] == feats["away_last_scheduled_game_date"] == "2026-10-13"
        assert new.producer_evidence_ref["restComplete"] is True


#: The producer's retained REAL Oct 13 capture (sha256 5032e57e...), cut to the 11 preparer columns for every
#: game date <= 2026-10-18 (136 rows): it lists Friday Oct 16 (PIT@COL 2026020119, BOS@ANA 2026020120) and
#: Saturday Oct 17, where PIT (@VGK, 2026020134) and BOS (@LAK, 2026020132) play the second of a back-to-back.
B2B = "20261013_through_20261018"
FRIDAY_RUN = datetime(2026, 10, 16, 13, 20, tzinfo=UTC)        # the Fri 09:20 ET cron
SATURDAY_RUN = datetime(2026, 10, 17, 13, 20, tzinfo=UTC)      # the Sat 09:20 ET cron
B2B_ASSET = {"FRI": "2026-10-16T08:00:00Z", "SAT": "2026-10-17T08:00:00Z"}


def _rows_for(session, source_id: str):
    return [r for r in _forecasts(session) if r.canonical_game_ref == f"nhl:game:{source_id}"]


async def test_a_friday_run_never_forecasts_saturdays_back_to_backs_with_friday_blind_rest(session, settings):
    """Regression (review MAJOR, 2026-10-16): the Friday run used to forecast VGK-PIT and LAK-BOS with PIT/BOS
    rest counted from Oct 13 (their Friday games had not started), and the paper route admitted it."""

    from app.nhl import paper
    from app.nhl.identity import ensure_game

    # VGK-PIT already exists in AISP (as a producer import or an earlier run would have made it).
    crosswalk = frozen.team_crosswalk()
    start = datetime(2026, 10, 18, 2, tzinfo=UTC)
    gid = ensure_game(session, {"canonicalGameId": "nhl:game:2026020134", "sourceGameId": "2026020134",
                                "season": "2026-27", "seasonStartYear": 2026,
                                "scheduledStartUtc": "2026-10-18T02:00:00Z",
                                "home": crosswalk["VGK"], "away": crosswalk["PIT"]})
    session.commit()
    friday, _ = await _run(session, settings, _schedule(B2B), FRIDAY_RUN, updated_at=B2B_ASSET["FRI"])
    assert friday["status"] == "COMPLETED" and friday["target_dates"] == ["2026-10-16", "2026-10-17"]
    session.commit()
    skipped = {e["sourceGameId"]: e["reason"] for e in friday["excluded"]}
    assert skipped == {"2026020132": f"{gen.REST_INCOMPLETE}:BOS:2026020120",
                       "2026020134": f"{gen.REST_INCOMPLETE}:PIT:2026020119"}, skipped
    assert _rows_for(session, "2026020134") == [] and _rows_for(session, "2026020132") == []
    # Friday's own games and the twelve Saturday games with no Friday team are forecast, rest complete.
    written = {r.source_game_id: r for r in _forecasts(session)}
    assert {"2026020119", "2026020120"} <= set(written) and len(written) == 2 + 12
    assert all(r.producer_evidence_ref["restComplete"] is True for r in written.values())

    # With only the Friday run held, the checkpoint has NO forecast for VGK-PIT: nothing can be decided on
    # the stale rest (before the fix: chosen, AISP_IN_PROCESS_ELIGIBLE, P(home) 0.5325 vs game-day 0.5795).
    moment = start - timedelta(minutes=20)
    assert paper.pregame_forecast(session, gid, moment=moment, starts_at=start) == (None, [])

    # Saturday 09:20 ET: PIT and BOS have played; the back-to-backs are forecast with one day of rest, and
    # every Saturday game the Friday run already wrote is UNCHANGED (the same features on the same bytes).
    saturday, _ = await _run(session, settings, _schedule(B2B), SATURDAY_RUN, updated_at=B2B_ASSET["SAT"])
    session.commit()
    by_game = {g["canonicalGameId"]: g for g in saturday["games"]}
    b2b = {"nhl:game:2026020134", "nhl:game:2026020132"}
    assert {by_game[ref]["disposition"] for ref in b2b} == {"IMPORTED"}
    saturday_refs = {ref for ref, g in by_game.items() if g["targetDate"] == "2026-10-17"}
    assert len(saturday_refs) == 14
    assert {by_game[ref]["disposition"] for ref in saturday_refs - b2b} == {"UNCHANGED"}
    (vgk_pit,) = _rows_for(session, "2026020134")
    (lak_bos,) = _rows_for(session, "2026020132")
    for row, side in ((vgk_pit, "away"), (lak_bos, "away")):
        feats = row.producer_evidence_ref["inputFeatures"]
        assert feats[f"{side}_rest_days"] == 1 and feats[f"{side}_last_scheduled_game_date"] == "2026-10-16"
        assert row.producer_evidence_ref["restComplete"] is True
    assert abs(vgk_pit.final_home_probability - 0.5795) < 5e-4         # the game-day value the review measured
    assert vgk_pit.game_id == gid and _aware(vgk_pit.scheduled_start_utc) == start
    chosen, _ = paper.pregame_forecast(session, vgk_pit.game_id, moment=moment, starts_at=start)
    assert chosen.forecast_id == vgk_pit.forecast_id
    assert paper.in_process_eligibility(chosen, moment=moment, starts_at=start)["eligible"] is True


def test_rest_completeness_is_judged_against_the_receipt_clock():
    head = ("game_id,season_full,game_type,game_date,game_time,home_team_abbr,away_team_abbr,home_team_name,"
            "away_team_name,game_state,venue\n")
    body = ("2026020401,20262027,R,2026-10-20,2026-10-20T23:00:00Z,BOS,TOR,Boston,Toronto,FUT,A\n"
            "2026020402,20262027,R,2026-10-21,2026-10-21T23:00:00Z,NYR,BOS,New York,Boston,FUT,B\n"
            "2026020403,20262027,R,2026-10-21,2026-10-21T23:30:00Z,MTL,OTT,Montreal,Ottawa,FUT,C\n")
    rows = gen.parse_schedule((head + body).encode())
    kw = {"crosswalk": frozen.team_crosswalk(), "model_team_ids": frozen.selected_state()["teamIds"]}
    # Before BOS's Oct 20 game starts: the Oct 21 BOS game is refused, the other Oct 21 game is not.
    early = gen.prepare_slate(rows, "2026-10-21", observed="2026-10-20T22:59:59Z",
                              started=datetime(2026, 10, 20, 23, tzinfo=UTC), **kw)
    assert [(e["sourceGameId"], e["reason"]) for e in early.excluded] == [
        ("2026020402", f"{gen.REST_INCOMPLETE}:BOS:2026020401")]
    assert [r["game_id"] for r in early.rows] == ["2026020403"]
    # Once the receipt clock is past BOS's Oct 20 start, that game is prior: one day of rest, no refusal.
    later = gen.prepare_slate(rows, "2026-10-21", observed="2026-10-20T23:00:01Z",
                              started=datetime(2026, 10, 20, 23, 1, tzinfo=UTC), **kw)
    assert later.excluded == [] and [r["game_id"] for r in later.rows] == ["2026020402", "2026020403"]
    bos = later.rows[0]
    assert bos["away_rest_days"] == 1 and bos["away_last_scheduled_game_date"] == "2026-10-20"
    assert gen.pending_earlier_games(rows, "BOS", "2026-10-21", "2026-10-20T22:59:59Z") == ["2026020401"]
    assert gen.pending_earlier_games(rows, "BOS", "2026-10-21", "2026-10-20T23:00:01Z") == []


async def test_an_existing_game_is_bound_never_duplicated(session, settings):
    from app.models import Game
    from app.nhl.identity import ensure_game

    crosswalk = frozen.team_crosswalk()
    gid = ensure_game(session, {"canonicalGameId": "nhl:game:2026020090", "sourceGameId": "2026020090",
                                "season": "2026-27", "seasonStartYear": 2026, "scheduledStartUtc": "2026-10-13T22:00:00Z",
                                "home": crosswalk["DET"], "away": crosswalk["NJD"]})
    session.commit()
    await _run(session, settings, _schedule("20261013"), OCT13_RUN)
    row = next(r for r in _forecasts(session) if r.canonical_game_ref == "nhl:game:2026020090")
    assert row.game_id == gid
    assert session.query(Game).count() == len({r.canonical_game_ref for r in _forecasts(session)})


async def test_a_reversed_orientation_is_refused_not_rebound(session, settings):
    from app.nhl.identity import ensure_game

    crosswalk = frozen.team_crosswalk()
    ensure_game(session, {"canonicalGameId": "nhl:game:2026020090", "sourceGameId": "2026020090",
                          "season": "2026-27", "seasonStartYear": 2026, "scheduledStartUtc": "2026-10-13T22:00:00Z",
                          "home": crosswalk["NJD"], "away": crosswalk["DET"]})
    session.commit()
    result, _ = await _run(session, settings, _schedule("20261013"), OCT13_RUN)
    refused = [g for g in result["games"] if g["canonicalGameId"] == "nhl:game:2026020090"]
    assert refused[0]["disposition"] == "REJECTED" and "ORIENTATION" in refused[0]["problems"][0]
    assert all(r.canonical_game_ref != "nhl:game:2026020090" for r in _forecasts(session))


async def test_a_backwards_write_clock_is_refused(session, settings):
    seen: list[str] = []
    async with httpx.AsyncClient(transport=_transport(_schedule("20261013"), seen=seen)) as client:
        capture = await gen.capture_source(settings, client=client, now=Clock(OCT13_RUN))
    # Run start, then the generation clock; every later read (the write clock) is earlier than generation.
    reads = iter([OCT13_RUN + timedelta(minutes=1), OCT13_RUN + timedelta(minutes=2)])

    def clock() -> datetime:
        return next(reads, OCT13_RUN + timedelta(seconds=90))

    result = gen.generate_from_capture(session, settings, capture, now=clock)
    assert result["counts"]["REJECTED"] == result["counts"]["considered"] > 0
    assert all("CLOCK_ORDER:write_clock_before_generation_clock" in g["problems"] for g in result["games"])
    assert _forecasts(session) == []


# =========================================================== fail closed ==
async def test_a_changed_license_fails_closed_before_any_schedule_request(session, settings):
    result, seen = await _run(session, settings, _schedule("20261013"), OCT13_RUN,
                              license_bytes=LICENSE.replace(b"MIT License", b"Proprietary License"))
    assert result["status"] == "RIGHTS_CHANGED" and result["reason"] == "LICENSE_BYTES_DIFFER_FROM_APPROVED"
    assert seen == [gen.LICENSE_URL] and result["records"] == 0 and result["writes"] is False
    assert _forecasts(session) == []
    # The changed text is itself retained as evidence.
    assert result["source"]["requests"][0]["archive"]["status"] == "ARCHIVED"


async def test_a_readme_that_drops_cc_by_4_fails_closed(session, settings):
    result, seen = await _run(session, settings, _schedule("20261013"), OCT13_RUN,
                              readme=b"# sportsdataverse-data\nAll rights reserved.\n")
    assert result["status"] == "RIGHTS_CHANGED" and result["reason"] == "README_NO_LONGER_DECLARES_CC_BY_4_0"
    assert seen == [gen.LICENSE_URL, gen.README_URL] and _forecasts(session) == []


@pytest.mark.parametrize("kw, reason", [
    ({"digest": "sha256:" + "a" * 64}, "ASSET_BYTES_DO_NOT_MATCH_RELEASE_DIGEST"),
    ({"digest": ""}, "RELEASE_API_PUBLISHES_NO_SHA256_DIGEST"),
])
async def test_a_digest_mismatch_fails_closed(session, settings, kw, reason):
    result, _ = await _run(session, settings, _schedule("20261013"), OCT13_RUN, **kw)
    assert result["status"] == "DIGEST_MISMATCH" and result["reason"] == reason
    assert result["records"] == 0 and _forecasts(session) == []


async def test_a_size_mismatch_fails_closed(session, settings):
    raw = _schedule("20261013")
    result, _ = await _run(session, settings, raw, OCT13_RUN, size=len(raw) + 1)
    assert result["status"] == "DIGEST_MISMATCH" and _forecasts(session) == []


async def test_a_redirect_to_an_unlisted_host_is_never_followed(session, settings):
    result, seen = await _run(session, settings, _schedule("20261013"), OCT13_RUN, redirect_host="evil.example.com")
    assert result["status"] == "SOURCE_REFUSED" and "HOST_NOT_ALLOWED" in result["reason"]
    assert all("evil.example.com" not in u for u in seen) and _forecasts(session) == []


async def test_a_stale_asset_is_refused(session, settings):
    result, seen = await _run(session, settings, _schedule("20261013"), OCT13_RUN, updated_at="2026-10-10T08:00:00Z")
    assert result["status"] == "ASSET_STALE" and gen.ASSET_URL not in seen and _forecasts(session) == []


# ======================================================================= job ==
def test_the_job_is_off_by_default_and_registered_only_when_on(settings):
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.date import DateTrigger

    from app.core.config import Settings
    from app.jobs.lanes import IMPORT_LANE, lane_for
    from app.jobs.orchestrator import AVAILABLE_JOBS
    from app.jobs.scheduler import build_scheduler

    assert Settings.model_fields["nhl_forecast_generation_enabled"].default is False
    assert "generate-nhl-forecasts" in AVAILABLE_JOBS
    off = build_scheduler(settings.model_copy(update={"nhl_forecast_generation_enabled": False}))
    assert [j.id for j in off.get_jobs() if "nhl-forecast-generation" in j.id] == []
    on = build_scheduler(settings.model_copy(update={"nhl_forecast_generation_enabled": True}))
    cron = on.get_job("asp-im-nhl-forecast-generation")
    startup = on.get_job("asp-im-nhl-forecast-generation-startup")
    for job in (cron, startup):
        assert job is not None and job.kwargs["job_name"] == "generate-nhl-forecasts"
        assert lane_for(job.id) == IMPORT_LANE and job.max_instances == 1 and job.coalesce is True
    assert isinstance(cron.trigger, CronTrigger) and str(cron.trigger.timezone) == "America/New_York"
    fields = {f.name: str(f) for f in cron.trigger.fields}
    assert fields["hour"] == "9,15" and fields["minute"] == "20"
    assert isinstance(startup.trigger, DateTrigger)
    delay = startup.trigger.run_date - datetime.now(UTC)
    assert timedelta(minutes=4) < delay <= timedelta(minutes=5)


async def test_a_manual_run_with_the_switch_off_sends_and_writes_nothing(session, settings, monkeypatch):
    from app.jobs.orchestrator import JobOrchestrator

    def boom(*a, **k):
        raise AssertionError("no request may be made while the switch is off")

    monkeypatch.setattr(gen, "_client", boom)
    run = await JobOrchestrator(session, settings.model_copy(update={"nhl_forecast_generation_enabled": False})).run(
        "generate-nhl-forecasts")
    assert run.result["status"] == "DISABLED" and run.result["records"] == 0
    assert _forecasts(session) == []


async def test_the_job_commits_a_run_and_records_a_serialisable_result(session, settings, monkeypatch):
    from app.jobs.orchestrator import JobOrchestrator
    from app.models import JobRun

    seen: list[str] = []
    monkeypatch.setattr(gen, "_client", lambda: httpx.AsyncClient(
        transport=_transport(_schedule("20261013"), seen=seen)))
    monkeypatch.setattr(gen, "_now", Clock(OCT13_RUN))
    monkeypatch.setattr(gen, "git_revision", lambda: "e" * 40)
    run = await JobOrchestrator(session, settings.model_copy(update={"nhl_forecast_generation_enabled": True})).run(
        "generate-nhl-forecasts")
    assert run.result["status"] == "COMPLETED" and run.result["records"] == len(_forecasts(session)) > 0
    session.expire_all()
    stored = session.get(JobRun, run.id)
    assert stored.result["counts"]["IMPORTED"] == run.result["records"] and len(seen) == 5
    assert _forecasts(session)[0].producer_evidence_ref["generatorGitRevision"] == "e" * 40

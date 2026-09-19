"""NBA Gen1 consumer: handoff pin, package verification, discovery, identity, finals, scoring.

All inputs are SYNTHETIC fixtures built in a temp directory (tests/nba_gen1_fixtures.py).
No provider is called and no real file is required; the one test that reads the
real Codex exchange is at the end and skips when the files are absent.
"""

from __future__ import annotations

import json
import pathlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import (
    ExternalIdMapping, Game, GameStatus, NbaChannelEvaluation, NbaDiscoveryObservation, NbaFinalObservation,
    NbaGen1Forecast, NbaHistoricalForecast,
)
from app.models.nba_gen1 import CHANNEL_ELO, CHANNEL_JOINT, CHANNEL_RIDGE, EVIDENCE_HISTORICAL, EVIDENCE_PROSPECTIVE
from app.nba import (
    gen1_common, gen1_evaluation, gen1_finals, gen1_handoff, gen1_historical, gen1_identity, gen1_package,
    gen1_production, gen1_read,
)
from tests import nba_gen1_fixtures as fx

NOW = datetime(2026, 10, 20, 22, 30, tzinfo=UTC)


@pytest.fixture
def world(tmp_path, settings):
    root = tmp_path / "exchange"
    root.mkdir()
    handoff = fx.make_handoff(root)
    configured = settings.model_copy(update={
        "nba_production_handoff_path": handoff["path"], "nba_production_handoff_sha256": handoff["sha256"],
        "nba_production_consumer_enabled": True, "nba_exchange_root": str(root)})
    return {"root": root, "handoff": handoff, "settings": configured, "tmp": tmp_path}


# ------------------------------------------------------------------ handoff --

def test_no_pin_is_not_received_and_imports_nothing(session, settings):
    assert gen1_handoff.production_status(settings).status == gen1_handoff.NOT_RECEIVED
    result = gen1_production.consume(session, settings, now=NOW)
    assert result["status"].startswith("CONSUMER_NOT_ACTIVE:NOT_RECEIVED")
    assert session.scalar(select(NbaGen1Forecast)) is None


def test_verified_pin_is_staged_until_the_consumer_switch_is_on(world):
    off = world["settings"].model_copy(update={"nba_production_consumer_enabled": False})
    assert gen1_handoff.production_status(off).status == gen1_handoff.STAGED
    verdict = gen1_handoff.production_status(world["settings"])
    assert verdict.status == gen1_handoff.ACTIVE
    assert verdict.detail["identity"]["authority"]["producerActivated"] is False


def test_a_member_one_byte_different_is_rejected(world):
    manifest = pathlib.Path(world["handoff"]["path"]).parent / "manifest.json"
    manifest.write_bytes(manifest.read_bytes() + b" ")
    verdict = gen1_handoff.production_status(world["settings"])
    assert verdict.status == gen1_handoff.REJECTED and "MEMBER_MISMATCH" in verdict.reason


def test_a_handoff_claiming_wagering_authority_is_rejected(tmp_path, settings):
    handoff = fx.make_handoff(tmp_path, wagering=True)
    verdict = gen1_handoff.production_status(settings.model_copy(update={
        "nba_production_handoff_path": handoff["path"], "nba_production_handoff_sha256": handoff["sha256"]}))
    assert verdict.status == gen1_handoff.REJECTED and "WAGERINGAUTHORITY" in verdict.reason


# ------------------------------------------------------------------ package --

def test_a_v1_package_verifies_and_every_summary_number_is_recomputed(world):
    package = fx.make_package(world["root"], world["handoff"])
    verified = gen1_package.verify_package(package, handoff_sha256=world["handoff"]["sha256"],
                                           effective_at=fx.EFFECTIVE)
    assert verified["schema"] == gen1_common.INDEX_SCHEMA_V1
    assert verified["joint_summary"]["draw_count"] == 10_000
    assert len(verified["joint_summary"]["intervals"]["total"]) == 3


@pytest.mark.parametrize("mutate, problem", [
    (lambda f: f["jointSimulation"]["pmfs"]["total"][0].update(count=f["jointSimulation"]["pmfs"]["total"][0]["count"] + 1),
     "JOINT_PMF_TOTAL_DOES_NOT_REPRODUCE"),
    (lambda f: f["jointSimulation"]["winnerProbabilities"].update(home=0.99), "JOINT_WINNER_DOES_NOT_REPRODUCE"),
    (lambda f: f["jointSimulation"]["intervals"]["margin"][1].update(lower=-99),
     "JOINT_INTERVAL_MARGIN_DOES_NOT_REPRODUCE"),
    (lambda f: f.update(createdAtUtc="2026-09-17T00:00:00+00:00"), "CREATED_BEFORE_PROSPECTIVE_EFFECTIVE_TIME"),
    (lambda f: f.update(configurationIdentity="SOMETHING_ELSE"), "CONFIGURATION_MISMATCH"),
    (lambda f: f["closeScoreChallenger"].update(enabled=True), "CLOSE_SCORE_CHALLENGER_NOT_DISABLED"),
    (lambda f: f["authority"].update(wageringAuthority=True), "AUTHORITY_WAGERINGAUTHORITY_NOT_FALSE"),
    (lambda f: f["eloWinnerProbabilities"].update(home=0.7), "ELO_MASS_NOT_ONE"),
    (lambda f: f["jointSimulation"]["illustrativeTotals"][0].update(lineClassification="MARKET"),
     "ILLUSTRATIVE_TOTAL_NOT_LABELLED_ILLUSTRATIVE"),
])
def test_a_package_that_does_not_reproduce_is_refused(world, mutate, problem):
    package = fx.make_package(world["root"], world["handoff"], mutate=mutate)
    with pytest.raises(gen1_package.PackageRefused) as refused:
        gen1_package.verify_package(package, handoff_sha256=world["handoff"]["sha256"], effective_at=fx.EFFECTIVE)
    assert any(problem in p for p in refused.value.problems), refused.value.problems


def test_a_package_bound_to_another_handoff_or_with_a_changed_population_is_refused(world, tmp_path):
    other = fx.make_handoff(tmp_path / "other", status="ACTIVE")
    package = fx.make_package(world["root"], other)
    with pytest.raises(gen1_package.PackageRefused) as refused:
        gen1_package.verify_package(package, handoff_sha256=world["handoff"]["sha256"], effective_at=fx.EFFECTIVE)
    assert "PACKAGE_NOT_BOUND_TO_PINNED_HANDOFF" in refused.value.problems
    package = fx.make_package(world["root"], world["handoff"], seed=9)
    population = package / "population" / "joint-final-score-draws.parquet"
    population.write_bytes(population.read_bytes()[:-1] + b"\x00")
    with pytest.raises(gen1_package.PackageRefused) as refused:
        gen1_package.verify_package(package, handoff_sha256=world["handoff"]["sha256"], effective_at=fx.EFFECTIVE)
    assert any("MEMBER_MISMATCH" in p for p in refused.value.problems)


# ------------------------------------------------------- discovery / import --

def test_v1_discovery_imports_once_binds_the_game_and_is_never_decision_eligible(session, world):
    package = fx.make_package(world["root"], world["handoff"])
    fx.write_v1_discovery(world["root"], [package])
    first = gen1_production.consume(session, world["settings"], now=NOW)
    assert first["status"] == "V1_DISCOVERY_RECORDED" and first["imported"] == 1, first
    row = session.scalar(select(NbaGen1Forecast))
    assert row.publication_clock_source == gen1_production.CLOCK_V1
    assert row.canonical_game_id is not None
    game = session.get(Game, row.canonical_game_id)
    assert game.source_mode == "RESEARCH_SHADOW"
    mapping = session.scalar(select(ExternalIdMapping).where(ExternalIdMapping.entity_id == game.id))
    assert (mapping.source, mapping.external_id) == ("NBA_ESPN", "990000001")
    # Three channels, each in its own columns.
    assert row.ridge_home_points == pytest.approx(116.2) and row.elo_home_win_probability == pytest.approx(0.64)
    assert row.joint_draw_count == 10_000 and 0 < row.joint_home_win_probability < 1
    again = gen1_production.consume(session, world["settings"], now=NOW)
    assert again["status"] == "V1_ALREADY_RECORDED" and again["imported"] == 0
    reading = gen1_production.present_eligibility(session, row, now=NOW)
    assert reading["state"] == gen1_production.NO_LEASE and reading["eligible"] is False


def test_different_bytes_under_a_known_forecast_id_are_rejected(session, world):
    package = fx.make_package(world["root"], world["handoff"])
    fx.write_v1_discovery(world["root"], [package])
    gen1_production.consume(session, world["settings"], now=NOW)
    tampered = fx.make_package(world["root"], world["handoff"], ridge=(120.0, 100.0))  # same forecastId, new bytes
    fx.write_v1_discovery(world["root"], [tampered])
    result = gen1_production.consume(session, world["settings"], now=NOW)
    assert result["rejected"] == 1
    assert any("CONFLICTING_BYTES" in p for p in result["rejections"][0]["problems"])


def _v2(world, *, eligible=True, until=NOW + timedelta(minutes=10), committed="2026-10-20T22:14:00+00:00", **kw):
    package = fx.make_package(world["root"], world["handoff"], schema="V2", created="2026-10-20T22:10:00+00:00")
    manifest = fx.write_v2_discovery(world["root"], world["handoff"], [(package, {
        "committedAtUtc": committed, "currentlyEligible": eligible,
        "eligibilityValidUntilUtc": until.isoformat() if until else None})], **kw)
    return package, manifest


def test_v2_uses_the_receipt_clock_and_a_live_lease(session, world):
    _v2(world)
    result = gen1_production.consume(session, world["settings"], now=NOW)
    assert result["format"] == "V2" and result["imported"] == 1, result
    row = session.scalar(select(NbaGen1Forecast))
    assert row.publication_clock_source == gen1_production.CLOCK_V2
    assert gen1_common.iso(row.exchange_published_utc) == "2026-10-20T22:14:00+00:00"
    assert row.producer_published_utc is None
    assert gen1_production.present_eligibility(session, row, now=NOW)["state"] == gen1_production.CURRENT
    later = gen1_production.present_eligibility(session, row, now=NOW + timedelta(minutes=11))
    assert later["state"] == gen1_production.EXPIRED_LEASE and later["eligible"] is False


def test_v2_refuses_a_release_that_does_not_bind_the_pinned_handoff(session, world, tmp_path):
    other = fx.make_handoff(tmp_path / "other", status="ACTIVE")
    _v2(world, release_handoff=other)
    result = gen1_production.consume(session, world["settings"], now=NOW)
    assert result["status"].startswith("DISCOVERY_REFUSED") and "RELEASE_DOES_NOT_BIND" in result["status"]


def test_v2_refuses_a_receipt_that_disagrees_with_its_entry(session, world):
    _v2(world, receipt_override={"committedAtUtc": "2026-10-20T20:00:00+00:00"})
    result = gen1_production.consume(session, world["settings"], now=NOW)
    assert result["rejected"] == 1 and "RECEIPT_COMMITTEDATUTC_MISMATCH" in result["rejections"][0]["problems"]
    obs = session.scalar(select(NbaDiscoveryObservation))
    assert obs.verified is False and obs.nba_gen1_forecast_id is None


def test_v2_successor_ticks_record_only_new_manifests_and_never_reverify_known_packages(
        session, world, monkeypatch):
    package, first = _v2(world)
    gen1_production.consume(session, world["settings"], now=NOW)
    # The next producer tick: same package, fresh lease, predecessor = the first manifest.
    fx.write_v2_discovery(world["root"], world["handoff"], [(package, {
        "committedAtUtc": "2026-10-20T22:14:00+00:00", "currentlyEligible": True,
        "eligibilityValidUntilUtc": (NOW + timedelta(minutes=25)).isoformat()})],
        created="2026-10-20T22:30:00+00:00", predecessor=first)
    monkeypatch.setattr(gen1_package, "verify_package",
                        lambda *a, **k: pytest.fail("a known package is never fully re-verified"))
    second = gen1_production.consume(session, world["settings"], now=NOW + timedelta(minutes=15))
    assert len(second["manifests_recorded"]) == 1 and second["duplicate"] == 1 and second["imported"] == 0
    row = session.scalar(select(NbaGen1Forecast))
    assert gen1_production.present_eligibility(session, row, now=NOW + timedelta(minutes=20))["state"] == \
        gen1_production.CURRENT


def test_a_refused_admission_leaves_no_half_imported_forecast(session, world):
    gen1_identity.admit_game(session, codex_game_id="nba-espn:game:990000001",
                             home_franchise_id="nba-neil:franchise:FXH", away_franchise_id="nba-neil:franchise:FXA",
                             home_code="FXH", away_code="FXA", tipoff_utc=fx.TIPOFF + timedelta(hours=3),
                             season_end=2027)
    package = fx.make_package(world["root"], world["handoff"])
    fx.write_v1_discovery(world["root"], [package])
    result = gen1_production.consume(session, world["settings"], now=NOW)
    assert result["rejected"] == 1 and "IdentityRefused" in result["rejections"][0]["problems"][0]
    assert session.scalar(select(NbaGen1Forecast)) is None


# ----------------------------------------------------------------- identity --

def test_admission_refuses_a_moved_tipoff_and_the_report_is_unique_both_ways(session, world):
    package = fx.make_package(world["root"], world["handoff"])
    fx.write_v1_discovery(world["root"], [package])
    gen1_production.consume(session, world["settings"], now=NOW)
    with pytest.raises(gen1_identity.IdentityRefused):
        gen1_identity.admit_game(session, codex_game_id="nba-espn:game:990000001",
                                 home_franchise_id="nba-neil:franchise:FXH", away_franchise_id="nba-neil:franchise:FXA",
                                 home_code="FXH", away_code="FXA", tipoff_utc=fx.TIPOFF + timedelta(hours=1),
                                 season_end=2027)
    report = gen1_identity.report(session, schedule_rows=[{"codex_game_id": "nba-espn:game:990000002",
                                                           "home_franchise_id": "nba-neil:franchise:FXH",
                                                           "away_franchise_id": "nba-neil:franchise:FXB",
                                                           "disposition": "NOT_DUE"},
                                                          {"codex_game_id": "nba-espn:game:TBD",
                                                           "disposition": "UNMAPPED_TEAM"}])
    states = {g["codex_game_id"]: g["state"] for g in report["games"]}
    assert states["nba-espn:game:990000001"] == "MAPPED"
    assert states["nba-espn:game:990000002"] == "UNMAPPED"
    assert states["nba-espn:game:TBD"] == "UNMAPPABLE_ID"
    assert report["fixture_id_space"]["nba_games"] == 0


def test_franchise_to_espn_team_pairing_refuses_a_conflict():
    codex = [{"espn_game_id": "1", "home_franchise_id": "F:A", "away_franchise_id": "F:B"},
             {"espn_game_id": "2", "home_franchise_id": "F:A", "away_franchise_id": "F:C"}]
    rows = {"1": {"home_id": "10", "away_id": "20"}, "2": {"home_id": "11", "away_id": "30"}}
    pairing = gen1_identity.espn_team_pairs(codex, rows)
    assert "F:A" not in pairing["pairs"] and pairing["conflicts"][0]["franchise"] == "F:A"
    assert pairing["pairs"] == {"F:B": "20", "F:C": "30"}


# ------------------------------------------------------------ finals/scores --

def _with_final(world, rows, **kw):
    entry = fx.write_schedule_object(world["tmp"], rows, **kw)
    return world["settings"].model_copy(update={"nba_final_source_objects": entry})


def test_finals_come_only_from_a_pinned_object_and_bind_exactly(session, world):
    package = fx.make_package(world["root"], world["handoff"])
    fx.write_v1_discovery(world["root"], [package])
    # Orientation needs the ESPN team binding; the retained 2026-27 schedule row supplies it at import.
    configured = _with_final(world, [fx.schedule_row(990000001, final=False)])
    gen1_production.consume(session, configured, now=NOW)
    assert gen1_finals.ingest_retained(session, world["settings"], now=NOW)["status"] == "NO_PINNED_SOURCE_OBJECT"
    assert gen1_finals.ingest_retained(session, configured, now=NOW)["not_final_yet"] == 1
    finished = _with_final(world, [fx.schedule_row(990000001, home=121, away=117, periods=5.0)])
    first = gen1_finals.ingest_retained(session, finished, now=NOW + timedelta(hours=4))
    assert first["observations_written"] == 1 and first["games_projected_final"] == 1, first
    final = session.scalar(select(NbaFinalObservation))
    assert (final.home_points, final.away_points, final.overtime) == (121, 117, True)
    assert final.completion_clock_status == "ABSENT_FROM_RETAINED_SOURCE_SCHEMA"
    game = session.get(Game, final.game_id)
    assert game.status == GameStatus.FINAL and (game.home_score, game.away_score) == (121, 117)
    assert game.final_at is None
    again = gen1_finals.ingest_retained(session, finished, now=NOW + timedelta(hours=5))
    assert again["observations_written"] == 0 and again["observations_reused"] == 1


@pytest.mark.parametrize("row_kwargs, skipped", [
    ({"home_id": 999}, "HOME_AWAY_IDENTITY_MISMATCH"),
    ({"tipoff": fx.TIPOFF + timedelta(hours=2)}, "TIPOFF_DIFFERS_FROM_ADMITTED_GAME"),
])
def test_a_final_that_disagrees_with_the_admitted_game_is_refused(session, world, row_kwargs, skipped):
    package = fx.make_package(world["root"], world["handoff"])
    fx.write_v1_discovery(world["root"], [package])
    gen1_production.consume(session, _with_final(world, [fx.schedule_row(990000001, final=False)]), now=NOW)
    result = gen1_finals.ingest_retained(session, _with_final(world, [fx.schedule_row(990000001, **row_kwargs)]), now=NOW)
    assert result["skipped"][0]["skipped"] == skipped and result["observations_written"] == 0


def test_each_channel_is_scored_separately_and_ridge_never_carries_a_probability(session, world):
    package = fx.make_package(world["root"], world["handoff"])
    fx.write_v1_discovery(world["root"], [package])
    gen1_production.consume(session, _with_final(world, [fx.schedule_row(990000001, final=False)]), now=NOW)
    gen1_finals.ingest_retained(session, _with_final(world, [fx.schedule_row(990000001, home=118, away=109)]), now=NOW)
    summary = gen1_evaluation.evaluate(session, now=NOW)
    assert summary["by_class"][EVIDENCE_PROSPECTIVE] == 3
    rows = {r.channel: r for r in session.scalars(select(NbaChannelEvaluation))}
    ridge, elo, joint = rows[CHANNEL_RIDGE], rows[CHANNEL_ELO], rows[CHANNEL_JOINT]
    assert ridge.log_loss is None and ridge.two_way_brier is None and ridge.home_win_probability is None
    assert ridge.home_abs_error == pytest.approx(abs(116.2 - 118))
    assert elo.predicted_home is None and elo.log_loss == pytest.approx(-__import__("math").log(0.64))
    assert elo.two_way_brier == pytest.approx((0.64 - 1) ** 2) and elo.winner_correct is True
    assert joint.crps_total is not None and joint.interval_coverage["total"]["80"]["contains"] in (True, False)
    assert gen1_evaluation.evaluate(session, now=NOW)["written"] == 0  # idempotent


def test_the_database_refuses_a_probability_on_a_ridge_row(session, world):
    package = fx.make_package(world["root"], world["handoff"])
    fx.write_v1_discovery(world["root"], [package])
    gen1_production.consume(session, _with_final(world, [fx.schedule_row(990000001, final=False)]), now=NOW)
    game_id = session.scalar(select(NbaGen1Forecast.canonical_game_id))
    session.add(NbaChannelEvaluation(
        evidence_class=EVIDENCE_PROSPECTIVE, channel=CHANNEL_RIDGE, forecast_identity="x", canonical_game_id=game_id,
        source_game_id="1", start_utc=NOW, result_identity="r", result_source="s", home_score=1, away_score=0,
        log_loss=0.5, detail={}, scoring_version="v", evaluated_at=NOW))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_a_package_published_after_tipoff_is_never_scored(session, world):
    package = fx.make_package(world["root"], world["handoff"])
    fx.write_v1_discovery(world["root"], [package])
    gen1_production.consume(session, _with_final(world, [fx.schedule_row(990000001, final=False)]), now=NOW)
    row = session.scalar(select(NbaGen1Forecast))
    game = session.get(Game, row.canonical_game_id)
    game.starts_at = datetime(2026, 9, 18, 1, 0, tzinfo=UTC)  # tipoff moved before publication
    session.flush()
    game.status = GameStatus.FINAL
    game.home_score, game.away_score = 100, 90
    from app.models import GameStateObservation

    session.add(GameStateObservation(game_id=game.id, scheduled_at=game.starts_at, status=GameStatus.FINAL.value, home_score=100,
                                     away_score=90, observed_at=NOW, available_as_of=NOW,
                                     source="ESPN_SPORTSDATAVERSE", source_mode="RESEARCH_SHADOW",
                                     payload_checksum="c" * 64))
    session.flush()
    summary = gen1_evaluation.evaluate(session, now=NOW)
    assert summary["written"] == 0 and summary["skipped"][0]["skipped"] == "NO_SCORABLE_FORECAST"


# --------------------------------------------------------------- historical --

HIST_GAMES = [
    {"espn": 990000101, "home": "FXH", "away": "FXA", "date": "2025-10-21",
     "tipoff": datetime(2025, 10, 21, 23, 30, tzinfo=UTC), "elo": 0.73, "ridge_home": 117.0, "ridge_away": 108.5,
     "seed": 11},
    {"espn": 990000102, "home": "FXA", "away": "FXH", "date": "2025-10-23",
     "tipoff": datetime(2025, 10, 24, 0, 0, tzinfo=UTC), "elo": 0.55, "ridge_home": 112.0, "ridge_away": 111.0,
     "seed": 12},
]


def _historical_settings(world, **kw):
    receipts = fx.make_historical(world["tmp"], HIST_GAMES, **kw)
    rows = [fx.schedule_row(g["espn"], tipoff=g["tipoff"], home_id=101 if g["home"] == "FXH" else 202,
                            away_id=202 if g["away"] == "FXA" else 101, home=120, away=100,
                            home_code=g["home"], away_code=g["away"]) for g in HIST_GAMES]
    return world["settings"].model_copy(update={
        "nba_historical_assessment_receipt_path": receipts["assessment_path"],
        "nba_historical_assessment_receipt_sha256": receipts["assessment_sha"],
        "nba_historical_joint_receipt_path": receipts["joint_path"],
        "nba_historical_joint_receipt_sha256": receipts["joint_sha"],
        "nba_final_source_objects": fx.write_schedule_object(world["tmp"], rows)})


def test_historical_rows_are_imported_labelled_and_scored_in_their_own_class(session, world):
    configured = _historical_settings(world)
    summary = gen1_historical.import_historical(session, configured, now=NOW)
    assert summary["imported"] == 2 and summary["franchise_espn_pairs"] == 2, summary
    row = session.scalar(select(NbaHistoricalForecast).where(NbaHistoricalForecast.source_game_id == "990000101"))
    assert row.evidence_class == EVIDENCE_HISTORICAL and row.joint_draw_count == 10_000
    assert row.ridge_home_points == 117.0 and row.elo_home_win_probability == 0.73
    gen1_finals.ingest_retained(session, configured, now=NOW)
    scored = gen1_evaluation.evaluate(session, now=NOW)
    assert scored["by_class"] == {EVIDENCE_PROSPECTIVE: 0, EVIDENCE_HISTORICAL: 6}
    again = gen1_historical.import_historical(session, configured, now=NOW)
    assert again["imported"] == 0 and again["reused"] == 2


def test_historical_rows_that_do_not_cross_check_are_skipped_by_name(session, world):
    configured = _historical_settings(world, corrupt_digest_for=0, joint_ridge_offset_for=1)
    summary = gen1_historical.import_historical(session, configured, now=NOW)
    reasons = {s["codex_game_id"]: s["reason"] for s in summary["skipped"]}
    assert reasons == {"nba-espn:game:990000101": "JOINT_COUNTS_DIGEST_MISMATCH",
                       "nba-espn:game:990000102": "JOINT_RECEIPT_DISAGREES_WITH_ASSESSMENT"}
    assert summary["imported"] == 0


def test_the_historical_import_refuses_without_pinned_receipts(session, settings):
    with pytest.raises(gen1_historical.HistoricalRefused):
        gen1_historical.import_historical(session, settings, now=NOW)


# ----------------------------------------------------------------- surfaces --

def test_performance_keeps_classes_and_channels_apart(session, world):
    configured = _historical_settings(world)
    gen1_historical.import_historical(session, configured, now=NOW)
    gen1_finals.ingest_retained(session, configured, now=NOW)
    gen1_evaluation.evaluate(session, now=NOW)
    from app.services.model_performance_read import ModelPerformanceReadService

    payload = ModelPerformanceReadService(session).league("NBA", now=NOW)
    by_engine = {m["engine_id"]: m for m in payload["models"]}
    assert set(by_engine) == {"NBA_GEN1_RIDGE_POINTS", "NBA_GEN1_ELO_WINNER", "NBA_GEN1_JOINT_DISTRIBUTION"}
    ridge = by_engine["NBA_GEN1_RIDGE_POINTS"]
    assert ridge["evidence_classification"] == "HISTORICAL_RECONSTRUCTION_NOT_PROSPECTIVE"
    assert ridge["cohorts"]["ALL"]["log_loss"] is None and ridge["cohorts"]["ALL"]["margin_mae"] is not None
    assert by_engine["NBA_GEN1_ELO_WINNER"]["cohorts"]["ALL"]["log_loss"] is not None
    assert by_engine["NBA_GEN1_ELO_WINNER"]["cohorts"]["VERIFIED_PUBLICATION"]["n"] == 0
    assert payload["requirement"] is None


def test_the_nba_routes_serve_board_game_and_identity(client, session, world):
    configured = _historical_settings(world)
    gen1_historical.import_historical(session, configured, now=NOW)
    gen1_finals.ingest_retained(session, configured, now=NOW)
    gen1_evaluation.evaluate(session, now=NOW)
    session.commit()
    board = client.get("/api/v1/nba/gen1/board").json()
    assert board["historical"]["rows"] == 2 and board["handoff"]["status"] == "NOT_RECEIVED"
    detail = client.get("/api/v1/nba/gen1/games/990000101").json()
    assert detail["historical_forecasts"][0]["label"].startswith("HISTORICAL")
    channels = detail["historical_forecasts"][0]["channels"]
    assert channels["RIDGE_POINTS"]["is_probability"] is False and channels["ELO_WINNER"]["is_probability"] is True
    assert {e["channel"] for e in detail["evaluations"]} == {CHANNEL_RIDGE, CHANNEL_ELO, CHANNEL_JOINT}
    assert detail["finals"][0]["source"] == "ESPN_SPORTSDATAVERSE"
    assert client.get("/api/v1/nba/gen1/games/nba-espn:game:990000101").status_code == 200
    assert client.get("/api/v1/nba/gen1/games/123").status_code == 404
    identity = client.get("/api/v1/nba/gen1/identity").json()
    assert identity["game_counts"] == {"MAPPED": 2}


def test_readiness_reports_each_component_with_what_is_due(session, world):
    package = fx.make_package(world["root"], world["handoff"])
    fx.write_v1_discovery(world["root"], [package])
    gen1_production.consume(session, world["settings"], now=NOW - timedelta(days=30))
    components = gen1_read.readiness(session, world["settings"], now=NOW - timedelta(days=30))
    assert components["forecast"]["state"] == "PRODUCTION_PACKAGE_HELD"
    assert components["eligibility"]["state"] == "NO_LEASE_IN_SCHEMA"
    assert components["price"]["state"] == "LANE_DISABLED"
    assert components["decision"]["state"] == "OWNER_INACTIVE" and components["decision"]["grant"] == "NO_GRANT_PINNED"
    assert "NOT YET DUE" in components["outcomes"]["prospective"]


def test_the_slate_model_is_the_joint_channel_and_says_so(session, world):
    package = fx.make_package(world["root"], world["handoff"])
    fx.write_v1_discovery(world["root"], [package])
    gen1_production.consume(session, world["settings"], now=NOW)
    row = session.scalar(select(NbaGen1Forecast))
    model = gen1_read.model_for_game(session, row.canonical_game_id)
    assert model["controlling_channel"] == CHANNEL_JOINT and model["home"] == row.joint_home_win_probability
    assert model["expected_points_channel"] == CHANNEL_RIDGE
    assert model["channels"][CHANNEL_ELO]["home"] == pytest.approx(0.64)


def test_the_slate_block_labels_nba_and_says_why_there_is_no_price(session, world):
    from app.services.command_center import _nba_coverage

    package = fx.make_package(world["root"], world["handoff"])
    fx.write_v1_discovery(world["root"], [package])
    gen1_production.consume(session, world["settings"], now=NOW)
    game_id = str(session.scalar(select(NbaGen1Forecast.canonical_game_id)))
    block = _nba_coverage(session, [game_id], now=NOW)
    model = block["models"][game_id]
    assert model["controlling_channel"] == CHANNEL_JOINT and model["expected_points_channel"] == CHANNEL_RIDGE
    assert model["evidence_class"] == EVIDENCE_PROSPECTIVE
    assert block["price"]["reason"] == "LANE_DISABLED" and block["collection"]["active"] is False
    assert block["eligibility"][game_id]["now"]["state"] == gen1_production.NO_LEASE


def test_a_historical_row_on_the_slate_is_labelled_historical(session, world):
    from app.services.command_center import _nba_coverage

    configured = _historical_settings(world)
    gen1_historical.import_historical(session, configured, now=NOW)
    game_id = str(session.scalar(select(NbaHistoricalForecast.canonical_game_id)))
    block = _nba_coverage(session, [game_id], now=NOW)
    model = block["models"][game_id]
    assert model["evidence_class"] == EVIDENCE_HISTORICAL and "HISTORICAL" in model["evidence_label"]
    assert model["engine_id"] == "NBA_GEN1_HISTORICAL" and block["eligibility"] == {}


def test_nothing_nba_is_scheduled_or_consumed_by_default(settings, monkeypatch):
    from app.jobs import scheduler as scheduler_module
    from app.jobs.model_exchange_consumer import _consume_nba_production

    monkeypatch.setattr(scheduler_module, "daily_pipeline_catch_up_time", lambda s: None)
    armed = settings.model_copy(update={
        "scheduler_enabled": True, "live_shadow_enabled": False, "nfl_shadow_enabled": False,
        "prediction_market_observer_enabled": False, "event_relative_scheduler_enabled": False,
        "odds_polling_enabled": False})
    ids = {job.id for job in scheduler_module.build_scheduler(armed).get_jobs()}
    assert not {i for i in ids if "nba" in i}
    on = {job.id for job in scheduler_module.build_scheduler(
        armed.model_copy(update={"nba_production_consumer_enabled": True})).get_jobs()}
    assert {"asp-nba-paper-checkpoint", "asp-nba-final-ingest"} <= on and "asp-nba-pregame-odds" not in on
    assert _consume_nba_production(None, settings, dry_run=False)["status"] == "NOT_RUN"


def test_the_migration_downgrades_and_upgrades_to_the_model_schema(tmp_path):
    """0048 then 0049: from the full model schema, downgrading in reverse removes exactly
    what they add, and upgrading again restores every table and column the models declare."""

    import importlib.util

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect

    from app.db.base import Base

    def load(name: str, path: str):
        spec = importlib.util.spec_from_file_location(
            name, pathlib.Path(__file__).parents[1] / "alembic" / "versions" / path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    first = load("m0048", "20260919_0048_nba_gen1_consumer.py")
    second = load("m0049", "20260919_0049_nba_final_exchange_lane.py")
    assert second.down_revision == first.revision
    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    Base.metadata.create_all(engine)
    tables = {"nba_gen1_forecasts", "nba_historical_forecasts", "nba_discovery_manifests",
              "nba_discovery_observations", "nba_final_observations", "nba_final_discovery_manifests",
              "nba_channel_evaluations"}
    expected = {t: {c.name for c in Base.metadata.tables[t].columns} for t in tables}
    for module in (second, first):
        with engine.begin() as connection:
            module.op = Operations(MigrationContext.configure(connection))
            module.downgrade()
    after_down = inspect(engine)
    assert not tables & set(after_down.get_table_names())
    assert "nba_gen1_forecast_id" not in {c["name"] for c in after_down.get_columns("shadow_experiment_decisions")}
    for module in (first, second):
        with engine.begin() as connection:
            module.op = Operations(MigrationContext.configure(connection))
            module.upgrade()
    after_up = inspect(engine)
    for table in tables:
        assert {c["name"] for c in after_up.get_columns(table)} == expected[table], table
    assert "nba_gen1_forecast_id" in {c["name"] for c in after_up.get_columns("shadow_experiment_decisions")}
    engine.dispose()


def test_market_channels_never_let_ridge_answer_a_price():
    assert gen1_common.channels_for("MONEYLINE") == (CHANNEL_ELO, CHANNEL_JOINT)
    assert gen1_common.channels_for("SPREAD") == (CHANNEL_JOINT,)
    assert gen1_common.channels_for("PLAYER_POINTS") == ()
    assert all(CHANNEL_RIDGE not in v for v in gen1_common.MARKET_CHANNELS.values())


# ---------------------------------------------------- the real Codex bytes --

REAL_HANDOFF = pathlib.Path(r"C:\Users\james\asp-model-exchange\nba-gen1-production-v1\handoffs\sha256"
                            r"\7113b71ff5819c89103ad19d5f9afb22c51dbc92980be12b56ce8826cb34ccaf\index.json")
REAL_PACKAGE = pathlib.Path(r"C:\Users\james\asp-model-exchange\nba-gen1-production-v1\forecasts\sha256"
                            r"\8a725a439af9f9ad4828281ff9bcdec5511c2af292c3feac4826b975dfa0d4c2")


@pytest.mark.skipif(not REAL_PACKAGE.exists(), reason="the real Codex exchange is not on this machine")
def test_codex_selected_phi_at_nyk_package_verifies_byte_for_byte():
    verified = gen1_handoff.verify_handoff(REAL_HANDOFF, gen1_handoff.SELECTED_HANDOFF_SHA256)
    assert gen1_handoff.check_identity(gen1_handoff.identity(verified["manifest"])) == []
    package = gen1_package.verify_package(REAL_PACKAGE, handoff_sha256=gen1_handoff.SELECTED_HANDOFF_SHA256,
                                          effective_at="2026-09-18T01:16:08.176807+00:00")
    assert package["forecast_sha256"] == "8c69bc25e64c969b0dfd2f0cfe3b3ec7af0f8699358368a44ab4289ba3f46282"
    assert package["population_logical_sha256"] == "6d7b75fbe79f3803269643f88bf29b117e182730d38b2ba5477d0bb70e2c0bb7"
    assert package["joint_summary"]["winner"]["home"] == 0.6819
    forecast = package["forecast"]
    assert forecast["eloWinnerProbabilities"]["home"] == pytest.approx(0.6772566444734303)
    assert forecast["ridgePointExpectations"]["home"] == pytest.approx(117.06131020680348)


# ------------------------------------------------ V2 effective clock (corr. 1)

def _release_effective(package: pathlib.Path) -> str:
    forecast = json.loads((package / "forecast.json").read_bytes())
    release = forecast["bindings"]["slateRelease"]
    return json.loads(pathlib.Path(release["path"]).read_bytes())["prospectiveEffectiveAtUtc"]


def test_a_v2_package_is_dated_by_its_own_release_not_by_the_handoff(session, world):
    """The release's clock is later than the pinned handoff's, and the package takes IT."""

    package, _ = _v2(world)
    assert fx.RELEASE_EFFECTIVE != fx.EFFECTIVE and _release_effective(package) == fx.RELEASE_EFFECTIVE
    assert gen1_production.consume(session, world["settings"], now=NOW)["imported"] == 1
    row = session.scalar(select(NbaGen1Forecast))
    assert gen1_common.iso(row.prospective_effective_at_utc) == fx.RELEASE_EFFECTIVE
    assert row.handoff_sha256 == world["handoff"]["sha256"]          # the pinned handoff is still checked
    assert row.slate_release_sha256 == json.loads(
        (package / "forecast.json").read_bytes())["bindings"]["slateRelease"]["sha256"]


def test_a_preserved_older_package_keeps_its_own_release_clock(session, world):
    """A newer discovery release never re-dates a package published under an older one."""

    older = fx.make_release(world["root"], world["handoff"], effective="2026-09-18T12:00:00+00:00")
    package = fx.make_package(world["root"], world["handoff"], schema="V2",
                              created="2026-10-20T22:10:00+00:00", release=older)
    newer = fx.make_release(world["root"], world["handoff"])  # today's release, a day later
    fx.write_v2_discovery(world["root"], world["handoff"], [(package, {
        "committedAtUtc": "2026-10-20T22:14:00+00:00", "currentlyEligible": True,
        "eligibilityValidUntilUtc": (NOW + timedelta(minutes=10)).isoformat()})], release=newer)
    assert gen1_production.consume(session, world["settings"], now=NOW)["imported"] == 1
    row = session.scalar(select(NbaGen1Forecast))
    assert gen1_common.iso(row.prospective_effective_at_utc) == "2026-09-18T12:00:00+00:00"
    assert row.slate_release_sha256 == older["sha256"] != newer["sha256"]


@pytest.mark.parametrize("mutate, problem", [
    (lambda f: f.update(prospectiveEffectiveAtUtc="2026-09-19T21:00:00+00:00"),
     "EFFECTIVE_TIME_DIFFERS_FROM_BOUND_SLATE_RELEASE"),
    (lambda f: f["bindings"].pop("slateRelease"), "SLATE_RELEASE_NOT_BOUND"),
    (lambda f: f["bindings"].update(productionHandoff={"sha256": "f" * 64, "bytes": 1, "path": "x"}),
     "PACKAGE_NOT_BOUND_TO_PINNED_HANDOFF"),
])
def test_a_v2_package_whose_clock_or_binding_drifts_is_refused(world, mutate, problem):
    package = fx.make_package(world["root"], world["handoff"], schema="V2",
                              created="2026-10-20T22:10:00+00:00", mutate=mutate)
    with pytest.raises(gen1_package.PackageRefused) as refused:
        gen1_package.verify_package(package, handoff_sha256=world["handoff"]["sha256"], effective_at=fx.EFFECTIVE)
    assert problem in refused.value.problems


def test_a_v1_package_still_compares_its_clock_with_the_handoff(world):
    package = fx.make_package(world["root"], world["handoff"],
                              mutate=lambda f: f.update(prospectiveEffectiveAtUtc=fx.RELEASE_EFFECTIVE))
    with pytest.raises(gen1_package.PackageRefused) as refused:
        gen1_package.verify_package(package, handoff_sha256=world["handoff"]["sha256"], effective_at=fx.EFFECTIVE)
    assert "EFFECTIVE_TIME_DIFFERS_FROM_HANDOFF" in refused.value.problems


# --------------------------------------------- source-confirmed finals lane --

def _admitted(session, world, *, tipoff=fx.TIPOFF):
    """One admitted AISP game for the fixture identity, via the ordinary package path."""

    package = fx.make_package(world["root"], world["handoff"], g=fx.game(tipoff=tipoff))
    fx.write_v1_discovery(world["root"], [package])
    gen1_production.consume(session, world["settings"], now=NOW)
    return session.scalar(select(NbaGen1Forecast))


def _lane(world, entries, **kw):
    published = fx.write_finals_lane(world["root"], world["handoff"], entries, **kw)
    return published, world["settings"].model_copy(update=published["settings"])


def test_the_finals_lane_is_a_no_op_until_its_pointer_exists(session, world):
    result = gen1_finals.ingest_exchange(session, world["settings"], now=NOW)
    assert result["status"] == "NO_FINALS_DISCOVERY_POINTER" and result["observations_written"] == 0


def test_a_source_confirmed_final_is_admitted_with_no_completion_clock(session, world):
    row = _admitted(session, world)
    published, configured = _lane(world, [{"game": fx.game(), "home": 121, "away": 117}])
    result = gen1_finals.ingest_exchange(session, configured, now=NOW + timedelta(hours=5))
    assert result["status"] == "OK" and result["observations_written"] == 1, result
    assert result["currently_source_confirmed"] == 1 and result["games_projected_final"] == 1
    final = session.scalar(select(NbaFinalObservation))
    assert final.lane == "EXCHANGE_SOURCE_CONFIRMED" and final.currently_source_confirmed is True
    assert (final.home_points, final.away_points) == (121, 117)
    assert final.completed_at_utc is None
    assert final.completion_clock_status == "NOT_RETAINED_BY_ADMITTED_SOURCE"
    assert final.final_record_sha256 == published["records"]["nba-espn:game:990000001"]["record"]["sha256"]
    assert gen1_common.iso(final.admitted_at_utc) == "2026-10-21T04:00:00+00:00"
    game = session.get(Game, row.canonical_game_id)
    assert game.status == GameStatus.FINAL and (game.home_score, game.away_score) == (121, 117)
    assert game.final_at is None                                   # never invented from a capture clock
    again = gen1_finals.ingest_exchange(session, configured, now=NOW + timedelta(hours=6))
    assert again["observations_written"] == 0 and again["observations_reused"] == 1


def test_a_correction_publishes_a_new_revision_and_keeps_the_old_one_as_evidence(session, world):
    row = _admitted(session, world)
    first, configured = _lane(world, [{"game": fx.game(), "home": 121, "away": 117}])
    gen1_finals.ingest_exchange(session, configured, now=NOW + timedelta(hours=5))
    _, corrected = _lane(world, [{"game": fx.game(), "home": 122, "away": 117}], previous=first,
                         captured="2026-10-21T05:20:00+00:00", admitted="2026-10-21T06:00:00+00:00")
    result = gen1_finals.ingest_exchange(session, corrected, now=NOW + timedelta(hours=8))
    assert result["observations_written"] == 1 and result["currently_source_confirmed"] == 1
    rows = {r.final_record_sha256: r for r in session.scalars(select(NbaFinalObservation))}
    assert len(rows) == 2
    superseded = [r for r in rows.values() if not r.currently_source_confirmed]
    assert len(superseded) == 1 and superseded[0].disposition == "SUPERSEDED_BY_CORRECTION"
    assert superseded[0].home_points == 121                        # the old bytes are kept, not rewritten
    game = session.get(Game, row.canonical_game_id)
    assert (game.home_score, game.away_score) == (122, 117)


def test_a_withdrawn_final_is_retained_as_ineligible_and_demands_reconciliation(session, world):
    row = _admitted(session, world)
    first, configured = _lane(world, [{"game": fx.game(), "home": 121, "away": 117}])
    gen1_finals.ingest_exchange(session, configured, now=NOW + timedelta(hours=5))
    # The source retracts it: the record stays listed, with membership withdrawn.
    _, withdrawn = _lane(world, [], previous=first, captured="2026-10-21T05:20:00+00:00",
                         games=[{"gameId": "nba-espn:game:990000001", "sourceGameId": 990000001,
                                 "disposition": "NOT_SOURCE_FINAL", "sourceStatus": "STATUS_SUSPENDED",
                                 "scheduledTipoffUtc": fx.TIPOFF.isoformat(), "reason": None,
                                 "currentFinal": None}])
    result = gen1_finals.ingest_exchange(session, withdrawn, now=NOW + timedelta(hours=8))
    assert result["currently_source_confirmed"] == 0 and result["observations_written"] == 0
    assert result["reconciliation_required"][0]["reason"] == "FINAL_WITHDRAWN_BY_SOURCE"
    kept = session.scalar(select(NbaFinalObservation))
    assert kept.currently_source_confirmed is False and kept.disposition == "WITHDRAWN_FROM_SOURCE_MEMBERSHIP"
    assert kept.reconciliation_required is True and kept.home_points == 121
    # AISP does not silently unsettle what it already recorded, and does not re-settle either.
    game = session.get(Game, row.canonical_game_id)
    assert (game.home_score, game.away_score) == (121, 117)


def test_a_final_whose_schedule_or_participants_moved_is_never_settled_automatically(session, world):
    row = _admitted(session, world)
    moved = fx.game(tipoff=fx.TIPOFF + timedelta(hours=2))
    _, configured = _lane(world, [{"game": moved, "home": 121, "away": 117}])
    result = gen1_finals.ingest_exchange(session, configured, now=NOW + timedelta(hours=8))
    assert result["observations_written"] == 0 and result["games_projected_final"] == 0
    assert result["reconciliation_required"][0]["reason"] == "TIPOFF_DIFFERS_FROM_ADMITTED_GAME"
    retained = session.scalar(select(NbaFinalObservation))
    assert retained.game_state_observation_id is None and retained.reconciliation_required is True
    assert session.get(Game, row.canonical_game_id).status == GameStatus.SCHEDULED


@pytest.mark.parametrize("mutate, problem", [
    (lambda r: r.update(completedAtUtc="2026-10-21T02:40:00+00:00"), "COMPLETION_CLOCK_CLAIMED"),
    (lambda r: r.update(completionClockBasis="ESTIMATED_FROM_LAST_PLAY"),
     "COMPLETION_CLOCK_BASIS_UNKNOWN:'ESTIMATED_FROM_LAST_PLAY'"),
    (lambda r: r.update(homeScore=117), "TIED_NBA_FINAL"),
    (lambda r: r.update(sourceStatus="STATUS_GAME_OVER"), "NOT_A_SOURCE_CONFIRMED_FINAL:'STATUS_GAME_OVER'"),
    (lambda r: r.update(admittedAtUtc="2026-10-21T01:00:00+00:00"),
     "CLOCK_ORDER_CAPTURE_VALIDATION_ADMISSION"),
    (lambda r: r.update(consumerAuthority="SETTLEMENT_AUTHORITY"), "RECORD_CLAIMS_CONSUMER_AUTHORITY"),
    (lambda r: r["game"].update(home_team_code="ZZZ"), "HOME_FRANCHISE_ID_DOES_NOT_NAME_ITS_CODE"),
])
def test_a_malformed_final_record_is_rejected_and_records_nothing(session, world, mutate, problem):
    _admitted(session, world)
    _, configured = _lane(world, [{"game": fx.game(), "home": 121, "away": 117, "mutate": mutate}])
    result = gen1_finals.ingest_exchange(session, configured, now=NOW + timedelta(hours=8))
    assert result["records_refused"] == 1 and result["observations_written"] == 0
    assert problem in result["refused_records"][0]["problems"]
    assert session.scalar(select(NbaFinalObservation)) is None


def test_a_final_for_a_game_aisp_does_not_hold_is_listed_never_guessed(session, world):
    _, configured = _lane(world, [{"game": fx.game(espn=990000777), "home": 121, "away": 117}])
    result = gen1_finals.ingest_exchange(session, configured, now=NOW + timedelta(hours=8))
    assert result["observations_written"] == 0
    assert result["unbound_games"][0]["state"] == "NO_AISP_GAME_FOR_THIS_ESPN_ID"
    assert session.scalar(select(NbaFinalObservation)) is None


def test_a_release_that_does_not_bind_the_lane_contract_refuses_the_whole_lane(session, world):
    other = fx.ref(fx.content_addressed(world["root"] / "other-contract", "contract.json",
                                        {**fx.FINALS_CONTRACT, "competition": "SOMETHING_ELSE"}))
    _, configured = _lane(world, [{"game": fx.game(), "home": 121, "away": 117}],
                          release=fx.make_release(world["root"], world["handoff"], finals_contract=other))
    result = gen1_finals.ingest_exchange(session, configured, now=NOW + timedelta(hours=8))
    assert result["status"] == "LANE_REFUSED" and "RELEASE_DOES_NOT_BIND_THIS_CONTRACT" in result["problems"]


def test_the_two_final_lanes_stay_separate_and_report_separately(session, world):
    _admitted(session, world)
    _, configured = _lane(world, [{"game": fx.game(), "home": 121, "away": 117}])
    both = gen1_finals.ingest(session, configured, now=NOW + timedelta(hours=8))
    assert both["production_lane"]["lane"] == "EXCHANGE_SOURCE_CONFIRMED"
    assert both["historical_lane"]["lane"] == "RETAINED_SOURCE_HISTORICAL"
    assert both["production_lane"]["observations_written"] == 1
    assert both["historical_lane"]["status"] == "NO_PINNED_SOURCE_OBJECT"
    assert both["status"] == "OK/NO_PINNED_SOURCE_OBJECT"


def test_a_source_confirmed_final_scores_every_channel(session, world):
    _admitted(session, world)
    _, configured = _lane(world, [{"game": fx.game(), "home": 118, "away": 109}])
    gen1_finals.ingest_exchange(session, configured, now=NOW + timedelta(hours=8))
    summary = gen1_evaluation.evaluate(session, now=NOW + timedelta(hours=9))
    assert summary["by_class"][EVIDENCE_PROSPECTIVE] == 3
    rows = {r.channel: r for r in session.scalars(select(NbaChannelEvaluation))}
    assert rows[CHANNEL_RIDGE].log_loss is None and rows[CHANNEL_ELO].predicted_home is None
    assert rows[CHANNEL_JOINT].crps_total is not None
    assert rows[CHANNEL_ELO].result_source == "NBA_GEN1_SOURCE_CONFIRMED_FINAL"


#: Model Studio's OWN finals-lane fixture, shipped with the completed delivery.
#: Reading it here is the "actual AISP-process reading" the contract asks for
#: before anyone claims consumer compatibility. Skipped when it is not present.
REAL_FINALS_LANE = pathlib.Path(
    r"C:\Users\james\asp-model-studio-source-nba-delivery\outputs\nba-finals-fixtures"
    r"\0300382f6d834473a3faae3933e414df\fixture-exchange\finals-discovery\current.json")


@pytest.mark.skipif(not REAL_FINALS_LANE.is_file(), reason="Model Studio's finals fixture is not on this machine")
def test_the_producers_own_finals_lane_verifies_and_admits(session, world):
    from app.nba import gen1_final_exchange

    verified = gen1_final_exchange.verify_lane(REAL_FINALS_LANE)
    assert verified["schema_version"] == "NBA_GEN1_FINAL_DISCOVERY_V1"
    assert [r.verified for r in verified["records"]] == [True, True]
    current = verified["current_records"]
    assert len(current) == 1 and current[0].codex_game_id == "nba-espn:game:99"
    assert (current[0].home_score, current[0].away_score) == (112, 109)
    assert current[0].doc["completedAtUtc"] is None
    assert current[0].doc["completionClockBasis"] == "NOT_RETAINED_BY_ADMITTED_SOURCE"
    # Admit the game exactly as a package would, then let the lane settle it.
    gen1_identity.admit_game(
        session, codex_game_id=current[0].codex_game_id, home_franchise_id=current[0].home_franchise_id,
        away_franchise_id=current[0].away_franchise_id, home_code=current[0].home_code,
        away_code=current[0].away_code, tipoff_utc=current[0].tipoff, season_end=2027)
    session.flush()
    configured = world["settings"].model_copy(update={"nba_finals_exchange_pointer": str(REAL_FINALS_LANE)})
    result = gen1_finals.ingest_exchange(session, configured, now=datetime(2026, 10, 21, 6, tzinfo=UTC))
    assert result["status"] == "OK" and result["observations_written"] == 1, result
    assert result["records_refused"] == 0 and result["retained_ineligible"] == 1
    rows = list(session.scalars(select(NbaFinalObservation)))
    assert {r.currently_source_confirmed for r in rows} == {True, False}
    settled = next(r for r in rows if r.currently_source_confirmed)
    assert (settled.home_points, settled.away_points) == (112, 109) and settled.completed_at_utc is None
    assert session.get(Game, settled.game_id).final_at is None


# ---------------------------------------------- the producer's OWN documents --

#: Model Studio's real recurring paper grant and the release that binds it,
#: shipped with the completed delivery. Reading these proves compatibility with
#: the producer's actual bytes rather than with a fixture of our own shape.
REAL_PAPER = pathlib.Path(r"C:\Users\james\asp-model-studio-source-nba-delivery\outputs\nba-paper-fixtures"
                          r"\05437214250c4efba616e8b6a2e464db")
REAL_GRANT = REAL_PAPER / "grants/sha256/6c30bcf2f761545ce3cc3916deae60424f0d7e8c2a908be24739529860377aa5/receipt.json"
REAL_GRANT_RELEASE = (REAL_PAPER
                      / "release/sha256/b1d1327cc14e3fb37e27d542e2e1ea2db052c218fec15e3193091a66dcee7950/receipt.json")
#: The real slate release staged for tonight, with its genuine effective clock.
REAL_RELEASE = pathlib.Path(
    r"C:\Users\james\asp-model-studio-source-nba-delivery\outputs\nba-gen1-delivery-completion-20260919-v1"
    r"\release-installable\releases\sha256"
    r"\9c5de22ce0cf9d366d7297202cccd4b18ba8738c2a8fccf1449cd8fdc17a4a0b\release.json")
PINNED_HANDOFF = "7113b71ff5819c89103ad19d5f9afb22c51dbc92980be12b56ce8826cb34ccaf"


@pytest.mark.skipif(not REAL_RELEASE.is_file(), reason="the producer's release is not on this machine")
def test_the_real_slate_release_carries_its_own_clock_and_no_paper_authority():
    release = json.loads(REAL_RELEASE.read_bytes())
    assert release["schema"] == gen1_common.SLATE_RELEASE_SCHEMA
    assert release["prospectiveEffectiveAtUtc"] == "2026-09-19T20:26:29.236061+00:00"
    # The SELECTED MODEL is still the September 18 identity: a new release does
    # not mean a new model, and the package clock is not the model clock.
    assert release["modelHandoff"]["sha256"] == PINNED_HANDOFF
    assert release["configurationIdentity"] == gen1_common.CONFIGURATION
    assert release["distributionConfigurationIdentity"] == gen1_common.DISTRIBUTION_CONFIGURATION
    assert release["drawCount"] == gen1_common.DRAW_COUNT
    assert release["paperEligibilityAuthorization"] is None       # optional lane, and it is absent


@pytest.mark.skipif(not REAL_GRANT.is_file(), reason="the producer's paper grant fixture is not on this machine")
def test_the_producers_own_recurring_paper_grant_is_accepted_but_not_current_yet(settings):
    from app.nba import gen1_paper

    release = json.loads(REAL_GRANT_RELEASE.read_bytes())
    configured = settings.model_copy(update={
        "nba_paper_policy_grant_path": str(REAL_GRANT),
        "nba_paper_policy_grant_sha256": gen1_common.sha256_bytes(REAL_GRANT.read_bytes())})
    inside = gen1_paper.policy_grant(configured, now=datetime(2026, 11, 1, tzinfo=UTC), release=release)
    assert inside["valid"] is True and inside["state"] == "GRANT_VALID"
    assert inside["scope"] == "ALL_SUPPORTED_2026_27_REGULAR_SEASON_FORECASTS"
    assert inside["valid_from"] == "2026-10-13T18:00:00+00:00"
    # September is before the approved interval opens: a September clock never
    # becomes fresh eligibility, and the release must still be the one binding it.
    assert gen1_paper.policy_grant(configured, now=datetime(2026, 9, 19, tzinfo=UTC),
                                   release=release)["state"] == "GRANT_NOT_CURRENT"
    assert gen1_paper.policy_grant(configured, now=datetime(2026, 11, 1, tzinfo=UTC))["state"] == \
        "GRANT_NOT_BOUND_TO_A_VERIFIED_RELEASE"

"""The CFB consumer end to end: import, eligibility, decision gates, finals, settlement.

Synthetic fixtures only: packages, manifests, evidence and raw captures are built
in a temp directory with the real schemas; nothing reads or writes the producer's
exchange.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, date, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.cfb import final_ingest, paper_decision, production_import
from app.cfb import production_discovery as pd
from app.models import (
    CfbDiscoveryManifest, CfbResearchForecast, Game, GameStatus, Market, MarketOutcome, MarketType,
    OddsSnapshot, OfficialAspPosition, PositionSettlement, PositionSettlementAdjustment, ShadowExperiment,
    ShadowExperimentDecision, Sportsbook,
)
from tests.test_cfb_production_discovery import KICK, NOW, _ref, _sha, _write_addressed
from tests.test_cfb_production_discovery import _package as _thin_package


def _package(tmp, game_id: int, home_id: int, away_id: int):
    """The verifier fixture's package, completed to the importer's full contract."""

    root, entry = _thin_package(tmp, game_id, home_id, away_id)
    forecast = json.loads((root / "forecast.json").read_text(encoding="utf-8"))
    joint_home = forecast["baseline"]["jointHomeWinProbability"]
    forecast.update({
        "schemaVersion": "CFB_GEN1_PROSPECTIVE_RESEARCH_FORECAST_V1", "researchOnly": True,
        "aispAuthority": False, "wageringAuthority": False, "status": "STAGED_PROSPECTIVE_BASELINE",
        "modelBindings": {"selectedModel": {"physicalSha256": "m" * 64}},
    })
    forecast["baseline"].update({
        "configuration": pd.CONFIGURATION, "expectedAwayPoints": 24.0, "expectedHomePoints": 27.0,
        "expectedTotal": 51.0, "expectedHomeMargin": 3.0,
        "ridgeHomeWinProbability": 0.58, "ridgeAwayWinProbability": 0.42,
        "eloHomeWinProbability": 0.55, "eloAwayWinProbability": 0.45,
        "jointAwayWinProbability": 1 - joint_home, "tieProbability": 0.0,
        "intervals": [], "illustrativeTotals": []})
    forecast["game"].update({"home_team": f"Home {game_id}", "away_team": f"Away {game_id}"})
    raw = json.dumps(forecast).encode()
    staging = {name: (root / name).read_bytes() for name in ("baseline-joint-draws.parquet", "baseline-pmfs.parquet")}
    staging["forecast.json"] = raw
    files = [{"path": name, "physicalSha256": _sha(body), "bytes": len(body)} for name, body in staging.items()]
    index = json.loads((root / "index.json").read_bytes())
    index["files"] = files
    index_raw = json.dumps(index, sort_keys=True).encode()
    new_root = root.parent / _sha(index_raw)
    new_root.mkdir(parents=True)
    for name, body in staging.items():
        (new_root / name).write_bytes(body)
    (new_root / "index.json").write_bytes(index_raw)
    entry.update({"packageAddress": new_root.name, "packageDirectory": str(new_root),
                  "index": _ref(new_root / "index.json")})
    receipt = json.dumps({k: entry[k] for k in ("gameId", "forecastId", "packageAddress", "revisionKey",
                                                "committedAtUtc")}).encode()
    entry["publicationReceipt"] = _ref(_write_addressed(tmp / "receipts", "receipt.json", receipt))
    return new_root, entry


# -- fixtures -----------------------------------------------------------------
@pytest.fixture
def world(tmp_path, session, monkeypatch):
    release = _write_addressed(tmp_path / "releases", "release.json", b'{"release": 1}')
    inputs = _write_addressed(tmp_path / "inputs", "input.json", b'{"schedule": null}')
    _, first = _package(tmp_path, 401, home_id=10, away_id=20)
    _, second = _package(tmp_path, 402, home_id=30, away_id=40)
    raw_schedule = tmp_path / "games.json"
    raw_schedule.write_text(json.dumps([
        {"id": 401, "homeId": 10, "awayId": 20}, {"id": 402, "homeId": 30, "awayId": 40}]))

    def publish(entries, *, predecessor=None, lease_until=None):
        entries = [dict(e) for e in entries]
        if lease_until is not None:
            for e in entries:
                e["eligibilityValidUntilUtc"] = lease_until.isoformat()
        manifest = json.dumps({"schemaVersion": pd.MANIFEST_SCHEMA, "createdAtUtc": NOW.isoformat(),
                               "release": _ref(release), "inputVersion": _ref(inputs),
                               "predecessor": predecessor, "packages": entries,
                               "games": [{"gameId": e["gameId"], "disposition": "PUBLISHED_NEW_PROSPECTIVE"}
                                         for e in entries]}).encode()
        path = _write_addressed(tmp_path / "versions", "index.json", manifest)
        pointer = tmp_path / "current.json"
        pointer.write_text(json.dumps({"schemaVersion": pd.POINTER_SCHEMA, "manifest": _ref(path)}))
        return _ref(path)

    anchor = {"sha": None}

    def status(_settings):
        return pd.HandoffVerdict(pd.ACTIVE, "TEST", "r", {"bound_discovery_sha256": anchor["sha"],
                                                           "handoff_sha256": "h" * 64})

    monkeypatch.setattr(pd, "production_status", status)
    settings = SimpleNamespace(cfb_discovery_pointer_path=str(tmp_path / "current.json"),
                               cfb_independent_schedule_path=str(raw_schedule),
                               cfb_production_consumer_enabled=True, cfb_paper_decisions_enabled=True,
                               cfb_final_evidence_pointer_path=str(tmp_path / "finals-pointer.json"))
    return SimpleNamespace(tmp=tmp_path, entries=[first, second], publish=publish, anchor=anchor,
                           settings=settings, session=session)


def _consume(world, now=NOW):
    result = production_import.consume_discovery(world.session, world.settings, now=now)
    world.session.flush()
    return result


# -- import -------------------------------------------------------------------
def test_import_is_idempotent_and_keeps_the_designated_publication_clock(world):
    ref = world.publish(world.entries)
    world.anchor["sha"] = ref["sha256"]
    first = _consume(world)
    assert first["status"] == "MANIFEST_RECORDED" and first["imported"] == 2 and first["games_admitted"] == 2
    second = _consume(world)
    assert second["status"] == "MANIFEST_ALREADY_RECORDED" and second["imported"] == 0
    rows = list(world.session.scalars(select(CfbResearchForecast)))
    assert len(rows) == 2
    for row in rows:
        entry = next(e for e in world.entries if e["forecastId"] == row.forecast_id)
        assert row.publication_clock_source == "DISCOVERY_RECEIPT_COMMITTED_AT"
        assert row.exchange_published_utc.replace(tzinfo=UTC).isoformat() == entry["committedAtUtc"]
        assert row.canonical_game_id is not None and row.origin == "PRODUCTION_DISCOVERY"
    assert world.session.scalar(select(func.count()).select_from(Game)) == 2


def test_a_successor_manifest_is_accepted_only_through_its_hash_bound_predecessor(world):
    anchor = world.publish(world.entries)
    world.anchor["sha"] = anchor["sha256"]
    successor = world.publish(world.entries, predecessor=anchor)
    result = _consume(world)
    assert result["status"] == "MANIFEST_RECORDED"
    assert [m["manifest_sha256"] for m in result["manifests_recorded"]] == [anchor["sha256"], successor["sha256"]]
    assert result["imported"] == 2   # the successor re-lists the same packages; nothing is imported twice

    world.anchor["sha"] = "0" * 64       # a pin that names some other discovery
    world.publish(world.entries, predecessor=None, lease_until=NOW + timedelta(minutes=5))
    refused = _consume(world)
    assert refused["status"].startswith("MANIFEST_NOT_IN_PINNED_LINEAGE")


def test_a_reversed_orientation_is_refused_and_imports_nothing(world):
    (world.tmp / "games.json").write_text(json.dumps([
        {"id": 401, "homeId": 20, "awayId": 10}, {"id": 402, "homeId": 30, "awayId": 40}]))
    world.anchor["sha"] = world.publish(world.entries)["sha256"]
    result = _consume(world)
    assert result["imported"] == 1 and result["rejected"] == 1
    assert "ORIENTATION_MISMATCH" in result["rejections"][0]["problems"]
    assert world.session.scalar(select(CfbResearchForecast).where(
        CfbResearchForecast.source_game_id == "401")) is None


def test_eligibility_expires_with_its_lease_and_at_kickoff_but_history_stays(world):
    world.anchor["sha"] = world.publish(world.entries)["sha256"]
    _consume(world)
    row = world.session.scalar(select(CfbResearchForecast).where(CfbResearchForecast.source_game_id == "401"))
    assert production_import.present_eligibility(world.session, row, now=NOW)["state"] == "CURRENT"
    later = NOW + timedelta(minutes=16)
    assert production_import.present_eligibility(world.session, row, now=later)["state"] == "EXPIRED_LEASE"
    assert production_import.present_eligibility(world.session, row, now=KICK)["state"] == "GAME_STARTED"
    assert world.session.scalar(select(func.count()).select_from(CfbResearchForecast)) == 2


# -- the paper checkpoint -------------------------------------------------------
@pytest.fixture
def era(session):
    experiment = ShadowExperiment(
        name="CFB PAPER TEST", version=9001, started_at=NOW - timedelta(days=3), start_date=date(2026, 9, 15),
        starting_bankroll=1000, sport="CFB", recommendation_policy_version="AISP_CFB_PAPER_POLICY_V1",
        staking_policy_version="AISP_QUARTER_KELLY_CFB_V1", decision_policy="AISP_CFB_PAPER_POLICY_V1",
        controlling_engine="CFB_GEN1_JOINT_BASELINE", supported_markets=["MONEYLINE"], kelly_fraction=0.25,
        unit_percentage=0.01, max_position_percentage=0.005, max_same_game_percentage=0.01,
        max_daily_percentage=0.02, code_commit="test", code_identity="test", is_active=True)
    session.add(experiment)
    session.flush()
    return experiment


def _in_window(world, era, monkeypatch, *, eligible=True, lease_minutes=10):
    """Lease current at T-20m, the producer's permission optionally present."""

    decision_time = KICK - timedelta(minutes=20)
    world.anchor["sha"] = world.publish(
        world.entries, lease_until=decision_time + timedelta(minutes=lease_minutes))["sha256"]
    _consume(world, now=decision_time)
    monkeypatch.setattr(paper_decision, "_supplement_published", lambda *_a: decision_time - timedelta(hours=2))
    monkeypatch.setattr(paper_decision, "eligibility_status", lambda *a, **k: (
        {"eligible": True, "state": "ELIGIBLE", "record_id": "rec", "contract": "c", "experiment_key": "k"}
        if eligible else {"eligible": False, "state": "AWAITING_PRODUCER_ELIGIBILITY", "detail": "none"}))
    game_id = production_import.current_forecast_for_game(world.session, "401").canonical_game_id
    return decision_time, world.session.get(Game, game_id)


def _moneyline(session, game, *, home, away, at):
    from app.models import GameTeam
    book = session.scalar(select(Sportsbook).where(Sportsbook.key == "sgo:draftkings"))
    if book is None:
        book = Sportsbook(key="sgo:draftkings", name="DraftKings")
        session.add(book)
        session.flush()
    market = Market(game_id=game.id, market_type=MarketType.MONEYLINE, period="full_game")
    session.add(market)
    session.flush()
    sides = {side.is_home: side.team_id for side in session.scalars(select(GameTeam).where(GameTeam.game_id == game.id))}
    out = {}
    for key, american, team in (("HOME", home, sides[True]), ("AWAY", away, sides[False])):
        outcome = MarketOutcome(market_id=market.id, outcome_key=key, label=key, participant_team_id=team)
        session.add(outcome)
        session.flush()
        decimal = 1 + (american / 100 if american > 0 else 100 / -american)
        session.add(OddsSnapshot(market_outcome_id=outcome.id, sportsbook_id=book.id, observed_at=at,
                                 available_as_of=at, american_odds=american, decimal_odds=decimal,
                                 source="SPORTSGAMEODDS"))
        out[key] = outcome
    session.flush()
    return out


def test_the_checkpoint_blocks_without_producer_eligibility_and_never_duplicates(world, era, monkeypatch):
    moment, _ = _in_window(world, era, monkeypatch, eligible=False)
    first = paper_decision.evaluate(world.session, world.settings, now=moment)
    assert {d["reason"] for d in first["decisions"]} == {"AWAITING_PRODUCER_ELIGIBILITY"}
    second = paper_decision.evaluate(world.session, world.settings, now=moment + timedelta(minutes=5))
    assert {d["persisted"] for d in second["decisions"]} == {"REUSED_EXISTING_DECISION"}
    # One decision per game in the window (both fixture games share a kickoff), never a second.
    assert world.session.scalar(select(func.count()).select_from(ShadowExperimentDecision)) == 2


def test_a_supplement_published_after_t_minus_30_is_refused(world, era, monkeypatch):
    moment, _ = _in_window(world, era, monkeypatch)
    monkeypatch.setattr(paper_decision, "_supplement_published", lambda *_a: KICK - timedelta(minutes=25))
    result = paper_decision.evaluate(world.session, world.settings, now=moment)
    assert {d["reason"] for d in result["decisions"]} == {"ELIGIBILITY_RECORD_INVALID"}


def test_nothing_is_positioned_after_the_grant_expires(world, era, monkeypatch):
    moment, _ = _in_window(world, era, monkeypatch)
    monkeypatch.setattr(paper_decision, "_grant", lambda _s: {"sha256": "g", "valid_until": moment - timedelta(minutes=1)})
    result = paper_decision.evaluate(world.session, world.settings, now=moment)
    assert {d["reason"] for d in result["decisions"]} == {"POLICY_GRANT_EXPIRED"}


def test_an_expired_lease_blocks_before_any_price_is_read(world, era, monkeypatch):
    moment, _ = _in_window(world, era, monkeypatch, lease_minutes=3)
    result = paper_decision.evaluate(world.session, world.settings, now=moment + timedelta(minutes=4))
    assert {d["reason"] for d in result["decisions"]} == {"DISCOVERY_NOT_CURRENT"}


def test_no_price_is_a_recorded_no_position_result(world, era, monkeypatch):
    moment, _ = _in_window(world, era, monkeypatch)
    result = paper_decision.evaluate(world.session, world.settings, now=moment)
    assert {d["reason"] for d in result["decisions"]} == {"NO_TWO_SIDED_QUOTE"}
    assert world.session.scalar(select(func.count()).select_from(OfficialAspPosition)) == 0


def test_a_stale_older_leg_declines(world, era, monkeypatch):
    moment, game = _in_window(world, era, monkeypatch)
    _moneyline(world.session, game, home=150, away=-170, at=moment - timedelta(minutes=241))
    result = paper_decision.evaluate(world.session, world.settings, now=moment)
    assert next(d for d in result["decisions"] if d["source_game_id"] == "401")["reason"] == "QUOTE_TOO_STALE"


def _favourite_side(world):
    row = production_import.current_forecast_for_game(world.session, "401")
    return ("HOME", row.joint_home_win_probability) if row.joint_home_win_probability >= 0.5 else (
        "AWAY", row.joint_away_win_probability)


def test_a_qualifying_price_records_one_capped_position_and_is_idempotent(world, era, monkeypatch):
    moment, game = _in_window(world, era, monkeypatch)
    side, _p = _favourite_side(world)
    # A generous price on the model's side, a short one on the other.
    home, away = (200, -260) if side == "HOME" else (-260, 200)
    _moneyline(world.session, game, home=home, away=away, at=moment - timedelta(minutes=10))
    result = paper_decision.evaluate(world.session, world.settings, now=moment)
    decision = next(d for d in result["decisions"] if d["source_game_id"] == "401")
    assert decision["outcome"] == "QUALIFIED" and decision["position_id"]
    position = world.session.get(OfficialAspPosition, uuid.UUID(decision["position_id"]))
    assert 0 < float(position.stake) <= 5.00
    again = paper_decision.evaluate(world.session, world.settings, now=moment + timedelta(minutes=5))
    assert next(d for d in again["decisions"] if d["source_game_id"] == "401")["persisted"] == "REUSED_EXISTING_DECISION"
    assert world.session.scalar(select(func.count()).select_from(OfficialAspPosition)) == 1


def test_the_activation_control_holds_positions_but_records_the_evaluation(world, era, monkeypatch):
    moment, game = _in_window(world, era, monkeypatch)
    world.settings.cfb_paper_decisions_enabled = False
    side, _ = _favourite_side(world)
    home, away = (200, -260) if side == "HOME" else (-260, 200)
    _moneyline(world.session, game, home=home, away=away, at=moment - timedelta(minutes=10))
    result = paper_decision.evaluate(world.session, world.settings, now=moment)
    assert next(d for d in result["decisions"] if d["source_game_id"] == "401")["reason"] == "ACTIVATION_CONTROL_OFF"
    assert world.session.scalar(select(func.count()).select_from(OfficialAspPosition)) == 0


# -- finals, settlement, correction --------------------------------------------
def _evidence(world, *, home_points, away_points, captured, raw_home=None, home_id=10, tag="a"):
    raw_rows = [{"id": 401, "homeId": home_id, "awayId": 20, "homePoints": raw_home if raw_home is not None else home_points,
                 "awayPoints": away_points, "completed": True, "homeLineScores": [7, 7, 7, 7],
                 "awayLineScores": [0, 3, 0, 0], "startDate": KICK.isoformat()}]
    raw_path = _write_addressed(world.tmp / f"raw-{tag}", "response.json", json.dumps(raw_rows).encode())
    export = {"schemaVersion": final_ingest.FINALS_SCHEMA, "source": _ref(raw_path), "rows": [{
        "sourceGameId": 401, "homeTeamId": home_id, "awayTeamId": 20, "homeFinalPoints": home_points,
        "awayFinalPoints": away_points, "sourceCompleted": True, "sourceStatusField": "completed",
        "sourceRecordLocator": "$[0]", "capturedAtUtc": captured.isoformat(),
        "scheduledKickoffUtc": KICK.isoformat(), "sourceResponseSha256": _ref(raw_path)["sha256"],
        "sourceCompletionTimestampStatus": "ABSENT_FROM_RETAINED_SOURCE_SCHEMA",
        "homeTeam": "H", "awayTeam": "A"}]}
    export_path = _write_addressed(world.tmp / f"export-{tag}", "FINAL_RESULT_EVIDENCE.json", json.dumps(export).encode())
    index = {"schemaVersion": final_ingest.INDEX_SCHEMA, "artifacts": {"finalResultEvidenceJson": _ref(export_path)}}
    index_path = _write_addressed(world.tmp / f"index-{tag}", "EVIDENCE_INDEX.json", json.dumps(index).encode())
    (world.tmp / "finals-pointer.json").write_text(json.dumps(
        {"schemaVersion": final_ingest.POINTER_SCHEMA, "index": _ref(index_path)}))


def test_a_row_that_disagrees_with_its_raw_bytes_is_refused(world):
    world.anchor["sha"] = world.publish(world.entries)["sha256"]
    _consume(world)
    _evidence(world, home_points=28, away_points=3, captured=KICK + timedelta(hours=4), raw_home=21)
    result = final_ingest.ingest_retained_finals(world.session, world.settings, now=KICK + timedelta(hours=5))
    assert result["skipped"][0]["skipped"] == "RAW_RECORD_DISAGREES_OR_UNAVAILABLE"
    assert result["observations_written"] == 0


def test_a_final_with_the_wrong_home_team_is_refused(world):
    world.anchor["sha"] = world.publish(world.entries)["sha256"]
    _consume(world)
    _evidence(world, home_points=28, away_points=3, captured=KICK + timedelta(hours=4), home_id=99)
    result = final_ingest.ingest_retained_finals(world.session, world.settings, now=KICK + timedelta(hours=5))
    assert result["skipped"][0]["skipped"] == "HOME_AWAY_IDENTITY_MISMATCH"


def test_settlement_then_a_correction_keeps_the_original_and_adds_an_adjustment(world, era, monkeypatch):
    from app.services.shadow_experiment_service import ShadowExperimentService

    moment, game = _in_window(world, era, monkeypatch)
    side, _ = _favourite_side(world)
    home, away = (200, -260) if side == "HOME" else (-260, 200)
    _moneyline(world.session, game, home=home, away=away, at=moment - timedelta(minutes=10))
    decision = next(d for d in paper_decision.evaluate(world.session, world.settings, now=moment)["decisions"]
                    if d["source_game_id"] == "401")
    position = world.session.get(OfficialAspPosition, uuid.UUID(decision["position_id"]))

    winner_home = side == "HOME"
    _evidence(world, home_points=28 if winner_home else 3, away_points=3 if winner_home else 28,
              captured=KICK + timedelta(hours=4), tag="first")
    first = final_ingest.ingest_retained_finals(world.session, world.settings, now=KICK + timedelta(hours=5))
    assert first["observations_written"] == 1 and world.session.get(Game, game.id).status == GameStatus.FINAL
    again = final_ingest.ingest_retained_finals(world.session, world.settings, now=KICK + timedelta(hours=5))
    assert again["observations_written"] == 0 and again["observations_reused"] == 1

    service = ShadowExperimentService(world.session, SimpleNamespace())
    service.settle(now=KICK + timedelta(hours=5))
    service.settle(now=KICK + timedelta(hours=5, minutes=30))   # idempotent
    settlement = world.session.scalar(select(PositionSettlement).where(PositionSettlement.position_id == position.id))
    assert settlement.result.value.upper() == "WIN"
    assert world.session.scalar(select(func.count()).select_from(PositionSettlement)) == 1
    bankroll_after_win = service.current_bankroll(era)

    # A later capture reverses the result.
    _evidence(world, home_points=3 if winner_home else 28, away_points=28 if winner_home else 3,
              captured=KICK + timedelta(hours=9), tag="second")
    corrected = final_ingest.ingest_retained_finals(world.session, world.settings, now=KICK + timedelta(hours=10))
    assert corrected["corrections"] and corrected["corrections"][0]["corrected"] == "LOSS"
    original = world.session.get(PositionSettlement, settlement.id)
    assert original.result.value.upper() == "WIN"              # never rewritten
    adjustment = world.session.scalar(select(PositionSettlementAdjustment))
    assert adjustment.previous_result == "WIN" and adjustment.corrected_result == "LOSS"
    assert float(adjustment.profit_loss_delta) == pytest.approx(-(float(original.profit_loss) + float(position.stake)))
    assert service.current_bankroll(era) == pytest.approx(bankroll_after_win + float(adjustment.profit_loss_delta))
    rerun = final_ingest.ingest_retained_finals(world.session, world.settings, now=KICK + timedelta(hours=11))
    assert rerun["corrections"] == []
    assert world.session.scalar(select(func.count()).select_from(CfbDiscoveryManifest)) >= 1

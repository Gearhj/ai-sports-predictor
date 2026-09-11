"""V18 integration and price-coverage accounting.

The properties under test are the ones that would silently produce a confident
wrong answer:

  * the funnel charges every game to exactly ONE reason, so the reasons sum to
    the slate rather than to some larger number;
  * the threshold ladder is inert -- it records what other minimums WOULD have
    picked and cannot create a position or move the official 2%;
  * an accounting-only package never reaches the forecast importer;
  * a V18 forecast's evidence cutoff and its publication time stay separate.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.jobs.v18_exchange_consumer import (
    ACCOUNTING_ONLY, EXECUTION_CLAIMED, NOT_A_PACKAGE, NOT_IMPORTABLE,
    PACKAGE_SCHEMA, UNSUPPORTED_SCHEMA, classify, discover_packages, verify_children,
)
from app.models import (
    Game, GameStatus, GameTeam, League, OfficialAspPosition, Season, ShadowThresholdSelection,
    Sport, Team,
)
from app.services.price_coverage_funnel import (
    PriceCoverageFunnel, REASON_NO_FORECAST, REASON_NO_MAPPING, STAGES, STAGE_SCHEDULED,
)
from app.services.shadow_threshold_ladder import (
    OFFICIAL_RUNG, RUNGS, ShadowThresholdLadder, policy_name,
)


# ==========================================================================
# The exchange package gate
# ==========================================================================

def write_package(root: Path, address: str, manifest: dict) -> Path:
    directory = root / "sha256" / address
    directory.mkdir(parents=True)
    (directory / "exchange-package.json").write_text(json.dumps(manifest), encoding="utf-8")
    return directory


BASE = {
    "schemaVersion": PACKAGE_SCHEMA, "importable": True, "forecastCount": 2,
    "gameCount": 5, "status": "RESEARCH_SHADOW", "researchShadowOnly": True,
    "promotionStatus": "NOT_PROMOTED", "wageringAuthority": False,
    "executionAllowedInAISP": False, "files": [],
}


def test_an_accounting_only_package_is_never_offered_to_the_importer(tmp_path):
    """The 2026-09-11 package's exact shape: real, honest, and not a forecast."""

    directory = write_package(tmp_path, "a" * 64, {
        **BASE, "importable": False, "forecastCount": 0,
        "status": ACCOUNTING_ONLY, "gameCount": 15,
    })
    kind, manifest, reason = classify(directory)
    assert kind == "ACCOUNTING"
    assert reason is None
    assert manifest["forecastCount"] == 0


def test_zero_forecasts_is_accounting_even_without_the_status_string(tmp_path):
    directory = write_package(tmp_path, "b" * 64, {**BASE, "forecastCount": 0})
    assert classify(directory)[0] == "ACCOUNTING"


def test_a_package_claiming_execution_or_authority_is_rejected(tmp_path):
    for field, value in (
        ("executionAllowedInAISP", True),
        ("wageringAuthority", True),
        ("promotionStatus", "PROMOTED"),
        ("researchShadowOnly", False),
    ):
        directory = write_package(tmp_path, f"{field[:3]}".ljust(64, "c"), {**BASE, field: value})
        kind, _, reason = classify(directory)
        assert kind is None, f"{field}={value} must be refused"
        assert reason == EXECUTION_CLAIMED


def test_a_foreign_schema_is_refused(tmp_path):
    directory = write_package(tmp_path, "d" * 64, {**BASE, "schemaVersion": "SOMETHING_ELSE"})
    assert classify(directory)[2] == UNSUPPORTED_SCHEMA


def test_a_directory_without_a_manifest_is_not_a_package(tmp_path):
    directory = tmp_path / "sha256" / ("e" * 64)
    directory.mkdir(parents=True)
    assert classify(directory)[2] == NOT_A_PACKAGE


def test_a_legitimate_package_is_importable(tmp_path):
    directory = write_package(tmp_path, "f" * 64, BASE)
    kind, _, reason = classify(directory)
    assert kind == "IMPORTABLE" and reason is None


def test_a_child_whose_bytes_disagree_with_the_manifest_is_refused(tmp_path):
    import hashlib

    directory = tmp_path / "sha256" / ("1" * 64)
    directory.mkdir(parents=True)
    payload = b'{"hello": "world"}'
    (directory / "child.json").write_bytes(payload)
    manifest = {
        **BASE,
        "files": [{
            "relativePath": "child.json",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload) + 1,   # off by one
        }],
    }
    assert verify_children(directory, manifest) is not None

    manifest["files"][0]["bytes"] = len(payload)
    assert verify_children(directory, manifest) is None

    manifest["files"][0]["sha256"] = "0" * 64
    assert verify_children(directory, manifest) is not None


def test_discovery_only_accepts_content_addressed_directories(tmp_path):
    write_package(tmp_path, "2" * 64, BASE)
    (tmp_path / "sha256" / "not-an-address").mkdir(parents=True)
    (tmp_path / "sha256" / "loose-file.json").write_text("{}", encoding="utf-8")
    found = discover_packages(tmp_path)
    assert [path.name for path in found] == ["2" * 64]


# ==========================================================================
# The coverage funnel
# ==========================================================================

@pytest.fixture
def slate(session: Session):
    """Five MLB games on one day, in assorted states of coverage."""

    sport = Sport(code="BASEBALL", name="Baseball")
    session.add(sport)
    session.flush()
    league = League(sport_id=sport.id, code="MLB", name="MLB")
    session.add(league)
    session.flush()
    day = date(2026, 9, 11)
    season = Season(league_id=league.id, name="2026",
                    starts_at=datetime(2026, 3, 1, tzinfo=UTC),
                    ends_at=datetime(2026, 11, 1, tzinfo=UTC))
    session.add(season)
    session.flush()
    games = []
    for index in range(5):
        home = Team(league_id=league.id, name=f"Home {index}", abbreviation=f"H{index}")
        away = Team(league_id=league.id, name=f"Away {index}", abbreviation=f"A{index}")
        session.add_all([home, away])
        session.flush()
        game = Game(
            league_id=league.id, season_id=season.id,
            starts_at=datetime(2026, 9, 11, 23, 0, tzinfo=UTC) + timedelta(minutes=index * 5),
            status=GameStatus.SCHEDULED, source_mode="LIVE",
        )
        session.add(game)
        session.flush()
        session.add_all([
            GameTeam(game_id=game.id, team_id=home.id, is_home=True),
            GameTeam(game_id=game.id, team_id=away.id, is_home=False),
        ])
        games.append(game)
    session.flush()
    return {"session": session, "league": league, "games": games, "date": day}


def test_every_game_is_charged_to_exactly_one_reason(slate, settings):
    """The property that makes a funnel a diagnosis instead of a pile."""

    funnel = PriceCoverageFunnel(slate["session"], settings)
    report = funnel.build(event_date=slate["date"], league_code="MLB")

    scheduled = report["stages"][0]["games"]
    positioned = report["stages"][-1]["games"]
    assert scheduled == 5
    # Reasons sum to exactly the games that did not reach the end. A game
    # charged twice, or not at all, breaks this.
    assert report["reasons_sum"] + positioned == scheduled
    assert len(report["games"]) == scheduled
    assert len({row["game_id"] for row in report["games"]}) == scheduled


def test_a_game_with_no_mapping_is_charged_to_the_mapping_and_nothing_else(slate, settings):
    report = PriceCoverageFunnel(slate["session"], settings).build(
        event_date=slate["date"], league_code="MLB")
    # No external id mappings exist in this fixture, so every game stops at the
    # first gate -- and carries exactly that reason, not the four downstream
    # ones it would also have failed.
    reasons = {row["reason"] for row in report["games"]}
    assert reasons == {REASON_NO_MAPPING}
    assert all(row["reached"] == STAGE_SCHEDULED for row in report["games"])


def test_the_stage_counts_are_monotonically_non_increasing(slate, settings):
    report = PriceCoverageFunnel(slate["session"], settings).build(
        event_date=slate["date"], league_code="MLB")
    counts = [stage["games"] for stage in report["stages"]]
    assert counts == sorted(counts, reverse=True)
    assert [stage["stage"] for stage in report["stages"]] == list(STAGES)


def test_the_funnel_reports_the_active_policy_minimum_and_changes_nothing(slate, settings):
    report = PriceCoverageFunnel(slate["session"], settings).build(
        event_date=slate["date"], league_code="MLB")
    # Read from the deployment's own setting, not a constant in the module.
    assert report["minimum_edge_percentage"] == pytest.approx(
        float(settings.shadow_qualified_edge) * 100)
    assert report["provider_calls"] == 0
    assert report["positions_created"] == 0


def test_every_reason_carries_an_explanation(slate, settings):
    report = PriceCoverageFunnel(slate["session"], settings).build(
        event_date=slate["date"], league_code="MLB")
    for row in report["games"]:
        if row["reason"] is not None:
            assert row["explanation"], f"{row['reason']} has no explanation"


# ==========================================================================
# The shadow threshold ladder
# ==========================================================================

def test_the_ladder_registers_the_six_required_rungs():
    assert RUNGS == (1.00, 1.25, 1.50, 1.75, 2.00, 2.50)
    assert OFFICIAL_RUNG == 2.00
    assert policy_name(1.25) == "AISP_SHADOW_THRESHOLD_1_25"


def test_the_ladder_reads_the_official_minimum_and_never_writes_it(slate, settings):
    ladder = ShadowThresholdLadder(slate["session"], settings)
    before = float(settings.shadow_qualified_edge)
    report = ladder.run(event_date=slate["date"], league_code="MLB")
    assert ladder.official_minimum() == pytest.approx(before * 100)
    assert float(settings.shadow_qualified_edge) == before
    assert report["official_threshold_changed"] is False
    assert report["official_minimum_percentage"] == pytest.approx(before * 100)


def test_no_rung_creates_a_position_or_a_decision(slate, settings):
    session = slate["session"]
    positions_before = session.scalar(select(func.count()).select_from(OfficialAspPosition))
    report = ShadowThresholdLadder(session, settings).run(
        event_date=slate["date"], league_code="MLB")
    assert report["positions_created"] == 0
    assert report["official_decisions_created"] == 0
    assert report["models_executed"] == 0
    assert report["provider_calls"] == 0
    assert report["funded_experiment_modified"] is False
    assert session.scalar(select(func.count()).select_from(OfficialAspPosition)) == positions_before
    for rung in report["rungs"]:
        assert rung["creates_positions"] is False
        assert rung["wagering_authority"] is False


def test_exactly_one_rung_is_marked_official(slate, settings):
    report = ShadowThresholdLadder(slate["session"], settings).run(
        event_date=slate["date"], league_code="MLB")
    official = [rung for rung in report["rungs"] if rung["is_official_threshold"]]
    assert len(official) == 1
    assert official[0]["rung_percentage"] == pytest.approx(OFFICIAL_RUNG)


def test_a_lower_rung_can_never_select_fewer_games_than_a_higher_one(slate, settings):
    """Monotonicity: a threshold that admits more must not admit fewer."""

    report = ShadowThresholdLadder(slate["session"], settings).run(
        event_date=slate["date"], league_code="MLB")
    counts = [rung["would_select"] for rung in report["rungs"]]
    assert counts == sorted(counts, reverse=True)


def test_the_ladder_is_idempotent(slate, settings):
    session = slate["session"]
    ladder = ShadowThresholdLadder(session, settings)
    ladder.run(event_date=slate["date"], league_code="MLB")
    first = session.scalar(select(func.count()).select_from(ShadowThresholdSelection))
    second = ladder.run(event_date=slate["date"], league_code="MLB")
    assert sum(rung["recorded"] for rung in second["rungs"]) == 0
    assert session.scalar(select(func.count()).select_from(ShadowThresholdSelection)) == first


def test_the_summary_names_the_review_checkpoint_and_claims_nothing(slate, settings):
    summary = ShadowThresholdLadder(slate["session"], settings).summary(league_code="MLB")
    assert summary["review_checkpoint_settled_positions"] == 50
    assert "hypothetical" in summary["note"].lower()
    assert all(rung["creates_positions"] is False for rung in summary["rungs"])

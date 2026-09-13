"""Reading the published September 13 formal assessment.

These tests run against the real delivered package, because the properties that
matter are properties of THOSE bytes: that they hash to what the handoff says,
that the five arms join to the exact forecasts AISP holds, and that the numbers
reproduce the difference table the producer published in RESULTS.md.

The failure modes pinned here are the ones that would be invisible in the UI:

  * a Brier that is already binary getting halved into a better-looking number;
  * the score-limited arm's 4,722 draws quietly re-based to 5,000;
  * one arm's scores attached to another arm's card because two ids differ only
    in a leading word;
  * a package that claims authority being displayed as if it were evidence.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app.explain.formal_assessment import (
    CONCLUSIONS,
    DEFAULT_INDEX,
    DEFAULT_INDEX_SHA256,
    FormalAssessmentError,
    assessment_for,
    load_assessment,
    package_summary,
    verify_package,
)

pytestmark = pytest.mark.skipif(
    not DEFAULT_INDEX.is_file(),
    reason="the published formal assessment package is not present on this host",
)

BASELINE_COL_DET = (
    "mlb-v18-daily-80ff0089388dd199fbeb14b70ff9465d76ea14abcc7a1be1100e3237f0f3826a")
SURVIVAL = (
    "mlb-v18-survival-e2be2661fa1f72156096dd3b16087e5cc98ccd329bf299a89653de06851edac1")
TRANSITION = (
    "mlb-v18-transition-e2be2661fa1f72156096dd3b16087e5cc98ccd329bf299a89653de06851edac1")
SCORE_LIMITED = (
    "mlb-v18-score-limited-8ac962b90c3da1bbdcefd84de01ebd403f5c44459ca4e4d68d1eb4d8350996bb")


def test_the_delivered_bytes_are_what_the_handoff_declared():
    package = verify_package()
    assert package["index_sha256"] == DEFAULT_INDEX_SHA256
    assert package["index_bytes"] == 5128
    # Every member, not just the index.
    assert set(package["files_verified"]) == {
        "formal-assessment.json", "RESULTS.md", "governed-finals.json",
        "identity-before-outcomes.json", "v18_survival_only-score.json",
        "v18_transition_only-score.json", "score-limited-score.json",
    }


def test_a_single_changed_byte_fails_the_whole_package(tmp_path: Path):
    root = tmp_path / "pkg"
    shutil.copytree(DEFAULT_INDEX.parent, root)
    victim = root / "RESULTS.md"
    victim.write_bytes(victim.read_bytes() + b"\n")
    with pytest.raises(FormalAssessmentError, match="does not match its declared bytes"):
        verify_package(root / "index.json", expected_sha256=None)


def test_a_package_claiming_authority_is_refused_not_displayed(tmp_path: Path):
    root = tmp_path / "pkg"
    shutil.copytree(DEFAULT_INDEX.parent, root)
    index_path = root / "index.json"
    index = json.loads(index_path.read_bytes())
    index["authority"]["promotionAllowed"] = True
    index_path.write_text(json.dumps(index), encoding="utf-8")
    with pytest.raises(FormalAssessmentError, match="promotionAllowed"):
        verify_package(index_path, expected_sha256=None)


def test_every_arm_binds_to_its_own_exact_forecast():
    rows = load_assessment()["rows"]
    assert len(rows) == 5
    keys = {(game, forecast, arm) for (game, forecast, arm) in rows}
    assert ("824225", SURVIVAL, "V18_SURVIVAL_ONLY") in keys
    assert ("824225", TRANSITION, "V18_TRANSITION_ONLY") in keys
    assert ("823734", SCORE_LIMITED, "SCORE_LIMITED") in keys
    # Survival and transition are distinct forecasts, not one shared id.
    assert SURVIVAL != TRANSITION


def test_a_forecast_id_reaches_only_its_own_arm():
    survival = assessment_for(source_game_id="824225", forecast_id=SURVIVAL)
    transition = assessment_for(source_game_id="824225", forecast_id=TRANSITION)
    assert survival["arm"] == "V18_SURVIVAL_ONLY"
    assert transition["arm"] == "V18_TRANSITION_ONLY"
    # The two are genuinely different numbers, so a crossed join would show.
    survival_brier = next(m for m in survival["metrics"] if m["key"] == "winner_brier")
    transition_brier = next(m for m in transition["metrics"] if m["key"] == "winner_brier")
    assert survival_brier["value"] != transition_brier["value"]
    assert survival_brier["favours"] == "CANDIDATE"
    assert transition_brier["favours"] == "BASELINE"


def test_the_wrong_game_reaches_nothing():
    assert assessment_for(source_game_id="823734", forecast_id=SURVIVAL) is None
    assert assessment_for(source_game_id="824225", forecast_id="not-a-forecast") is None
    assert assessment_for(source_game_id=None, forecast_id=SURVIVAL) is None


def test_the_brier_is_the_published_binary_one_and_is_not_halved():
    row = assessment_for(source_game_id="824225", forecast_id=TRANSITION)
    brier = next(m for m in row["metrics"] if m["key"] == "winner_brier")
    # The producer's table: 0.140175 for transition, 0.140026 for the baseline.
    assert brier["value"] == pytest.approx(0.140175, abs=5e-7)
    assert brier["baseline_value"] == pytest.approx(0.140026, abs=5e-7)
    assert "pHome" in row["brier_convention"]
    assert "twice" in row["brier_note"]


def test_the_published_deltas_are_reproduced_exactly():
    """Against the producer's own RESULTS.md difference table."""

    expected = {
        (SURVIVAL, "winner_log_loss"): -0.002235,
        (SURVIVAL, "winner_brier"): -0.001046,
        (SURVIVAL, "crps_away"): +0.019248,
        (SURVIVAL, "crps_home"): -0.021588,
        (SURVIVAL, "crps_total"): +0.002457,
        (SURVIVAL, "crps_margin"): -0.005067,
        (SURVIVAL, "joint_energy"): -0.001736,
        (TRANSITION, "winner_log_loss"): +0.000320,
        (TRANSITION, "winner_brier"): +0.000150,
        (TRANSITION, "crps_away"): -0.046846,
        (TRANSITION, "crps_home"): -0.003376,
        (TRANSITION, "crps_total"): +0.006529,
        (TRANSITION, "crps_margin"): -0.050027,
        (TRANSITION, "joint_energy"): -0.029087,
        (SCORE_LIMITED, "winner_log_loss"): -0.004637,
        (SCORE_LIMITED, "winner_brier"): -0.002318,
        (SCORE_LIMITED, "crps_away"): -0.020398,
        (SCORE_LIMITED, "crps_home"): -0.043893,
        (SCORE_LIMITED, "crps_total"): -0.076619,
        (SCORE_LIMITED, "crps_margin"): -0.044054,
        (SCORE_LIMITED, "joint_energy"): -0.058154,
    }
    for (forecast, key), published in expected.items():
        game = "823734" if forecast == SCORE_LIMITED else "824225"
        row = assessment_for(source_game_id=game, forecast_id=forecast)
        metric = next(m for m in row["metrics"] if m["key"] == key)
        assert metric["delta"] == pytest.approx(published, abs=5e-7), f"{forecast} {key}"


def test_the_score_limited_denominator_is_its_own_and_is_flagged_unequal():
    row = assessment_for(source_game_id="823734", forecast_id=SCORE_LIMITED)
    assert row["draw_count"] == 4722
    assert row["baseline_draw_count"] == 5000
    assert row["equal_denominator"] is False
    limits = row["score_limited"]
    assert limits["retained_draw_count"] == 4722
    assert limits["original_draw_count"] == 5000
    assert limits["excluded_draw_count"] == 278
    # The winning selection did not change, in either direction.
    assert limits["picks_changed"] == 0
    assert row["correct_pick"] is True


def test_an_arm_carries_its_governed_final_identity_not_a_nearest_match():
    col_det = assessment_for(source_game_id="824225", forecast_id=BASELINE_COL_DET)
    cin_mil = assessment_for(source_game_id="823734", forecast_id=SCORE_LIMITED)
    assert col_det["final_identity"]["source_outcome_id"] == (
        "cd779bf5-e277-4d9c-a631-6eb3e422a0f1")
    assert col_det["final_identity"]["source_revision"] == 1
    assert col_det["final_identity"]["away_score"] == 1
    assert col_det["final_identity"]["home_score"] == 8
    assert cin_mil["final_identity"]["source_outcome_id"] == (
        "105dc37c-8fbe-4dbf-abf4-a81bfddb2194")
    assert cin_mil["final_identity"]["source_revision"] == 1
    assert cin_mil["final_identity"]["away_score"] == 4
    assert cin_mil["final_identity"]["home_score"] == 3


def test_nothing_is_promoted_and_the_sample_limit_travels_with_the_numbers():
    for game, forecast in (("824225", SURVIVAL), ("823734", SCORE_LIMITED)):
        row = assessment_for(source_game_id=game, forecast_id=forecast)
        assert row["promoted"] is False
        assert "do not advance" in (row["decision"] or "")
        assert "no recurring improvement" in " ".join(row["conclusions"]).lower()
        assert "profitability" in row["sample_note"]
        assert "no wagering authority" in row["authority_note"].lower()


def test_pitcher_allocation_stays_unavailable():
    row = assessment_for(source_game_id="824225", forecast_id=SURVIVAL)
    assert row["pitcher_allocation"]["status"] == "UNAVAILABLE"
    assert "Pitcher-allocation assessment remains unavailable." in row["conclusions"]


def test_the_summary_acknowledges_the_package_and_preserves_the_earlier_one():
    summary = package_summary()
    assert summary["index_sha256"] == DEFAULT_INDEX_SHA256
    assert summary["index_bytes"] == 5128
    assert summary["governed_study_games_scored"] == 2
    assert summary["research_interventions"] == 3
    assert summary["original_baseline_draws_per_game"] == 5000
    assert summary["score_limited_retained_draws"] == 4722
    assert summary["previous_qualified_package_sha256"] == (
        "dc279581869935ce817fda246c3920001faf20114046b160c717944a74a8fb4b")
    assert summary["promoted"] is False
    assert summary["conclusions"] == CONCLUSIONS


def test_an_absent_package_is_not_an_error():
    assert load_assessment(str(Path("no-such-directory") / "index.json")) is None
    assert assessment_for(
        source_game_id="824225", forecast_id=SURVIVAL,
        index_path=str(Path("no-such-directory") / "index.json"),
    ) is None

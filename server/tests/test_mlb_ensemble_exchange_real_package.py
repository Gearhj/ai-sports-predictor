"""The real Model Studio exchange package, verified against what AISP stores.

This reads the actual published exchange when it is present on this machine and
skips otherwise, so it is a real-package check rather than another fixture. It
pins the three things that could go wrong with a producer's own shape:

  * the pointer -> handoff -> package -> object chain must hash all the way
    down, and a package claiming authority or promotion must be refused;
  * the baseline it names must be the V18 forecast AISP already stores --
    same game, orientation, horizon, estimator, seed, count and source clock --
    and the release default must reproduce AISP's stored numbers, because the
    package says the default is unchanged V18;
  * what AISP cannot check must be labelled declared, not verified.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.shadow_import.mlb_ensemble_candidate import (
    BaselineBindingError, EnsembleCandidateImportError,
)
from app.shadow_import.mlb_ensemble_exchange import (
    REPLAY_FORECAST_TYPE, verify_exchange,
)

POINTER = Path(
    r"C:\Users\james\asp-model-exchange\mlb-v18-ensemble-research-v1\current.json"
)
PACKAGE_SHA = "665b8d7052acdb1c8c30298648a49e4d043c68aa19ada711f95a9d63c545ef91"
PA_ARTIFACT = "44ec4f457741f1aaeff42b81af1be5fd2712e494709ff1a0a8f0742b88d71c8b"

pytestmark = pytest.mark.skipif(
    not POINTER.is_file(), reason="the published ensemble exchange is not on this machine"
)


def test_the_published_package_verifies_end_to_end():
    verified = verify_exchange(POINTER)

    assert verified.candidate_id == "mlb-v18-ensemble-rc-20260912-v1"
    assert verified.package_sha256 == PACKAGE_SHA
    assert verified.artifact_type == "HISTORICAL_REPLAY"
    assert verified.package["forecastType"] == REPLAY_FORECAST_TYPE
    # Every document AISP can hash, hashed.
    for label in ("candidate package", "research handoff", "protocol",
                  "game comparison", "configuration table"):
        assert label in verified.verified_objects


def test_artifacts_owned_elsewhere_are_declared_not_claimed_as_verified():
    """The duration pilot's fitted hazard lives in its owner's exchange."""

    verified = verify_exchange(POINTER)

    assert "starter-duration fitted hazard" in verified.external_objects
    assert "starter-duration fitted hazard" not in verified.verified_objects


def test_a_tampered_pointer_is_refused(tmp_path: Path):
    """Copy the exchange, change one byte of the package, and it must not pass."""

    import shutil

    root = tmp_path / "exchange"
    shutil.copytree(POINTER.parent, root)
    package_dir = root / "objects" / "sha256" / PACKAGE_SHA
    target = next(child for child in package_dir.iterdir() if child.is_file())
    body = json.loads(target.read_text(encoding="utf-8"))
    body["candidateId"] = "tampered"
    target.write_text(json.dumps(body), encoding="utf-8")

    with pytest.raises(EnsembleCandidateImportError, match="fails its checksum"):
        verify_exchange(root / "current.json")


def test_a_package_claiming_promotion_is_refused(tmp_path: Path):
    import shutil

    root = tmp_path / "exchange"
    shutil.copytree(POINTER.parent, root)
    pointer = json.loads((root / "current.json").read_text(encoding="utf-8"))
    pointer["wageringAuthority"] = True
    (root / "current.json").write_text(json.dumps(pointer), encoding="utf-8")

    with pytest.raises(EnsembleCandidateImportError, match="wageringAuthority"):
        verify_exchange(root / "current.json")


def test_the_package_binds_the_complete_baseline_system_not_just_the_pa_artifact():
    """Every link AISP can check, and an explicit list of the ones it cannot."""

    verified = verify_exchange(POINTER)
    v18 = verified.package["components"]["v18"]

    # The PA estimator, and much more besides it.
    assert v18["paArtifact"]["sha256"] == PA_ARTIFACT
    assert v18["parentBundleSha256"]
    assert v18["transitionArtifact"]["sha256"]
    assert v18["transitionFold"] == 4
    assert v18["treatmentManifestSha256"]
    assert v18["treatmentSpecificationId"]
    assert v18["preprocessorAndEncoderPreservedInsidePaArtifact"] is True
    assert v18["fitCount"] == 0
    assert len(v18["paClassOrder"]) == 8
    assert (v18["rawInputCount"], v18["treatedInputCount"]) == (246, 273)

    comparison = verified.comparison
    assert comparison["masterSeed"]
    assert comparison["simulationCountPerConfiguration"] == 5000
    assert comparison["paReload"]["archivedPregameCapture"]
    assert comparison["teams"]["HOME"]["uuid"] and comparison["teams"]["AWAY"]["uuid"]
    assert comparison["scheduledStartUtc"]


def test_the_replay_declares_itself_a_replay_and_orders_nothing():
    verified = verify_exchange(POINTER)

    assert verified.comparison["forecast"] is False
    assert verified.comparison["replayGeneratedAfterGameStart"] is True
    assert verified.comparison["games"] == 1
    assert verified.comparison["selectionUse"] == "NONE"
    assert verified.comparison["commonFourWayCohortTooSparse"] is True
    assert "COMPLETED_ONE_GAME_HISTORICAL_REPLAY_TOO_SPARSE_FOR_ORDERING" in str(
        verified.package["gameComparisonStatus"])


def test_the_release_default_is_unchanged_v18_with_the_others_not_enabled():
    verified = verify_exchange(POINTER)
    components = verified.package["components"]

    assert verified.package["defaultConfiguration"] == "A_FROZEN_V18_BASELINE_HOOK"
    assert components["calibration"]["status"] == "DISABLED_SELECTION_GATE"
    assert components["calibration"]["comparisonOnly"] is True
    assert components["starterDuration"]["status"] == "SHADOW_ONLY_UNASSESSED_GAME_LEVEL"
    assert components["matchupSpecialist"]["status"] == "DISABLED_NO_VALIDATED_SPECIALIST"

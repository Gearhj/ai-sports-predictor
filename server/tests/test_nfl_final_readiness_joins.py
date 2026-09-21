"""The five NFL FINAL readiness joins, each fail-closed condition, and the flag.

Every test here runs against the synthetic family in
``tests/fixtures/nfl_final_readiness.py``, written into ``tmp_path``. The real
exchange root is never read and never written.
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path

import pytest

from app.gen2.exchange.canonical_json import canonical_json_bytes
from app.services import nfl_final_readiness as gate
from tests.fixtures import nfl_final_readiness as fx


def _verify(family, *, observed_delta_minutes: int = 18, **kwargs):
    """Verify the positive family, with named overrides for the negatives."""

    params = {
        "exchange_root": family.root,
        "forecast_id": family.forecast_id,
        "event_id": family.event_id,
        "kickoff_utc": family.kickoff,
        "observed_at": family.kickoff - timedelta(minutes=observed_delta_minutes),
        "snapshot_version": 1,
    }
    params.update(kwargs)
    return gate.verify_final_readiness(**params)


def _rewrite(path: Path, mutate) -> None:
    document = json.loads(path.read_bytes())
    mutate(document)
    path.write_bytes(canonical_json_bytes(document))


# ---------------------------------------------------------------------------
# the positive case
# ---------------------------------------------------------------------------

def test_a_complete_family_verifies_and_reports_both_clocks(tmp_path: Path) -> None:
    family = fx.build(tmp_path)
    result = _verify(family)

    assert result.verified, (result.sub_reasons, result.detail)
    assert result.reason is None
    # Join 4 keeps the two obligations apart, and both are recorded.
    assert result.evidence["producer_deadline_utc"] == (
        family.kickoff - timedelta(minutes=21)).isoformat()
    assert result.evidence["consumer_cutoff_utc"] == (
        family.kickoff - timedelta(minutes=15)).isoformat()
    assert result.evidence["exposure_status"] == gate.STATUS_TIMELY
    assert result.evidence["receipt_sha256"] == family.receipt_sha256
    assert result.evidence["recontainered"] is False


def test_a_sidecar_first_seen_after_T21_is_telemetry_delay_not_a_missed_boundary(
    tmp_path: Path,
) -> None:
    """The contract's central distinction, stated as a test.

    PAIR_AND_READY_INDEX_TIMELY certifies the pair and the ready index were
    observed by T-21. It does NOT certify the sidecar was readable by T-21. So
    a sidecar first observed two minutes AFTER the producer boundary -- but
    still read by this consumer before T-15 -- verifies.
    """

    family = fx.build(
        tmp_path, sidecar_first_observed=fx.KICKOFF - timedelta(minutes=19))
    result = _verify(family, observed_delta_minutes=16)

    assert result.verified, (result.sub_reasons, result.detail)
    assert result.evidence["proof_observed_after_producer_boundary"] is True
    assert result.evidence["consumer_cutoff_passed_at_proof_observation"] is False


def test_a_recontainer_keeps_the_grant_on_its_own_child(tmp_path: Path) -> None:
    """An unrelated child's split does not invalidate the original snapshots.

    The receipt's original one-game bundle stays on disk as historical grant
    provenance; the new current wrapper carries a byte-identical copy of this
    child alongside another game.
    """

    family = fx.build(tmp_path)
    original_child = json.loads(
        (tmp_path / fx.FORECAST_FAMILY / "sha256" / family.bundle_sha256
         / "bundle.json").read_bytes())["forecasts"][0]
    neighbour = fx.build_forecast_child(
        event_id="NFL-2026-W03-NEIGHBOUR",
        home=("nfl-home-201", "41", "NBH", "Neighbour Home"),
        away=("nfl-away-202", "42", "NBA", "Neighbour Away"))
    new_sha, _ = fx.write_forecast_wrapper(tmp_path, [original_child, neighbour])
    fx.write_forecast_index(tmp_path, [new_sha], historical=[family.bundle_sha256])

    result = _verify(family)

    assert result.verified, (result.sub_reasons, result.detail)
    assert result.evidence["recontainered"] is True
    assert result.evidence["current_bundle_sha256"] == new_sha
    # The grant's provenance is still the ORIGINAL bundle, never the new one.
    assert result.evidence["grant_provenance_bundle_sha256"] == family.bundle_sha256


# ---------------------------------------------------------------------------
# join 1 -- physical reads, content addresses, identity, positive status
# ---------------------------------------------------------------------------

def test_an_absent_sidecar_fails_closed_with_no_fallback(tmp_path: Path) -> None:
    family = fx.build(tmp_path, with_availability=False)
    result = _verify(family)

    assert not result.verified
    assert result.reason == gate.REASON
    assert result.sub_reasons == (gate.EXPOSURE_REFERENCE_ABSENT,)


def test_late_not_ready_is_never_positive_evidence(tmp_path: Path) -> None:
    family = fx.build(tmp_path, exposure_status="LATE_NOT_READY")
    result = _verify(family)

    assert not result.verified
    assert result.sub_reasons == (gate.EXPOSURE_STATUS_NOT_POSITIVE,)


def test_receipt_bytes_that_do_not_hash_to_their_address_fail_closed(
    tmp_path: Path,
) -> None:
    family = fx.build(tmp_path)
    _rewrite(family.receipt_path(),
             lambda doc: doc.update({"producerRevision": "tampered-revision"}))
    result = _verify(family)

    assert not result.verified
    assert result.sub_reasons == (gate.RECEIPT_ADDRESS_MISMATCH,)


def test_exposure_bytes_that_do_not_hash_to_their_address_fail_closed(
    tmp_path: Path,
) -> None:
    family = fx.build(tmp_path)
    _rewrite(family.exposure_path(),
             lambda doc: doc.update({"readyAvailableAtUtc": "2026-09-22T00:00:00Z"}))
    result = _verify(family)

    assert not result.verified
    assert result.sub_reasons == (gate.EXPOSURE_ADDRESS_MISMATCH,)


def test_an_ambiguous_ready_index_identity_fails_closed(tmp_path: Path) -> None:
    family = fx.build(tmp_path)

    def duplicate(document):
        document["entries"] = document["entries"] * 2

    _rewrite(family.index_path(), duplicate)
    result = _verify(family)

    assert not result.verified
    assert result.sub_reasons == (gate.READINESS_INDEX_AMBIGUOUS_ENTRY,)


def test_a_receipt_for_a_different_game_does_not_certify_this_one(
    tmp_path: Path,
) -> None:
    family = fx.build(tmp_path)
    result = _verify(family, event_id="NFL-2026-W03-SOME-OTHER-GAME")

    assert not result.verified
    assert result.sub_reasons == (gate.RECEIPT_IDENTITY_MISMATCH,)


def test_a_forecast_with_no_ready_index_entry_fails_closed(tmp_path: Path) -> None:
    family = fx.build(tmp_path)
    result = _verify(family, forecast_id="asp:nfl:forecast:sha256:" + "0" * 64)

    assert not result.verified
    assert result.sub_reasons == (gate.READINESS_INDEX_NO_ENTRY,)


# ---------------------------------------------------------------------------
# join 2 -- the immutable snapshots
# ---------------------------------------------------------------------------

def test_a_forecast_absent_from_the_original_snapshot_fails_closed(
    tmp_path: Path,
) -> None:
    """A membership the producer never recorded cannot be supplied later.

    The receipt's forecast-index snapshot is rewritten to omit the bundle. The
    receipt is re-addressed so the tampering passes join 1 cleanly and the
    failure is attributable to the snapshot join alone.
    """

    family = fx.build(tmp_path)
    receipt = json.loads(family.receipt_path().read_bytes())
    receipt["forecastIndexSnapshot"]["currentBundleSha256s"] = []
    receipt["forecastIndexSnapshot"]["currentBundleCount"] = 0
    new_bytes = canonical_json_bytes(receipt)
    new_sha = hashlib.sha256(new_bytes).hexdigest()
    (tmp_path / gate.READINESS_ROOT / gate.OBJECTS_DIR / new_sha).mkdir(parents=True)
    (tmp_path / gate.READINESS_ROOT / gate.OBJECTS_DIR / new_sha
     / "receipt.json").write_bytes(new_bytes)

    def repoint(document):
        entry = document["entries"][0]
        entry["receiptSha256"] = new_sha
        entry["receiptRef"] = f"{gate.OBJECTS_DIR}/{new_sha}/receipt.json"
        entry["exposureReferencePath"] = f"{gate.AVAILABILITY_DIR}/{new_sha}.json"

    _rewrite(family.index_path(), repoint)
    availability = json.loads(family.availability_path().read_bytes())
    availability["baseReceiptSha256"] = new_sha
    (tmp_path / gate.READINESS_ROOT / gate.AVAILABILITY_DIR
     / f"{new_sha}.json").write_bytes(canonical_json_bytes(availability))
    _rewrite(family.exposure_path(),
             lambda doc: doc.update({"baseReceiptSha256": new_sha}))
    # The exposure has been re-addressed by that edit, so re-point the sidecar.
    exposure_bytes = family.exposure_path().read_bytes()
    exposure_sha = hashlib.sha256(exposure_bytes).hexdigest()
    (tmp_path / gate.READINESS_ROOT / gate.OBJECTS_DIR / exposure_sha).mkdir(parents=True)
    (tmp_path / gate.READINESS_ROOT / gate.OBJECTS_DIR / exposure_sha
     / "exposure.json").write_bytes(exposure_bytes)
    availability["exposureSha256"] = exposure_sha
    availability["exposureObjectPath"] = f"{gate.OBJECTS_DIR}/{exposure_sha}/exposure.json"
    (tmp_path / gate.READINESS_ROOT / gate.AVAILABILITY_DIR
     / f"{new_sha}.json").write_bytes(canonical_json_bytes(availability))

    result = _verify(family)

    assert not result.verified
    assert result.sub_reasons == (gate.FORECAST_SNAPSHOT_MEMBERSHIP_MISSING,)


def test_an_exposure_whose_ready_index_snapshot_differs_fails_closed(
    tmp_path: Path,
) -> None:
    family = fx.build(tmp_path)
    exposure = json.loads(family.exposure_path().read_bytes())
    exposure["readyIndexSnapshot"] = {"schemaVersion": gate.INDEX_SCHEMA, "entries": []}
    new_bytes = canonical_json_bytes(exposure)
    new_sha = hashlib.sha256(new_bytes).hexdigest()
    (tmp_path / gate.READINESS_ROOT / gate.OBJECTS_DIR / new_sha).mkdir(parents=True)
    (tmp_path / gate.READINESS_ROOT / gate.OBJECTS_DIR / new_sha
     / "exposure.json").write_bytes(new_bytes)

    def repoint(document):
        document["exposureSha256"] = new_sha
        document["exposureObjectPath"] = f"{gate.OBJECTS_DIR}/{new_sha}/exposure.json"

    _rewrite(family.availability_path(), repoint)
    result = _verify(family)

    assert not result.verified
    assert result.sub_reasons == (gate.READY_INDEX_SNAPSHOT_MISMATCH,)


# ---------------------------------------------------------------------------
# join 3 -- present-tense discovery
# ---------------------------------------------------------------------------

def test_a_superseded_forecast_is_not_rescued_by_an_old_receipt(
    tmp_path: Path,
) -> None:
    family = fx.build(tmp_path, forecast_current=False)
    result = _verify(family)

    assert not result.verified
    assert result.sub_reasons == (gate.FORECAST_NOT_CURRENT,)


def test_a_supplement_no_longer_named_fails_closed(tmp_path: Path) -> None:
    family = fx.build(tmp_path, supplement_current=False)
    result = _verify(family)

    assert not result.verified
    assert result.sub_reasons == (gate.SUPPLEMENT_NOT_CURRENTLY_NAMED,)


def test_the_same_forecast_in_two_current_wrappers_is_ambiguous(
    tmp_path: Path,
) -> None:
    family = fx.build(tmp_path)
    child = json.loads(
        (tmp_path / fx.FORECAST_FAMILY / "sha256" / family.bundle_sha256
         / "bundle.json").read_bytes())["forecasts"][0]
    neighbour = fx.build_forecast_child(
        event_id="NFL-2026-W03-NEIGHBOUR",
        home=("nfl-home-201", "41", "NBH", "Neighbour Home"),
        away=("nfl-away-202", "42", "NBA", "Neighbour Away"))
    second_sha, _ = fx.write_forecast_wrapper(tmp_path, [child, neighbour])
    fx.write_forecast_index(tmp_path, [family.bundle_sha256, second_sha])

    result = _verify(family)

    assert not result.verified
    assert result.sub_reasons == (gate.FORECAST_AMBIGUOUS_IN_CURRENT,)


def test_a_grant_naming_a_different_child_is_never_transferred(
    tmp_path: Path,
) -> None:
    family = fx.build(tmp_path)
    other = fx.write_supplement(
        tmp_path, forecast_id="asp:nfl:forecast:sha256:" + "b" * 64)
    # The receipt still points at the original grant; the index now names only
    # a grant for a DIFFERENT child. The original is no longer current.
    fx.write_supplement_index(tmp_path, [other])
    result = _verify(family)

    assert not result.verified
    assert result.sub_reasons == (gate.SUPPLEMENT_NOT_CURRENTLY_NAMED,)


def test_a_receipt_granting_a_different_child_is_refused(tmp_path: Path) -> None:
    family = fx.build(tmp_path)
    result = _verify(family)
    assert result.verified

    # Re-point the supplement's own binding at another forecast without moving
    # the receipt: the grant may not follow.
    directory = (tmp_path / fx.SUPPLEMENT_ROOT / "sha256" / family.supplement_sha256)
    supplement = json.loads((directory / "supplement.json").read_bytes())
    supplement["forecastId"] = "asp:nfl:forecast:sha256:" + "c" * 64
    (directory / "supplement.json").write_bytes(canonical_json_bytes(supplement))

    # The bytes no longer hash to the address, which is itself fail-closed.
    assert _verify(family).sub_reasons == (gate.SUPPLEMENT_ABSENT,)


def test_changed_membership_between_first_read_and_recheck_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A concurrent index replacement is caught by the post-verification re-read."""

    family = fx.build(tmp_path)
    original = gate._verify_grant_currently_named

    def mutate_then_call(root, **kwargs):
        original(root, **kwargs)
        # The producer replaces the locator while this run is mid-flight.
        fx.write_forecast_index(tmp_path, [family.bundle_sha256],
                                updated_at=family.kickoff - timedelta(minutes=10))

    monkeypatch.setattr(gate, "_verify_grant_currently_named", mutate_then_call)
    result = _verify(family)

    assert not result.verified
    assert result.sub_reasons == (gate.DISCOVERY_CHANGED_DURING_VERIFICATION,)


def test_a_current_wrapper_that_does_not_verify_supplies_nothing(
    tmp_path: Path,
) -> None:
    """A wrapper refused at import may not supply a controlling forecast."""

    family = fx.build(tmp_path)
    directory = tmp_path / fx.FORECAST_FAMILY / "sha256" / family.bundle_sha256
    (directory / "VALIDATION.md").write_bytes(b"# not the pinned instructions\n")
    result = _verify(family)

    assert not result.verified
    assert result.sub_reasons == (gate.FORECAST_WRAPPER_NOT_VERIFIED,)


# ---------------------------------------------------------------------------
# join 4 -- the two clocks
# ---------------------------------------------------------------------------

def test_a_consumer_observation_after_T15_fails_closed(tmp_path: Path) -> None:
    family = fx.build(tmp_path)
    result = _verify(family, observed_delta_minutes=14)

    assert not result.verified
    assert result.sub_reasons == (gate.CONSUMER_OBSERVATION_LATE,)


def test_a_package_imported_after_T15_fails_closed(tmp_path: Path) -> None:
    family = fx.build(tmp_path)
    result = _verify(family,
                     package_imported_at=family.kickoff - timedelta(minutes=14))

    assert not result.verified
    assert result.sub_reasons == (gate.PACKAGE_IMPORT_LATE,)


def test_a_grant_imported_after_T15_fails_closed(tmp_path: Path) -> None:
    family = fx.build(tmp_path)
    result = _verify(family, grant_imported_at=family.kickoff - timedelta(minutes=1))

    assert not result.verified
    assert result.sub_reasons == (gate.GRANT_IMPORT_LATE,)


def test_a_producer_observation_after_T21_fails_closed(tmp_path: Path) -> None:
    """The producer boundary, tested on the PRODUCER's own observations only."""

    family = fx.build(
        tmp_path, ready_index_observed=fx.KICKOFF - timedelta(minutes=20))
    result = _verify(family)

    assert not result.verified
    assert result.sub_reasons == (gate.PRODUCER_BOUNDARY_MISSED,)


def test_the_checkpoints_selected_forecast_is_recorded_and_must_match(
    tmp_path: Path,
) -> None:
    family = fx.build(tmp_path)
    result = _verify(family, checkpoint_forecast_id="asp:nfl:forecast:sha256:" + "d" * 64)

    assert not result.verified
    assert result.sub_reasons == (gate.CHECKPOINT_FORECAST_MISMATCH,)
    assert result.evidence["checkpoint_selected_forecast_id"].endswith("d" * 64)


def test_an_unknown_package_import_can_be_required(tmp_path: Path) -> None:
    family = fx.build(tmp_path)
    assert _verify(family, require_package_import=True).sub_reasons == (
        gate.PACKAGE_IMPORT_UNKNOWN,)
    # And is recorded, not inferred, when it is known.
    ok = _verify(family, require_package_import=True,
                 package_imported_at=family.kickoff - timedelta(minutes=20))
    assert ok.verified
    assert ok.evidence["package_imported_at_utc"] is not None

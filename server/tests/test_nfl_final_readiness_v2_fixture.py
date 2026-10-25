"""The readiness joins against Model Studio's AUTHENTIC staged-V2 fixture.

WHY THIS FILE EXISTS SEPARATELY
-------------------------------
Every other readiness test runs against a fixture we built ourselves, from our
own reading of the contract prose. That proved the joins' LOGIC and nothing
about their field names -- and the names turned out to be wrong in almost every
document: the ready index is a map keyed by forecast id rather than a list of
entries, the producer revision lives inside ``engine``, the snapshots are
content-addressed references rather than inline copies, the availability
sidecar is a bare ``{bytes, path, sha256}`` object with no schema wrapper at
all, and the "schema hash" is the hash of a CONTRACT FILE rather than a field
on the receipt.

So this file reads the bytes Codex actually emitted, at the hashes Codex
published, and pins the consumer to them. The fixture is vendored under
``tests/fixtures/model_studio_readiness_v2`` so the suite is hermetic; its three
top-level hashes are asserted below, which makes an accidental edit to the
vendored copy a test failure rather than a silent drift.

THE FIXTURE'S ABSOLUTE PATHS ARE A FEATURE OF THIS TEST
-------------------------------------------------------
Every reference inside the authentic documents carries an absolute path on the
machine that wrote them -- ``C:\\Users\\james\\asp-model-studio\\...`` -- which
does not exist in the vendored tree. The suite passing anyway is the proof that
``_resolve_ref`` never follows a declared path and instead resolves each object
by its content address beneath the configured root.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.gen2.exchange.canonical_json import canonical_json_bytes
from app.services import nfl_final_readiness as rg

VENDORED = Path(__file__).parent / "fixtures" / "model_studio_readiness_v2"

#: Published by Model Studio, 2026-09-21. Re-hashed here from the vendored
#: bytes, so this file fails if the copy is ever edited.
SCHEMA_SHA256 = "963774925dfa39d6f8aff7623ad4825f4bdcca9d942e6c3c52e456cad964ce21"
MANIFEST_SHA256 = "554cb0f1b8d9f866c48a7895d3cf9b44ccda991ae02a951867d27ffef39d3394"
PRODUCER_REVISION = "078e5dafd1f61fd42b7a36e4d6a4e8073be0c50b"

FORECAST_ID = ("asp-sports-game-forecast:sha256:"
               "218c519efa15f5ea16ae80917ea7b54142faaae372bb9d05b4e497da7504ef2e")
EVENT_ID = "SYNTHETIC-NFL-V2-FIXTURE"
RECEIPT_SHA = "f96a87ad31e7e7ded22f8238c1e0ec7361379da6d989de5ead164605d301a29e"
EXPOSURE_SHA = "f5bb308a15d13789c2eda29e0e85957ee1b68f1a43d06db318ab5ef64aa5ea83"
BUNDLE_SHA = "5137b944c37d53434c009e2734ad93216d4b5900feca5168c7492a8344df2097"
SUPPLEMENT_ADDRESS = "3424f80579567873833a918b59b70cf3056bb00bec6987e96bc9ac7bed2525b7"

#: The fixture's own clocks. Kickoff is T; the producer boundary is T-21 and the
#: consumer cutoff T-15, both of which the authentic documents state themselves.
KICKOFF = datetime(2026, 9, 21, 16, 45, tzinfo=UTC)
PRODUCER_DEADLINE = KICKOFF - timedelta(minutes=21)
CONSUMER_CUTOFF = KICKOFF - timedelta(minutes=15)
#: Comfortably inside the window, and after the producer's own observations.
OBSERVED = KICKOFF - timedelta(minutes=20)


def test_the_vendored_fixture_is_the_bytes_model_studio_published() -> None:
    """If this fails, the fixture was edited and nothing below means anything."""

    assert hashlib.sha256(
        (VENDORED / "READINESS_CONSUMER_SCHEMA.json").read_bytes()).hexdigest() == SCHEMA_SHA256
    assert hashlib.sha256(
        (VENDORED / "FIXTURE_MANIFEST.json").read_bytes()).hexdigest() == MANIFEST_SHA256
    pointer = json.loads((VENDORED / "LATEST.json").read_text(encoding="utf-8"))
    assert pointer["producerRevision"] == PRODUCER_REVISION
    assert pointer["schemaSha256"] == SCHEMA_SHA256
    # It is synthetic review evidence, and says so. We never treat it as
    # operational exposure.
    assert pointer["operationalExposure"] is False


@pytest.fixture
def family(tmp_path: Path) -> dict:
    """A writable copy, so a negative case can damage exactly one thing."""

    root = tmp_path / "fixture"
    shutil.copytree(VENDORED, root)
    return {
        "root": root / "workspace" / "exchange",
        "schema": root / "READINESS_CONSUMER_SCHEMA.json",
        "readiness": root / "workspace" / "exchange" / "nfl-final-readiness",
    }


def verify(family: dict, **overrides):
    kwargs = dict(
        exchange_root=family["root"], forecast_id=FORECAST_ID, event_id=EVENT_ID,
        kickoff_utc=KICKOFF, observed_at=OBSERVED,
        snapshot_type="FINAL_PREGAME", snapshot_version=1,
        engine_id="NFL_M1_XGBOOST",
        required_producer_revision=PRODUCER_REVISION,
        required_schema_sha256=SCHEMA_SHA256,
        schema_path=family["schema"],
    )
    kwargs.update(overrides)
    return rg.verify_final_readiness(**kwargs)


def rewrite(path: Path, mutate) -> None:
    """Edit a document in place, in CANONICAL exchange JSON.

    Canonical bytes matter here. Every reader in the module refuses
    non-canonical JSON outright, so a helper that wrote ``json.dumps`` defaults
    would make each negative case pass for the wrong reason -- reporting a
    malformed document where the test means to prove a membership or identity
    refusal. Writing canonically forces each case to fail on the join it names.
    """

    document = json.loads(path.read_text(encoding="utf-8"))
    mutate(document)
    path.write_bytes(canonical_json_bytes(document))


# --- the positive case ------------------------------------------------------

def test_the_authentic_fixture_verifies(family) -> None:
    """Every join, against real producer bytes, pinned to the reviewed identity."""

    out = verify(family)

    assert out.verified is True, (out.sub_reasons, out.detail)
    assert out.evidence["producer_revision"] == PRODUCER_REVISION
    assert out.evidence["receipt_sha256"] == RECEIPT_SHA
    assert out.evidence["exposure_status"] == rg.STATUS_TIMELY
    assert out.evidence["receipt_bundle_sha256"] == BUNDLE_SHA
    assert out.evidence["supplement_sha256"] == SUPPLEMENT_ADDRESS
    assert out.evidence["bound_schema_sha256"] == SCHEMA_SHA256
    # Recorded verbatim, never used as a pass/fail input.
    assert out.evidence["certifies_sidecar_exposure_before_producer_deadline"] is False
    assert out.evidence["producer_clock_meaning"].startswith("ACTUAL_OBSERVATION")


def test_the_declared_absolute_paths_are_never_followed(family) -> None:
    """The fixture's refs point at a directory that does not exist here.

    It verifies anyway, because every object is located by content address
    beneath the configured root. A consumer that opened the declared path would
    fail this test -- and in production would read whatever file a receipt
    chose to name, anywhere on the machine.
    """

    index = json.loads((family["readiness"] / "current.json").read_text(encoding="utf-8"))
    declared = index["receipts"][FORECAST_ID]["receiptRef"]["path"]
    assert "asp-model-studio" in declared
    # The declared path names the machine Model Studio wrote on, which is NOT
    # under the root we were configured with. Whether it happens to exist on
    # this machine is irrelevant -- and on a developer box it usually does,
    # which is exactly why following it would be dangerous rather than merely
    # broken.
    assert family["root"] not in Path(declared).parents

    assert verify(family).verified is True


# --- revision and schema pins ----------------------------------------------

def test_a_receipt_from_another_revision_is_refused(family) -> None:
    out = verify(family, required_producer_revision="0" * 40)

    assert out.verified is False
    assert out.sub_reasons == (rg.PRODUCER_REVISION_NOT_PINNED,)


def test_the_revision_is_read_from_engine_not_the_top_level(family) -> None:
    """Pinning against a top-level copy must not rescue a wrong engine revision."""

    receipt = family["readiness"] / "objects" / "sha256" / RECEIPT_SHA / "receipt.json"
    rewrite(receipt, lambda doc: doc.__setitem__("producerRevision", PRODUCER_REVISION))
    rewrite(receipt, lambda doc: doc["engine"].__setitem__("producerRevision", "wrong"))

    out = verify(family)

    # The edit also breaks the content address, which is caught first -- and
    # that is the point: the receipt cannot be reshaped at all.
    assert out.verified is False
    assert out.sub_reasons[0] in (
        rg.RECEIPT_ADDRESS_MISMATCH, rg.RECEIPT_MALFORMED, rg.PRODUCER_REVISION_NOT_PINNED)


def test_a_schema_hash_that_does_not_match_the_file_is_refused(family) -> None:
    out = verify(family, required_schema_sha256="a" * 64)

    assert out.verified is False
    assert out.sub_reasons == (rg.READINESS_SCHEMA_UNBOUND,)


def test_a_missing_schema_file_is_refused_not_skipped(family) -> None:
    family["schema"].unlink()

    out = verify(family)

    assert out.verified is False
    assert out.sub_reasons == (rg.READINESS_SCHEMA_UNBOUND,)


def test_a_pinned_hash_with_no_configured_schema_is_refused(family) -> None:
    out = verify(family, schema_path=None)

    assert out.verified is False
    assert out.sub_reasons == (rg.READINESS_SCHEMA_UNBOUND,)


def test_a_document_reshaped_against_the_bound_schema_is_refused(family) -> None:
    """An extra field the reviewed schema forbids fails shape validation.

    The sidecar is the one document whose address is its FILENAME rather than
    its content, so it is the one place a reshape survives long enough for the
    schema check to be what catches it.
    """

    exposure = family["readiness"] / "objects" / "sha256" / EXPOSURE_SHA / "exposure.json"
    document = json.loads(exposure.read_text(encoding="utf-8"))
    document["unexpectedField"] = "x"
    raw = canonical_json_bytes(document)
    new_sha = hashlib.sha256(raw).hexdigest()
    target = family["readiness"] / "objects" / "sha256" / new_sha
    target.mkdir(parents=True, exist_ok=True)
    (target / "exposure.json").write_bytes(raw)
    # Re-point the sidecar at the reshaped object, honestly, so the address
    # check passes and the SHAPE check is what refuses.
    sidecar = family["readiness"] / "availability" / f"{RECEIPT_SHA}.json"
    reference = json.loads(sidecar.read_text(encoding="utf-8"))
    reference.update({"sha256": new_sha, "bytes": len(raw)})
    sidecar.write_bytes(canonical_json_bytes(reference))

    out = verify(family)

    assert out.verified is False
    assert out.sub_reasons == (rg.RECEIPT_SCHEMA_MISMATCH,), out.detail


# --- absence ----------------------------------------------------------------

def test_an_absent_sidecar_is_refused(family) -> None:
    (family["readiness"] / "availability" / f"{RECEIPT_SHA}.json").unlink()

    out = verify(family)

    assert out.verified is False
    assert out.sub_reasons == (rg.EXPOSURE_REFERENCE_ABSENT,)


def test_an_absent_exposure_object_is_refused(family) -> None:
    (family["readiness"] / "objects" / "sha256" / EXPOSURE_SHA / "exposure.json").unlink()

    out = verify(family)

    assert out.verified is False
    assert out.sub_reasons == (rg.EXPOSURE_ABSENT,)


def test_an_absent_receipt_is_refused(family) -> None:
    (family["readiness"] / "objects" / "sha256" / RECEIPT_SHA / "receipt.json").unlink()

    out = verify(family)

    assert out.verified is False
    assert out.sub_reasons == (rg.RECEIPT_ABSENT,)


def test_an_absent_ready_index_is_refused(family) -> None:
    (family["readiness"] / "current.json").unlink()

    out = verify(family)

    assert out.verified is False
    assert out.sub_reasons == (rg.READINESS_INDEX_ABSENT,)


def test_a_forecast_the_index_does_not_map_is_refused(family) -> None:
    out = verify(family, forecast_id="asp-sports-game-forecast:sha256:" + "b" * 64)

    assert out.verified is False
    assert out.sub_reasons == (rg.READINESS_INDEX_NO_ENTRY,)


# --- the two clocks ---------------------------------------------------------

def test_a_consumer_observation_after_t_minus_15_is_refused(family) -> None:
    out = verify(family, observed_at=CONSUMER_CUTOFF + timedelta(seconds=1))

    assert out.verified is False
    assert out.sub_reasons == (rg.CONSUMER_OBSERVATION_LATE,)


def test_a_package_imported_after_t_minus_15_is_refused(family) -> None:
    out = verify(family, package_imported_at=CONSUMER_CUTOFF + timedelta(minutes=1))

    assert out.verified is False
    assert out.sub_reasons == (rg.PACKAGE_IMPORT_LATE,)


def test_a_grant_imported_after_t_minus_15_is_refused(family) -> None:
    out = verify(family, grant_imported_at=CONSUMER_CUTOFF + timedelta(minutes=1))

    assert out.verified is False
    assert out.sub_reasons == (rg.GRANT_IMPORT_LATE,)


def test_an_unknown_package_import_is_refused_when_required(family) -> None:
    out = verify(family, package_imported_at=None, require_package_import=True)

    assert out.verified is False
    assert out.sub_reasons == (rg.PACKAGE_IMPORT_UNKNOWN,)


def test_a_kickoff_that_does_not_match_the_receipts_deadline_is_refused(family) -> None:
    """The receipt's T-21 must be THIS game's T-21, not merely well formed."""

    out = verify(family, kickoff_utc=KICKOFF + timedelta(hours=3))

    assert out.verified is False
    assert out.sub_reasons == (rg.RECEIPT_MALFORMED,)


def test_the_sidecars_own_readability_is_never_a_timing_failure(family) -> None:
    """Touching the sidecar's mtime to after T-21 changes nothing.

    ``PAIR_AND_READY_INDEX_TIMELY`` does not certify sidecar readability, and
    the authentic exposure says so in its own bytes. What matters is OUR
    observation by T-15, which is a clock we own.
    """

    sidecar = family["readiness"] / "availability" / f"{RECEIPT_SHA}.json"
    late = (PRODUCER_DEADLINE + timedelta(minutes=4)).timestamp()
    import os
    os.utime(sidecar, (late, late))

    assert verify(family).verified is True


# --- membership and discovery ----------------------------------------------

def test_a_forecast_no_longer_current_is_refused(family) -> None:
    """Present-tense membership, not the frozen snapshot, governs."""

    index = family["root"] / "aisp-daily-forecast-bundles" / "nfl-2026-research-shadow-index.json"
    rewrite(index, lambda doc: doc.__setitem__(
        "currentBundleSha256s",
        [sha for sha in doc["currentBundleSha256s"] if sha != BUNDLE_SHA]))

    out = verify(family)

    assert out.verified is False
    assert out.sub_reasons == (rg.FORECAST_NOT_CURRENT,)


def test_a_second_current_wrapper_claiming_this_forecast_is_refused(family) -> None:
    """Ambiguous membership is fail closed, never "take the readable one".

    The copied wrapper does not verify -- its bytes sit at an address that is
    not their hash -- so an earlier draft skipped it and passed on the strength
    of the one good host. Two current containers claiming the same child, one
    of them unreadable, is an exchange state we cannot account for.
    """

    bundles = family["root"] / "aisp-daily-forecast-bundles"
    duplicate = bundles / "sha256" / ("c" * 64)
    duplicate.mkdir(parents=True, exist_ok=True)
    shutil.copy(bundles / "sha256" / BUNDLE_SHA / "bundle.json", duplicate / "bundle.json")
    index = bundles / "nfl-2026-research-shadow-index.json"
    rewrite(index, lambda doc: doc["currentBundleSha256s"].append("c" * 64))

    out = verify(family)

    assert out.verified is False
    assert out.sub_reasons == (rg.FORECAST_WRAPPER_NOT_VERIFIED,), out.detail


def test_a_supplement_no_longer_named_is_refused(family) -> None:
    index = family["root"] / "nfl-labs-supplements" / "nfl-labs-supplement-index.json"
    rewrite(index, lambda doc: doc.__setitem__("currentSupplementSha256s", []))

    out = verify(family)

    assert out.verified is False
    assert out.sub_reasons == (rg.SUPPLEMENT_NOT_CURRENTLY_NAMED,)


def test_an_absent_supplement_package_is_refused(family) -> None:
    shutil.rmtree(family["root"] / "nfl-labs-supplements" / "sha256" / SUPPLEMENT_ADDRESS)

    out = verify(family)

    assert out.verified is False
    assert out.sub_reasons == (rg.SUPPLEMENT_ABSENT,)


def test_a_supplement_that_fails_the_full_validator_is_refused(family) -> None:
    """Shallow presence is not enough; the deployed validator runs in full.

    Deleting one listed artifact leaves the package present and its address
    intact, so only the real validator catches it.
    """

    package = family["root"] / "nfl-labs-supplements" / "sha256" / SUPPLEMENT_ADDRESS
    (package / "supplement.json").write_text("{}", encoding="utf-8")

    out = verify(family)

    assert out.verified is False
    assert out.sub_reasons == (rg.SUPPLEMENT_ABSENT,)
    assert "does not verify" in out.detail


def test_a_checkpoint_that_selected_a_different_forecast_is_refused(family) -> None:
    out = verify(family, checkpoint_forecast_id="asp-sports-game-forecast:sha256:" + "d" * 64)

    assert out.verified is False
    assert out.sub_reasons == (rg.CHECKPOINT_FORECAST_MISMATCH,)

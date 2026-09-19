"""V18 market pilot: Model Studio's readable canonical originals, for FUTURE packets.

The exchange package AISP's importer read is ACL-restricted to elevated tokens,
so a sealed packet's ``forecast.objectRef`` / ``inputSnapshotRef`` /
``publicationReceiptRef`` and their duplicates in ``packet.sourceRefs`` resolve
to files the producer's own unelevated private reader cannot open. Model Studio
supplied the already-readable originals of the SAME bytes
(SOURCE_ACCESS_REPAIR_PROPOSAL.json, SHA-256
f69c0bf6aca205926bf707ccc8c665d89a86d711e07d1f34987fdbfbdb7e0572).

These tests pin what that repair may and may not do:

* the happy path -- the exact named object's originals, byte-identical, replace
  the blocked references everywhere they appear;
* a hash mismatch and a missing original keep the current behaviour and say why;
* a publication receipt failing ANY one condition (game, object, selected
  object, completed object, exposure present, exposure by the anchor, the
  packet's own handoff) is not bound;
* an already-sealed packet is never rebuilt, reopened or amended.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.research_exports import v18_market_pilot as pilot
from app.research_exports import v18_market_pilot_seal as seal
from tests.test_v18_market_pilot_export import DEADLINE, LOCK, World

EXPOSED = LOCK - timedelta(minutes=50)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Studio:
    """Model Studio's own outputs: the same object bytes, readable unelevated."""

    def __init__(self, world: World, root: Path) -> None:
        self.world = world
        self.root = root
        self.objects = root / "mlb-v18-current-state-pipeline-v1" / "objects" / "sha256"
        self.handoffs = root / "mlb-v18-current-state-pipeline-v1" / "handoffs" / "sha256"
        self.receipts = root / "mlb-v18-unattended-v1" / "objects" / "sha256"
        self.object_sha = world.forecast.lineage_detail["objectSha256"]
        self.handoff_sha = world.handoff_sha
        self.exchange_handoff = (world.root / "exchange" / "handoffs" / "sha256" / self.handoff_sha
                                 / pilot.CANONICAL_HANDOFF_FILE)
        self.object_dir = self.objects / self.object_sha
        shutil.copytree(world.object_root, self.object_dir)
        self.handoff_dir = self.handoffs / self.handoff_sha
        self.handoff_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.exchange_handoff, self.handoff_dir / pilot.CANONICAL_HANDOFF_FILE)

    # -- the producer's per-object atomic publication receipt -----------------
    def receipt(self, **overrides) -> Path:
        document = {
            "schemaVersion": pilot.PUBLICATION_RECEIPT_SCHEMA,
            "capturedAt": (EXPOSED - timedelta(minutes=9)).isoformat(),
            "forecastCreatedAt": (EXPOSED - timedelta(seconds=3)).isoformat(),
            "publicationCompletedAtUtc": (EXPOSED - timedelta(seconds=2)).isoformat(),
            "callbackConfirmedAtUtc": (EXPOSED + timedelta(seconds=1)).isoformat(),
            "packageExposedAtUtc": EXPOSED.isoformat(),
            "gameId": str(self.world.game.id),
            "objectSha256": self.object_sha,
            "selectedObjectSha256": self.object_sha,
            "completedObjectIncluded": True,
            "newInference": True,
            "promotionStatus": "NOT_PROMOTED",
            "deploymentIdentity": {"commit": "c" * 40, "manifestSha256": "d" * 64},
            "publicationClockMeaning": "Exposure is the actual atomic exchange-directory rename.",
            "handoff": {"logicalHandoffSha256": self.handoff_sha, "sha256": "e" * 64, "bytes": 19565},
        }
        document.update(overrides)
        data = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        folder = self.receipts / _sha(data)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / pilot.PUBLICATION_RECEIPT_FILE
        path.write_bytes(data)
        return path

    def inputs(self, *, objects: bool = True, handoffs: bool = True, receipts: bool = True
               ) -> pilot.BuildInputs:
        return pilot.BuildInputs(
            protocol=pilot.load_frozen_protocol(None), quarantine_ledger_path=self.world.ledger_path,
            raw_data_root=self.world.raw, mode=pilot.MODE_PROSPECTIVE,
            canonical_object_root=self.objects if objects else None,
            canonical_handoff_root=self.handoffs if handoffs else None,
            publication_receipt_root=self.receipts if receipts else None)

    def build(self, **kwargs) -> pilot.PacketBuild:
        game = pilot.read_game(self.world.session, self.world.game.id)
        ref = {"path": "attempt-ledger.jsonl", "sha256": "0" * 64, "bytes": 1, "locator": "seq=1"}
        return pilot.build_packet(self.world.session, game, inputs=self.inputs(**kwargs),
                                  built_at=LOCK + timedelta(minutes=1), attempt_ref=ref)

    def block_the_exchange(self) -> None:
        """What the elevated-token ACL looks like to this exporter: unreadable."""

        shutil.rmtree(self.world.object_root)
        self.exchange_handoff.unlink()


@pytest.fixture
def studio(session: Session, tmp_path: Path) -> Studio:
    return Studio(World(session, tmp_path), tmp_path / "asp-model-studio" / "outputs")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
def test_blocked_exchange_references_resolve_to_the_readable_canonical_originals(studio):
    receipt = studio.receipt()
    studio.block_the_exchange()
    build = studio.build()
    forecast = build.packet["forecast"]

    assert forecast["objectRef"]["path"] == str(studio.object_dir / "manifest.json")
    assert forecast["inputSnapshotRef"]["path"] == str(studio.object_dir / "pregame-evidence.json")
    assert forecast["scorePopulationRef"]["path"] == str(studio.object_dir / "execution-inputs.json")
    assert forecast["publicationReceiptRef"]["path"] == str(receipt)
    assert forecast["objectRefIsPhysical"] is True and forecast["scorePopulationRefIsPhysical"] is True

    # The digests are the object's own, read physically, not copied from a DB row.
    for name, child in (("objectRef", "manifest.json"), ("inputSnapshotRef", "pregame-evidence.json"),
                        ("scorePopulationRef", "execution-inputs.json")):
        data = (studio.object_dir / child).read_bytes()
        assert (forecast[name]["sha256"], forecast[name]["bytes"]) == (_sha(data), len(data))

    # The forecast is the one the packet already chose, and it is now eligible.
    assert forecast["forecastIdentity"] == studio.world.forecast.forecast_id
    assert build.packet["eligibility"]["forecastEligible"] is True
    assert "FORECAST_INTEGRITY_FAILED" not in build.packet["eligibility"]["reasonCodes"]
    assert build.schema_errors == []


def test_the_duplicate_references_in_source_refs_move_too(studio):
    receipt = studio.receipt()
    studio.block_the_exchange()
    build = studio.build()
    paths = {ref["path"] for ref in build.packet["sourceRefs"]}

    assert {str(studio.object_dir / name) for name in
            ("manifest.json", "pregame-evidence.json", "execution-inputs.json", "forecast.json")} <= paths
    assert str(receipt) in paths
    assert not [p for p in paths if "exchange" in p]          # nothing blocked is left behind


def test_the_readable_exchange_handoff_is_not_left_beside_the_receipt(studio):
    # The service that seals may itself be able to read the exchange; the
    # producer's reader cannot. When the receipt is bound it REPLACES the slate
    # handoff's duplicate reference rather than sitting next to it.
    receipt = studio.receipt()
    build = studio.build()
    paths = {ref["path"] for ref in build.packet["sourceRefs"]}

    assert build.packet["forecast"]["publicationReceiptRef"]["path"] == str(receipt)
    assert str(studio.exchange_handoff) not in paths
    assert str(receipt) in paths
    # The handoff's identity is still on the record, just not as a file ref.
    note = build.evidence["notes"]["exchangeFiles"]
    assert note["handoffPath"] == str(studio.exchange_handoff)
    assert build.evidence["notes"]["producerObjectVerification"]["handoffRegisteredSha256"] == (
        studio.handoff_sha)


def test_without_a_receipt_the_readable_exchange_handoff_is_still_the_reference(studio):
    build = studio.build(receipts=False)
    paths = {ref["path"] for ref in build.packet["sourceRefs"]}

    assert build.packet["forecast"]["publicationReceiptRef"]["path"] == str(studio.exchange_handoff)
    assert str(studio.exchange_handoff) in paths
    assert build.packet["forecast"]["operationalEvidence"]["referenceBasis"]["fields"][
        "forecast.publicationReceiptRef"] == pilot.REFERENCE_CLASS_EXCHANGE


def test_the_packet_says_which_class_every_reference_belongs_to(studio):
    studio.receipt()
    studio.block_the_exchange()
    build = studio.build()
    basis = build.packet["forecast"]["operationalEvidence"]["referenceBasis"]

    assert basis["proposalSha256"] == pilot.SOURCE_ACCESS_REPAIR_PROPOSAL_SHA256
    assert basis["fields"] == {
        "forecast.objectRef": pilot.REFERENCE_CLASS_CANONICAL,
        "forecast.inputSnapshotRef": pilot.REFERENCE_CLASS_CANONICAL,
        "forecast.scorePopulationRef": pilot.REFERENCE_CLASS_CANONICAL,
        "forecast.publicationReceiptRef": pilot.REFERENCE_CLASS_RECEIPT,
    }
    assert basis["canonicalOriginalsUsed"] is True
    note = build.evidence["notes"]["referenceBasis"]
    assert note["canonicalOriginals"]["handoffCopy"]["contentAddressOk"] is True
    assert note["atomicPublicationReceipt"]["bound"] is True
    # The frozen schema is untouched: new provenance lives in open objects only.
    assert build.schema_errors == []
    assert build.packet["selection"]["consumerExportRevision"] == pilot.EXPORT_REVISION
    assert pilot.EXPORT_REVISION == "R3-2026-09-19-MODEL-STUDIO-READABLE-CANONICAL-ORIGINALS"


def test_atomic_exposure_is_reported_as_its_own_clock_and_moves_nothing(studio):
    studio.receipt()
    studio.block_the_exchange()
    before = studio.build(objects=False, handoffs=False, receipts=False).packet["forecast"]
    after = studio.build().packet["forecast"]
    atomic = after["operationalEvidence"]["atomicPublicationReceipt"]

    assert atomic["bound"] is True and atomic["packageExposedAtUtc"] == pilot.iso(EXPOSED)
    assert atomic["receipt"]["publicationCompletedAtUtcLocalCompletion"] == pilot.iso(
        EXPOSED - timedelta(seconds=2))
    # Every clock the packet already carried is the same clock afterwards.
    for field in ("createdAtUtc", "aispImportTransactionStartedAtUtc", "publicationCompletedAtUtc",
                  "sourceCutoffUtc", "importReceiptCompletedAtUtc"):
        assert after[field] == before[field]
    assert after["publicationCompletedAtUtc"] != atomic["packageExposedAtUtc"]


# ---------------------------------------------------------------------------
# A hash mismatch, and a missing original
# ---------------------------------------------------------------------------
def test_a_canonical_copy_whose_bytes_differ_is_refused(studio):
    # The exchange copy is readable here, so its digests ARE the bound identity.
    (studio.object_dir / "pregame-evidence.json").write_bytes(b'{"contentSha256":"different"}')
    build = studio.build()
    forecast = build.packet["forecast"]
    basis = forecast["operationalEvidence"]["referenceBasis"]

    assert forecast["objectRef"]["path"] == str(studio.world.object_root / "manifest.json")
    assert forecast["inputSnapshotRef"]["path"] == str(
        studio.world.object_root / "pregame-evidence.json")
    assert basis["canonicalOriginalsUsed"] is False
    assert "LIMITATION_CANONICAL_ORIGINALS_NOT_PHYSICALLY_VERIFIED" in basis["reasonCodes"]
    assert build.packet["eligibility"]["forecastEligible"] is True      # unchanged behaviour


def test_a_canonical_copy_that_disagrees_with_the_bound_manifest_is_refused(studio, monkeypatch):
    # The canonical object verifies on its own terms but is NOT the same bytes
    # the packet already binds: refused, with the disagreement recorded.
    real = pilot.verify_producer_object
    canonical = studio.object_dir

    def verify(object_root, object_sha, handoff_path, aisp):
        result = real(object_root, object_sha, handoff_path, aisp)
        if Path(object_root) == canonical and result["objectRef"]:
            result["objectRef"] = {**result["objectRef"], "sha256": "f" * 64}
        return result

    monkeypatch.setattr(pilot, "verify_producer_object", verify)
    build = studio.build()
    basis = build.packet["forecast"]["operationalEvidence"]["referenceBasis"]
    note = build.evidence["notes"]["referenceBasis"]["canonicalOriginals"]

    assert basis["canonicalOriginalsUsed"] is False
    assert "LIMITATION_CANONICAL_ORIGINALS_DIGEST_DIFFERS_FROM_BOUND_IDENTITY" in basis["reasonCodes"]
    assert note["digestAgreementWithExchange"]["objectRef"] is False
    assert note["detail"]["digestDisagreements"]["objectRef"]["canonicalOriginal"]["sha256"] == "f" * 64
    assert build.packet["forecast"]["objectRef"]["path"] == str(studio.world.object_root / "manifest.json")


def test_a_missing_original_keeps_the_current_behaviour_and_never_picks_another_object(studio):
    other = studio.objects / ("a" * 64)                      # a readable object that is not ours
    shutil.copytree(studio.object_dir, other)
    shutil.rmtree(studio.object_dir)
    studio.block_the_exchange()
    build = studio.build()
    forecast = build.packet["forecast"]
    basis = forecast["operationalEvidence"]["referenceBasis"]

    assert basis["canonicalOriginalsUsed"] is False
    assert "LIMITATION_CANONICAL_ORIGINALS_OBJECT_DIRECTORY_MISSING" in basis["reasonCodes"]
    assert forecast["objectRefIsPhysical"] is False and forecast["scorePopulationRefIsPhysical"] is False
    assert forecast["forecastIdentity"] == studio.world.forecast.forecast_id
    assert build.packet["eligibility"]["forecastEligible"] is False
    assert build.packet["eligibility"]["firstFailure"] == "FORECAST_INTEGRITY_FAILED"
    assert other.is_dir()                                     # present, readable and never referenced
    assert str(other) not in json.dumps(build.packet)


def test_a_content_failure_in_the_exchange_object_is_never_read_away(studio):
    (studio.world.object_root / "execution-inputs.json").write_bytes(b'{"population":{}}')
    build = studio.build()
    basis = build.packet["forecast"]["operationalEvidence"]["referenceBasis"]

    assert basis["canonicalOriginalsUsed"] is False
    assert ("LIMITATION_CANONICAL_ORIGINALS_NOT_SUBSTITUTED_EXCHANGE_OBJECT_FAILED_ON_CONTENT"
            in basis["reasonCodes"])
    assert ("FORECAST_SCORE_POPULATION_NOT_PHYSICALLY_VERIFIED_DIGEST_DIFFERS_FROM_MANIFEST"
            in build.packet["eligibility"]["reasonCodes"])
    assert build.packet["eligibility"]["forecastEligible"] is False


def test_an_unverifiable_handoff_copy_stops_the_substitution(studio):
    (studio.handoff_dir / pilot.CANONICAL_HANDOFF_FILE).write_bytes(b'{"forecasts": []}')
    studio.block_the_exchange()
    build = studio.build()
    basis = build.packet["forecast"]["operationalEvidence"]["referenceBasis"]

    assert basis["canonicalOriginalsUsed"] is False
    assert ("LIMITATION_CANONICAL_ORIGINALS_HANDOFF_COPY_NOT_CONTENT_ADDRESS_VERIFIED"
            in basis["reasonCodes"])


def test_a_readable_exchange_handoff_can_still_name_the_members(studio):
    # The sealing service may be able to read the exchange even though the
    # producer's reader cannot. Either copy names the same members, so long as
    # its content address is the handoff SHA-256 AISP registered.
    shutil.rmtree(studio.handoff_dir)
    studio.receipt()
    build = studio.build()
    basis = build.packet["forecast"]["operationalEvidence"]["referenceBasis"]
    note = build.evidence["notes"]["referenceBasis"]["canonicalOriginals"]

    assert basis["canonicalOriginalsUsed"] is True
    assert note["handoffCopy"]["namesTakenFrom"] == "EXCHANGE_HANDOFF_CONTENT_ADDRESS_VERIFIED"
    assert basis["fields"]["forecast.objectRef"] == pilot.REFERENCE_CLASS_CANONICAL
    # Both copies were readable here, so the digests had to agree byte for byte.
    assert note["digestAgreementWithExchange"] == {"objectRef": True, "inputSnapshotRef": True,
                                                   "populationRef": True, "forecastFileRef": True}


# ---------------------------------------------------------------------------
# The publication receipt: every condition, one at a time
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("overrides, failure", [
    ({"gameId": "00000000-0000-0000-0000-000000000000"}, "RECEIPT_IS_FOR_A_DIFFERENT_GAME"),
    ({"objectSha256": "b" * 64}, None),                      # a receipt for another object is not ours
    ({"selectedObjectSha256": "b" * 64}, "RECEIPT_OBJECT_IS_NOT_THE_SELECTED_OBJECT"),
    ({"completedObjectIncluded": False}, "RECEIPT_COMPLETED_OBJECT_NOT_INCLUDED"),
    ({"packageExposedAtUtc": None}, "RECEIPT_PACKAGE_EXPOSED_AT_UTC_ABSENT_EXPOSURE_UNPROVED"),
    ({"packageExposedAtUtc": (LOCK + timedelta(minutes=2)).isoformat()},
     "RECEIPT_EXPOSURE_AFTER_THE_ANCHOR"),
    ({"handoff": {"logicalHandoffSha256": "b" * 64}}, "RECEIPT_DOES_NOT_BIND_THE_PACKETS_HANDOFF"),
    ({"schemaVersion": "SOMETHING_ELSE_V9"},
     "RECEIPT_SCHEMA_IS_NOT_THE_INCREMENTAL_PUBLICATION_SCHEMA"),
])
def test_a_receipt_failing_any_condition_is_not_bound(studio, overrides, failure):
    studio.receipt(**overrides)
    studio.block_the_exchange()
    build = studio.build()
    forecast = build.packet["forecast"]
    atomic = forecast["operationalEvidence"]["atomicPublicationReceipt"]
    note = build.evidence["notes"]["referenceBasis"]["atomicPublicationReceipt"]

    assert atomic["bound"] is False and atomic["packageExposedAtUtc"] is None
    if failure is None:
        assert "LIMITATION_PUBLICATION_RECEIPT_NOT_FOUND_FOR_THIS_OBJECT" in atomic["reasonCodes"]
        assert note["candidates"] == []
    else:
        assert "LIMITATION_PUBLICATION_RECEIPT_FOUND_BUT_CONDITIONS_NOT_MET" in atomic["reasonCodes"]
        assert failure in note["candidates"][0]["failures"]
    # The reference keeps exactly what it had: the consumer bundle row here,
    # because the exchange handoff is the blocked path.
    assert forecast["publicationReceiptRef"]["locator"].startswith("/rows/shadow_forecast_bundles/")
    assert forecast["operationalEvidence"]["referenceBasis"]["fields"][
        "forecast.publicationReceiptRef"] == pilot.REFERENCE_CLASS_EVIDENCE


def test_a_receipt_whose_file_is_not_its_content_address_is_not_bound(studio):
    path = studio.receipt()
    path.write_bytes(path.read_bytes() + b" ")
    studio.block_the_exchange()
    build = studio.build()
    note = build.evidence["notes"]["referenceBasis"]["atomicPublicationReceipt"]

    assert build.packet["forecast"]["operationalEvidence"]["atomicPublicationReceipt"]["bound"] is False
    assert "RECEIPT_FILE_IS_NOT_ITS_CONTENT_ADDRESS" in note["candidates"][0]["failures"]


def test_the_earliest_supported_exposure_wins(studio):
    late = studio.receipt(packageExposedAtUtc=(EXPOSED + timedelta(minutes=5)).isoformat())
    early = studio.receipt()
    studio.block_the_exchange()
    build = studio.build()
    atomic = build.packet["forecast"]["operationalEvidence"]["atomicPublicationReceipt"]

    assert build.packet["forecast"]["publicationReceiptRef"]["path"] == str(early)
    assert atomic["packageExposedAtUtc"] == pilot.iso(EXPOSED)
    assert str(late) != str(early)


def test_unconfigured_roots_are_inert(studio):
    studio.receipt()
    build = studio.build(objects=False, handoffs=False, receipts=False)
    forecast = build.packet["forecast"]
    basis = forecast["operationalEvidence"]["referenceBasis"]

    assert forecast["objectRef"]["path"] == str(studio.world.object_root / "manifest.json")
    assert basis["fields"]["forecast.objectRef"] == pilot.REFERENCE_CLASS_EXCHANGE
    assert "LIMITATION_CANONICAL_ORIGINALS_ROOT_NOT_CONFIGURED" in basis["reasonCodes"]
    assert "LIMITATION_PUBLICATION_RECEIPT_ROOT_NOT_CONFIGURED" in basis["reasonCodes"]
    assert build.packet["eligibility"]["forecastEligible"] is True


# ---------------------------------------------------------------------------
# Already-sealed packets
# ---------------------------------------------------------------------------
def test_an_already_sealed_packet_is_never_rebuilt_by_the_repair(studio):
    world = studio.world
    game = pilot.read_game(world.session, world.game.id)
    first = seal.seal_game(world.session, game, inputs=world.inputs(), directory=world.packets,
                           clock=lambda: LOCK + timedelta(minutes=1))
    assert first.sealed
    packet_file = world.packets / f"{first.episode_key}.json"
    before = packet_file.read_bytes()
    evidence_before = (world.packets / first.evidence_name).read_bytes()
    sealed = json.loads(before)
    assert sealed["forecast"]["objectRef"]["path"] == str(world.object_root / "manifest.json")

    # Now the repair is configured and the receipts exist: the sealed packet is
    # still refused, byte for byte, and no new evidence file appears beside it.
    studio.receipt()
    second = seal.seal_game(world.session, game, inputs=studio.inputs(), directory=world.packets,
                            clock=lambda: LOCK + timedelta(minutes=3))
    assert not second.sealed and second.code == "ALREADY_SEALED_NO_OVERWRITE"
    assert packet_file.read_bytes() == before
    assert (world.packets / first.evidence_name).read_bytes() == evidence_before
    assert sorted(p.name for p in world.packets.glob("*.evidence-*.json")) == [first.evidence_name]
    entries = seal.AttemptLedger(world.packets).entries()
    assert [e["kind"] for e in entries] == ["SEAL_ATTEMPT", "SEALED", "SEAL_REFUSED"]
    assert entries[-1]["code"] == "ALREADY_SEALED_NO_OVERWRITE"


def test_the_sealer_passes_the_configured_roots_and_a_missed_game_is_still_missed(studio, settings):
    world = studio.world
    on = settings.model_copy(update={
        "v18_market_pilot_export_enabled": True, "v18_market_pilot_packet_dir": str(world.packets),
        "v18_market_pilot_protocol_dir": "", "raw_data_root": world.raw,
        "v18_market_pilot_quarantine_ledger_path": str(world.ledger_path),
        "v18_market_pilot_canonical_object_root": str(studio.objects),
        "v18_market_pilot_canonical_handoff_root": str(studio.handoffs),
        "v18_market_pilot_publication_receipt_root": str(studio.receipts)})
    built = seal.build_inputs(on)
    assert (built.canonical_object_root, built.canonical_handoff_root,
            built.publication_receipt_root) == (studio.objects, studio.handoffs, studio.receipts)

    late = DEADLINE + timedelta(minutes=2)
    result = seal.run_seal_tick(world.session, on, now=late, clock=lambda: late)
    assert result["missed"] and not result["sealed"]
    assert not list(world.packets.glob("MLB-*.json"))


def test_defaults_point_at_model_studios_outputs(settings):
    for field, tail in (("v18_market_pilot_canonical_object_root",
                         ("mlb-v18-current-state-pipeline-v1", "objects", "sha256")),
                        ("v18_market_pilot_canonical_handoff_root",
                         ("mlb-v18-current-state-pipeline-v1", "handoffs", "sha256")),
                        ("v18_market_pilot_publication_receipt_root",
                         ("mlb-v18-unattended-v1", "objects", "sha256"))):
        assert Path(getattr(settings, field)).parts[-3:] == tail
        assert "asp-model-studio" in getattr(settings, field)


def test_no_outcome_field_survives_the_new_provenance(studio):
    studio.receipt()
    studio.block_the_exchange()
    build = studio.build()
    pilot.assert_no_outcome_fields(build.packet, "packet")
    pilot.assert_no_outcome_fields(build.evidence, "evidence")
    for document in (build.packet, build.evidence):
        assert '"score"' not in json.dumps(document) and '"result"' not in json.dumps(document)

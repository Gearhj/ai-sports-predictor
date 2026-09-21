"""The NFL FINAL readiness joins required by Model Studio's frozen contract.

WHAT THIS ANSWERS
-----------------
"Physical forecast bytes without all required positive evidence are not
consumer readiness." A FINAL_PREGAME forecast row can exist in our database --
imported, well-formed, available before the cutoff -- and still not be
something an official research position may be controlled by, because the
producer publishes the forecast, its grant, its locator and its readiness proof
as FOUR SEPARATE filesystem operations and not as one atomic transaction. Any
one of them can be missing, late, or superseded while the other three look
perfect.

This module re-reads the exchange from disk and answers one question: does the
exact forecast that the checkpoint selected carry complete, positive, current
readiness evidence by this game's consumer cutoff? It returns a verdict and its
evidence. It decides nothing about price, stake or position.

THE ONE SEMANTIC THAT IS EASIEST TO GET WRONG
---------------------------------------------
``PAIR_AND_READY_INDEX_TIMELY`` certifies that the exact pair and the ready
index were observed by T-21. It does NOT certify that the sidecar carrying that
proof became readable by T-21, and the contract says so explicitly. So a
sidecar first observed after T-21 is a TELEMETRY DELAY, not evidence that the
pair missed the producer boundary -- treating it as a failure would refuse a
genuinely timely publication because its receipt was slow to appear.

What the consumer must establish for ITSELF is its own actual observation and
import by T-15. Those are different clocks with different owners, and this
module keeps them apart by construction: ``_producer_boundary`` never reads a
consumer time and ``_consumer_cutoff`` never reads a producer one.

WHAT IT MAY NOT DO
------------------
It calls no provider, writes no record, and grants nothing. It reads files.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.forecast_exchange.importer import (
    ForecastBundleImportError, verify_and_load_sports_bundle,
)
from app.gen2.exchange.canonical_json import canonical_json_bytes

READINESS_ROOT = "nfl-final-readiness"
READINESS_INDEX_FILENAME = "current.json"
OBJECTS_DIR = "objects/sha256"
AVAILABILITY_DIR = "availability"

INDEX_SCHEMA = "NFL_FINAL_READINESS_INDEX_V1"
RECEIPT_SCHEMA = "NFL_FINAL_READY_RECEIPT_V1"
EXPOSURE_SCHEMA = "NFL_FINAL_READY_EXPOSURE_V1"

#: The ONLY positive exposure status. Everything else -- including
#: ``LATE_NOT_READY``, which the contract names as never positive evidence --
#: fails closed.
STATUS_TIMELY = "PAIR_AND_READY_INDEX_TIMELY"

#: The producer's hard publication boundary and the consumer's decision cutoff,
#: as offsets from kickoff. They are deliberately separate constants: the whole
#: point of the contract's timing section is that these are two different
#: obligations owned by two different parties.
PRODUCER_BOUNDARY = timedelta(minutes=21)
CONSUMER_CUTOFF = timedelta(minutes=15)

FORECAST_FAMILY = "aisp-daily-forecast-bundles"
FORECAST_INDEX_FILENAME = "nfl-2026-research-shadow-index.json"
SUPPLEMENT_FAMILY = "nfl-labs-supplements"
SUPPLEMENT_INDEX_FILENAME = "nfl-labs-supplement-index.json"
SUPPLEMENT_INDEX_SCHEMA = "AISP_NFL_LABS_SUPPLEMENT_INDEX_V1"

#: The single reason the decision owner records when readiness does not verify.
#: Every specific cause below travels with it as a sub-reason, so the record
#: says both "this was blocked for readiness" and exactly which join failed.
REASON = "NO_POSITIVE_READINESS_EVIDENCE"

# --- sub-reasons, grouped by the join that produces them -------------------

# Join 1: physical reads, content addresses, identity, positive status.
READINESS_INDEX_ABSENT = "READINESS_INDEX_ABSENT"
READINESS_INDEX_MALFORMED = "READINESS_INDEX_MALFORMED"
READINESS_INDEX_NO_ENTRY = "READINESS_INDEX_NO_ENTRY"
READINESS_INDEX_AMBIGUOUS_ENTRY = "READINESS_INDEX_AMBIGUOUS_ENTRY"
RECEIPT_ABSENT = "RECEIPT_ABSENT"
RECEIPT_ADDRESS_MISMATCH = "RECEIPT_ADDRESS_MISMATCH"
RECEIPT_MALFORMED = "RECEIPT_MALFORMED"
RECEIPT_IDENTITY_MISMATCH = "RECEIPT_IDENTITY_MISMATCH"
#: The receipt exists and is well formed, but was not written by the reviewed
#: operational-V2 producer revision. Distinct from a malformed receipt on
#: purpose: this one is authentic evidence from the WRONG producer.
PRODUCER_REVISION_NOT_PINNED = "PRODUCER_REVISION_NOT_PINNED"
#: The receipt declares a schema this consumer was not written against.
RECEIPT_SCHEMA_MISMATCH = "RECEIPT_SCHEMA_MISMATCH"
#: A schema hash is pinned but the receipt declares none under any field name we
#: read. That is a REFUSAL, not a pass: an unreadable schema declaration under a
#: live pin means we cannot tell whether the receipt has the reviewed shape, and
#: the contract's rule for absent evidence is fail closed.
RECEIPT_SCHEMA_ABSENT = "RECEIPT_SCHEMA_ABSENT"

#: Where a receipt may declare its own schema hash, most specific first.
#:
#: The producer contract names the receipt schema id (``NFL_FINAL_READY_RECEIPT_V1``)
#: but does not state the FIELD that carries its hash, so these three spellings
#: are our reading and must be reconciled against the first authentic receipt.
#: Reading a set rather than one name keeps a rename-only correction cheap; it
#: does NOT weaken the pin, because a receipt declaring nothing under any of them
#: is refused outright while a pin is configured.
SCHEMA_SHA256_FIELDS: tuple[str, ...] = (
    "schemaSha256", "receiptSchemaSha256", "schemaHashSha256",
)

EXPOSURE_REFERENCE_ABSENT = "EXPOSURE_REFERENCE_ABSENT"
EXPOSURE_REFERENCE_MALFORMED = "EXPOSURE_REFERENCE_MALFORMED"
EXPOSURE_REFERENCE_NOT_BOUND = "EXPOSURE_REFERENCE_NOT_BOUND"
EXPOSURE_ABSENT = "EXPOSURE_ABSENT"
EXPOSURE_ADDRESS_MISMATCH = "EXPOSURE_ADDRESS_MISMATCH"
EXPOSURE_MALFORMED = "EXPOSURE_MALFORMED"
EXPOSURE_NOT_BOUND_TO_RECEIPT = "EXPOSURE_NOT_BOUND_TO_RECEIPT"
EXPOSURE_STATUS_NOT_POSITIVE = "EXPOSURE_STATUS_NOT_POSITIVE"

# Join 2: the immutable snapshots.
READY_INDEX_SNAPSHOT_MISMATCH = "READY_INDEX_SNAPSHOT_MISMATCH"
READY_INDEX_SNAPSHOT_ENTRY_MISSING = "READY_INDEX_SNAPSHOT_ENTRY_MISSING"
FORECAST_SNAPSHOT_MEMBERSHIP_MISSING = "FORECAST_SNAPSHOT_MEMBERSHIP_MISSING"
SUPPLEMENT_SNAPSHOT_MEMBERSHIP_MISSING = "SUPPLEMENT_SNAPSHOT_MEMBERSHIP_MISSING"

# Join 3: present-tense discovery.
FORECAST_DISCOVERY_ABSENT = "FORECAST_DISCOVERY_ABSENT"
FORECAST_DISCOVERY_MALFORMED = "FORECAST_DISCOVERY_MALFORMED"
FORECAST_WRAPPER_NOT_VERIFIED = "FORECAST_WRAPPER_NOT_VERIFIED"
FORECAST_NOT_CURRENT = "FORECAST_NOT_CURRENT"
FORECAST_AMBIGUOUS_IN_CURRENT = "FORECAST_AMBIGUOUS_IN_CURRENT"
FORECAST_BYTES_CHANGED = "FORECAST_BYTES_CHANGED"
GRANT_PROVENANCE_UNREADABLE = "GRANT_PROVENANCE_UNREADABLE"
GRANT_TRANSFER_REFUSED = "GRANT_TRANSFER_REFUSED"
SUPPLEMENT_DISCOVERY_ABSENT = "SUPPLEMENT_DISCOVERY_ABSENT"
SUPPLEMENT_ABSENT = "SUPPLEMENT_ABSENT"
SUPPLEMENT_NOT_CURRENTLY_NAMED = "SUPPLEMENT_NOT_CURRENTLY_NAMED"
DISCOVERY_CHANGED_DURING_VERIFICATION = "DISCOVERY_CHANGED_DURING_VERIFICATION"

# Join 4: the two clocks.
PRODUCER_BOUNDARY_MISSED = "PRODUCER_BOUNDARY_MISSED"
CONSUMER_OBSERVATION_LATE = "CONSUMER_OBSERVATION_LATE"
PACKAGE_IMPORT_LATE = "PACKAGE_IMPORT_LATE"
GRANT_IMPORT_LATE = "GRANT_IMPORT_LATE"
PACKAGE_IMPORT_UNKNOWN = "PACKAGE_IMPORT_UNKNOWN"
CHECKPOINT_FORECAST_MISMATCH = "CHECKPOINT_FORECAST_MISMATCH"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ReadinessVerification:
    """One verdict, its causes, and the evidence that produced it."""

    verified: bool
    forecast_id: str
    reason: str | None = None
    sub_reasons: tuple[str, ...] = ()
    detail: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_record(self) -> dict[str, Any]:
        """The JSON-safe shape the decision record stores."""

        return {
            "verified": self.verified,
            "forecast_id": self.forecast_id,
            "reason": self.reason,
            "sub_reasons": list(self.sub_reasons),
            "detail": self.detail,
            **self.evidence,
        }


class _Fail(Exception):
    """Internal control flow: one named fail-closed condition."""

    def __init__(self, sub_reason: str, detail: str) -> None:
        super().__init__(detail)
        self.sub_reason = sub_reason
        self.detail = detail


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _parse_time(value: Any, *, label: str, sub_reason: str) -> datetime:
    try:
        return _aware(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except (TypeError, ValueError) as exc:
        raise _Fail(sub_reason, f"{label} is not a readable timestamp") from exc


def _read_json(path: Path, *, label: str, absent: str, malformed: str) -> tuple[bytes, dict]:
    """Physically read one document, then require canonical JSON bytes.

    Reading the BYTES first and hashing those, rather than re-serialising a
    parsed object, is the difference between verifying what is on disk and
    verifying what we happen to have decoded.
    """

    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise _Fail(absent, f"{label} is not physically readable at {path}") from exc
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _Fail(malformed, f"{label} is not JSON") from exc
    if not isinstance(document, dict):
        raise _Fail(malformed, f"{label} is not a JSON object")
    if raw != canonical_json_bytes(document):
        raise _Fail(malformed, f"{label} is not canonical exchange JSON")
    return raw, document


def _require_address(raw: bytes, expected: str, *, label: str, sub_reason: str) -> str:
    digest = hashlib.sha256(raw).hexdigest()
    if not _SHA256.fullmatch(str(expected or "")):
        raise _Fail(sub_reason, f"{label} address {expected!r} is not a sha256 address")
    if digest != expected:
        raise _Fail(
            sub_reason,
            f"{label} bytes hash to {digest} but are stored at {expected}",
        )
    return digest


# ---------------------------------------------------------------------------
# join 1 -- physical reads, content addresses, unique identity, positive status
# ---------------------------------------------------------------------------

def _readiness_entry(root: Path, forecast_id: str) -> dict[str, Any]:
    path = root / READINESS_ROOT / READINESS_INDEX_FILENAME
    _, index = _read_json(
        path, label="readiness index",
        absent=READINESS_INDEX_ABSENT, malformed=READINESS_INDEX_MALFORMED)
    if index.get("schemaVersion") != INDEX_SCHEMA:
        raise _Fail(READINESS_INDEX_MALFORMED,
                    f"readiness index declares {index.get('schemaVersion')!r}")
    entries = index.get("entries")
    if not isinstance(entries, list):
        raise _Fail(READINESS_INDEX_MALFORMED, "readiness index has no entry list")
    matches = [item for item in entries
               if isinstance(item, dict) and item.get("forecastId") == forecast_id]
    if not matches:
        raise _Fail(READINESS_INDEX_NO_ENTRY,
                    f"the ready index does not map {forecast_id}")
    if len(matches) > 1:
        # Ambiguous identity is fail-closed, not "take the first".
        raise _Fail(READINESS_INDEX_AMBIGUOUS_ENTRY,
                    f"the ready index maps {forecast_id} {len(matches)} times")
    return matches[0]


def _receipt(root: Path, entry: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    receipt_sha = str(entry.get("receiptSha256") or "")
    ref = str(entry.get("receiptRef") or "")
    expected_ref = f"{OBJECTS_DIR}/{receipt_sha}/receipt.json"
    if ref != expected_ref:
        raise _Fail(RECEIPT_ADDRESS_MISMATCH,
                    f"receiptRef {ref!r} does not name the declared address")
    raw, receipt = _read_json(
        root / READINESS_ROOT / OBJECTS_DIR / receipt_sha / "receipt.json",
        label="base receipt", absent=RECEIPT_ABSENT, malformed=RECEIPT_MALFORMED)
    _require_address(raw, receipt_sha, label="base receipt",
                     sub_reason=RECEIPT_ADDRESS_MISMATCH)
    if receipt.get("schemaVersion") != RECEIPT_SCHEMA:
        raise _Fail(RECEIPT_MALFORMED,
                    f"base receipt declares {receipt.get('schemaVersion')!r}")
    return receipt_sha, receipt


def _exposure(root: Path, entry: dict[str, Any], receipt_sha: str) -> dict[str, Any]:
    """The availability sidecar, then the exposure object it references.

    Absence of the sidecar is fail closed and there is no fallback. That is the
    contract's own wording, and it is the reason this function never reaches
    for an exposure object by guessing a path.
    """

    reference_path = str(entry.get("exposureReferencePath") or "")
    if reference_path != f"{AVAILABILITY_DIR}/{receipt_sha}.json":
        raise _Fail(EXPOSURE_REFERENCE_MALFORMED,
                    f"exposureReferencePath {reference_path!r} is not the receipt's sidecar")
    raw_ref, reference = _read_json(
        root / READINESS_ROOT / AVAILABILITY_DIR / f"{receipt_sha}.json",
        label="exposure reference", absent=EXPOSURE_REFERENCE_ABSENT,
        malformed=EXPOSURE_REFERENCE_MALFORMED)
    if reference.get("baseReceiptSha256") != receipt_sha:
        raise _Fail(EXPOSURE_REFERENCE_NOT_BOUND,
                    "the exposure reference does not bind its base receipt")
    exposure_sha = str(reference.get("exposureSha256") or "")
    if reference.get("exposureObjectPath") != f"{OBJECTS_DIR}/{exposure_sha}/exposure.json":
        raise _Fail(EXPOSURE_REFERENCE_MALFORMED,
                    "the exposure reference path does not match its declared address")
    raw, exposure = _read_json(
        root / READINESS_ROOT / OBJECTS_DIR / exposure_sha / "exposure.json",
        label="exposure object", absent=EXPOSURE_ABSENT, malformed=EXPOSURE_MALFORMED)
    _require_address(raw, exposure_sha, label="exposure object",
                     sub_reason=EXPOSURE_ADDRESS_MISMATCH)
    if exposure.get("schemaVersion") != EXPOSURE_SCHEMA:
        raise _Fail(EXPOSURE_MALFORMED,
                    f"exposure object declares {exposure.get('schemaVersion')!r}")
    if exposure.get("baseReceiptSha256") != receipt_sha:
        raise _Fail(EXPOSURE_NOT_BOUND_TO_RECEIPT,
                    "the exposure object names a different base receipt")
    del raw_ref
    return exposure


def _declared_schema_sha256(receipt: dict[str, Any]) -> str | None:
    """The receipt's own schema hash, lowercased, or None if it declares none."""

    for name in SCHEMA_SHA256_FIELDS:
        value = str(receipt.get(name) or "").strip().lower()
        if value:
            return value
    return None


def _require_identity(receipt: dict[str, Any], exposure: dict[str, Any], *,
                      forecast_id: str, event_id: str, snapshot_type: str,
                      snapshot_version: int | None, engine_id: str,
                      required_producer_revision: str | None = None,
                      required_schema_sha256: str | None = None) -> None:
    """One exact game/forecast/snapshot/model/producer identity, or nothing.

    ``required_producer_revision`` PINS the revision. The contract distinguishes
    "the activated operational-V2 producer revision" from the current one but
    does not state the literal value, so until it is supplied the check can only
    require a non-empty string -- which an OLD-revision receipt would satisfy.
    Once pinned, a receipt from any other revision is refused outright, which is
    the difference between "a receipt exists" and "the reviewed producer wrote
    it".

    ``required_schema_sha256`` pins the receipt schema the producer emits, so a
    reshaped receipt cannot pass a join written against the older shape.
    """

    declared = {
        "forecastId": (receipt.get("forecastId"), forecast_id),
        "eventId": (receipt.get("eventId"), event_id),
        "snapshotType": (receipt.get("snapshotType"), snapshot_type),
        "engineId": (receipt.get("engineId"), engine_id),
    }
    for label, (actual, expected) in declared.items():
        if actual != expected:
            raise _Fail(RECEIPT_IDENTITY_MISMATCH,
                        f"receipt {label} is {actual!r}, the checkpoint selected {expected!r}")
    if snapshot_version is not None and receipt.get("snapshotVersion") != snapshot_version:
        raise _Fail(RECEIPT_IDENTITY_MISMATCH,
                    f"receipt snapshotVersion is {receipt.get('snapshotVersion')!r}, "
                    f"the checkpoint selected {snapshot_version!r}")
    revision = str(receipt.get("producerRevision") or "").strip()
    if not revision:
        raise _Fail(RECEIPT_IDENTITY_MISMATCH, "receipt names no producer revision")
    if required_producer_revision:
        if revision != required_producer_revision:
            raise _Fail(PRODUCER_REVISION_NOT_PINNED,
                        f"receipt producerRevision is {revision!r}; the reviewed "
                        f"operational-V2 revision is {required_producer_revision!r}")
    if required_schema_sha256:
        declared_schema = _declared_schema_sha256(receipt)
        if declared_schema is None:
            raise _Fail(RECEIPT_SCHEMA_ABSENT,
                        "a schema hash is pinned but the receipt declares none under "
                        f"any of {list(SCHEMA_SHA256_FIELDS)}")
        if declared_schema != required_schema_sha256.strip().lower():
            raise _Fail(RECEIPT_SCHEMA_MISMATCH,
                        f"receipt schema hash is {declared_schema!r}; the reviewed "
                        f"schema is {required_schema_sha256.strip().lower()!r}")
    if exposure.get("forecastId") != forecast_id:
        raise _Fail(RECEIPT_IDENTITY_MISMATCH,
                    "the exposure object names a different forecast")


def _require_positive_status(exposure: dict[str, Any]) -> None:
    status = exposure.get("exposureStatus")
    if status != STATUS_TIMELY:
        raise _Fail(EXPOSURE_STATUS_NOT_POSITIVE,
                    f"exposure status {status!r} is not positive evidence")


# ---------------------------------------------------------------------------
# join 2 -- the immutable snapshots
# ---------------------------------------------------------------------------

def _verify_snapshots(receipt: dict[str, Any], exposure: dict[str, Any], *,
                      forecast_id: str, bundle_sha: str, supplement_sha: str) -> None:
    """Match the exposure to its ORIGINAL ready-index snapshot and receipt.

    Every check here reads the snapshots the receipt and exposure CARRY. The
    mutable discovery files on disk are not consulted -- they are the present
    tense and are checked separately in join 3. A snapshot is a historical hash
    reference; a discovery path is not.
    """

    receipt_snapshot = receipt.get("readyIndexSnapshot")
    exposure_snapshot = exposure.get("readyIndexSnapshot")
    if not isinstance(receipt_snapshot, dict) or not isinstance(exposure_snapshot, dict):
        raise _Fail(READY_INDEX_SNAPSHOT_MISMATCH, "a ready-index snapshot is missing")
    if canonical_json_bytes(receipt_snapshot) != canonical_json_bytes(exposure_snapshot):
        raise _Fail(READY_INDEX_SNAPSHOT_MISMATCH,
                    "the exposure's ready-index snapshot differs from the receipt's")
    entries = receipt_snapshot.get("entries")
    if not isinstance(entries, list) or not any(
            isinstance(item, dict) and item.get("forecastId") == forecast_id
            for item in entries):
        raise _Fail(READY_INDEX_SNAPSHOT_ENTRY_MISSING,
                    "the original ready-index snapshot has no entry for this forecast")

    forecast_snapshot = receipt.get("forecastIndexSnapshot")
    if not isinstance(forecast_snapshot, dict):
        raise _Fail(FORECAST_SNAPSHOT_MEMBERSHIP_MISSING,
                    "the receipt carries no forecast-index snapshot")
    current = forecast_snapshot.get("currentBundleSha256s")
    if not isinstance(current, list) or bundle_sha not in current:
        raise _Fail(FORECAST_SNAPSHOT_MEMBERSHIP_MISSING,
                    f"bundle {bundle_sha} was not current in the original forecast snapshot")

    supplement_snapshot = receipt.get("supplementIndexSnapshot")
    if not isinstance(supplement_snapshot, dict):
        raise _Fail(SUPPLEMENT_SNAPSHOT_MEMBERSHIP_MISSING,
                    "the receipt carries no supplement-index snapshot")
    named = supplement_snapshot.get("currentSupplementSha256s")
    if not isinstance(named, list) or supplement_sha not in named:
        raise _Fail(SUPPLEMENT_SNAPSHOT_MEMBERSHIP_MISSING,
                    f"supplement {supplement_sha} was not named in the original snapshot")


# ---------------------------------------------------------------------------
# join 3 -- present-tense discovery, verified inventories, unique membership
# ---------------------------------------------------------------------------

def _discovery_fingerprint(root: Path) -> tuple[bytes, bytes]:
    """The exact bytes of both mutable locators, for the concurrency re-check."""

    def read(path: Path) -> bytes:
        try:
            return path.read_bytes()
        except OSError:
            return b""

    return (
        read(root / FORECAST_FAMILY / FORECAST_INDEX_FILENAME),
        read(root / SUPPLEMENT_FAMILY / SUPPLEMENT_INDEX_FILENAME),
    )


def _current_bundle_addresses(root: Path) -> list[str]:
    raw, index = _read_json(
        root / FORECAST_FAMILY / FORECAST_INDEX_FILENAME,
        label="forecast discovery index", absent=FORECAST_DISCOVERY_ABSENT,
        malformed=FORECAST_DISCOVERY_MALFORMED)
    del raw
    schema = index.get("schemaVersion")
    if schema == "ASP_NFL_2026_RESEARCH_SHADOW_INDEX_V2":
        addresses = index.get("currentBundleSha256s")
    elif schema == "ASP_NFL_2026_RESEARCH_SHADOW_INDEX_V1":
        addresses = index.get("bundleSha256s")
    else:
        raise _Fail(FORECAST_DISCOVERY_MALFORMED,
                    f"forecast discovery declares {schema!r}")
    if not isinstance(addresses, list) or not all(
            isinstance(value, str) and _SHA256.fullmatch(value) for value in addresses):
        raise _Fail(FORECAST_DISCOVERY_MALFORMED,
                    "forecast discovery does not list sha256 bundle addresses")
    return list(addresses)


def _child_bytes(bundle_dir: Path, forecast_id: str) -> bytes | None:
    """The canonical bytes of one named child inside a physical wrapper."""

    try:
        bundle = json.loads((bundle_dir / "bundle.json").read_bytes())
    except (OSError, json.JSONDecodeError):
        return None
    children = bundle.get("forecasts") if isinstance(bundle, dict) else None
    if not isinstance(children, list):
        return None
    found = [child for child in children
             if isinstance(child, dict) and child.get("forecastId") == forecast_id]
    if len(found) != 1:
        return None
    return canonical_json_bytes(found[0])


def _verify_current_membership(root: Path, *, forecast_id: str,
                               receipt_bundle_sha: str) -> dict[str, Any]:
    """Require the exact forecast bytes to occur UNIQUELY in a VERIFIED wrapper.

    "Verified" is the deployed bundle verifier, not a looser local reading of
    it: a wrapper that would be refused at import may not supply a forecast that
    controls a position. Ordinarily the current wrapper is the receipt's own
    one-game bundle and the two agree trivially. When an unrelated child forced
    a recontainer, the receipt's ORIGINAL bundle is retained as the historical
    grant provenance -- read physically, not assumed -- and the new wrapper must
    carry a BYTE-IDENTICAL copy of this child. That is what makes a recontainer
    survivable without ever moving a grant onto a different child.
    """

    addresses = _current_bundle_addresses(root)
    family = root / FORECAST_FAMILY / "sha256"
    hosts: list[str] = []
    child_bytes: bytes | None = None
    unverified: list[str] = []
    for address in addresses:
        directory = family / address
        candidate = _child_bytes(directory, forecast_id)
        if candidate is None:
            continue
        try:
            verify_and_load_sports_bundle(directory)
        except (ForecastBundleImportError, OSError) as exc:
            unverified.append(f"{address}: {exc}")
            continue
        hosts.append(address)
        child_bytes = candidate

    if not hosts:
        if unverified:
            raise _Fail(FORECAST_WRAPPER_NOT_VERIFIED,
                        "the only current wrapper carrying this forecast does not "
                        f"verify ({unverified[0]})")
        # Absent or superseded. An old readiness receipt does not rescue it.
        raise _Fail(FORECAST_NOT_CURRENT,
                    f"{forecast_id} is not a child of any current wrapper; an "
                    "immutable readiness receipt does not make a superseded or "
                    "absent forecast current")
    if len(hosts) > 1:
        raise _Fail(FORECAST_AMBIGUOUS_IN_CURRENT,
                    f"{forecast_id} occurs in {len(hosts)} current wrappers")

    provenance = family / receipt_bundle_sha
    original = _child_bytes(provenance, forecast_id)
    if original is None:
        raise _Fail(GRANT_PROVENANCE_UNREADABLE,
                    "the receipt's original bundle is not physically readable, so "
                    "the grant has no retained provenance")
    if original != child_bytes:
        raise _Fail(FORECAST_BYTES_CHANGED,
                    "the current wrapper's child bytes differ from the bundle the "
                    "readiness receipt granted")
    return {"current_bundle_sha256": hosts[0],
            "grant_provenance_bundle_sha256": receipt_bundle_sha,
            "recontainered": hosts[0] != receipt_bundle_sha}


def _verify_grant_currently_named(root: Path, *, supplement_sha: str,
                                  forecast_id: str) -> None:
    """The exact supplement must be named NOW, and must bind THIS child."""

    _, index = _read_json(
        root / SUPPLEMENT_FAMILY / SUPPLEMENT_INDEX_FILENAME,
        label="supplement discovery index", absent=SUPPLEMENT_DISCOVERY_ABSENT,
        malformed=SUPPLEMENT_DISCOVERY_ABSENT)
    if index.get("schemaVersion") != SUPPLEMENT_INDEX_SCHEMA:
        raise _Fail(SUPPLEMENT_DISCOVERY_ABSENT,
                    f"supplement discovery declares {index.get('schemaVersion')!r}")
    named = index.get("currentSupplementSha256s")
    if not isinstance(named, list) or supplement_sha not in named:
        raise _Fail(SUPPLEMENT_NOT_CURRENTLY_NAMED,
                    f"supplement {supplement_sha} is not currently named")
    directory = root / SUPPLEMENT_FAMILY / "sha256" / supplement_sha
    raw, supplement = _read_json(
        directory / "supplement.json", label="supplement",
        absent=SUPPLEMENT_ABSENT, malformed=SUPPLEMENT_ABSENT)
    _require_address(raw, supplement_sha, label="supplement",
                     sub_reason=SUPPLEMENT_ABSENT)
    if supplement.get("forecastId") != forecast_id:
        # A grant is never transferred to a different child.
        raise _Fail(GRANT_TRANSFER_REFUSED,
                    f"supplement {supplement_sha} grants "
                    f"{supplement.get('forecastId')!r}, not {forecast_id}")


# ---------------------------------------------------------------------------
# join 4 -- the two clocks, kept apart
# ---------------------------------------------------------------------------

def _producer_boundary(receipt: dict[str, Any], *, kickoff: datetime) -> dict[str, Any]:
    """The PRODUCER's obligation: the pair and index observed by T-21.

    This function reads producer observations only. The sidecar's own first
    observation is deliberately NOT consulted here: the contract states that
    ``PAIR_AND_READY_INDEX_TIMELY`` does not certify sidecar readability, and
    that late proof observation is a telemetry delay rather than a missed
    boundary. Folding the sidecar clock in here would turn a timely publication
    into a false failure.
    """

    deadline = _parse_time(
        receipt.get("producerDeadlineUtc"), label="producerDeadlineUtc",
        sub_reason=RECEIPT_MALFORMED)
    expected = kickoff - PRODUCER_BOUNDARY
    if deadline != expected:
        raise _Fail(RECEIPT_MALFORMED,
                    f"the receipt's T-21 deadline {deadline.isoformat()} is not this "
                    f"game's boundary {expected.isoformat()}")
    grant_seen = _parse_time(receipt.get("grantObservedAtUtc"),
                             label="grantObservedAtUtc", sub_reason=RECEIPT_MALFORMED)
    index_seen = _parse_time(receipt.get("currentIndexObservedAtUtc"),
                             label="currentIndexObservedAtUtc", sub_reason=RECEIPT_MALFORMED)
    late = [name for name, moment in
            (("grant", grant_seen), ("ready index", index_seen)) if moment > deadline]
    if late:
        raise _Fail(PRODUCER_BOUNDARY_MISSED,
                    f"the producer observed the {' and '.join(late)} after T-21")
    return {"producer_deadline_utc": deadline.isoformat(),
            "grant_observed_at_utc": grant_seen.isoformat(),
            "ready_index_observed_at_utc": index_seen.isoformat()}


def _consumer_cutoff(exposure: dict[str, Any], *, kickoff: datetime,
                     observed_at: datetime, package_imported_at: datetime | None,
                     grant_imported_at: datetime | None,
                     require_package_import: bool) -> dict[str, Any]:
    """The CONSUMER's own obligation: genuine observation and import by T-15.

    ``observed_at`` is when THIS run physically read the sidecar. It is not
    ``availableAsOfUtc`` (numerical completion, not visibility), not the
    producer's ready-index observation, and not any declared generation clock.
    Those three are recorded, and none of them can satisfy this test.
    """

    cutoff = kickoff - CONSUMER_CUTOFF
    observed = _aware(observed_at)
    if observed > cutoff:
        raise _Fail(CONSUMER_OBSERVATION_LATE,
                    f"this consumer first observed the readiness sidecar at "
                    f"{observed.isoformat()}, after its T-15 cutoff {cutoff.isoformat()}")
    if package_imported_at is None:
        if require_package_import:
            raise _Fail(PACKAGE_IMPORT_UNKNOWN,
                        "no actual forecast-package import time is recorded")
    elif _aware(package_imported_at) > cutoff:
        raise _Fail(PACKAGE_IMPORT_LATE,
                    f"the forecast package was imported at "
                    f"{_aware(package_imported_at).isoformat()}, after T-15")
    if grant_imported_at is not None and _aware(grant_imported_at) > cutoff:
        raise _Fail(GRANT_IMPORT_LATE,
                    f"the grant was imported at {_aware(grant_imported_at).isoformat()}, "
                    "after T-15")
    return {
        "consumer_cutoff_utc": cutoff.isoformat(),
        "consumer_observed_at_utc": observed.isoformat(),
        "package_imported_at_utc": (
            _aware(package_imported_at).isoformat() if package_imported_at else None),
        "grant_imported_at_utc": (
            _aware(grant_imported_at).isoformat() if grant_imported_at else None),
        # Recorded, never used as a pass/fail input. See _producer_boundary.
        "sidecar_first_observed_at_utc": exposure.get("sidecarFirstObservedAtUtc"),
        "proof_observed_after_producer_boundary": bool(
            exposure.get("proofObservedAfterProducerBoundary")),
        "consumer_cutoff_passed_at_proof_observation": bool(
            exposure.get("consumerCutoffPassedAtProofObservation")),
        "ready_available_at_utc": exposure.get("readyAvailableAtUtc"),
    }


# ---------------------------------------------------------------------------
# the entry point
# ---------------------------------------------------------------------------

def verify_final_readiness(
    *,
    exchange_root: Path | str,
    forecast_id: str,
    event_id: str,
    kickoff_utc: datetime,
    observed_at: datetime,
    checkpoint_forecast_id: str | None = None,
    snapshot_type: str = "FINAL_PREGAME",
    snapshot_version: int | None = None,
    engine_id: str = "NFL_M1_XGBOOST",
    package_imported_at: datetime | None = None,
    grant_imported_at: datetime | None = None,
    require_package_import: bool = False,
    required_producer_revision: str | None = None,
    required_schema_sha256: str | None = None,
) -> ReadinessVerification:
    """Does this exact FINAL forecast carry complete, positive, current readiness?

    Returns a verdict; raises nothing for a failed join. The caller decides what
    an unverified result means -- during the pre-activation period it is
    recorded without blocking, and after activation it blocks.
    """

    root = Path(exchange_root)
    kickoff = _aware(kickoff_utc)
    evidence: dict[str, Any] = {
        "exchange_root": str(root),
        "readiness_family": READINESS_ROOT,
        # The checkpoint's selected forecast is recorded SEPARATELY from the
        # package and grant imports, as join 4 requires.
        "checkpoint_selected_forecast_id": checkpoint_forecast_id or forecast_id,
    }
    try:
        if checkpoint_forecast_id is not None and checkpoint_forecast_id != forecast_id:
            raise _Fail(CHECKPOINT_FORECAST_MISMATCH,
                        f"the checkpoint selected {checkpoint_forecast_id!r} but "
                        f"readiness was verified for {forecast_id!r}")

        before = _discovery_fingerprint(root)

        # Join 1.
        entry = _readiness_entry(root, forecast_id)
        receipt_sha, receipt = _receipt(root, entry)
        exposure = _exposure(root, entry, receipt_sha)
        _require_identity(receipt, exposure, forecast_id=forecast_id, event_id=event_id,
                          snapshot_type=snapshot_type, snapshot_version=snapshot_version,
                          engine_id=engine_id,
                          required_producer_revision=required_producer_revision,
                          required_schema_sha256=required_schema_sha256)
        _require_positive_status(exposure)
        bundle = receipt.get("bundle") if isinstance(receipt.get("bundle"), dict) else {}
        grant = receipt.get("grant") if isinstance(receipt.get("grant"), dict) else {}
        bundle_sha = str(bundle.get("bundleSha256") or "")
        supplement_sha = str(grant.get("supplementSha256") or "")
        if not _SHA256.fullmatch(bundle_sha) or not _SHA256.fullmatch(supplement_sha):
            raise _Fail(RECEIPT_MALFORMED,
                        "the receipt does not name a sha256 bundle and grant")
        if grant.get("forecastId") not in (None, forecast_id):
            raise _Fail(GRANT_TRANSFER_REFUSED,
                        f"the receipt's grant names {grant.get('forecastId')!r}")
        evidence.update({
            "receipt_sha256": receipt_sha,
            "exposure_status": exposure.get("exposureStatus"),
            "producer_revision": receipt.get("producerRevision"),
            "receipt_bundle_sha256": bundle_sha,
            "supplement_sha256": supplement_sha,
        })

        # Join 2.
        _verify_snapshots(receipt, exposure, forecast_id=forecast_id,
                          bundle_sha=bundle_sha, supplement_sha=supplement_sha)

        # Join 3.
        membership = _verify_current_membership(
            root, forecast_id=forecast_id, receipt_bundle_sha=bundle_sha)
        evidence.update(membership)
        _verify_grant_currently_named(root, supplement_sha=supplement_sha,
                                      forecast_id=forecast_id)

        # Join 4. The producer's boundary and the consumer's cutoff, separately.
        evidence.update(_producer_boundary(receipt, kickoff=kickoff))
        evidence.update(_consumer_cutoff(
            exposure, kickoff=kickoff, observed_at=observed_at,
            package_imported_at=package_imported_at,
            grant_imported_at=grant_imported_at,
            require_package_import=require_package_import))

        # Join 3's final sentence: re-check discovery AFTER verification, so a
        # wrapper or grant index that changed underneath this run is caught
        # rather than certified from a read that is no longer true.
        if _discovery_fingerprint(root) != before:
            raise _Fail(DISCOVERY_CHANGED_DURING_VERIFICATION,
                        "forecast or eligibility discovery changed while this "
                        "verification was running")
    except _Fail as failure:
        return ReadinessVerification(
            verified=False, forecast_id=forecast_id, reason=REASON,
            sub_reasons=(failure.sub_reason,), detail=failure.detail,
            evidence=evidence)

    return ReadinessVerification(verified=True, forecast_id=forecast_id,
                                 evidence=evidence)


__all__ = [
    "AVAILABILITY_DIR", "CONSUMER_CUTOFF", "EXPOSURE_SCHEMA", "INDEX_SCHEMA",
    "OBJECTS_DIR", "PRODUCER_BOUNDARY", "READINESS_INDEX_FILENAME",
    "READINESS_ROOT", "REASON", "RECEIPT_SCHEMA", "STATUS_TIMELY",
    "PRODUCER_REVISION_NOT_PINNED", "RECEIPT_SCHEMA_MISMATCH",
    "RECEIPT_SCHEMA_ABSENT", "SCHEMA_SHA256_FIELDS",
    "ReadinessVerification", "verify_final_readiness",
]

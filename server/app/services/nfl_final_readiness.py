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
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from app.forecast_exchange.importer import (
    ForecastBundleImportError, verify_and_load_sports_bundle,
)
from app.forecast_exchange.supplements import SupplementImportError, verify_supplement
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
#: A readiness document does not have the field shape the bound schema requires.
RECEIPT_SCHEMA_MISMATCH = "RECEIPT_SCHEMA_MISMATCH"
#: The bound schema file is missing, unreadable, or hashes to something other
#: than the reviewed value. The pin is the whole point, so this is a refusal.
READINESS_SCHEMA_UNBOUND = "READINESS_SCHEMA_UNBOUND"

#: Where the reviewed producer revision actually lives in the receipt.
#:
#: NOT top level. Model Studio's authentic fixture nests it inside ``engine``,
#: beside the model manifest and binary hashes -- which is the right place for
#: it, because the revision is part of the ENGINE identity that produced the
#: forecast, not a property of the receipt document.
RECEIPT_REVISION_PATH: tuple[str, ...] = ("engine", "producerRevision")

#: The readiness documents the bound schema describes, by the key it files each
#: one under. ``readyIndexSnapshot`` shares ``readyIndex``'s shape.
SCHEMA_DOCUMENTS = (
    "readyIndex", "readyIndexSnapshot", "readyReceipt", "readyExposure",
    "forecastCurrentMembership", "supplementCurrentMembership",
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

def _resolve_ref(root: Path, ref: Any, *, label: str, malformed: str,
                 absent: str, mismatch: str) -> tuple[bytes, dict[str, Any], str]:
    """Read the object a ``{bytes, path, sha256}`` reference names.

    THE DECLARED PATH IS NEVER FOLLOWED.
    ------------------------------------
    Every reference the producer emits carries an ABSOLUTE path on the machine
    that wrote it. Opening that path would let a receipt point this consumer at
    any file on disk -- outside the exchange, outside the root we were
    configured with -- and the bytes read would still hash correctly because the
    receipt names the hash too. So the path is treated as EVIDENCE, not as an
    instruction: the object is located by its content address beneath our own
    configured root, and only the declared file NAME is taken from the path.

    ``bytes`` is checked as well, because a reference whose declared length
    disagrees with the object at its own address is not a reference we
    understand, whichever of the two is wrong.
    """

    if not isinstance(ref, dict):
        raise _Fail(malformed, f"{label} reference is not an object")
    sha = str(ref.get("sha256") or "").strip().lower()
    if not _SHA256.fullmatch(sha):
        raise _Fail(malformed, f"{label} reference names no sha256 address")
    declared_path = str(ref.get("path") or "")
    name = PureWindowsPath(declared_path).name or PurePosixPath(declared_path).name
    if not name or name != PurePosixPath(name).name or name.startswith("."):
        raise _Fail(malformed, f"{label} reference names no readable file: {declared_path!r}")
    raw, document = _read_json(
        root / READINESS_ROOT / OBJECTS_DIR / sha / name,
        label=label, absent=absent, malformed=malformed)
    _require_address(raw, sha, label=label, sub_reason=mismatch)
    declared_bytes = ref.get("bytes")
    if isinstance(declared_bytes, int) and declared_bytes != len(raw):
        raise _Fail(mismatch,
                    f"{label} reference declares {declared_bytes} bytes, the object at "
                    f"its address is {len(raw)}")
    return raw, document, sha


def _readiness_entry(root: Path, forecast_id: str) -> dict[str, Any]:
    """This forecast's entry in the live ready index.

    The index is a MAP keyed by forecast id, not a list, so "maps it twice" is
    impossible by construction -- a duplicate key cannot survive JSON parsing.
    The ambiguity this join still has to refuse is a key that differs from the
    forecast the checkpoint selected only by whitespace or case, which would
    otherwise look like a miss and be reported as "no entry" rather than as the
    malformed index it is.
    """

    path = root / READINESS_ROOT / READINESS_INDEX_FILENAME
    _, index = _read_json(
        path, label="readiness index",
        absent=READINESS_INDEX_ABSENT, malformed=READINESS_INDEX_MALFORMED)
    if index.get("schemaVersion") != INDEX_SCHEMA:
        raise _Fail(READINESS_INDEX_MALFORMED,
                    f"readiness index declares {index.get('schemaVersion')!r}")
    receipts = index.get("receipts")
    if not isinstance(receipts, dict):
        raise _Fail(READINESS_INDEX_MALFORMED, "readiness index has no receipts map")
    entry = receipts.get(forecast_id)
    if entry is None:
        near = [key for key in receipts
                if isinstance(key, str) and key.strip().lower() == forecast_id.strip().lower()]
        if near:
            raise _Fail(READINESS_INDEX_AMBIGUOUS_ENTRY,
                        f"the ready index keys {near[0]!r}, the checkpoint selected "
                        f"{forecast_id!r}; they differ only in case or whitespace")
        raise _Fail(READINESS_INDEX_NO_ENTRY,
                    f"the ready index does not map {forecast_id}")
    if not isinstance(entry, dict):
        raise _Fail(READINESS_INDEX_MALFORMED, "the ready index entry is not an object")
    return entry


def _receipt(root: Path, entry: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    raw, receipt, receipt_sha = _resolve_ref(
        root, entry.get("receiptRef"), label="base receipt",
        malformed=RECEIPT_MALFORMED, absent=RECEIPT_ABSENT,
        mismatch=RECEIPT_ADDRESS_MISMATCH)
    if receipt.get("schemaVersion") != RECEIPT_SCHEMA:
        raise _Fail(RECEIPT_MALFORMED,
                    f"base receipt declares {receipt.get('schemaVersion')!r}")
    del raw
    return receipt_sha, receipt


def _exposure(root: Path, entry: dict[str, Any], receipt_sha: str) -> dict[str, Any]:
    """The availability sidecar, then the exposure object it references.

    Absence of the sidecar is fail closed and there is no fallback. That is the
    contract's own wording, and it is the reason this function never reaches
    for an exposure object by guessing a path.

    The sidecar is a BARE reference object -- ``{bytes, path, sha256}`` and
    nothing else. It carries no base-receipt field, so it cannot state which
    receipt it belongs to; what binds it is its own location, ``availability/
    <receipt-sha>.json``, and the exposure object's ``readyReceiptRef``. Both
    are checked, because the filename alone is a claim the producer makes and
    the exposure's own reference is the one that has to agree with it.
    """

    sidecar = root / READINESS_ROOT / AVAILABILITY_DIR / f"{receipt_sha}.json"
    _, reference = _read_json(
        sidecar, label="exposure reference", absent=EXPOSURE_REFERENCE_ABSENT,
        malformed=EXPOSURE_REFERENCE_MALFORMED)
    _, exposure, exposure_sha = _resolve_ref(
        root, reference, label="exposure object",
        malformed=EXPOSURE_REFERENCE_MALFORMED, absent=EXPOSURE_ABSENT,
        mismatch=EXPOSURE_ADDRESS_MISMATCH)
    if exposure.get("schemaVersion") != EXPOSURE_SCHEMA:
        raise _Fail(EXPOSURE_MALFORMED,
                    f"exposure object declares {exposure.get('schemaVersion')!r}")
    declared_receipt = exposure.get("readyReceiptRef")
    if not isinstance(declared_receipt, dict) or \
            str(declared_receipt.get("sha256") or "").strip().lower() != receipt_sha:
        raise _Fail(EXPOSURE_NOT_BOUND_TO_RECEIPT,
                    "the exposure object's readyReceiptRef names a different receipt "
                    f"than the sidecar it was reached through ({receipt_sha})")
    del exposure_sha
    return exposure


def bind_schema(schema_path: Path | str | None, required_sha256: str | None) -> dict[str, Any]:
    """Read the reviewed field-shape schema and re-hash it before trusting it.

    ``required_sha256`` is the hash Model Studio published for
    ``READINESS_CONSUMER_SCHEMA.json``. It is NOT a field inside any receipt --
    an earlier draft of this consumer looked for one and would have refused
    every authentic document, which is the fail-closed behaviour working but
    for the wrong reason. It is the hash of the CONTRACT, so the contract is
    what gets hashed, here, from bytes on disk, every time.

    Returns the parsed ``documents`` map. Refusing is the only other outcome.
    """

    if not required_sha256:
        return {}
    if not schema_path:
        raise _Fail(READINESS_SCHEMA_UNBOUND,
                    "a schema hash is pinned but no schema file is configured")
    path = Path(schema_path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise _Fail(READINESS_SCHEMA_UNBOUND,
                    f"the bound schema is unreadable at {path}: {exc}") from exc
    actual = hashlib.sha256(raw).hexdigest()
    if actual != required_sha256.strip().lower():
        raise _Fail(READINESS_SCHEMA_UNBOUND,
                    f"the schema at {path} hashes to {actual}, the reviewed schema is "
                    f"{required_sha256.strip().lower()}")
    try:
        schema = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _Fail(READINESS_SCHEMA_UNBOUND, f"the bound schema is not JSON: {exc}") from exc
    documents = schema.get("documents")
    if not isinstance(documents, dict):
        raise _Fail(READINESS_SCHEMA_UNBOUND, "the bound schema describes no documents")
    missing = [name for name in SCHEMA_DOCUMENTS if name not in documents]
    if missing:
        raise _Fail(READINESS_SCHEMA_UNBOUND,
                    f"the bound schema is missing {missing}")
    return documents


_JSON_TYPES: dict[str, Any] = {
    "object": dict, "array": list, "string": str, "integer": int, "boolean": bool,
}


def _check_shape(document: Any, shape: Any, *, label: str, pinned_keys: bool) -> None:
    """One document against one shape: required fields, types, no extras.

    ``pinned_keys`` is False for a map whose KEYS are data rather than schema.
    The published schema was generated from the synthetic fixture, so it lists
    that fixture's forecast id as a required property of ``receipts`` and
    forbids any other. Enforcing that literally would refuse every production
    document, because a production ready index is keyed by a real forecast id.
    So for those maps the VALUE shape is enforced and the key list is not --
    and that relaxation is named here rather than left implicit, because it is
    the one place this consumer knowingly does not do what the schema says.
    """

    if not isinstance(shape, dict):
        return
    expected = shape.get("type")
    if expected in _JSON_TYPES and not isinstance(document, _JSON_TYPES[expected]):
        # bool is a subclass of int in Python; the schema means them separately.
        raise _Fail(RECEIPT_SCHEMA_MISMATCH,
                    f"{label} should be a JSON {expected}, got {type(document).__name__}")
    if expected == "integer" and isinstance(document, bool):
        raise _Fail(RECEIPT_SCHEMA_MISMATCH, f"{label} should be an integer, got a boolean")
    if expected != "object" or not isinstance(document, dict):
        if expected == "array" and isinstance(document, list):
            item_shape = shape.get("items")
            for position, item in enumerate(document):
                _check_shape(item, item_shape, label=f"{label}[{position}]",
                             pinned_keys=pinned_keys)
        return
    properties = shape.get("properties") if isinstance(shape.get("properties"), dict) else {}
    required = shape.get("required") if isinstance(shape.get("required"), list) else []
    if pinned_keys:
        for name in required:
            if name not in document:
                raise _Fail(RECEIPT_SCHEMA_MISMATCH, f"{label} is missing {name!r}")
        if shape.get("additionalProperties") is False:
            extra = sorted(set(document) - set(properties))
            if extra:
                raise _Fail(RECEIPT_SCHEMA_MISMATCH,
                            f"{label} carries fields the reviewed schema forbids: {extra}")
        for name, child in properties.items():
            if name in document:
                _check_shape(document[name], child, label=f"{label}.{name}",
                             pinned_keys=True)
        return
    # Keys are data. Every value must match the single value shape the schema
    # gives, whichever key it arrived under.
    value_shapes = [child for child in properties.values() if isinstance(child, dict)]
    if not value_shapes:
        return
    for key, value in document.items():
        _check_shape(value, value_shapes[0], label=f"{label}[{key!r}]", pinned_keys=True)


def _validate_documents(documents: dict[str, Any], **named: Any) -> None:
    """Every readiness document this verification read, against the bound schema."""

    if not documents:
        return
    for key, value in named.items():
        if value is None:
            continue
        shape = documents.get(key)
        if shape is None:
            continue
        if key in ("readyIndex", "readyIndexSnapshot"):
            # `receipts` is keyed by forecast id, which is data, not schema.
            _check_shape({k: v for k, v in value.items() if k != "receipts"},
                         {"type": "object",
                          "properties": {k: v for k, v in
                                         (shape.get("properties") or {}).items()
                                         if k != "receipts"},
                          "required": [name for name in (shape.get("required") or [])
                                       if name != "receipts"]},
                         label=key, pinned_keys=True)
            _check_shape(value.get("receipts"),
                         (shape.get("properties") or {}).get("receipts"),
                         label=f"{key}.receipts", pinned_keys=False)
            continue
        _check_shape(value, shape, label=key, pinned_keys=True)


def _require_identity(receipt: dict[str, Any], exposure: dict[str, Any], *,
                      forecast_id: str, event_id: str, snapshot_type: str,
                      snapshot_version: int | None, engine_id: str,
                      required_producer_revision: str | None = None) -> None:
    """One exact game/forecast/snapshot/model/producer identity, or nothing.

    ``required_producer_revision`` PINS the revision. Unpinned, the check can
    only require a NON-EMPTY string -- which a receipt from the superseded
    producer satisfies. Pinned, a receipt from any other revision is refused
    outright, which is the difference between "a receipt exists" and "the
    reviewed producer wrote it".

    ``engineId`` is NOT read from the top level. The authentic receipt carries
    the whole engine identity -- id, version, candidate, model manifest and
    binary hashes, feature contract, training dataset, and the producer revision
    -- in one ``engine`` object, and the revision is a property of that engine,
    not of the receipt document.
    """

    declared = {
        "forecastId": (receipt.get("forecastId"), forecast_id),
        "eventId": (receipt.get("eventId"), event_id),
        "snapshotType": (receipt.get("snapshotType"), snapshot_type),
    }
    for label, (actual, expected) in declared.items():
        if actual != expected:
            raise _Fail(RECEIPT_IDENTITY_MISMATCH,
                        f"receipt {label} is {actual!r}, the checkpoint selected {expected!r}")
    if snapshot_version is not None and receipt.get("snapshotVersion") != snapshot_version:
        raise _Fail(RECEIPT_IDENTITY_MISMATCH,
                    f"receipt snapshotVersion is {receipt.get('snapshotVersion')!r}, "
                    f"the checkpoint selected {snapshot_version!r}")
    engine = receipt.get("engine")
    if not isinstance(engine, dict):
        raise _Fail(RECEIPT_MALFORMED, "the receipt carries no engine identity")
    if engine.get("engineId") != engine_id:
        raise _Fail(RECEIPT_IDENTITY_MISMATCH,
                    f"receipt engine.engineId is {engine.get('engineId')!r}, "
                    f"the checkpoint selected {engine_id!r}")
    revision = str(engine.get("producerRevision") or "").strip()
    if not revision:
        raise _Fail(RECEIPT_IDENTITY_MISMATCH, "receipt names no producer revision")
    if required_producer_revision and revision != required_producer_revision.strip():
        raise _Fail(PRODUCER_REVISION_NOT_PINNED,
                    f"receipt engine.producerRevision is {revision!r}; the reviewed "
                    f"operational-V2 revision is {required_producer_revision.strip()!r}")
    if exposure.get("forecastId") != forecast_id:
        raise _Fail(RECEIPT_IDENTITY_MISMATCH,
                    "the exposure object names a different forecast")
    if exposure.get("eventId") != event_id:
        raise _Fail(RECEIPT_IDENTITY_MISMATCH,
                    "the exposure object names a different event")


def _require_positive_status(exposure: dict[str, Any]) -> None:
    """``status``, and only the one positive value.

    The authentic exposure also SAYS what its clock means:
    ``certifiesSidecarExposureBeforeProducerDeadline`` is false and
    ``clockMeaning`` is ACTUAL_OBSERVATION_AFTER_READY_INDEX_REPLACE_NOT_SIDECAR_
    FILE_EXPOSURE. Both confirm, from the producer's own bytes, that the sidecar
    becoming readable is NOT the boundary being certified -- so this consumer
    must never read sidecar readability as a timing failure, and must establish
    its own T-15 observation instead. ``consumerObservedAvailabilityRequired``
    states that obligation explicitly, and is required to be true: a document
    that waived it would be asking us to trust a clock we do not own.
    """

    status = exposure.get("status")
    if status != STATUS_TIMELY:
        raise _Fail(EXPOSURE_STATUS_NOT_POSITIVE,
                    f"exposure status {status!r} is not positive evidence")
    if exposure.get("consumerObservedAvailabilityRequired") is not True:
        raise _Fail(EXPOSURE_STATUS_NOT_POSITIVE,
                    "the exposure does not require consumer-observed availability; "
                    "this consumer will not accept a clock it does not own")
    if exposure.get("certifiesSidecarExposureBeforeProducerDeadline") is not False:
        raise _Fail(EXPOSURE_STATUS_NOT_POSITIVE,
                    "the exposure claims to certify sidecar exposure before the "
                    "producer deadline, which the contract says it does not")


# ---------------------------------------------------------------------------
# join 2 -- the immutable snapshots
# ---------------------------------------------------------------------------

def _verify_snapshots(root: Path, receipt: dict[str, Any], exposure: dict[str, Any], *,
                      forecast_id: str, receipt_sha: str, bundle_sha: str,
                      supplement_sha: str) -> dict[str, Any]:
    """Match the exposure to its ORIGINAL ready-index snapshot and receipt.

    THE SNAPSHOTS ARE REFERENCES, NOT INLINE COPIES.
    -----------------------------------------------
    The authentic receipt and exposure do not embed the index they saw. They
    name it -- ``currentLocatorSnapshotRef``, ``supplement.indexRef``,
    ``readyIndexSnapshotRef`` -- as content-addressed objects, which is
    strictly stronger than an inline copy: the bytes are frozen at an address
    that cannot be edited without changing the address, and both parties can
    cite the same one.

    So each is READ and RE-HASHED, and every membership question is asked of
    the snapshot's own bytes. The mutable discovery files on disk are still not
    consulted here; they are the present tense and are checked in join 3. A
    snapshot is a historical hash reference, a discovery path is not.
    """

    _, ready_snapshot, ready_snapshot_sha = _resolve_ref(
        root, exposure.get("readyIndexSnapshotRef"), label="ready-index snapshot",
        malformed=READY_INDEX_SNAPSHOT_MISMATCH, absent=READY_INDEX_SNAPSHOT_MISMATCH,
        mismatch=READY_INDEX_SNAPSHOT_MISMATCH)
    if ready_snapshot.get("schemaVersion") != INDEX_SCHEMA:
        raise _Fail(READY_INDEX_SNAPSHOT_MISMATCH,
                    f"the ready-index snapshot declares {ready_snapshot.get('schemaVersion')!r}")
    snapshot_entry = (ready_snapshot.get("receipts") or {}).get(forecast_id) \
        if isinstance(ready_snapshot.get("receipts"), dict) else None
    if not isinstance(snapshot_entry, dict):
        raise _Fail(READY_INDEX_SNAPSHOT_ENTRY_MISSING,
                    "the original ready-index snapshot has no entry for this forecast")
    # The snapshot must name THIS receipt. Otherwise a positive exposure could
    # be carried across from an earlier, superseded readiness pass.
    snapshot_receipt = snapshot_entry.get("receiptRef")
    if not isinstance(snapshot_receipt, dict) or \
            str(snapshot_receipt.get("sha256") or "").strip().lower() != receipt_sha:
        raise _Fail(READY_INDEX_SNAPSHOT_MISMATCH,
                    "the original ready-index snapshot names a different receipt for "
                    "this forecast")

    _, forecast_snapshot, forecast_snapshot_sha = _resolve_ref(
        root, receipt.get("currentLocatorSnapshotRef"), label="forecast-index snapshot",
        malformed=FORECAST_SNAPSHOT_MEMBERSHIP_MISSING,
        absent=FORECAST_SNAPSHOT_MEMBERSHIP_MISSING,
        mismatch=FORECAST_SNAPSHOT_MEMBERSHIP_MISSING)
    current = forecast_snapshot.get("currentBundleSha256s")
    if not isinstance(current, list) or bundle_sha not in current:
        raise _Fail(FORECAST_SNAPSHOT_MEMBERSHIP_MISSING,
                    f"bundle {bundle_sha} was not current in the original forecast snapshot")
    # The receipt names the same snapshot twice -- once as the immutable
    # reference above, once inside forecastLocatorExposure as the locator it
    # actually observed. They must be the same object, or the receipt is
    # certifying a boundary against a locator other than the one it froze.
    locator = receipt.get("forecastLocatorExposure")
    if not isinstance(locator, dict):
        raise _Fail(RECEIPT_MALFORMED, "the receipt carries no forecast locator exposure")
    if str(locator.get("locatorSha256") or "").strip().lower() != forecast_snapshot_sha:
        raise _Fail(FORECAST_SNAPSHOT_MEMBERSHIP_MISSING,
                    "the receipt's observed locator is not the snapshot it froze")

    supplement = receipt.get("supplement")
    if not isinstance(supplement, dict):
        raise _Fail(SUPPLEMENT_SNAPSHOT_MEMBERSHIP_MISSING,
                    "the receipt carries no supplement block")
    _, supplement_snapshot, supplement_snapshot_sha = _resolve_ref(
        root, supplement.get("indexRef"), label="supplement-index snapshot",
        malformed=SUPPLEMENT_SNAPSHOT_MEMBERSHIP_MISSING,
        absent=SUPPLEMENT_SNAPSHOT_MEMBERSHIP_MISSING,
        mismatch=SUPPLEMENT_SNAPSHOT_MEMBERSHIP_MISSING)
    if supplement_snapshot.get("schemaVersion") != SUPPLEMENT_INDEX_SCHEMA:
        raise _Fail(SUPPLEMENT_SNAPSHOT_MEMBERSHIP_MISSING,
                    f"the supplement snapshot declares "
                    f"{supplement_snapshot.get('schemaVersion')!r}")
    named = supplement_snapshot.get("currentSupplementSha256s")
    if not isinstance(named, list) or supplement_sha not in named:
        raise _Fail(SUPPLEMENT_SNAPSHOT_MEMBERSHIP_MISSING,
                    f"supplement {supplement_sha} was not named in the original snapshot")
    return {
        "readyIndexSnapshot": ready_snapshot,
        "forecastCurrentMembership": forecast_snapshot,
        "supplementCurrentMembership": supplement_snapshot,
        "ready_index_snapshot_sha256": ready_snapshot_sha,
        "forecast_index_snapshot_sha256": forecast_snapshot_sha,
        "supplement_index_snapshot_sha256": supplement_snapshot_sha,
    }


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

    if unverified:
        # ANY current wrapper that carries this forecast and does not verify is
        # a refusal, even when another one does verify.
        #
        # The looser reading -- "one verified host is enough, ignore the broken
        # ones" -- is wrong for the same reason ambiguity is wrong. Two current
        # containers both claiming this child, one of them unreadable, is an
        # exchange in a state we cannot account for: we cannot tell whether the
        # broken wrapper is a superseded leftover, a partial write in progress,
        # or the one that was actually meant to be current. Choosing the
        # readable one would be choosing the answer we prefer.
        raise _Fail(FORECAST_WRAPPER_NOT_VERIFIED,
                    f"{len(unverified)} current wrapper(s) carrying this forecast do "
                    f"not verify ({unverified[0]}); {len(hosts)} do")
    if not hosts:
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
                                  forecast_id: str) -> dict[str, Any]:
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
    # THE FULL DEPLOYED VALIDATOR, not a content-address spot check.
    #
    # An earlier draft hashed supplement.json and required it to equal the
    # directory name. That was simply wrong about the addressing scheme -- a
    # supplement's address is the canonical hash of its MANIFEST IDENTITY, not
    # of its payload file -- and it would have refused every authentic package.
    # Reimplementing an addressing rule we already own was the mistake; this
    # calls the owner instead, which also re-verifies the manifest, every listed
    # artifact's hash, the contract version, governance and the payload shape.
    directory = root / SUPPLEMENT_FAMILY / "sha256" / supplement_sha
    try:
        verified = verify_supplement(directory)
    except SupplementImportError as exc:
        raise _Fail(SUPPLEMENT_ABSENT,
                    f"supplement {supplement_sha} does not verify: {exc}") from exc
    granted = verified.binding.get("forecastId") or verified.supplement.get("forecastId")
    if granted != forecast_id:
        # A grant is never transferred to a different child.
        raise _Fail(GRANT_TRANSFER_REFUSED,
                    f"supplement {supplement_sha} grants {granted!r}, not {forecast_id}")
    return verified.supplement


# ---------------------------------------------------------------------------
# join 4 -- the two clocks, kept apart
# ---------------------------------------------------------------------------

def _producer_boundary(receipt: dict[str, Any], exposure: dict[str, Any], *,
                       kickoff: datetime) -> dict[str, Any]:
    """The PRODUCER's obligation: the PAIR and the index observed by T-21.

    THE PAIR IS BOTH HALVES.
    ------------------------
    ``PAIR_AND_READY_INDEX_TIMELY`` certifies the forecast AND its grant. An
    earlier version of this function checked only the forecast locator, which
    meant a grant published AFTER the producer boundary passed join 4
    untouched -- precisely the failure the contract's "four separate filesystem
    operations" framing warns about, and precisely the half we were not
    reading. The grant's three publication clocks are now checked against the
    same deadline as the forecast's.

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
    # The exposure states the same deadline. Two documents disagreeing about
    # the boundary they certify is not something to resolve in our favour.
    exposure_deadline = _parse_time(
        exposure.get("producerDeadlineUtc"), label="exposure producerDeadlineUtc",
        sub_reason=EXPOSURE_MALFORMED)
    if exposure_deadline != deadline:
        raise _Fail(EXPOSURE_MALFORMED,
                    f"the exposure's T-21 deadline {exposure_deadline.isoformat()} "
                    f"differs from the receipt's {deadline.isoformat()}")
    # PARSED, BUT DELIBERATELY NOT GATED.
    #
    # It is tempting to refuse a readyAvailableAtUtc after T-21, and that is
    # wrong. The exposure says what this clock means in its own bytes:
    # ACTUAL_OBSERVATION_AFTER_READY_INDEX_REPLACE_NOT_SIDECAR_FILE_EXPOSURE,
    # with certifiesSidecarExposureBeforeProducerDeadline false. It is the
    # moment the producer OBSERVED availability, not the moment availability
    # happened -- so a late value is the telemetry delay the contract warns
    # about, and refusing on it would reject a genuinely timely publication
    # whose proof was slow to appear. What IS gated are the publication and
    # validation clocks below, which are actions rather than observations.
    #
    # Parsing it anyway matters: a malformed or absent stamp must not reach the
    # decision record as though it were a time.
    ready_available = _parse_time(
        exposure.get("readyAvailableAtUtc"), label="readyAvailableAtUtc",
        sub_reason=EXPOSURE_MALFORMED)
    locator = receipt.get("forecastLocatorExposure")
    if not isinstance(locator, dict):
        raise _Fail(RECEIPT_MALFORMED, "the receipt carries no forecast locator exposure")
    not_after = _parse_time(locator.get("notAfterUtc"), label="locator notAfterUtc",
                            sub_reason=RECEIPT_MALFORMED)
    if not_after != deadline:
        raise _Fail(RECEIPT_MALFORMED,
                    f"the locator's notAfterUtc {not_after.isoformat()} is not the "
                    f"receipt's own T-21 deadline {deadline.isoformat()}")
    visible = _parse_time(locator.get("visibleAtUtc"), label="locator visibleAtUtc",
                          sub_reason=RECEIPT_MALFORMED)
    checked = _parse_time(locator.get("publicationCheckedAtUtc"),
                          label="locator publicationCheckedAtUtc",
                          sub_reason=RECEIPT_MALFORMED)
    validated = _parse_time(receipt.get("readyValidatedAtUtc"),
                            label="readyValidatedAtUtc", sub_reason=RECEIPT_MALFORMED)
    # The grant half of the pair. Its three publication clocks are the
    # producer's own statement of when the eligibility package and its index
    # became visible, and they are bound by the same T-21 boundary.
    grant = receipt.get("supplement")
    if not isinstance(grant, dict):
        raise _Fail(RECEIPT_MALFORMED, "the receipt carries no supplement block")
    grant_clocks = {
        "grant package visible": _parse_time(
            grant.get("packageVisibleAtUtc"), label="supplement packageVisibleAtUtc",
            sub_reason=RECEIPT_MALFORMED),
        "grant index visible": _parse_time(
            grant.get("indexVisibleAtUtc"), label="supplement indexVisibleAtUtc",
            sub_reason=RECEIPT_MALFORMED),
        "grant declared publication": _parse_time(
            grant.get("declaredPublishedUtc"), label="supplement declaredPublishedUtc",
            sub_reason=RECEIPT_MALFORMED),
    }
    late = [name for name, moment in (
        ("current locator", visible),
        ("publication check", checked),
        ("ready validation", validated),
        *grant_clocks.items(),
    ) if moment > deadline]
    if late:
        raise _Fail(PRODUCER_BOUNDARY_MISSED,
                    f"the producer observed the {' and '.join(late)} after T-21")
    # The producer states its own verdict on the same question. If it says it
    # was late, we do not overrule it in our own favour -- a disagreement
    # between the timestamps and the flag is itself a refusal, because one of
    # the two is wrong and we cannot tell which.
    if locator.get("observedAfterDeadline") is not False:
        raise _Fail(PRODUCER_BOUNDARY_MISSED,
                    "the receipt's own locator flags observation after the deadline")
    return {"producer_deadline_utc": deadline.isoformat(),
            "locator_visible_at_utc": visible.isoformat(),
            "publication_checked_at_utc": checked.isoformat(),
            "ready_validated_at_utc": validated.isoformat(),
            "ready_available_at_utc": ready_available.isoformat(),
            "grant_package_visible_at_utc": grant_clocks["grant package visible"].isoformat(),
            "grant_index_visible_at_utc": grant_clocks["grant index visible"].isoformat(),
            "grant_declared_published_at_utc":
                grant_clocks["grant declared publication"].isoformat()}


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
        # The exposure states in its own bytes what this clock means, and it is
        # not sidecar file exposure; we keep the producer's wording verbatim
        # rather than paraphrasing it into a claim it does not make.
        "producer_clock_meaning": exposure.get("clockMeaning"),
        "certifies_sidecar_exposure_before_producer_deadline": exposure.get(
            "certifiesSidecarExposureBeforeProducerDeadline"),
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
    schema_path: Path | str | None = None,
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

        # The bound schema is re-hashed from disk before any document is read
        # against it, so the shape this consumer enforces is the reviewed one
        # and not whatever happens to be on the filesystem.
        documents = bind_schema(schema_path, required_schema_sha256)
        evidence["bound_schema_sha256"] = required_schema_sha256 or None

        before = _discovery_fingerprint(root)

        # Join 1.
        index_document = _read_json(
            root / READINESS_ROOT / READINESS_INDEX_FILENAME, label="readiness index",
            absent=READINESS_INDEX_ABSENT, malformed=READINESS_INDEX_MALFORMED)[1]
        entry = _readiness_entry(root, forecast_id)
        receipt_sha, receipt = _receipt(root, entry)
        exposure = _exposure(root, entry, receipt_sha)
        _validate_documents(documents, readyIndex=index_document,
                            readyReceipt=receipt, readyExposure=exposure)
        _require_identity(receipt, exposure, forecast_id=forecast_id, event_id=event_id,
                          snapshot_type=snapshot_type, snapshot_version=snapshot_version,
                          engine_id=engine_id,
                          required_producer_revision=required_producer_revision)
        _require_positive_status(exposure)
        bundle = receipt.get("bundleRef") if isinstance(receipt.get("bundleRef"), dict) else {}
        supplement = receipt.get("supplement") \
            if isinstance(receipt.get("supplement"), dict) else {}
        bundle_sha = str(bundle.get("sha256") or "").strip().lower()
        supplement_sha = str(supplement.get("supplementAddress") or "").strip().lower()
        if not _SHA256.fullmatch(bundle_sha) or not _SHA256.fullmatch(supplement_sha):
            raise _Fail(RECEIPT_MALFORMED,
                        "the receipt does not name a sha256 bundle and grant")
        if supplement.get("forecastId") not in (None, forecast_id):
            raise _Fail(GRANT_TRANSFER_REFUSED,
                        f"the receipt's grant names {supplement.get('forecastId')!r}")
        if supplement.get("eventId") not in (None, event_id):
            raise _Fail(GRANT_TRANSFER_REFUSED,
                        f"the receipt's grant names event {supplement.get('eventId')!r}")
        evidence.update({
            "receipt_sha256": receipt_sha,
            "exposure_status": exposure.get("status"),
            "producer_revision": (receipt.get("engine") or {}).get("producerRevision"),
            "receipt_bundle_sha256": bundle_sha,
            "supplement_sha256": supplement_sha,
            "supplement_publication_disposition": supplement.get("publicationDisposition"),
            "timing_policy_version": receipt.get("timingPolicyVersion"),
        })

        # Join 2.
        snapshots = _verify_snapshots(
            root, receipt, exposure, forecast_id=forecast_id, receipt_sha=receipt_sha,
            bundle_sha=bundle_sha, supplement_sha=supplement_sha)
        _validate_documents(
            documents,
            readyIndexSnapshot=snapshots["readyIndexSnapshot"],
            forecastCurrentMembership=snapshots["forecastCurrentMembership"],
            supplementCurrentMembership=snapshots["supplementCurrentMembership"])
        evidence.update({key: value for key, value in snapshots.items()
                         if key.endswith("_sha256")})

        # Join 3.
        membership = _verify_current_membership(
            root, forecast_id=forecast_id, receipt_bundle_sha=bundle_sha)
        evidence.update(membership)
        _verify_grant_currently_named(root, supplement_sha=supplement_sha,
                                      forecast_id=forecast_id)

        # Join 4. The producer's boundary and the consumer's cutoff, separately.
        evidence.update(_producer_boundary(receipt, exposure, kickoff=kickoff))
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
    "READINESS_SCHEMA_UNBOUND", "RECEIPT_REVISION_PATH", "bind_schema",
    "SCHEMA_DOCUMENTS",
    "ReadinessVerification", "verify_final_readiness",
]
